"""Erros estruturados e consistentes para todas as ferramentas."""

from __future__ import annotations

from typing import Any


class RecapMcpError(Exception):
    """Erro de domínio com código estável, mensagem em português e detalhes opcionais.

    Os códigos são estáveis para que clientes e testes possam reagir a eles sem
    depender do texto da mensagem.
    """

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {k: v for k, v in details.items() if v is not None}

    def to_dict(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return {"ok": False, "error": err}


# Códigos usados no projeto (documentados em docs/ferramentas.md)
RECAP_NOT_FOUND = "recap_not_found"
DECAP_NOT_FOUND = "decap_not_found"
OPTION_UNSUPPORTED = "option_unsupported"
INVALID_ARGUMENT = "invalid_argument"
PATH_NOT_FOUND = "path_not_found"
NOT_A_DIRECTORY = "not_a_directory"
NOT_A_FILE = "not_a_file"
UNSUPPORTED_FORMAT = "unsupported_format"
INVALID_PROJECT = "invalid_project"
LIMIT_EXCEEDED = "limit_exceeded"
DESTINATION_EXISTS = "destination_exists"
JOB_NOT_FOUND = "job_not_found"
JOB_NOT_CANCELLABLE = "job_not_cancellable"
TOO_MANY_JOBS = "too_many_jobs"
LAUNCH_FAILED = "launch_failed"
RECAP_ALREADY_RUNNING = "recap_already_running"
