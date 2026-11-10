"""Gerenciamento de jobs assíncronos do DeCap.

* Cada job tem identificador próprio, pasta própria em ``<estado>/jobs/<id>`` com
  ``job.json`` (estado persistido) e ``decap.log`` (saída bruta do processo).
* O processo é iniciado com lista de argumentos (sem shell). STDOUT/STDERR do DeCap
  vão para o arquivo de log; o STDOUT do servidor fica reservado ao protocolo MCP.
* O resultado é decidido por verificação: código de saída, mensagens ``[E]`` e
  ``[P][n Failed]``, existência e leitura do .rcp, presença dos .rcs e, quando
  pedido, o sistema de coordenadas gravado. O DeCap retorna 0 mesmo em falhas.
* Reinício do servidor: jobs que estavam ``queued``/``running`` no ``job.json`` são
  marcados ``interrupted``. O servidor novo não controla mais esses processos e
  não tenta encerrá-los; ``cancel_job`` recusa jobs interrompidos.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import errors
from .decap import ImportPlan
from .decap_messages import free_text_warnings, parse_messages, summarize
from .installation import decode_console

log = logging.getLogger("recap_mcp.jobs")

ACTIVE = ("queued", "running")
FINAL = ("succeeded", "failed", "cancelled", "interrupted")
MAX_LOG_TAIL_LINES = 200
MAX_LOG_TAIL_BYTES = 64 * 1024
CANCEL_WAIT_S = 15
ANSI_ENCODING = "mbcs" if os.name == "nt" else "cp1252"


class TaskCancelled(Exception):
    """Levantada por uma tarefa interna quando o cancelamento é pedido."""


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def describe_exit_code(code: int | None) -> str | None:
    if code is None:
        return None
    unsigned = code & 0xFFFFFFFF
    if unsigned >= 0xC0000000:
        known = {0xC0000005: "violação de acesso", 0xC000013A: "interrompido (Ctrl+C)",
                 0xC0000409: "falha de segurança da pilha"}
        return f"0x{unsigned:08X} ({known.get(unsigned, 'exceção do Windows')})"
    return str(code)


class JobManager:
    def __init__(
        self,
        state_dir: Path,
        launcher: Callable[[], list[str]],
        max_concurrent: int = 2,
        project_reader: Callable[[str], dict[str, Any]] | None = None,
        scan_lister: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.jobs_dir = self.state_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._launcher = launcher
        self.max_concurrent = max_concurrent
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_requested: set[str] = set()
        self._tasks: dict[str, threading.Event] = {}
        self._shutting_down = False
        if project_reader is None or scan_lister is None:
            from .projects import list_scans, read_project
            project_reader = project_reader or read_project
            scan_lister = scan_lister or (lambda p: list_scans(p, verify_rcs_headers=True))
        self._read_project = project_reader
        self._list_scans = scan_lister
        self._recover()

    # ------------------------------------------------------------------ estado
    def _job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def _persist(self, job: dict[str, Any]) -> None:
        path = self._job_dir(job["job_id"]) / "job.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _recover(self) -> None:
        for d in sorted(self.jobs_dir.iterdir()) if self.jobs_dir.is_dir() else []:
            f = d / "job.json"
            if not f.is_file():
                continue
            try:
                job = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("job.json ilegível em %s: %s", d, exc)
                continue
            if job.get("status") in ACTIVE:
                job["status"] = "interrupted"
                job["finished_at"] = None
                job["interrupted_at"] = _now()
                if job.get("kind") == "import_scans":
                    job["failure_reasons"] = [(
                        "O servidor MCP foi reiniciado enquanto o job estava ativo. O processo DeCap "
                        f"(PID {job.get('pid')}) pode ter continuado ou terminado sem supervisão; confira os "
                        "arquivos de saída antes de reutilizar o destino."
                    )]
                else:
                    job["failure_reasons"] = ["O servidor MCP foi reiniciado durante a tarefa; repita a operação."]
                self._persist(job)
            self._jobs[job["job_id"]] = job

    # ------------------------------------------------------------------ início
    def running_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j["status"] in ACTIVE and j["job_id"] in self._procs)

    def start_import(self, plan: ImportPlan) -> dict[str, Any]:
        with self._lock:
            if self.running_count() >= self.max_concurrent:
                raise errors.RecapMcpError(errors.TOO_MANY_JOBS,
                                           f"Já há {self.max_concurrent} importações em andamento; aguarde ou cancele uma.")
            # Revalida o destino imediatamente antes de iniciar (evita corrida entre pedidos).
            for target in (plan.expected_rcp, plan.expected_support):
                if target.exists():
                    raise errors.RecapMcpError(errors.DESTINATION_EXISTS, f"Já existe '{target}'.", path=str(target))
                for j in self._jobs.values():
                    if j["status"] in ACTIVE and j.get("plan", {}).get("expected_rcp") == str(plan.expected_rcp):
                        raise errors.RecapMcpError(errors.DESTINATION_EXISTS,
                                                   "Outro job ativo já grava este projeto.", job_id=j["job_id"])

            control_bytes = None
            if plan.needs_control_file():
                # O DeCap 27.0.1.242 lê o arquivo de controle na página de código ANSI (UTF-8 falha
                # com acentos). Caminhos fora dessa página não podem ir por arquivo de controle.
                text = "\r\n".join(str(p) for p in plan.inputs) + "\r\n"
                try:
                    control_bytes = text.encode(ANSI_ENCODING)
                except UnicodeEncodeError as exc:
                    raise errors.RecapMcpError(
                        errors.LIMIT_EXCEEDED,
                        "A lista de arquivos é longa demais para a linha de comando e contém caracteres fora da "
                        "página de código ANSI do Windows, que o arquivo de controle do DeCap não aceita. "
                        "Divida a importação em lotes menores ou renomeie as pastas.") from exc

            job_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
            jdir = self._job_dir(job_id)
            jdir.mkdir(parents=True)
            created_output = False
            if not plan.output_folder.exists():
                plan.output_folder.mkdir(parents=False)
                created_output = True

            control = None
            if control_bytes is not None:
                control = jdir / "scans.txt"
                control.write_bytes(control_bytes)
            launcher = self._launcher()
            command = [*launcher, *plan.build_args(control)]
            log_path = jdir / "decap.log"

            job: dict[str, Any] = {
                "job_id": job_id,
                "kind": "import_scans",
                "status": "queued",
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "pid": None,
                "exit_code": None,
                "exit_code_description": None,
                "command": command,
                "control_file": str(control) if control else None,
                "log_file": str(log_path),
                "created_output_folder": created_output,
                "plan": plan.to_dict(),
                "verification": None,
                "failure_reasons": [],
            }
            self._jobs[job_id] = job
            self._persist(job)

            log_fh = open(log_path, "wb")  # noqa: SIM115 - fechado pela thread de espera
            try:
                proc = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    cwd=str(Path(launcher[-1]).parent) if Path(launcher[-1]).is_file() else None,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            except OSError as exc:
                log_fh.close()
                job.update(status="failed", finished_at=_now(), failure_reasons=[f"Falha ao iniciar o DeCap: {exc}"])
                self._persist(job)
                raise errors.RecapMcpError(errors.LAUNCH_FAILED, f"Falha ao iniciar o DeCap: {exc}",
                                           job_id=job_id) from exc
            job.update(status="running", started_at=_now(), pid=proc.pid)
            self._procs[job_id] = proc
            self._persist(job)
            t = threading.Thread(target=self._wait, args=(job_id, proc, log_fh, plan), daemon=True,
                                 name=f"decap-{job_id}")
            self._threads[job_id] = t
            t.start()
            log.info("job %s iniciado (pid %s)", job_id, proc.pid)
            return self._public(job)

    def _wait(self, job_id: str, proc: subprocess.Popen, log_fh, plan: ImportPlan) -> None:
        code = proc.wait()
        try:
            log_fh.close()
        except OSError:
            pass
        text = self._read_log(Path(self._jobs[job_id]["log_file"]))
        with self._lock:
            job = self._jobs[job_id]
            job["exit_code"] = code
            job["exit_code_description"] = describe_exit_code(code)
            job["finished_at"] = _now()
            verification, reasons = self._verify(plan, code, text)
            job["verification"] = verification
            if job_id in self._cancel_requested:
                job["status"] = "cancelled"
                why = ("Encerrado porque o servidor MCP foi finalizado." if self._shutting_down
                       else "Cancelado a pedido do cliente.")
                job["failure_reasons"] = [why + " Arquivos parciais podem ter ficado no destino."]
            else:
                job["status"] = "succeeded" if not reasons else "failed"
                job["failure_reasons"] = reasons
            self._procs.pop(job_id, None)
            self._persist(job)
            log.info("job %s terminou: %s (código %s)", job_id, job["status"], code)

    # ------------------------------------------------------------ verificação
    def _verify(self, plan: ImportPlan, code: int | None, text: str) -> tuple[dict[str, Any], list[str]]:
        msgs = parse_messages(text)
        summ = summarize(msgs)
        reasons: list[str] = []
        v: dict[str, Any] = {
            "exit_code_zero": code == 0,
            "decap_errors": summ["errors"],
            "failed_scan_indexes": summ["failed_scan_indexes"],
            "reported_projects": summ["reported_projects"],
            "free_text_warnings": free_text_warnings(text),
            "rcp_exists": plan.expected_rcp.is_file(),
            "support_folder_exists": plan.expected_support.is_dir(),
        }
        if code != 0:
            reasons.append(f"DeCap terminou com código {describe_exit_code(code)}.")
        for e in summ["errors"]:
            reasons.append(f"DeCap informou erro {e['code']}: {e['text']}")
        if summ["failed_scan_indexes"]:
            reasons.append(f"DeCap marcou como falha os scans de índice {', '.join(summ['failed_scan_indexes'])}.")
        if not v["rcp_exists"]:
            reasons.append("O arquivo .rcp esperado não foi criado.")
        else:
            v["rcp_size_bytes"] = plan.expected_rcp.stat().st_size
            try:
                info = self._read_project(str(plan.expected_rcp))
                v["manifest_readable"] = True
                v["scan_count"] = info.get("scan_count")
                v["total_points"] = info.get("total_points")
                v["coordinate_system"] = info.get("coordinate_system")
                v["project_bounds"] = info.get("project_bounds")
                if not info.get("scan_count"):
                    reasons.append("O .rcp foi criado, mas não contém scans.")
                scans = self._list_scans(str(plan.expected_rcp))
                v["missing_rcs_count"] = scans.get("missing_rcs_count")
                v["rcs_files"] = [
                    {"name": s.get("name"), "path": s["rcs"].get("resolved_path"), "exists": s["rcs"].get("exists"),
                     "num_points": s.get("num_points"),
                     "header_matches": (s["rcs"].get("header_check") or {}).get("matches_manifest_points")}
                    for s in scans.get("scans", [])
                ]
                if scans.get("missing_rcs_count"):
                    reasons.append(f"{scans['missing_rcs_count']} arquivo(s) .rcs referenciado(s) não existem.")
                if any(r["header_matches"] is False for r in v["rcs_files"]):
                    reasons.append("Número de pontos no cabeçalho .rcs difere do manifesto.")
                expected_cs = plan.requested.get("target_coordinate_system") or plan.requested.get(
                    "current_coordinate_system")
                if expected_cs:
                    v["coordinate_system_requested"] = expected_cs
                    v["coordinate_system_applied"] = info.get("coordinate_system") == expected_cs
                    if not v["coordinate_system_applied"]:
                        reasons.append(
                            f"Sistema de coordenadas pedido ({expected_cs}) não foi gravado no projeto "
                            f"(gravado: {info.get('coordinate_system') or 'nenhum'}).")
            except errors.RecapMcpError as exc:
                v["manifest_readable"] = False
                reasons.append(f"O .rcp criado não pôde ser lido: {exc.message}")
        for w in v["free_text_warnings"]:
            if w.endswith("_invalid") and not any("Sistema de coordenadas" in r for r in reasons):
                reasons.append(f"DeCap avisou: {w}")
        return v, reasons

    # -------------------------------------------------------------- consultas
    @staticmethod
    def _read_log(path: Path) -> str:
        try:
            return decode_console(path.read_bytes())
        except OSError:
            return ""

    @staticmethod
    def _tail(path: Path, max_lines: int) -> tuple[list[str], int]:
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                if size > MAX_LOG_TAIL_BYTES:
                    fh.seek(size - MAX_LOG_TAIL_BYTES)
                data = fh.read()
        except OSError:
            return [], 0
        text = decode_console(data).replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln for ln in text.split("\n") if ln.strip() and set(ln.strip()) != {"-"}]
        return lines[-max_lines:], size

    def _controlled(self, job_id: str) -> bool:
        return job_id in self._procs or job_id in self._tasks

    def _public(self, job: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in job.items() if k != "plan"}
        plan = job.get("plan") or {}
        if job.get("kind") == "import_scans":
            out["output"] = {
                "expected_rcp": plan.get("expected_rcp"),
                "expected_support_folder": plan.get("expected_support_folder"),
            }
            out["inputs"] = plan.get("inputs")
            out["options"] = plan.get("options")
        else:
            out["task"] = plan.get("task")
        out["warnings"] = plan.get("warnings", [])
        out["controlled_by_this_server"] = self._controlled(job["job_id"])
        return out

    # ----------------------------------------------------- tarefas internas
    def start_task(self, kind: str, task: dict[str, Any], func: Callable[..., dict[str, Any]],
                   warnings: list[str] | None = None) -> dict[str, Any]:
        """Executa ``func(progress, cancel_event, log)`` numa thread, como job persistido.

        ``progress(percent, message)`` atualiza o estado; ``cancel_event`` é sinalizado por
        ``cancel_job``; ``log(texto)`` acrescenta ao log do job. ``func`` devolve o resultado.
        """
        with self._lock:
            if sum(1 for j in self._tasks if self._jobs[j]["status"] in ACTIVE) >= self.max_concurrent:
                raise errors.RecapMcpError(errors.TOO_MANY_JOBS,
                                           f"Já há {self.max_concurrent} tarefas em andamento; aguarde ou cancele uma.")
            job_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
            jdir = self._job_dir(job_id)
            jdir.mkdir(parents=True)
            log_path = jdir / "tarefa.log"
            log_path.write_text("", encoding="utf-8")
            job: dict[str, Any] = {
                "job_id": job_id, "kind": kind, "status": "running", "created_at": _now(),
                "started_at": _now(), "finished_at": None, "pid": os.getpid(), "exit_code": None,
                "exit_code_description": None, "command": None, "log_file": str(log_path),
                "plan": {"task": task, "warnings": warnings or []}, "progress": {"percent": 0, "message": "iniciando"},
                "result": None, "verification": None, "failure_reasons": [],
            }
            self._jobs[job_id] = job
            cancel = threading.Event()
            self._tasks[job_id] = cancel
            self._persist(job)

        def log_line(text: str) -> None:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{_now()} {text}\n")

        def progress(percent: float, message: str = "") -> None:
            with self._lock:
                job["progress"] = {"percent": round(float(percent), 1), "message": message}
                self._persist(job)

        def run() -> None:
            try:
                result = func(progress, cancel, log_line)
                status, reasons = "succeeded", []
            except TaskCancelled:
                result, status = None, "cancelled"
                reasons = ["Cancelado a pedido do cliente." if not self._shutting_down
                           else "Encerrado porque o servidor MCP foi finalizado."]
            except errors.RecapMcpError as exc:
                result, status, reasons = None, "failed", [exc.message]
            except Exception as exc:
                log.exception("tarefa %s falhou", job_id)
                result, status, reasons = None, "failed", [f"{type(exc).__name__}: {exc}"]
            log_line(f"fim: {status} {'; '.join(reasons)}")
            with self._lock:
                job.update(status=status, finished_at=_now(), result=result, failure_reasons=reasons)
                if status == "succeeded":
                    job["progress"] = {"percent": 100.0, "message": "concluído"}
                self._tasks.pop(job_id, None)
                self._persist(job)

        t = threading.Thread(target=run, daemon=True, name=f"tarefa-{job_id}")
        self._threads[job_id] = t
        t.start()
        return self._public(job)

    def _get(self, job_id: str) -> dict[str, Any]:
        if not isinstance(job_id, str) or job_id not in self._jobs:
            raise errors.RecapMcpError(errors.JOB_NOT_FOUND, f"Job '{job_id}' não encontrado.", job_id=job_id)
        return self._jobs[job_id]

    def status(self, job_id: str, log_lines: int = 40) -> dict[str, Any]:
        log_lines = max(0, min(int(log_lines), MAX_LOG_TAIL_LINES))
        with self._lock:
            job = self._get(job_id)
            out = self._public(job)
        tail, size = self._tail(Path(job["log_file"]), log_lines) if log_lines else ([], 0)
        msgs = parse_messages("\n".join(tail))
        out["log_tail"] = tail
        out["log_size_bytes"] = size
        if job.get("kind") == "import_scans":
            out["progress_by_scan"] = summarize(msgs)["progress_by_scan"] if job["status"] in ACTIVE else None
        if job["started_at"]:
            end = job["finished_at"] or _now()
            try:
                out["elapsed_seconds"] = round((_dt.datetime.fromisoformat(end)
                                                - _dt.datetime.fromisoformat(job["started_at"])).total_seconds(), 1)
            except ValueError:
                out["elapsed_seconds"] = None
        if job["status"] in FINAL and job.get("kind") == "import_scans" and job.get("plan"):
            out["output_now"] = {
                "rcp_exists": Path(job["plan"]["expected_rcp"]).is_file(),
                "support_folder_exists": Path(job["plan"]["expected_support_folder"]).is_dir(),
            }
        return {"ok": True, **out}

    def list(self, status: str | None = None, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)
            if status:
                jobs = [j for j in jobs if j["status"] == status]
            items = [{
                "job_id": j["job_id"], "kind": j["kind"], "status": j["status"],
                "created_at": j["created_at"], "finished_at": j["finished_at"],
                "exit_code": j["exit_code"], "expected_rcp": (j.get("plan") or {}).get("expected_rcp"),
                "progress": j.get("progress"),
                "controlled_by_this_server": self._controlled(j["job_id"]),
            } for j in jobs[:limit]]
        return {"ok": True, "count": len(items), "total": len(self._jobs), "jobs": items}

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._get(job_id)
            event = self._tasks.get(job_id)
        if event is not None and job["status"] in ACTIVE:
            event.set()  # a tarefa verifica o sinal entre blocos de pontos
            t = self._threads.get(job_id)
            if t:
                t.join(CANCEL_WAIT_S)
            res = self.status(job_id, log_lines=10)
            res["note"] = "Tarefa interrompida. Arquivos parciais desta tarefa são removidos por ela mesma."
            return res
        with self._lock:
            proc = self._procs.get(job_id)
            if job["status"] not in ACTIVE or proc is None:
                raise errors.RecapMcpError(
                    errors.JOB_NOT_CANCELLABLE,
                    f"O job '{job_id}' está '{job['status']}' e não é um processo ativo controlado por esta "
                    "instância do servidor.", status=job["status"])
            self._cancel_requested.add(job_id)
            proc.terminate()  # TerminateProcess no Windows; somente o PID iniciado por este servidor
        t = self._threads.get(job_id)
        deadline = time.monotonic() + CANCEL_WAIT_S
        while t and t.is_alive() and time.monotonic() < deadline:
            t.join(0.2)
        res = self.status(job_id, log_lines=10)
        res["note"] = ("O DeCap foi encerrado. Arquivos parciais não são apagados automaticamente; "
                       "confira output_now e remova-os manualmente se quiser repetir a importação no mesmo destino.")
        return res

    def wait(self, job_id: str, timeout: float) -> dict[str, Any]:
        """Auxiliar para testes e diagnóstico: espera o job terminar."""
        t = self._threads.get(job_id)
        if t:
            t.join(timeout)
        return self.status(job_id)

    def shutdown(self) -> None:
        """Ao encerrar o servidor, termina processos ainda controlados para não deixá-los órfãos."""
        with self._lock:
            self._shutting_down = True
            procs = list(self._procs.items())
            tasks = list(self._tasks.items())
        for job_id, event in tasks:
            event.set()
            t = self._threads.get(job_id)
            if t:
                t.join(5)
        for job_id, proc in procs:
            if proc.poll() is None:
                self._cancel_requested.add(job_id)
                try:
                    proc.terminate()
                except OSError:
                    pass
        for job_id, _ in procs:
            t = self._threads.get(job_id)
            if t:
                t.join(5)
