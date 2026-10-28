"""Un resultado real de estático de varios puntos sin RTKLIB: los vectores procesados de la red de la RGNA del
15-07-2026 (`tests/core/data/red_rgna_2026196.json`, ver `scripts/red_rgna.py`) como líneas de la red, ajustadas con
`core.static_network.readjust`. Lo usan las pruebas del informe, la exportación, el proyecto y la interfaz."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from tresvizo_ppk.core import static_network
from tresvizo_ppk.core.geodesy import azimuth_zenith, ecef_delta_to_enu
from tresvizo_ppk.core.networks import find_station
from tresvizo_ppk.model import (BaselineSolution, DataQuality, Ecef, Enu, Ephemeris, NetworkFixChoice, NetworkLine,
                                NetworkOccupation, NetworkStaticResult, ObservationSet, Solution)

DATA = json.loads((Path(__file__).resolve().parent / "core" / "data" / "red_rgna_2026196.json").read_text(
    encoding="utf-8"))


def network_result(fix: list[NetworkFixChoice] | None = None) -> NetworkStaticResult:
    stations = {sid: find_station(sid) for sid in DATA["control"] + DATA["nuevos"]}
    lines, occupations, seen = [], [], set()
    for v in DATA["vectores"]:
        start, end = datetime.fromisoformat(v["start"]), datetime.fromisoformat(v["end"])
        base = stations[v["from"]]
        b0 = (base.position.x, base.position.y, base.position.z)
        rover = Ecef(*(b + d for b, d in zip(b0, v["vector"])), base.position.frame, base.position.epoch)
        az, zen = azimuth_zenith(b0, v["vector"])
        solution = BaselineSolution(
            base=base, rover_marker=v["to"], start=start, end=end, epochs_used=960, satellites=["G01", "E11"],
            solution=Solution(v["solution"]), ratio=v["ratio"], rms_m=0.004, vector_ecef=Ecef(*v["vector"]),
            vector_enu=Enu(*ecef_delta_to_enu(b0, v["vector"])), length_m=math.dist((0, 0, 0), v["vector"]),
            azimuth_deg=az, zenith_deg=zen, covariance_ecef=tuple(tuple(r) for r in v["covariance"]),
            rover_position=rover, ephemeris=Ephemeris.PRECISE, engine=v["engine"], fix_rate=1.0, pdop_max=1.6)
        lines.append(NetworkLine(v["id"], v["from"], v["to"], "base" if v["from"] in DATA["control"] else "rover",
                                 solution))
        for name in (v["from"], v["to"]):
            if name in DATA["nuevos"] and (name, v["session"]) not in seen:
                seen.add((name, v["session"]))
                occupations.append(NetworkOccupation(name, name, start, end, antenna_height_m=0.0,
                                                     source=f"sesión {v['session']}"))
    rovers = {name: ObservationSet(obs=[], marker=name, receiver="TRIMBLE ALLOY", antenna_type="TRM115000.00",
                                   start=min(o.start for o in occupations if o.receiver == name),
                                   end=max(o.end for o in occupations if o.receiver == name))
              for name in DATA["nuevos"]}
    result = NetworkStaticResult(occupations=occupations, rovers=rovers,
                                 bases=[stations[s] for s in DATA["control"]], lines=lines, network=None,
                                 rover_quality={k: DataQuality(epochs=960, expected_epochs=960) for k in rovers},
                                 log=["Motor: prueba."], ephemeris=Ephemeris.PRECISE,
                                 precise_files=["ESA0OPSFIN_20261960000_01D_05M_ORB.SP3"])
    return static_network.readjust(result, fix=fix)


def without_ineg() -> list[NetworkFixChoice]:
    return [NetworkFixChoice("INEG", "check"), NetworkFixChoice("ISLP"), NetworkFixChoice("UQRO")]
