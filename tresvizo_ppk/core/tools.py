"""Programas externos del motor (RTKLIB `rnx2rtkp`/`convbin`, `crx2rnx`): dónde están y cómo se corren con progreso y
cancelar.

Orden de búsqueda de los ejecutables:
1. La variable de entorno `TVPPK_BIN_DIR`.
2. Junto al programa congelado (instalador: `<carpeta del ejecutable>/bin` o `sys._MEIPASS/bin`).
3. `third_party/bin/<plataforma>/` del repositorio (lo arma `scripts/build_rtklib.py`).
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from ..errors import Cancelled, UserError

ROOT = Path(__file__).resolve().parents[2]
RTKLIB_VERSION = "RTKLIB-EX 2.5.1 (rtklibexplorer, sucesor de demo5 b34L)"

Progress = Callable[[float, str], None]


def platform_tag() -> str:
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    if sys.platform == "win32":
        return f"windows-{'x64' if arch in ('x64', 'x86_64') else arch}"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    return f"linux-{'x86_64' if arch in ('x64', 'x86_64') else arch}"


def _candidates() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("TVPPK_BIN_DIR")
    if env:
        dirs.append(Path(env))
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass) / "bin")
        dirs.append(Path(sys.executable).resolve().parent / "bin")
    dirs.append(ROOT / "third_party" / "bin" / platform_tag())
    return dirs


def find_tool(name: str) -> Path:
    exe = name + (".exe" if sys.platform == "win32" else "")
    for folder in _candidates():
        path = folder / exe
        if path.is_file():
            return path
    raise UserError(f"Falta el programa «{name}» del motor de cálculo. Vuelve a instalar TresVizo Field PPK "
                    f"(en desarrollo: python scripts/build_rtklib.py).")


def check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def run(args: list[str], *, cwd: Path, progress: Progress | None = None, cancel: threading.Event | None = None,
        progress_parser: Callable[[str], tuple[float, str] | None] | None = None,
        log: list[str] | None = None, timeout_s: float | None = None) -> subprocess.CompletedProcess:
    """Corre un programa y devuelve su resultado; cancela (mata el proceso) si `cancel` se activa.

    `progress_parser` convierte cada línea de stderr del programa en (fracción, mensaje) para `progress`.
    """
    check(cancel)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    if log is not None:
        log.append("$ " + " ".join(_quote(a) for a in args))
    proc = subprocess.Popen(args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, creationflags=creationflags)
    stdout_chunks: list[bytes] = []
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(65536), b""):
            stdout_chunks.append(chunk)

    def read_stderr() -> None:
        assert proc.stderr is not None
        buffer = b""
        while True:
            chunk = proc.stderr.read(256)
            if not chunk:
                break
            buffer += chunk
            parts = re.split(rb"[\r\n]", buffer)
            buffer = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if progress_parser and progress:
                    parsed = progress_parser(line)
                    if parsed:
                        progress(*parsed)
                        continue
                stderr_lines.append(line)
        if buffer.strip():
            stderr_lines.append(buffer.decode("utf-8", "replace").strip())

    threads = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    for t in threads:
        t.start()
    started = time.monotonic()
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                raise Cancelled()
            if timeout_s is not None and time.monotonic() - started > timeout_s:
                proc.kill()
                proc.wait()
                raise UserError(f"El programa «{Path(args[0]).stem}» no terminó en {timeout_s:.0f} s.")
            time.sleep(0.05)
    finally:
        for t in threads:
            t.join(timeout=5)
    result = subprocess.CompletedProcess(args, proc.returncode, b"".join(stdout_chunks),
                                         "\n".join(stderr_lines))
    if log is not None:
        tail = [line for line in stderr_lines if line][-20:]
        log.extend("  " + line for line in tail)
        log.append(f"  (salida {proc.returncode}, {time.monotonic() - started:.1f} s)")
    return result


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg
