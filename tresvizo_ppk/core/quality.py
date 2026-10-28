"""Control de calidad de las observaciones, al estilo de TEQC/Anubis.

Por archivo: épocas registradas y esperadas, huecos, satélites y señales; por satélite, saltos de ciclo (bandera de
pérdida de enganche del receptor más detección propia con las combinaciones libre de geometría y de
Melbourne-Wübbena); por señal de código, multitrayectoria MP (código menos fase, sin ionosfera ni geometría, con la
media de cada arco continuo restada), en metros RMS.
"""
from __future__ import annotations

import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..model import DataQuality, ObservationSet
from . import tools
from .rinex import RinexHeader, epoch_values, iter_epochs, read_header, rinex_time_to_utc

C_LIGHT = 299_792_458.0
SYSTEM_NAMES = {"G": "GPS", "R": "GLONASS", "E": "Galileo", "C": "BeiDou", "J": "QZSS", "S": "SBAS", "I": "NavIC"}

# Frecuencias por sistema y banda (dígito del código RINEX), en Hz. GLONASS FDMA: base + k·paso.
_FREQ = {
    "G": {"1": 1575.42e6, "2": 1227.60e6, "5": 1176.45e6},
    "E": {"1": 1575.42e6, "5": 1176.45e6, "7": 1207.14e6, "8": 1191.795e6, "6": 1278.75e6},
    "C": {"1": 1575.42e6, "2": 1561.098e6, "5": 1176.45e6, "7": 1207.14e6, "8": 1191.795e6, "6": 1268.52e6},
    "J": {"1": 1575.42e6, "2": 1227.60e6, "5": 1176.45e6, "6": 1278.75e6},
    "S": {"1": 1575.42e6, "5": 1176.45e6},
    "I": {"5": 1176.45e6, "9": 2492.028e6},
}
_GLO = {"1": (1602.0e6, 0.5625e6), "2": (1246.0e6, 0.4375e6), "3": (1202.025e6, 0.0), "4": (1600.995e6, 0.0),
        "6": (1248.06e6, 0.0)}
# Par de bandas para las combinaciones, por orden de preferencia.
_PAIRS = {"G": [("1", "2"), ("1", "5")], "R": [("1", "2")], "E": [("1", "5"), ("1", "7")],
          "C": [("2", "6"), ("2", "7"), ("1", "5")], "J": [("1", "2"), ("1", "5")], "I": [("5", "9")]}
# Preferencia de atributo de la señal (la primera presente se usa).
_ATTR_ORDER = "CWPYSLXQIBDZAEMN"

GF_SLIP_M = 0.05           # salto en la combinación libre de geometría (m) entre épocas consecutivas
MW_SLIP_CYCLES = 4.0       # salto en Melbourne-Wübbena (ciclos de banda ancha)
ARC_GAP_FACTOR = 3.0       # un hueco de más de 3 intervalos corta el arco
MIN_ARC_EPOCHS = 10        # arcos más cortos no entran en la multitrayectoria


def frequency(system: str, band: str, glonass_k: int | None = None) -> float | None:
    if system == "R":
        base = _GLO.get(band)
        if base is None:
            return None
        if base[1] and glonass_k is None:
            return None
        return base[0] + (glonass_k or 0) * base[1]
    return _FREQ.get(system, {}).get(band)


def _codes_v2_to_v3(code: str) -> tuple[str, str, str]:
    """«L1», «C1», «P2»… → (tipo, banda, atributo). En RINEX 2 el atributo se infiere: C/A, P, etc."""
    kind = code[0]
    band = code[1:2]
    if kind == "P":
        return "C", band, "W"
    if kind == "C":
        return "C", band, "C"
    return kind, band, "?"


@dataclass
class _Arc:
    last_time: datetime | None = None
    gf: float | None = None
    mw_mean: float = 0.0
    mw_n: int = 0
    mp: dict[str, list[float]] = field(default_factory=dict)


@dataclass
class _Accumulator:
    sums: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add_arc(self, values: dict[str, list[float]]) -> None:
        for signal, series in values.items():
            if len(series) < MIN_ARC_EPOCHS:
                continue
            mean = sum(series) / len(series)
            self.sums[signal] += sum((v - mean) ** 2 for v in series)
            self.counts[signal] += len(series)

    def rms(self) -> dict[str, float]:
        return {s: math.sqrt(self.sums[s] / self.counts[s]) for s in sorted(self.counts) if self.counts[s]}


def _pick(values: dict[str, tuple[float, int]], kind: str, band: str, v3: bool) -> tuple[str, float, int] | None:
    best: tuple[int, str, float, int] | None = None
    for code, (value, lli) in values.items():
        if v3:
            if len(code) < 3 or code[0] != kind or code[1] != band:
                continue
            rank = _ATTR_ORDER.find(code[2])
            rank = rank if rank >= 0 else 99
        else:
            k, b, attr = _codes_v2_to_v3(code)
            if k != kind or b != band:
                continue
            rank = 0 if (code[0] == "P") else 1
        if best is None or rank < best[0]:
            best = (rank, code, value, lli)
    return None if best is None else (best[1], best[2], best[3])


