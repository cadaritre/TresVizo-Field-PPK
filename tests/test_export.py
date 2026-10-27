"""Exportar los puntos posprocesados: CSV de TresVizo Field, CSV simple, GeoJSON y KML."""
from __future__ import annotations

import csv
import io
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import sinteticos as s  # noqa: E402

from tresvizo_ppk.crs import api as crs  # noqa: E402
from tresvizo_ppk.errors import UserError  # noqa: E402
from tresvizo_ppk.export import api, columnas  # noqa: E402
from tresvizo_ppk.model import CoordinateChoice, Solution  # noqa: E402

REFERENCE = Path(__file__).parent / "datos" / "apps" / "csv_referencia_todas.csv"
KML = "{http://www.opengis.net/kml/2.2}"


def _reference_rows() -> tuple[list[str], list[list[str]]]:
    text = REFERENCE.read_bytes().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    return rows[0], rows[1:]


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf") and b"\r\n" in data
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    return rows[0], [dict(zip(rows[0], r)) for r in rows[1:]]


@pytest.fixture()
def result(tmp_path):
    r = s.points_result(tmp_path)
    header, rows = _reference_rows()
    template = dict(zip(header, rows[1]))            # la fila 2: descripción con coma, punto flotante con inclinación
    points = []
    for p in r.points:
        row = dict(template, Name=p.point.name, Code=p.point.code)
        points.append(replace(p, point=replace(p.point, row=row)))
    r.points = points
    return r


def test_el_escritor_reproduce_el_csv_de_las_apps_byte_a_byte():
    header, rows = _reference_rows()
    out = api.write_rows(header, [[api.neutralized(v) for v in row] for row in rows])
    assert out == REFERENCE.read_bytes()


def test_csv_de_tresvizo_field(result, tmp_path):
    choice = CoordinateChoice()
    out = api.export_points(result, choice, "field_csv", tmp_path / "puntos")
    assert out.name == "puntos.csv"
    header, rows = _read_csv(out)
    ref_header, _ = _reference_rows()
    assert header == ref_header + [en for _, en, _ in columnas.PPK_COLUMNS]
    assert len(rows) == 12
    zone = crs.transform(result.points[0].position, CoordinateChoice(choice.crs_id, "", None)).zone
    fixed = CoordinateChoice(choice.crs_id, zone, choice.geoid_id)
    for p, row in zip(result.points, rows):
        assert row["Name"] == p.point.name and row["Description"] == "Pc, junto al poste"
        assert row["Device serial number"] == "MV-74E8280C1A2B"        # lo de la ocupación se queda
        if p.solution == Solution.NONE:
            assert row["Easting"] == "399602.111" and row["PPK solution"] == "NONE"   # las de campo, y lo dice
            assert "coordenadas de campo" in row["PPK message"] or row["PPK message"]
            continue
        c = crs.transform(p.position, fixed)
        assert row["Easting"] == f"{c.easting_m:.3f}" and row["Northing"] == f"{c.northing_m:.3f}"
        assert row["Elevation"] == f"{c.orthometric_height_m:.3f}"
        assert row["Ellipsoidal height"] == f"{c.ellipsoidal_height_m:.3f}"
        assert row["Latitude"] == f"{c.receiver_latitude_deg:.8f}"
        assert row["CS name"] == f"Mexico ITRF2008 / UTM zona {zone} + GGM10 height"
        assert row["CS code"] == c.crs_code and row["Geoid"] == "GGM10" and row["Frame"] == "ITRF2008"
        assert row["Solution status"] == ("FIX" if p.solution == Solution.FIXED else "FLOAT")
        assert row["Correction type"] == "PPK" and row["Mount point"] == "" and row["Caster"] == ""
        assert row["Base receiver"] == "TRIMBLE NETR9" and row["Base elevation reference"] == "ARP"
        assert row["PPK base"] == "PRB1 (Estación de prueba 1)" and row["Processed with"].startswith("TresVizo Field PPK")
        assert row["Field easting"] and float(row["Field delta horizontal"]) < 0.1
        assert row["PPK epochs"] == str(p.epochs_total)
        assert row["Samples"] == str(p.epochs_fixed if p.solution == Solution.FIXED else p.epochs_total)


