"""Control de calidad del proyecto (calidad.py): cada tolerancia justo en su borde, dos ocupaciones del mismo punto
con su media ponderada resuelta a mano, el control con residuos calculados a mano, el estático y la PDOP."""
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from tresvizo_ppk import calidad
from tresvizo_ppk.core import dop
from tresvizo_ppk.core.geodesy import ecef_delta_to_enu, enu_to_ecef_delta, geodetic_to_ecef
from tresvizo_ppk.crs import api as crs
from tresvizo_ppk.model import (ControlCoordinate, CoordinateChoice, Ecef, Enu, FieldPoint, ObservationSet,
                                PointsResult, ProcessedPoint, ProjectTolerances, Solution, Station)
from tests import sinteticos

T0 = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
BASE = Station(id="PRB1", name="Base de prueba", position=Ecef(*geodetic_to_ecef(21.88, -102.29, 1880.0)))
GROUND = np.array(geodetic_to_ecef(21.8705, -102.2901, 1885.0))
CHOICE = CoordinateChoice("mexico-itrf2008-utm", "", None)


def processed(name="P1", *, offset=(0.0, 0.0, 0.0), sigma=(0.004, 0.005, 0.010), fixed=60, total=60, ratio=9.0,
              pdop=1.8, start_s=0, duration_s=60.0, solution=Solution.FIXED, field=None):
    xyz = GROUND + np.array(enu_to_ecef_delta(tuple(GROUND), offset))
    fp = FieldPoint(name=name, code="", start=T0 + timedelta(seconds=start_s),
                    end=T0 + timedelta(seconds=start_s + duration_s), antenna_height_m=1.8, field_position=field)
    return ProcessedPoint(point=fp, solution=solution, position=Ecef(*(float(v) for v in xyz)),
                          sigma_enu=Enu(*sigma), epochs_fixed=fixed, epochs_total=total, distance_to_base_m=1100.0,
                          delta_from_field_enu=None, ratio=ratio, pdop_max=pdop, height_used_m=1.916)


def result(points):
    return PointsResult(base=BASE, rover=ObservationSet(obs=[]), rover_quality=None, points=points)


def tol(**values):
    return replace(ProjectTolerances(order="", max_sigma_h_m=0.020, max_sigma_v_m=0.030, min_fixed_percent=90.0,
                                     min_ratio=3.0, max_pdop=4.0, min_duration_s=30.0, max_repeat_h_m=0.020,
                                     max_repeat_v_m=0.040, max_control_h_m=0.020, max_control_v_m=0.040,
                                     fail_factor=2.0), **values)


def level_of(point, tolerances=None, criterion=None):
    report = calidad.evaluate_points(result([point]), tolerances or tol())
    status = report.points[0].status
    if criterion is None:
        return status.level, status.reason
    c = next(c for c in status.checks if c.criterion == criterion)
    return c.level, c.text


# --- Tolerancias del orden -------------------------------------------------------------------------------------------

def test_tolerances_from_accuracy_order():
    t = calidad.tolerances_for_order("2 centímetros", "points")
    # σH = 0.020 / (1.2238·√2) = 0.01156 → 0.011 (truncada al mm); σV = 2·0.020 / 1.96 = 0.0204 → 0.020
    assert (t.max_sigma_h_m, t.max_sigma_v_m) == (0.011, 0.020)
    assert (t.max_repeat_h_m, t.max_repeat_v_m, t.max_control_h_m, t.max_control_v_m) == (0.02, 0.04, 0.02, 0.04)
    assert t.min_duration_s == 30.0 and calidad.tolerances_for_order("2 centímetros", "static").min_duration_s == 3600
    # con σE = σN = σH/√2 en el borde, el CEP95 de la NT EEP (art. 6) no pasa del orden
    s = t.max_sigma_h_m / math.sqrt(2)
    assert 1.2238 * 2 * s <= 0.020
    assert calidad.default_tolerances("points") == calidad.tolerances_for_order("5 centímetros", "points")
    assert calidad.matches_order(t, "points") and not calidad.matches_order(replace(t, max_pdop=5.0), "points")
    with pytest.raises(ValueError):
        calidad.tolerances_for_order("3 centímetros")
    assert calidad.tolerance_issues(replace(t, max_pdop=0.5)) and not calidad.tolerance_issues(t)


