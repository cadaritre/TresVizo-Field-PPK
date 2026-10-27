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
