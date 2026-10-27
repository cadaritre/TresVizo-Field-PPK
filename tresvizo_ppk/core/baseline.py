"""Una línea base estática con RTKLIB: estrategia según la longitud, proceso combinado y `BaselineSolution`.

Estrategia (`ProcessingOptions.ionosphere`), ajustada con las pruebas sobre la RGNA (docs/validacion.md):
- «auto»:
  - menos de 20 km: ionosfera del mensaje de navegación, troposfera Saastamoinen, ambigüedades enteras;
  - de 20 a 60 km: lo mismo con el retardo cenital troposférico estimado y, además, la solución libre de
    ionosfera (flotante); se queda la fija solo si fijó al menos la mitad de las épocas y coincide con la libre de
    ionosfera dentro de su tolerancia; si no, la libre de ionosfera;
  - 60 km o más: libre de ionosfera (combinación L1/L2), ambigüedades flotantes, troposfera estimada. Es lo que
    resuelve bien RTKLIB en líneas largas; con sesiones de horas da centímetros.
- «iono-free»: siempre libre de ionosfera (flotante). «broadcast»: siempre con la ionosfera del mensaje y
  ambigüedades enteras. «estimated»: ionosfera estimada por satélite (opción de RTKLIB; en las pruebas no fijó).
- Ambigüedades enteras solo de GPS, Galileo y QZSS: las de GLONASS (sesgos entre canales) y BeiDou impidieron
  fijar en las pruebas (ver docs/validacion.md); esos satélites entran en la solución con ambigüedad flotante.

Coordenada final de la línea: mediana de las épocas de la solución combinada adelante/atrás con la mejor calidad
(fijas; si no hay, flotantes). Covarianza: la formal de RTKLIB más un modelo de error realista (ver `ERROR_MODEL`),
porque la formal de un estático largo es de décimas de milímetro y no incluye órbitas, troposfera ni
multitrayectoria; el ajuste de red pone a prueba ese modelo con su chi².
"""
from __future__ import annotations

import math
import statistics
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from ..errors import UserError
from ..model import BaselineSolution, Ecef, Enu, Ephemeris, ObservationSet, ProcessingOptions, Solution, Station
from . import antex, rtklib, tools
from .geodesy import (azimuth_zenith, covariance_ecef_to_enu, covariance_enu_to_ecef, ecef_delta_to_enu,
                      gps_to_utc, utc_to_gps)

SHORT_BASELINE_M = 20_000.0      # «auto»: hasta aquí, ionosfera del mensaje y ambigüedades enteras
LONG_BASELINE_M = 60_000.0       # «auto»: desde aquí, solo libre de ionosfera (flotante)
MIN_FIX_RATE_AUTO = 0.5          # «auto», línea media: mínimo de épocas fijas para quedarse con la fija
AGREEMENT_H_M = 0.03             # tolerancia fija ↔ libre de ionosfera (o 3σ de la libre, lo que sea mayor)
AGREEMENT_V_M = 0.06

# Modelo de error a priori de una línea base estática, 1σ por componente (E, N y U):
#     σ = a + b·L + c/√T        (L en metros, T = duración de la sesión en horas)
# Calibrado con la validación sobre la RGNA (docs/validacion.md): errores del vector frente a la referencia PPP del
# día, líneas de 30 a 141 km y sesiones de 1 a 24 h; el modelo queda del orden del error RMS observado o un poco
# por encima. SINGLE y NONE solo sirven para no dividir entre cero: esas líneas no deben entrar a un ajuste.
ERROR_MODEL = {
    Solution.FIXED: {"h": (0.003, 0.2e-6, 0.005), "v": (0.005, 0.4e-6, 0.010)},
    Solution.FLOAT: {"h": (0.003, 0.02e-6, 0.018), "v": (0.005, 0.05e-6, 0.030)},
    Solution.SINGLE: {"h": (1.5, 0.0, 0.0), "v": (3.0, 0.0, 0.0)},
    Solution.NONE: {"h": (10.0, 0.0, 0.0), "v": (10.0, 0.0, 0.0)},
}

Q_TO_SOLUTION = {rtklib.Q_FIX: Solution.FIXED, rtklib.Q_FLOAT: Solution.FLOAT, rtklib.Q_DGPS: Solution.SINGLE,
                 rtklib.Q_SINGLE: Solution.SINGLE, rtklib.Q_SBAS: Solution.SINGLE, rtklib.Q_PPP: Solution.FLOAT}


