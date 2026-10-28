"""Exportación de los puntos ajustados de la red (export.api.export_network) con la red real de la RGNA."""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import red_datos  # noqa: E402

from tresvizo_ppk.errors import UserError  # noqa: E402
from tresvizo_ppk.export import api  # noqa: E402
from tresvizo_ppk.model import CoordinateChoice  # noqa: E402


@pytest.fixture(scope="module")
def result():
    return red_datos.network_result(red_datos.without_ineg())


def _rows(path: Path) -> list[dict]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def test_field_csv_and_simple_csv(result, tmp_path):
    choice = CoordinateChoice(geoid_id=None)
    rows = _rows(api.export_network(result, choice, "field_csv", tmp_path / "red"))
    assert [r["Name"] for r in rows] == ["UGTO", "TOL2", "IHID"]
    ugto = rows[0]
    assert ugto["Solution status"] == "FIX" and ugto["Correction type"] == "Static"
    assert "ajuste de red restringido (ISLP, UQRO fijas)" in ugto["Static adjustment"]
    assert "UQRO-UGTO" in ugto["Static baselines"] and float(ugto["Easting RMS"]) < 0.01
    simple = _rows(api.export_network(result, choice, "csv", tmp_path / "simple"))
    assert [r["Nombre"] for r in simple] == ["UGTO", "TOL2", "IHID"]
    # el restringido no pasa la χ² (las fijas difieren unos 2 cm de 2010 a hoy): queda «a revisar»
    assert all(r["Estado"] == "Revisar" for r in simple)


def test_geojson_kml_dxf_landxml(result, tmp_path):
    choice = CoordinateChoice(geoid_id=None)
    doc = json.loads(api.export_network(result, choice, "geojson", tmp_path / "red").read_text(encoding="utf-8"))
    assert len(doc["features"]) == 3 and doc["metadata"]["fijas"] == ["ISLP", "UQRO"]
    props = doc["features"][0]["properties"]
    assert props["nombre"] == "UGTO" and props["ocupaciones"] == 3 and len(props["elipse_95_m"]) == 3
    for formato, suffix in (("kml", ".kml"), ("dxf", ".dxf"), ("landxml", ".xml")):
        out = api.export_network(result, choice, formato, tmp_path / f"red{suffix}", project="Red RGNA")
        text = out.read_text(encoding="utf-8", errors="replace")
        assert "UGTO" in text and "IHID" in text, formato


def test_custom_csv_and_no_points(result, tmp_path):
    options = api.CsvOptions(layout="custom", custom=(("name", ""), ("staticAdjustment", "Ajuste"),
                                                      ("qcStatus", "")))
    rows = _rows(api.export_network(result, CoordinateChoice(geoid_id=None), "csv", tmp_path / "medida",
                                    csv_options=options))
    assert rows[0]["Ajuste"].startswith("ajuste de red restringido")
    from dataclasses import replace
    with pytest.raises(UserError, match="no tiene puntos"):
        api.export_network(replace(result, points=[]), CoordinateChoice(), "csv", tmp_path / "x")
