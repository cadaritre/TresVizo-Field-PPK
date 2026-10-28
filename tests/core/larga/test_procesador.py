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
    assert procesador.min_fix_hours(30.0) == pytest.approx(0.75)
    assert procesador.min_fix_hours(100.0) == pytest.approx(1.0)


def test_minimum_duration_with_high_mask():
    """Con máscara de más de 20° hacen falta 2 h (§7: con 25–30° y 1 h hubo fijos a 3.6 y 6.8σ; con 2 h, ninguno)."""
    assert procesador.min_fix_hours(100.0, 20.0) == pytest.approx(1.0)
    assert procesador.min_fix_hours(100.0, 25.0) == pytest.approx(2.0)
    assert procesador.min_fix_hours(30.0, 30.0) == pytest.approx(2.0)


def test_error_floor_revision():
    fixed = procesador.error_floor_enu(100.0, 1.0, False, True)
    assert fixed[2] == pytest.approx(1.15 * (0.004 + 0.006 + 0.002))
    base = procesador.error_floor_enu(100.0, 1.0, False, False, "corta") / np.array([1.6, 1.0, 1.2])
    failed = procesador.error_floor_enu(100.0, 1.0, False, False, "fallida")
    assert failed == pytest.approx(base * np.array([2.4, 1.0, 1.4]))


def test_high_mask_one_hour_stays_float_with_reason(larga_data):
    rover = _endpoint("UGTO", larga_data["ugto"], _official("UGTO") + np.array([2.0, -3.0, 1.5]))
    base = _endpoint("UQRO", larga_data["uqro"], _official("UQRO"))
    start = gps_seconds(datetime(2026, 10, 20, 0, 0))
    res = procesador.process(rover, base, [larga_data["nav"]], [], mask_deg=25.0, start=start, end=start + 3599)
    assert not res.fixed and "máscara de elevación de 25°" in res.reason and "2 h" in res.reason
    assert res.min_hours == pytest.approx(2.0)


def test_fix_float_coherence(larga_data, monkeypatch):
    """La fija se compara con la flotante: si se aleja más de FIX_FLOAT_K veces la σ de la flotante, no se fija.
    Con la sesión buena el salto es pequeño; con un límite artificial de 0.01σ la misma sesión queda flotante."""
    good = _run(larga_data, 60)
    assert good.fixed
    d, k = procesador._fix_float_jump(good.position, good.float_position, good.float_covariance)
    assert np.all(k < procesador.FIX_FLOAT_K) and np.all(np.abs(d) < 0.03)
    assert any("σ de la flotante" in line for line in good.log)
    monkeypatch.setattr(procesador, "FIX_FLOAT_K", 0.01)
    res = _run(larga_data, 60)
    assert not res.fixed and "se aleja" in res.reason and "de la flotante" in res.reason
    assert res.position == pytest.approx(res.float_position)
    # la flotante de una sesión que debía fijar y no fijó lleva la σ de «fallida» (mayor)
    sig_fail = np.sqrt(np.diag(covariance_ecef_to_enu(_official("UGTO"), res.covariance)))
    sig_short = np.sqrt(np.diag(covariance_ecef_to_enu(_official("UGTO"), good.float_covariance)))
    assert sig_fail[0] > sig_short[0] and sig_fail[2] > sig_short[2]


def test_imported_antex_reaches_the_long_baseline(larga_data, tmp_path):
    """Un ANTEX importado por el usuario (`antex.session`) cambia la solución del procesador de líneas largas, no solo
    el informe: el rover con una antena «de laboratorio» igual a la suya pero con el PCO vertical de G01 y G02 50 mm más
    alto da una altura (solo GPS) 50 mm más baja; sin importarlo, esa antena no tiene calibración."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from test_antenas import lab_antex

    start = gps_seconds(datetime(2026, 10, 20, 0, 0))
    base = _endpoint("UQRO", larga_data["uqro"], _official("UQRO"))

    def run(antenna):
        st = find_station("UGTO")
        rover = procesador.Endpoint(larga_data["ugto"], _official("UGTO") + np.array([2.0, -3.0, 1.5]),
                                    st.antenna_height_m, antenna, "UGTO")
        return procesador.process(rover, base, [larga_data["nav"]], [], mask_deg=15.0, start=start, end=start + 3599,
                                  systems="G")

    ref = run(antena.receiver_antenna("TRM115000.00", "NONE"))
    lab = lab_antex(tmp_path, "TVLAB50", source=("TRM115000.00", "NONE"))
    assert antena.receiver_antenna("TVLAB50", "NONE") is None
    with antex.session([lab], [("TVLAB50", "NONE")]):
        imported = antena.receiver_antenna("TVLAB50", "NONE")
    assert imported is not None
    moved = run(imported)
    d = np.array(ecef_delta_to_enu(_official("UGTO"), moved.float_position - ref.float_position)) * 1000
    assert d[2] == pytest.approx(-50.0, abs=3.0), d
    assert np.hypot(d[0], d[1]) < 3.0
