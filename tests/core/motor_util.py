"""Utilidades compartidas por las pruebas del motor (se importa como módulo: pytest pone esta carpeta en sys.path)."""
from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent / "data"


def has_tools() -> bool:
    from tresvizo_ppk.core import tools
    try:
        tools.find_tool("rnx2rtkp")
        tools.find_tool("crx2rnx")
        return True
    except Exception:
        return False


needs_tools = pytest.mark.skipif(not has_tools(), reason="faltan los binarios de RTKLIB (scripts/build_rtklib.py)")


def gunzip(name: str, folder: Path, target: str | None = None) -> Path:
    out = folder / (target or name[:-3])
    with gzip.open(DATA / name, "rb") as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return out
