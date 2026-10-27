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
        [--rover UGTO] [--bases UQRO,ISLP,INEG] [--sesiones 1,2,4,24] [--ventanas 4] [--fecha 2026-10-20]
    python scripts/validacion_rgna.py --resumen DIR/validacion.json [otro.json …]

Verificación fuera de muestra (docs/validacion.md, «Verificación fuera de muestra»): con las opciones de abajo se
corre otro día y se ataca el motor sin cambiarlo; sin ellas el resultado es el mismo de antes.
    --datos DIR_DIA DIR_DIA_SIGUIENTE   los ZIP de varios días (sesiones que cruzan la medianoche); la referencia
                                        PPP usa solo el primero
    --referencia ppp.json               guarda y reutiliza la referencia PPP de 24 h de cada estación
    --inicios 2026-07-15T23:30,…        inicios de sesión explícitos (en vez de --ventanas)
    --mascara 25 --sistemas GPS,Galileo máscara de elevación y constelaciones de la app
    --aprox-desplazada E,N,U | ninguna  mueve la posición aproximada del rover (m) o la quita (puntual de RTKLIB):
                                        el resultado no debe depender de ella
    --hueco 20,30                       quita al rover las épocas entre esos minutos desde el inicio de la sesión
    --saltos G05:4:3@30,E11:4:3@30      salto de ciclo sin indicador LLI en el rover: n1 ciclos en las fases de la
                                        banda 1 y n2 en las demás, desde ese minuto
    --datos-rover DIR                   los ZIP del rover de otra carpeta (p. ej. RINEX 2.11 contra bases en 3.04)
    --seguir                            una sesión que falla queda en el JSON con su error y se sigue
    --limpiar                           borra los intermedios de cada sesión en cuanto guarda su resultado (cada
                                        sesión copia los archivos del día: decenas de MB por estación)

Cada línea base guarda su motor (RTKLIB o el procesador propio de líneas largas), su solución, la resolución de
ambigüedades (combinaciones fijadas, razón, tasa de éxito o por qué quedó flotante), el error del vector frente a
la referencia del día y su σ. `--resumen` imprime las tablas de docs/validacion.md: por línea, duración y
efemérides, cuántas fijó, el RMS y el máximo del error y los **fijos erróneos** (fija con algún componente del
error mayor que 3 veces su σ).

