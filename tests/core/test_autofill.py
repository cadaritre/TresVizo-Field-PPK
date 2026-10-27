"""Lo que traen los archivos llega lleno y con su origen, y la coordenada de la base no se pide a mano."""
import sys
from pathlib import Path

import numpy as np
import pytest

from tresvizo_ppk.core import api, rinex
from tresvizo_ppk.core.geodesy import ecef_delta_to_enu
from tresvizo_ppk.model import ProcessingOptions

from motor_util import DATA, gunzip, needs_tools


def test_rgna_rinex_fills_everything_with_origin(tmp_path):
    obs = api.prepare_observations([DATA / "ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz"], tmp_path / "b")
    assert obs.marker == "ICEP" and obs.point_name == "ICEP" and obs.receiver == "TRIMBLE ALLOY"
    assert obs.antenna_type == "TRM115000.00" and obs.radome == "NONE" and obs.interval_s == 15.0
    assert obs.antenna_height_m == pytest.approx(api.find_station("ICEP").antenna_height_m)
    assert obs.field_sources["marker"] == "encabezado RINEX" and obs.field_sources["interval_s"] == "encabezado RINEX"
    assert obs.field_sources["antenna_height_m"].startswith("catálogo RGNA")
    assert obs.approx_position is not None and obs.approx_position_source == "encabezado RINEX"


def test_catalog_wins_over_header_and_warns(tmp_path, unzip):
    src = unzip("ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz")
    text = src.read_text(encoding="latin-1").replace(
        "        0.1210        0.0000        0.0000                  ANTENNA: DELTA H/E/N",
        "        0.5000        0.0000        0.0000                  ANTENNA: DELTA H/E/N")
    assert "0.5000" in text
    src.write_text(text, encoding="latin-1")
    obs = api.prepare_observations([src], tmp_path / "b")
    assert obs.antenna_height_m == pytest.approx(0.121)
    assert any("se usa el catálogo" in w and "0.5000" in w for w in obs.warnings)


def test_meridian_session_pole_and_name(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "meridian_rinex"))
    from test_converter import FIRMWARE_META, unicore_session
    meta = FIRMWARE_META.replace(b'"session_name":null', b'"session_name":"Mojonera 7"')
    # recalcular la suma XOR de la sentencia $PTVZ
    body = meta[1:meta.rindex(b"*")]
    checksum = 0
    for b in body:
        checksum ^= b
    meta = meta[:meta.rindex(b"*") + 1] + f"{checksum:02X}".encode() + b"\r\n"
    rec = tmp_path / "sesion.bin"
    rec.write_bytes(unicore_session(meta=meta))
    obs = api.prepare_observations([rec], tmp_path / "t")
    assert obs.pole_height_m == pytest.approx(1.8) and obs.field_sources["pole_height_m"] == "sesión del receptor"
    assert obs.antenna_height_m == pytest.approx(1.916)
    assert obs.point_name == "Mojonera 7" and obs.marker == "Mojonera_7"
    assert obs.field_sources["point_name"] == "sesión del receptor"
    # lo que escribe el usuario manda
    obs2 = api.prepare_observations([rec], tmp_path / "t2", pole_height_m=2.0, marker="P9")
    assert obs2.antenna_height_m == pytest.approx(2.116) and obs2.marker == "P9"
    assert obs2.field_sources["pole_height_m"] == "indicada"


def test_official_base_is_the_only_candidate(tmp_path):
    obs = api.prepare_observations([DATA / "ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz"], tmp_path / "b")
    candidates = api.base_coordinate_candidates(obs)
    assert len(candidates) == 1 and candidates[0].official and candidates[0].coordinate_origin == "official"
    assert not candidates[0].approximate and candidates[0].position.frame == "ITRF2008"


@needs_tools
def test_own_base_candidates_header_and_autonomous(tmp_path, unzip):
    """Una base que no está en ningún catálogo (ICEP con otro nombre): encabezado y media autónoma, aproximadas."""
    src = unzip("ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz")
    src.write_text(src.read_text(encoding="latin-1").replace("ICEP" + " " * 56 + "MARKER NAME",
                                                             "MIBASE" + " " * 54 + "MARKER NAME"), encoding="latin-1")
    nav = DATA / "ITLA00MEX_R_20262931400_01H_MN.rnx.gz"
    obs = api.prepare_observations([src, nav], tmp_path / "b")
    assert obs.marker == "MIBASE" and api.find_station(obs.marker) is None
    header, autonomous = api.base_coordinate_candidates(obs, tmp_path / "c", autonomous=True)
    assert header.coordinate_origin == "rinex-header" and header.approximate and not header.official
    assert "encabezado RINEX" in header.source and "desplazados lo mismo que la base" in header.source
    assert autonomous.coordinate_origin == "autonomous" and autonomous.sigma_enu is not None
    assert autonomous.position.frame.startswith("WGS 84") and 2026.7 < autonomous.position.epoch < 2026.9
    off = api.find_station("ICEP").position
    origin = np.array([off.x, off.y, off.z])
    d = ecef_delta_to_enu(origin, np.array([autonomous.position.x, autonomous.position.y,
                                            autonomous.position.z]) - origin)
    assert abs(d[0]) < 5 and abs(d[1]) < 5 and abs(d[2]) < 10          # autónoma: metros
    # proceso con la base aproximada: corre y lo avisa
    rover = api.prepare_observations([DATA / "ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz", nav], tmp_path / "r")
    result = api.process_static(rover, [(header, obs)], ProcessingOptions(), tmp_path / "p")
    assert any("aproximada" in w for w in result.warnings)
