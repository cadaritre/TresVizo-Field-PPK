"""Localización de proyecto (calibración local) contra los vectores de las apps TresVizo Field
(`tests/datos/apps/localization_vectors.json`, ver FUENTE.md), un caso de cuatro pares resuelto a mano, la cuadrícula
base por el sistema del proyecto, el registro del contrato, la importación de un proyecto de las apps y la exportación
en locales."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import sys
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tresvizo_ppk.crs import api as crs  # noqa: E402
from tresvizo_ppk.crs import localizacion as L  # noqa: E402
from tresvizo_ppk.errors import UserError  # noqa: E402
from tresvizo_ppk.model import (CoordinateChoice, Ecef, Localization, LocalizationControl,  # noqa: E402
                                LocalizationMeasured, LocalizationPair)

VECTORS = json.loads((Path(__file__).parent / "datos" / "apps" / "localization_vectors.json").read_text("utf-8"))
TOL = VECTORS["tolerances"]


# --- Vectores de las apps ---------------------------------------------------------------------------------------------

def _observations(case) -> list[L.Observation]:
    return [L.Observation(p["name"], p["grid"]["e"], p["grid"]["n"], p["baseHeight"], p["local"]["e"],
                          p["local"]["n"], p["local"]["z"], p["useH"], p["useV"]) for p in case["pairs"]]


def _options(case) -> L.Options:
    o = case["options"]
    return L.Options(o["horizontalMethod"], o["verticalMethod"], o["horizontalToleranceMeters"],
                     o["verticalToleranceMeters"])


def _close(actual, expected, tol, what):
    if expected is None:
        assert actual is None, what
    else:
        assert actual is not None and abs(actual - expected) <= tol, f"{what}: {actual} != {expected}"


def _assert_solution(s: L.Solution, e: dict, name: str, metres: float, ratio: float, ppm: float, arcsec: float,
                     probes: bool = True):
    h, eh = s.horizontal, e["horizontal"]
    assert (h.requested_method, h.method) == (eh["requestedMethod"], eh["method"]), name
    for actual, key, tol in ((h.a, "a", ratio), (h.b, "b", ratio), (h.scale, "scale", ratio),
                             (h.scale_ppm, "scalePpm", ppm), (h.rotation_rad, "rotationRadians", ratio),
                             (h.azimuth_rotation_arcsec, "azimuthRotationArcsec", arcsec),
                             (h.translation_e, "translationE", metres), (h.translation_n, "translationN", metres),
                             (h.sigma0_m, "sigma0Meters", metres), (h.scale_sigma_ppm, "scaleSigmaPpm", ppm),
                             (h.rotation_sigma_arcsec, "rotationSigmaArcsec", arcsec)):
        _close(actual, eh[key], tol, f"{name} {key}")
    for actual, (group, axis) in ((h.ec, ("gridCentroid", "e")), (h.nc, ("gridCentroid", "n")),
                                  (h.Ec, ("localCentroid", "e")), (h.Nc, ("localCentroid", "n"))):
        _close(actual, eh[group][axis], metres, f"{name} {group}.{axis}")
    assert h.redundancy == eh["redundancy"], name
    v, ev = s.vertical, e["vertical"]
    assert (v.requested_method, v.method) == (ev["requestedMethod"], ev["method"]), name
    for actual, key, tol in ((v.e0, None, metres), (v.c0, "constantMeters", metres), (v.cN, "slopeNorth", ratio),
                             (v.cE, "slopeEast", ratio), (v.slope_north_ppm, "slopeNorthPpm", ppm),
                             (v.slope_east_ppm, "slopeEastPpm", ppm), (v.sigma0_m, "sigma0Meters", metres)):
        if key:
            _close(actual, ev[key], tol, f"{name} {key}")
    _close(v.e0, ev["origin"]["e"], metres, f"{name} e0")
    _close(v.n0, ev["origin"]["n"], metres, f"{name} n0")
    assert v.redundancy == ev["redundancy"], name
    assert len(s.residuals) == len(e["residuals"]), name
    for r, er in zip(s.residuals, e["residuals"]):
        assert r.name == er["name"]
        for actual, key in ((r.dE, "dE"), (r.dN, "dN"), (r.dH, "dH"), (r.dZ, "dZ"), (r.loo_dH, "leaveOneOutDH"),
                            (r.loo_dZ, "leaveOneOutDZ")):
            _close(actual, er[key], metres, f"{name} {r.name} {key}")
        assert (r.used_h, r.used_v) == (er["usedH"], er["usedV"]), f"{name} {r.name}"
    assert [w.code for w in s.warnings] == [w["code"] for w in e["warnings"]], name
    for w, ew in zip(s.warnings, e["warnings"]):
        if "valuePpm" in ew:
            _close(w.value_ppm, ew["valuePpm"], ppm, f"{name} {w.code}")
        if "valueMeters" in ew:
            _close(w.value_m, ew["valueMeters"], metres, f"{name} {w.code}")
        if "pair" in ew:
            assert (w.pair_name, w.is_check) == (ew["pair"], ew["isCheck"]), f"{name} {w.code}"
    if not probes:
        return
    t = s.transform
    assert len(e["forward"]) == len(e["inverse"])
    for fw, iv in zip(e["forward"], e["inverse"]):
        E, N = t.forward(fw["grid"]["e"], fw["grid"]["n"])
        _close(E, fw["local"]["e"], metres, f"{name} directa E")
        _close(N, fw["local"]["n"], metres, f"{name} directa N")
        _close(t.elevation(fw["baseHeight"], fw["grid"]["e"], fw["grid"]["n"]), fw["local"]["z"], metres,
               f"{name} directa Z")
        e2, n2 = t.inverse(iv["local"]["e"], iv["local"]["n"])
        _close(e2, iv["grid"]["e"], metres, f"{name} inversa e")
        _close(n2, iv["grid"]["n"], metres, f"{name} inversa n")
        _close(t.base_height(iv["local"]["z"], e2, n2), iv["baseHeight"], metres, f"{name} inversa v")
        ce, cn = t.inverse(E, N)       # directa e inversa cierran a 1e-7 m (§2.3)
        assert abs(ce - fw["grid"]["e"]) < 1e-7 and abs(cn - fw["grid"]["n"]) < 1e-7, name


def test_el_archivo_de_vectores_es_el_esperado():
    assert VECTORS["version"] == 1
    assert len(VECTORS["cases"]) == 21
    assert len(VECTORS["pipelineUTM13N"]) == 6
    assert hashlib.sha256((Path(__file__).parent / "datos" / "apps" / "localization_vectors.json").read_bytes()
                          ).hexdigest() == "56a30ad5794408833f05d5c4dd7b0512d47430222b78edd244582ee420dade8e"


def test_umbrales_del_arbitro():
    t = VECTORS["thresholds"]
    assert L.MINIMUM_HORIZONTAL_SPREAD_METERS == t["minimumHorizontalSpreadMeters"]
    assert L.A_PRIORI_HORIZONTAL_SIGMA_METERS == t["aPrioriHorizontalSigmaMeters"]
    assert L.WEAK_SCALE_PPM == t["weakScalePpm"]
    assert L.UNUSUAL_SCALE_PPM == t["unusualScalePpm"]
    assert L.MINIMUM_VERTICAL_SPREAD_METERS == t["minimumVerticalSpreadMeters"]
    assert L.LARGE_SLOPE_PPM == t["largeSlopePpm"]
    assert L.DEFAULT_HORIZONTAL_TOLERANCE_METERS == t["defaultHorizontalToleranceMeters"]
    assert L.DEFAULT_VERTICAL_TOLERANCE_METERS == t["defaultVerticalToleranceMeters"]


def test_cada_caso_reproduce_al_arbitro():
    """En la cuadrícula del árbitro: 1e-6 m, 1e-11 en a y b, 1e-5 ppm y 1e-5″; los errores con su código."""
    solved = errors = 0
    for case in VECTORS["cases"]:
        if "baseGrid" in case:
            continue
        if "expectedError" in case:
            with pytest.raises(L.LocalizationError) as info:
                L.solve(_observations(case), _options(case))
            assert info.value.code == case["expectedError"], case["name"]
            errors += 1
            continue
        s = L.solve(_observations(case), _options(case))
        _assert_solution(s, case["expected"], case["name"], TOL["gridMeters"], TOL["ratio"], TOL["ppm"],
                         TOL["arcsec"])
        solved += 1
    assert (solved, errors) == (16, 4)


def test_el_sospechoso_se_senala_solo_si_pasa_de_la_tolerancia():
    def solved(name):
        case = next(c for c in VECTORS["cases"] if c["name"] == name)
        return L.solve(_observations(case), _options(case)), _options(case)

    clean, o = solved("similarity_6pts_plane")
    c4 = next(r.index for r in clean.residuals if r.name == "C4")
    assert clean.horizontal_suspect_index == c4 and clean.vertical_suspect_index == c4
    assert not any(clean.flags_horizontal_suspect(r.index, o.horizontal_tolerance_m) or
                   clean.flags_vertical_suspect(r.index, o.vertical_tolerance_m) for r in clean.residuals)
    bad, o = solved("blunder_detected")
    c4 = next(r.index for r in bad.residuals if r.name == "C4")
    assert bad.flags_horizontal_suspect(c4, o.horizontal_tolerance_m)
    assert bad.flags_vertical_suspect(c4, o.vertical_tolerance_m)
    assert abs(bad.residuals[c4].loo_dH - 0.071) < 0.0005 and abs(bad.residuals[c4].loo_dZ - 0.067) < 0.0005
    assert not any(bad.flags_horizontal_suspect(r.index, o.horizontal_tolerance_m) for r in bad.residuals
                   if r.index != c4)
    three, _ = solved("three_points_plane_exact")
    assert three.horizontal.pair_count < L.MINIMUM_PAIRS_TO_FLAG_SUSPECT
    assert not any(three.flags_horizontal_suspect(r.index, 0.0) for r in three.residuals)


def test_formas_cerradas_contra_minimos_cuadrados_genericos():
    """Como `check_against_generic_lsq.py` de las apps: las cuatro incógnitas explícitas dan lo mismo a 1e-9."""
    for case in VECTORS["cases"]:
        if "expected" not in case or case["options"]["horizontalMethod"] != "similarity" or "baseGrid" in case:
            continue
        obs = [o for o in _observations(case) if o.use_h]
        if len(obs) < 2:
            continue
        # Las cuatro incógnitas sin reducir al centroide, con un origen cualquiera (el primer par) para que la matriz
        # no pierda los decimales en coordenadas de millones de metros.
        e0, n0, E0, N0 = obs[0].e, obs[0].n, obs[0].E, obs[0].N
        A, y = [], []
        for o in obs:
            A += [[1, 0, o.e - e0, -(o.n - n0)], [0, 1, o.n - n0, o.e - e0]]
            y += [o.E - E0, o.N - N0]
        tE, tN, a, b = np.linalg.lstsq(np.array(A), np.array(y), rcond=None)[0]
        s = L.solve(_observations(case), _options(case))
        h = s.horizontal
        assert abs(h.a - a) < 1e-9 and abs(h.b - b) < 1e-9, case["name"]
        # La traslación de la forma clásica, con el mismo origen.
        assert abs(h.translation_e - (E0 + tE - a * e0 + b * n0)) < 1e-5
        assert abs(h.translation_n - (N0 + tN - b * e0 - a * n0)) < 1e-5


# --- Un caso de cuatro pares resuelto a mano ---------------------------------------------------------------------------

def test_cuatro_pares_calculados_a_mano():
    """Un cuadrado de 100 m; el control es lo medido corrido (1000, 2000) m, con 4 cm de error en el este de P1 y
    3 cm en la cota de P4. A mano (docs/localizacion.md §2.1 y §2.2 de las apps):
    ē = n̄ = 50, Ē = 1050.01, N̄ = 2050, D = 20000, P = 19998, Q = 2 → a = 0.9999, b = 0.0001;
    residuos dE = +0.02, −0.01, 0, −0.01 y dN = 0, −0.01, 0, +0.01; σ0 = √(0.0008/4) = 0.014142 m.
    Cota: d = 1, 1, 1, 1.03 → c0 = 1.0075, cN = +150 ppm, cE = −150 ppm; dZ = ∓0.0075; σ0 = 0.015 m (r = 1)."""
    grid = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    local = [(1000.04, 2000.0, 101.0), (1100.0, 2000.0, 101.0), (1100.0, 2100.0, 101.0), (1000.0, 2100.0, 101.03)]
    obs = [L.Observation(f"P{i + 1}", e, n, 100.0, E, N, Z) for i, ((e, n), (E, N, Z)) in enumerate(zip(grid, local))]
    s = L.solve(obs, L.Options(horizontal_tolerance_m=0.025))
    h, v = s.horizontal, s.vertical
    assert h.method == "similarity" and h.redundancy == 4
    assert (h.ec, h.nc) == (50.0, 50.0) and abs(h.Ec - 1050.01) < 1e-9 and h.Nc == 2050.0
    assert abs(h.a - 0.9999) < 1e-12 and abs(h.b - 0.0001) < 1e-12
    assert abs(h.scale - math.hypot(0.9999, 0.0001)) < 1e-12
    assert abs(h.azimuth_rotation_arcsec + math.atan2(0.0001, 0.9999) * L.ARCSEC_PER_RADIAN) < 1e-9
    for r, dE, dN in zip(s.residuals, (0.02, -0.01, 0.0, -0.01), (0.0, -0.01, 0.0, 0.01)):
        assert abs(r.dE - dE) < 1e-9 and abs(r.dN - dN) < 1e-9, r.name
        assert abs(r.dH - math.hypot(dE, dN)) < 1e-9
    assert abs(h.sigma0_m - math.sqrt(0.0008 / 4)) < 1e-12
    assert abs(h.scale_sigma_ppm - math.sqrt(0.0002) / math.sqrt(20000) * 1e6) < 1e-6
    assert v.method == "plane" and v.redundancy == 1
    assert abs(v.c0 - 1.0075) < 1e-12 and abs(v.cN - 0.00015) < 1e-12 and abs(v.cE + 0.00015) < 1e-12
    for r, dZ in zip(s.residuals, (-0.0075, 0.0075, -0.0075, 0.0075)):
        assert abs(r.dZ - dZ) < 1e-9, r.name
    assert abs(v.sigma0_m - 0.015) < 1e-12
    # Pendiente de 212 ppm: aviso de plano muy inclinado; todo dentro de tolerancia.
    assert [w.code for w in s.warnings] == ["slopeLarge"]
    assert abs(s.warnings[0].value_ppm - math.hypot(150, 150)) < 1e-6
    # Sin el par: con tres pares, la semejanza que queda predice el cuarto (comprobado con mínimos cuadrados).
    for i, r in enumerate(s.residuals):
        rest = [o for j, o in enumerate(obs) if j != i]
        A = np.array([row for o in rest for row in ([1, 0, o.e, -o.n], [0, 1, o.n, o.e])])
        y = np.array([x for o in rest for x in (o.E, o.N)])
        tE, tN, a, b = np.linalg.lstsq(A, y, rcond=None)[0]
        o = obs[i]
        assert abs(r.loo_dH - math.hypot(o.E - (tE + a * o.e - b * o.n), o.N - (tN + b * o.e + a * o.n))) < 1e-9
    # Sin el par, en cota: el plano exacto por los otros tres (sigue siendo «plano»). A mano: sin P1, d = 1.03 −
    # 0.0003·e por P2, P3 y P4 predice 1.03 en P1 → −0.03; y así los demás.
    for r, loo in zip(s.residuals, (-0.03, 0.03, -0.03, 0.03)):
        assert abs(r.loo_dZ - loo) < 1e-9, r.name
    with pytest.raises(L.LocalizationError) as info:
        L.solve([replace(o, E=None) if o.name == "P2" else o for o in obs])
    assert info.value.code == "horizontalPairWithoutLocalPlanar" and "“P2”" in str(info.value)


def test_un_par_y_bancos_de_nivel():
    one = L.solve([L.Observation("C1", 100.0, 200.0, 50.0, 1100.0, 2200.0, 51.0)])
    assert one.horizontal.method == "translation" and one.vertical.method == "constant"
    assert [w.code for w in one.warnings] == ["noHorizontalRedundancy", "noVerticalRedundancy"]
    assert L.horizontal_notes(one) == ["Con un solo par en planta solo hay traslación: no hay giro ni escala que "
                                       "sacar de un punto."]
    bench = L.solve([L.Observation("BN1", 0, 0, 10.0, None, None, 12.0, use_h=False)])
    assert bench.horizontal.method == "none" and bench.residuals[0].dH is None
    E, N = bench.transform.forward(5.0, 6.0)
    assert (E, N) == (5.0, 6.0) and bench.transform.elevation(10.0, 5, 6) == 12.0
    assert bench.transform.elevation(None, 5, 6) is None          # sin altura, sin cota (nunca cero)


# --- La cuadrícula base por el sistema del proyecto -------------------------------------------------------------------

def _site_pairs(case_name="similarity_6pts_plane") -> list[LocalizationPair]:
    case = next(c for c in VECTORS["cases"] if c["name"] == case_name)
    sites = {p["name"]: p for p in VECTORS["pipelineUTM13N"]}
    return [LocalizationPair(name=p["name"], control=LocalizationControl(p["local"]["n"], p["local"]["e"],
                                                                         p["local"]["z"]),
                             measured=LocalizationMeasured(p["name"], sites[p["name"]]["latitudeDegrees"],
                                                           sites[p["name"]]["longitudeDegrees"],
                                                           sites[p["name"]]["ellipsoidalHeightMeters"]),
                             uses_horizontal=p["useH"], uses_vertical=p["useV"]) for p in case["pairs"]]


def test_conversiones_de_la_tuberia_a_un_milimetro():
    utm = L.frame_for(Localization(base_grid_crs_code="EPSG:32613"), None)
    lon0 = L.central_meridian([p["longitudeDegrees"] for p in VECTORS["pipelineUTM13N"]])
    tm = L.TransverseMercator(lon0)
    worst = 0.0
    for p in VECTORS["pipelineUTM13N"]:
        e, n = utm.grid(p["latitudeDegrees"], p["longitudeDegrees"])
        le, ln, _ = tm.forward(p["latitudeDegrees"], p["longitudeDegrees"])
        worst = max(worst, abs(e - p["utm13n"]["e"]), abs(n - p["utm13n"]["n"]),
                    abs(le - p["localTransverseMercator"]["e"]), abs(ln - p["localTransverseMercator"]["n"]))
        lat, lon = tm.inverse(le, ln)
        assert abs(lat - p["latitudeDegrees"]) < 1e-11 and abs(lon - p["longitudeDegrees"]) < 1e-11
    assert worst < TOL["pipelineMeters"]
    assert worst < 1e-6            # la UTM de PROJ y la serie de Krüger del árbitro: el mismo resultado


def test_caso_realista_por_el_sistema_del_proyecto():
    """Los seis puntos en WGS 84 de un proyecto UTM 13N con el control del primer caso, como lo hacen las apps: 1 mm,
    0.01 ppm y 0.01″; la escala esperada a nivel del terreno, +483.61 ppm (la de su prueba)."""
    case = next(c for c in VECTORS["cases"] if c["name"] == "similarity_6pts_plane")
    record = L.draft(_site_pairs(), CoordinateChoice("EPSG:32613", "", None))
    assert (record.base_grid_kind, record.base_grid_crs_code) == ("projectCRS", "EPSG:32613")
    computed = L.compute(record, None)
    _assert_solution(computed.solution, case["expected"], "por UTM", TOL["pipelineMeters"], 0.01e-6, 0.01, 0.01)
    assert abs((computed.expected_ground_scale - 1) * 1e6 - 483.61) < 0.01
    # La escala calculada (+287 ppm) no es ni la de cuadrícula ni la de terreno a menos de 300 ppm de las dos.
    assert computed.scale_match is None and "scaleUnusual" not in [w.code for w in computed.warnings]


def test_proyecto_en_geograficas_con_transversa_local():
    case = next(c for c in VECTORS["cases"] if c["name"] == "geographic_project_local_tm")
    record = L.draft(_site_pairs(), CoordinateChoice("EPSG:4326", "", None))
    assert record.base_grid_kind == "localTransverseMercator"
    assert abs(record.base_grid_central_meridian_deg - case["baseGrid"]["centralMeridianDegrees"]) < 1e-12
    assert record.base_grid_ellipsoid == case["baseGrid"]["ellipsoid"]
    computed = L.compute(record, None)
    for o, p in zip(computed.observations, case["pairs"]):
        assert abs(o.e - p["grid"]["e"]) < TOL["pipelineMeters"] and abs(o.n - p["grid"]["n"]) < TOL["pipelineMeters"]
    _assert_solution(computed.solution, case["expected"], case["name"], TOL["pipelineMeters"], 0.01e-6, 0.01, 0.01,
                     probes=False)
    assert L.system_label(computed.frame.name) == "Local · sobre transversa local"


def test_meridiano_central_por_el_lado_corto():
    assert L.central_meridian([179.999, -179.999]) in (180.0, -180.0)
    assert L.central_meridian([-106.1, -106.0]) == pytest.approx(-106.05)
    assert L.central_meridian([]) is None


def test_cuadricula_guardada_y_la_desconocida_no_se_cambia_por_otra():
    record = L.applied(L.draft(_site_pairs(), CoordinateChoice("EPSG:32613", "", None)))
    other = L.compute(record, None)
    # Aunque el sistema elegido ahora sea otro, se calcula con la cuadrícula guardada.
    assert other.frame.crs_code == "EPSG:32613"
    unknown = replace(record, base_grid_crs_code="EPSG:99999")
    with pytest.raises(L.LocalizationError) as info:
        L.compute(unknown, None)
    assert info.value.code == "noBaseGrid"
    choice = CoordinateChoice("EPSG:32613", "", None, localization=unknown)
    p = _site_pairs()[0].measured
    position = Ecef(*crs.motor.geocentric(p.latitude_deg, p.longitude_deg, p.ellipsoidal_height_m,
                                          crs.definition("EPSG:32613").ellipsoid))
    with pytest.raises(UserError, match="no se puede calcular"):
        crs.transform(position, choice)


# --- Aplicarla: transform, σ y rótulos ---------------------------------------------------------------------------------

def _position(m: LocalizationMeasured, code="EPSG:32613") -> Ecef:
    return Ecef(*crs.motor.geocentric(m.latitude_deg, m.longitude_deg, m.ellipsoidal_height_m,
                                      crs.definition(code).ellipsoid))


def test_transform_da_las_locales_de_las_apps():
    pairs = _site_pairs()
    record = L.applied(L.draft(pairs, CoordinateChoice("EPSG:32613", "", None)))
    choice = CoordinateChoice("EPSG:32613", "", None, localization=record)
    computed = L.compute(record, None)
    for pair, r in zip(pairs, computed.solution.residuals):
        c = crs.transform(_position(pair.measured), choice)
        # Lo calculado en el par es el control menos su residuo.
        assert abs(c.easting_m - (pair.control.easting_m - r.dE)) < 1e-6
        assert abs(c.northing_m - (pair.control.northing_m - r.dN)) < 1e-6
        assert abs(c.orthometric_height_m - (pair.control.elevation_m - r.dZ)) < 1e-6
        assert c.localized and c.crs_code == "LOCAL" and c.base_crs_code == "EPSG:32613"
        assert c.label == "Local · sobre UTM zona 13N" and c.height_kind == "local"
        plain = crs.transform(_position(pair.measured), CoordinateChoice("EPSG:32613", "", None))
        assert abs(c.grid_easting_m - plain.easting_m) < 1e-9 and c.convergence_deg is None
    # Con el nombre de un sistema de «Mis sistemas» de las apps.
    named = replace(record, saved_system_name="Obra Norte")
    c = crs.transform(_position(pairs[0].measured), replace(choice, localization=named))
    assert c.label == "Obra Norte · local sobre UTM zona 13N"
    # σ: la de la cuadrícula girada y escalada.
    from tresvizo_ppk.model import Enu
    s_grid = crs.transform_sigma(_position(pairs[0].measured), Enu(0.01, 0.02, 0.03),
                                 CoordinateChoice("EPSG:32613", "", None))
    s_local = crs.transform_sigma(_position(pairs[0].measured), Enu(0.01, 0.02, 0.03), choice)
    assert abs(math.hypot(s_local.e, s_local.n) - computed.solution.horizontal.scale *
               math.hypot(s_grid.e, s_grid.n)) < 1e-9 and s_local.u == 0.03


def test_la_cota_va_sobre_el_geoide_de_ahora():
    """Manda el geoide del sistema elegido (§9.3): la planta no cambia, la cota sí, y el plano absorbe la N."""
    pairs = _site_pairs()
    record = L.applied(L.draft(pairs, CoordinateChoice("EPSG:32613", "", None)))
    ellipsoidal = L.compute(record, None)
    over_geoid = L.compute(record, "ggm10")
    assert over_geoid.solution.horizontal.a == ellipsoidal.solution.horizontal.a
    assert abs(over_geoid.solution.vertical.c0 - ellipsoidal.solution.vertical.c0) > 1.0
    grid = crs.geoid("ggm10")
    p = pairs[0].measured
    assert abs(over_geoid.observations[0].v - (p.ellipsoidal_height_m - grid.undulation(p.latitude_deg,
                                                                                      p.longitude_deg))) < 1e-9
    choice = CoordinateChoice("EPSG:32613", "", "ggm10", localization=record)
    c = crs.transform(_position(p), choice)
    r = over_geoid.solution.residuals[0]
    assert abs(c.orthometric_height_m - (pairs[0].control.elevation_m - r.dZ)) < 1e-6
    assert crs.height_sigma(0.02, c) == 0.02


def test_avisos_de_escala_contra_el_terreno():
    """Control en la cuadrícula (escala 1) con escala esperada de +484 ppm: «rígida» avisa que la escala fija se
    aparta del terreno; la semejanza no avisa porque la escala coincide con la de cuadrícula."""
    pairs = _site_pairs()
    utm = L.frame_for(Localization(base_grid_crs_code="EPSG:32613"), None)
    on_grid = []
    for p in pairs:
        e, n = utm.grid(p.measured.latitude_deg, p.measured.longitude_deg)
        on_grid.append(replace(p, control=LocalizationControl(n + 0.001, e - 0.002, p.control.elevation_m)))
    rigid = L.compute(L.draft(on_grid, CoordinateChoice("EPSG:32613", "", None), horizontal_method="rigid"), None)
    assert [w.code for w in rigid.warnings][:1] == ["scaleFixedAwayFromGround"]
    text = L.warning_message(rigid.warnings[0], rigid.options)
    assert text.startswith("Escala fija en 1, pero aquí la de cuadrícula a terreno es +483.6")
    sim = L.compute(L.draft(on_grid, CoordinateChoice("EPSG:32613", "", None)), None)
    assert sim.scale_match == "grid" and L.scale_match_text("grid") == "La escala coincide con coordenadas de cuadrícula."
    assert not any(w.code.startswith("scale") for w in sim.warnings)


def test_textos_y_declaracion_de_la_exportacion():
    assert L.rotation_text(4946.37) == "+1° 22' 26.37\""
    assert L.rotation_text(-4946.37) == "−1° 22' 26.37\""
    assert L.ascii_rotation(-4946.37) == "-1d 22' 26.37\""
    assert L.signed_ppm_text(-290.97) == "−290.97 ppm" and L.scale_text(1.000290965) == "1.000290965"
    assert L.signed_length(-0.0231) == "−0.023 m" and L.signed_length(0.0001) == "0.000 m"
    record = L.applied(L.draft(_site_pairs(), CoordinateChoice("EPSG:32613", "", None)),
                       at=datetime(2026, 10, 4, 17, 31, 5, 250000, tzinfo=timezone.utc))
    lines = L.declaration_lines(CoordinateChoice("EPSG:32613", "", None, localization=record))
    assert lines[0] == (f"LOCALIZACION: coordenadas locales del proyecto, no de EPSG:32613. Aplicada "
                        f"2026-10-04T17:31:05Z, id {record.id}.")
    assert lines[1] == "localizacion_cuadricula_base: cuadricula WGS 84 / UTM zona 13N (EPSG:32613)"
    assert lines[2] == "localizacion_altura_base: elipsoidal"
    assert lines[3].startswith("localizacion_planta: semejanza, pedido semejanza; escala 1.000")
    assert lines[4].startswith("localizacion_cota: plano inclinado; ")
    assert lines[5].startswith("localizacion_pares: 6 (6 en planta, 6 en cota); RMS planta 0.00")
    assert all(line.isascii() for line in lines)


# --- El registro del contrato ------------------------------------------------------------------------------------------

EXAMPLE = {
    "id": "0B6E3C1E-6A0F-4F7E-9A51-2A5F1D3C9E10", "projectID": "8C2E0F4B-1D7A-4E7C-9C1B-5E7B0A4D2F11",
    "pairs": [{"id": "11111111-2222-3333-4444-555555555555", "name": "C1",
               "control": {"northingMeters": 10000.0, "eastingMeters": 5000.0, "elevationMeters": 1396.777},
               "measured": {"pointID": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", "name": "P12",
                            "latitudeDegrees": 28.635271, "longitudeDegrees": -106.088912,
                            "ellipsoidalHeightMeters": 1418.214},
               "usesHorizontal": True, "usesVertical": True},
              {"id": "22222222-2222-3333-4444-555555555555", "name": "BN1",
               "control": {"northingMeters": None, "eastingMeters": None, "elevationMeters": 1390.5},
               "measured": {"pointID": None, "name": "BN1", "latitudeDegrees": 28.6347, "longitudeDegrees": -106.0835,
                            "ellipsoidalHeightMeters": None},
               "usesHorizontal": False, "usesVertical": True}],
    "horizontalMethod": "similarity", "verticalMethod": "automatic",
    "horizontalToleranceMeters": 0.02, "verticalToleranceMeters": 0.04,
    "baseGrid": {"kind": "projectCRS", "crsCode": "EPSG:32613"}, "heightBase": {"kind": "ellipsoidal"},
    "appliedAt": "2026-09-21T14:13:20Z", "removedAt": None,
}


def test_registro_con_las_claves_del_contrato_ida_y_vuelta():
    record = L.record_from_json(EXAMPLE)
    assert record.pairs[1].control.northing_m is None and record.pairs[1].measured.ellipsoidal_height_m is None
    assert record.applied_at == datetime(2026, 9, 21, 14, 13, 20, tzinfo=timezone.utc)
    out = L.record_to_json(record)
    assert out == EXAMPLE                       # mismas claves, nulos escritos, ISO 8601 al segundo
    assert set(out) == {"id", "projectID", "pairs", "horizontalMethod", "verticalMethod", "horizontalToleranceMeters",
                        "verticalToleranceMeters", "baseGrid", "heightBase", "appliedAt", "removedAt"}
    tm = dict(EXAMPLE, baseGrid={"kind": "localTransverseMercator", "centralMeridianDegrees": -106.085,
                                 "ellipsoid": "WGS 84"}, heightBase={"kind": "orthometric", "geoidModelID": "ggm10"},
              savedSystemID="6F1C2B1E-58B4-4A57-9D52-0A8F4B2E7C11", savedSystemName="Obra Chihuahua")
    assert L.record_to_json(L.record_from_json(tm)) == tm


def test_lee_lo_que_escribe_android():
    """UUID en minúsculas, milisegundos y claves nulas omitidas (`testReadsWhatAnotherWriterMightProduce`)."""
    text = """{"id":"0b6e3c1e-6a0f-4f7e-9a51-2a5f1d3c9e10","projectID":"8c2e0f4b-1d7a-4e7c-9c1b-5e7b0a4d2f11",
     "pairs":[{"id":"11111111-2222-3333-4444-555555555555","name":"C1",
               "control":{"northingMeters":10000.0,"eastingMeters":5000.0,"elevationMeters":1396.777},
               "measured":{"pointID":null,"name":"P12","latitudeDegrees":28.635271,"longitudeDegrees":-106.088912},
               "usesHorizontal":true,"usesVertical":false}],
     "horizontalMethod":"rigid","verticalMethod":"none","horizontalToleranceMeters":0.02,"verticalToleranceMeters":0.04,
     "baseGrid":{"kind":"projectCRS","crsCode":"EPSG:32613"},"heightBase":{"kind":"orthometric","geoidModelID":"ggm10"},
     "appliedAt":"2026-10-04T17:31:05.250Z"}"""
    record = L.record_from_json(json.loads(text))
    assert record.id == "0B6E3C1E-6A0F-4F7E-9A51-2A5F1D3C9E10" and record.removed_at is None
    assert record.horizontal_method == "rigid" and record.vertical_method == "none"
    assert record.height_base_geoid_id == "ggm10" and record.pairs[0].measured.ellipsoidal_height_m is None
    assert abs(record.applied_at.timestamp() - 1_791_135_065.25) < 1e-3
    with pytest.raises(ValueError):
        L.record_from_json(dict(EXAMPLE, baseGrid={"kind": "lambertLocal", "crsCode": "X"}))


# --- Importar de un proyecto de TresVizo Field --------------------------------------------------------------------------

def _mvz(payload: dict) -> bytes:
    """Un `.mvz` como lo escribe `ProjectArchive` de las apps: el contenido como cadena con claves ordenadas, su
    SHA-256 y la envoltura con fechas ISO 8601."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    container = {"appVersion": "1.0 (812)", "checksum": hashlib.sha256(text.encode()).hexdigest(), "format": "mvz",
                 "payload": text, "version": 2, "writtenAt": "2026-10-27T15:00:00Z"}
    return json.dumps(container, sort_keys=True, indent=2).encode()


