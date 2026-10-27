import math

import numpy as np
import pytest

from tresvizo_ppk.core import adjust
from tresvizo_ppk.core.geodesy import enu_to_ecef_delta, geodetic_to_ecef, covariance_enu_to_ecef

P = np.array(geodetic_to_ecef(21.0, -101.27, 2062.0))


def line(base_id, enu_offset, sigma_enu):
    position = P + np.array(enu_to_ecef_delta(P, enu_offset))
    cov = covariance_enu_to_ecef(P, np.diag(np.square(sigma_enu)))
    return adjust.LineObservation(base_id, position, cov)


def test_chi2_quantiles_against_tables():
    # tablas de χ²: 95 % con 3 gl = 7.815, con 6 gl = 12.592; 5 % con 3 gl = 0.352
    assert abs(adjust.chi2_ppf(0.95, 3) - 7.8147) < 1e-3
    assert abs(adjust.chi2_ppf(0.95, 6) - 12.5916) < 1e-3
    assert abs(adjust.chi2_ppf(0.05, 3) - 0.3518) < 1e-3
    assert abs(adjust.chi2_cdf(5.991, 2) - 0.95) < 1e-4


def test_two_equal_lines_resolved_by_hand():
    # dos líneas con la misma σ: la ajustada es el promedio; residuos ±d/2; χ² = Σ (d/2)²/σ² por componente
    a = line("A", (0.005, -0.002, 0.010), (0.01, 0.01, 0.02))
    b = line("B", (-0.005, 0.002, -0.010), (0.01, 0.01, 0.02))
    r = adjust.adjust([a, b], "ITRF2008", 2010.0)
    assert np.allclose([r.position.x, r.position.y, r.position.z], P, atol=1e-9)
    assert abs(r.residuals_enu["A"].e - 0.005) < 1e-9 and abs(r.residuals_enu["B"].u + 0.010) < 1e-9
    chi2 = 2 * ((0.005 / 0.01) ** 2 + (0.002 / 0.01) ** 2 + (0.010 / 0.02) ** 2)
    assert abs(r.chi2 - chi2) < 1e-5 and r.degrees_of_freedom == 3
    assert abs(r.sigma0 - math.sqrt(chi2 / 3)) < 1e-6
    # σ de la media de dos observaciones iguales: σ/√2 (σ0 < 1, sin escalar)
    assert abs(r.sigma_enu.e - 0.01 / math.sqrt(2)) < 1e-8 and abs(r.sigma_enu.u - 0.02 / math.sqrt(2)) < 1e-8
    assert r.passed and r.flagged_baselines == []
    # w de Baarda: residuo / σ del residuo (σ_r² = σ² − σ²/2)
    assert abs(r.standardized_residuals["A"].e - 0.005 / math.sqrt(0.01 ** 2 / 2)) < 1e-6


def test_weighted_mean():
    a = line("A", (0.0, 0.0, 0.0), (0.01, 0.01, 0.01))
    b = line("B", (0.03, 0.0, 0.0), (0.02, 0.02, 0.02))
    r = adjust.adjust([a, b], "ITRF2008", 2010.0)
    from tresvizo_ppk.core.geodesy import ecef_delta_to_enu
    e, n, u = ecef_delta_to_enu(P, np.array([r.position.x, r.position.y, r.position.z]) - P)
    # pesos 1/σ²: (0·10000 + 0.03·2500) / 12500 = 0.006
    assert abs(e - 0.006) < 1e-9 and abs(n) < 1e-9 and abs(u) < 1e-9


def test_outlier_line_is_flagged_with_three_bases():
    good1 = line("UQRO", (0.004, -0.003, 0.010), (0.01, 0.01, 0.02))
    good2 = line("ISLP", (-0.005, 0.002, -0.008), (0.01, 0.01, 0.02))
    bad = line("INEG", (0.010, -0.018, 0.735), (0.01, 0.01, 0.02))       # el hundimiento de INEG
    r = adjust.adjust([good1, good2, bad], "ITRF2008", 2010.0)
    assert not r.passed and r.degrees_of_freedom == 6
    assert r.flagged_baselines == ["INEG"]
    assert abs(r.standardized_residuals["INEG"].u) > adjust.W_CRITICAL


def test_two_inconsistent_lines_flag_both():
    a = line("A", (0.0, 0.0, 0.0), (0.01, 0.01, 0.02))
    b = line("B", (0.0, 0.0, 0.30), (0.01, 0.01, 0.02))
    r = adjust.adjust([a, b], "ITRF2008", 2010.0)
    assert not r.passed and sorted(r.flagged_baselines) == ["A", "B"]
    assert r.sigma0 > 1 and r.sigma_enu.u > 0.02 / math.sqrt(2)      # escalada por σ0²


def test_needs_two_lines():
    with pytest.raises(ValueError):
        adjust.adjust([line("A", (0, 0, 0), (0.01, 0.01, 0.01))], "ITRF2008", 2010.0)