# --- Cada criterio en su borde ---------------------------------------------------------------------------------------

def test_sigma_h_edge():
    # σH = √(σE² + σN²): 0.012 y 0.016 dan 0.020 exacto
    assert level_of(processed(sigma=(0.012, 0.016, 0.01)), criterion="sigma_h") == ("pass", "σH 0.020 ≤ 0.020")
    assert level_of(processed(sigma=(0.012, 0.0168, 0.01)), criterion="sigma_h") == ("review", "σH 0.021 > 0.020")
    assert level_of(processed(sigma=(0.024, 0.032, 0.01)), criterion="sigma_h")[0] == "review"     # 0.040 = 2×
    assert level_of(processed(sigma=(0.024, 0.0328, 0.01)), criterion="sigma_h")[0] == "fail"      # 0.041


def test_sigma_v_edge_compares_the_printed_figure():
    assert level_of(processed(sigma=(0.004, 0.005, 0.0304)), criterion="sigma_v") == ("pass", "σV 0.030 ≤ 0.030")
    assert level_of(processed(sigma=(0.004, 0.005, 0.0305)), criterion="sigma_v") == ("review", "σV 0.031 > 0.030")
    assert level_of(processed(sigma=(0.004, 0.005, 0.061)), criterion="sigma_v") == ("fail", "σV 0.061 > 0.030")


def test_fixed_percent_edge():
    assert level_of(processed(fixed=54, total=60), criterion="fixed") == ("pass", "fijas 90.0 % ≥ 90.0 %")
    assert level_of(processed(fixed=53, total=60), criterion="fixed") == ("review", "fijas 88.3 % < 90.0 %")
    assert level_of(processed(fixed=27, total=60), criterion="fixed")[0] == "review"     # 45 % = 90 / 2
    assert level_of(processed(fixed=26, total=60), criterion="fixed")[0] == "fail"


def test_ratio_edge_and_float_has_no_ratio():
    assert level_of(processed(ratio=3.0), criterion="ratio") == ("pass", "cociente 3.0 ≥ 3.0")
    assert level_of(processed(ratio=2.9), criterion="ratio") == ("review", "cociente 2.9 < 3.0")
    assert level_of(processed(ratio=1.4), criterion="ratio")[0] == "fail"
    report = calidad.evaluate_points(result([processed(solution=Solution.FLOAT, fixed=0)]), tol())
    assert not any(c.criterion == "ratio" for c in report.points[0].status.checks)
    assert report.points[0].status.level == "fail" and "fijas 0.0 % < 90.0 %" in report.points[0].status.reason


def test_pdop_edge_and_missing():
    assert level_of(processed(pdop=4.0), criterion="pdop") == ("pass", "PDOP 4.0 ≤ 4.0")
    assert level_of(processed(pdop=4.1), criterion="pdop") == ("review", "PDOP 4.1 > 4.0")
    assert level_of(processed(pdop=8.1), criterion="pdop")[0] == "fail"
    # sin dato no cuenta para el estado, pero se dice
    lv, text = level_of(processed(pdop=None), criterion="pdop")
    assert lv == "na" and text == "PDOP: sin dato"
    assert level_of(processed(pdop=None))[0] == "pass"


def test_duration_edge_and_disabled_criterion():
    assert level_of(processed(duration_s=30), criterion="duration") == ("pass", "duración 30 s ≥ 30 s")
    assert level_of(processed(duration_s=29), criterion="duration") == ("review", "duración 29 s < 30 s")
    assert level_of(processed(duration_s=14), criterion="duration")[0] == "fail"
    t = tol(min_duration_s=None)
    assert not any(c.criterion == "duration" for c in
                   calidad.evaluate_points(result([processed(duration_s=1)]), t).points[0].status.checks)


def test_reason_is_one_line_with_what_fails():
    lv, reason = level_of(processed(sigma=(0.004, 0.005, 0.034), pdop=4.5))
    assert lv == "review" and reason == "σV 0.034 > 0.030; PDOP 4.5 > 4.0"
    assert calidad.status_text(calidad.evaluate_points(result([processed()]), tol()).points[0].status) == "✓ Pasa"


