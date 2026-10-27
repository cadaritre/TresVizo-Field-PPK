"""Una línea base estática: estrategia según la longitud, proceso y `BaselineSolution`.

Estrategia (`ProcessingOptions.ionosphere`), ajustada con las pruebas sobre la RGNA (docs/validacion.md):
- «auto»:
  - menos de 20 km: RTKLIB con la ionosfera del mensaje de navegación, troposfera Saastamoinen y ambigüedades
    enteras;
  - 20 km o más: el **procesador propio** (`larga`): libre de ionosfera de GPS, Galileo y BeiDou, troposfera
    estimada con gradientes, ambigüedades de ancho y estrecho de carril con LAMBDA, solución fija solo si pasa la
    tasa de éxito, la prueba de razón, la prueba χ² de la distancia entera, la duración mínima validada (45 min hasta
    60 km, 1 h desde ahí, 2 h con máscara de más de 20°) y la coherencia de la fija con la flotante (a menos de 3σ).
    En la validación (tres días de la RGNA, docs/validacion.md) fijó casi todas las sesiones de 1 h o más a 30–183 km,
    con error de centímetro, y su flotante es mejor que la libre de ionosfera de RTKLIB. RTKLIB corre además la
    libre de ionosfera como comprobación independiente (y sus gráficas de cielo, satélites y residuos van al informe);
    si las dos no concuerdan, se avisa (es una comprobación gruesa: con su tolerancia no avisó en ninguno de los fijos
    a más de 3σ de la §7). La fija de RTKLIB con la ionosfera del mensaje (la «media» de antes, entre 20 y 60 km) ya no
    se usa: en la validación, con 15 min a 30 km, dio un fijo con 12.5 cm de error en la vertical (3.3σ) donde el
    propio, con buena razón, quedó flotante. Si el procesador propio no puede con los datos por otra razón (p. ej. sin
    órbitas de sus sistemas), se usa RTKLIB como antes (media + libre de ionosfera entre 20 y 60 km; libre de
    ionosfera desde ahí), salvo que los datos no alcancen para ninguna solución con fundamento
    (`larga.procesador.InsufficientData`: pocos satélites comunes, sin dos frecuencias en común, fases incoherentes
    con el código): entonces la línea base no tiene solución y el error dice por qué (con varias bases, esa se deja
    fuera y las demás se ajustan).
- «iono-free»: siempre libre de ionosfera de RTKLIB (flotante). «broadcast»: siempre con la ionosfera del mensaje y
  ambigüedades enteras. «estimated»: ionosfera estimada por satélite (opción de RTKLIB; en las pruebas no fijó).
- RTKLIB: ambigüedades enteras solo de GPS, Galileo y QZSS: las de GLONASS (sesgos entre canales) y BeiDou impidieron
  fijar en las pruebas (ver docs/validacion.md); esos satélites entran en la solución con ambigüedad flotante.

Coordenada final de una línea de RTKLIB: mediana de las épocas de la solución combinada adelante/atrás con la mejor
calidad (fijas; si no hay, flotantes). Covarianza: la formal de RTKLIB más un modelo de error realista (ver
`ERROR_MODEL`), porque la formal de un estático largo es de décimas de milímetro y no incluye órbitas, troposfera ni
multitrayectoria; el ajuste de red pone a prueba ese modelo con su chi². La del procesador propio trae su propio
modelo calibrado (`larga.procesador.ERROR_FLOOR`).
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

SHORT_BASELINE_M = 20_000.0      # «auto»: hasta aquí, ionosfera del mensaje y ambigüedades enteras (RTKLIB)
LONG_BASELINE_M = 60_000.0       # «auto»: desde aquí, RTKLIB solo libre de ionosfera (flotante)
OWN_BASELINE_M = 20_000.0        # «auto»: desde aquí, el procesador propio de líneas largas (`larga`)
CHECK_H_M = 0.03                 # tolerancia mínima propio ↔ libre de ionosfera de RTKLIB (o 3σ combinada)
CHECK_V_M = 0.06
SYSTEM_CODES = {"GPS": "G", "Galileo": "E", "BeiDou": "C"}
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
    stage = rtklib.Stage(workdir, out.stem)          # nombres ASCII y rutas relativas (ver rtklib.Stage)
    try:
        args = [str(exe), "-p", "0", "-e", "-o", "salida.pos"]
        if obs.start:
            s = utc_to_gps(obs.start)
            args += ["-ts", s.strftime("%Y/%m/%d"), s.strftime("%H:%M:%S"),
                     "-te", (s + timedelta(minutes=10)).strftime("%Y/%m/%d"),
                     (s + timedelta(minutes=10)).strftime("%H:%M:%S")]
        args += [stage.file(obs.obs[0]), stage.folder(list(nav), "navegacion")]
        tools.run(args, cwd=stage.dir, cancel=cancel, log=log)
        out.unlink(missing_ok=True)
        stage.collect("salida.pos", out)
    finally:
        stage.close()
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
    base_type, base_radome, base_h = base_antenna(station, base, warnings)
    base_choice = antex.choose(base_type, base_radome, f"Base {station.id}")
    notes.append(base_choice.note)
    rover_h = rover.antenna_height_m if rover_antenna_delta_u is None else rover_antenna_delta_u
    rover_setup = rtklib.AntennaSetup(rover_choice.rtklib_name, (0.0, 0.0, rover_h))
    base_setup = rtklib.AntennaSetup(base_choice.rtklib_name, (0.0, 0.0, base_h))

    # Longitud aproximada → estrategia
    approx = approx_position(rover, nav, workdir, cancel=cancel, log=log)
    length = float(np.linalg.norm(approx - base_xyz))
    plan = strategies(length, options, mode)
    use_own = own_applies(length, options, mode)
    interval = options.interval_s or max(rover.interval_s or 1.0, base.interval_s or 1.0) if mode == "static" \
        else options.interval_s
    start_g = utc_to_gps(start).replace(tzinfo=None)
    end_g = utc_to_gps(end).replace(tzinfo=None)
    precise = options.ephemeris == Ephemeris.PRECISE
    own = None
    if use_own:
        tools.check(cancel)
        from .larga.procesador import InsufficientData
        try:
            own = _process_own(rover, station, base, options, base_xyz, approx, rover_choice, base_choice, rover_h,
                               base_h, start_g, end_g, workdir, log,
                               progress=lambda f, m: step(0.6 * f, m), cancel=cancel)
        except InsufficientData as error:
            # los datos no alcanzan (pocos satélites comunes, sin dos frecuencias en común, fases incoherentes):
            # RTKLIB tendría los mismos datos y daría una solución sin fundamento, así que no se entrega ninguna
            log.append(f"  procesador propio: {error}")
            raise UserError(f"Línea base {station.id} ({length / 1000:.1f} km): no hay solución con estos datos. "
                            f"{error}") from error
        except UserError as error:
            warnings.append(f"Línea base {station.id} ({length / 1000:.1f} km): el procesador de líneas largas no pudo "
                            f"con estos datos ({error}); se usa RTKLIB.")
            log.append(f"  procesador propio: {error}")
    if own is not None:
        # el propio decide; RTKLIB solo corre la libre de ionosfera como comprobación independiente (su fija con la
        # ionosfera del mensaje, que antes se probaba entre 20 y 60 km, dio un fijo erróneo en la validación)
        plan = [s_ for s_ in plan if s_.key == "libre-de-ionosfera"] or plan
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
        lo, hi = (0.6, 1.0) if own is not None else (0.0, 1.0)
        frac0, frac1 = lo + (hi - lo) * k / len(plan), lo + (hi - lo) * (k + 1) / len(plan)
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
        log.append(f"  fija de RTKLIB frente a su libre de ionosfera: {detail}")
        if fixed_run.fix_rate >= MIN_FIX_RATE_AUTO and agree:
            chosen = fixed_run
        else:
            chosen = float_run
            if own is None:
                reason = (f"solo {100 * fixed_run.fix_rate:.0f} % de épocas fijas" if fixed_run.fix_rate <
                          MIN_FIX_RATE_AUTO else f"la fija no coincide con la libre de ionosfera ({detail})")
                warnings.append(f"Línea base {station.id} ({length / 1000:.1f} km): {reason}; se usa la solución "
                                "libre de ionosfera (flotante).")
    elif chosen.strategy.key == "libre-de-ionosfera" and (options.ionosphere or "auto") == "auto" and own is None:
        log.append(f"  {length / 1000:.1f} km: línea larga, solución libre de ionosfera.")
    if own is not None:
        control = runs[-1]
        solution = summarize_own(own, rover, station, base_xyz, options, control, warnings, notes)
        _cross_check(solution, control, rover, station, base_xyz, options, log)
        return solution, control
    solution = summarize(chosen, rover, station, base_xyz, options, warnings, notes)
    return solution, chosen


def own_applies(length_m: float, options: ProcessingOptions, mode: str = "static") -> bool:
    """El procesador propio se usa en estático, «auto», doble frecuencia, desde `OWN_BASELINE_M`."""
    return (mode == "static" and (options.ionosphere or "auto").lower() == "auto" and options.frequencies >= 2
            and length_m >= OWN_BASELINE_M and any(c in options.constellations for c in SYSTEM_CODES))


def _process_own(rover, station, base, options, base_xyz, approx, rover_choice, base_choice, rover_h, base_h,
                 start_g, end_g, workdir, log, *, progress=None, cancel=None):
    from .larga import antena as larga_antena
    from .larga import procesador
    from .larga.astro import gps_seconds

    def full(choice):
        return larga_antena.receiver_antenna(choice.model.type, choice.model.radome) if choice.model else None

    systems = "".join(code for name, code in SYSTEM_CODES.items() if name in options.constellations)
    rover_ep = procesador.Endpoint(rover.obs[0], np.array(approx, dtype=float), rover_h, full(rover_choice),
                                   rover.marker)
    base_ep = procesador.Endpoint(base.obs[0], base_xyz, base_h, full(base_choice), station.id)
    nav = list(dict.fromkeys(list(rover.nav) + list(base.nav)))
    precise = [Path(p) for p in options.precise_files] if options.ephemeris == Ephemeris.PRECISE else []
    if any(p.suffix.lower() in (".gz", ".z", ".zip") for p in precise):
        from . import compress
        precise = compress.unpack(precise, workdir / "orbitas_descomprimidas")
    result = procesador.process(rover_ep, base_ep, nav, precise, mask_deg=options.elevation_mask_deg,
                                start=gps_seconds(start_g), end=gps_seconds(end_g), interval_s=options.interval_s,
                                systems=systems, progress=progress, cancel=cancel)
    log.append(f"Línea base {station.id}: procesador propio de líneas largas.")
    log.extend(f"  {line}" for line in result.log)
    log.extend(f"  {line}" for line in result.notes)
    if result.fixed:
        log.append(f"  FIJA: {result.n_nl_fixed} combinaciones enteras de {result.n_nl_candidates} candidatas "
                   f"({result.n_amb} dobles diferencias), razón {result.ratio:.1f}, tasa de éxito "
                   f"{100 * result.success:.2f} %.")
    else:
        log.append(f"  FLOTANTE: {result.reason}.")
    return result


def summarize_own(own, rover: ObservationSet, station: Station, base_xyz: np.ndarray, options: ProcessingOptions,
                  control: "RunResult", warnings: list[str], notes: list[str]) -> BaselineSolution:
    """`BaselineSolution` del procesador propio (las gráficas de cielo, satélites y residuos son las de la libre de
    ionosfera de RTKLIB que corre como control)."""
    from ..model import AmbiguityResolution
    from .larga import procesador
    xyz = np.asarray(own.position, dtype=float)
    cov = np.asarray(own.covariance, dtype=float)
    vector = xyz - base_xyz
    length = float(np.linalg.norm(vector))
    e, n, u = ecef_delta_to_enu(base_xyz, vector)
    az, zen = azimuth_zenith(base_xyz, vector)
    solution = Solution.FIXED if own.fixed else Solution.FLOAT
    formal = own.formal_covariance if own.formal_covariance is not None else cov
    cov_enu_formal = covariance_ecef_to_enu(base_xyz, formal)
    float_enu = covariance_ecef_to_enu(own.float_position, own.float_covariance)
    frame, epoch = station.position.frame, station.position.epoch
    groups = [f"{g.label}: {', '.join(g.satellites)}" + ("" if g.ar else f" (flotantes: {g.note})")
              for g in own.groups]
    excluded = list(own.notes) + ["GLONASS no entra en las líneas largas: cada satélite transmite en su frecuencia "
                                  "(FDMA) y los sesgos entre canales dependen del receptor; QZSS tampoco (pocos "
                                  "satélites visibles)."]
    amb = AmbiguityResolution(
        fixed=own.fixed, reason=own.reason, double_differences=own.n_amb, widelane_fixed=own.n_wl_fixed,
        narrowlane_candidates=own.n_nl_candidates, narrowlane_fixed=own.n_nl_fixed, ratio=own.ratio,
        min_ratio=procesador.NL_MIN_RATIO, success_rate=own.success, min_success_rate=procesador.NL_MIN_SUCCESS,
        min_session_h=own.min_hours, sigma0=own.sigma0_fixed if own.fixed else own.sigma0_float,
        rms_phase_m=own.rms_phase_m, troposphere_interval_h=own.trop_step_h, gradient_interval_h=own.grad_step_h,
        signal_groups=groups, excluded=excluded,
        float_position=Ecef(*(float(v) for v in own.float_position), frame, epoch),
        float_sigma_enu=Enu(*(math.sqrt(max(0.0, float(float_enu[i, i]))) for i in range(3))),
        observations=own.observations, rejected=own.rejected)
    km = length / 1000.0
    if own.fixed:
        text = (f"procesador propio: libre de ionosfera ({_systems_text(own)}), troposfera estimada cada "
                f"{own.trop_step_h:g} h con gradientes, ambigüedades de ancho y estrecho de carril con LAMBDA: fijas "
                f"({own.n_nl_fixed} de {own.n_nl_candidates} combinaciones, razón {own.ratio:.1f}, tasa de éxito "
                f"{100 * own.success:.2f} %)")
    else:
        text = (f"procesador propio: libre de ionosfera ({_systems_text(own)}), troposfera estimada cada "
                f"{own.trop_step_h:g} h con gradientes, ambigüedades flotantes ({own.reason})")
        warnings.append(f"La línea base {station.id} ({km:.1f} km) quedó FLOTANTE: {own.reason}.")
    start_t = gps_to_utc(own.start.replace(tzinfo=timezone.utc))
    end_t = gps_to_utc(own.end.replace(tzinfo=timezone.utc)) + timedelta(seconds=own.interval_s)
    return BaselineSolution(
        base=station, rover_marker=rover.marker, start=start_t, end=end_t, epochs_used=own.epochs,
        satellites=list(own.satellites), solution=solution, ratio=own.ratio if own.fixed else None,
        rms_m=own.rms_phase_m,
        vector_ecef=Ecef(float(vector[0]), float(vector[1]), float(vector[2]), frame, epoch),
        vector_enu=Enu(e, n, u), length_m=length, azimuth_deg=az, zenith_deg=zen,
        covariance_ecef=tuple(tuple(float(v) for v in row) for row in cov),
        rover_position=Ecef(float(xyz[0]), float(xyz[1]), float(xyz[2]), frame, epoch),
        ephemeris=options.ephemeris, pos_file=None, stat_file=control.stat_file, warnings=warnings,
        fix_rate=1.0 if own.fixed else 0.0, strategy=text, config_file=control.config_file,
        formal_sigma_enu=Enu(*(math.sqrt(max(0.0, float(cov_enu_formal[i, i]))) for i in range(3))),
        antenna_notes=notes, engine="TresVizo", ambiguity=amb)


def _systems_text(own) -> str:
    names = []
    for g in own.groups:
        name = g.label.split(" ")[0].split("-")[0]
        if name not in names:
            names.append(name)
    return " + ".join(names)


def _cross_check(solution: BaselineSolution, control: "RunResult", rover: ObservationSet, station: Station,
                 base_xyz: np.ndarray, options: ProcessingOptions, log: list[str]) -> None:
    """Compara la solución propia con la libre de ionosfera de RTKLIB (comprobación independiente) y avisa si no
    concuerdan dentro de 3σ combinada (o `CHECK_H_M`/`CHECK_V_M`)."""
    if not control.epochs:
        log.append("  comprobación con RTKLIB: sin solución de RTKLIB para comparar.")
        return
    try:
        other = summarize(control, rover, station, base_xyz, options, [], [])
    except UserError:
        log.append("  comprobación con RTKLIB: sin solución de RTKLIB para comparar.")
        return
    a = np.array([solution.rover_position.x, solution.rover_position.y, solution.rover_position.z])
    b = np.array([other.rover_position.x, other.rover_position.y, other.rover_position.z])
    d = ecef_delta_to_enu(base_xyz, a - b)
    cov = np.array(solution.covariance_ecef) + np.array(other.covariance_ecef)
    ce = covariance_ecef_to_enu(base_xyz, cov)
    tol_h = max(CHECK_H_M, 3.0 * math.sqrt(max(0.0, ce[0, 0] + ce[1, 1])))
    tol_v = max(CHECK_V_M, 3.0 * math.sqrt(max(0.0, ce[2, 2])))
    dh = math.hypot(d[0], d[1])
    text = (f"diferencia con la libre de ionosfera de RTKLIB: {dh * 100:.1f} cm en horizontal y {d[2] * 100:+.1f} cm "
            f"en vertical (tolerancia {tol_h * 100:.1f} / {tol_v * 100:.1f} cm)")
    log.append(f"  comprobación independiente: {text}.")
    if dh > tol_h or abs(d[2]) > tol_v:
        solution.warnings.append(f"Línea base {station.id}: la solución del procesador de líneas largas no concuerda "
                                 f"con la comprobación independiente ({text}). Revisa los datos de las dos "
                                 "estaciones.")


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


def base_antenna(station: Station, base: ObservationSet, warnings: list[str]) -> tuple[str, str, float]:
    """Antena, radomo y altura (marca → ARP) de la base. Estación de una red: los del catálogo (gana el catálogo y
    se avisa si el RINEX dice otra cosa). Base propia: los de sus observaciones (o los de la Station si faltan)."""
    if station.network and station.official:
        if base.antenna_type and station.antenna_type and \
                base.antenna_type.replace(" ", "").upper() != station.antenna_type.replace(" ", "").upper():
            warnings.append(f"La antena del RINEX de {station.id} ({base.antenna_type}) no es la del catálogo "
                            f"({station.antenna_type}): se usa la del catálogo.")
        if base.antenna_height_m and abs(base.antenna_height_m - station.antenna_height_m) > 0.0005:
            warnings.append(f"Altura de antena de {station.id}: los datos dicen {base.antenna_height_m:.4f} m y el "
                            f"catálogo {station.antenna_height_m:.4f} m; se usa la del catálogo.")
        return (station.antenna_type or base.antenna_type, station.radome or "NONE" if station.antenna_type
                else base.radome or "NONE", station.antenna_height_m)
    kind = base.antenna_type or station.antenna_type
    dome = (base.radome or "NONE") if base.antenna_type else (station.radome or "NONE")
    height = base.antenna_height_m if base.antenna_height_m else station.antenna_height_m
    return kind, dome, height


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

