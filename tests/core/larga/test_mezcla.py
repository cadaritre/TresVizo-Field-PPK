"""Receptores con señales distintas y datos que no alcanzan, en el procesador de líneas largas (1 h de UGTO–UQRO,
100.4 km, cada 30 s).

- **Clases de señal mezcladas.** Un receptor que en GPS L2 solo registra P(Y) (como el NetR9 de IMIE en RINEX 2.10)
  contra uno que registra L2C (el Alloy de MEXI, o un Meridian): la doble diferencia de fase sigue siendo entera (el
  desfase de cada clase es el mismo en todos los satélites de un receptor y se cancela dentro del grupo), pero el
  sesgo de código de cada satélite entre clases no se cancela y sesga la Melbourne-Wübbena. Esos satélites entran con
  su ambigüedad flotante y su código con el sesgo como incertidumbre; los demás sistemas fijan igual. Antes se
  dejaban fuera y, con un solo sistema, el resultado era una «flotante» de kilómetros.
- **Datos que no alcanzan.** Con pocos satélites comunes o sin dos frecuencias en común no se entrega ninguna
  solución: `InsufficientData` dice por qué (y RTKLIB no la sustituye, tendría los mismos datos)."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

import editar_rinex
from tresvizo_ppk.core import antex
from tresvizo_ppk.core.geodesy import covariance_ecef_to_enu, ecef_delta_to_enu, enu_to_ecef_delta
from tresvizo_ppk.core.larga import antena, procesador
from tresvizo_ppk.core.larga.astro import gps_seconds
from tresvizo_ppk.core.networks import find_station

START = datetime(2026, 10, 20, 0, 0)
MOTION = {"UGTO": (-152.9, -96.1, 58.9), "UQRO": (-138.5, -94.7, 18.8)}       # PPP del día − oficial, mm
P_ONLY = [("C1C", "C1C"), ("L1C", "L1C"), ("C2W", "C2X"), ("L2W", "L2X")]   # «solo P(Y) en L2»


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


def _run(rover_path, base_path, nav, systems="GEC", minutes=60):
    rover = _endpoint("UGTO", rover_path, _official("UGTO") + np.array([2.0, -3.0, 1.5]))
    base = _endpoint("UQRO", base_path, _official("UQRO"))
    start = gps_seconds(START)
    return procesador.process(rover, base, [nav], [], mask_deg=15.0, start=start, end=start + minutes * 60 - 1,
                              systems=systems)


def _error(res, position=None):
    ugto = _truth("UGTO")
    pos = res.position if position is None else position
    return np.array(ecef_delta_to_enu(ugto, (pos - _official("UQRO")) - (ugto - _truth("UQRO"))))


def _sigma(res):
    return np.sqrt(np.diag(covariance_ecef_to_enu(_truth("UGTO"), res.covariance)))


@pytest.fixture(scope="module")
def p_only_base(larga_data, tmp_path_factory):
    return editar_rinex.retyped(larga_data["uqro"], tmp_path_factory.mktemp("mezcla") / "uqro_p.rnx", "G", P_ONLY)


def test_mixed_gps_classes_enter_float_and_the_rest_fixes(larga_data, p_only_base):
    res = _run(larga_data["ugto"], p_only_base, larga_data["nav"])
    mixed = [g for g in res.groups if "~" in g.key]
    assert mixed and all(not g.ar for g in mixed)
    assert "L2C en el rover y P(Y) en la base" in mixed[0].note
    assert len(mixed[0].satellites) >= 6
    assert res.fixed, res.reason                      # Galileo y BeiDou fijan
    err, sig = _error(res), _sigma(res)
    assert np.all(np.abs(err) < 3 * sig), (err, sig)
    assert any("clases distintas" in n for n in res.notes)


def test_mixed_gps_only_is_an_honest_float(larga_data, p_only_base):
    """Solo GPS, todo L2 mezclado: antes daba kilómetros; ahora una flotante de centímetros con su σ y el porqué."""
    res = _run(larga_data["ugto"], p_only_base, larga_data["nav"], systems="G")
    assert not res.fixed
    assert "clase de señal" in res.reason and "P(Y)" in res.reason
    err, sig = _error(res), _sigma(res)
    assert np.all(np.abs(err) < 3 * sig), (err, sig)
    assert np.all(sig < 0.3) and np.all(np.abs(err) < 0.3)


def test_few_common_satellites_is_rejected_with_reason(larga_data, tmp_path):
    keep = {"G01", "G14", "G17"}
    base = editar_rinex.only_sats(larga_data["uqro"], tmp_path / "uqro3.rnx", lambda s: s in keep)
    with pytest.raises(procesador.InsufficientData, match=r"Solo hay \d satélites comunes"):
        _run(larga_data["ugto"], base, larga_data["nav"], systems="G")


def test_no_second_frequency_in_common_is_rejected(larga_data, tmp_path):
    l1 = editar_rinex.retyped(larga_data["uqro"], tmp_path / "uqro_l1.rnx", "G", [("C1C", "C1C"), ("L1C", "L1C")])
    with pytest.raises(procesador.InsufficientData, match="no comparten dos frecuencias"):
        _run(larga_data["ugto"], l1, larga_data["nav"], systems="G")


def test_insufficient_data_is_a_user_error():
    from tresvizo_ppk.errors import UserError
    assert issubclass(procesador.InsufficientData, UserError)


def test_float_sanity_checks():
    xyz = np.array([-1.0e6, -6.0e6, 2.3e6])
    ok = np.diag([0.01, 0.01, 0.02]) ** 2
    procesador._check_float(xyz, ok, xyz + 0.5, 0.4)
    with pytest.raises(procesador.InsufficientData, match="σ de 12.0 m"):
        procesador._check_float(xyz, np.eye(3) * 144.0, xyz, 0.4)
    with pytest.raises(procesador.InsufficientData, match="96 000 km"):
        procesador._check_float(xyz + np.array([9.6e7, 0, 0]), ok, xyz, 0.4)
    with pytest.raises(procesador.InsufficientData, match="solución de código"):
        procesador._check_float(xyz, ok, xyz, 50.0)
