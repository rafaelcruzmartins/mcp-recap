"""DeCap falso para testes automatizados (não exige ReCap).

Comportamento controlado por ``FAKE_DECAP_MODE``:
success (padrão), error_missing, crash, hang, no_output, cs_ignored, scan_failed.
Grava os argumentos recebidos em ``FAKE_DECAP_ARGS_FILE`` (se definido).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rcp_fixtures import ScanSpec, make_project

HELP = """Welcome to DeCap.exe.
Usage:
DeCap.exe --help
DeCap.exe --version
DeCap.exe --import <auth token> <public key> <output folder> <project name> [options] <input scan 0>
DeCap.exe --importWithLicense <output folder> <project name> [options] <input scan 0> [input scan 1]
DeCap.exe --importWithLicense <output folder> <project name> [options] --controlFile <control file name>
DeCap.exe --checkLicense
  options:
	--minRangeClipping <min clipping>
	--maxRangeClipping <max clipping>
	--decimation <decimation value mm>
	--unify
	--normalizeIntensity <0|1>
	--currentCoordinateSystem <cs>
	--targetCoordinateSystem <cs>
	--inputUnitType <0|1|2|3|4|5>
	--e57EnableImageImport
	--e57CreateScanPerImage
	--e57MatchImageResolution
	--e57LevelImage
"""

VALUE_OPTS = {"--minRangeClipping", "--maxRangeClipping", "--decimation", "--normalizeIntensity",
              "--currentCoordinateSystem", "--targetCoordinateSystem", "--inputUnitType", "--controlFile"}


def main(argv: list[str]) -> int:
    if os.environ.get("FAKE_DECAP_ARGS_FILE"):
        Path(os.environ["FAKE_DECAP_ARGS_FILE"]).write_text(json.dumps(argv), encoding="utf-8")
    mode = os.environ.get("FAKE_DECAP_MODE", "success")
    if os.environ.get("FAKE_DECAP_HELP") == "minimal":
        help_text = "DeCap.exe --importWithLicense <output folder> <project name> <input scan 0>\n"
    else:
        help_text = HELP
    if argv[:1] == ["--help"]:
        print(help_text)
        return 0
    if argv[:1] == ["--version"]:
        print("[M][2048][27.0.1.242]")
        return 0
    if argv[:1] == ["--checkLicense"]:
        if os.environ.get("FAKE_DECAP_LICENSE") == "bad":
            print("[E][2050][No valid license]")
        else:
            print("[M][2049][License Check OK]")
        return 0
    if argv[:1] != ["--importWithLicense"]:
        print("unknown")
        return 2

    folder, name = Path(argv[1]), argv[2]
    rest = argv[3:]
    opts: dict[str, str | bool] = {}
    inputs: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in VALUE_OPTS:
            opts[a] = rest[i + 1]
            i += 2
        elif a.startswith("--"):
            opts[a] = True
            i += 1
        else:
            inputs.append(a)
            i += 1
    if "--controlFile" in opts:
        # como o DeCap real: arquivo de controle na página de código ANSI
        enc = "mbcs" if os.name == "nt" else "cp1252"
        inputs = [ln.strip() for ln in Path(str(opts["--controlFile"])).read_text(encoding=enc).splitlines()
                  if ln.strip()]

    print("Welcome to DeCap.exe.", flush=True)
    if mode == "hang":
        print(f"[F][{inputs[0]}][1]", flush=True)
        print("[P][1 10]", flush=True)
        time.sleep(120)
        return 0
    if mode == "crash":
        print("Enabling unified output", flush=True)
        return -1073741819
    if mode == "no_output":
        print("(0%)  Completed", flush=True)
        return 0
    if mode == "error_missing":
        print(f"[E][1025][File '{inputs[0]}' can not be found]", flush=True)
        return 0
    for n, inp in enumerate(inputs, 1):
        print(f"[F][{inp}][{n}]", flush=True)
        if mode == "scan_failed":
            print(f"[P][{n} Failed]", flush=True)
        else:
            print(f"[P][{n} 100]", flush=True)
    if mode == "scan_failed":
        make_project(folder, name, [])
    else:
        cs = ""
        if mode != "cs_ignored":
            cs = str(opts.get("--targetCoordinateSystem") or opts.get("--currentCoordinateSystem") or "")
        else:
            print("Current coordinate system not valid. Setting current coordinate system to default.")
        scans = [ScanSpec(name=Path(p).stem, num_points=1234) for p in inputs]
        make_project(folder, name, scans, coordinate_system=cs)
    print(f"[R][{folder / (name + '.rcp')}]", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
