"""Épocas desalineadas entre rover y base en el procesador de líneas largas (1 h de UGTO–UQRO, 100.4 km, cada 30 s).

Antes, un rover y una base sin las mismas épocas tumbaban el proceso (`ValueError` al elegir la pareja de señales).
Un hueco, una época que falte, intervalos distintos, un rover a más frecuencia que la base o etiquetas de tiempo a
unos ms del segundo son datos de campo normales (un Meridian a 1 Hz contra la RGNA cada 15 s): se procesa en las
épocas comunes, al intervalo de proceso, y el resultado no cambia más de lo que cambian los datos."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

import editar_rinex
from tresvizo_ppk.core import antex
from tresvizo_ppk.core.geodesy import ecef_delta_to_enu
from tresvizo_ppk.core.larga import antena, procesador
from tresvizo_ppk.core.larga.astro import gps_seconds
from tresvizo_ppk.core.networks import find_station
from tresvizo_ppk.errors import UserError

START = datetime(2026, 10, 20, 0, 0)


def _official(sid):
    p = find_station(sid).position
    return np.array([p.x, p.y, p.z])


def _endpoint(sid, path, xyz):
    st = find_station(sid)
    choice = antex.choose(st.antenna_type, st.radome)
    ant = antena.receiver_antenna(choice.model.type, choice.model.radome)
    return procesador.Endpoint(path, xyz, st.antenna_height_m, ant, sid)


def _run(rover_path, base_path, nav, minutes=60, interval_s=None):
    rover = _endpoint("UGTO", rover_path, _official("UGTO") + np.array([2.0, -3.0, 1.5]))
    base = _endpoint("UQRO", base_path, _official("UQRO"))
    start = gps_seconds(START)
    return procesador.process(rover, base, [nav], [], mask_deg=15.0, start=start, end=start + minutes * 60 - 1,
                              interval_s=interval_s)


def _diff_mm(a, b):
    return np.array(ecef_delta_to_enu(_official("UGTO"), a.position - b.position)) * 1000.0


@pytest.fixture(scope="module")
def reference(larga_data):
    return _run(larga_data["ugto"], larga_data["uqro"], larga_data["nav"])


def test_align_epochs_grid():
    t_r = np.arange(0.0, 600.0, 1.0) + 0.003                      # 1 Hz, etiquetas a 3 ms del segundo
    t_b = np.delete(np.arange(0.0, 600.0, 15.0), 7)                # cada 15 s, falta una época
    times, ir, ib, proc = procesador.align_epochs(t_r, t_b, 15.0, 1.0, 15.0)
    assert proc == 15.0 and len(times) == 39
    assert np.allclose(t_r[ir] - times, 0.003) and np.allclose(t_b[ib], times)
    # 15 s contra 30 s: rejilla de 30 s; 10 s contra 15 s: la común es de 30 s
    assert procesador.align_epochs(np.arange(0, 600, 15.0), np.arange(0, 600, 30.0), 15.0, 15.0, 30.0)[3] == 30.0
    times, _, _, proc = procesador.align_epochs(np.arange(0, 600, 10.0), np.arange(0, 600, 15.0), 15.0, 10.0, 15.0)
    assert proc == 30.0 and np.all(np.remainder(times, 30.0) == 0)
    # etiquetas a más de la tolerancia no se toman como la misma época
    assert len(procesador.align_epochs(np.arange(0, 600, 15.0) + 0.5, np.arange(0, 600, 15.0), 15.0, 15.0,
                                       15.0)[0]) == 0


def test_gap_in_rover(larga_data, reference, tmp_path):
    gap = editar_rinex.keep(larga_data["ugto"], tmp_path / "hueco.rnx",
                            lambda t: not (20 <= (t - START).total_seconds() / 60 < 30))
    res = _run(gap, larga_data["uqro"], larga_data["nav"])
    assert res.fixed, res.reason
    assert res.epochs == reference.epochs - 20
    assert np.all(np.abs(_diff_mm(res, reference)) < [10, 10, 20])


def test_one_missing_epoch_each_side(larga_data, reference, tmp_path):
    rover = editar_rinex.keep(larga_data["ugto"], tmp_path / "r.rnx", lambda t: (t - START).total_seconds() != 900)
    base = editar_rinex.keep(larga_data["uqro"], tmp_path / "b.rnx", lambda t: (t - START).total_seconds() != 1800)
    res = _run(rover, base, larga_data["nav"])
    assert res.fixed and res.epochs == reference.epochs - 2
    assert np.all(np.abs(_diff_mm(res, reference)) < 3.0)


def test_different_intervals(larga_data, reference, tmp_path):
    base60 = editar_rinex.keep(larga_data["uqro"], tmp_path / "b60.rnx", lambda t: t.second == 0)
    res = _run(larga_data["ugto"], base60, larga_data["nav"])
    assert res.interval_s == 60.0 and res.epochs == 60
    assert np.all(np.abs(_diff_mm(res, reference)) < [10, 10, 20])
    sig = np.sqrt(np.diag(np.array(res.covariance)))
    assert np.all(np.isfinite(sig))


def test_rover_faster_than_base(larga_data, reference, tmp_path):
    """Rover cada 5 s (épocas intermedias) contra la base cada 30 s: las épocas sin pareja se descartan y el
    resultado es el mismo que con el rover cada 30 s."""
    dense = editar_rinex.densified(larga_data["ugto"], tmp_path / "r5.rnx", 5.0)
    res = _run(dense, larga_data["uqro"], larga_data["nav"])
    assert res.interval_s == 30.0 and res.epochs == reference.epochs
    assert res.fixed == reference.fixed
    assert np.all(np.abs(_diff_mm(res, reference)) < 0.5)


def test_time_tags_off_by_milliseconds(larga_data, reference, tmp_path):
    """Rover con el reloj sin gobernar: etiquetas de −0.9 a +0.9 ms y +3 ms, con código y fase coherentes. La
    geometría se calcula en la etiqueta real; el desfase queda como reloj del receptor y se elimina. Sin eso, el error
    sería de metros (la distancia a cada satélite cambia hasta 800 m/s). Lo que queda es el redondeo del archivo editado
    (0.001 ciclos y 1 mm en el código), que mueve qué datos se descartan: menos de 2 mm."""
    saw = editar_rinex.shifted(larga_data["ugto"], tmp_path / "rs.rnx", lambda k: 0.003 + 0.0009 * ((k % 7) - 3) / 3)
    res = _run(saw, larga_data["uqro"], larga_data["nav"])
    assert res.fixed == reference.fixed and res.epochs == reference.epochs
    assert np.all(np.abs(_diff_mm(res, reference)) < 2.0)
    assert any("ms del segundo" in line for line in res.log)


def test_no_common_epochs_is_a_clear_error(larga_data, tmp_path):
    late = editar_rinex.shifted(larga_data["ugto"], tmp_path / "rl.rnx", lambda k: 0.5)
    with pytest.raises(UserError, match="no coinciden en el tiempo"):
        _run(late, larga_data["uqro"], larga_data["nav"])
