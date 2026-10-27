"""Fixtures de las pruebas del motor."""
from __future__ import annotations

import pytest

from motor_util import gunzip


@pytest.fixture
def unzip(tmp_path):
    """Descomprime un .gz de tests/core/data en tmp_path y devuelve la ruta."""
    def _unzip(name: str, target: str | None = None):
        return gunzip(name, tmp_path, target)
    return _unzip