def test_csv_en_espanol_y_sin_filas(result, tmp_path):
    spanish = {en: es for _, en, es in columnas.COLUMNS}
    for p in result.points:
        p.point.row = {spanish.get(k, k): v for k, v in p.point.row.items()}
    header, rows = _read_csv(api.export_points(result, CoordinateChoice(), "field_csv", tmp_path / "es.csv"))
    assert header[0] == "Nombre" and "Solución PPK" in header and "Este de campo" in header
    for p in result.points:
        p.point.row = {}
    header, rows = _read_csv(api.export_points(result, CoordinateChoice(), "field_csv", tmp_path / "vacio.csv"))
    assert header[:94] == [en for _, en, _ in columnas.COLUMNS] and len(header) == 94 + len(columnas.PPK_COLUMNS)
    assert rows[0]["Name"] == "P01" and rows[0]["Easting"]


def test_csv_simple(result, tmp_path):
    header, rows = _read_csv(api.export_points(result, CoordinateChoice(), "csv", tmp_path / "simple.csv"))
    assert header == ["Nombre", "Este", "Norte", "Cota", "Código"]
    assert [r["Nombre"] for r in rows] == [f"P{i:02d}" for i in range(1, 12)]   # P12 no se posprocesó
    header, rows = _read_csv(api.export_points(result, CoordinateChoice("wgs84", "", None), "csv", tmp_path / "g.csv"))
    assert header == ["Nombre", "Longitud", "Latitud", "Cota", "Código"]
    assert rows[0]["Longitud"].startswith("-102.") and len(rows[0]["Longitud"].split(".")[1]) == 8


def test_geojson_y_kml_dicen_lo_mismo(result, tmp_path):
    gj = json.loads(api.export_points(result, CoordinateChoice(), "geojson", tmp_path / "p").read_text("utf-8"))
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == 11
    meta = gj["metadata"]
    assert meta["puntos_sin_posproceso"] == ["P12"] and meta["marco"] == "ITRF2008" and meta["epoca_marco"] == 2010.0
    assert "INEGI" in " ".join(meta["licencia_geoide"])
    f0 = gj["features"][0]
    lon, lat, h = f0["geometry"]["coordinates"]
    c = crs.transform(result.points[0].position, CoordinateChoice())
    assert (lon, lat) == (c.receiver_longitude_deg, c.receiver_latitude_deg)
    assert f0["properties"]["este_m"] == round(c.easting_m, 3) and f0["properties"]["solucion"] == "FIX"
    assert h == round(c.ellipsoidal_height_m, 3) and f0["properties"]["altura_en_geometria"] == "elipsoidal_del_suelo"

    path = api.export_points(result, CoordinateChoice(), "kml", tmp_path / "p")
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf") and b"\r\n" not in data and data.endswith(b"\n")
    doc = ET.fromstring(data)
    placemarks = doc.findall(f".//{KML}Placemark")
    assert len(placemarks) == 11
    first = placemarks[0]
    keys = [d.get("name") for d in first.findall(f"{KML}ExtendedData/{KML}Data")]
    assert keys == list(f0["properties"].keys())
    coords = first.find(f".//{KML}coordinates").text.split(",")
    assert len(coords[0].split(".")[1]) == 9 and float(coords[0]) == pytest.approx(lon, abs=1e-9)


def test_errores(result, tmp_path):
    with pytest.raises(UserError, match="desconocido"):
        api.export_points(result, CoordinateChoice(), "dxf", tmp_path / "x")
    empty = replace(result, points=[p for p in result.points if p.solution == Solution.NONE])
    with pytest.raises(UserError, match="No hay puntos"):
        api.export_points(empty, CoordinateChoice(), "geojson", tmp_path / "x")


def test_formatos_de_numero_y_texto():
    assert api.number(-0.0004, 3) == "0.000" and api.number(None, 3) == "" and api.number(2.0005, 3) == "2.001"
    assert api.neutralized("=SUMA(A1)") == "'=SUMA(A1)" and api.neutralized("-12.5") == "-12.5"
    assert api.neutralized("-abc") == "'-abc" and api.quoted("#12") == '"#12"' and api.quoted('a"b') == '"a""b"'
