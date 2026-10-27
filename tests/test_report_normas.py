"""El informe conforme a las normas técnicas del INEGI (DOF 23-12-2010), con el texto leído del PDF con pypdf.

- NT EEP (Estándares de Exactitud Posicional): art. 6 «CEP95 = 1.2238 (σφ + σλ)», art. 7 «EPV = 1.9600σ», art. 8
  tabla 1 (órdenes de exactitud horizontal) y art. 12 «EPV = 1.9600 (σ²GNSS + σ²N)^½».
- NT SGN (Sistema Geodésico Nacional): art. 10 (ITRF08 época 2010.0, GRS80), art. 12 fr. I y II (vinculados a la
  RGNA) y art. 15 fr. III (cota con el modelo geoidal vigente del INEGI).
"""
from __future__ import annotations

import math
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).parent))
import sinteticos as s  # noqa: E402

from tresvizo_ppk import project as proj  # noqa: E402
from tresvizo_ppk.crs import api as crs  # noqa: E402
from tresvizo_ppk.model import CoordinateChoice, Ecef, Enu, TransformedCoordinate  # noqa: E402
from tresvizo_ppk.report import api, calculos, normas  # noqa: E402

GGM25 = CoordinateChoice("mexico-itrf2008-utm", "", "ggm25")
GGM10 = CoordinateChoice("mexico-itrf2008-utm", "", "ggm10")
ELLIPSOIDAL = CoordinateChoice("mexico-itrf2008-utm", "", None)
DECLARATION = "Levantamiento ligado a la Red Geodésica Nacional Activa del INEGI"


def _pages(pdf: Path) -> list[str]:
    return [re.sub(r"\s+", " ", page.extract_text() or "") for page in PdfReader(str(pdf)).pages]


def _rgna(st):
    return replace(st, network="rgna-inegi", network_name="RGNA — INEGI, México")


def _noaa(st):
    p = st.position
    return replace(st, network="noaa-cors", network_name="NOAA CORS (NGS)",
                   position=Ecef(p.x, p.y, p.z, "NAD83(2011)", 2010.0))


def _static(folder: Path, station=_rgna, choice=GGM25, place="Aguascalientes, Ags.", sigma: Enu | None = None):
    result = s.static_result(folder)
    result.bases = [station(b) for b in result.bases]
    for b in result.baselines:
        b.base = station(b.base)
    if result.bases[0].position.frame != result.position.frame:
        p, b = result.position, result.bases[0].position
        result.position = Ecef(p.x, p.y, p.z, b.frame, b.epoch)
    if sigma is not None:
        result.sigma_enu = sigma
    project = s.static_project(folder, choice)
    project.place = place
    return result, project


# --- Fórmulas, a mano ------------------------------------------------------------------------------------------------

def test_cep95_epv_con_sigmas_distintas():
    # σφ = σN = 0.004 m, σλ = σE = 0.012 m: CEP95 = 1.2238 × 0.016 = 0.0195808 m (art. 6).
    assert calculos.cep95(0.004, 0.012) == pytest.approx(0.0195808, abs=1e-10)
    # El círculo al 95 % no es la cifra de la norma cuando σE ≠ σN: 2.447747 × √((0.012² + 0.004²)/2) = 0.0218933 m.
    assert calculos.horizontal_95(0.012, 0.004) == pytest.approx(0.0218933, abs=1e-6)
    # σφ = 0.021, σλ = 0.003: 1.2238 × 0.024 = 0.0293712 m.
    assert calculos.cep95(0.021, 0.003) == pytest.approx(0.0293712, abs=1e-10)
    # Art. 7: EPV = 1.96 × 0.015 = 0.0294 m. Art. 12: 1.96 × √(0.015² + 0.10²) = 1.96 × 0.1011187 = 0.1981926 m.
    assert calculos.epv(0.015) == pytest.approx(0.0294, abs=1e-12)
    assert calculos.epv_gnss_leveling(0.015, 0.10) == pytest.approx(0.1981926, abs=1e-6)
    # Con σE = σN las dos cifras horizontales coinciden (2 × 1.2238 = 2.4476 ≈ 2.447747).
    assert calculos.cep95(0.01, 0.01) == pytest.approx(calculos.horizontal_95(0.01, 0.01), rel=1e-4)


