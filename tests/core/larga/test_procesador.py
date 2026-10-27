"""Procesador propio de líneas largas de punta a punta con datos reales de la RGNA: UGTO–UQRO (100.4 km), 1 h cada
30 s del 20-10-2026 con efemérides difundidas (tests/core/larga/datos), contra la referencia PPP del día."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from tresvizo_ppk.core import antex
from tresvizo_ppk.core.geodesy import covariance_ecef_to_enu, ecef_delta_to_enu, enu_to_ecef_delta
from tresvizo_ppk.core.larga import antena, procesador
from tresvizo_ppk.core.larga.astro import gps_seconds
from tresvizo_ppk.core.networks import find_station

# PPP estático de 24 h del día menos la coordenada oficial, mm E, N, U (docs/validacion.md)
MOTION = {"UGTO": (-152.9, -96.1, 58.9), "UQRO": (-138.5, -94.7, 18.8)}


def _official(sid):
    p = find_station(sid).position
    return np.array([p.x, p.y, p.z])


def _truth(sid):
    off = _official(sid)
    return off + np.array(enu_to_ecef_delta(off, np.array(MOTION[sid]) / 1000.0))


def _endpoint(sid, path, xyz):
    st = find_station(sid)
    choice = antex.choose(st.antenna_type, st.radome)
    ant = antena.receiver_antenna(choice.model.type, choice.model.radome)
    return procesador.Endpoint(path, xyz, st.antenna_height_m, ant, sid)


def _run(larga_data, minutes):
    rover = _endpoint("UGTO", larga_data["ugto"], _official("UGTO") + np.array([2.0, -3.0, 1.5]))
    base = _endpoint("UQRO", larga_data["uqro"], _official("UQRO"))
    start = gps_seconds(datetime(2026, 10, 20, 0, 0))
    return procesador.process(rover, base, [larga_data["nav"]], [], mask_deg=15.0, start=start,
                              end=start + minutes * 60 - 1)


def test_fixes_100_km_in_one_hour(larga_data):
    res = _run(larga_data, 60)
    assert res.fixed, res.reason
    assert res.n_nl_fixed >= procesador.NL_MIN_FIXED and res.ratio >= procesador.NL_MIN_RATIO
    assert res.success >= procesador.NL_MIN_SUCCESS
    assert res.n_wl_fixed >= 0.8 * res.n_amb
    truth_vec = _truth("UGTO") - _truth("UQRO")
    ugto = _truth("UGTO")
    err = np.array(ecef_delta_to_enu(ugto, (res.position - _official("UQRO")) - truth_vec))
    sig = np.sqrt(np.diag(covariance_ecef_to_enu(ugto, res.covariance)))
    assert np.all(np.abs(err) < 3 * sig), (err, sig)
    assert np.hypot(err[0], err[1]) < 0.02 and abs(err[2]) < 0.04
    # la fija mejora a la flotante y su σ es menor
    sig_float = np.sqrt(np.diag(covariance_ecef_to_enu(ugto, res.float_covariance)))
    assert np.all(sig < sig_float)
    assert {g.key.split(":")[0][0] for g in res.groups} == {"G", "E", "C"}
    assert res.ambiguities and all(abs(a.narrowlane_float - round(a.narrowlane_float)) < 0.5 for a in res.ambiguities)


def test_short_session_stays_float_with_reason(larga_data):
    res = _run(larga_data, 40)
    assert not res.fixed
    assert "corta" in res.reason and "60 min" in res.reason
    truth_vec = _truth("UGTO") - _truth("UQRO")
    ugto = _truth("UGTO")
    err = np.array(ecef_delta_to_enu(ugto, (res.position - _official("UQRO")) - truth_vec))
    sig = np.sqrt(np.diag(covariance_ecef_to_enu(ugto, res.covariance)))
    assert np.all(np.abs(err) < 3 * sig), (err, sig)


def test_minimum_duration_rule():
    assert procesador.min_fix_hours(30.0) == pytest.approx(0.5)
    assert procesador.min_fix_hours(100.0) == pytest.approx(1.0)