def test_static_excludes_single_bad_line(monkeypatch, tmp_path):
    """process_static: con tres bases y una que no cuadra, la deja fuera si el ajuste sin ella pasa (sin RTKLIB)."""
    from tresvizo_ppk.core import static
    from tresvizo_ppk.model import BaselineSolution, Ecef, Enu, ObservationSet, ProcessingOptions, Solution, Station
    from datetime import datetime, timezone
    offsets = {"UQRO": (0.004, -0.003, 0.010), "ISLP": (-0.005, 0.002, -0.008), "INEG": (0.010, -0.018, 0.735)}
    t0 = datetime(2026, 10, 20, 14, tzinfo=timezone.utc)

    def fake_baseline(rover, station, base, options, workdir, **kw):
        obs = line(station.id, offsets[station.id], (0.01, 0.01, 0.02))
        pos = Ecef(*obs.position)
        return BaselineSolution(base=station, rover_marker="UGTO", start=t0, end=t0, epochs_used=10, satellites=[],
                                solution=Solution.FLOAT, ratio=None, rms_m=0.01, vector_ecef=pos, vector_enu=Enu(0, 0, 0),
                                length_m=1e5, azimuth_deg=0, zenith_deg=90,
                                covariance_ecef=tuple(map(tuple, obs.covariance)), rover_position=pos,
                                ephemeris=options.ephemeris), None

    monkeypatch.setattr(static, "process_baseline", fake_baseline)
    monkeypatch.setattr(static, "trim", lambda obs, *a, **k: obs)
    monkeypatch.setattr(static.quality, "assess", lambda obs, cancel=None: __import__("tresvizo_ppk.model", fromlist=["DataQuality"]).DataQuality())
    rover = ObservationSet(obs=[], marker="UGTO", start=t0, end=t0)
    bases = [(Station(id=s, name=s, position=Ecef(*P)), ObservationSet(obs=[], start=t0, end=t0)) for s in offsets]
    result = static.process_static(rover, bases, ProcessingOptions(), tmp_path)
    a = result.adjustment
    assert a.excluded_baselines == ["INEG"] and a.passed and a.degrees_of_freedom == 3
    assert any("se dejó fuera" in w for w in result.warnings)
    assert abs(result.position.x - a.position.x) < 1e-12


def test_static_excludes_bad_line_when_the_rest_agree_by_w(monkeypatch, tmp_path):
    """Con líneas fijas (σ de milímetros) las dos bases buenas difieren unos 2 cm por su movimiento desde 2010: no
    pasan la χ² pero sí la prueba w, así que la mala (INEG, 0.7 m) se deja fuera igual y se avisa de la diferencia."""
    from tresvizo_ppk.core import static
    from tresvizo_ppk.model import (BaselineSolution, DataQuality, Ecef, Enu, ObservationSet, ProcessingOptions,
                                    Solution, Station)
    from datetime import datetime, timezone
    offsets = {"UQRO": (0.008, -0.004, 0.008), "ISLP": (-0.008, 0.004, -0.008), "INEG": (0.010, -0.018, 0.735)}
    t0 = datetime(2026, 10, 20, 14, tzinfo=timezone.utc)

    def fake_baseline(rover, station, base, options, workdir, **kw):
        obs = line(station.id, offsets[station.id], (0.004, 0.004, 0.008))
        pos = Ecef(*obs.position)
        return BaselineSolution(base=station, rover_marker="UGTO", start=t0, end=t0, epochs_used=10, satellites=[],
                                solution=Solution.FIXED, ratio=5.0, rms_m=0.01, vector_ecef=pos,
                                vector_enu=Enu(0, 0, 0), length_m=1e5, azimuth_deg=0, zenith_deg=90,
                                covariance_ecef=tuple(map(tuple, obs.covariance)), rover_position=pos,
                                ephemeris=options.ephemeris), None

    monkeypatch.setattr(static, "process_baseline", fake_baseline)
    monkeypatch.setattr(static, "trim", lambda obs, *a, **k: obs)
    monkeypatch.setattr(static.quality, "assess", lambda obs, cancel=None: DataQuality())
    rover = ObservationSet(obs=[], marker="UGTO", start=t0, end=t0)
    bases = [(Station(id=s, name=s, position=Ecef(*P)), ObservationSet(obs=[], start=t0, end=t0)) for s in offsets]
    result = static.process_static(rover, bases, ProcessingOptions(), tmp_path)
    a = result.adjustment
    assert a.excluded_baselines == ["INEG"] and not a.passed and a.chi2 > a.chi2_upper
    assert max(max(abs(w.e), abs(w.n), abs(w.u)) for w in a.standardized_residuals.values()) <= a.critical_value
    assert any("se dejó fuera" in w for w in result.warnings)
    assert any("tampoco pasan la prueba χ²" in w and "movimiento de las estaciones" in w for w in result.warnings)
    # sin INEG: la media de UQRO e ISLP, lejos de los 0.7 m de INEG
    assert np.linalg.norm(np.array([a.position.x, a.position.y, a.position.z]) - np.array(P)) < 0.01
