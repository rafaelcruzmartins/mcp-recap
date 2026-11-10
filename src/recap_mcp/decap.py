"""Validação de pedidos de importação e montagem dos argumentos do DeCap.

Somente o modo ``--importWithLicense`` é usado: ele aproveita a licença do ReCap
já ativada na máquina. O modo ``--import <token> <chave pública>`` não é exposto
porque exigiria credenciais.

Cada opção só é montada se aparecer em ``decap.exe --help`` da versão instalada.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import errors
from .installation import Capabilities
from .paths import as_absolute, existing_file

# Formatos importados com sucesso pelo DeCap 27.0.1.242 nos testes de integração.
VERIFIED_INPUT_EXTENSIONS = frozenset({".pts", ".xyz", ".e57"})
# Formatos de scan listados pelo ReCap para importação, ainda não exercitados nos testes.
LISTED_INPUT_EXTENSIONS = frozenset({
    ".asc", ".txt", ".las", ".laz", ".ptx", ".ptg", ".fls", ".fws", ".zfs", ".zfprj",
    ".rds", ".rsp", ".xyb", ".cl3", ".clr", ".pcg", ".isproj", ".lsproj",
})
SUPPORTED_INPUT_EXTENSIONS = VERIFIED_INPUT_EXTENSIONS | LISTED_INPUT_EXTENSIONS

# Tabela de --inputUnitType, conforme ``decap.exe --help`` e a documentação oficial.
INPUT_UNITS = {"mm": 0, "cm": 1, "m": 2, "in": 3, "ft": 4, "us_ft": 5}

MAX_INPUTS = 500
# Acima deste comprimento a lista de scans vai para um arquivo de controle (--controlFile),
# evitando o limite de 32 767 caracteres da linha de comando do Windows.
CONTROL_FILE_THRESHOLD_CHARS = 24000
PROJECT_NAME_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,120}$")
RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
CS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]{0,79}$")
MAX_RANGE_M = 100000


@dataclass
class ImportPlan:
    output_folder: Path
    project_name: str
    inputs: list[Path]
    options: list[str]
    expected_rcp: Path
    expected_support: Path
    create_output_folder: bool
    requested: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def build_args(self, control_file: Path | None = None) -> list[str]:
        args = ["--importWithLicense", str(self.output_folder), self.project_name, *self.options]
        if control_file is not None:
            args += ["--controlFile", str(control_file)]
        else:
            args += [str(p) for p in self.inputs]
        return args

    def needs_control_file(self) -> bool:
        return sum(len(str(p)) + 3 for p in self.inputs) > CONTROL_FILE_THRESHOLD_CHARS

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_folder": str(self.output_folder),
            "project_name": self.project_name,
            "inputs": [str(p) for p in self.inputs],
            "options": self.options,
            "expected_rcp": str(self.expected_rcp),
            "expected_support_folder": str(self.expected_support),
            "requested": self.requested,
            "warnings": self.warnings,
        }


def _require(caps: Capabilities, option: str) -> None:
    if not caps.supports(option):
        raise errors.RecapMcpError(
            errors.OPTION_UNSUPPORTED,
            f"A opção {option} não aparece em 'decap.exe --help' da versão instalada.",
            option=option, available=caps.import_options,
        )


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{label}' deve ser numérico.")
    v = float(value)
    if not math.isfinite(v):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{label}' deve ser finito.")
    return v


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def plan_import(
    caps: Capabilities,
    output_folder: str,
    project_name: str,
    inputs: list[str],
    *,
    create_output_folder: bool = True,
    decimation_mm: float | None = None,
    min_range_m: float | None = None,
    max_range_m: float | None = None,
    unify: bool = False,
    normalize_intensity: bool | None = None,
    input_unit: str | None = None,
    current_coordinate_system: str | None = None,
    target_coordinate_system: str | None = None,
    e57_enable_image_import: bool = False,
    e57_create_scan_per_image: bool = False,
    e57_match_image_resolution: bool = False,
    e57_level_image: bool = False,
) -> ImportPlan:
    """Valida tudo antes de iniciar o processo e devolve o plano de execução."""
    if not caps.help_available:
        raise errors.RecapMcpError(errors.DECAP_NOT_FOUND,
                                   "Não foi possível consultar 'decap.exe --help'; importação indisponível.")
    if "--importWithLicense" not in caps.modes:
        raise errors.RecapMcpError(errors.OPTION_UNSUPPORTED,
                                   "Esta versão do DeCap não oferece --importWithLicense.")

    # Nome do projeto
    if not isinstance(project_name, str):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "'project_name' deve ser texto.")
    name = project_name.strip()
    if name.lower().endswith(".rcp"):
        name = name[:-4]
    if not PROJECT_NAME_RE.match(name) or name.endswith((".", " ")) or name.lower() in RESERVED_NAMES:
        raise errors.RecapMcpError(
            errors.INVALID_ARGUMENT,
            "Nome de projeto inválido: use até 120 caracteres, sem \\ / : * ? \" < > | e sem nomes "
            "reservados do Windows.", project_name=project_name)

    # Destino: sempre novo, sem sobrescrita
    out = as_absolute(output_folder, "output_folder")
    if out.exists() and not out.is_dir():
        raise errors.RecapMcpError(errors.NOT_A_DIRECTORY, f"O destino existe e não é pasta: {out}")
    if not out.exists():
        if not create_output_folder:
            raise errors.RecapMcpError(errors.PATH_NOT_FOUND, f"Pasta de destino não existe: {out}")
        if not out.parent.is_dir():
            raise errors.RecapMcpError(errors.PATH_NOT_FOUND,
                                       f"A pasta-mãe do destino não existe: {out.parent}")
    rcp = out / f"{name}.rcp"
    support = out / f"{name} Support"
    for target in (rcp, support):
        if target.exists():
            raise errors.RecapMcpError(
                errors.DESTINATION_EXISTS,
                f"Já existe '{target}'. Escolha outro nome ou outra pasta; o servidor nunca sobrescreve projetos.",
                path=str(target))

    # Entradas
    if not isinstance(inputs, list) or not inputs:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "Informe ao menos um arquivo em 'inputs'.")
    if len(inputs) > MAX_INPUTS:
        raise errors.RecapMcpError(errors.LIMIT_EXCEEDED, f"Máximo de {MAX_INPUTS} arquivos por importação.")
    resolved: list[Path] = []
    seen: set[str] = set()
    warnings: list[str] = []
    for i, item in enumerate(inputs):
        p = existing_file(item, f"inputs[{i}]")
        ext = p.suffix.lower()
        if ext not in SUPPORTED_INPUT_EXTENSIONS:
            raise errors.RecapMcpError(
                errors.UNSUPPORTED_FORMAT, f"Formato '{p.suffix}' não suportado para importação.",
                path=str(p), supported=sorted(SUPPORTED_INPUT_EXTENSIONS))
        if ext not in VERIFIED_INPUT_EXTENSIONS:
            warnings.append(f"{p.name}: formato {ext} listado pelo ReCap, mas não verificado nos testes deste servidor.")
        key = str(p).lower()
        if key in seen:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"Arquivo repetido em 'inputs': {p}")
        seen.add(key)
        resolved.append(p)

    opts: list[str] = []
    requested: dict[str, Any] = {}

    if decimation_mm is not None:
        _require(caps, "--decimation")
        v = _number(decimation_mm, "decimation_mm")
        if not (0 < v <= 10000):
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "decimation_mm deve estar entre 0 e 10000 mm.")
        opts += ["--decimation", _fmt(v)]
        requested["decimation_mm"] = v

    for label, val, flag in (("min_range_m", min_range_m, "--minRangeClipping"),
                             ("max_range_m", max_range_m, "--maxRangeClipping")):
        if val is None:
            continue
        _require(caps, flag)
        v = _number(val, label)
        if not v.is_integer():
            # Observado no DeCap 27.0.1.242: "0.3" é lido como 0. Recusar evita truncamento silencioso.
            raise errors.RecapMcpError(
                errors.INVALID_ARGUMENT,
                f"{label} deve ser um número inteiro de metros: o DeCap trunca valores decimais.", value=val)
        if not (0 <= v <= MAX_RANGE_M):
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"{label} deve estar entre 0 e {MAX_RANGE_M}.")
        opts += [flag, _fmt(v)]
        requested[label] = int(v)
    if min_range_m is not None and max_range_m is not None and requested["min_range_m"] >= requested["max_range_m"]:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "min_range_m deve ser menor que max_range_m.")

    if unify:
        _require(caps, "--unify")
        opts.append("--unify")
        requested["unify"] = True
        warnings.append("--unify não produziu projeto nos testes com DeCap 27.0.1.242 (veja docs/limitacoes.md); "
                        "o resultado será verificado ao final.")

    if normalize_intensity is not None:
        _require(caps, "--normalizeIntensity")
        if not isinstance(normalize_intensity, bool):
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "normalize_intensity deve ser true ou false.")
        opts += ["--normalizeIntensity", "1" if normalize_intensity else "0"]
        requested["normalize_intensity"] = normalize_intensity

    if input_unit is not None:
        _require(caps, "--inputUnitType")
        key = str(input_unit).strip().lower()
        if key not in INPUT_UNITS:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"input_unit inválida: {input_unit}",
                                       allowed=list(INPUT_UNITS))
        opts += ["--inputUnitType", str(INPUT_UNITS[key])]
        requested["input_unit"] = key

    for label, val, flag in (("current_coordinate_system", current_coordinate_system, "--currentCoordinateSystem"),
                             ("target_coordinate_system", target_coordinate_system, "--targetCoordinateSystem")):
        if val is None:
            continue
        _require(caps, flag)
        if not isinstance(val, str) or not CS_RE.match(val.strip()):
            raise errors.RecapMcpError(
                errors.INVALID_ARGUMENT,
                f"{label} deve ser um código de sistema de coordenadas da biblioteca Autodesk "
                "(ex.: SIRGAS2000.UTM-23S), sem espaços.", value=val)
        opts += [flag, val.strip()]
        requested[label] = val.strip()
    if target_coordinate_system is not None and current_coordinate_system is None:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT,
                                   "target_coordinate_system exige current_coordinate_system.")

    e57_flags = (
        (e57_enable_image_import, "--e57EnableImageImport", "e57_enable_image_import"),
        (e57_create_scan_per_image, "--e57CreateScanPerImage", "e57_create_scan_per_image"),
        (e57_match_image_resolution, "--e57MatchImageResolution", "e57_match_image_resolution"),
        (e57_level_image, "--e57LevelImage", "e57_level_image"),
    )
    if any(f for f, _, _ in e57_flags) and not any(p.suffix.lower() == ".e57" for p in resolved):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "Opções e57_* exigem ao menos uma entrada .e57.")
    for flag_on, flag, label in e57_flags:
        if flag_on:
            _require(caps, flag)
            opts.append(flag)
            requested[label] = True

    return ImportPlan(out, name, resolved, opts, rcp, support, create_output_folder, requested, warnings)
