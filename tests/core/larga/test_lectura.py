"""Lector de observaciones (RINEX 3 y 2.11) y órbitas (navegación difundida y SP3) del procesador de líneas largas,
con datos reales de la RGNA y de la ESA (tests/core/larga/datos y tests/core/data)."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from tresvizo_ppk.core.larga import efemerides, observaciones, senales
from tresvizo_ppk.core.larga.astro import gps_seconds


def test_reads_rinex3_sample(larga_data):
    obs = observaciones.read(larga_data["ugto"])
    assert len(obs.times) == 120 and obs.interval == 30.0
    assert obs.times[0] == gps_seconds(datetime(2026, 10, 20, 0, 0, 0))
    assert {s[0] for s in obs.sats} == {"G", "E", "C"}
    assert obs.types["G"] == ["C1C", "L1C", "C2W", "L2W", "C2X", "L2X"]
    j = obs.column("G20")
    assert obs.values["C1C"][0, j] == pytest.approx(24330764.977)
    assert obs.values["L1C"][0, j] == pytest.approx(127859010.819)
    # los geoestacionarios de BeiDou no se leen
    assert not any(s in obs.sats for s in ("C01", "C02", "C03", "C04", "C05"))


def test_reads_window_and_decimation(larga_data):
    start = gps_seconds(datetime(2026, 10, 20, 0, 10))
    obs = observaciones.read(larga_data["ugto"], start=start, end=start + 600, decimate_s=60)
    assert len(obs.times) == 11 and np.all(np.diff(obs.times) == 60)


def test_reads_rinex2_with_rinex3_codes(larga_data):
    obs = observaciones.read(larga_data["ineg_v2"])
    assert set(obs.types["G"]) >= {"C1C", "L1C", "C1W", "L1W", "C2W", "L2W"}
    assert all(s[0] in "GE" for s in obs.sats)
    j = obs.sats.index(next(s for s in obs.sats if s[0] == "G"))
    assert np.nanmax(np.abs(obs.values["L1C"][:, j] - obs.values["L1W"][:, j])) == 0.0


def test_signal_pairs_match_classes():
    assert senales.signal_class("G", "2", "W") == senales.signal_class("G", "2", "P")
    assert senales.signal_class("G", "2", "X") == senales.signal_class("G", "2", "L")
    assert senales.signal_class("G", "2", "W") != senales.signal_class("G", "2", "X")
    pair = senales.make_pair("G", "1", "2")
    assert pair.lam_wl == pytest.approx(0.8619, abs=1e-4) and pair.lam_nl == pytest.approx(0.10695, abs=1e-5)
    assert pair.lam_nl + pair.kappa * 0 == pytest.approx(pair.lam_nl)
    # B_IF = λ_NL·N1 + κ·N_WL, con la definición de la libre de ionosfera
    n1, n2 = 7, 3
    b_if = pair.alpha * pair.lam1 * n1 - pair.beta * pair.lam2 * n2
    assert b_if == pytest.approx(pair.lam_nl * n1 + pair.kappa * (n1 - n2), abs=1e-9)


def test_broadcast_matches_precise_orbits(larga_data):
    nav = efemerides.Broadcast(efemerides.read_nav([larga_data["nav"]]))
    precise = efemerides.Precise([larga_data["sp3"]])
    t = gps_seconds(datetime(2026, 10, 20, 0, 30)) + np.arange(0, 1800, 300.0)
    checked = 0
    for sat in ("G01", "G14", "E07", "E13", "C20", "C30"):
        pb, cb, okb = nav.state(sat, t)
        pp, cp, okp = precise.state(sat, t)
        ok = okb & okp
        assert ok.any(), sat
        d = pb[ok] - pp[ok]
        radial = np.sum(d * pp[ok] / np.linalg.norm(pp[ok], axis=1)[:, None], axis=1)
        # las difundidas van al centro de fase (más cerca de la Tierra que el centro de masa del SP3)
        assert np.all(np.linalg.norm(d, axis=1) < 5.0), sat
        assert np.all(radial < -0.3), sat
        assert np.all(np.abs(cb[ok] - cp[ok]) < 100e-9), sat        # relojes a < 100 ns (BeiDou: otra referencia)
        checked += 1
    assert checked == 6


def test_sp3_lagrange_interpolation(larga_data):
    precise = efemerides.Precise([larga_data["sp3"]])
    sat = "G05"
    times, pos = precise.times, precise.pos[sat]
    # sin una de cada dos épocas, la interpolación de las que faltan queda a menos de 1 cm
    coarse = efemerides._lagrange(times[::2], pos[::2], times[9:17:2])
    assert np.max(np.linalg.norm(coarse - pos[9:17:2], axis=1)) < 0.01
    # en las épocas del SP3 da el valor exacto
    exact = efemerides._lagrange(times, pos, times[8:12])
    assert np.allclose(exact, pos[8:12], atol=1e-6)