def _payload(localizations) -> dict:
    project = {"id": "8C2E0F4B-1D7A-4E7C-9C1B-5E7B0A4D2F11", "name": "Obra Chihuahua", "crsCode": "EPSG:32613",
               "createdAt": 811692800.0, "updatedAt": 811692800.0, "deviceID": "prueba", "defaults": {}}
    points = [{"id": f"AAAAAAAA-0000-0000-0000-00000000000{i}", "name": p.name,
               "latitudeDegrees": p.measured.latitude_deg, "longitudeDegrees": p.measured.longitude_deg,
               "ellipsoidalHeightMeters": p.measured.ellipsoidal_height_m} for i, p in enumerate(_site_pairs())]
    return {"project": project, "points": points, "deletedPoints": [], "baseShifts": [], "lines": [],
            "stakeoutRecords": [], "localizations": localizations}


def _app_record(removed=None) -> dict:
    """La localización del primer caso como la guardan las apps."""
    pairs = []
    for i, p in enumerate(_site_pairs()):
        pairs.append({"id": f"11111111-2222-3333-4444-55555555555{i}", "name": p.name,
                      "control": {"northingMeters": p.control.northing_m, "eastingMeters": p.control.easting_m,
                                  "elevationMeters": p.control.elevation_m},
                      "measured": {"pointID": f"AAAAAAAA-0000-0000-0000-00000000000{i}", "name": p.name,
                                   "latitudeDegrees": p.measured.latitude_deg,
                                   "longitudeDegrees": p.measured.longitude_deg,
                                   "ellipsoidalHeightMeters": p.measured.ellipsoidal_height_m},
                      "usesHorizontal": True, "usesVertical": True})
    return dict(EXAMPLE, pairs=pairs, removedAt=removed,
                id="0B6E3C1E-6A0F-4F7E-9A51-2A5F1D3C9E1" + ("1" if removed else "0"))


