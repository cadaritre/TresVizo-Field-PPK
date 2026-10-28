"""Intermedios de la carpeta de trabajo: un solo juego de productos por proceso y limpieza al terminar.

Antes, cada línea base dejaba su ANTEX (6.5 MB), sus órbitas y relojes descomprimidos (el CLK de 30 s pesa 68 MB) y
sus ventanas de RINEX, y un proyecto de tres bases de 4 h llegaba a 205 MB. Ahora:

- **Productos compartidos** (`<trabajo>/productos/`): el ANTEX recortado a las antenas del proyecto se escribe una
  vez y lo usan todas las líneas base; las órbitas y relojes precisos se descomprimen (o se enlazan, o se copian si
  están en otro disco) una sola vez en `productos/orbitas/`, de donde RTKLIB y el procesador propio los toman con
  enlaces duros. Las órbitas descomprimidas se borran al terminar: se rehacen de las entradas del proyecto.
- **Lo que solo sirve durante el proceso** se borra al terminar (bien o mal): las ventanas recortadas de las bases,
  el RINEX unido de cada base, y lo que se descomprimió de las entradas (`entrada/`, `rinex/`) salvo lo que el
  resultado sigue usando (el RINEX del rover y su navegación, que se exportan desde el resultado).
- **Se queda** lo que el informe y la bitácora necesitan: `.pos`, `.stat` y `.conf` de cada línea base o de la
  trayectoria, el ANTEX usado, el RINEX del rover, las efemérides bajadas y la bitácora del proceso.

Nunca se borra nada fuera de la carpeta de trabajo ni ningún archivo que haya entregado el usuario.
"""
from __future__ import annotations

import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

from ..model import ObservationSet
from . import tools

PRODUCTS_DIR = "productos"
ORBITS_DIR = "orbitas"
LOG_NAME = "Bitácora del proceso.txt"
COMPRESSED = (".gz", ".z", ".zip")


def _resolved(path: Path) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(os.path.abspath(path))


def inside(path: Path, folder: Path) -> bool:
    """¿`path` está dentro de `folder` (y no es la carpeta misma)?"""
    p, f = _resolved(path), _resolved(folder)
    return p != f and f in p.parents


class Products:
    """El juego de productos de un proceso: ANTEX y órbitas una sola vez para todas las líneas base.

    Se usa como contexto: al salir se borran las órbitas descomprimidas (el ANTEX se queda: lo nombra la
    configuración de RTKLIB que guarda la carpeta de trabajo)."""

    def __init__(self, workdir: Path, cancel: threading.Event | None = None) -> None:
        self.dir = Path(workdir) / PRODUCTS_DIR
        self.cancel = cancel
        self._orbits = self.dir / ORBITS_DIR
        self._antex: Path | None = None

    def __enter__(self) -> "Products":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def antex(self) -> Path:
        """El ANTEX del proyecto (el de la sesión de antenas en curso), escrito una sola vez."""
        if self._antex is None:
            from . import antex
            self._antex = antex.ensure_antex(self.dir)
        return self._antex

    def precise(self, files: list[Path]) -> list[Path]:
        """Las órbitas y relojes listos para leer, todos en `productos/orbitas/` (mismo disco que la carpeta de
        trabajo, para que cada proceso los enlace en vez de copiarlos)."""
        if not files:
            return []
        from . import compress
        if self._orbits.exists():
            shutil.rmtree(self._orbits)
        self._orbits.mkdir(parents=True)
        out: list[Path] = []
        for source in (Path(p) for p in files):
            tools.check(self.cancel)
            if source.suffix.lower() in COMPRESSED:
                out.extend(compress.unpack([source], self._orbits, cancel=self.cancel))
                continue
            target = self._orbits / source.name
            if target.exists():
                target = self._orbits / f"{len(out):03d}_{source.name}"
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
            out.append(target)
        return out

    def close(self) -> None:
        shutil.rmtree(self._orbits, ignore_errors=True)


def discard(workdir: Path, *, keep: list[ObservationSet], drop: list[ObservationSet]) -> int:
    """Borra lo preparado para el proceso que ya no hace falta y devuelve los bytes liberados.

    `keep`: juegos que el resultado sigue usando (el rover): se quedan sus `obs` y `nav`. `drop`: juegos que solo
    sirvieron para procesar (las bases unidas y sus ventanas): se borran sus `obs`. De la carpeta de cada juego se
    vacían `entrada/` y `rinex/` (lo descomprimido o convertido antes de unir). Solo dentro de `workdir`."""
    workdir = _resolved(workdir)
    protect: set[Path] = set()
    for s in list(keep) + list(drop):
        protect.update(_resolved(p) for p in s.source_files)
    for s in keep:
        protect.update(_resolved(p) for p in list(s.obs) + list(s.nav))
    freed = 0
    candidates: list[Path] = []
    folders: set[Path] = set()
    for s in list(keep) + list(drop):
        for p in s.obs[:1]:
            folder = _resolved(p).parent
            if inside(folder, workdir) or folder == workdir:
                folders.add(folder)
    for s in drop:
        candidates.extend(_resolved(p) for p in s.obs)
    for folder in folders:
        for sub in ("entrada", "rinex"):
            d = folder / sub
            if d.is_dir() and inside(d, workdir):
                candidates.extend(p for p in d.rglob("*") if p.is_file())
    for path in dict.fromkeys(candidates):
        if path in protect or not inside(path, workdir) or not path.is_file():
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            freed += size
        except OSError:
            continue
    for folder in folders:
        for sub in ("entrada", "rinex"):
            _remove_empty(folder / sub)
    return freed


def _remove_empty(folder: Path) -> None:
    if not folder.is_dir():
        return
    for d in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass


def write_log(workdir: Path, title: str, log: list[str], warnings: list[str]) -> Path:
    """La bitácora del proceso en texto, junto al informe: se queda aunque se limpien los intermedios."""
    lines = [f"{title} — {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z}", "", *log]
    if warnings:
        lines += ["", "Avisos:", *(f"· {w}" for w in warnings)]
    path = Path(workdir) / LOG_NAME
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
