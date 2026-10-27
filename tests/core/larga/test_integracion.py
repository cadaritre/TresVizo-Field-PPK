"""El estático del motor usa el procesador propio en las líneas largas: process_static con 1 h de UGTO–UQRO
(100.4 km, datos reales de la RGNA) da la línea FIJA con su resolución de ambigüedades, RTKLIB corre como
comprobación independiente y la coordenada cae donde dice su σ frente a la referencia del día."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from motor_util import needs_tools
from tresvizo_ppk.core import api, baseline
from tresvizo_ppk.core.geodesy import ecef_delta_to_enu, enu_to_ecef_delta
from tresvizo_ppk.model import ProcessingOptions, Solution

DATA = Path(__file__).resolve().parent / "datos"

MOTION = {"UGTO": (-152.9, -96.1, 58.9), "UQRO": (-138.5, -94.7, 18.8)}     # PPP del día − oficial (mm)


@needs_tools
def test_static_long_line_fixed_by_own_processor(tmp_path):
    rover = api.prepare_observations([DATA / "UGTO00MEX_R_20262930000_01H_30S_MO.rnx.gz",
                                      DATA / "UQRO00MEX_R_20262930000_01H_MN.rnx.gz"], tmp_path / "rover")
    base = api.prepare_observations([DATA / "UQRO00MEX_R_20262930000_01H_30S_MO.rnx.gz"], tmp_path / "base")
    station = api.find_station("UQRO")
    result = api.process_static(rover, [(station, base)], ProcessingOptions(), tmp_path / "proceso")
    b = result.baselines[0]
    assert b.engine == "TresVizo" and b.solution == Solution.FIXED, b.warnings
    a = b.ambiguity
    assert a.fixed and a.narrowlane_fixed >= 4 and a.ratio >= 3.0 and a.success_rate >= 0.999
    assert a.min_session_h == 1.0 and a.signal_groups and any("GLONASS" in x for x in a.excluded)
    assert b.stat_file.is_file() and b.pos_file is None              # gráficas de datos de la comprobación
    assert "procesador propio" in b.strategy and "fijas" in b.strategy
    assert any("comprobación independiente" in line for line in result.log)
    assert not any("no concuerda" in w for w in result.warnings)
    # error del vector frente a la referencia del día, dentro de 3σ
    off_r, off_b = (np.array([p.x, p.y, p.z]) for p in (api.find_station("UGTO").position, station.position))
    truth = (off_r + np.array(enu_to_ecef_delta(off_r, np.array(MOTION["UGTO"]) / 1000))) - \
        (off_b + np.array(enu_to_ecef_delta(off_b, np.array(MOTION["UQRO"]) / 1000)))
    vec = np.array([b.vector_ecef.x, b.vector_ecef.y, b.vector_ecef.z])
    err = np.array(ecef_delta_to_enu(off_r, vec - truth))
    sigma = np.array([result.sigma_enu.e, result.sigma_enu.n, result.sigma_enu.u])
    assert np.all(np.abs(err) < 3 * sigma), (err, sigma)


def test_own_processor_applies_from_20_km():
    o = ProcessingOptions()
    assert not baseline.own_applies(19_000, o) and baseline.own_applies(20_000, o)
    assert not baseline.own_applies(100_000, o, mode="kinematic")
    assert not baseline.own_applies(100_000, ProcessingOptions(ionosphere="iono-free"))
    assert not baseline.own_applies(100_000, ProcessingOptions(frequencies=1))
