"""Los informes PDF se arman y dicen lo que tienen que decir (texto extraído con pypdf)."""
from __future__ import annotations

import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).parent))
import sinteticos as s  # noqa: E402

from tresvizo_ppk.crs import api as crs  # noqa: E402
from tresvizo_ppk.errors import UserError  # noqa: E402
from tresvizo_ppk.model import CoordinateChoice, Enu, Solution  # noqa: E402
from tresvizo_ppk.report import api, calculos, estilo, rtklib_salida  # noqa: E402
from tresvizo_ppk.version import __version__  # noqa: E402

LOGO = Path(__file__).resolve().parents[1] / "assets" / "marca.png"


def _read(pdf: Path) -> tuple[PdfReader, str]:
    reader = PdfReader(str(pdf))
    raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    return reader, re.sub(r"\s+", " ", raw)


def _images(reader: PdfReader) -> int:
    return sum(len(page.images) for page in reader.pages)


@pytest.fixture(scope="module")
def static_pdf(tmp_path_factory):
    folder = tmp_path_factory.mktemp("estatico")
    result = s.static_result(folder)
    project = s.static_project(folder)
    out = api.static_report(result, project, s.user(LOGO), folder / "informe.pdf")
    return out, result, project


@pytest.fixture(scope="module")
def points_pdf(tmp_path_factory):
    folder = tmp_path_factory.mktemp("puntos")
    result = s.points_result(folder)
    project = s.points_project(folder)
    out = api.points_report(result, project, s.user(LOGO), folder / "informe.pdf")
    return out, result, project


def test_informe_estatico_completo(static_pdf):
    out, result, project = static_pdf
    reader, t = _read(out)
    assert len(reader.pages) >= 8
    # Encabezado y pie
    for needle in ("Informe de posproceso de línea base", "Topografía de Prueba S.A. de C.V.",
                   "Responsable: Ing. Ana Prueba López", "Cédula profesional: 0000000 (prueba)",
                   "Proyecto n.º 2026-117", f"TresVizo Field PPK {__version__}", f"Página 1 de {len(reader.pages)}"):
        assert needle in t, needle
    # Resumen con la coordenada en el sistema elegido y en el marco oficial
    c = crs.transform(result.position, project.coordinates)
    for needle in ("Resumen · VERT-01", "Resultado: Aprobado", "FIJA (todas las líneas base)",
                   "México ITRF2008 / UTM zona 13N", f"{c.northing_m:.3f} m", f"{c.easting_m:.3f} m",
                   f"{c.orthometric_height_m:.3f} m", "Cota ortométrica (H)", "ITRF2008, época 2010.0",
                   f"{result.position.x:.3f} m", "Horizontal al 95 %", "Elipse de error (1σ)"):
        assert needle in t, needle
    # Por línea base: vector, longitud, acimut, covarianza; ajuste de red
    b = result.baselines[0]
    for needle in ("Línea base PRB1 → VERT-01", "Línea base PRB2 → VERT-01", "ΔX", "ΔE", "Distancia cenital",
                   f"{b.length_m:.4f} m", "Matriz de covarianza del vector (ECEF, m²)", "Correlaciones",
                   "Cociente (ratio)", "difundidas (de navegación)", "Ajuste de red", "Prueba χ²", "pasa",
                   "Grados de libertad", "0.216 – 9.348"):
        assert needle in t, needle
    # Calidad, gráficas, licencia del geoide y anexo
    for needle in ("Calidad de los datos del rover", "236 de 240 esperadas (98.3 %)", "Saltos de ciclo por satélite",
                   "PCO de la antena del rover sin medir", "Fuente: INEGI, Geoide Gravimétrico Mexicano 2010",
                   "Anexo: bitácora del proceso", "pos1-elmask =15", "no transforma entre marcos ni épocas"):
        assert needle in t, needle
    assert _images(reader) >= 1 + 2 * 5          # logo y cinco gráficas por línea base
    meta = reader.metadata
    assert meta.title == "Informe de posproceso de línea base"
    assert meta.creator == f"TresVizo Field PPK {__version__}"
    assert meta.author == "Topografía de Prueba S.A. de C.V."


def test_informe_de_puntos_completo(points_pdf):
    out, result, project = points_pdf
    reader, t = _read(out)
    assert len(reader.pages) >= 5
    for needle in ("Informe de PPK de la jornada", "Proyecto n.º 2026-118", "2 de 12 puntos sin solución fija",
                   "P08: flotante", "P12: sin solución", "se asume plomeado",
                   "estación de referencia, coordenada oficial de su red", "cinemático, combinado adelante y atrás",
                   "Calidad de cada punto", "Épocas fijas / total", "Trayectoria Épocas", "SIN SOLUCIÓN",
                   "FLOTANTE", "Anexo: bitácora del proceso"):
        assert needle in t, needle
    zone = crs.transform(result.points[0].position, project.coordinates).zone
    choice = CoordinateChoice(project.coordinates.crs_id, zone, project.coordinates.geoid_id)
    for p in result.points:
        assert p.point.name in t
        if p.position is not None:
            c = crs.transform(p.position, choice)
            assert f"{c.northing_m:.3f}" in t and f"{c.easting_m:.3f}" in t and f"{c.orthometric_height_m:.3f}" in t
    assert _images(reader) >= 1 + 6                # logo, trayectoria y las cinco de la solución