def test_point_without_solution_fails():
    p = replace(processed(), solution=Solution.NONE, position=None, sigma_enu=None, message="fuera de la grabación")
    lv, reason = level_of(p)
    assert lv == "fail" and reason == "sin posición (fuera de la grabación)"


# --- Ocupaciones repetidas -------------------------------------------------------------------------------------------

def test_two_occupations_weighted_mean_by_hand():
    # A: 0 en E, σE 0.004; B: +0.012 en E, σE 0.008 → pesos 1/16 y 1/64: media = 0.012·(1/64)/(5/64) = 0.0024
    a = processed("P7", offset=(0.0, 0.0, 0.0), sigma=(0.004, 0.004, 0.010), fixed=60, total=60, start_s=0)
    b = processed("p7 ", offset=(0.012, 0.0, 0.016), sigma=(0.008, 0.004, 0.010), fixed=50, total=60,
                  start_s=600, ratio=5.5, pdop=2.4)
    t = tol()
    report = calidad.evaluate_points(result([a, b]), t)
    assert len(report.points) == 1 and len(report.groups) == 1
    pq, g = report.points[0], report.groups[0]
    assert pq.averaged and pq.occupations == 2 and pq.note == "promedio de 2 ocupaciones"
    assert g.used == [0, 1] and g.choice == 0
    d = ecef_delta_to_enu(tuple(GROUND), np.array([pq.point.position.x, pq.point.position.y,
                                                    pq.point.position.z]) - GROUND)
    assert abs(d[0] - 0.0024) < 1e-6 and abs(d[1]) < 1e-6 and abs(d[2] - 0.008) < 1e-6      # U con pesos iguales
    # cada ocupación menos la media
    assert abs(g.deltas_enu[0].e + 0.0024) < 1e-6 and abs(g.deltas_enu[1].e - 0.0096) < 1e-6
    # σ de la media: 1/√(1/16e-6 + 1/64e-6) = 3.58 mm, pero no baja de la mínima de una fija (8 mm + 1 ppm)
    floor = 0.008 + 1e-6 * 1100.0
    assert abs(pq.point.sigma_enu.e - floor) < 1e-9 and abs(pq.point.sigma_enu.u - (0.015 + 2e-6 * 1100.0)) < 1e-9
    assert pq.point.epochs_fixed == 110 and pq.point.epochs_total == 120
    assert pq.point.ratio == 5.5 and pq.point.pdop_max == 2.4 and "promedio" in pq.point.message
    # la diferencia entre las dos: 0.012 en planta y 0.016 en cota, dentro de 0.020 / 0.040
    assert abs(g.spread_h_m - 0.012) < 1e-6 and abs(g.spread_v_m - 0.016) < 1e-6
    # σH de la media √2 · 9.1 mm = 0.013, fijas 110/120 = 91.7 %, cociente el menor (5.5): todo pasa
    assert g.status.level == "pass" and pq.status.level == "pass" and pq.status.reason == ""
    assert "σH 0.013 ≤ 0.020" in [c.text for c in pq.status.checks]
    assert "entre ocupaciones ΔH 0.012 ≤ 0.020" in [c.text for c in pq.status.checks]


def test_repeat_tolerance_edge():
    a = processed("R", offset=(0.0, 0.0, 0.0))
    b = processed("R", offset=(0.020, 0.0, 0.040), start_s=300)
    g = calidad.evaluate_points(result([a, b]), tol()).groups[0]
    assert [c.level for c in g.status.checks] == ["pass", "pass"]
    b = processed("R", offset=(0.0212, 0.0, 0.0806), start_s=300)
    g = calidad.evaluate_points(result([a, b]), tol()).groups[0]
    assert [c.text for c in g.status.checks] == ["entre ocupaciones ΔH 0.021 > 0.020",
                                                 "entre ocupaciones ΔV 0.081 > 0.040"]
    assert g.status.level == "fail"