def test_importa_la_localizacion_de_un_mvz_y_la_aplica_igual(tmp_path):
    old = _app_record(removed="2026-09-22T10:00:00Z")
    path = tmp_path / "Obra.mvz"
    path.write_bytes(_mvz(_payload([old, _app_record()])))
    (project,) = L.read_field_project(path)
    assert project.name == "Obra Chihuahua" and project.crs_code == "EPSG:32613" and len(project.points) == 6
    assert len(project.localizations) == 2 and project.in_use.id.endswith("E10")
    record = L.import_localization(project)
    choice = L.choice_for(project, record)
    assert (choice.crs_id, choice.geoid_id) == ("EPSG:32613", None)
    case = next(c for c in VECTORS["cases"] if c["name"] == "similarity_6pts_plane")
    _assert_solution(L.compute(record, choice.geoid_id).solution, case["expected"], "importada", TOL["pipelineMeters"],
                     0.01e-6, 0.01, 0.01)
    c = crs.transform(_position(_site_pairs()[0].measured), choice)
    assert c.localized and c.label == "Local · sobre UTM zona 13N"


def test_mvz_danado_o_de_otra_version_no_se_importa(tmp_path):
    container = json.loads(_mvz(_payload([_app_record()])))
    container["payload"] = container["payload"].replace("Obra Chihuahua", "Obra Chihuahue")   # sin rehacer la suma
    bad = tmp_path / "danado.mvz"
    bad.write_text(json.dumps(container))
    with pytest.raises(UserError, match="suma de verificación"):
        L.read_field_project(bad)
    newer = json.loads(_mvz(_payload([])))
    newer["version"] = 3
    (tmp_path / "nuevo.mvz").write_text(json.dumps(newer))
    with pytest.raises(UserError, match="más nueva"):
        L.read_field_project(tmp_path / "nuevo.mvz")
    (tmp_path / "otro.mvz").write_text("{}")
    with pytest.raises(UserError, match="no es un proyecto"):
        L.read_field_project(tmp_path / "otro.mvz")
    two = _payload([_app_record(), dict(_app_record(), id="0B6E3C1E-6A0F-4F7E-9A51-2A5F1D3C9E12")])
    (tmp_path / "dos.mvz").write_bytes(_mvz(two))
    with pytest.raises(UserError, match="más de una localización en uso"):
        L.read_field_project(tmp_path / "dos.mvz")