@dataclass
class Strategy:
    key: str                      # «short», «estimated», «iono-free»
    ionoopt: str
    tropopt: str
    ambiguity: str
    text: str


def strategies(length_m: float, options: ProcessingOptions, mode: str = "static") -> list[Strategy]:
    """Estrategias a correr, en orden, para una línea de esta longitud."""
    freq_text = {1: "L1", 2: "L1+L2", 3: "L1+L2+L5"}.get(options.frequencies, "L1+L2")
    ar = options.ambiguity_mode if options.ambiguity_mode in ("continuous", "fix-and-hold", "instantaneous",
                                                              "off") else "continuous"
    short = Strategy("corta", "brdc", "saas", ar, f"{freq_text}, ionosfera del mensaje de navegación, "
                                                  "troposfera Saastamoinen, ambigüedades enteras")
    medium = Strategy("media", "brdc", "est-ztd", ar, f"{freq_text}, ionosfera del mensaje de navegación, "
                                                      "retardo cenital troposférico estimado, ambigüedades enteras")
    estimated = Strategy("ionosfera-estimada", "est-stec", "est-ztd", ar,
                         f"{freq_text}, ionosfera estimada por satélite, retardo cenital troposférico estimado, "
                         "ambigüedades enteras")
    iono_free = Strategy("libre-de-ionosfera", "dual-freq", "est-ztd", "off",
                         "combinación libre de ionosfera L1/L2, retardo cenital troposférico estimado, "
                         "ambigüedades flotantes")
    choice = (options.ionosphere or "auto").lower()
    if options.frequencies < 2:
        return [short]
    if choice in ("iono-free", "libre", "dual-freq"):
        return [iono_free]
    if choice in ("estimated", "est-stec"):
        return [estimated]
    if choice in ("broadcast", "brdc", "off"):
        return [short]
    if length_m < SHORT_BASELINE_M:
        return [short]
    if mode != "static":
        return [medium]
    if length_m < LONG_BASELINE_M:
        return [medium, iono_free]
    return [iono_free]


def glonass_ar(rover: ObservationSet, base: ObservationSet) -> str:
    """Ambigüedades enteras de GLONASS: apagadas. Los sesgos entre canales de frecuencia dependen del receptor y
    en las pruebas impidieron fijar incluso entre receptores iguales (docs/validacion.md)."""
    return "off"


def approx_position(obs: ObservationSet, nav: list[Path], workdir: Path, cancel=None,
                    log: list[str] | None = None) -> np.ndarray:
    """Posición aproximada (pocos metros): la del RINEX si la trae; si no, posicionamiento puntual de RTKLIB."""
    if obs.approx_position is not None:
        p = obs.approx_position
        return np.array([p.x, p.y, p.z])
    exe = tools.find_tool("rnx2rtkp")
    out = workdir / f"{obs.obs[0].stem}_puntual.pos"
    args = [str(exe), "-p", "0", "-e", "-o", str(out)]
    if obs.start:
        s = utc_to_gps(obs.start)
        args += ["-ts", s.strftime("%Y/%m/%d"), s.strftime("%H:%M:%S"),
                 "-te", (s + timedelta(minutes=10)).strftime("%Y/%m/%d"),
                 (s + timedelta(minutes=10)).strftime("%H:%M:%S")]
    args += [str(obs.obs[0])] + [str(n) for n in nav]
    tools.run(args, cwd=workdir, cancel=cancel, log=log)
    rows = _read_single(out)
    if not rows:
        raise UserError(f"No se pudo calcular una posición aproximada de {obs.marker}: revisa el archivo de "
                        "navegación.")
    return np.median(np.array(rows), axis=0)


def _read_single(path: Path) -> list[tuple[float, float, float]]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith("%") or not line.strip():
            continue
        parts = line.split()
        try:
            rows.append((float(parts[2]), float(parts[3]), float(parts[4])))
        except (IndexError, ValueError):
            continue
    return rows


@dataclass
class RunResult:
    strategy: Strategy
    epochs: list[rtklib.PosEpoch]
    pos_file: Path
    stat_file: Path
    config_file: Path

    @property
    def fix_rate(self) -> float:
        solved = [e for e in self.epochs if e.q in (rtklib.Q_FIX, rtklib.Q_FLOAT)]
        return (sum(1 for e in solved if e.q == rtklib.Q_FIX) / len(solved)) if solved else 0.0


