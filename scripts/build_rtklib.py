"""Compila las herramientas de terceros que usa el motor: `rnx2rtkp` y `convbin` (RTKLIB-EX) y `crx2rnx` (RNXCMP).

Uso:
    python scripts/build_rtklib.py                 # compilador del sistema (cc/gcc/clang, o MSVC «cl» en Windows)
    python scripts/build_rtklib.py --cc gcc        # un compilador concreto (MinGW en Windows: --cc gcc)
    python scripts/build_rtklib.py --out DIR       # otra carpeta de salida

Los ejecutables quedan en `third_party/bin/<plataforma>/` (p. ej. `macos-arm64`, `windows-x64`, `linux-x86_64`),
que es donde los busca `tresvizo_ppk.core.tools`. Las opciones de compilación son las de los makefiles de RTKLIB
(`app/consapp/*/gcc/makefile`): todas las constelaciones, NFREQ=4, NEXOBS=3, sin LAPACK.

Sin dependencias: solo la biblioteca estándar de Python y un compilador de C.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.macos_minimo import MINIMUM as _MACOS_MINIMUM  # noqa: E402

MACOS_MINIMUM = _MACOS_MINIMUM.get({"aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower()),
                                   "14.0")
RTKLIB = ROOT / "third_party" / "rtklib"
RNXCMP = ROOT / "third_party" / "rnxcmp"

DEFINES = ["TRACE", "ENAGLO", "ENAQZS", "ENAGAL", "ENACMP", "ENAIRN", "NFREQ=4", "NEXOBS=3"]

# Fuentes de cada programa, como en sus makefiles de RTKLIB.
RNX2RTKP = ["rtkcmn", "trace", "rinex", "rtkpos", "postpos", "solution", "lambda", "geoid", "sbas", "preceph",
            "pntpos", "ephemeris", "options", "ppp", "ppp_ar", "rtcm", "rtcm2", "rtcm3", "rtcm3e", "ionex", "tides",
            "sofa"]
CONVBIN = ["rtkcmn", "trace", "rinex", "sbas", "preceph", "rcvraw", "convrnx", "rtcm", "rtcm2", "rtcm3", "rtcm3e",
           "pntpos", "ephemeris", "ionex", "sofa",
           "rcv/novatel", "rcv/ublox", "rcv/crescent", "rcv/skytraq", "rcv/javad", "rcv/nvs", "rcv/binex",
           "rcv/rt17", "rcv/septentrio", "rcv/swiftnav", "rcv/unicore"]


def platform_tag() -> str:
    """Nombre de la carpeta de binarios de esta plataforma (el mismo que usa el motor)."""
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    if sys.platform == "win32":
        return f"windows-{'x64' if arch in ('x64', 'x86_64') else arch}"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    return f"linux-{'x86_64' if arch in ('x64', 'x86_64') else arch}"


def _run(cmd: list[str], cwd: Path) -> None:
    print("  " + " ".join(cmd[:3]) + (" …" if len(cmd) > 3 else ""), flush=True)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"Falló la compilación: {' '.join(cmd)}")


def _is_msvc(cc: str) -> bool:
    return Path(cc).stem.lower() == "cl"


def build_program(cc: str, name: str, main_c: Path, modules: list[str], out_dir: Path, work: Path,
                  include: Path | None, extra_libs: list[str]) -> Path:
    exe = out_dir / (name + (".exe" if sys.platform == "win32" else ""))
    sources = [main_c] + [RTKLIB / "src" / f"{m}.c" for m in modules]
    obj_dir = work / name
    obj_dir.mkdir(parents=True, exist_ok=True)
    if _is_msvc(cc):
        cmd = [cc, "/nologo", "/O2", "/W1", "/D_CRT_SECURE_NO_WARNINGS", f"/Fo{obj_dir}\\"]
        # RTKLIB y crx2rnx eligen Windows con `WIN32`, que el compilador de Microsoft no define (solo `_WIN32`).
        cmd.append("/DWIN32")
        cmd += [f"/D{d}" for d in DEFINES] if include else []
        if include:
            cmd.append(f"/I{include}")
        cmd += [str(s) for s in sources] + [f"/Fe{exe}", "/link"] + [f"{lib}.lib" for lib in extra_libs]
    else:
        cmd = [cc, "-std=c99", "-O3", "-w"]
        # Con -std=c99 (modo estricto) MinGW define `_WIN32` pero no `WIN32`, y RTKLIB elige Windows con `WIN32`:
        # sin esto busca cabeceras de Unix (sys/select.h). Los makefiles de RTKLIB para MinGW lo ponen igual.
        if sys.platform == "win32":
            cmd.append("-DWIN32")
        # En macOS, para la versión mínima que declara la app (scripts/macos_minimo.py) y no para la del equipo que
        # compila: si no, el programa pide el macOS del equipo de la CI y no abre en uno anterior.
        if sys.platform == "darwin":
            cmd.append(f"-mmacosx-version-min={MACOS_MINIMUM}")
        cmd += [f"-D{d}" for d in DEFINES] if include else []
        if include:
            cmd.append(f"-I{include}")
        cmd += [str(s) for s in sources] + ["-o", str(exe), "-lm"] + [f"-l{lib}" for lib in extra_libs]
    _run(cmd, cwd=obj_dir)
    return exe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cc", default=None, help="compilador de C (por omisión: CC, cc, gcc, clang o cl)")
    parser.add_argument("--out", type=Path, default=None, help="carpeta de salida")
    args = parser.parse_args(argv)

    cc = args.cc or os.environ.get("CC")
    if not cc:
        for candidate in (("cl", "gcc", "cc", "clang") if sys.platform == "win32" else ("cc", "gcc", "clang")):
            if shutil.which(candidate):
                cc = candidate
                break
    if not cc or not shutil.which(cc):
        raise SystemExit("No hay compilador de C (cc, gcc, clang o cl).")

    out_dir = args.out or ROOT / "third_party" / "bin" / platform_tag()
    out_dir.mkdir(parents=True, exist_ok=True)
    win_libs = ["ws2_32", "winmm"] if sys.platform == "win32" else []
    print(f"Compilando con {cc} en {out_dir}")
    with tempfile.TemporaryDirectory(prefix="tvppk-build-") as tmp:
        work = Path(tmp)
        built = [
            build_program(cc, "rnx2rtkp", RTKLIB / "app" / "rnx2rtkp" / "rnx2rtkp.c", RNX2RTKP, out_dir, work,
                          RTKLIB / "src", win_libs),
            build_program(cc, "convbin", RTKLIB / "app" / "convbin" / "convbin.c", CONVBIN, out_dir, work,
                          RTKLIB / "src", win_libs),
            build_program(cc, "crx2rnx", RNXCMP / "crx2rnx.c", [], out_dir, work, None, []),
        ]
    for exe in built:
        print(f"  listo: {exe.relative_to(ROOT) if exe.is_relative_to(ROOT) else exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