def test_importa_de_un_respaldo_zip(tmp_path):
    path = tmp_path / "TresVizo-respaldo-2026-10-27-0900.zip"
    pid = "8C2E0F4B-1D7A-4E7C-9C1B-5E7B0A4D2F11"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({
            "format": "tresvizo-field-backup", "formatVersion": 1, "createdAt": "2026-10-27T15:00:00Z",
            "app": {"platform": "android", "version": "1.0 (90)"},
            "sections": [{"id": "preferences", "version": 1, "file": "preferences.json"},
                         {"id": "projects", "version": 1, "file": "projects.json"}]}))
        z.writestr("preferences.json", json.dumps({"values": {}}))
        z.writestr(f"projects/{pid}.mvz", _mvz(_payload([_app_record()])))
        z.writestr("projects.json", json.dumps({"projects": [{"id": pid, "name": "Obra Chihuahua",
                                                              "file": f"projects/{pid}.mvz", "pointCount": 6}]}))
    (project,) = L.read_field_project(path)
    assert project.in_use is not None and project.source.endswith(f"projects/{pid}.mvz")
    no_projects = tmp_path / "sin.zip"
    with zipfile.ZipFile(no_projects, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": "tresvizo-field-backup", "formatVersion": 1,
                                                "sections": []}))
    with pytest.raises(UserError, match="Incluir proyectos"):
        L.read_field_project(no_projects)