def process_baseline(rover: ObservationSet, station: Station, base: ObservationSet, options: ProcessingOptions,
                     workdir: Path, *, mode: str = "static", rover_antenna_delta_u: float | None = None,
                     progress: tools.Progress | None = None, cancel: threading.Event | None = None,
                     log: list[str] | None = None, label: str = "") -> tuple[BaselineSolution, RunResult]:
    """Procesa la línea base estática rover–base y devuelve su `BaselineSolution` y el detalle del proceso."""
    log = log if log is not None else []
    workdir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    notes: list[str] = []
    step = progress or (lambda f, m: None)
    if options.ephemeris == Ephemeris.PRECISE and not options.precise_files:
        raise UserError("Se pidieron efemérides precisas pero no hay archivos SP3: bájalos primero.")
    nav = list(dict.fromkeys(list(rover.nav) + list(base.nav)))
    if not nav and options.ephemeris != Ephemeris.PRECISE:
        raise UserError("No hay archivo de navegación (ni del rover ni de la base). Agrega el .nav/.YYn de la base.")
    if not (rover.start and rover.end and base.start and base.end):
        raise UserError("No se conocen los horarios de las observaciones.")
    start = max(rover.start, base.start)
    end = min(rover.end, base.end)
    if (end - start).total_seconds() <= 0:
        raise UserError(f"La base {station.id} no tiene datos en el horario del rover "
                        f"({rover.start:%Y-%m-%d %H:%M}–{rover.end:%H:%M} UTC).")
    antex_path = antex.ensure_antex(workdir)
    base_xyz = np.array([station.position.x, station.position.y, station.position.z])

    # Antenas y alturas
    rover_choice = antex.choose(rover.antenna_type, rover.radome or "NONE", "Rover")
    notes.append(rover_choice.note)
    if rover_choice.model is None and rover.receiver_model:
        warnings.append("PCO de la antena del rover sin medir: se procesa sin modelo de centro de fase.")
    base_type = base.antenna_type or station.antenna_type
    base_radome = (base.radome or station.radome or "NONE") if base.antenna_type else (station.radome or "NONE")
    if base.antenna_type and station.antenna_type and \
            base.antenna_type.replace(" ", "").upper() != station.antenna_type.replace(" ", "").upper():
        warnings.append(f"La antena del RINEX de {station.id} ({base.antenna_type}) no es la del catálogo "
                        f"({station.antenna_type}): se usa la del RINEX.")
    base_choice = antex.choose(base_type, base_radome, f"Base {station.id}")
    notes.append(base_choice.note)
    base_h = base.antenna_height_m if base.antenna_height_m else station.antenna_height_m
    if base.antenna_height_m and station.antenna_height_m and abs(base.antenna_height_m -
                                                                  station.antenna_height_m) > 0.002:
        warnings.append(f"Altura de antena de {station.id}: el RINEX dice {base.antenna_height_m:.4f} m y el "
                        f"catálogo {station.antenna_height_m:.4f} m; se usa la del RINEX (la de esos datos).")
    rover_h = rover.antenna_height_m if rover_antenna_delta_u is None else rover_antenna_delta_u
    rover_setup = rtklib.AntennaSetup(rover_choice.rtklib_name, (0.0, 0.0, rover_h))
    base_setup = rtklib.AntennaSetup(base_choice.rtklib_name, (0.0, 0.0, base_h))

    # Longitud aproximada → estrategia
    approx = approx_position(rover, nav, workdir, cancel=cancel, log=log)
    length = float(np.linalg.norm(approx - base_xyz))
    plan = strategies(length, options, mode)
    interval = options.interval_s or max(rover.interval_s or 1.0, base.interval_s or 1.0) if mode == "static" \
        else options.interval_s
    start_g = utc_to_gps(start).replace(tzinfo=None)
    end_g = utc_to_gps(end).replace(tzinfo=None)
    precise = options.ephemeris == Ephemeris.PRECISE
    runs: list[RunResult] = []
    for k, strategy in enumerate(plan):
        tools.check(cancel)
        name = f"{label or station.id}_{strategy.key}"
        cfg = rtklib.base_config(
            mode=mode, options=options, frequencies=options.frequencies, ionosphere=strategy.ionoopt,
            troposphere=strategy.tropopt, navsys=rtklib.navsys_mask(options.constellations),
            ambiguity=strategy.ambiguity if mode == "static" else _kinematic_ar(options),
            glonass_ar=glonass_ar(rover, base), precise=precise, antex=antex_path, rover=rover_setup,
            base=base_setup, base_xyz=tuple(base_xyz), interval_s=base.interval_s,
            elevation_mask=options.elevation_mask_deg, min_ratio=options.min_ratio, combined=options.combined,
            kinematic_dynamics=False)
        conf = workdir / f"{name}.conf"
        cfg.write(conf)
        pos = workdir / f"{name}.pos"
        log.append(f"Línea base {station.id}: {strategy.text} ({length / 1000:.1f} km).")
        frac0, frac1 = k / len(plan), (k + 1) / len(plan)
        rtklib.run_rnx2rtkp(conf, pos, rover.obs[0], base.obs[0], nav, [Path(p) for p in options.precise_files],
                            workdir=workdir, start_gpst=start_g, end_gpst=end_g, interval_s=interval,
                            progress=lambda f, m: step(frac0 + (frac1 - frac0) * f, m), cancel=cancel, log=log,
                            label=f"{station.id}: ", combined=options.combined)
        epochs = rtklib.parse_pos(pos)
        run = RunResult(strategy, epochs, pos, Path(str(pos) + ".stat"), conf)
        runs.append(run)
        log.append(f"  épocas: {len(epochs)}, fijas: {100 * run.fix_rate:.0f} %")
    chosen = runs[0]
    if len(runs) == 2:
        fixed_run, float_run = runs
        agree, detail = _agreement(fixed_run, float_run, base_xyz)
        log.append(f"  fija frente a libre de ionosfera: {detail}")
        if fixed_run.fix_rate >= MIN_FIX_RATE_AUTO and agree:
            chosen = fixed_run
        else:
            chosen = float_run
            reason = (f"solo {100 * fixed_run.fix_rate:.0f} % de épocas fijas" if fixed_run.fix_rate <
                      MIN_FIX_RATE_AUTO else f"la fija no coincide con la libre de ionosfera ({detail})")
            warnings.append(f"Línea base {station.id} ({length / 1000:.1f} km): {reason}; se usa la solución libre "
                            "de ionosfera (flotante).")
    elif chosen.strategy.key == "libre-de-ionosfera" and (options.ionosphere or "auto") == "auto":
        log.append(f"  {length / 1000:.1f} km: línea larga, solución libre de ionosfera.")
    solution = summarize(chosen, rover, station, base_xyz, options, warnings, notes)
    return solution, chosen


