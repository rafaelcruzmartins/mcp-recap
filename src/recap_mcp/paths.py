"""Validação de caminhos recebidos pelas ferramentas."""

from __future__ import annotations

from pathlib import Path

from . import errors

MAX_PATH_CHARS = 4096


def as_absolute(value: str, label: str) -> Path:
    """Converte um argumento em caminho absoluto, rejeitando entradas ambíguas.

    Caminhos relativos não são aceitos porque o diretório atual do servidor é
    definido pelo cliente MCP e não é previsível para quem conversa com o agente.
    """
    if not isinstance(value, str) or not value.strip():
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{label}' não pode ser vazio.")
    if len(value) > MAX_PATH_CHARS:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{label}' excede {MAX_PATH_CHARS} caracteres.")
    if "\x00" in value:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{label}' contém caractere nulo.")
    p = Path(value.strip().strip('"'))
    if not p.is_absolute():
        raise errors.RecapMcpError(
            errors.INVALID_ARGUMENT,
            f"'{label}' precisa ser um caminho absoluto (ex.: D:\\Projetos\\nuvem.e57).",
            received=value,
        )
    return Path(_normalize(p))


def _normalize(p: Path) -> str:
    try:
        return str(p.resolve(strict=False))
    except OSError:
        return str(p)


def existing_file(value: str, label: str) -> Path:
    p = as_absolute(value, label)
    if not p.exists():
        raise errors.RecapMcpError(errors.PATH_NOT_FOUND, f"Arquivo não encontrado: {p}", path=str(p))
    if not p.is_file():
        raise errors.RecapMcpError(errors.NOT_A_FILE, f"Não é um arquivo: {p}", path=str(p))
    return p


def existing_dir(value: str, label: str) -> Path:
    p = as_absolute(value, label)
    if not p.exists():
        raise errors.RecapMcpError(errors.PATH_NOT_FOUND, f"Pasta não encontrada: {p}", path=str(p))
    if not p.is_dir():
        raise errors.RecapMcpError(errors.NOT_A_DIRECTORY, f"Não é uma pasta: {p}", path=str(p))
    return p
