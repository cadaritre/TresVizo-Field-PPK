"""Saltos de ciclo, arcos y prueba de confianza del ancho de carril con series sintéticas."""
from __future__ import annotations

import math

import numpy as np
import pytest

from tresvizo_ppk.core.larga import preproceso, senales
from tresvizo_ppk.core.larga.procesador import rounding_probability

PAIR = senales.make_pair("G", "1", "2")


def _series(n=200, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(n) * 30.0
    rho = 2.2e7 + 300.0 * t                                     # geometría que cambia
    iono = 3.0 + 0.5 * np.sin(t / 3000.0)                       # m en L1
    gamma = (PAIR.f1 / PAIR.f2) ** 2
    n1, n2 = 1_000_000.0, 800_000.0
    l1 = (rho - iono) / PAIR.lam1 + n1 + rng.normal(0, 0.01, n)
    l2 = (rho - gamma * iono) / PAIR.lam2 + n2 + rng.normal(0, 0.01, n)
    p1 = rho + iono + rng.normal(0, 0.3, n)
    p2 = rho + gamma * iono + rng.normal(0, 0.3, n)
    return t, p1, l1, p2, l2


def _arcs(t, p1, l1, p2, l2, lli=None):
    lli = np.zeros(len(t), dtype=np.int8) if lli is None else lli
    c = preproceso.combos(PAIR, p1, l1, p2, l2, lli, np.zeros_like(lli))
    return preproceso.detect_arcs(t, c, np.ones(len(t), dtype=bool), 30.0)


def test_clean_series_is_one_arc():
    arc, bad = _arcs(*_series())
    assert set(arc) == {0} and not bad.any()


@pytest.mark.parametrize("d1, d2", [(1, 0), (0, 1), (1, 1), (5, 4), (9, 7)])
def test_detects_cycle_slips(d1, d2):
    t, p1, l1, p2, l2 = _series()
    l1[120:] += d1
    l2[120:] += d2
    arc, _ = _arcs(t, p1, l1, p2, l2)
    assert arc[119] == 0 and arc[120] == 1 and set(arc) == {0, 1}


def test_lli_and_gap_start_new_arcs():
    t, p1, l1, p2, l2 = _series()
    lli = np.zeros(len(t), dtype=np.int8)
    lli[50] = 1
    t = t.copy()
    t[150:] += 600.0                                               # hueco de 10 min
    arc, _ = _arcs(t, p1, l1, p2, l2, lli)
    assert arc[49] == 0 and arc[50] == 1 and arc[149] == 1 and arc[150] == 2


def test_isolated_code_outlier_is_dropped_not_a_slip():
    t, p1, l1, p2, l2 = _series()
    p1[80] += 8.0
    arc, bad = _arcs(t, p1, l1, p2, l2)
    assert bad[80] and set(arc[arc >= 0]) == {0}


def test_sd_arcs_drop_short_ones():
    t = np.arange(100) * 30.0
    r = np.zeros(100, dtype=int)
    b = np.zeros(100, dtype=int)
    b[90:] = 1                                                      # salto en la base a 45 min
    out = preproceso.sd_arcs(r, b, t, 600.0, 30.0)
    assert set(out[:90]) == {0} and set(out[90:]) == {-1}          # el tramo de 5 min no entra


def test_autocorrelation_factor():
    rng = np.random.default_rng(0)
    white = rng.normal(size=5000)
    assert preproceso.autocorr_factor([white]) == pytest.approx(1.0, abs=0.1)
    ar = np.zeros(5000)
    for i in range(1, 5000):
        ar[i] = 0.8 * ar[i - 1] + rng.normal()
    assert preproceso.autocorr_factor([ar]) == pytest.approx((1 + 0.8) / (1 - 0.8), rel=0.15)


def test_rounding_probability():
    assert rounding_probability(0.0, 0.05) == pytest.approx(1.0)
    assert rounding_probability(0.0, 0.2) == pytest.approx(math.erf(0.5 / (math.sqrt(2) * 0.2)), abs=1e-12)
    assert rounding_probability(0.3, 0.1) < 0.999 < rounding_probability(0.1, 0.1)
    assert rounding_probability(0.0, 0.16) < 0.999 < rounding_probability(0.0, 0.15)
    assert rounding_probability(0.5, 0.01) == pytest.approx(0.5, abs=1e-9)
