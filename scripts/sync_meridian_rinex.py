"""Copia el convertidor de grabaciones del MeridianV/MeridianVI (repo MeridianV, `tools/rinex/`) a
`tresvizo_ppk/core/meridian_rinex/` y sus pruebas a `tests/core/meridian_rinex/`, y anota el commit de origen.

Uso:  python scripts/sync_meridian_rinex.py [RUTA_AL_REPO_MERIDIANV]   (por omisión ~/Documents/MeridianV)

El paquete se copia sin cambios; en las pruebas solo se cambia `from meridian_rinex…` por
`from tresvizo_ppk.core.meridian_rinex…` y la ruta que insertan en `sys.path`. Después: correr las pruebas
(`python -m pytest tests/core`) y hacer commit con el commit de origen en el mensaje.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tresvizo_ppk" / "core" / "meridian_rinex"
TESTS = ROOT / "tests" / "core" / "meridian_rinex"
TEST_FILES = ("builders.py", "orbits.py", "rinex_check.py", "test_converter.py", "test_framing.py",
              "test_rtcm3.py", "test_segments.py", "test_unicore.py")


def main() -> int:
    repo = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "Documents" / "MeridianV"
    source = repo / "tools" / "rinex"
    if not (source / "meridian_rinex" / "converter.py").is_file():
        raise SystemExit(f"No está el convertidor en {source}")
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", "tools/rinex"],
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise SystemExit("tools/rinex tiene cambios sin commit en el repo de origen: haz commit primero.")
    commit = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%H %cs", "--", "tools/rinex"],
                            capture_output=True, text=True, check=True).stdout.strip()
    for old in PACKAGE.glob("*.py"):
        old.unlink()
    for py in sorted((source / "meridian_rinex").glob("*.py")):
        shutil.copy2(py, PACKAGE / py.name)
    for name in TEST_FILES:
        text = (source / "tests" / name).read_text(encoding="utf-8")
        text = re.sub(r"from meridian_rinex([. ])", r"from tresvizo_ppk.core.meridian_rinex\1", text)
        text = text.replace('sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))',
                            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))')
        (TESTS / name).write_text(text, encoding="utf-8")
    shutil.copytree(source / "tests" / "fixtures" / "pyrtcm", TESTS / "fixtures" / "pyrtcm", dirs_exist_ok=True)
    origin = PACKAGE / "ORIGEN.md"
    text = origin.read_text(encoding="utf-8")
    text = re.sub(r"(\*\*Commit de origen:\*\* `)[^`]*(`)", lambda m: m.group(1) + commit + m.group(2), text)
    origin.write_text(text, encoding="utf-8")
    print(f"Copiado desde {repo} ({commit}). Corre las pruebas y haz commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
