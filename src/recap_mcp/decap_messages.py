"""Interpretação das linhas de status que o DeCap escreve no console.

Formato observado no DeCap 27.0.1.242 (não documentado oficialmente):

``[M][<código>][<texto>]``  mensagem (ex.: ``[M][2049][License Check OK]``)
``[E][<código>][<texto>]``  erro (ex.: ``[E][1025][File '...' can not be found]``)
``[F][<arquivo>][<n>]``      início do scan de entrada ``n``
``[P][<n> <pct>|Failed]``    progresso do scan ``n`` ou falha
``[R][<caminho .rcp>]``      projeto gravado

O DeCap retorna código de saída 0 mesmo quando escreve ``[E]`` ou ``[P][n Failed]``.
Por isso o resultado de um job nunca é decidido apenas pelo código de saída.
"""

from __future__ import annotations

import re
from typing import Any

_LINE_RE = re.compile(r"^\[(?P<kind>[MEFPRW])\]\[(?P<a>[^\]]*)\](?:\[(?P<b>[^\]]*)\])?\s*$")


def parse_messages(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = raw.strip()
        m = _LINE_RE.match(line)
        if not m:
            continue
        kind, a, b = m.group("kind"), m.group("a"), m.group("b")
        if kind in ("M", "E", "W"):
            out.append({"kind": kind, "code": a, "text": b or ""})
        elif kind == "F":
            out.append({"kind": "F", "file": a, "index": b})
        elif kind == "P":
            parts = a.split()
            item: dict[str, Any] = {"kind": "P", "index": parts[0] if parts else ""}
            if len(parts) > 1:
                if parts[1].lower() == "failed":
                    item["failed"] = True
                else:
                    try:
                        item["percent"] = int(parts[1])
                    except ValueError:
                        item["raw"] = a
            out.append(item)
        elif kind == "R":
            out.append({"kind": "R", "path": a})
    return out


def summarize(messages: list[dict[str, Any]]) -> dict[str, Any]:
    errors_ = [m for m in messages if m["kind"] == "E"]
    failed = sorted({m["index"] for m in messages if m["kind"] == "P" and m.get("failed")})
    progress: dict[str, int] = {}
    for m in messages:
        if m["kind"] == "P" and "percent" in m:
            progress[m["index"]] = m["percent"]
    results = [m["path"] for m in messages if m["kind"] == "R"]
    return {
        "errors": [{"code": m["code"], "text": m["text"]} for m in errors_],
        "failed_scan_indexes": failed,
        "progress_by_scan": progress,
        "reported_projects": results,
    }


# Avisos em texto livre observados no DeCap 27.0.1.242.
WARNING_PATTERNS = (
    (re.compile(r"current coordinate system not valid", re.IGNORECASE), "current_coordinate_system_invalid"),
    (re.compile(r"target coordinate system not valid", re.IGNORECASE), "target_coordinate_system_invalid"),
)


def free_text_warnings(text: str) -> list[str]:
    return [code for rx, code in WARNING_PATTERNS if rx.search(text or "")]
