"""Procesador propio de líneas base largas: ancho de carril, carril estrecho con LAMBDA y solución fija validada.

Pasos (ver el paquete `larga` para cada modelo):
1. Lectura de las observaciones, épocas comunes y pareja de frecuencias por satélite (misma clase de señal en los
   dos receptores, `senales`).
2. Geometría y modelos por receptor (`geometria`), combinaciones y arcos sin salto de ciclo (`preproceso`).
3. Solución de código (posición aproximada a decímetros) y ajuste flotante por lotes de la libre de ionosfera de
   fase y código con la troposfera estimada (`ajuste`), con depuración de datos aberrantes.
4. **Ancho de carril:** media de la Melbourne-Wübbena de cada arco de la diferencia simple; la doble diferencia
   contra el arco de referencia del grupo se fija si la probabilidad de que el redondeo sea el entero verdadero
   (`rounding_probability`) llega a `WL_MIN_PROB`, con la σ de la media inflada por la correlación temporal de la
   serie (modelo AR(1)) y por `WL_SIGMA_FACTOR`.
5. **Carril estrecho:** con el ancho de carril fijo, la ambigüedad libre de ionosfera es B = λ_NL·N1 + κ·N_WL; N1 se
   estima flotante en el ajuste y se fija con LAMBDA y resolución parcial (`lambda_ils.partial_fix`) con su tasa de
   éxito del bootstrapping, la prueba de razón y la prueba de la distancia entera (χ²).
6. **Solución fija:** ajuste condicionado por las combinaciones enteras fijadas; covarianza escalada por σ0² y por
   la correlación temporal de los residuos; si alguna prueba falla, queda la flotante y se dice por qué.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from ...errors import UserError
from .. import tools
from ..geodesy import covariance_ecef_to_enu, covariance_enu_to_ecef, ecef_delta_to_enu
from ..adjust import chi2_ppf
from . import ajuste, efemerides, geometria, observaciones, preproceso, senales
from .astro import gps_datetime
from .lambda_ils import LambdaError, partial_fix
from .senales import Pair

# Ruido a priori de una observación no diferenciada en el cenit (se lleva a la elevación con 1 + 1/sen²e)
SIGMA_PHASE_M = 0.003
SIGMA_CODE_M = 0.30
MIN_ARC_S = 600.0               # arcos más cortos no entran
TROP_STEP_S = 3600.0            # nodos del retardo húmedo cenital
TROP_SIGMA_ABS_M = 0.30         # restricción de cada nodo respecto del a priori
TROP_SIGMA_RW_M_PER_SQRT_H = 0.02
WL_SIGMA_FACTOR = 2.0           # σ de la media de la MW: la formal (con AR(1)) por este factor (validación)
BIAS_B0_M = 0.001               # error sistemático por arco: (B0 + B1·L/100 km)·(1/sen e media), en metros
BIAS_B1_M = 0.003
GRAD_STEP_S = 6 * 3600.0        # nodos de los gradientes troposféricos
GRAD_SIGMA_ABS_M = 0.002        # gradiente (Chen y Herring 1997): a priori 0 ± 2 mm
GRAD_SIGMA_RW_M_PER_SQRT_H = 0.0005
WL_MIN_PROB = 0.999
NL_MIN_SUCCESS = 0.999
NL_MIN_RATIO = 3.0
NL_MIN_FIXED = 4
# Duración mínima de la sesión para fijar el carril estrecho: (menos de MIN_FIX_LONG_KM, desde ahí), en horas.
# De la validación (docs/validacion.md): a 100–141 km, con 45 min ya hubo fijos a más de 3σ en la vertical y con 1 h
# se fijan casi todas; a 30 km, con 30 min 1 de 16 pasó de 3σ en otro día (§7) y con 45 min ninguna. Con menos, la
# línea queda flotante y el informe lo dice.
MIN_FIX_HOURS = (0.75, 1.0)
MIN_FIX_LONG_KM = 60.0
# Con máscara de elevación alta la altura y la troposfera no se separan en una hora (sin observaciones bajas el
# retardo cenital y la vertical se confunden): con más de HIGH_MASK_DEG hacen falta HIGH_MASK_MIN_HOURS para fijar
# (§7: con 25–30° y 1 h, 4 de 36 fijas pasaron de 3σ, una a 6.8σ; con 2 h, ninguna).
HIGH_MASK_DEG = 20.0
HIGH_MASK_MIN_HOURS = 2.0
# Prueba de coherencia fija–flotante: con enteros correctos la fija cae dentro de la región de confianza de la
# flotante (que no depende de los enteros). Si algún componente se aleja más de FIX_FLOAT_K σ de la flotante (con la
# σ realista de la flotante), los enteros no son confiables aunque pasen la razón y la tasa de éxito, y no se fija.
# Atrapa el fijo de +136 mm de la §7 (flotante a +1.5 mm) y casi no cuesta fijas (0.4 % en la §8).
FIX_FLOAT_K = 3.0
# Datos mínimos para entregar una solución (si no, InsufficientData y no hay solución): dobles diferencias por época
# (posición + troposfera con redundancia) en al menos la mitad de las épocas; σ formal de la flotante; coherencia de
# la solución de las fases con la de código.
MIN_DD_PER_EPOCH = 4
MAX_FLOAT_SIGMA_M = 1.0
MAX_CODE_SIGMA_M = 5.0
MAX_CODE_PHASE_M = 5.0
# Satélite con clases de señal distintas en los dos receptores en una banda (p. ej. L2C en uno y L2 P(Y) en el
# otro): el sesgo de código del satélite entre clases (DCB, del orden de ±1 ns) no se cancela. Su código entra con
# esta σ adicional por banda (m) y su ancho de carril no se fija (ver `senales`).
DCB_SIGMA_M = 0.3
# Flotante con satélites de clases mezcladas: el sesgo de código entre clases y lo que no se fija dejan la σ corta;
# con las 16 sesiones de 1 h de MEXI–IMIE (137 km, Trimble Alloy con L2C contra NetR9 con P(Y) en RINEX 2.10, el
# único par mixto de la RGNA, 15-07 y 18-02) el RMS de error/σ era 1.7–1.8: la σ se multiplica por este factor.
MIXED_FLOAT_FACTOR = 1.8
OUTLIER_SIGMAS = 4.0
# Error que la covarianza formal no ve (troposfera, órbitas, multitrayectoria), 1σ por componente en metros:
#     σ = a + b·(L/100 km)/√T + c·(L/100 km) [solo con efemérides difundidas]   (T en horas)
# Calibrado con la validación sobre la RGNA (docs/validacion.md, 168 sesiones de 15 min a 4 h): con él, el error
# frente a la referencia PPP del día queda con RMS de ≤ 1σ por componente y ninguno pasa de 3σ.
#
# Revisado con la verificación fuera de muestra (docs/validacion.md, §7 y §8; tres días): la vertical de la fija ×1.15
# (el RMS de error/σ en U era 1.10–1.14) y, en la flotante, el este y la vertical por FLOAT_FACTOR (su RMS de
# error/σ era 1.3–1.9 en el este con 15–45 min los tres días): «corta» es la flotante de una sesión más corta que la
# duración mínima, la que se espera; «fallida» la de una sesión que debía fijar y no fijó, que es de datos peores
# (con 1 h o más, 4–9 cm en el este donde la σ decía 1.5–2 cm).
ERROR_FLOOR = {
    "fixed": {"h": (0.003, 0.004, 0.001), "v": (0.0046, 0.0069, 0.0023)},
    "float": {"h": (0.006, 0.0055, 0.001), "v": (0.005, 0.0075, 0.0025)},
}
FLOAT_FACTOR = {"corta": (1.6, 1.0, 1.2), "fallida": (2.4, 1.0, 1.4)}      # E, N, U
MAX_ITER = 6
# Tolerancia para tomar como la misma época las etiquetas de tiempo de los dos receptores (y como múltiplo del
# intervalo de proceso): los receptores sin reloj gobernado etiquetan a unos ms del segundo (saltos de 1 ms). La
# geometría se calcula en la etiqueta real de cada receptor, así que el desfase no entra en la solución; el límite lo
# pone la extrapolación de la órbita (`geometria.SatTrack`, |Δτ| < 30 ms).
EPOCH_TOL_S = 0.02


class InsufficientData(UserError):
    """Los datos de la línea base no alcanzan para una solución con fundamento: no se entrega ninguna (ni la de
    RTKLIB, que tendría los mismos datos) y el mensaje dice por qué."""


@dataclass
class Endpoint:
    """Un extremo para el procesador: archivo, marca (ECEF), altura vertical a la ARP y antena del ANTEX."""
    obs_path: Path
    xyz: np.ndarray                  # base: la coordenada fija; rover: aproximada (metros)
    height_m: float
    antenna: object | None           # antena.Antenna
    name: str = ""


@dataclass
class GroupInfo:
    key: str
    label: str
    pair: Pair
    satellites: list[str] = field(default_factory=list)
    ar: bool = True
    note: str = ""
    mix: str = ""                    # clases distintas en los dos receptores («L2C en el rover y P(Y) en la base»)


@dataclass
class LongResult:
    fixed: bool
    position: np.ndarray             # rover (marca), ECEF
    covariance: np.ndarray           # 3×3, m²: la formal más el error que no ve (`realistic_covariance`)
    float_position: np.ndarray
    float_covariance: np.ndarray
    reason: str = ""                 # por qué quedó flotante (vacío si fijó)
    n_amb: int = 0                   # ambigüedades de doble diferencia (arcos fuera de los de referencia)
    n_wl_fixed: int = 0
    n_nl_candidates: int = 0         # con el ancho de carril fijo, las que entran a LAMBDA
    n_nl_fixed: int = 0              # combinaciones enteras fijadas (resolución parcial)
    ratio: float | None = None
    success: float | None = None
    sq_norm: float | None = None
    sq_norm_limit: float | None = None
    sigma0_float: float = 0.0
    sigma0_fixed: float = 0.0
    correlation_factor: float = 1.0
    rms_phase_m: float = 0.0
    rms_code_m: float = 0.0
    epochs: int = 0
    start: datetime | None = None
    end: datetime | None = None
    interval_s: float = 0.0
    satellites: list[str] = field(default_factory=list)
    groups: list[GroupInfo] = field(default_factory=list)
    trop_step_h: float = 1.0
    grad_step_h: float = 6.0
    ztd_rover: list[tuple[datetime, float]] = field(default_factory=list)    # retardo húmedo estimado
    ztd_base: list[tuple[datetime, float]] = field(default_factory=list)
    observations: int = 0
    rejected: int = 0
    notes: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    min_hours: float = 0.0           # duración mínima para fijar a esta distancia
    precise: bool = False
    length_km: float = 0.0
    hours: float = 0.0
    ambiguities: list["AmbiguityRow"] = field(default_factory=list)
    formal_covariance: np.ndarray | None = None   # la formal escalada (σ0² y correlación temporal), sin el piso


@dataclass
class AmbiguityRow:
    """Una ambigüedad de doble diferencia con el ancho de carril fijo (para el informe y el diagnóstico)."""
    satellite: str
    reference: str
    start: datetime
    end: datetime
    epochs: int
    elevation: tuple[float, float]
    widelane: int
    narrowlane_float: float
    narrowlane_sigma: float


def rounding_probability(frac: float, sigma: float) -> float:
    """Probabilidad de que el entero redondeado sea el verdadero: área de la normal N(valor flotante, σ²) sobre el
    intervalo de ±0.5 ciclos alrededor de ese entero, ½·[erf((0.5−b)/(√2σ)) + erf((0.5+b)/(√2σ))] con b la parte
    fraccionaria. Con b = 0 es la tasa de éxito del redondeo de Teunissen (1998), erf(1/(2√2σ)); exige a la vez una
    σ pequeña y un valor cercano al entero (con 0.999: σ ≤ 0.15 ciclos si b = 0, σ ≤ 0.12 si b = 0.1)."""
    b = abs(frac)
    if sigma <= 0:
        return 1.0 if b < 0.5 else 0.0
    root = math.sqrt(2.0) * sigma
    return 0.5 * (math.erf((0.5 - b) / root) + math.erf((0.5 + b) / root))


@dataclass
class _Sat:
    name: str
    pair: Pair
    group: str
    rov: preproceso.Combos
    bas: preproceso.Combos
    arc: np.ndarray                  # arco de la diferencia simple por época (−1 fuera)
    code_extra_var: float = 0.0      # m², libre de ionosfera: DCB entre clases de señal distintas (mezcla)


def _pick_pair(sat: str, rov: observaciones.Observations, bas: observaciones.Observations, ir: np.ndarray,
               ib: np.ndarray) -> tuple | None:
    """La pareja de frecuencias y los tipos de cada receptor para un satélite, o None. `ir`, `ib`: filas de cada
    receptor en las épocas comunes (las dos series no tienen por qué tener las mismas épocas).

    En cada banda se toma la misma clase de señal en los dos receptores; si no la hay, la de clases distintas (p. ej.
    L2C en el rover y L2 P(Y) en la base), marcada como mezcla. Entre parejas de frecuencias gana la que no mezcla
    clases (con ella se pueden fijar las ambigüedades) y luego la de más épocas. Devuelve (épocas, bandas, elección
    por banda, mezcla); la elección de cada banda es (épocas, código y fase del rover, código y fase de la base, clase
    del rover, clase de la base)."""
    system = sat[0]
    jr, jb = rov.column(sat), bas.column(sat)
    best = None
    for b1, b2 in senales.PAIRS.get(system, []):
        choice = []
        for band in (b1, b2):
            cand_r = senales.candidates(system, band, rov.types.get(system, []))
            cand_b = senales.candidates(system, band, bas.types.get(system, []))
            same = cross = None
            for cr, pr in cand_r:
                cls_r = senales.signal_class(system, band, cr[2])
                for cb, pb in cand_b:
                    cls_b = senales.signal_class(system, band, cb[2])
                    if cls_r is None or cls_b is None:
                        continue
                    n = int(np.sum(np.isfinite(rov.values[cr][ir, jr]) & np.isfinite(rov.values[pr][ir, jr]) &
                                   np.isfinite(bas.values[cb][ib, jb]) & np.isfinite(bas.values[pb][ib, jb])))
                    if n <= 0:
                        continue
                    pick = (n, cr, pr, cb, pb, cls_r, cls_b)
                    if cls_r == cls_b:
                        if same is None or n > same[0]:
                            same = pick
                    elif cross is None or n > cross[0]:
                        cross = pick
            pick = same or cross
            if pick is None:
                break
            choice.append(pick)
        if len(choice) == 2:
            n = min(choice[0][0], choice[1][0])
            mixed = any(c[5] != c[6] for c in choice)
            if best is None or (not mixed, n) > (not best[3], best[0]):
                best = (n, (b1, b2), choice, mixed)
    return best


def process(rover: Endpoint, base: Endpoint, nav: list[Path], precise_files: list[Path], *, mask_deg: float,
            start: float, end: float, interval_s: float | None = None, systems: str = "GEC",
            progress: tools.Progress | None = None, cancel: threading.Event | None = None) -> LongResult:
    """Procesa la línea base rover–base (tiempos en segundos GPS) y devuelve la solución fija o flotante.

    Si los datos no alcanzan para una solución con fundamento, `InsufficientData` con el porqué."""
    result = _process(rover, base, nav, precise_files, mask_deg=mask_deg, start=start, end=end,
                      interval_s=interval_s, systems=systems, progress=progress, cancel=cancel)
    mixed = [g for g in result.groups if "~" in g.key]
    if mixed and not result.fixed:
        k2 = MIXED_FLOAT_FACTOR ** 2
        result.covariance = result.covariance * k2
        result.float_covariance = result.float_covariance * k2
        names = sorted({s for g in mixed for s in g.satellites})
        detail = "; ".join(dict.fromkeys(f"{g.label}, {g.mix}" for g in mixed))
        if not any(g.ar for g in result.groups if g not in mixed and len(g.satellites) > 1):
            result.reason = (f"los receptores no comparten la misma clase de señal ({detail}): sin dobles "
                             "diferencias fijables, la solución queda flotante")
        elif result.reason.startswith("solo ") and "ancho de carril" in result.reason:
            result.reason = (f"los receptores no comparten la misma clase de señal ({detail}): {len(names)} "
                             "satélites entran sin fijar y los demás no bastan para fijar; la solución queda "
                             "flotante")
        else:
            result.reason += (f"; {len(names)} satélites entran sin fijar por clases de señal distintas en los dos "
                              f"receptores ({detail})")
        result.notes.append(f"Flotante con clases de señal mezcladas: σ ×{MIXED_FLOAT_FACTOR:g} (validación con "
                            "receptores mixtos de la RGNA).")
    return result


def _process(rover: Endpoint, base: Endpoint, nav: list[Path], precise_files: list[Path], *, mask_deg: float,
             start: float, end: float, interval_s: float | None = None, systems: str = "GEC",
             progress: tools.Progress | None = None, cancel: threading.Event | None = None) -> LongResult:
    step = progress or (lambda f, m: None)
    log: list[str] = []
    notes: list[str] = []
    duration = end - start
    if interval_s is None:
        interval_s = 30.0 if duration > 6 * 3600 else 15.0
    step(0.02, "Leyendo las observaciones…")
    # cada receptor se lee ya diezmado al intervalo de proceso (un rover a 1 Hz no llena la memoria)
    rov = observaciones.read(rover.obs_path, start=start, end=end, decimate_s=interval_s, tolerance_s=EPOCH_TOL_S)
    bas = observaciones.read(base.obs_path, start=start, end=end, decimate_s=interval_s, tolerance_s=EPOCH_TOL_S)
    tools.check(cancel)
    times, ir, ib, proc = align_epochs(rov.times, bas.times, interval_s, rov.interval, bas.interval)
    if len(times) < 20:
        raise InsufficientData(_few_epochs_text(rov, bas, len(times), proc))
    t_rov, t_bas = rov.times[ir], bas.times[ib]           # etiquetas de tiempo reales de cada receptor
    log.append(f"Épocas comunes: {len(times)} cada {proc:g} s, de {gps_datetime(times[0]):%H:%M:%S} a "
               f"{gps_datetime(times[-1]):%H:%M:%S} GPS (rover cada {rov.interval:g} s, base cada "
               f"{bas.interval:g} s).")
    skew = max(float(np.max(np.abs(t_rov - times))), float(np.max(np.abs(t_bas - times))))
    if skew > 1e-6:
        log.append(f"Etiquetas de tiempo corridas hasta {skew * 1000:.1f} ms del segundo: la geometría se calcula en "
                   "el instante de cada receptor.")

    # Órbitas
    step(0.05, "Órbitas y relojes de los satélites…")
    broadcast = efemerides.Broadcast(efemerides.read_nav(nav)) if nav else None
    sp3, clk = efemerides.sp3_and_clk(precise_files)
    if sp3:
        orbits = efemerides.Precise(sp3, clk, broadcast)
        precise = True
        log.append("Órbitas precisas: " + ", ".join(p.name for p in sp3) +
                   ("; relojes: " + ", ".join(p.name for p in clk) if clk else "; relojes del SP3"))
    elif broadcast is not None:
        orbits = broadcast
        precise = False
        log.append("Órbitas difundidas (mensaje de navegación).")
    else:
        raise UserError("No hay órbitas: falta el archivo de navegación o el SP3.")
    env = geometria.environment(times, orbits, precise)
    mjd = 44244.0 + times[0] / 86400.0
    when = gps_datetime(times[0]).replace(tzinfo=None)

    # Satélites y parejas
    common = [s for s in rov.sats if s in bas.sats and s in orbits.satellites and s[0] in systems]
    off_systems = [senales.SYSTEM_NAMES[c] for c in "GEC" if c not in systems]
    if off_systems:
        notes.append("Sin " + ", ".join(off_systems) + " (apagados en las opciones).")
    sats: list[_Sat] = []
    groups: dict[str, GroupInfo] = {}
    skipped: dict[str, list[str]] = {}
    for s in common:
        got = _pick_pair(s, rov, bas, ir, ib)
        if got is None:
            skipped.setdefault("sin pareja de frecuencias común (misma señal en los dos receptores)", []).append(s)
            continue
        _, (b1, b2), choice, mixed = got
        pair = senales.make_pair(s[0], b1, b2)
        # clases de cada banda (rover~base si no coinciden): las dobles diferencias se forman dentro de cada grupo,
        # así que el desfase constante de cada clase en cada receptor (el cuarto de ciclo de L2C) se cancela
        classes = "/".join(c[5] if c[5] == c[6] else f"{c[5]}~{c[6]}" for c in choice)
        gkey = senales.group_key(s, pair) + ":" + classes
        if s[0] == "C":
            gen = _bds_generation(s, when)
            if gen is None:
                skipped.setdefault("BeiDou geoestacionario", []).append(s)
                continue
            gkey = f"C{gen}-{b1}{b2}:{classes}"
        jr, jb = rov.column(s), bas.column(s)
        (_, cr1, pr1, cb1, pb1, _, _), (_, cr2, pr2, cb2, pb2, _, _) = choice
        rc = preproceso.combos(pair, rov.values[cr1][ir, jr], rov.values[pr1][ir, jr], rov.values[cr2][ir, jr],
                               rov.values[pr2][ir, jr], rov.lli[pr1][ir, jr], rov.lli[pr2][ir, jr])
        bc = preproceso.combos(pair, bas.values[cb1][ib, jb], bas.values[pb1][ib, jb], bas.values[cb2][ib, jb],
                               bas.values[pb2][ib, jb], bas.lli[pb1][ib, jb], bas.lli[pb2][ib, jb])
        g = groups.setdefault(gkey, GroupInfo(gkey, _group_text(gkey, pair), pair))
        g.satellites.append(s)
        extra = 0.0
        if mixed:
            g.ar = False
            g.mix = _mixed_bands(choice)
            g.note = (f"clases de señal distintas ({g.mix}): el sesgo de código de cada satélite entre clases no se "
                      "cancela y el ancho de carril no se puede fijar; ambigüedades flotantes")
            coef = (pair.alpha, pair.beta)
            extra = sum((coef[k] * DCB_SIGMA_M) ** 2 for k, c in enumerate(choice) if c[5] != c[6])
        sats.append(_Sat(s, pair, gkey, rc, bc, np.full(len(times), -1), extra))
    for why, names in skipped.items():
        notes.append(f"Fuera de la línea larga ({why}): {', '.join(names)}.")
    if not sats:
        raise InsufficientData("Ningún satélite de GPS, Galileo o BeiDou tiene una pareja de frecuencias que observen "
                               "los dos receptores" + _signals_text(rov, bas, systems) + ".")
    for g in groups.values():
        if len(g.satellites) < 2:
            g.ar = False
            g.note = "un solo satélite: sin dobles diferencias"
    if any(not g.ar and "~" in g.key for g in groups.values()):
        notes.append("Receptores con señales de clases distintas en alguna banda: esos satélites entran con "
                     "ambigüedades flotantes (ver los grupos de señal).")

    # Geometría de la base (fija) y primera del rover
    step(0.12, "Modelos: mareas, troposfera, antenas…")
    base_station = geometria.Station(base.xyz, base.height_m, base.antenna, base.name)
    rover_station = geometria.Station(rover.xyz, rover.height_m, rover.antenna, rover.name)
    base_frame = geometria.site_frame(base.xyz, mjd)
    base_rec = geometria.receiver_positions(base_station, base.xyz, env, base_frame)
    cache: dict = {}
    tracks = {s.name: geometria.satellite_track(s.name, s.pair, env, when=when, sat_ant_cache=cache) for s in sats}
    base_geo = {s.name: geometria.satellite_geometry(s.name, s.pair, s.bas.code1, base_station, base_rec,
                                                     base_frame, env, tracks[s.name], t_rx=t_bas) for s in sats}
    rover_xyz = np.array(rover.xyz, dtype=float)
    tide_rover = geometria.tide_series(rover_xyz, env)

    def rover_geometry(xyz):
        frame = geometria.site_frame(xyz, mjd)
        rec = xyz[None, :] + tide_rover + frame.rot[2][None, :] * rover.height_m
        geo = {s.name: geometria.satellite_geometry(s.name, s.pair, s.rov.code1, rover_station, rec, frame, env,
                                                    tracks[s.name], t_rx=t_rov) for s in sats}
        return frame, geo

    rover_frame, rover_geo = rover_geometry(rover_xyz)
    mask_rad = math.radians(mask_deg)

    # Arcos
    step(0.18, "Saltos de ciclo y arcos…")
    for s in sats:
        use = base_geo[s.name].ok & rover_geo[s.name].ok & (base_geo[s.name].elev >= mask_rad) & \
            (rover_geo[s.name].elev >= mask_rad)
        arc_r, bad_r = preproceso.detect_arcs(times, s.rov, use, proc)
        arc_b, bad_b = preproceso.detect_arcs(times, s.bas, use, proc)
        s.arc = preproceso.sd_arcs(arc_r, arc_b, times, MIN_ARC_S, proc)
    tools.check(cancel)

    # Arcos globales, grupos, componentes y referencias
    arcs = []                                 # (índice de satélite, número de arco local)
    arc_of = {}
    for k, s in enumerate(sats):
        for a in sorted(set(s.arc[s.arc >= 0].tolist())):
            arc_of[(k, a)] = len(arcs)
            arcs.append((k, a))
    if not arcs:
        raise InsufficientData("Ningún satélite tiene un arco limpio de 10 minutos o más en los dos receptores.")
    _check_geometry(sats, groups, times, proc, skipped, rov, bas, systems)
    arc_epochs = [np.flatnonzero(sats[k].arc == a) for k, a in arcs]
    ref_arcs, amb_index = _datum(arcs, arc_epochs, sats, rover_geo, len(times))
    n_amb = int(max(amb_index.values()) + 1) if amb_index else 0

    # Troposfera: retardo húmedo cenital y gradientes, lineales por tramos (nodos de cada estación)
    zwd_nodes, zwd_seg, zwd_frac = _nodes(times, TROP_STEP_S)
    grad_nodes, grad_seg, grad_frac = _nodes(times, GRAD_STEP_S)
    nz, ng = len(zwd_nodes), len(grad_nodes)
    # bloque: [ZWD rover (nz), ZWD base (nz), GN rover, GE rover, GN base, GE base (ng cada uno)]
    off = {"zr": 0, "zb": nz, "nr": 2 * nz, "er": 2 * nz + ng, "nb": 2 * nz + 2 * ng, "eb": 2 * nz + 3 * ng}
    layout = ajuste.Layout(n_trop=2 * nz + 4 * ng, n_amb=n_amb)
    seg_h = (zwd_nodes[1] - zwd_nodes[0]) / 3600.0
    grad_h = (grad_nodes[1] - grad_nodes[0]) / 3600.0
    constraints = []
    for key in ("zr", "zb"):
        constraints += ajuste.node_constraints(layout, off[key], nz, TROP_SIGMA_ABS_M,
                                               TROP_SIGMA_RW_M_PER_SQRT_H * math.sqrt(max(seg_h, 1e-3)))
    for key in ("nr", "er", "nb", "eb"):
        constraints += ajuste.node_constraints(layout, off[key], ng, GRAD_SIGMA_ABS_M,
                                               GRAD_SIGMA_RW_M_PER_SQRT_H * math.sqrt(max(grad_h, 1e-3)))
    group_ids = {g: i for i, g in enumerate(groups)}

    def build_rows(rgeo) -> ajuste.Rows:
        cols = {k: [] for k in ("cell", "value", "var", "los", "ti", "tc", "amb", "phase", "sat", "epoch")}
        for k, s in enumerate(sats):
            rg, bg = rgeo[s.name], base_geo[s.name]
            ep = np.flatnonzero(s.arc >= 0)
            if not len(ep):
                continue
            er, eb = rg.elev[ep], bg.elev[ep]
            var_zd = (1.0 + 1.0 / np.sin(er) ** 2) + (1.0 + 1.0 / np.sin(eb) ** 2)
            f_if = s.pair.alpha ** 2 + s.pair.beta ** 2
            gid = group_ids[s.group]
            amb = np.array([amb_index.get(arc_of[(k, a)], -1) for a in s.arc[ep]])
            # sesgo de código constante por arco (DCB entre clases): para que la media del arco no lo promedie como
            # ruido blanco, su varianza por época se multiplica por las épocas del arco (la media queda con σ_DCB)
            code_extra = 0.0
            if s.code_extra_var:
                arc_ids, arc_len = np.unique(s.arc[ep], return_counts=True)
                code_extra = s.code_extra_var * arc_len[np.searchsorted(arc_ids, s.arc[ep])]
            zs, zf, gs, gf = zwd_seg[ep], zwd_frac[ep], grad_seg[ep], grad_frac[ep]
            ti_cols, tc_cols = [], []
            for key, geo, sign in (("zr", rg, 1.0), ("zb", bg, -1.0)):
                ti_cols += [off[key] + zs, off[key] + zs + 1]
                tc_cols += [sign * geo.map_wet[ep] * (1 - zf), sign * geo.map_wet[ep] * zf]
            for kn, ke, geo, sign in (("nr", "er", rg, 1.0), ("nb", "eb", bg, -1.0)):
                el, az = geo.elev[ep], geo.azim[ep]
                mg = 1.0 / (np.sin(el) * np.tan(el) + 0.0032)          # Chen y Herring (1997)
                for key, comp in ((kn, np.cos(az)), (ke, np.sin(az))):
                    ti_cols += [off[key] + gs, off[key] + gs + 1]
                    tc_cols += [sign * mg * comp * (1 - gf), sign * mg * comp * gf]
            ti = np.stack(ti_cols, axis=1)
            tc = np.stack(tc_cols, axis=1)
            code = (s.rov.code_if[ep] - rg.model_code[ep]) - (s.bas.code_if[ep] - bg.model_code[ep])
            phase = (s.rov.phase_if[ep] - rg.model_phase[ep]) - (s.bas.phase_if[ep] - bg.model_phase[ep])
            for is_phase, value, sig in ((False, code, SIGMA_CODE_M), (True, phase, SIGMA_PHASE_M)):
                cols["cell"].append((ep * len(groups) + gid) * 2 + int(is_phase))
                cols["value"].append(value)
                cols["var"].append(var_zd * f_if * sig ** 2 + (0.0 if is_phase else code_extra))
                cols["los"].append(rg.los[ep])
                cols["ti"].append(ti)
                cols["tc"].append(tc)
                cols["amb"].append(amb if is_phase else np.full(len(ep), -1))
                cols["phase"].append(np.full(len(ep), is_phase))
                cols["sat"].append(np.full(len(ep), k))
                cols["epoch"].append(ep)
        cat = {k: np.concatenate(v) for k, v in cols.items()}
        return ajuste.Rows(cell=cat["cell"], value=cat["value"], var=cat["var"], los=cat["los"],
                           trop_idx=cat["ti"], trop_coef=cat["tc"], amb=cat["amb"], phase=cat["phase"],
                           sat=cat["sat"], epoch=cat["epoch"])

    # Solución de código para acercar el rover
    step(0.25, "Solución de código…")
    for _ in range(4):
        rows = build_rows(rover_geo)
        mask = ~rows.phase & np.isfinite(rows.value)
        sol = ajuste.solve(rows, layout, mask, constraints)
        dx = sol.x[:3]
        rover_xyz = rover_xyz + dx
        rover_frame, rover_geo = rover_geometry(rover_xyz)
        if np.linalg.norm(dx) < 0.05:
            break
    log.append(f"Solución de código: corrección {np.linalg.norm(dx):.2f} m en la última iteración.")
    pos_code = rover_xyz.copy()
    sigma_code = float(np.sqrt(np.max(np.diag(sol.n_inv[:3, :3])) * max(sol.sigma0, 1.0) ** 2))

    # Ajuste flotante con depuración
    step(0.35, "Ajuste flotante (libre de ionosfera)…")
    rows = build_rows(rover_geo)
    mask = np.isfinite(rows.value)
    rejected = 0
    sol = None
    for it in range(MAX_ITER + 4):
        tools.check(cancel)
        sol = ajuste.solve(rows, layout, mask, constraints)
        dx = sol.x[:3]
        if np.linalg.norm(dx) > 1e-4:
            rover_xyz = rover_xyz + dx
            rover_frame, rover_geo = rover_geometry(rover_xyz)
            rows = build_rows(rover_geo)
            continue
        norm = np.abs(sol.residuals) / (np.sqrt(rows.var) * sol.sigma0)
        norm[~mask] = 0.0
        norm = np.nan_to_num(norm)
        worst = float(norm.max()) if len(norm) else 0.0
        if worst <= OUTLIER_SIGMAS:
            break
        # se quitan los peores de cada tipo (más de 4σ, como mucho el 1 % por vuelta)
        cut = max(OUTLIER_SIGMAS, float(np.quantile(norm[mask], 0.99)))
        drop = norm > cut
        rejected += int(drop.sum())
        mask &= ~drop
    # sin desplazamiento pendiente: residuos y estadísticos del flotante
    rows = build_rows(rover_geo)
    sol = ajuste.solve(rows, layout, mask, constraints)
    x_float = sol.x.copy()
    pos_float = rover_xyz + x_float[:3]
    phase_res = [sol.residuals[(rows.phase) & (rows.amb == amb_index.get(arc_of[(k, a)], -2)) & mask]
                 for k, a in arcs]
    corr = preproceso.autocorr_factor(phase_res)
    s0 = sol.sigma0
    scale = s0 ** 2 * corr
    q_float = sol.n_inv * scale
    rms_phase = float(np.sqrt(np.nanmean(sol.residuals[rows.phase & mask] ** 2)))
    rms_code = float(np.sqrt(np.nanmean(sol.residuals[~rows.phase & mask] ** 2)))
    log.append(f"Flotante: σ0 = {s0:.2f}, factor de correlación temporal {corr:.1f}, RMS fase {rms_phase * 1000:.1f} "
               f"mm, código {rms_code:.2f} m, {int(mask.sum())} observaciones, {rejected} descartadas.")

    _check_float(pos_float, q_float[:3, :3], pos_code, sigma_code)
    length_km = float(np.linalg.norm(rover_xyz - base.xyz)) / 1000.0
    hours = max((times[-1] - times[0] + proc) / 3600.0, 0.05)
    min_hours = min_fix_hours(length_km, mask_deg)
    expected = "corta" if hours + 1e-6 < min_hours else "fallida"
    cov_float = realistic_covariance(q_float[:3, :3], pos_float, length_km, hours, precise, fixed=False,
                                     float_kind=expected)
    # la de la flotante «corta» es la que se compara con la fija (prueba de coherencia): la «fallida» solo se usa si
    # la sesión, que debía fijar, queda flotante
    cov_float_test = realistic_covariance(q_float[:3, :3], pos_float, length_km, hours, precise, fixed=False,
                                          float_kind="corta")
    result = LongResult(fixed=False, position=pos_float, covariance=cov_float, float_position=pos_float,
                        float_covariance=cov_float, n_amb=n_amb, sigma0_float=s0, correlation_factor=corr,
                        rms_phase_m=rms_phase, rms_code_m=rms_code, epochs=len(times),
                        start=gps_datetime(times[0]), end=gps_datetime(times[-1]), interval_s=proc,
                        satellites=sorted({sats[k].name for k, _ in arcs}), groups=list(groups.values()),
                        trop_step_h=seg_h, observations=int(mask.sum()), rejected=rejected, notes=notes, log=log,
                        precise=precise, length_km=length_km, hours=hours)
    trop_r = x_float[layout.trop0 + off["zr"]:layout.trop0 + off["zr"] + nz] + rover_frame.zwd
    trop_b = x_float[layout.trop0 + off["zb"]:layout.trop0 + off["zb"] + nz] + base_frame.zwd
    result.formal_covariance = q_float[:3, :3]
    result.grad_step_h = grad_h
    result.ztd_rover = [(gps_datetime(t), float(v)) for t, v in zip(zwd_nodes, trop_r)]
    result.ztd_base = [(gps_datetime(t), float(v)) for t, v in zip(zwd_nodes, trop_b)]

    # Ancho de carril
    step(0.7, "Ambigüedades del ancho de carril…")
    wl = _widelane(sats, arcs, arc_epochs, ref_arcs, amb_index, groups)
    result.n_wl_fixed = sum(1 for v in wl.values() if v is not None)
    log.append(f"Ancho de carril: {result.n_wl_fixed} de {n_amb} dobles diferencias fijas.")

    # Carril estrecho
    step(0.8, "Ambigüedades del carril estrecho (LAMBDA)…")
    cand = [(j, n_w) for j, n_w in wl.items() if n_w is not None]
    result.n_nl_candidates = len(cand)
    result.min_hours = min_hours
    if hours + 1e-6 < result.min_hours:
        if mask_deg > HIGH_MASK_DEG and min_hours > min_fix_hours(length_km):
            result.reason = (f"con máscara de elevación de {mask_deg:g}° la sesión de {hours * 60:.0f} min es corta "
                             f"para fijar: hacen falta {min_hours:g} h o más (sin satélites bajos la altura y la "
                             "troposfera no se separan; la validación dio fijos con error de varias σ)")
        else:
            result.reason = (f"la sesión de {hours * 60:.0f} min es corta para fijar a {length_km:.0f} km: hacen "
                             f"falta {result.min_hours * 60:.0f} min o más (con menos, la validación dio fijos con "
                             "error de varias σ)")
        return result
    if len(cand) < NL_MIN_FIXED:
        result.reason = (f"solo {len(cand)} ambigüedades con el ancho de carril fijo (mínimo {NL_MIN_FIXED})"
                         if n_amb else "sin dobles diferencias")
        return result
    arc_of_amb = {jj: ia for ia, jj in amb_index.items()}
    sel = np.array([layout.amb0 + j for j, _ in cand])
    lam = np.array([sats[arcs[arc_of_amb[j]][0]].pair.lam_nl for j, _ in cand])
    kap = np.array([sats[arcs[arc_of_amb[j]][0]].pair.kappa for j, _ in cand])
    nw = np.array([float(v) for _, v in cand])
    n1 = (x_float[sel] - kap * nw) / lam
    q_n1 = q_float[np.ix_(sel, sel)] / np.outer(lam, lam)
    # error sistemático por arco (troposfera e ionosfera residuales, multitrayectoria): ver BIAS_*
    comp_ref = _component_refs(arcs, arc_epochs, sats, ref_arcs)
    arc_bias = {}
    for i, (k, _) in enumerate(arcs):
        ep = arc_epochs[i]
        m_arc = float(np.mean(0.5 / np.sin(rover_geo[sats[k].name].elev[ep]) +
                              0.5 / np.sin(base_geo[sats[k].name].elev[ep])))
        arc_bias[i] = (BIAS_B0_M + BIAS_B1_M * length_km / 100.0) * m_arc
    q_bias = np.zeros_like(q_n1)
    for r_, (j, _) in enumerate(cand):
        ia = arc_of_amb[j]
        for c_, (j2, _) in enumerate(cand):
            ib = arc_of_amb[j2]
            val = 0.0
            if comp_ref[ia] == comp_ref[ib]:
                val += arc_bias[comp_ref[ia]] ** 2
            if ia == ib:
                val += arc_bias[ia] ** 2
            q_bias[r_, c_] = val / (lam[r_] * lam[c_])
    q_n1 = q_n1 + q_bias
    for (j, v), value, sg in zip(cand, n1, np.sqrt(np.diag(q_n1))):
        ia = arc_of_amb[j]
        k = arcs[ia][0]
        ep = arc_epochs[ia]
        el = np.degrees(rover_geo[sats[k].name].elev[ep])
        result.ambiguities.append(AmbiguityRow(
            satellite=sats[k].name, reference=sats[arcs[comp_ref[ia]][0]].name, start=gps_datetime(times[ep[0]]),
            end=gps_datetime(times[ep[-1]]), epochs=len(ep), elevation=(float(el.min()), float(el.max())),
            widelane=int(v), narrowlane_float=float(value), narrowlane_sigma=float(sg)))
    try:
        fix = partial_fix(n1, q_n1, min_success=NL_MIN_SUCCESS, min_ratio=NL_MIN_RATIO, min_count=NL_MIN_FIXED,
                          max_sq_prob=0.999, chi2_ppf=chi2_ppf)
    except LambdaError as error:
        result.reason = f"la búsqueda entera falló ({error})"
        return result
    result.ratio = fix.ratio if fix.n_fixed else None
    result.success = fix.success if fix.n_fixed else fix.full_success
    log.append(f"Carril estrecho: {len(cand)} candidatas, {fix.n_fixed} combinaciones con tasa de éxito "
               f"{100 * fix.success:.3f} %, razón {fix.ratio:.2f}.")
    if not fix.ok:
        result.reason = fix.reason
        return result
    result.sq_norm, result.sq_norm_limit = fix.sq_norm, chi2_ppf(0.999, fix.n_fixed)
    # Solución fija: ajuste condicionado
    g = np.zeros((fix.n_fixed, layout.n))
    g[:, sel] = (fix.z_cols.T / lam[None, :])
    h = fix.z_fixed + fix.z_cols.T @ (kap * nw / lam)
    misclosure = g @ x_float - h
    gqg = g @ q_float @ g.T
    gain = q_float @ g.T @ np.linalg.inv(gqg)
    x_fix = x_float - gain @ misclosure
    q_fix = q_float - gain @ g @ q_float
    delta = float(misclosure @ np.linalg.solve(g @ sol.n_inv @ g.T, misclosure))
    s0_fix = math.sqrt((sol.vtpv + delta) / max(1, sol.dof + fix.n_fixed))
    q_fix = q_fix * (s0_fix ** 2 / s0 ** 2 if s0 > 0 else 1.0)
    pos_fix = rover_xyz + x_fix[:3]
    jump = _fix_float_jump(pos_fix, pos_float, cov_float_test)
    de, dn, du = jump[0] * 1000.0
    log.append(f"Fija: σ0 = {s0_fix:.2f}, cambio respecto de la flotante "
               f"{np.linalg.norm(pos_fix - pos_float) * 1000:.1f} mm (E {de:+.1f}, N {dn:+.1f}, U {du:+.1f}; hasta "
               f"{np.max(jump[1]):.1f}σ de la flotante).")
    worst = int(np.argmax(jump[1]))
    if jump[1][worst] > FIX_FLOAT_K:
        name = ("en el este", "en el norte", "en la vertical")[worst]
        result.reason = (f"la solución fija se aleja {abs(jump[0][worst]) * 1000:.0f} mm de la flotante {name} "
                         f"({jump[1][worst]:.1f} veces su σ; el máximo es {FIX_FLOAT_K:g}): las ambigüedades no son "
                         "confiables aunque pasen la prueba de razón")
        result.n_nl_fixed = fix.n_fixed
        return result
    result.fixed = True
    result.position = pos_fix
    result.covariance = realistic_covariance(q_fix[:3, :3], result.position, length_km, hours, precise, fixed=True)
    result.float_covariance = cov_float_test
    result.formal_covariance = q_fix[:3, :3]
    result.n_nl_fixed = fix.n_fixed
    result.sigma0_fixed = s0_fix
    return result


def align_epochs(t_rov: np.ndarray, t_bas: np.ndarray, interval_s: float, rov_interval: float, bas_interval: float,
                 tol_s: float = EPOCH_TOL_S) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Épocas comunes de dos receptores en la rejilla del intervalo de proceso.

    El intervalo de proceso es el mayor entre el pedido y los de los dos receptores (un rover a 1 Hz contra una base
    cada 15 s se procesa cada 15 s); cada etiqueta se lleva al múltiplo más cercano de ese intervalo si está a menos
    de `tol_s` (si dos caen en el mismo, la más cercana). Devuelve las épocas nominales, las filas de cada receptor y
    el intervalo. Huecos, épocas sueltas que falten e intervalos distintos quedan fuera sin más."""
    rov_interval, bas_interval = (round(float(v or 0.0), 2) for v in (rov_interval, bas_interval))
    proc = max(float(interval_s), rov_interval, bas_interval, 1.0)
    # un intervalo de datos que no es divisor del de proceso (10 s contra 15 s): la rejilla común es su mínimo común
    for other in (rov_interval, bas_interval):
        if other and other >= 1.0 and abs(math.remainder(proc, other)) > tol_s:
            a, b = int(round(proc)), int(round(other))
            if abs(proc - a) < 1e-6 and abs(other - b) < 1e-6:
                proc = float(a * b // math.gcd(a, b))

    def snap(t):
        k = np.round(t / proc)
        off = np.abs(t - k * proc)
        rows = {}
        for i in np.flatnonzero(off <= tol_s):
            key = int(k[i])
            if key not in rows or off[i] < off[rows[key]]:
                rows[key] = int(i)
        return rows

    sr, sb = snap(np.asarray(t_rov, dtype=float)), snap(np.asarray(t_bas, dtype=float))
    keys = sorted(set(sr) & set(sb))
    times = np.array([k * proc for k in keys], dtype=float)
    ir = np.array([sr[k] for k in keys], dtype=int)
    ib = np.array([sb[k] for k in keys], dtype=int)
    return times, ir, ib, proc


def _few_epochs_text(rov, bas, n: int, proc: float) -> str:
    """Por qué no hay épocas comunes suficientes, dicho para el usuario."""
    if not n and len(rov.times) and len(bas.times):
        overlap = min(rov.times[-1], bas.times[-1]) - max(rov.times[0], bas.times[0])
        if overlap > 0:
            return (f"Las épocas del rover y de la base no coinciden en el tiempo (el rover observa cada "
                    f"{rov.interval:g} s y la base cada {bas.interval:g} s, sin épocas comunes cada {proc:g} s): "
                    "revisa los intervalos de registro.")
        return "El rover y la base no observaron al mismo tiempo."
    return (f"Solo hay {n} épocas comunes entre el rover y la base cada {proc:g} s: hacen falta al menos 20 "
            "(unos 5 minutos) para la línea larga.")


def min_fix_hours(length_km: float, mask_deg: float = 15.0) -> float:
    """Duración mínima para fijar el carril estrecho (`MIN_FIX_HOURS`; con máscara alta, `HIGH_MASK_MIN_HOURS`)."""
    hours = MIN_FIX_HOURS[1] if length_km >= MIN_FIX_LONG_KM else MIN_FIX_HOURS[0]
    if mask_deg > HIGH_MASK_DEG:
        hours = max(hours, HIGH_MASK_MIN_HOURS)
    return hours


def error_floor_enu(length_km: float, hours: float, precise: bool, fixed: bool,
                    float_kind: str = "corta") -> np.ndarray:
    """σ (E, N, U) del error que no ve la covarianza formal (`ERROR_FLOOR`, y `FLOAT_FACTOR` en la flotante)."""
    model = ERROR_FLOOR["fixed" if fixed else "float"]
    scale = length_km / 100.0
    out = []
    for comp in ("h", "h", "v"):
        a, b, c = model[comp]
        out.append(a + b * scale / math.sqrt(max(hours, 0.05)) + (0.0 if precise else c * scale))
    floor = np.array(out)
    return floor if fixed else floor * np.array(FLOAT_FACTOR[float_kind])


def realistic_covariance(formal_ecef: np.ndarray, xyz: np.ndarray, length_km: float, hours: float, precise: bool,
                         *, fixed: bool, float_kind: str = "corta") -> np.ndarray:
    """Covarianza formal (escalada por σ0² y la correlación temporal) más el error que no ve (`ERROR_FLOOR`)."""
    floor = error_floor_enu(length_km, hours, precise, fixed, float_kind)
    return formal_ecef + covariance_enu_to_ecef(xyz, np.diag(floor ** 2))


def _fix_float_jump(pos_fix: np.ndarray, pos_float: np.ndarray, cov_float: np.ndarray
                    ) -> tuple[np.ndarray, np.ndarray]:
    """Diferencia fija − flotante en E, N, U (m) y en veces la σ realista de la flotante."""
    d = np.array(ecef_delta_to_enu(pos_float, pos_fix - pos_float))
    sig = np.sqrt(np.maximum(np.diag(covariance_ecef_to_enu(pos_float, cov_float)), 1e-12))
    return d, np.abs(d) / sig


def _check_float(pos_float: np.ndarray, q_pos: np.ndarray, pos_code: np.ndarray, sigma_code: float) -> None:
    """Rechazo de una flotante sin fundamento (con muy pocos satélites el ajuste da kilómetros con σ del mismo orden):
    geometría de la solución de código, σ formal de la flotante y coherencia de la fase con el código."""
    if not np.all(np.isfinite(pos_float)) or not np.all(np.isfinite(q_pos)):
        raise InsufficientData("El ajuste de la línea base no tiene solución con estos datos (la geometría de los "
                               "satélites comunes no alcanza).")
    if sigma_code > MAX_CODE_SIGMA_M:
        raise InsufficientData(f"La geometría de los satélites comunes no alcanza: la solución de código tiene una σ "
                               f"de {sigma_code:.0f} m (máximo {MAX_CODE_SIGMA_M:g} m).")
    sig = float(np.sqrt(max(0.0, float(np.max(np.diag(q_pos))))))
    if sig > MAX_FLOAT_SIGMA_M:
        raise InsufficientData(f"La geometría de los satélites comunes no alcanza: la solución tiene una σ de "
                               f"{_length_text(sig)} (máximo {MAX_FLOAT_SIGMA_M:g} m).")
    gap = float(np.linalg.norm(pos_float - pos_code))
    if gap > MAX_CODE_PHASE_M:
        raise InsufficientData(f"La solución de las fases se aleja {_length_text(gap)} de la de código (máximo "
                               f"{MAX_CODE_PHASE_M:g} m): los datos de los dos receptores no son coherentes.")


def _length_text(m: float) -> str:
    return f"{m / 1000:,.0f} km".replace(",", " ") if m >= 10_000 else f"{m:.1f} m"


def _check_geometry(sats, groups, times, proc, skipped, rov, bas, systems) -> None:
    """Dobles diferencias por época: hacen falta `MIN_DD_PER_EPOCH` (cinco satélites de un sistema, o 3 + 3 de dos)
    en al menos la mitad de las épocas y en 20 o más; si no, `InsufficientData` con el porqué."""
    n_dd = np.zeros(len(times), dtype=int)
    present: dict[str, np.ndarray] = {}
    for s in sats:
        present.setdefault(s.group, np.zeros(len(times), dtype=int))
        present[s.group] += (s.arc >= 0)
    for count in present.values():
        n_dd += np.maximum(count - 1, 0)
    good = int(np.sum(n_dd >= MIN_DD_PER_EPOCH))
    if good >= max(20, 0.5 * len(times)):
        return
    used = sorted({s.name for s in sats if np.any(s.arc >= 0)})
    per_epoch = int(np.median(sum(present.values()))) if present else 0
    text = (f"Solo hay {len(used)} satélites comunes útiles entre el rover y la base ({per_epoch} a la vez, mediana "
            f"por época) y la línea base necesita al menos {MIN_DD_PER_EPOCH + 1} a la vez en la mitad de la sesión")
    why = _signals_text(rov, bas, systems)
    if why:
        text += why
    elif skipped:
        lost = sum(len(v) for v in skipped.values())
        text += f" ({lost} satélites quedan fuera: " + "; ".join(skipped) + ")"
    raise InsufficientData(text + ".")


def _band_names(system: str, types: list[str]) -> list[str]:
    bands = sorted({t[1] for t in types if t[0] == "L"} & {b for p in senales.PAIRS.get(system, []) for b in p})
    return [senales._band_text(system, b) for b in bands]


def _signals_text(rov, bas, systems: str) -> str:
    """Por qué los receptores no comparten parejas de frecuencias, dicho por sistema («el rover registra L1 y L2 y la
    base solo L1»); vacío si las comparten."""
    parts = []
    for system in "GEC":
        if system not in systems:
            continue
        r, b = rov.types.get(system, []), bas.types.get(system, [])
        name = senales.SYSTEM_NAMES[system]
        if not r and not b:
            continue
        if not r or not b:
            who = "el rover" if not r else "la base"
            parts.append(f"{who} no registra {name}")
            continue
        ok = any(senales.candidates(system, b1, r) and senales.candidates(system, b1, b) and
                 senales.candidates(system, b2, r) and senales.candidates(system, b2, b)
                 for b1, b2 in senales.PAIRS.get(system, []))
        if not ok:
            parts.append(f"en {name} el rover registra {', '.join(_band_names(system, r)) or 'nada útil'} y la base "
                         f"{', '.join(_band_names(system, b)) or 'nada útil'}: no comparten dos frecuencias")
    return (": " + "; ".join(parts)) if parts else ""


CLASS_NAMES = {"P": "P(Y)", "CA": "C/A"}


def _mixed_bands(choice) -> str:
    """«L2C en el rover y P(Y) en la base» (las bandas con clases distintas)."""
    def name(c):
        return CLASS_NAMES.get(c, c)
    return "; ".join(f"{name(c[5])} en el rover y {name(c[6])} en la base" for c in choice if c[5] != c[6])


def _nodes(times: np.ndarray, step_s: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Nodos de una función lineal por tramos que cubre la sesión, y el tramo y la fracción de cada época."""
    duration = times[-1] - times[0]
    n = max(2, int(math.ceil(duration / step_s - 1e-6)) + 1)
    nodes = np.linspace(times[0], times[-1], n)
    seg = np.clip(np.searchsorted(nodes, times, side="right") - 1, 0, n - 2)
    frac = (times - nodes[seg]) / (nodes[seg + 1] - nodes[seg])
    return nodes, seg, frac


def _group_text(key: str, pair: Pair) -> str:
    return senales.group_label(key.split(":")[0])


def _bds_generation(sat: str, when: datetime) -> int | None:
    from .antena import satellite_antenna
    ant = satellite_antenna(sat, when)
    prn = int(sat[1:])
    if ant is not None:
        kind = ant.type.upper()
        if "-2G" in kind or "-3G" in kind:
            return None
        if "BEIDOU-2" in kind:
            return 2
        if "BEIDOU-3" in kind:
            return 3
    if prn in senales.BDS_GEO:
        return None
    return senales.bds_generation(prn)


def _datum(arcs, arc_epochs, sats, rover_geo, n_epochs) -> tuple[dict, dict]:
    """Arco de referencia por grupo y componente conexa (el más largo) e índice de ambigüedad de los demás."""
    by_group: dict[str, list[int]] = {}
    for i, (k, _) in enumerate(arcs):
        by_group.setdefault(sats[k].group, []).append(i)
    parent = list(range(len(arcs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for members in by_group.values():
        present: dict[int, int] = {}
        for i in members:
            for e in arc_epochs[i]:
                e = int(e)
                if e in present:
                    a, b = find(i), find(present[e])
                    if a != b:
                        parent[a] = b
                else:
                    present[e] = i
    comps: dict[tuple[str, int], list[int]] = {}
    for g, members in by_group.items():
        for i in members:
            comps.setdefault((g, find(i)), []).append(i)
    refs = {}
    amb_index = {}
    for (g, _), members in comps.items():
        def score(i):
            k, _ = arcs[i]
            el = rover_geo[sats[k].name].elev[arc_epochs[i]]
            return (len(arc_epochs[i]), float(np.mean(el)))
        ref = max(members, key=score)
        refs[(g, ref)] = ref
        for i in members:
            if i != ref:
                amb_index[i] = len(amb_index)
    return refs, amb_index


def _widelane(sats, arcs, arc_epochs, refs, amb_index, groups) -> dict[int, int | None]:
    """Doble diferencia del ancho de carril de cada ambigüedad (contra el arco de referencia de su componente)."""
    stats = {}
    for i, (k, a) in enumerate(arcs):
        s = sats[k]
        ep = arc_epochs[i]
        sd = s.rov.mw[ep] - s.bas.mw[ep]
        sd = sd[np.isfinite(sd)]
        if len(sd) < 10:
            stats[i] = None
            continue
        f = preproceso.autocorr_factor([sd])
        stats[i] = (float(np.mean(sd)), float(np.std(sd)) * math.sqrt(f / len(sd)))
    out: dict[int, int | None] = {}
    comp_ref = _component_refs(arcs, arc_epochs, sats, refs)
    for i, j in amb_index.items():
        k = arcs[i][0]
        group = groups[sats[k].group]
        ref = comp_ref[i]
        if not group.ar or stats.get(i) is None or stats.get(ref) is None:
            out[j] = None
            continue
        m, sg = stats[i]
        mr, sr = stats[ref]
        w = m - mr
        sigma = math.hypot(sg, sr) * WL_SIGMA_FACTOR
        n = round(w)
        prob = rounding_probability(w - n, sigma)
        out[j] = int(n) if prob >= WL_MIN_PROB else None
    return out


def _component_refs(arcs, arc_epochs, sats, refs) -> dict[int, int]:
    """Para cada arco, el arco de referencia de su componente (por grupo, conectividad por épocas)."""
    ref_list = [ref for (_, ref) in refs]
    by_group: dict[str, list[int]] = {}
    for i, (k, _) in enumerate(arcs):
        by_group.setdefault(sats[k].group, []).append(i)
    out = {}
    for g, members in by_group.items():
        refs_g = [r for r in ref_list if sats[arcs[r][0]].group == g]
        # conectividad: búsqueda en anchura desde cada referencia
        epoch_sets = {i: set(int(e) for e in arc_epochs[i]) for i in members}
        for r in refs_g:
            seen = {r}
            frontier = [r]
            while frontier:
                cur = frontier.pop()
                for other in members:
                    if other not in seen and epoch_sets[cur] & epoch_sets[other]:
                        seen.add(other)
                        frontier.append(other)
            for i in seen:
                out[i] = r
    return out
