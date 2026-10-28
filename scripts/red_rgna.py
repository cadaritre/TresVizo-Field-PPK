"""Red de prueba con datos públicos de la RGNA para el ajuste de red (`tresvizo_ppk/core/red.py`).

Simula una campaña estática de control como la haría el usuario: tres vértices «nuevos» (estaciones de la RGNA que
hacen de rover: UGTO, TOL2 e IHID) y tres de control (INEG, ISLP y UQRO, con su coordenada oficial ITRF2008 2010.0),
ocupados en cuatro sesiones de 4 h del 15-07-2026 (día 196) con cuatro receptores cada una. Cada línea base se
procesa con el motor de la app (`core.static.process_static`, órbitas y relojes finales de la ESA), cada sesión con
todas sus líneas de hasta 190 km: las triviales también, para que el ajuste las marque.

Guarda en `--salida` (por omisión `tests/core/data/red_rgna_2026196.json`) los vectores con su covarianza completa,
las coordenadas oficiales de las seis estaciones y la referencia independiente del día (PPP estático de 24 h de cada
estación, `scripts/validacion_rgna.py --referencia`), con su fuente. La prueba `tests/core/test_red_rgna.py` ajusta la
red con eso, sin RTKLIB.

Uso:
    python scripts/red_rgna.py --datos DIR_RGNA_20260715 --precisas DIR_SP3_CLK --referencia oos_ref_196.json \
        --trabajo DIR_TRABAJO [--salida tests/core/data/red_rgna_2026196.json]
    python scripts/red_rgna.py … --proyecto [--salida resumen.json]

Con `--proyecto` la misma campaña pasa por el motor del proyecto «network» (`core.api.process_network`): las tres
estaciones nuevas son receptores con su grabación del día y sus ocupaciones (los tramos de las sesiones), y el motor
decide qué líneas procesar (cada ocupación contra cada estación y entre ocupaciones simultáneas, de cualquier
longitud). Imprime y guarda el resumen contra las oficiales y el PPP del día.

DIR_RGNA_20260715 tiene una carpeta por estación con los ZIP horarios tal como los entrega el INEGI; DIR_SP3_CLK, el
SP3 y el CLK finales de la ESA del día, descomprimidos. Los intermedios de `--trabajo` se borran al terminar.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tresvizo_ppk.core.networks import find_station  # noqa: E402
from tresvizo_ppk.core.observations import prepare_observations  # noqa: E402
from tresvizo_ppk.core.static import process_static  # noqa: E402
from tresvizo_ppk.model import Ephemeris, ProcessingOptions  # noqa: E402

DAY = datetime(2026, 7, 15, tzinfo=timezone.utc)
CONTROL = ["INEG", "ISLP", "UQRO"]
NEW = ["UGTO", "TOL2", "IHID"]
# sesión → (hora de inicio UTC, líneas «desde→hasta»): cuatro receptores por sesión, todas las líneas de ≤ 190 km
SESSIONS = {
    "S1": (0, ["UQRO>UGTO", "ISLP>UGTO", "INEG>UGTO", "INEG>ISLP", "UQRO>ISLP"]),
    "S2": (6, ["UQRO>UGTO", "UQRO>TOL2", "UQRO>IHID", "TOL2>IHID"]),
    "S3": (12, ["INEG>ISLP", "UQRO>ISLP", "UQRO>TOL2"]),
    "S4": (18, ["UQRO>UGTO", "ISLP>UGTO", "UQRO>IHID", "UQRO>ISLP"]),
}
HOURS = 4.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--datos", type=Path, required=True)
    parser.add_argument("--precisas", type=Path, required=True)
    parser.add_argument("--referencia", type=Path, required=True)
    parser.add_argument("--trabajo", type=Path, required=True)
    parser.add_argument("--salida", type=Path, default=ROOT / "tests" / "core" / "data" / "red_rgna_2026196.json")
    parser.add_argument("--proyecto", action="store_true", help="por el motor del proyecto «network»")
    args = parser.parse_args()
    if args.proyecto:
        return project_run(args)
    ids = CONTROL + NEW
    stations = {sid: find_station(sid) for sid in ids}
    reference = json.loads(args.referencia.read_text(encoding="utf-8"))
    sp3 = sorted(args.precisas.glob("*ORB.SP3"))
    work = args.trabajo
    work.mkdir(parents=True, exist_ok=True)
    print("Preparando las observaciones del día…", flush=True)
    full = {sid: prepare_observations(sorted((args.datos / sid).glob("*.zip")), work / "dia" / sid) for sid in ids}
    options = ProcessingOptions(ephemeris=Ephemeris.PRECISE, precise_files=list(sp3))
    vectors = []
    t0 = time.monotonic()
    for session, (hour, lines) in SESSIONS.items():
        start = DAY + timedelta(hours=hour)
        end = start + timedelta(hours=HOURS)
        by_rover: dict[str, list[str]] = {}
        for text in lines:
            a, b = text.split(">")
            by_rover.setdefault(b, []).append(a)
        for rover_id, base_ids in by_rover.items():
            print(f"{session}: {rover_id} desde {', '.join(base_ids)}…", flush=True)
            folder = work / "sesiones" / session / rover_id
            rover = prepare_observations([full[rover_id].obs[0]] + full[rover_id].nav, folder / "rover",
                                         window=(start, end))
            bases = [(stations[b], prepare_observations([full[b].obs[0]] + full[b].nav, folder / b,
                                                        window=(start, end))) for b in base_ids]
            result = process_static(rover, bases, options, folder / "proceso", point_name=rover_id)
            for line in result.baselines:
                v = line.vector_ecef
                vectors.append({
                    "id": f"{session}:{line.base.id}-{rover_id}", "from": line.base.id, "to": rover_id,
                    "session": session, "start": line.start.isoformat(), "end": line.end.isoformat(),
                    "vector": [v.x, v.y, v.z], "covariance": [list(r) for r in line.covariance_ecef],
                    "solution": line.solution.value, "engine": line.engine, "ratio": line.ratio,
                    "km": round(line.length_m / 1000.0, 1)})
                print(f"  {line.base.id}–{rover_id}: {line.solution.value}, {line.length_m / 1000:.1f} km, "
                      f"{line.engine}", flush=True)
            shutil.rmtree(folder, ignore_errors=True)
    data = {
        "fuente": "INEGI, Archivos RINEX de la Red Geodésica Nacional Activa, 15-07-2026 (día 196), "
                  "https://www.inegi.org.mx/app/geo2/rgna/ (uso libre con atribución, Términos de Libre Uso de la "
                  "Información del INEGI). Órbitas y relojes finales de la ESA (ESA0OPSFIN_20261960000). Vectores "
                  "procesados con el motor de TresVizo Field PPK (scripts/red_rgna.py), sesiones de "
                  f"{HOURS:g} h.",
        "control": CONTROL, "nuevos": NEW,
        "oficial": {sid: [stations[sid].position.x, stations[sid].position.y, stations[sid].position.z]
                    for sid in ids},
        "marco": stations[CONTROL[0]].position.frame, "epoca": stations[CONTROL[0]].position.epoch,
        "ppp_24h": {sid: reference[sid] for sid in ids},
        "ppp_fuente": "PPP estático de 24 h de RTKLIB con órbitas y relojes finales de la ESA "
                      "(scripts/validacion_rgna.py --referencia), en el marco de las órbitas (IGS20) a la época "
                      "de los datos",
        "vectores": vectors,
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    print(f"Listo en {time.monotonic() - t0:.0f} s: {len(vectors)} líneas en {args.salida}", flush=True)
    return 0


def project_run(args) -> int:
    import numpy as np
    from tresvizo_ppk.core import api
    from tresvizo_ppk.core.geodesy import ecef_delta_to_enu
    from tresvizo_ppk.model import NetworkFixChoice, NetworkOccupation
    ids = CONTROL + NEW
    stations = {sid: find_station(sid) for sid in ids}
    reference = {k: np.array(v) for k, v in json.loads(args.referencia.read_text(encoding="utf-8")).items()}
    work = args.trabajo
    work.mkdir(parents=True, exist_ok=True)
    print("Preparando las observaciones del día…", flush=True)
    full = {sid: prepare_observations(sorted((args.datos / sid).glob("*.zip")), work / "dia" / sid) for sid in ids}
    occupations = []
    for session, (hour, lines) in SESSIONS.items():
        start = DAY + timedelta(hours=hour)
        present = {p for text in lines for p in text.split(">")}
        for sid in NEW:
            if sid in present:
                occupations.append(NetworkOccupation(sid, sid, start, start + timedelta(hours=HOURS),
                                                     source=f"sesión {session}"))
    options = ProcessingOptions(ephemeris=Ephemeris.PRECISE, precise_files=sorted(args.precisas.glob("*ORB.SP3")))
    t0 = time.monotonic()
    rovers = {sid: full[sid] for sid in NEW}
    bases = [(stations[s], full[s]) for s in CONTROL]
    summary = {}
    result = api.process_network(rovers, occupations, bases, options, work / "proceso",
                                 progress=lambda f, m: None)
    elapsed = time.monotonic() - t0
    for label, fix in (("todas", None), ("sin INEG", [NetworkFixChoice("INEG", "check"),
                                                      NetworkFixChoice("ISLP"), NetworkFixChoice("UQRO")])):
        r = api.readjust_network(result, fix=fix) if fix else result
        net = r.network
        fixed = [s for s in CONTROL if not fix or s != "INEG"]
        motion = np.mean([reference[s] - np.array(full_xyz(stations[s])) for s in fixed], axis=0)
        rows = {}
        for p in r.points:
            x = np.array([p.position.x, p.position.y, p.position.z])
            off = np.array(full_xyz(stations[p.name]))
            truth = off + (reference[p.name] - off) - motion
            rows[p.name] = {"menos_oficial_mm": [round(v * 1000, 1) for v in ecef_delta_to_enu(off, x - off)],
                            "menos_verdad_mm": [round(v * 1000, 1) for v in ecef_delta_to_enu(off, x - truth)],
                            "sigma_mm": [round(v * 1000, 1) for v in (p.sigma_enu.e, p.sigma_enu.n, p.sigma_enu.u)]}
        m, c = net.minimal, net.constrained
        summary[label] = {
            "lineas": len(r.lines), "fallidas": r.failed_lines,
            "solucion": {ln.id: (ln.baseline.solution.value, round(ln.baseline.length_m / 1000, 1),
                                 ln.baseline.engine) for ln in r.lines},
            "triviales": [v.id for v in net.vectors if v.trivial],
            "bucles": [(lp.points, round(lp.closure_h_m * 1000, 1), round(lp.closure_v_m * 1000, 1),
                        lp.status.level) for lp in net.loops],
            "minimo": {"chi2": round(m.chi2, 2), "limite": round(m.chi2_upper, 2), "gl": m.degrees_of_freedom,
                       "sigma0": round(m.sigma0, 3), "marcadas": m.flagged},
            "restringido": None if c is None else {"chi2": round(c.chi2, 2), "limite": round(c.chi2_upper, 2),
                                                   "gl": c.degrees_of_freedom, "sigma0": round(c.sigma0, 3),
                                                   "marcadas": c.flagged},
            "control_inconsistente": net.inconsistent_control, "puntos": rows}
    summary["segundos"] = round(elapsed, 1)
    text = json.dumps(summary, ensure_ascii=False, indent=1)
    print(text)
    args.salida.write_text(text, encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return 0


def full_xyz(station):
    return [station.position.x, station.position.y, station.position.z]


if __name__ == "__main__":
    raise SystemExit(main())