DIR_RGNA tiene una carpeta por estación con los ZIP tal como los entrega el INEGI. DIR_SP3_CLK, el SP3 y el CLK
finales de la ESA del día (ESA0OPSFIN_…_ORB.SP3 y _CLK.CLK, descomprimidos).
"""
from __future__ import annotations

import argparse
import json
import math
import re
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
    parser.add_argument("--datos", type=Path, nargs="+")
    parser.add_argument("--datos-rover", type=Path, nargs="+")
    parser.add_argument("--referencia", type=Path, help="JSON con la referencia PPP de cada estación (se completa)")
    parser.add_argument("--inicios", default="", help="inicios de sesión ISO separados por comas")
    parser.add_argument("--mascara", type=float, default=None)
    parser.add_argument("--sistemas", default="", help="constelaciones, p. ej. GPS,Galileo")
    parser.add_argument("--aprox-desplazada", default="", help="E,N,U en metros, o «ninguna»")
    parser.add_argument("--hueco", default="", help="minutos desde el inicio: desde,hasta")
    parser.add_argument("--saltos", default="", help="SAT:n1:n2@minuto, separados por comas")
    parser.add_argument("--seguir", action="store_true",
                        help="si una sesión falla, guarda el error en el resultado y sigue con la siguiente")
    parser.add_argument("--limpiar", action="store_true",
                        help="borra los intermedios de cada sesión al guardar su resultado (y los del día al final)")
    parser.add_argument("--precisas", type=Path)
    parser.add_argument("--salida", type=Path)
    parser.add_argument("--ventanas", type=int, default=4, help="sesiones por duración repartidas en el día")
    parser.add_argument("--resumen", type=Path, nargs="+", help="imprime las tablas de uno o varios validacion.json")
    parser.add_argument("--rover", default="UGTO")
    parser.add_argument("--bases", default="UQRO,ISLP,INEG")
    parser.add_argument("--sesiones", default="1,2,4,24")
    parser.add_argument("--fecha", default="2026-10-20")
    parser.add_argument("--efemerides", default="broadcast,precise")
    args = parser.parse_args()
    if args.resumen:
        return summary(args.resumen)
    if not (args.datos and args.precisas and args.salida):
        parser.error("hacen falta --datos, --precisas y --salida (o --resumen)")
    out = args.salida
    out.mkdir(parents=True, exist_ok=True)
    day = datetime.fromisoformat(args.fecha).replace(tzinfo=timezone.utc)
    ids = [args.rover] + args.bases.split(",")
    sp3 = sorted(args.precisas.glob("*ORB.SP3"))
    clk = sorted(args.precisas.glob("*CLK.CLK"))

    print("Preparando las observaciones del día…", flush=True)
    full = {}
    for sid in ids:
        dirs = args.datos_rover if (sid == args.rover and args.datos_rover) else args.datos
        full[sid] = prepare_observations(sorted(p for d in dirs for p in (d / sid).glob("*.zip")), out / "dia" / sid)
    stations = {sid: find_station(sid) for sid in ids}

    print("PPP de 24 h de cada estación (referencia del día)…", flush=True)
    truth = {}
    cache = json.loads(args.referencia.read_text(encoding="utf-8")) if args.referencia and \
        args.referencia.is_file() else {}
    for sid in ids:
        if sid in cache:
            truth[sid] = np.array(cache[sid])
            continue
        day_obs = full[sid] if len(args.datos) == 1 else prepare_observations(
            sorted((args.datos[0] / sid).glob("*.zip")), out / "dia_ppp" / sid)
        truth[sid] = ppp_truth(day_obs.obs[0], stations[sid], day_obs.nav, sp3, clk, out / "ppp")
        if args.referencia:
            cache[sid] = [float(v) for v in truth[sid]]
            args.referencia.parent.mkdir(parents=True, exist_ok=True)
            args.referencia.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    rover_off = stations[args.rover].position
    R0 = np.array([rover_off.x, rover_off.y, rover_off.z])
    motion = {}
    for sid in ids:
        st = stations[sid].position
        off = np.array([st.x, st.y, st.z])
        motion[sid] = enu_mm(off, truth[sid] - off)

    results = {"fecha": args.fecha, "rover": args.rover, "bases": args.bases.split(","),
               "ppp_menos_oficial_mm": motion, "sesiones": []}
    attack = {k: v for k, v in (("mascara", args.mascara), ("sistemas", args.sistemas),
                                ("aprox_desplazada", args.aprox_desplazada), ("hueco", args.hueco),
                                ("saltos", args.saltos), ("inicios", args.inicios)) if v not in (None, "")}
    if attack:
        results["prueba"] = attack
    for hours in [float(h) for h in args.sesiones.split(",")]:
        n_windows = 1 if hours >= 24 else min(args.ventanas, int(24 // hours))
        starts = [day + timedelta(hours=k * (24 / n_windows)) for k in range(n_windows)] if hours < 24 else [day]
        if args.inicios:
            starts = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in args.inicios.split(",")]
        for eph in args.efemerides.split(","):
            for start in starts:
                end = start + timedelta(hours=hours)
                label = f"{hours:g}h_{start:%H}_{eph}" if not args.inicios else \
                    f"{hours:g}h_{start:%d%H%M}_{eph}"
                print(f"Sesión {label}…", flush=True)
                work = out / "sesiones" / label
                t0 = time.monotonic()
                try:
                    rover = prepare_observations([full[args.rover].obs[0]] + full[args.rover].nav, work / "rover",
                                                 window=(start, end))
                    if args.hueco or args.saltos:
                        edited = _edit_rinex(rover.obs[0], work / "rover_editado" / rover.obs[0].name, start,
                                             args.hueco, args.saltos)
                        rover = prepare_observations([edited] + full[args.rover].nav, work / "rover2")
                    if args.aprox_desplazada:
                        rover.approx_position = _shift_approx(rover.approx_position, args.aprox_desplazada)
                    bases = []
                    for sid in args.bases.split(","):
                        bases.append((stations[sid], prepare_observations([full[sid].obs[0]] + full[sid].nav,
                                                                          work / sid, window=(start, end))))
                    options = ProcessingOptions()
                    if args.mascara is not None:
                        options.elevation_mask_deg = args.mascara
                    if args.sistemas:
                        options.constellations = tuple(args.sistemas.split(","))
                    if eph == "precise":
                        options.ephemeris = Ephemeris.PRECISE
                        options.precise_files = list(sp3)
                    result = process_static(rover, bases, options, work / "proceso")
                except Exception as error:  # noqa: BLE001 (--seguir: el fallo queda en el resultado)
                    if not args.seguir:
                        raise
                    results["sesiones"].append({"sesion": label, "horas": hours, "inicio": start.isoformat(),
                                                "efemerides": eph, "lineas": [],
                                                "segundos": round(time.monotonic() - t0, 1),
                                                "error": f"{type(error).__name__}: {error}"})
                    (out / "validacion.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
                    print(f"Sesión {label}: FALLÓ ({type(error).__name__}: {error})", flush=True)
                    if args.limpiar:
                        shutil.rmtree(work, ignore_errors=True)
                    continue
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
                        "motor": b.engine,
                    })
                    if b.ambiguity is not None:
                        a = b.ambiguity
                        row["lineas"][-1]["ambiguedades"] = {
                            "fija": a.fixed, "motivo": a.reason, "dobles_diferencias": a.double_differences,
                            "ancho_fijas": a.widelane_fixed, "candidatas": a.narrowlane_candidates,
                            "fijadas": a.narrowlane_fixed, "razon": a.ratio, "exito": a.success_rate}
                        fp = a.float_position
                        row["lineas"][-1]["error_flotante_mm"] = enu_mm(
                            R0, (np.array([fp.x, fp.y, fp.z]) - B0) - true_vec)
                        fs = a.float_sigma_enu
                        row["lineas"][-1]["sigma_flotante_mm"] = [round(v * 1000, 1) for v in (fs.e, fs.n, fs.u)]
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
                if args.limpiar:
                    shutil.rmtree(work, ignore_errors=True)
    if args.limpiar:
        for folder in ("dia", "dia_ppp", "sesiones"):
            shutil.rmtree(out / folder, ignore_errors=True)
    print(f"Listo: {out / 'validacion.json'}")
    return 0


def summary(paths: list[Path]) -> int:
    """Tablas de docs/validacion.md a partir de los validacion.json."""
    rows = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for ses in data["sesiones"]:
            for line in ses["lineas"]:
                rows.append({**line, "rover": data["rover"], "horas": ses["horas"], "efemerides": ses["efemerides"]})
    keys = sorted({(r["rover"], r["base"], r["km"] // 1) for r in rows}, key=lambda k: (k[2], k[1]))
    print("| Línea | km | Sesión | Efemérides | Fijas | RMS E | RMS N | RMS U | Máx. horiz. | Máx. vert. | "
          "Fijos erróneos |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for rover, base, _ in keys:
        sel = [r for r in rows if r["rover"] == rover and r["base"] == base]
        km = sel[0]["km"]
        for hours in sorted({r["horas"] for r in sel}):
            for eph in ("broadcast", "precise"):
                group = [r for r in sel if r["horas"] == hours and r["efemerides"] == eph]
                if not group:
                    continue
                err = np.array([r["error_vector_mm"] for r in group])
                sig = np.array([r["sigma_mm"] for r in group])
                fixed = [r["solucion"] == "fixed" for r in group]
                wrong = sum(1 for r, e, s_ in zip(group, err, sig) if r["solucion"] == "fixed" and
                            np.any(np.abs(e) > 3 * s_))
                rms = np.sqrt(np.mean(err ** 2, axis=0))
                mh = float(np.max(np.hypot(err[:, 0], err[:, 1])))
                mv = float(np.max(np.abs(err[:, 2])))
                print(f"| {rover}–{base} | {km:.1f} | {hours:g} h | {'precisas' if eph == 'precise' else 'difundidas'}"
                      f" | {sum(fixed)}/{len(group)} | {rms[0]:.1f} | {rms[1]:.1f} | {rms[2]:.1f} | {mh:.1f} | "
                      f"{mv:.1f} | {wrong} |")
    return 0


def _shift_approx(approx, spec: str):
    """Posición aproximada del rover movida E, N, U metros (o None con «ninguna»): prueba de fugas."""
    from tresvizo_ppk.model import Ecef
    if spec == "ninguna":
        return None
    e, n, u = (float(v) for v in spec.split(","))
    xyz = np.array([approx.x, approx.y, approx.z])
    lat = math.atan2(xyz[2], math.hypot(xyz[0], xyz[1]))
    lon = math.atan2(xyz[1], xyz[0])
    sl, cl, so, co = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
    d = e * np.array([-so, co, 0.0]) + n * np.array([-sl * co, -sl * so, cl]) + u * np.array([cl * co, cl * so, sl])
    p = xyz + d
    return Ecef(float(p[0]), float(p[1]), float(p[2]), approx.frame, approx.epoch)


def _edit_rinex(src: Path, dst: Path, start: datetime, gap: str, slips: str) -> Path:
    """Copia del RINEX 3 del rover con un hueco (minutos desde el inicio) y saltos de ciclo sin LLI.

    `slips`: «G05:4:3@30»: desde el minuto 30, n1 = 4 ciclos en las fases de la banda 1 y n2 = 3 en las demás (sin
    marcar el indicador de pérdida de enganche). Un salto 4/3 cambia la Melbourne-Wübbena en un ciclo y la libre de
    geometría en 2.9 cm en GPS y 0.3 cm en Galileo E1/E5a: es el caso más difícil de ver."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    t0 = start.replace(tzinfo=None)
    g0 = g1 = None
    if gap:
        a, b = (float(v) for v in gap.split(","))
        g0, g1 = t0 + timedelta(minutes=a), t0 + timedelta(minutes=b)
    plan = {}
    for item in filter(None, slips.split(",")):
        m = re.fullmatch(r"([A-Z]\d\d):(-?\d+):(-?\d+)@([\d.]+)", item.strip())
        if not m:
            raise SystemExit(f"--saltos: no entiendo «{item}»")
        plan[m[1]] = (int(m[2]), int(m[3]), t0 + timedelta(minutes=float(m[4])))
    lines = src.read_text(encoding="ascii", errors="replace").splitlines()
    end = next(i for i, l in enumerate(lines) if l[60:].strip() == "END OF HEADER")
    if not lines[0][:9].strip().startswith("3"):
        raise SystemExit("--hueco y --saltos piden RINEX 3")
    types: dict[str, list[str]] = {}
    cur = None
    for l in lines[:end]:
        if l[60:].strip() == "SYS / # / OBS TYPES":
            if l[0] != " ":
                cur = l[0]
                types[cur] = []
            types[cur] += l[7:60].split()
    out = lines[:end + 1]
    i = end + 1
    while i < len(lines):
        head = lines[i]
        n = int(head[32:35])
        when = datetime(int(head[2:6]), int(head[7:9]), int(head[10:12]), int(head[13:15]), int(head[16:18])) + \
            timedelta(seconds=float(head[18:29]))
        block = lines[i + 1:i + 1 + n]
        i += 1 + n
        if g0 is not None and g0 <= when < g1:
            continue
        new = []
        for rec in block:
            sat = rec[:3].replace(" ", "0")
            if sat in plan and when >= plan[sat][2]:
                n1, n2 = plan[sat][:2]
                rec = rec.ljust(3 + 16 * len(types[sat[0]]))
                fields = [rec[3 + 16 * k:3 + 16 * (k + 1)] for k in range(len(types[sat[0]]))]
                for k, t in enumerate(types[sat[0]]):
                    if t[0] == "L" and fields[k][:14].strip():
                        v = float(fields[k][:14]) + (n1 if t[1] == "1" else n2)
                        fields[k] = f"{v:14.3f}" + fields[k][14:]
                rec = (rec[:3] + "".join(fields)).rstrip()
            new.append(rec)
        out.append(head)
        out += new
    dst.write_text("\n".join(out) + "\n", encoding="ascii")
    return dst


def _sigma_enu(origin, cov):
    from tresvizo_ppk.core.geodesy import covariance_ecef_to_enu
    c = covariance_ecef_to_enu(origin, np.array(cov))
    return [math.sqrt(max(0.0, float(c[i, i]))) for i in range(3)]


if __name__ == "__main__":
    raise SystemExit(main())