def test_borde_de_cada_orden_de_la_tabla_1():
    orders = calculos.ACCURACY_ORDERS
    assert [name for _, name in orders] == [
        "1 centímetro", "2 centímetros", "5 centímetros", "1 decímetro", "2 decímetros", "5 decímetros", "1 metro",
        "2 metros", "5 metros", "10 metros", "20 metros", "50 metros", "100 metros", "200 metros", "500 metros"]
    for k, (limit, name) in enumerate(orders):
        assert calculos.accuracy_order(limit / 1000) == name, limit                # «Hasta …» / «… a X metros»
        nxt = orders[k + 1][1] if k + 1 < len(orders) else None
        assert calculos.accuracy_order((limit + 1) / 1000) == nxt, limit           # «De X + 0.001 a …»
    assert calculos.accuracy_order(0.0) == "1 centímetro"
    # Se clasifica al milímetro, como se imprime: 0.01049 → «0.010»; 0.01051 → «0.011».
    assert calculos.accuracy_order(0.01049) == "1 centímetro"
    assert calculos.accuracy_order(0.01051) == "2 centímetros"
    assert calculos.accuracy_order(None) is None and calculos.accuracy_order(float("nan")) is None
    # Un CEP95 de la norma en el borde: σφ = 0.005, σλ = 0.0035 → 1.2238 × 0.0085 = 0.0104023 → 0.010 → 1 cm.
    assert calculos.accuracy_order(calculos.cep95(0.005, 0.0035)) == "1 centímetro"
    # σφ = 0.005, σλ = 0.0037 → 0.01064706 → 0.011 → 2 cm.
    assert calculos.accuracy_order(calculos.cep95(0.005, 0.0037)) == "2 centímetros"


# --- Informe estático ------------------------------------------------------------------------------------------------

def test_estatico_cifras_de_la_norma_y_declaracion(tmp_path):
    sigma = Enu(0.012, 0.004, 0.015)            # σE ≠ σN
    result, project = _static(tmp_path, sigma=sigma)
    pages = _pages(api.static_report(result, project, s.user(), tmp_path / "x.pdf"))
    t = " ".join(pages)
    c = crs.transform(result.position, project.coordinates)
    sigma_n = c.geoid_uncertainty_m
    assert sigma_n == pytest.approx(0.10)          # GGM25: precisión declarada en su ficha
    for needle in ("CEP95 (NT EEP art. 6) 0.020 m",                                  # 1.2238 × 0.016 = 0.0196
                   "Horizontal al 95 % 0.022 m",                                     # el círculo: 0.0219
                   "Orden de exactitud horizontal (NT EEP art. 8, tabla 1) 2 centímetros",
                   "Orden de exactitud 2 centímetros (CEP95 0.020 m, NT EEP art. 8)",
                   "EPV de la altura elipsoidal (NT EEP art. 7) 0.029 m",           # 1.96 × 0.015
                   "EPV de la cota ortométrica (NT EEP art. 12) 0.198 m, con σN = 0.100 m",
                   "coinciden si σE = σN", "circuitos cerrados"):
        assert needle in t, needle
    last = pages[-1]
    assert "Declaración y firma" in last and DECLARATION in last
    for needle in ("estaciones PRB1, PRB2", "ITRF2008 época 2010.0", "Norma Técnica para el Sistema Geodésico Nacional",
                   "Norma Técnica de Estándares de Exactitud Posicional (DOF 23-12-2010)", "(GGM25), INEGI",
                   "NT SGN art. 15, fr. III", "Lugar y fecha Aguascalientes, Ags.,", "Nombre Ing. Ana Prueba López",
                   "Cédula profesional 0000000 (prueba)", "Empresa Topografía de Prueba S.A. de C.V.", "Firma", "Sello"):
        assert needle in last, needle
    assert DECLARATION not in " ".join(pages[:-1])


def test_estatico_altura_y_carpeta(tmp_path):
    result, project = _static(tmp_path)
    t = " ".join(_pages(api.static_report(result, project, s.user(), tmp_path / "x.pdf")))
    head, annex = t.split("Anexo: bitácora del proceso")
    assert "Altura de la antena a la ARP 1.800 m" in head and "Altura del bastón" not in head
    assert str(project.folder) not in head.replace(" ", "") and "Carpeta " not in head
    assert "Carpeta del proyecto:" in annex
    result.rover = replace(result.rover, receiver_model="MeridianV", case_offset_m=0.116)
    t = " ".join(_pages(api.static_report(result, project, s.user(), tmp_path / "y.pdf")))
    assert "Altura del bastón o trípode 1.800 m" in t


@pytest.mark.parametrize("case", ["noaa", "ggm10", "marco", "aproximada", "propia"])
def test_estatico_sin_declaracion(tmp_path, case):
    station, choice = _rgna, GGM25
    if case == "noaa":
        station = _noaa
    elif case == "ggm10":
        choice = GGM10
    result, project = _static(tmp_path, station=station, choice=choice)
    if case == "marco":
        p = result.position
        result.position = Ecef(p.x, p.y, p.z, "ITRF2014", 2024.5)
    elif case == "aproximada":
        result.bases[0] = replace(result.bases[0], approximate=True, coordinate_origin="rinex-header", official=False)
    elif case == "propia":
        result.bases = [replace(b, network="", network_name="", official=False, coordinate_origin="manual")
                        for b in result.bases]
    last = _pages(api.static_report(result, project, s.user(), tmp_path / "x.pdf"))[-1]
    assert DECLARATION not in last and "Declaración y firma" in last and "Firma" in last
    expected = {
        "noaa": "Levantamiento ligado a NOAA CORS (NGS) (estaciones PRB1, PRB2), con las coordenadas oficiales de su "
                "red en NAD83(2011), época 2010.0",
        "ggm10": "la cota usa Geoide Gravimétrico Mexicano 2010 (GGM10), INEGI, que no es el modelo geoidal vigente "
                 "del INEGI (NT SGN art. 15, fr. III); el vigente es Geoide Gravimétrico Mexicano 2025 (GGM25), INEGI",
        "marco": "el resultado queda en ITRF2014, época 2024.5 y la NT SGN (art. 10) pide ITRF2008, época 2010.0",
        "aproximada": "la coordenada de la base PRB1 es aproximada",
        "propia": "PRB1 es una base propia, sin la coordenada oficial de una red",
    }[case]
    assert expected in last, last
    if case != "noaa":
        assert "No se imprime la declaración de conformidad con las normas técnicas del INEGI" in last