# --- Pares desde un CSV de control ------------------------------------------------------------------------------------

def test_control_de_un_csv_pnezd_y_nxyzd(tmp_path):
    pairs = _site_pairs()
    pnezd = tmp_path / "control.csv"
    pnezd.write_text("\n".join(f"{p.name},{p.control.northing_m:.4f},{p.control.easting_m:.4f},"
                               f"{p.control.elevation_m:.4f},vértice" for p in pairs) + "\nBN9,,,1390.5,banco\n",
                     encoding="utf-8")
    control, notes = L.read_control_csv(pnezd, "pnezd")
    assert len(control) == 7 and control[-1].northing_m is None and control[-1].elevation_m == 1390.5 and not notes
    nxyzd = tmp_path / "control2.csv"
    nxyzd.write_text("Nombre;Este;Norte;Cota;Descripción\n" + "\n".join(
        f"{p.name};{p.control.easting_m:.4f};{p.control.northing_m:.4f};{p.control.elevation_m:.4f};x".replace(".", ",")
        for p in pairs), encoding="utf-8-sig")
    control2, _ = L.read_control_csv(nxyzd, "pnezd")      # el encabezado manda sobre el orden elegido
    assert [(c.northing_m, c.easting_m) for c in control2] == [(c.northing_m, c.easting_m) for c in control[:6]]
    measured = [replace(p.measured, name=p.name.lower()) for p in pairs]
    made, missing = L.pairs_from(control, measured)
    assert len(made) == 6 and missing == ["BN9"] and all(p.uses_horizontal and p.uses_vertical for p in made)
    repeated = tmp_path / "rep.csv"
    repeated.write_text("C1,1,2,3\nC1,1,2,3\n")
    with pytest.raises(UserError, match="repetido"):
        L.read_control_csv(repeated)


