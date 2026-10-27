"""Validación de punta a punta del motor con datos públicos de la RGNA (especificación §4; resultados en
docs/validacion.md).

Una estación de la RGNA hace de rover y otras de bases. Para cada sesión (de 1 h a 24 h, en varias horas del día) se
procesa con `process_static` como lo haría el usuario y se compara:

1. **Con la coordenada oficial** del rover (ITRF2008, época 2010.0, catálogo del INEGI): es lo que vería el usuario.
   Incluye el movimiento de las estaciones entre 2010 y la fecha de los datos (hundimientos, deformación), que no
   es error del proceso.
2. **Con la referencia independiente del día:** PPP estático de 24 h de RTKLIB (órbitas y relojes finales de la
   ESA) en cada estación. El vector rover−base de los PPP es la «verdad» de ese día (±1–2 cm); la diferencia entre
   ese vector y el de la línea base es el error del proceso.

Uso:
    python scripts/validacion_rgna.py --datos DIR_RGNA --precisas DIR_SP3_CLK --salida DIR
        [--rover UGTO] [--bases UQRO,ISLP,INEG] [--sesiones 1,2,4,24] [--fecha 2026-10-20]

DIR_RGNA tiene una carpeta por estación con los ZIP tal como los entrega el INEGI. DIR_SP3_CLK, el SP3 y el CLK
finales de la ESA del día (ESA0OPSFIN_…_ORB.SP3 y _CLK.CLK, descomprimidos).
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tresvizo_ppk.core import antex, rtklib, tools  # noqa: E402
from tresvizo_ppk.core.geodesy import ecef_delta_to_enu  # noqa: E402
from tresvizo_ppk.core.observations import prepare_observations  # noqa: E402
from tresvizo_ppk.core.networks import find_station  # noqa: E402
from tresvizo_ppk.core.static import process_static  # noqa: E402
from tresvizo_ppk.model import Ephemeris, ProcessingOptions  # noqa: E402


def enu_mm(origin, delta) -> list[float]:
    return [round(v * 1000.0, 1) for v in ecef_delta_to_enu(origin, delta)]


def ppp_truth(obs_path: Path, station, nav: list[Path], sp3: list[Path], clk: list[Path], work: Path) -> np.ndarray:
    work.mkdir(parents=True, exist_ok=True)
    atx = antex.ensure_antex(work)
    choice = antex.choose(station.antenna_type, station.radome)
    cfg = rtklib.ppp_static_config(antex=atx, antenna=rtklib.AntennaSetup(choice.rtklib_name,
                                                                          (0.0, 0.0, station.antenna_height_m)))
    conf = work / f"{station.id}_ppp.conf"
    cfg.write(conf)
    navdir = work / f"{station.id}_ppp_nav"
    navdir.mkdir(exist_ok=True)
    for k, n in enumerate(nav):
        shutil.copy2(n, navdir / f"{k:03d}_{n.name}")
    out = work / f"{station.id}_ppp.pos"
    args = [str(tools.find_tool("rnx2rtkp")), "-k", str(conf), "-o", str(out), str(obs_path), str(navdir / "*")]
    args += [str(p) for p in sp3 + clk]
    tools.run(args, cwd=work)
    epochs = rtklib.parse_pos(out)
    if not epochs:
        raise SystemExit(f"PPP sin solución en {station.id}")
    return epochs[-1].xyz


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--datos", type=Path, required=True)
    parser.add_argument("--precisas", type=Path, required=True)
    parser.add_argument("--salida", type=Path, required=True)
    parser.add_argument("--rover", default="UGTO")
    parser.add_argument("--bases", default="UQRO,ISLP,INEG")
    parser.add_argument("--sesiones", default="1,2,4,24")
    parser.add_argument("--fecha", default="2026-10-20")
    parser.add_argument("--efemerides", default="broadcast,precise")
    args = parser.parse_args()
    out = args.salida
    out.mkdir(parents=True, exist_ok=True)
    day = datetime.fromisoformat(args.fecha).replace(tzinfo=timezone.utc)
    ids = [args.rover] + args.bases.split(",")
    sp3 = sorted(args.precisas.glob("*ORB.SP3"))
    clk = sorted(args.precisas.glob("*CLK.CLK"))

    print("Preparando las observaciones del día…", flush=True)
    full = {}
    for sid in ids:
        full[sid] = prepare_observations(sorted((args.datos / sid).glob("*.zip")), out / "dia" / sid)
    stations = {sid: find_station(sid) for sid in ids}

    print("PPP de 24 h de cada estación (referencia del día)…", flush=True)
    truth = {}
    for sid in ids:
        truth[sid] = ppp_truth(full[sid].obs[0], stations[sid], full[sid].nav, sp3, clk, out / "ppp")
    rover_off = stations[args.rover].position
    R0 = np.array([rover_off.x, rover_off.y, rover_off.z])
    motion = {}
    for sid in ids:
        st = stations[sid].position
        off = np.array([st.x, st.y, st.z])
        motion[sid] = enu_mm(off, truth[sid] - off)

    results = {"fecha": args.fecha, "rover": args.rover, "bases": args.bases.split(","),
               "ppp_menos_oficial_mm": motion, "sesiones": []}
    for hours in [float(h) for h in args.sesiones.split(",")]:
        n_windows = 1 if hours >= 24 else min(4, int(24 // hours))
        starts = [day + timedelta(hours=k * (24 / n_windows)) for k in range(n_windows)] if hours < 24 else [day]
        for eph in args.efemerides.split(","):
            for start in starts:
                end = start + timedelta(hours=hours)
                label = f"{hours:g}h_{start:%H}_{eph}"
                print(f"Sesión {label}…", flush=True)
                work = out / "sesiones" / label
                t0 = time.monotonic()
                rover = prepare_observations([full[args.rover].obs[0]] + full[args.rover].nav, work / "rover",
                                             window=(start, end))
                bases = []
                for sid in args.bases.split(","):
                    bases.append((stations[sid], prepare_observations([full[sid].obs[0]] + full[sid].nav,
                                                                      work / sid, window=(start, end))))
                options = ProcessingOptions()
                if eph == "precise":
                    options.ephemeris = Ephemeris.PRECISE
                    options.precise_files = list(sp3)
                result = process_static(rover, bases, options, work / "proceso")
                elapsed = time.monotonic() - t0
                row = {"sesion": label, "horas": hours, "inicio": start.isoformat(), "efemerides": eph,
                       "segundos": round(elapsed, 1), "lineas": []}
                for b in result.baselines:
                    sid = b.base.id
                    base_off = stations[sid].position
                    B0 = np.array([base_off.x, base_off.y, base_off.z])
                    vec = np.array([b.vector_ecef.x, b.vector_ecef.y, b.vector_ecef.z])
                    true_vec = truth[args.rover] - truth[sid]
                    row["lineas"].append({
                        "base": sid, "km": round(b.length_m / 1000, 1), "solucion": b.solution.value,
                        "fijas": round(b.fix_rate, 2), "cociente": b.ratio, "estrategia": b.strategy,
                        "error_vector_mm": enu_mm(R0, vec - true_vec),
                        "rover_menos_oficial_mm": enu_mm(R0, (B0 + vec) - R0),
                        "sigma_mm": [round(v * 1000, 1) for v in _sigma_enu(R0, b.covariance_ecef)],
                    })
                p = result.position
                final = np.array([p.x, p.y, p.z])
                row["final_menos_oficial_mm"] = enu_mm(R0, final - R0)
                row["sigma_final_mm"] = [round(v * 1000, 1) for v in (result.sigma_enu.e, result.sigma_enu.n,
                                                                       result.sigma_enu.u)]
                if result.adjustment:
                    a = result.adjustment
                    row["ajuste"] = {"chi2": round(a.chi2, 2), "gl": a.degrees_of_freedom, "pasa": a.passed,
                                     "limite": round(a.chi2_upper, 2), "sigma0": round(a.sigma0, 2),
                                     "marcadas": a.flagged_baselines, "excluidas": a.excluded_baselines,
                                     "w": {k: [round(v.e, 1), round(v.n, 1), round(v.u, 1)]
                                           for k, v in a.standardized_residuals.items()}}
                row["avisos"] = result.warnings
                results["sesiones"].append(row)
                (out / "validacion.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
                print(json.dumps({k: row[k] for k in ("sesion", "final_menos_oficial_mm")}, ensure_ascii=False),
                      [(l["base"], l["solucion"], l["error_vector_mm"]) for l in row["lineas"]], flush=True)
    print(f"Listo: {out / 'validacion.json'}")
    return 0


def _sigma_enu(origin, cov):
    from tresvizo_ppk.core.geodesy import covariance_ecef_to_enu
    c = covariance_ecef_to_enu(origin, np.array(cov))
    return [math.sqrt(max(0.0, float(c[i, i]))) for i in range(3)]


if __name__ == "__main__":
    raise SystemExit(main())
