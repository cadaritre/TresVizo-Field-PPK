"""Ajuste de red con vectores reales de la RGNA (15-07-2026): tres vértices «nuevos» (UGTO, TOL2, IHID) y tres de
control (INEG, ISLP, UQRO) en cuatro sesiones de 4 h con cuatro receptores, procesados con el motor de la app
(`scripts/red_rgna.py`; datos y fuente en `data/red_rgna_2026196.json`). No necesita RTKLIB.

Verdad independiente: el PPP estático de 24 h de cada estación ese día. INEG se hundió unos 0.69 m desde la época de
su coordenada oficial (2010.0); las demás se movieron casi lo mismo entre sí (1–2 cm de diferencia)."""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from tresvizo_ppk.core import red
from tresvizo_ppk.core.geodesy import ecef_delta_to_enu
from tresvizo_ppk.model import Ecef, NetworkConstraint, NetworkVector

DATA = json.loads((Path(__file__).resolve().parent / "data" / "red_rgna_2026196.json").read_text(encoding="utf-8"))
OFFICIAL = {k: np.array(v) for k, v in DATA["oficial"].items()}
PPP = {k: np.array(v) for k, v in DATA["ppp_24h"].items()}


def vectors():
    return [NetworkVector(id=v["id"], from_id=v["from"], to_id=v["to"], vector_ecef=Ecef(*v["vector"]),
                          covariance_ecef=tuple(tuple(r) for r in v["covariance"]),
                          start=datetime.fromisoformat(v["start"]), end=datetime.fromisoformat(v["end"]),
                          engine=v["engine"]) for v in DATA["vectores"]]


def control(use=("INEG", "ISLP", "UQRO")):
    return [NetworkConstraint(s, Ecef(*OFFICIAL[s], DATA["marco"], DATA["epoca"]), use=s in use)
            for s in DATA["control"]]


def xyz(p) -> np.ndarray:
    return np.array([p.position.x, p.position.y, p.position.z])


@pytest.fixture(scope="module")
def network():
    return red.adjust_network(vectors(), control())


def test_sessions_and_trivial_lines(network):
    assert all(v["solution"] == "fixed" for v in DATA["vectores"]) and len(DATA["vectores"]) == 16
    # sin sesión escrita: salen del horario (cuatro ventanas de 4 h)
    assert sorted(network.sessions) == ["S1", "S2", "S3", "S4"]
    assert [len(network.sessions[s]) for s in ("S1", "S2", "S3", "S4")] == [5, 4, 3, 4]
    # cuatro receptores por sesión: tres líneas independientes; las triviales, las más largas de cada ciclo
    assert {v.id for v in network.vectors if v.trivial} == {"S1:INEG-UGTO", "S1:UQRO-ISLP", "S2:UQRO-IHID",
                                                           "S4:UQRO-ISLP"}


def test_loop_closures(network):
    loops = network.loops
    assert len(loops) == 7 and not network.unchecked
    assert all(lp.status.level == "pass" and len(lp.sessions) >= 2 for lp in loops)
    assert max(lp.closure_h_m for lp in loops) < 0.006          # 0.9–4.4 mm en planta
    assert max(lp.closure_v_m for lp in loops) < 0.020          # 0.4–14.1 mm en cota
    assert max(lp.ppm_3d for lp in loops) < 0.05                # en bucles de 200–480 km
    assert all(lp.chi2 < lp.chi2_limit for lp in loops)


def test_minimal_constraint_passes_and_matches_the_ppp(network):
    m = network.minimal
    assert m.datum == "INEG fijo" and m.degrees_of_freedom == 21 and m.observations == 36
    assert m.passed and m.sigma0 < 1.0 and not m.flagged and m.scale_factor == 1.0
    # la forma de la red contra el PPP de 24 h del día (los 15 pares de puntos)
    points = {p.id: xyz(p) for p in m.points}
    errors = []
    ids = sorted(points)
    for k, a in enumerate(ids):
        for b in ids[k + 1:]:
            errors.append(ecef_delta_to_enu(points[a], (points[b] - points[a]) - (PPP[b] - PPP[a])))
    errors = np.abs(np.array(errors))
    assert errors[:, 0].max() < 0.015 and errors[:, 1].max() < 0.008 and errors[:, 2].max() < 0.025
    # las elipses relativas al 95 %: 7–12 mm en 100–183 km (≤ 0.1 ppm)
    assert all(e.semi_major_95_m < 0.015 and e.ppm_95 < 0.1 for e in m.relative)


def test_control_check_finds_the_subsidence_of_ineg(network):
    assert network.inconsistent_control[0] == "INEG"
    text = next(w for w in network.warnings if w.startswith("La coordenada de INEG no concuerda"))
    up = float(text.split(" U ")[1].split(" mm")[0])
    # lo que dice el PPP: INEG respecto de ISLP y UQRO, desde 2010.0
    motion = (PPP["INEG"] - OFFICIAL["INEG"]) - np.mean([PPP[s] - OFFICIAL[s] for s in ("ISLP", "UQRO")], axis=0)
    expected = ecef_delta_to_enu(OFFICIAL["INEG"], motion)[2] * 1000
    assert -720 < up < -650 and abs(up - expected) < 15
    # con las tres fijas, el restringido no pasa
    assert not network.constrained.passed


def test_constrained_to_islp_and_uqro_against_official_and_ppp():
    result = red.adjust_network(vectors(), control(use=("ISLP", "UQRO")))
    c = result.constrained
    assert c.datum == "ISLP, UQRO fijas"
    motion = np.mean([PPP[s] - OFFICIAL[s] for s in ("ISLP", "UQRO")], axis=0)
    for name in ("UGTO", "TOL2", "IHID", "INEG"):
        p = next(p for p in c.points if p.id == name)
        assert p.kind == "free"
        # contra la oficial más el movimiento de la estación respecto de las fijas (del PPP del día)
        truth = OFFICIAL[name] + (PPP[name] - OFFICIAL[name]) - motion
        e, n, u = ecef_delta_to_enu(truth, xyz(p) - truth)
        assert abs(e) < 0.025 and abs(n) < 0.015 and abs(u) < 0.015
    ineg = next(p for p in c.points if p.id == "INEG")
    assert -0.72 < ineg.shift_enu.u < -0.65                  # el hundimiento, con INEG solo de comprobación