def test_choose_one_occupation():
    a = processed("Q", offset=(0.0, 0.0, 0.0))
    b = processed("Q", offset=(0.010, 0.0, 0.0), start_s=300)
    report = calidad.evaluate_points(result([a, b]), tol(), choices={"q": 2})
    pq = report.points[0]
    assert not pq.averaged and pq.note == "ocupación 2 de 2 (elegida)" and pq.point.position == b.position
    assert report.groups[0].choice == 2 and calidad.effective_points(replace(result([a, b]), quality=report)) == \
        [pq.point]


def test_only_fixed_occupations_enter_the_mean():
    a = processed("F", offset=(0.0, 0.0, 0.0))
    b = processed("F", offset=(0.3, 0.0, 0.0), start_s=300, solution=Solution.FLOAT, sigma=(0.05, 0.05, 0.1))
    c = processed("F", offset=(0.006, 0.0, 0.0), start_s=600)
    report = calidad.evaluate_points(result([a, b, c]), tol())
    g = report.groups[0]
    assert g.used == [0, 2] and g.deltas_enu[1] is None and abs(g.spread_h_m - 0.006) < 1e-6
    assert "sin 1 flotante" in report.points[0].point.message


def test_single_usable_occupation_is_review():
    a = processed("S")
    b = replace(processed("S", start_s=300), solution=Solution.NONE, position=None, sigma_enu=None)
    report = calidad.evaluate_points(result([a, b]), tol())
    g, pq = report.groups[0], report.points[0]
    assert g.status.level == "review" and "no hay con qué comparar" in g.status.reason
    assert pq.note == "ocupación 1 de 2 (la única con solución)" and pq.point.position == a.position


# --- Control ---------------------------------------------------------------------------------------------------------

def test_control_residuals_by_hand():
    p = processed("C1")
    c = crs.transform(p.position, calidad.fixed_choice(p.position, CHOICE))
    assert c.northing_m is not None
    # control 12 mm al sur y 9 mm al este de lo medido: medido − control = ΔN +0.012, ΔE −0.009 → ΔH 0.015
    control = [ControlCoordinate("c1", c.northing_m - 0.012, c.easting_m + 0.009, None),
               ControlCoordinate("NO-MEDIDO", 1.0, 2.0, 3.0)]
    report = calidad.evaluate_points(result([p]), tol(max_control_h_m=0.015), control, choice=CHOICE,
                                     control_source="control.csv")
    r = report.control[0]
    assert abs(r.delta_n_m - 0.012) < 1e-6 and abs(r.delta_e_m + 0.009) < 1e-6 and r.delta_h_m is None
    assert r.status.checks[0].text == "control ΔH 0.015 ≤ 0.015" and r.status.level == "pass"
    assert "sin cota" not in r.note             # el control no trae cota: no hay nada que comparar
    assert report.control_missing == ["NO-MEDIDO"] and "NO-MEDIDO" in report.notes[0]
    report = calidad.evaluate_points(result([p]), tol(max_control_h_m=0.014), control, choice=CHOICE)
    assert report.points[0].status.reason == "control ΔH 0.015 > 0.014"


def test_control_height_needs_a_geoid_and_compares_the_elevation():
    p = processed("C2")
    plain = crs.transform(p.position, calidad.fixed_choice(p.position, CHOICE))
    control = [ControlCoordinate("C2", None, None, 1885.0)]       # banco de nivel
    r = calidad.evaluate_points(result([p]), tol(), control, choice=CHOICE).control[0]
    assert r.delta_h_m is None and "elige un geoide" in r.note and plain.orthometric_height_m is None
    choice = CoordinateChoice("mexico-itrf2008-utm", "", "ggm10")
    c = crs.transform(p.position, calidad.fixed_choice(p.position, choice))
    if c.orthometric_height_m is None:
        pytest.skip("sin el geoide GGM10 en este equipo")
    control = [ControlCoordinate("C2", None, None, c.orthometric_height_m + 0.041)]
    r = calidad.evaluate_points(result([p]), tol(), control, choice=choice).control[0]
    assert abs(r.delta_h_m + 0.041) < 1e-6 and r.status.reason == "control ΔV 0.041 > 0.040"