# --- Exportar en locales ----------------------------------------------------------------------------------------------

def test_exporta_en_locales_como_las_apps(tmp_path):
    import sinteticos as s
    from tresvizo_ppk.export import api as export
    result = s.points_result(tmp_path)
    processed = [p for p in result.points if p.position is not None and p.solution.value != "none"]
    plain = CoordinateChoice("EPSG:6368", "", "ggm10")
    measured = [L.measured_from_coordinate(p.point.name, crs.transform(p.position, plain), "PPK") for p in processed]
    grid = L.frame_for(Localization(base_grid_crs_code="EPSG:6368"), "ggm10")
    control = []
    for m in measured[:5]:          # un sistema local girado 1° y corrido, con la cota 100 m arriba
        e, n = grid.grid(m.latitude_deg, m.longitude_deg)
        th = math.radians(1.0)
        de, dn = e - 780000, n - 2420000
        control.append(L.ControlPoint(m.name, 5000 + math.sin(th) * de + math.cos(th) * dn,
                                      1000 + math.cos(th) * de - math.sin(th) * dn,
                                      grid.base_height(m.ellipsoidal_height_m, m.latitude_deg, m.longitude_deg) + 100))
    pairs, _ = L.pairs_from(control, measured)
    record = L.applied(L.draft(pairs, plain))
    choice = replace(plain, localization=record)
    out = export.export_points(result, choice, "field_csv", tmp_path / "local")
    rows = list(csv.DictReader(io.StringIO(out.read_bytes().decode("utf-8-sig"), newline="")))
    by_name = {r["Name"]: r for r in rows}
    for p in processed:
        c = crs.transform(p.position, choice)
        row = by_name[p.point.name]
        assert row["Easting"] == f"{c.easting_m:.3f}" and row["Northing"] == f"{c.northing_m:.3f}"
        assert row["Elevation"] == f"{c.orthometric_height_m:.3f}"
        assert row["CS name"] == "Local · sobre Mexico ITRF2008 / UTM zona 13N" and row["CS code"] == "LOCAL"
        assert row["Latitude"] == f"{c.receiver_latitude_deg:.8f}"           # WGS 84 (marco de la base) siempre
        assert row["Geoid"] == "GGM10"
    first = next(r for r in rows if r["Name"] == control[0].name)
    assert abs(float(first["Northing"]) - control[0].northing_m) < 0.01
    geo = json.loads(export.export_points(result, choice, "geojson", tmp_path / "local").read_text("utf-8"))
    assert geo["metadata"]["sistema"].startswith("LOCAL: Local · sobre Mexico ITRF2008 / UTM zona 13N (cuadricula "
                                                 "base EPSG:6368")
    assert geo["metadata"]["localizacion"][0].startswith("LOCALIZACION: coordenadas locales del proyecto")
    dxf = export.export_points(result, choice, "dxf", tmp_path / "local")
    text = dxf.read_bytes().decode("cp1252")
    assert "\r\nLOCALIZACION\r\n" in text and "localizacion_planta: semejanza" in text
    assert not dxf.with_suffix(".prj").exists()       # unas locales no tienen WKT: no se inventa un .prj


