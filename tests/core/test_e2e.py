"""De punta a punta con datos reales de la RGNA (ITLA–ICEP, 30.7 km, 20 min, 20-10-2026) y RTKLIB de verdad.

Referencia del día (PPP estático de 24 h con órbitas y relojes finales de la ESA, scripts/validacion_rgna.py): la
posición real de ITLA con ICEP fija en su coordenada oficial queda, respecto de la oficial de ITLA,
en (−35.7, +24.2, +99.5) mm E, N, U (movimiento relativo de las dos estaciones desde 2010).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from tresvizo_ppk.core import api, cli
from tresvizo_ppk.core.geodesy import ecef_delta_to_enu, ecef_to_geodetic
from tresvizo_ppk.model import ProcessingOptions, Solution

from motor_util import DATA, needs_tools

TRUTH_MM = np.array([-35.7, 24.2, 99.5])
ROVER = [DATA / "ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz", DATA / "ITLA00MEX_R_20262931400_01H_MN.rnx.gz"]
BASE = [DATA / "ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz"]


def enu_mm(station_id, position):
    off = api.find_station(station_id).position
    origin = np.array([off.x, off.y, off.z])
    return np.array(ecef_delta_to_enu(origin, np.array([position.x, position.y, position.z]) - origin)) * 1000


@needs_tools
def test_static_one_base(tmp_path):
    rover = api.prepare_observations(ROVER, tmp_path / "rover")
    assert rover.marker == "ITLA" and rover.epochs == 81 and len(rover.nav) == 1
    base = api.prepare_observations(BASE, tmp_path / "base")
    station = api.find_station(base.marker)
    assert station.id == "ICEP" and station.network == "rgna-inegi"
    result = api.process_static(rover, [(station, base)], ProcessingOptions(), tmp_path / "proceso",
                                point_name="ITLA-prueba")
    b = result.baselines[0]
    assert result.adjustment is None and result.point_name == "ITLA-prueba"
    assert 30_600 < b.length_m < 30_800 and b.solution in (Solution.FIXED, Solution.FLOAT)
    assert b.pos_file.is_file() and b.stat_file == Path(str(b.pos_file) + ".stat") and b.stat_file.is_file()
    assert b.config_file.is_file() and len(b.satellites) > 15 and b.rms_m is not None
    assert result.position.frame == "ITRF2008" and result.position.epoch == 2010.0
    assert any("una sola base" in w.lower() for w in result.warnings)
    # error frente a la referencia del día dentro de 3σ del modelo de error
    error = enu_mm("ITLA", result.position) - TRUTH_MM
    sigma = np.array([result.sigma_enu.e, result.sigma_enu.n, result.sigma_enu.u]) * 1000
    assert np.all(np.abs(error) < 3 * sigma), (error, sigma)
    assert result.base_quality["ICEP"].epochs > 70 and result.rover_quality.epochs == 81
    assert any("RTKLIB-EX 2.5.1" in line for line in result.log)


@needs_tools
def test_points_and_cli(tmp_path):
    off = api.find_station("ITLA").position
    lat, lon, h = ecef_to_geodetic(off.x, off.y, off.z)
    csv = tmp_path / "puntos.csv"
    csv.write_text("﻿Name,Code,Longitude,Latitude,Ellipsoidal height,Origin,Antenna height,Solution status,"
                   "Averaging start,Averaging end,Device type,Antenna offset,Total antenna height\r\n"
                   f"P1,MOJ,{lon:.8f},{lat:.8f},{h:.3f},Global,,FIX,2026-10-20 08:02:00.0 UTC-06:00,"
                   "2026-10-20 08:05:00.0 UTC-06:00,,,0.067\r\n"
                   "P2,MOJ,,,,Global,,FIX,2026-10-20 15:10:00.0 UTC,2026-10-20 15:11:00.0 UTC,,,0.067\r\n",
                   encoding="utf-8")
    out = tmp_path / "resultado.json"
    code = cli.main(["puntos", "--rover", *map(str, ROVER), "--base", *map(str, BASE), "--csv", str(csv),
                     "--salida", str(tmp_path / "trabajo"), "--json", str(out), "--silencio"])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["base"]["id"] == "ICEP" and len(data["points"]) == 2
    p1, p2 = data["points"]
    assert p1["solution"] in ("fixed", "float") and p1["epochs_total"] == 12          # épocas GPS :00/:15… = UTC :42/:57…
    assert p2["solution"] == "none" and p2["position"] is None
    assert Path(data["pos_file"]).is_file() and Path(data["stat_file"]).is_file()
    # la marca de ITLA (antena − 0.067 m) cae donde dice su σ (flotante: holgada) respecto de la referencia del día
    pos = p1["position"]
    error = enu_mm("ITLA", type("E", (), pos)()) - TRUTH_MM
    sigma = np.array([p1["sigma_enu"]["e"], p1["sigma_enu"]["n"], p1["sigma_enu"]["u"]]) * 1000
    assert np.all(np.abs(error) < 3 * sigma), (error, sigma)


@needs_tools
def test_static_cli_and_user_errors(tmp_path, capsys):
    out = tmp_path / "estatico.json"
    code = cli.main(["estatico", "--rover", *map(str, ROVER), "--punto", "ITLA-CLI", "--base", *map(str, BASE),
                     "--salida", str(tmp_path / "t"), "--json", str(out), "--silencio", "--ionosfera", "iono-free"])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["point_name"] == "ITLA-CLI" and data["baselines"][0]["solution"] == "float"
    assert "libre de ionosfera" in data["baselines"][0]["strategy"]
    code = cli.main(["estatico", "--rover", str(tmp_path / "no-existe.bin"), "--base", *map(str, BASE),
                     "--salida", str(tmp_path / "t2"), "--silencio"])
    assert code == 1 and "No existe" in capsys.readouterr().err


def test_prepare_meridian_recording(tmp_path):
    """Una grabación .bin del MeridianV se convierte con el convertidor incluido y la altura del bastón + case."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "meridian_rinex"))
    from test_converter import unicore_session
    rec = tmp_path / "sesion.bin"
    rec.write_bytes(unicore_session())
    obs = api.prepare_observations([rec], tmp_path / "trabajo", pole_height_m=1.8, marker="P01")
    assert obs.receiver_model == "MeridianV" and obs.case_offset_m == pytest.approx(0.116)
    assert obs.antenna_height_m == pytest.approx(1.916) and obs.marker == "P01" and obs.epochs == 4
    assert obs.antenna_pco_measured is False and obs.antenna_type == "HARXON HA-901A"