def _best_epochs(run: "RunResult") -> tuple[np.ndarray | None, np.ndarray | None]:
    if not run.epochs:
        return None, None
    order = [rtklib.Q_FIX, rtklib.Q_FLOAT, rtklib.Q_PPP, rtklib.Q_DGPS, rtklib.Q_SBAS, rtklib.Q_SINGLE]
    best_q = min((e.q for e in run.epochs), key=lambda q: order.index(q) if q in order else 99)
    chosen = [e for e in run.epochs if e.q == best_q]
    best = min(chosen, key=lambda e: float(np.trace(e.cov)))
    return np.median(np.array([e.xyz for e in chosen]), axis=0), best.cov


def _agreement(fixed_run: "RunResult", float_run: "RunResult", base_xyz: np.ndarray) -> tuple[bool, str]:
    a, _ = _best_epochs(fixed_run)
    b, cov_b = _best_epochs(float_run)
    if a is None or b is None:
        return False, "sin solución para comparar"
    d = ecef_delta_to_enu(base_xyz, a - b)
    cov_enu = covariance_ecef_to_enu(base_xyz, cov_b)
    tol_h = max(AGREEMENT_H_M, 3.0 * math.sqrt(max(0.0, cov_enu[0, 0] + cov_enu[1, 1])))
    tol_v = max(AGREEMENT_V_M, 3.0 * math.sqrt(max(0.0, cov_enu[2, 2])))
    dh = math.hypot(d[0], d[1])
    ok = dh <= tol_h and abs(d[2]) <= tol_v
    return ok, (f"diferencia {dh * 100:.1f} cm en horizontal y {d[2] * 100:+.1f} cm en vertical "
                f"(tolerancia {tol_h * 100:.1f} / {tol_v * 100:.1f} cm)")


def _kinematic_ar(options: ProcessingOptions) -> str:
    mode = options.ambiguity_mode
    return mode if mode in ("continuous", "fix-and-hold", "instantaneous", "off") else "fix-and-hold"


def error_model_enu(solution: Solution, length_m: float, hours: float = 1.0) -> np.ndarray:
    model = ERROR_MODEL[solution]
    root = math.sqrt(max(hours, 0.25))
    sh = model["h"][0] + model["h"][1] * length_m + model["h"][2] / root
    sv = model["v"][0] + model["v"][1] * length_m + model["v"][2] / root
    return np.diag([sh * sh, sh * sh, sv * sv])


