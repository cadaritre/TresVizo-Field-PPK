from pathlib import Path

import numpy as np

from tresvizo_ppk.core import rtklib

POS = """% program   : RNX2RTKP ver.EX 2.5.1
%  GPST                      x-ecef(m)      y-ecef(m)      z-ecef(m)   Q  ns   sdx(m)   sdy(m)   sdz(m)  sdxy(m)  sdyz(m)  sdzx(m) age(s)  ratio
2026/10/20 14:00:15.000  -1164730.1638  -5843944.7291   2272414.2484   1  21   0.0030   0.0060   0.0040   0.0020  -0.0030   0.0010  15.00    7.2
2026/10/20 14:00:30.000  -1164730.1600  -5843944.7300   2272414.2500   2  20   0.0300   0.0600   0.0400   0.0000   0.0000   0.0000  15.00    1.1
"""
STAT = """$POS,2441,223215.000,1,-1164730.1638,-5843944.7291,2272414.2484,0.0,0.0,0.0
$SAT,2441,223215.000,G23,1,80.0,35.3,0.1234,0.0050,1,44,2,0,10,0,0,0,0.00,0.000000,0.00000
$SAT,2441,223215.000,G23,2,80.0,35.3,0.2234,-0.0030,1,40,2,0,10,0,0,0,0.00,0.000000,0.00000
$SAT,2441,223215.000,E11,1,10.0,12.0,0.0,0.0,0,30,0,0,0,0,0,0,0.00,0.000000,0.00000
"""


def test_parse_pos_covariance_signs(tmp_path):
    path = tmp_path / "x.pos"
    path.write_text(POS)
    epochs = rtklib.parse_pos(path)
    assert len(epochs) == 2 and epochs[0].q == 1 and epochs[0].ns == 21 and epochs[0].ratio == 7.2
    c = epochs[0].cov
    assert abs(c[0, 0] - 0.003 ** 2) < 1e-12 and abs(c[0, 1] - 0.002 ** 2) < 1e-12
    assert abs(c[1, 2] + 0.003 ** 2) < 1e-12 and np.allclose(c, c.T)


def test_parse_stat_and_rms(tmp_path):
    path = tmp_path / "x.pos.stat"
    path.write_text(STAT)
    rows = rtklib.parse_stat(path)
    assert len(rows) == 3 and rows[0].sat == "G23" and rows[0].valid and not rows[2].valid
    assert abs(rtklib.residual_rms(rows) - ((0.005 ** 2 + 0.003 ** 2) / 2) ** 0.5) < 1e-12
    assert rtklib.satellites_used(rows) == ["G23"]
    assert rows[0].time.hour == 14 and rows[0].time.minute == 0 and rows[0].time.second == 15


def test_config_has_documented_keys(tmp_path):
    cfg = rtklib.base_config(mode="static", options=None, frequencies=2, ionosphere="dual-freq", troposphere="est-ztd",
                             navsys=rtklib.navsys_mask(["GPS", "Galileo"]), ambiguity="off", glonass_ar="off",
                             precise=False, antex=None, rover=rtklib.AntennaSetup("", (0, 0, 1.916)),
                             base=rtklib.AntennaSetup("TRM115000.00 NONE", (0, 0, 0.084)),
                             base_xyz=(-1079874.5339, -5876441.4062, 2229716.6036), interval_s=15.0,
                             elevation_mask=15.0, min_ratio=3.0, combined=True)
    path = tmp_path / "x.conf"
    cfg.write(path)
    text = path.read_text()
    for needle in ("pos1-posmode      =static", "pos1-soltype      =combined", "pos1-navsys       =9",
                   "pos1-ionoopt      =dual-freq", "ant2-postype      =xyz", "ant2-pos1         =-1079874.5339",
                   "ant1-antdelu      =1.916", "ant2-anttype      =TRM115000.00 NONE", "pos2-gloarmode    =off",
                   "out-solformat     =xyz", "out-outstat       =residual"):
        assert needle in text, needle
    # todas las claves existen en RTKLIB-EX 2.5.1 (src/options.c)
    options_c = (Path(__file__).resolve().parents[2] / "third_party" / "rtklib" / "src" / "options.c").read_text()
    for key in cfg.values:
        assert f'"{key}"' in options_c, key