def test_read_control_csv(tmp_path):
    path = tmp_path / "control.csv"
    path.write_text("C1,2419000.123,780000.456,1885.10,mojonera\nC2,2419010.000,780010.000,,\n", encoding="utf-8")
    points, notes = calidad.read_control(path, "pnezd")
    assert [(p.name, p.northing_m, p.easting_m, p.elevation_m) for p in points] == [
        ("C1", 2419000.123, 780000.456, 1885.10), ("C2", 2419010.0, 780010.0, None)]
    points, _ = calidad.read_control(path, "nxyzd")      # NXYZD: nombre, X (este), Y (norte)
    assert points[0].easting_m == 2419000.123 and points[0].northing_m == 780000.456


# --- Estático --------------------------------------------------------------------------------------------------------

def test_static_baselines_and_point(tmp_path):
    r = sinteticos.static_result(tmp_path, bases=2)
    r.baselines[0].pdop_max = 2.0
    r.baselines[1].pdop_max = 9.0                            # > 2 × 4: falla
    report = calidad.evaluate_static(r, tol(min_duration_s=7200.0))
    b0, b1 = report.baselines
    # la sesión es de 239 × 30 s = 1 h 59 min 30 s < 2 h: las dos líneas y el vértice, a revisar
    assert b0.status.level == "review" and b0.status.reason == "duración 1 h 59 min < 2 h 00 min"
    assert b1.status.level == "fail" and b1.status.reason.endswith("PDOP 9.0 > 4.0; duración 1 h 59 min < 2 h 00 min")
    point = report.points[0].status
    # y la línea que falla se dice
    assert point.level == "review" and "duración 1 h 59 min < 2 h 00 min" in point.reason
    assert "falla la línea base con PRB2" in point.reason and report.level == "fail"
    # una línea que el ajuste dejó fuera no cuenta para el proyecto
    r.adjustment.excluded_baselines = ["PRB2"]
    report = calidad.evaluate_static(r, tol(min_duration_s=7200.0))
    assert "PRB2" not in report.points[0].status.reason and report.level == "review"


def test_static_control_by_point_name(tmp_path):
    r = sinteticos.static_result(tmp_path, bases=2)
    c = crs.transform(r.position, calidad.fixed_choice(r.position, CHOICE))
    control = [ControlCoordinate("vert-01", c.northing_m + 0.030, c.easting_m, None)]
    report = calidad.evaluate_static(r, tol(), control, CHOICE)
    assert abs(report.control[0].delta_n_m + 0.030) < 1e-6
    assert "control ΔH 0.030 > 0.020" in report.points[0].status.reason


# --- PDOP -------------------------------------------------------------------------------------------------------------

def test_pdop_by_hand():
    # un satélite en el cenit y cuatro en el horizonte cada 90°: Q_EE = Q_NN = 0.5, Q_UU = 1.25 → PDOP 1.5
    geometry = [(0.0, math.pi / 2)] + [(math.radians(a), 0.0) for a in (0, 90, 180, 270)]
    assert abs(dop.pdop(geometry) - 1.5) < 1e-12
    assert dop.pdop(geometry[:3]) is None


def test_pdop_from_stat_matches_report_reader(tmp_path):
    import random
    from tresvizo_ppk.report import rtklib_salida
    rng = random.Random(3)
    sky = sinteticos._sky(rng)
    positions = [sinteticos.ecef(21.9, -102.3, 1890.0)] * 20
    _, stat = sinteticos.write_rtklib(tmp_path, "d", positions, 1.0, rng, sky)
    mine = dop.pdop_by_epoch(stat)
    theirs = rtklib_salida.dop_series(rtklib_salida.read_stat(stat))
    assert len(mine) == len(theirs) == 20
    for (t, p, *_), (tm, pm) in zip(theirs, sorted(mine.items())):
        assert t == tm.replace(tzinfo=timezone.utc) - timedelta(seconds=18) and abs(p - pm) < 1e-9
    # el proceso combinado escribe cada época dos veces: un satélite cuenta una vez
    text = Path(stat).read_text()
    Path(stat).write_text(text + text)
    assert dop.pdop_by_epoch(stat) == mine
