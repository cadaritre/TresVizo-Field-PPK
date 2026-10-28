"""Informe de ajuste de red con la red real de la RGNA (15-07-2026): lo que tiene que decir, como el de TBC."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).parent))
import red_datos  # noqa: E402
import sinteticos as s  # noqa: E402

from tresvizo_ppk.model import CoordinateChoice, Project  # noqa: E402
from tresvizo_ppk.report import api  # noqa: E402

LOGO = Path(__file__).resolve().parents[1] / "assets" / "marca.png"


def _text(pdf: Path) -> tuple[PdfReader, str]:
    reader = PdfReader(str(pdf))
    return reader, re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in reader.pages))


def _project(folder: Path, geoid="ggm10") -> Project:
    return Project(kind="network", name="Red de control RGNA", number="2026-196", folder=folder,
                   coordinates=CoordinateChoice(geoid_id=geoid), place="Aguascalientes, Ags.",
                   created=datetime(2026, 7, 15, tzinfo=timezone.utc),
                   processed=datetime(2026, 7, 16, tzinfo=timezone.utc))


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    folder = tmp_path_factory.mktemp("red")
    result = red_datos.network_result(red_datos.without_ineg())
    out = api.network_report(result, _project(folder), s.user(LOGO), folder / "informe.pdf")
    return out, result


def test_report_sections(report):
    out, result = report
    reader, text = _text(out)
    assert len(reader.pages) >= 6
    for section in ("Informe de ajuste de red", "Resumen", "Coordenadas ajustadas", "Exactitud posicional (NT EEP)",
                    "Esquema", "Estaciones y ocupaciones", "Líneas base", "Cierres de bucle",
                    "Ajuste mínimamente restringido", "Comprobación del control", "Ajuste restringido",
                    "Elipses relativas al 95 %", "Control de calidad", "Declaración y firma"):
        assert section in text, section
    for name in ("UGTO", "TOL2", "IHID", "S1:INEG-UGTO", "trivial", "τ E / N / U"):
        assert name in text, name
    # el orden de la NT EEP de cada punto y el datum del restringido
    assert "ISLP, UQRO fijas" in text and "INEG" in text
    assert re.search(r"UGTO \S+ (\d+ centímetros?|\d+ milímetros?)", text) or "centímetro" in text
    assert "Cierre de bucle en planta" in text and "0.050 m + 1.0 ppm" in text
    assert _images(reader) >= 1


def _images(reader):
    return sum(len(page.images) for page in reader.pages)


def test_declaration_only_with_official_rgna_and_current_geoid(tmp_path):
    result = red_datos.network_result(red_datos.without_ineg())
    out = api.network_report(result, _project(tmp_path, geoid=None), s.user(), tmp_path / "a.pdf")
    _, text = _text(out)
    # la RGNA con sus coordenadas oficiales en ITRF2008 2010.0 y sin cota: va la declaración del INEGI
    assert "Red Geodésica Nacional Activa del INEGI" in text and "ISLP, UQRO" in text
    # con una cota de un geoide que no es el vigente del INEGI, no va
    out = api.network_report(result, _project(tmp_path, geoid="ggm10"), s.user(), tmp_path / "b.pdf")
    _, text = _text(out)
    assert "No se imprime la declaración" in text


def test_failed_adjustment_is_said_in_the_summary(tmp_path):
    result = red_datos.network_result()          # con INEG fija: el restringido no pasa
    out = api.network_report(result, _project(tmp_path), s.user(), tmp_path / "c.pdf")
    _, text = _text(out)
    assert "El ajuste no pasa todas sus pruebas" in text
    assert "Fijas que no concuerdan con las demás: INEG" in text