def test_declaracion_sin_cota_y_con_geoide_ajeno():
    st = _rgna(s.stations()[0])
    pos = st.position
    # Sin cota (alturas elipsoidales) el art. 15 no aplica: va la declaración y dice que no hay cota.
    d = normas.declaration([st], pos, TransformedCoordinate(21.0, -102.0, 1880.0), True)
    assert d.kind == "inegi" and "Alturas elipsoidales sobre GRS80" in d.text
    # Con un geoide que no es del INEGI (EGM2008 de la NGA), no.
    c = TransformedCoordinate(21.0, -102.0, 1880.0, orthometric_height_m=1890.0, undulation_m=-10.0,
                              geoid_id="egm2008", geoid_name="EGM2008 2008 · NGA", geoid_uncertainty_m=0.1)
    d = normas.declaration([st], pos, c, True)
    assert d.kind == "none" and "la cota usa EGM2008 2008 · NGA" in d.reasons[0]
    assert normas.declaration([st], pos, c, False).kind == "none"
    # Una estación de la RGNA corregida a mano no es la coordenada oficial.
    d = normas.declaration([replace(st, official=False)], pos, None, True)
    assert d.kind == "none" and "se corrigió a mano" in d.reasons[0]


def test_estatico_sin_lugar_deja_la_raya(tmp_path):
    result, project = _static(tmp_path, choice=ELLIPSOIDAL, place="")
    last = _pages(api.static_report(result, project, s.user(), tmp_path / "x.pdf"))[-1]
    assert DECLARATION in last and "Alturas elipsoidales sobre GRS80" in last
    assert re.search(r"Lugar y fecha _{10,}, \d{1,2} de [a-z]+ de \d{4}", last)


# --- Informe de PPK --------------------------------------------------------------------------------------------------

def _points(folder: Path, base=_rgna, choice=GGM25):
    result = s.points_result(folder)
    result.base = base(result.base)
    project = s.points_project(folder, choice)
    project.place = "Zacatecas, Zac."
    return result, project


def test_ppk_orden_por_punto_y_declaracion(tmp_path):
    result, project = _points(tmp_path)
    pages = _pages(api.points_report(result, project, s.user(), tmp_path / "x.pdf"))
    t = " ".join(pages)
    assert "Exactitud posicional (NT EEP, DOF 23-12-2010)" in t and "Orden de exactitud" in t
    for p in result.points:
        if p.sigma_enu is None or p.position is None:
            continue
        cep = 1.2238 * (p.sigma_enu.n + p.sigma_enu.e)                          # art. 6, a mano
        assert f"{cep:.3f}" in t
        order = calculos.accuracy_order(cep)
        assert f"{p.point.name} " in t and order in t
    summary = pages[0]
    assert "Orden de exactitud (NT EEP art. 8)" in summary
    last = pages[-1]
    assert DECLARATION in last and "(estaciones PRB1)" in last and "Lugar y fecha Zacatecas, Zac.," in last


def test_ppk_sin_declaracion_con_noaa_y_base_propia(tmp_path):
    result, project = _points(tmp_path, base=_noaa, choice=ELLIPSOIDAL)
    last = _pages(api.points_report(result, project, s.user(), tmp_path / "x.pdf"))[-1]
    assert DECLARATION not in last and "Levantamiento ligado a NOAA CORS (NGS)" in last
    result, project = _points(tmp_path, base=lambda st: replace(st, network="", official=False,
                                                                coordinate_origin="project"))
    last = _pages(api.points_report(result, project, s.user(), tmp_path / "y.pdf"))[-1]
    assert DECLARATION not in last and "es una base propia" in last


def test_lugar_se_guarda_en_el_proyecto(tmp_path):
    project = s.static_project(tmp_path)
    project.place = "Mérida, Yuc."
    assert proj.from_dict(proj.to_dict(project, tmp_path), tmp_path).place == "Mérida, Yuc."
    data = proj.to_dict(project, tmp_path)
    del data["place"]
    assert proj.from_dict(data, tmp_path).place == ""       # proyectos de antes: vacío


def test_sin_glifos_que_falten():
    from tresvizo_ppk.report.documento import text
    assert text("W₀ = 62 636 856,0 m²s⁻²") == "W<sub>0</sub> = 62 636 856,0 m²s<super>−2</super>"
    assert not math.isnan(calculos.cep95(0, 0))