def summarize(run: RunResult, rover: ObservationSet, station: Station, base_xyz: np.ndarray,
              options: ProcessingOptions, warnings: list[str], notes: list[str]) -> BaselineSolution:
    epochs = run.epochs
    if not epochs:
        raise UserError(f"RTKLIB no dio ninguna solución con la base {station.id}: revisa que haya satélites "
                        "comunes y archivo de navegación.")
    best_q = min((e.q for e in epochs), key=lambda q: [rtklib.Q_FIX, rtklib.Q_FLOAT, rtklib.Q_PPP, rtklib.Q_DGPS,
                                                       rtklib.Q_SBAS, rtklib.Q_SINGLE].index(q)
                 if q in (1, 2, 3, 4, 5, 6) else 99)
    solution = Q_TO_SOLUTION.get(best_q, Solution.NONE)
    chosen = [e for e in epochs if e.q == best_q]
    xyz = np.median(np.array([e.xyz for e in chosen]), axis=0)
    best = min(chosen, key=lambda e: float(np.trace(e.cov)))
    formal = best.cov
    vector = xyz - base_xyz
    length = float(np.linalg.norm(vector))
    hours = max((epochs[-1].time - epochs[0].time).total_seconds() / 3600.0, 1.0 / 60.0)
    model_enu = error_model_enu(solution, length, hours)
    model_ecef = covariance_enu_to_ecef(xyz, model_enu)
    spread = np.cov(np.array([e.xyz for e in chosen]).T) if len(chosen) > 2 else np.zeros((3, 3))
    cov = formal + model_ecef
    cov_enu_formal = covariance_ecef_to_enu(base_xyz, formal)
    e, n, u = ecef_delta_to_enu(base_xyz, vector)
    az, zen = azimuth_zenith(base_xyz, vector)
    stat_rows = rtklib.parse_stat(run.stat_file)
    fixed_times = {ep.time for ep in chosen}
    rms = rtklib.residual_rms(stat_rows, fixed_times) or rtklib.residual_rms(stat_rows)
    sats = rtklib.satellites_used([r for r in stat_rows if r.time in fixed_times]) or rtklib.satellites_used(stat_rows)
    ratios = [ep.ratio for ep in chosen if ep.ratio > 0]
    ratio = statistics.median(ratios) if ratios else None
    if solution == Solution.FIXED and ratio is not None and ratio < options.min_ratio:
        warnings.append(f"Cociente (ratio) bajo en la línea {station.id}: {ratio:.1f}.")
    if solution != Solution.FIXED:
        warnings.append(f"La línea base {station.id} quedó {'FLOTANTE' if solution == Solution.FLOAT else 'sin solución diferencial'}.")
    spread_enu = covariance_ecef_to_enu(base_xyz, spread)
    spread_h = math.sqrt(max(0.0, spread_enu[0, 0] + spread_enu[1, 1]))
    if solution == Solution.FIXED and spread_h > 0.02:
        warnings.append(f"Las épocas fijas de la línea {station.id} se dispersan {spread_h * 100:.1f} cm en "
                        "horizontal: puede haber fijas falsas.")
    start_t = gps_to_utc(epochs[0].time.replace(tzinfo=timezone.utc))
    end_t = gps_to_utc(epochs[-1].time.replace(tzinfo=timezone.utc))
    return BaselineSolution(
        base=station, rover_marker=rover.marker, start=start_t, end=end_t,
        epochs_used=sum(1 for ep in epochs if ep.q in (rtklib.Q_FIX, rtklib.Q_FLOAT)),
        satellites=sats, solution=solution, ratio=ratio, rms_m=rms,
        vector_ecef=Ecef(float(vector[0]), float(vector[1]), float(vector[2]), station.position.frame,
                         station.position.epoch),
        vector_enu=Enu(e, n, u), length_m=length, azimuth_deg=az, zenith_deg=zen,
        covariance_ecef=tuple(tuple(float(v) for v in row) for row in cov),
        rover_position=Ecef(float(xyz[0]), float(xyz[1]), float(xyz[2]), station.position.frame,
                            station.position.epoch),
        ephemeris=options.ephemeris, pos_file=run.pos_file, stat_file=run.stat_file, warnings=warnings,
        fix_rate=run.fix_rate, strategy=run.strategy.text, config_file=run.config_file,
        formal_sigma_enu=Enu(*(math.sqrt(max(0.0, float(cov_enu_formal[i, i]))) for i in range(3))),
        antenna_notes=notes)