def assess(obs: ObservationSet, cancel: threading.Event | None = None,
           progress: tools.Progress | None = None) -> DataQuality:
    """Calidad de un juego de observaciones (el primer archivo de `obs.obs`, ya unido)."""
    quality = DataQuality()
    if not obs.obs:
        quality.warnings.append("No hay archivo de observación.")
        return quality
    header = read_header(obs.obs[0])
    _assess_file(header, quality, cancel, progress)
    return quality


def _assess_file(header: RinexHeader, quality: DataQuality, cancel, progress) -> None:
    v3 = header.major >= 3
    interval = header.interval
    times: list[datetime] = []
    sats: set[str] = set()
    signals: dict[str, set[str]] = defaultdict(set)
    slips: dict[str, int] = defaultdict(int)
    slip_times: dict[str, list[datetime]] = defaultdict(list)
    tracks: dict[str, list[list[datetime]]] = defaultdict(list)    # satélite → [[inicio, fin], …] (línea de tiempo)
    arcs: dict[str, _Arc] = {}
    accumulator = _Accumulator()
    glonass_k = dict(header.glonass_slots)
    no_glonass_k: set[str] = set()
    epoch_counter = 0
    for block in iter_epochs(header):
        if block.flag > 1:
            continue
        epoch_counter += 1
        if epoch_counter % 500 == 0:
            tools.check(cancel)
            if progress:
                progress(min(0.99, 0.5), f"Control de calidad: {epoch_counter} épocas…")
        times.append(block.time)
        values_by_sat = epoch_values(header, block)
        for sat, values in values_by_sat.items():
            if not values:
                continue
            system = sat[0]
            sats.add(sat)
            for code in values:
                signals[system].add(code if v3 else code)
            arc = arcs.get(sat)
            gap_limit = (interval or 30.0) * ARC_GAP_FACTOR
            spans = tracks[sat]
            if spans and (block.time - spans[-1][1]).total_seconds() <= gap_limit:
                spans[-1][1] = block.time
            else:
                spans.append([block.time, block.time])
            if arc is None or arc.last_time is None or (block.time - arc.last_time).total_seconds() > gap_limit:
                if arc is not None:
                    accumulator.add_arc(arc.mp)
                arc = arcs[sat] = _Arc()
            dt = (block.time - arc.last_time).total_seconds() if arc.last_time else 0.0
            arc.last_time = block.time
            pair = _find_pair(system, values, v3)
            if pair is None:
                continue
            (b1, l1, lli1, p1code, p1), (b2, l2, lli2, p2code, p2) = pair
            k = glonass_k.get(sat)
            f1, f2 = frequency(system, b1, k), frequency(system, b2, k)
            if f1 is None or f2 is None:
                if system == "R":
                    no_glonass_k.add(sat)
                continue
            lam1, lam2 = C_LIGHT / f1, C_LIGHT / f2
            phi1, phi2 = l1 * lam1, l2 * lam2           # fase en metros
            gf = phi1 - phi2
            lam_wl = C_LIGHT / (f1 - f2)
            mw = ((f1 * phi1 - f2 * phi2) / (f1 - f2) - (f1 * p1 + f2 * p2) / (f1 + f2)) / lam_wl
            slip = bool((lli1 & 1) or (lli2 & 1))
            if arc.gf is not None and abs(gf - arc.gf) > GF_SLIP_M + 0.002 * dt:
                slip = True
            if arc.mw_n >= 3 and abs(mw - arc.mw_mean) > MW_SLIP_CYCLES:
                slip = True
            if slip and arc.gf is not None:
                slips[sat] += 1
                slip_times[sat].append(block.time)
                accumulator.add_arc(arc.mp)
                arc.mp = {}
                arc.mw_mean, arc.mw_n = mw, 1
            else:
                arc.mw_n += 1
                arc.mw_mean += (mw - arc.mw_mean) / arc.mw_n
            arc.gf = gf
            alpha = (f1 / f2) ** 2
            mp1 = p1 - (1 + 2 / (alpha - 1)) * phi1 + (2 / (alpha - 1)) * phi2
            mp2 = p2 - (2 * alpha / (alpha - 1)) * phi1 + (2 * alpha / (alpha - 1) - 1) * phi2
            arc.mp.setdefault(f"{SYSTEM_NAMES.get(system, system)} {p1code}", []).append(mp1)
            arc.mp.setdefault(f"{SYSTEM_NAMES.get(system, system)} {p2code}", []).append(mp2)
    for arc in arcs.values():
        accumulator.add_arc(arc.mp)

    quality.epochs = len(times)
    if times:
        if not interval:
            steps = sorted((b - a).total_seconds() for a, b in zip(times, times[1:]) if b > a)
            interval = steps[len(steps) // 2] if steps else None
        if interval:
            quality.expected_epochs = int(round((times[-1] - times[0]).total_seconds() / interval)) + 1
            limit = max(interval * 1.5, interval + 0.5)
            for a, b in zip(times, times[1:]):
                if (b - a).total_seconds() > limit:
                    quality.gaps.append((rinex_time_to_utc(a + timedelta(seconds=interval), header.time_system),
                                         rinex_time_to_utc(b - timedelta(seconds=interval), header.time_system)))
    quality.satellites = sorted(sats, key=lambda s: ("GRECJSI".find(s[0]), s))
    quality.signals = {SYSTEM_NAMES.get(sys_, sys_): sorted(codes, key=lambda c: (c[1:2], c[0], c[2:]))
                       for sys_, codes in sorted(signals.items(), key=lambda kv: "GRECJSI".find(kv[0]))}
    quality.cycle_slips = {sat: slips[sat] for sat in quality.satellites if slips.get(sat)}
    quality.multipath_m = {k: round(v, 4) for k, v in accumulator.rms().items()}
    utc = lambda t: rinex_time_to_utc(t, header.time_system)  # noqa: E731
    quality.tracks = {sat: [(utc(a), utc(b)) for a, b in tracks[sat]] for sat in quality.satellites if tracks.get(sat)}
    quality.slip_times = {sat: [utc(t) for t in slip_times[sat]] for sat in quality.satellites if slip_times.get(sat)}
    quality.interval_s = interval

    # Avisos
    if quality.epochs == 0:
        quality.warnings.append("El archivo no tiene épocas de observación.")
        return
    if quality.expected_epochs and quality.epochs < 0.95 * quality.expected_epochs:
        missing = quality.expected_epochs - quality.epochs
        quality.warnings.append(f"Faltan {missing} de {quality.expected_epochs} épocas "
                                f"({100.0 * missing / quality.expected_epochs:.1f} %).")
    if quality.gaps:
        longest = max((b - a).total_seconds() for a, b in quality.gaps) + (interval or 0)
        quality.warnings.append(f"{len(quality.gaps)} hueco(s) en los datos; el más largo de {longest:.0f} s.")
    total_slips = sum(quality.cycle_slips.values())
    obs_per_slip = (quality.epochs * max(1, len(quality.satellites)) / total_slips) if total_slips else None
    if obs_per_slip is not None and obs_per_slip < 100:
        quality.warnings.append(f"Muchos saltos de ciclo ({total_slips}; uno cada {obs_per_slip:.0f} "
                                "observaciones): obstrucciones, multitrayectoria o interferencia.")
    high_mp = [f"{k} {v:.2f} m" for k, v in quality.multipath_m.items() if v > 0.5]
    if high_mp:
        quality.warnings.append("Multitrayectoria alta: " + ", ".join(high_mp) + ".")
    dual = any(len({c[1] for c in codes if c[0] == "L"}) >= 2 for codes in signals.values())
    if not dual:
        quality.warnings.append("Solo hay fase en una frecuencia: las líneas base largas no se podrán resolver bien.")
    if no_glonass_k:
        quality.warnings.append("Sin canal de frecuencia de " + str(len(no_glonass_k)) + " satélite(s) GLONASS en la "
                                "cabecera: no entran en la detección de saltos ni en la multitrayectoria.")


def _find_pair(system: str, values: dict[str, tuple[float, int]], v3: bool):
    for b1, b2 in _PAIRS.get(system, []):
        l1 = _pick(values, "L", b1, v3)
        l2 = _pick(values, "L", b2, v3)
        c1 = _pick(values, "C", b1, v3)
        c2 = _pick(values, "C", b2, v3)
        if l1 and l2 and c1 and c2:
            return (b1, l1[1], l1[2], c1[0], c1[1]), (b2, l2[1], l2[2], c2[0], c2[1])
    return None


def overlap_warnings(rover: ObservationSet, base: ObservationSet, base_name: str) -> list[str]:
    """Avisos de traslape entre rover y base (tiempos UTC de los ObservationSet)."""
    warnings: list[str] = []
    if not (rover.start and rover.end and base.start and base.end):
        return warnings
    start = max(rover.start, base.start)
    end = min(rover.end, base.end)
    rover_span = (rover.end - rover.start).total_seconds()
    common = (end - start).total_seconds()
    if common <= 0:
        warnings.append(f"La base {base_name} no tiene datos en el horario del rover "
                        f"({rover.start:%Y-%m-%d %H:%M}–{rover.end:%H:%M} UTC).")
    elif rover_span > 0 and common < 0.98 * rover_span:
        warnings.append(f"La base {base_name} cubre solo {100 * common / rover_span:.0f} % de la sesión del rover.")
    if base.interval_s and rover.interval_s and base.interval_s > rover.interval_s * 1.01:
        warnings.append(f"La base {base_name} registra cada {base.interval_s:g} s y el rover cada "
                        f"{rover.interval_s:g} s: se procesa a {base.interval_s:g} s.")
    return warnings


def header_of(obs: ObservationSet) -> RinexHeader:
    return read_header(obs.obs[0])