def test_sin_ajuste_con_una_base_y_en_geograficas(tmp_path):
    result = s.static_result(tmp_path, bases=1)
    project = s.static_project(tmp_path, CoordinateChoice("wgs84", "", None))
    out = api.static_report(result, project, s.user(), tmp_path / "una_base")
    assert out.suffix == ".pdf" and out.is_file()
    _, t = _read(out)
    assert "Resultado: Con avisos" not in t or "Con una sola base" in t
    assert "Con una sola base no hay redundancia" in t
    assert "Ajuste de red" not in t
    assert "WGS 84" in t and "Altura elipsoidal (h)" in t and "Norte" not in t.split("Coordenada final en el marco")[0]


def test_sin_archivos_de_rtklib_y_sistema_no_instalado(tmp_path):
    result = s.static_result(tmp_path)
    for b in result.baselines:
        b.pos_file.unlink()
        b.stat_file.unlink()
    project = s.static_project(tmp_path, CoordinateChoice("EPSG:9377", "", None))   # Colombia: paquete no instalado
    _, t = _read(api.static_report(result, project, s.user(), tmp_path / "x.pdf"))
    assert "Gráficas que faltan" in t and "linea_PRB1.pos" in t
    assert "No se pudo expresar en el sistema elegido" in t and "no está en este equipo" in t


def test_linea_flotante_da_avisos(tmp_path):
    result = s.static_result(tmp_path)
    result.baselines[0] = replace(result.baselines[0], solution=Solution.FLOAT, ratio=1.8)    # 6.7 km: es un fallo
    result.adjustment = replace(result.adjustment, passed=False, flagged_baselines=["PRB1"])
    _, t = _read(api.static_report(result, s.static_project(tmp_path), s.user(), tmp_path / "x.pdf"))
    for needle in ("Resultado: Con avisos", "quedó flotante", "no pasa la prueba χ²", "no cuadra",
                   "MIXTA: PRB1 FLOTANTE, PRB2 FIJA"):
        assert needle in t, needle


def test_linea_larga_flotante_no_parece_un_fallo(tmp_path):
    result = s.static_result(tmp_path)
    assert result.baselines[1].length_m > 60_000
    result.baselines[1] = replace(result.baselines[1], solution=Solution.FLOAT, ratio=None)
    _, t = _read(api.static_report(result, s.static_project(tmp_path), s.user(), tmp_path / "x.pdf"))
    assert "Resultado: Aprobado" in t and "Líneas largas con solución flotante" in t
    assert "Línea larga (99.3 km): se calcula libre de ionosfera" in t and "σ horizontal" in t
    assert "quedó flotante" not in t and "FIJA y FLOTANTE (líneas largas)" in t


def test_linea_excluida_del_ajuste_y_base_aproximada(tmp_path):
    result = s.static_result(tmp_path, bases=3)
    adj = result.adjustment
    residuals = {k: v for k, v in adj.residuals_enu.items() if k != "PRB3"}
    w = {k: Enu(0.5, -0.8, 1.1) for k in residuals}
    result.adjustment = replace(adj, residuals_enu=residuals, standardized_residuals=w, critical_value=3.29,
                                chi2_lower=0.48, chi2_upper=11.14, excluded_baselines=["PRB3"],
                                flagged_baselines=["PRB3"], degrees_of_freedom=3)
    result.bases[0] = replace(result.bases[0], approximate=True, coordinate_origin="rinex-header", official=False,
                              source="encabezado de PRB10293.26O")
    _, t = _read(api.static_report(result, s.static_project(tmp_path), s.user(), tmp_path / "x.pdf"))
    for needle in ("La línea base con PRB3 se dejó fuera del ajuste", "Línea base excluida del ajuste", "excluida",
                   "w máx. (crít. 3.29)", "0.480 – 11.140",
                   "Coordenada de la base PRB1 aproximada (posición del encabezado RINEX; encabezado de PRB10293.26O): "
                   "los puntos quedan bien entre sí pero desplazados lo mismo que la base."):
        assert needle in t, needle