# --- Se guarda en el proyecto -----------------------------------------------------------------------------------------

def test_se_guarda_en_el_proyecto(tmp_path):
    from tresvizo_ppk import project as projects
    from tresvizo_ppk.model import Project
    record = L.applied(L.draft(_site_pairs(), CoordinateChoice("EPSG:32613", "", "ggm10")),
                       at=datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc))
    old = L.removed(L.applied(L.draft(_site_pairs()[:3], CoordinateChoice("EPSG:32613", "", None))),
                    at=datetime(2026, 10, 27, 13, 0, 0, tzinfo=timezone.utc))
    project = Project(kind="points", coordinates=CoordinateChoice("EPSG:32613", "", "ggm10", "UTM 13N", record),
                      localization_history=[old])
    path = projects.save(project, tmp_path / "obra.tvppk")
    data = json.loads(path.read_text("utf-8"))
    stored = data["coordinates"]["localization"]
    assert stored["baseGrid"] == {"kind": "projectCRS", "crsCode": "EPSG:32613"}
    assert stored["heightBase"] == {"kind": "orthometric", "geoidModelID": "ggm10"}
    assert stored["origin"] == "calculada en la oficina" and stored["appliedAt"] == "2026-10-27T12:00:00Z"
    again = projects.load(path)
    assert again.coordinates.localization == record
    assert again.localization_history == [old] and again.localization_history[0].removed_at is not None
    assert projects.load(projects.save(Project(kind="static"), tmp_path / "sin.tvppk")).coordinates.localization is None
