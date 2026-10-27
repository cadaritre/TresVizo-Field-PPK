"""Exportación para CAD y del estático: DXF R12, LandXML 1.2, CSV a medida (PNEZD, PENZD…), el vértice del estático,
la trayectoria del PPK y el RINEX de la sesión."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import sinteticos as s  # noqa: E402

from tresvizo_ppk.crs import api as crs  # noqa: E402
from tresvizo_ppk.errors import UserError  # noqa: E402
from tresvizo_ppk.export import api, columnas, dxf, landxml  # noqa: E402
from tresvizo_ppk.model import CoordinateChoice, Solution  # noqa: E402

ezdxf = pytest.importorskip("ezdxf")

OFICINA = Path(__file__).parent / "datos" / "apps" / "oficina"
LANDXML_NS = "{http://www.landxml.org/schema/LandXML-1.2}"
# El esquema oficial de LandXML 1.2 (landxml.org, 225 951 bytes) no va en el repo: no declara licencia. Se baja una vez
# a la carpeta de datos (`docs/estado.md`) y la prueba comprueba su suma.
XSD_SHA256 = "f4f732341970a923943f94592a3fde0838534515d564a699761e85420fad4d77"
XSD = Path(os.environ.get("TVPPK_LANDXML_XSD",
                          Path.home() / "Documents" / "TresVizo-Field-PPK-datos" / "landxml" / "LandXML-1.2.xsd"))
WEIRD_NAME = "Vértice 1, Peña «Ñ»"
WEIRD_CODE = "Árbol, ñandú"
WEIRD_DESC = 'Junto al mojón "La Peña", lado norte'


@pytest.fixture()
def result(tmp_path):
    r = s.points_result(tmp_path)
    reference = (Path(__file__).parent / "datos" / "apps" / "csv_referencia_todas.csv").read_bytes().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(reference, newline="")))
    template = dict(zip(rows[0], rows[2]))
    points = []
    for i, p in enumerate(r.points):
        name, code = (WEIRD_NAME, WEIRD_CODE) if i == 0 else (p.point.name, p.point.code)
        row = dict(template, Name=name, Code=code, Description=WEIRD_DESC if i == 0 else "")
        points.append(replace(p, point=replace(p.point, name=name, code=code, row=row)))
    r.points = points
    return r


@pytest.fixture()
def static(tmp_path):
    r = s.static_result(tmp_path)
    r.rover.pole_height_m = 1.7789
    r.rover.case_offset_m = 0.116
    return r


def _choice(result_position) -> CoordinateChoice:
    zone = crs.transform(result_position, CoordinateChoice("mexico-itrf2008-utm", "", None)).zone
    return CoordinateChoice("mexico-itrf2008-utm", zone, "ggm10")


# --- DXF --------------------------------------------------------------------------------------------------------------

def test_dxf_se_lee_de_vuelta_con_capas_puntos_y_rotulos(result, tmp_path):
    out = api.export_points(result, CoordinateChoice(), "dxf", tmp_path / "plano")
    assert out.name == "plano.dxf"
    data = out.read_bytes()
    assert data.startswith(b"999\r\nTresVizo Field PPK") and data.endswith(b"0\r\nEOF\r\n")
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    assert "Peña".encode("cp1252") in data and b"\xc3" not in data          # Windows-1252, no UTF-8
    prj = out.with_suffix(".prj").read_text("ascii")
    assert prj.startswith('PROJCS["Mexico_ITRF2008_UTM_Zone_13N"') and prj == api.esri_wkt("EPSG:6368")

    doc = ezdxf.readfile(out)
    assert doc.dxfversion == "AC1009" and doc.encoding == "cp1252"
    auditor = doc.audit()
    assert not auditor.has_errors, [str(e) for e in auditor.errors]
    layers = {layer.dxf.name: layer.dxf.color for layer in doc.layers if layer.dxf.name != "0"}
    code_layer = dxf.sanitized_layer(WEIRD_CODE)
    assert code_layer == "ARBOL__NANDU"
    assert {"PUNTOS", "TEXTO", "DESCRIPCION", "COTA", "DESPLAZAMIENTO_DE_BASE", code_layer, "POSTE", "BN"} <= set(layers)
    assert layers["TEXTO"] == 7 and layers["DESCRIPCION"] == 8 and layers["COTA"] == 30
    msp = doc.modelspace()
    points = list(msp.query("POINT"))
    processed = [p for p in result.points if p.solution != Solution.NONE]
    assert len(points) == len(processed) == 11
    texts = list(msp.query("TEXT"))
    choice = api._fixed_choice(processed[0].position, CoordinateChoice())
    for entity, pp in zip(points, processed):
        c = crs.transform(pp.position, choice)
        x, y, z = entity.dxf.location
        assert (x, y, z) == pytest.approx((c.easting_m, c.northing_m, c.orthometric_height_m), abs=6e-4)
        assert entity.dxf.layer == (code_layer if pp is processed[0] else dxf.layer_name(pp.point.code))
        name = next(t for t in texts if t.dxf.layer == "TEXTO" and t.dxf.text == pp.point.name)
        assert tuple(name.dxf.insert) == pytest.approx((x, y, z), abs=1e-9) and name.dxf.height == 0.5
        elevation = next(t for t in texts if t.dxf.layer == "COTA" and tuple(t.dxf.insert)[0] == x)
        assert elevation.dxf.text == f"{c.orthometric_height_m:.3f}"
        assert elevation.dxf.insert[1] == pytest.approx(y - 0.7, abs=1e-3)
    description = [t for t in texts if t.dxf.layer == "DESCRIPCION"]
    assert [t.dxf.text for t in description] == [WEIRD_DESC]
    assert description[0].dxf.insert[1] == pytest.approx(points[0].dxf.location[1] - 1.4, abs=1e-3)
    assert next(t for t in texts if t.dxf.text == WEIRD_NAME)          # acentos, comas y «» de vuelta
    comments = [line for line in data.decode("cp1252").split("\r\n")[1::2][:12]]
    assert "sistema: EPSG:6368 Mexico ITRF2008 / UTM zona 13N" in comments
    assert any(c.startswith("Z: ortometrica (H)   modelo: Geoide Gravimetrico Mexicano 2010") for c in comments)
    assert "sin posproceso (no van en el dibujo): P12" in comments


def test_dxf_como_el_de_las_apps_byte_a_byte():
    """Sin el rótulo de la cota, el archivo de `SurveyExport.dxf` de las apps, escrito a mano desde su código."""
    points = [dxf.DxfPoint("P1", "Poste", 397512.3454, 3168004.2104, 1432.1184, "Junto a la caseta"),
              dxf.DxfPoint("P2", "", 397530.0, 3168010.0, None)]
    out = dxf.write(points, ["TresVizo Field PPK: coordenadas en metros, X = Este, Y = Norte", "marco: ITRF2008"],
                    dxf.DxfOptions(elevation_labels=False))
    h = 2166136261
    for b in b"POSTE":
        h = ((h ^ b) * 16777619) % 2 ** 32
    poste = (1, 2, 3, 4, 5, 6, 30, 40, 84, 140, 200, 220)[h % 12]
    expected = [
        "999", "TresVizo Field PPK: coordenadas en metros, X = Este, Y = Norte", "999", "marco: ITRF2008",
        "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$DWGCODEPAGE", "3", "ANSI_1252",
        "9", "$INSUNITS", "70", "6", "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", "6",
        "0", "LAYER", "2", "DESCRIPCION", "70", "0", "62", "8", "6", "CONTINUOUS",
        "0", "LAYER", "2", "DESPLAZAMIENTO_DE_BASE", "70", "0", "62", "6", "6", "CONTINUOUS",
        "0", "LAYER", "2", "POSTE", "70", "0", "62", str(poste), "6", "CONTINUOUS",
        "0", "LAYER", "2", "PUNTOS", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "LAYER", "2", "SIN_COTA", "70", "0", "62", "1", "6", "CONTINUOUS",
        "0", "LAYER", "2", "TEXTO", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES",
        "0", "POINT", "8", "POSTE", "10", "397512.345", "20", "3168004.210", "30", "1432.118",
        "0", "TEXT", "8", "TEXTO", "10", "397512.345", "20", "3168004.210", "30", "1432.118", "40", "0.500", "1", "P1",
        "0", "TEXT", "8", "DESCRIPCION", "10", "397512.345", "20", "3168003.510", "30", "1432.118", "40", "0.500",
        "1", "Junto a la caseta",
        "999", "sin cota: P2",
        "0", "POINT", "8", "SIN_COTA", "10", "397530.000", "20", "3168010.000",
        "0", "TEXT", "8", "SIN_COTA", "10", "397530.000", "20", "3168010.000", "40", "0.500", "1", "P2",
        "0", "ENDSEC", "0", "EOF"]
    assert out == ("\r\n".join(expected) + "\r\n").encode("ascii")


def test_dxf_texto_y_capas_como_las_apps():
    assert dxf.windows1252("Peña €‰ 測") == b"Pe\xf1a \x80\x89 \\U+6E2C"
    assert dxf.sanitized_layer("Línea de cerca/norte #2") == "LINEA_DE_CERCA_NORTE__2"
    assert len(dxf.sanitized_layer("x" * 40)) == 31 and dxf.layer_name("") == "PUNTOS"
    assert dxf.declaration_chunks(["á" * 500]) == ["a" * 240, "a" * 240, "a" * 20]
    assert dxf.sanitized("uno\r\ndos") == "uno  dos"


def test_dxf_sin_cota_ni_rotulos_y_geograficas(result, tmp_path):
    choice = CoordinateChoice("mexico-itrf2008-utm", "", "no-existe")
    data = api.export_points(result, choice, "dxf", tmp_path / "x", dxf_options=dxf.DxfOptions(point_names=False))
    doc = ezdxf.readfile(tmp_path / "x.dxf")
    points = list(doc.modelspace().query("POINT"))
    assert points and all(p.dxf.layer == "SIN_COTA" and not p.dxf.hasattr("location") or p.dxf.location.z == 0
                          for p in points)
    assert b"\r\n30\r\n" not in data.read_bytes().split(b"ENTITIES")[1]          # nunca una Z = 0 inventada
    assert not list(doc.modelspace().query("TEXT"))
    with pytest.raises(UserError, match="DXF necesita un sistema proyectado"):
        api.export_points(result, CoordinateChoice("wgs84", "", None), "dxf", tmp_path / "g")
    with pytest.raises(UserError, match="LandXML necesita un sistema proyectado"):
        api.export_points(result, CoordinateChoice("wgs84", "", None), "landxml", tmp_path / "g")


# --- LandXML ----------------------------------------------------------------------------------------------------------

def _reference_document() -> tuple[landxml.Document, list[str]]:
    project = json.loads((OFICINA / "proyecto_oficina.json").read_text("utf-8"))
    reference = (OFICINA / "Prueba_oficina.xml").read_text("utf-8").split("\n")
    # Lo que la oficina escribe: los puntos sin atributos ni fotos (los otros llevan un Feature dentro, que el
    # esquema no admite) y sin figuras (PlanFeatures).
    simple = [p for p in project["points"] if not p["attributes"] and not p["photos"]]
    points = [landxml.CgPoint(name=p["name"], northing=p["northing"], easting=p["easting"],
                              elevation=p["elevation"] or "", code=p["code"] or "", desc=p["description"] or "",
                              timestamp=api._iso_from_app_time(p["observedAt"] or "")) for p in simple]
    wkt = api.esri_wkt(project["crs"]["code"])
    doc = landxml.Document(project=project["project"], application_version=project["appVersion"],
                           exported_at=datetime.fromisoformat(project["exportedAt"]),
                           crs=landxml.CoordinateSystem(project["crs"]["name"], "6368", "Mexico ITRF2008", "GGM10",
                                                        wkt),
                           points=points, application="TresVizo Field")
    start = reference.index("  <CgPoints>")
    names = {p.name for p in points}
    expected = reference[:start + 1]
    expected += [line for line in reference if line.startswith("    <CgPoint ") and line.endswith("</CgPoint>")
                 and line.split('"')[1] in names]
    expected += ["  </CgPoints>", "</LandXML>", ""]
    return doc, expected


def test_landxml_byte_a_byte_con_la_referencia_de_las_apps():
    assert hashlib.sha256((OFICINA / "Prueba_oficina.xml").read_bytes()).hexdigest() == \
        "e2bb0a504c43aa2c09e34b1c8556f21ad1900bc52a60a6e72ff82471b8a0c3f2"
    doc, expected = _reference_document()
    assert len(doc.points) == 10
    assert landxml.write(doc) == "\n".join(expected).encode("utf-8")


def _schema():
    etree = pytest.importorskip("lxml.etree")
    if not XSD.is_file():
        pytest.skip(f"falta el esquema de LandXML 1.2 en {XSD} (docs/estado.md dice de dónde se baja)")
    assert hashlib.sha256(XSD.read_bytes()).hexdigest() == XSD_SHA256
    return etree, etree.XMLSchema(etree.parse(str(XSD)))


def test_landxml_del_ppk_y_del_estatico_pasan_el_esquema(result, static, tmp_path):
    etree, schema = _schema()
    now = datetime(2026, 10, 27, 9, 30, tzinfo=timezone(timedelta(hours=-6)))
    ppk = api.export_points(result, CoordinateChoice(), "landxml", tmp_path / "ppk", project="Jornada, Peña", now=now)
    assert ppk.name == "ppk.xml"
    vertex = api.export_static(static, CoordinateChoice(), "landxml", tmp_path / "vertice", code="VERT",
                               description="Mojón de concreto, «norte»", now=now)
    for path in (ppk, vertex):
        doc = etree.parse(str(path))
        assert schema.validate(doc), str(schema.error_log)
    root = ET.parse(ppk).getroot()
    assert root.get("date") == "2026-10-27" and root.get("time") == "09:30:00"
    cs = root.find(f"{LANDXML_NS}CoordinateSystem")
    assert cs.get("name") == "Mexico ITRF2008 / UTM zone 13N" and cs.get("epsgCode") == "6368"
    assert cs.get("verticalDatum") == "GGM10" and cs.get("horizontalDatum") == "Mexico ITRF2008"
    assert cs.get("ogcWktCode") == api.esri_wkt("EPSG:6368")
    assert root.find(f"{LANDXML_NS}Project").get("name") == "Jornada, Peña"
    unit = root.find(f"{LANDXML_NS}Units/{LANDXML_NS}Metric")
    assert unit.get("linearUnit") == "meter"
    points = root.findall(f"{LANDXML_NS}CgPoints/{LANDXML_NS}CgPoint")
    assert len(points) == 11
    first = points[0]
    assert first.get("name") == WEIRD_NAME and first.get("code") == WEIRD_CODE and first.get("desc") == WEIRD_DESC
    choice = api._fixed_choice(result.points[0].position, CoordinateChoice())
    c = crs.transform(result.points[0].position, choice)
    assert first.text == f"{c.northing_m:.3f} {c.easting_m:.3f} {c.orthometric_height_m:.3f}"   # N E Z
    assert first.get("timeStamp") == "2026-10-15T12:47:05.8-06:00"     # el de la fila de campo, con su desfase
    v = ET.parse(vertex).getroot().find(f"{LANDXML_NS}CgPoints/{LANDXML_NS}CgPoint")
    assert v.get("name") == "VERT-01" and v.get("code") == "VERT" and v.get("timeStamp").endswith("+00:00")


def test_la_referencia_de_las_apps_no_pasa_el_esquema():
    """Lo que se avisa a las apps (`docs/estado.md`): `Feature` dentro de `CgPoint` y `code` en `PlanFeature` no son
    LandXML 1.2 válido."""
    etree, schema = _schema()
    assert not schema.validate(etree.parse(str(OFICINA / "Prueba_oficina.xml")))
    messages = " ".join(str(e) for e in schema.error_log)
    assert "CgPoint" in messages and "PlanFeature" in messages


# --- CSV a medida -----------------------------------------------------------------------------------------------------

def _rows(path: Path, separator=","):
    data = path.read_bytes()
    text = data.decode("utf-8-sig")
    return data, list(csv.reader(io.StringIO(text, newline=""), delimiter=separator))


@pytest.mark.parametrize("layout, header, columns", [
    ("pnezd", True, ["Name", "Northing", "Easting", "Elevation", "Description"]),
    ("penzd", True, ["Name", "Easting", "Northing", "Elevation", "Description"]),
    ("nxyzd", True, ["Name", "Easting", "Northing", "Elevation", "Description"]),
    ("pnezd", False, None),
    ("penzd", False, None),
])
def test_csv_a_medida_plantillas(result, tmp_path, layout, header, columns):
    options = api.CsvOptions(layout=layout, header=header)
    data, rows = _rows(api.export_points(result, CoordinateChoice(), "csv", tmp_path / "p", csv_options=options))
    assert data.startswith(b"\xef\xbb\xbf") and data.endswith(b"\r\n")
    if header:
        assert rows[0] == columns
        rows = rows[1:]
    assert len(rows) == 11                                  # P12 no se posprocesó
    choice = api._fixed_choice(result.points[0].position, CoordinateChoice())
    c = crs.transform(result.points[0].position, choice)
    n, e, z = f"{c.northing_m:.3f}", f"{c.easting_m:.3f}", f"{c.orthometric_height_m:.3f}"
    expected = [WEIRD_NAME, n, e, z, WEIRD_DESC] if layout == "pnezd" else [WEIRD_NAME, e, n, z, WEIRD_DESC]
    assert rows[0] == expected
    raw = data.decode("utf-8-sig").split("\r\n")[1 if header else 0]
    assert raw.startswith('"Vértice 1, Peña «Ñ»",') and raw.endswith('"Junto al mojón ""La Peña"", lado norte"')


def test_csv_a_medida_espanol_separadores_bom_coma_decimal_y_personalizada(result, tmp_path):
    options = api.CsvOptions(layout="pnezd", language="es", separator=";", bom=False, decimal_comma=True)
    data, rows = _rows(api.export_points(result, CoordinateChoice(), "csv", tmp_path / "a", csv_options=options), ";")
    assert not data.startswith(b"\xef\xbb\xbf")
    assert rows[0] == ["Nombre", "Norte", "Este", "Cota", "Descripción"]
    assert "," in rows[1][1] and "." not in rows[1][1] and rows[1][0] == WEIRD_NAME   # coma decimal, el nombre igual
    options = api.CsvOptions(layout="penzd", separator=",", decimal_comma=True)       # con coma no cabe: punto
    _, rows = _rows(api.export_points(result, CoordinateChoice(), "csv", tmp_path / "b", csv_options=options))
    assert "." in rows[1][1]
    options = api.CsvOptions(layout="pnezd", separator="\t")
    data, rows = _rows(api.export_points(result, CoordinateChoice(), "csv", tmp_path / "t", csv_options=options), "\t")
    assert rows[1][0] == WEIRD_NAME and b'"' not in data.split(b"\r\n")[1][:30]        # con tabulador, sin comillas
    options = api.CsvOptions(layout="custom", custom=(("code", "Código Civil"), ("name", ""), ("ppkSolution", ""),
                                                      ("ellipsoidalHeight", "h")))
    _, rows = _rows(api.export_points(result, CoordinateChoice(), "csv", tmp_path / "c", csv_options=options))
    assert rows[0] == ["Código Civil", "Name", "PPK solution", "h"] and rows[1][:3] == [WEIRD_CODE, WEIRD_NAME, "FIX"]
    options = api.CsvOptions(layout="all", language="es")
    _, rows = _rows(api.export_points(result, CoordinateChoice(), "csv", tmp_path / "d", csv_options=options))
    assert rows[0] == [es for _, _, es in columnas.COLUMNS] + [es for _, _, es in columnas.PPK_COLUMNS]
    for bad, message in ((api.CsvOptions(layout="xyz"), "Disposición"), (api.CsvOptions(separator="|"), "separador"),
                         (api.CsvOptions(layout="custom"), "ninguna columna"),
                         (api.CsvOptions(layout="custom", custom=(("nada", ""),)), "desconocidas")):
        with pytest.raises(UserError, match=message):
            api.export_points(result, CoordinateChoice(), "csv", tmp_path / "e", csv_options=bad)


def test_csv_a_medida_en_geograficas_lleva_longitud_y_latitud(result, tmp_path):
    options = api.CsvOptions(layout="penzd")
    _, rows = _rows(api.export_points(result, CoordinateChoice("wgs84", "", None), "csv", tmp_path / "g",
                                      csv_options=options))
    assert rows[1][1].startswith("-102.") and len(rows[1][1].split(".")[1]) == 8


# --- Estático ---------------------------------------------------------------------------------------------------------

def test_estatico_csv_de_field_para_reimportar(static, tmp_path):
    out = api.export_static(static, CoordinateChoice(), "field_csv", tmp_path / "vertice", code="BN",
                            description="Mojón, lado «norte»")
    data, rows = _rows(out)
    header = rows[0]
    assert header[:94] == [en for _, en, _ in columnas.COLUMNS]
    assert header[94:] == [en for _, en, _ in api.STATIC_COLUMNS] and len(rows) == 2
    row = dict(zip(header, rows[1]))
    choice = api._fixed_choice(static.position, CoordinateChoice())
    c = crs.transform(static.position, choice)
    sigma = crs.transform_sigma(static.position, static.sigma_enu, choice, static.adjustment.covariance_ecef)
    assert row["Name"] == "VERT-01" and row["Code"] == "BN" and row["Description"] == "Mojón, lado «norte»"
    assert row["Easting"] == f"{c.easting_m:.3f}" and row["Northing"] == f"{c.northing_m:.3f}"
    assert row["Elevation"] == f"{c.orthometric_height_m:.3f}" and row["Geoid"] == "GGM10"
    assert row["Latitude"] == f"{c.receiver_latitude_deg:.8f}" and row["Ellipsoidal height"] == f"{c.ellipsoidal_height_m:.3f}"
    assert row["Easting RMS"] == f"{sigma.e:.3f}" and row["Elevation RMS"] == f"{sigma.u:.3f}"
    assert row["Solution status"] == "FIX" and row["Origin"] == "Global" and row["Correction type"] == "Static"
    assert row["Antenna height"] == "1.779" and row["Antenna offset"] == "0.1160" and row["Total antenna height"] == "1.895"
    assert row["CS code"] == "EPSG:6368" and row["Frame"] == "ITRF2008"
    assert row["Base easting"] == "" and row["Baseline"] == ""                 # dos bases: no hay «la» base
    assert row["Averaging start"] == "2026-10-20 15:00:00.0 UTC+00:00" and row["Samples"] == "240"
    assert row["Static solution"] == "FIX" and "PRB1" in row["Static baselines"] and "PRB2" in row["Static baselines"]
    assert "mínimos cuadrados con 2 líneas base" in row["Static adjustment"]
    assert row["Processed with"].startswith("TresVizo Field PPK")
    # Con una sola base, sus columnas
    (tmp_path / "una").mkdir()
    one = s.static_result(tmp_path / "una", bases=1)
    _, rows = _rows(api.export_static(one, CoordinateChoice(), "field_csv", tmp_path / "una.csv"))
    row = dict(zip(rows[0], rows[1]))
    assert row["Base easting"] and row["Base elevation reference"] == "ARP"
    assert row["Baseline"] == f"{one.baselines[0].length_m:.3f}" and row["Static adjustment"].startswith("una sola")


def test_estatico_en_todos_los_formatos(static, tmp_path):
    for formato in api.FORMATS:
        out = api.export_static(static, CoordinateChoice(), formato, tmp_path / f"v-{formato}", code="VERT")
        assert out.is_file() and out.stat().st_size > 60, formato
    gj = json.loads((tmp_path / "v-geojson.geojson").read_text("utf-8"))
    assert len(gj["features"]) == 1 and gj["features"][0]["properties"]["solucion"] == "FIX"
    assert gj["metadata"]["bases"] and gj["metadata"]["ajuste"].startswith("mínimos cuadrados")
    kml = ET.parse(tmp_path / "v-kml.kml").getroot()
    assert len(kml.findall(".//{http://www.opengis.net/kml/2.2}Placemark")) == 1
    doc = ezdxf.readfile(tmp_path / "v-dxf.dxf")
    (point,) = doc.modelspace().query("POINT")
    assert point.dxf.layer == "VERT"
    assert (tmp_path / "v-dxf.prj").is_file()
    _, rows = _rows(api.export_static(static, CoordinateChoice(), "csv", tmp_path / "pnezd",
                                      csv_options=api.CsvOptions(layout="pnezd", header=False)))
    assert len(rows) == 1 and rows[0][0] == "VERT-01"
    _, rows = _rows(api.export_static(static, CoordinateChoice(), "csv", tmp_path / "simple"))
    assert rows[0] == ["Nombre", "Este", "Norte", "Cota", "Código"] and rows[1][4] == ""


# --- Trayectoria y RINEX ----------------------------------------------------------------------------------------------

def _xyz_pos(result, path: Path) -> Path:
    """Un `.pos` como el que escribe el motor (out-solformat=xyz): una época por segundo sobre los puntos."""
    lines = ["% program   : RTKLIB-EX 2.5.1",
             "%  GPST                      x-ecef(m)      y-ecef(m)      z-ecef(m)   Q  ns   sdx(m)   sdy(m)   sdz(m)"
             "  sdxy(m)  sdyz(m)  sdzx(m) age(s)  ratio"]
    t0 = datetime(2026, 10, 20, 15, 0, 18)
    for i, pp in enumerate(p for p in result.points if p.position is not None):
        q = 1 if pp.solution == Solution.FIXED else 2
        p = pp.position
        lines.append(f"{t0 + timedelta(seconds=i):%Y/%m/%d %H:%M:%S}.000 {p.x:14.4f} {p.y:14.4f} {p.z:14.4f} "
                     f"{q:3d} {20:3d} {0.006:8.4f} {0.005:8.4f} {0.012:8.4f} {0.002:8.4f} {-0.001:8.4f} "
                     f"{0.001:8.4f} {1.0:6.2f} {12.3:6.1f}")
    path.write_text("\n".join(lines) + "\n", "ascii")
    return path


def test_trayectoria_pos_y_csv(result, tmp_path):
    result.pos_file = _xyz_pos(result, tmp_path / "salida.pos")
    pos = api.export_trajectory(result, CoordinateChoice(), "pos", tmp_path / "trayectoria")
    assert pos.name == "trayectoria.pos" and pos.read_bytes() == result.pos_file.read_bytes()
    out = api.export_trajectory(result, CoordinateChoice(), "csv", tmp_path / "trayectoria")
    _, rows = _rows(out)
    assert rows[0] == api.TRAJECTORY_HEADER
    from tresvizo_ppk.core.rtklib import parse_pos
    epochs = parse_pos(result.pos_file)
    assert len(rows) - 1 == len(epochs) == 11
    first = dict(zip(rows[0], rows[1]))
    assert first["Época UTC"] == "2026-10-20T15:00:00.000Z"          # GPST − 18 s
    assert first["Solución"] == "FIX" and first["Satélites"] == "20" and first["Razón"] == "12.3"
    choice = api._fixed_choice(result.points[0].position, CoordinateChoice())
    c = crs.transform(result.points[0].position, choice)
    assert first["Este"] == f"{c.easting_m:.3f}" and first["Cota de la antena"] == f"{c.orthometric_height_m:.3f}"
    assert first["Latitud"] == f"{c.receiver_latitude_deg:.9f}"
    r = crs.enu_rotation(c.receiver_latitude_deg, c.receiver_longitude_deg)
    cov = epochs[0].cov
    sigma = [sum(r[a][k] * cov[k][m] * r[a][m] for k in range(3) for m in range(3)) ** 0.5 for a in range(3)]
    assert [first["σ este local"], first["σ norte local"], first["σ vertical"]] == [f"{v:.3f}" for v in sigma]
    with pytest.raises(UserError, match="desconocido"):
        api.export_trajectory(result, CoordinateChoice(), "gpx", tmp_path / "x")
    result.pos_file = tmp_path / "no-existe.pos"
    with pytest.raises(UserError, match="vuelve a procesar"):
        api.export_trajectory(result, CoordinateChoice(), "csv", tmp_path / "x")


def test_rinex_de_la_sesion(tmp_path):
    work = tmp_path / "trabajo"
    work.mkdir()
    (work / "VERT01.26O").write_bytes(b"     3.04           OBSERVATION DATA\n")
    (work / "VERT01.26P").write_bytes(b"     3.04           NAVIGATION DATA\n")
    obs = s.static_result(tmp_path).rover
    obs = replace(obs, obs=[work / "VERT01.26O"], nav=[work / "VERT01.26P"])
    written = api.export_rinex(obs, tmp_path / "José Ñúñez" / "entrega")
    assert [p.name for p in written] == ["VERT01.26O", "VERT01.26P"]
    assert all(p.read_bytes() == (work / p.name).read_bytes() for p in written)
    assert not list((tmp_path / "José Ñúñez" / "entrega").glob(".*"))
    (work / "VERT01.26P").unlink()
    with pytest.raises(UserError, match="vuelve a procesar"):
        api.export_rinex(obs, tmp_path / "otra")