def test_base_aproximada_en_el_informe_de_puntos(tmp_path):
    result = s.points_result(tmp_path)
    result.base = replace(result.base, approximate=True, coordinate_origin="autonomous", official=False, source="")
    project = replace(s.points_project(tmp_path), base_kind="own")
    _, t = _read(api.points_report(result, project, s.user(), tmp_path / "x.pdf"))
    assert t.count("los puntos quedan bien entre sí pero desplazados lo mismo que la base") >= 2
    assert "media de la solución autónoma de la sesión" in t


def test_nombres_en_espanol():
    from tresvizo_ppk.report.secciones import spanish_name
    assert spanish_name("Mexico ITRF2008 / UTM zona 13N") == "México ITRF2008 / UTM zona 13N"
    assert spanish_name("NAD27 / UTM zone 13N") == "NAD27 / UTM zona 13N (NAD27 / UTM zone 13N)"
    assert spanish_name("Mexico ITRF92 / UTM zone 12N") == "México ITRF92 / UTM zona 12N (Mexico ITRF92 / UTM zone 12N)"
    assert spanish_name("MAGNA-SIRGAS 2018 / Origen-Nacional") == "MAGNA-SIRGAS 2018 / Origen-Nacional"
    assert spanish_name("WGS 84") == "WGS 84"


def test_carpeta_sin_permiso(tmp_path):
    result = s.static_result(tmp_path)
    # Una «carpeta» que es un archivo: no se puede escribir ahí en ningún sistema.
    blocked = tmp_path / "archivo"
    blocked.write_text("")
    with pytest.raises(UserError):
        api.static_report(result, s.static_project(tmp_path), s.user(), blocked / "x.pdf")
    if sys.platform == "win32":
        return  # En Windows el permiso lo dan las ACL: chmod no le quita la escritura a una carpeta.
    locked = tmp_path / "cerrada"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(UserError):
            api.static_report(result, s.static_project(tmp_path), s.user(), locked / "x.pdf")
    finally:
        locked.chmod(0o700)


# --- Piezas ----------------------------------------------------------------------------------------------------------

def test_lector_pos_y_stat(tmp_path):
    result = s.static_result(tmp_path)
    pos = rtklib_salida.read_pos(result.baselines[0].pos_file)
    assert len(pos.epochs) == 240 and pos.skipped == 0 and not pos.geocentric
    assert pos.epochs[0].time == s.START and pos.epochs[0].q == 2 and pos.epochs[-1].q == 1
    stat = rtklib_salida.read_stat(result.baselines[0].stat_file)
    assert stat.satellites and stat.skipped == 0 and stat.satellites[0].time == s.START
    dops = rtklib_salida.dop_series(stat, 15)
    assert len(dops) == 240 and all(1.0 < d[1] < 3.0 and d[2] < d[1] for d in dops)


def test_lector_pos_geocentricas_y_utc(tmp_path):
    f = tmp_path / "x.pos"
    f.write_text("% (x/y/z-ecef=WGS84,Q=1:fix,2:float,3:sbas,4:dgps,5:single,6:ppp,ns=# of satellites)\n"
                 "%  UTC                   x-ecef(m)      y-ecef(m)      z-ecef(m)   Q  ns   sdx(m)   sdy(m)   sdz(m)"
                 "  sdxy(m)  sdyz(m)  sdzx(m) age(s)  ratio\n"
                 "2026/10/20 15:00:00.000  -1262837.2000  -5785732.6390   2365927.8790   1  21   0.0040   0.0050"
                 "   0.0060   0.0010  -0.0010   0.0010   1.00   25.3\n"
                 "línea que no se entiende\n", encoding="utf-8")
    pos = rtklib_salida.read_pos(f)
    assert pos.geocentric and pos.skipped == 1
    e = pos.epochs[0]
    assert e.time == s.START and e.x == -1262837.2 and e.q == 1 and e.ratio == 25.3


def test_calculos():
    assert calculos.chi2_bounds(3) == pytest.approx((0.215795, 9.348404), abs=1e-5)
    assert calculos.chi2_quantile(0.95, 2) == pytest.approx(5.991465, abs=1e-5)
    assert calculos.error_ellipse(4, 1, 0) == pytest.approx((2, 1, 90))
    assert calculos.error_ellipse(2.5, 2.5, 1.5) == pytest.approx((2, 1, 45))


def test_formatos():
    assert estilo.num(-0.0004, 3) == "0.000" and estilo.num(None) == "—" and estilo.num(1.23456, 2, "m") == "1.23 m"
    assert estilo.signed(0.0123, 3) == "+0.012" and estilo.signed(-1.5, 1) == "-1.5"
    assert estilo.lat_text(21.856) == "21°51′21.60000″ N" and estilo.lon_text(-102.284) == "102°17′02.40000″ O"
    assert estilo.lat_text(-0.9999999999999) == "1°00′00.00000″ S"
    assert estilo.duration_text(7170) == "1 h 59 min 30 s"
