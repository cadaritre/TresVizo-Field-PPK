"""Conversión de una sesión grabada (`.bin` completa o `.part` interrumpida) a RINEX 3.04.

Tres pasadas sobre las tramas ya separadas, para no guardar en memoria todas las observaciones de
una sesión de 64 MiB:
1. Reconocimiento: metadatos de sesión, fuente de observaciones, referencia de tiempo, GGA/RMC,
   posición de estación (1005/1006).
2. Decodificación para la cabecera: tipos de observación presentes, primera y última época,
   intervalo, canales GLONASS y efemérides.
3. Escritura: se decodifica otra vez, se calcula el indicador de pérdida de enganche (LLI) y se
   escribe cada época.
"""
from __future__ import annotations

import collections
import datetime as _dt
import math
import os
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from . import PROGRAM, gnsstime, rtcm3, unicore
from .framing import META_PREFIX, Frame, FrameStats, parse_session_metadata, split_frames, find_session_metadata
from .geodesy import ecef_to_geodetic, geodetic_to_ecef
from .model import Epoch, GlonassEphemeris, KeplerEphemeris, SignalObs
from .nmea import parse_gga, parse_rmc_datetime
from .products import product
from .segments import chain_from_files, find_chain, join
from .rinex import ObsHeader, epoch_line, observation_line, phase_shift_for, write_nav
from .signals import SYSTEM_NAMES, SYSTEM_ORDER, code_sort_key

MAX_ANTENNA_HEIGHT_M = 100.0
EPHEMERIS_IDS = (unicore.ID_GPSEPH, unicore.ID_GLOEPH, unicore.ID_BDSEPH, unicore.ID_GALEPH, unicore.ID_QZSSEPH)
RTCM_EPHEMERIS = (1019, 1020, 1042, 1044, 1045, 1046)


class ConversionError(Exception):
    """Error con un mensaje para el usuario, en español."""


@dataclass
class Options:
    antenna_height_m: float | None = None     # de la marca a la base del receptor (asiento de 5/8")
    marker: str | None = None
    out_dir: Path | None = None
    model: str | None = None                  # solo si la grabación no trae metadatos
    date: _dt.date | None = None              # solo para RTCM sin otra referencia de fecha
    observer: str = ""
    agency: str = ""
    # Una grabación larga rota en tramos de 64 MiB (GNS2-01): unir los tramos hermanos que estén junto al
    # archivo (siguiendo `previous_session_id`) y sacar un solo RINEX. `segments`, si se da, son los
    # archivos exactos a unir (en cualquier orden).
    join_segments: bool = True
    segments: list[Path] | None = None


@dataclass
class Report:
    input_path: Path
    obs_path: Path | None = None
    nav_path: Path | None = None
    source: str = ""
    model: str = ""
    metadata: dict = field(default_factory=dict)
    marker: str = ""
    antenna_height_m: float = 0.0
    antenna_height_source: str = ""
    case_offset_m: float = 0.0
    delta_h_m: float = 0.0
    approx_source: str = ""
    epochs: int = 0
    first: _dt.datetime | None = None
    last: _dt.datetime | None = None
    interval_s: float | None = None
    satellites: dict[str, int] = field(default_factory=dict)
    obs_types: dict[str, list[str]] = field(default_factory=dict)
    nav_records: dict[str, int] = field(default_factory=dict)
    frames: FrameStats | None = None
    warnings: list[str] = field(default_factory=list)
    partial: bool = False
    segments: list[Path] = field(default_factory=list)   # los tramos que se unieron (vacío: uno solo)


# --- Utilidades ------------------------------------------------------------------------------------------
def safe_name(text: str, fallback: str = "MERIDIAN") -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", text or "").strip("_")
    return name[:40] or fallback


def parse_height(text: str) -> float:
    """Acepta «1.80» o «1,80» (metros)."""
    value = float(text.strip().replace(",", "."))
    if not math.isfinite(value) or not 0.0 <= value <= MAX_ANTENNA_HEIGHT_M:
        raise ValueError(text)
    return value


def inspect(path: Path) -> dict:
    """Lo que la ventana necesita antes de convertir: el registro de metadatos (si lo hay)."""
    with open(path, "rb") as handle:
        head = handle.read(1 << 20)
    return find_session_metadata(head) or {}


def _sat_key(sat: str) -> tuple[int, int]:
    return SYSTEM_ORDER.find(sat[0]), int(sat[1:])


# --- Pasada 1: reconocimiento ----------------------------------------------------------------------------
@dataclass
class Survey:
    meta: dict | None = None
    has_obsvm: bool = False
    has_msm: bool = False
    msm_kinds: collections.Counter = field(default_factory=collections.Counter)
    old_msm: int = 0
    unicore_ref_s: float | None = None
    leap: int | None = None
    rmc_utc: _dt.datetime | None = None
    week_hints: list[tuple[str, int, bytes]] = field(default_factory=list)
    gga: list[tuple[float, float, float]] = field(default_factory=list)
    station: rtcm3.StationPosition | None = None
    descriptor: dict | None = None
    rtcm_1230: bool = False


def _survey(data: bytes, frames: list[Frame]) -> Survey:
    s = Survey()
    for frame in frames:
        if frame.kind == "unicore":
            header = unicore.parse_header(data[frame.start:frame.start + 24])
            if header.week and s.unicore_ref_s is None and header.time_ref == 0:
                s.unicore_ref_s = header.gps_s
            if header.leap and s.leap is None:
                s.leap = header.leap
            if header.message_id == unicore.ID_OBSVM:
                s.has_obsvm = True
        elif frame.kind == "rtcm":
            payload = data[frame.start + 3:frame.end - 3]
            kind = rtcm3.message_type(payload)
            if kind // 10 in rtcm3.MSM_SYSTEMS and 1 <= kind % 10 <= 7:
                if kind % 10 >= 4:
                    s.has_msm = True
                    s.msm_kinds[kind] += 1
                else:
                    s.old_msm += 1
            elif kind in (1005, 1006):
                try:
                    s.station = rtcm3.decode_1005_1006(payload)
                except ValueError:
                    pass
            elif kind == 1033 and s.descriptor is None:
                try:
                    s.descriptor = rtcm3.decode_1033(payload)
                except ValueError:
                    pass
            elif kind == 1230:
                s.rtcm_1230 = True
            elif kind in (1019, 1042, 1045, 1046) and len(s.week_hints) < 16:
                hint = rtcm3.decode_week_hint(payload)
                if hint:
                    s.week_hints.append((hint[0], hint[1], payload))
        else:
            line = data[frame.start:frame.end].rstrip(b"\r")
            if line.startswith(META_PREFIX):
                if s.meta is None:
                    s.meta = parse_session_metadata(line)
            elif line[3:6] == b"GGA":
                fix = parse_gga(line)
                if fix:
                    s.gga.append((fix.lat, fix.lon, fix.height))
            elif line[3:6] == b"RMC" and s.rmc_utc is None:
                s.rmc_utc = parse_rmc_datetime(line)
    return s


def _reference_time(survey: Survey, options: Options, warnings: list[str]) -> tuple[float | None, str]:
    """Instante de referencia (s GPS) para dar semana a los tiempos de las MSM."""
    if survey.unicore_ref_s is not None:
        return survey.unicore_ref_s, "cabecera Unicore"
    if survey.rmc_utc is not None:
        return gnsstime.from_datetime(survey.rmc_utc) + gnsstime.leap_seconds_utc(survey.rmc_utc), "NMEA RMC"
    if options.date is not None:
        moment = _dt.datetime.combine(options.date, _dt.time(12))
        return gnsstime.from_datetime(moment) + gnsstime.leap_seconds_utc(moment), "fecha indicada"
    for kind, week, payload in survey.week_hints:
        try:
            if kind == "bdt":
                eph = rtcm3.decode_1042(payload, gnsstime.bdt_to_gps(week, 302400), None)
            elif kind == "gal":
                eph = rtcm3.decode_galileo(payload, gnsstime.gps_seconds(week + 1024, 302400), None)
            else:
                continue
        except ValueError:
            continue
        if eph:
            return eph.toe, "efemérides RTCM"
    for kind, week, payload in survey.week_hints:
        if kind == "gps10":
            now = gnsstime.from_datetime(_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None))
            try:
                eph = rtcm3.decode_1019(payload, now, None)
            except ValueError:
                continue
            if eph:
                warnings.append("La semana GPS (1019, 10 bits) se resolvió con la fecha de hoy; si la "
                                "grabación tiene más de 19 años, indica la fecha.")
                return eph.toe, "efemérides GPS"
    return None, ""


# --- Pasadas 2 y 3: decodificación -----------------------------------------------------------------------
class Decoder:
    def __init__(self, data: bytes, frames: list[Frame], source: str, reference_s: float, leap: int,
                 glonass_fcn: dict[str, int]):
        self.data = data
        self.frames = frames
        self.source = source
        self.reference_s = reference_s
        self.leap = leap
        self.glonass_fcn = glonass_fcn
        self.nav: dict[tuple, KeplerEphemeris | GlonassEphemeris] = {}
        self.skipped: collections.Counter = collections.Counter()
        self.ignored_msm = 0
        self.bd3eph = 0
        self.irnss_eph = 0
        self.decode_errors = 0
        self.progress = 0.0               # fracción de tramas recorridas, para la ventana

    def _add_nav(self, record) -> None:
        if record is None:
            return
        if isinstance(record, GlonassEphemeris):
            key = (record.sat, round(record.toe))
        else:
            key = (record.sat, round(record.toe), int(record.iode), record.data_sources)
        if key not in self.nav:
            self.nav[key] = record

    def epochs(self, collect: bool) -> Iterator[Epoch]:
        current_s = self.reference_s
        pending: dict[int, Epoch] = {}
        data = self.data
        total = max(1, len(self.frames))
        for number, frame in enumerate(self.frames):
            if not number % 2048:
                self.progress = number / total
            if frame.kind == "unicore":
                raw = data[frame.start:frame.end]
                header = unicore.parse_header(raw)
                if header.week and header.time_ref == 0:
                    current_s = header.gps_s
                    if header.leap:
                        self.leap = header.leap
                mid = header.message_id
                if mid == unicore.ID_OBSVM:
                    if self.source != "unicore":
                        continue
                    result = unicore.decode_obsvm(raw, header)
                    self.glonass_fcn.update(result.glonass_fcn)
                    if collect:
                        self.skipped.update(result.skipped_signals)
                        if result.reason:
                            self.skipped["obsvm_" + result.reason] += 1
                    if result.epoch is not None and result.epoch.satellites:
                        yield result.epoch
                elif collect and mid in EPHEMERIS_IDS:
                    if mid in (unicore.ID_GPSEPH, unicore.ID_QZSSEPH):
                        self._add_nav(unicore.decode_gps_like(raw, header, mid == unicore.ID_QZSSEPH))
                    elif mid == unicore.ID_BDSEPH:
                        self._add_nav(unicore.decode_bds(raw, header))
                    elif mid == unicore.ID_GALEPH:
                        for record in unicore.decode_galileo(raw, header):
                            self._add_nav(record)
                    elif mid == unicore.ID_GLOEPH:
                        record = unicore.decode_glonass(raw, header)
                        if record:
                            self.glonass_fcn.setdefault(record.sat, record.fcn)
                        self._add_nav(record)
                elif collect and mid == unicore.ID_BD3EPH:
                    self.bd3eph += 1
                elif collect and mid == 112:
                    self.irnss_eph += 1
            elif frame.kind == "rtcm":
                payload = data[frame.start + 3:frame.end - 3]
                kind = rtcm3.message_type(payload)
                try:
                    if kind // 10 in rtcm3.MSM_SYSTEMS and 4 <= kind % 10 <= 7:
                        if self.source != "rtcm":
                            if collect:
                                self.ignored_msm += 1
                            continue
                        message = rtcm3.decode_msm(payload, current_s, self.leap, self.glonass_fcn)
                        if message is None:
                            continue
                        self.glonass_fcn.update(message.glonass_fcn)
                        if collect:
                            self.skipped.update(message.skipped)
                        current_s = message.time_ms / 1000.0
                        epoch = pending.setdefault(message.time_ms, Epoch(message.time_ms))
                        for sat, signals in message.epoch.satellites.items():
                            for signal in signals.values():
                                epoch.add(sat, signal)
                        if not message.multiple:
                            for time_ms in sorted(t for t in pending if t <= message.time_ms):
                                ready = pending.pop(time_ms)
                                if ready.satellites:
                                    yield ready
                    elif collect and kind in RTCM_EPHEMERIS:
                        ttr = current_s if current_s != self.reference_s else None
                        if kind == 1019:
                            self._add_nav(rtcm3.decode_1019(payload, current_s, ttr))
                        elif kind == 1044:
                            self._add_nav(rtcm3.decode_1044(payload, current_s, ttr))
                        elif kind == 1042:
                            self._add_nav(rtcm3.decode_1042(payload, current_s, ttr))
                        elif kind in (1045, 1046):
                            self._add_nav(rtcm3.decode_galileo(payload, current_s, ttr))
                        elif kind == 1020:
                            record = rtcm3.decode_1020(payload, current_s, self.leap)
                            if record:
                                self.glonass_fcn.setdefault(record.sat, record.fcn)
                            self._add_nav(record)
                except ValueError:
                    if collect:
                        self.decode_errors += 1
        for time_ms in sorted(pending):
            ready = pending.pop(time_ms)
            if ready.satellites:
                yield ready


# --- LLI ------------------------------------------------------------------------------------------------
class LockTracker:
    """Indicador de pérdida de enganche (bit 0) y de medio ciclo (bit 1) por satélite y señal."""

    def __init__(self) -> None:
        self.state: dict[tuple[str, str], tuple[float, float]] = {}

    def lli(self, sat: str, signal: SignalObs, time_s: float) -> int:
        key = (sat, signal.code)
        previous = self.state.get(key)
        flag = 0
        if signal.lock_time_s is not None:
            # Unicore: el tiempo de enganche tiene que crecer lo mismo que el tiempo transcurrido
            # (tolerancia de 50 ms, como RTKLIB); si creció menos, hubo pérdida.
            if previous is not None and signal.lock_time_s - previous[1] + 0.05 <= time_s - previous[0]:
                flag |= 1
            self.state[key] = (time_s, signal.lock_time_s)
        elif signal.lock_indicator is not None:
            # MSM: el indicador no puede bajar sin pérdida, y un enganche más corto que el tiempo
            # transcurrido desde la observación anterior implica pérdida posible.
            if previous is not None:
                elapsed = time_s - previous[0]
                if (signal.lock_indicator < previous[1]
                        or (signal.lock_indicator == 0 and previous[1] == 0)
                        or (signal.lock_min_s is not None and signal.lock_min_s < elapsed - 0.0005)):
                    flag |= 1
            self.state[key] = (time_s, float(signal.lock_indicator))
        if signal.half_cycle:
            flag |= 2
        return flag


# --- Conversión -------------------------------------------------------------------------------------------
def convert(path: Path | str, options: Options | None = None,
            progress: Callable[[float, str], None] | None = None) -> Report:
    options = options or Options()
    path = Path(path)
    report = Report(input_path=path, partial=path.suffix.lower() == ".part")

    def step(fraction: float, text: str) -> None:
        if progress:
            progress(fraction, text)

    step(0.02, "Leyendo el archivo…")
    chain = None
    try:
        if options.segments:
            chain = chain_from_files([Path(p) for p in options.segments])
        elif options.join_segments:
            chain = find_chain(path)
        if chain and chain.joined:
            data = join(chain)
            report.segments = [s.path for s in chain.segments]
            report.input_path = chain.segments[0].path
            report.partial = chain.segments[-1].path.suffix.lower() == ".part"
        else:
            data = path.read_bytes()
    except OSError as error:
        raise ConversionError(f"No se pudo leer el archivo: {error.strerror or error}") from error
    if not data:
        raise ConversionError("El archivo está vacío.")
    if chain:
        report.warnings.extend(chain.warnings)
        if chain.joined:
            report.warnings.append(f"Grabación larga en {len(chain.segments)} tramos, unidos en un solo RINEX: "
                                   + ", ".join(s.path.name for s in chain.segments) + ".")
    step(0.08, "Separando las tramas…")
    frames, stats = split_frames(data)
    report.frames = stats
    survey = _survey(data, frames)
    warnings = report.warnings
    if stats.truncated_tail:
        warnings.append("El archivo termina a mitad de una trama (grabación interrumpida o descarga "
                        "incompleta): se ignoró ese final.")
    if stats.unicore_crc_errors or stats.rtcm_crc_errors:
        warnings.append(f"Tramas con CRC incorrecto descartadas: {stats.unicore_crc_errors} Unicore, "
                        f"{stats.rtcm_crc_errors} candidatas RTCM.")

    # Fuente de observaciones.
    if survey.has_obsvm:
        source = "unicore"
    elif survey.has_msm:
        source = "rtcm"
    else:
        detail = []
        if stats.ascii_lines:
            detail.append(f"{stats.ascii_lines} líneas NMEA o de texto")
        if stats.unicore:
            detail.append(f"{stats.unicore} mensajes Unicore sin OBSVM")
        if stats.rtcm:
            detail.append(f"{stats.rtcm} mensajes RTCM sin MSM4-7")
        raise ConversionError(
            "La grabación no contiene observaciones crudas (ni OBSVM del UM980 ni MSM4-7 de RTCM)"
            + (": solo " + ", ".join(detail) if detail else "") + ". En el MeridianV, aplica el perfil de "
            "datos crudos («Activar datos crudos» / acción raw_profile) antes de grabar.")
    report.source = source
    if survey.old_msm:
        warnings.append(f"{survey.old_msm} mensajes MSM1-3 ignorados (sin observación completa).")

    # Referencia de tiempo.
    reference_s, reference_source = _reference_time(survey, options, warnings)
    if reference_s is None:
        raise ConversionError("Las MSM solo traen la hora dentro de la semana y la grabación no tiene nada "
                              "que dé la fecha (ni RMC ni efemérides). Indica la fecha de la grabación "
                              "(--date AAAA-MM-DD).")
    leap = survey.leap or gnsstime.leap_seconds_gps(reference_s)

    # Metadatos del receptor y altura de antena.
    meta = survey.meta or {}
    report.metadata = meta
    model = meta.get("model") if isinstance(meta.get("model"), str) else None
    if model and options.model and product(options.model) and product(options.model)["model"] != model:
        warnings.append(f"El archivo dice que es de un {model}; se usa eso y no el modelo indicado "
                        f"({options.model}).")
    if not model:
        model = options.model
    if not model:
        if source == "unicore":
            model = "MeridianV"
            warnings.append("La grabación no trae metadatos de sesión: se supone un MeridianV (binario "
                            "nativo del UM980). Revisa el modelo y la altura del case.")
        else:
            raise ConversionError("La grabación no trae metadatos de sesión y las MSM pueden venir de un "
                                  "MeridianV o de un MeridianVI. Indica el modelo (--model MeridianV o "
                                  "MeridianVI).")
    defaults = product(model)
    if defaults is None:
        raise ConversionError(f"Modelo desconocido: {model}. Usa MeridianV o MeridianVI.")
    report.model = defaults["model"]

    def meta_number(key: str, default: float) -> tuple[float, bool]:
        value = meta.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value), True
        return default, False

    case_offset, from_meta = meta_number("case_offset_m", defaults["case_offset_m"])
    if not from_meta:
        warnings.append(f"Altura del case tomada de la tabla de la herramienta ({case_offset:.3f} m del "
                        f"{defaults['model']}), no del receptor.")
    pco, _ = meta_number("antenna_pco_m", defaults["antenna_pco_m"])
    height = options.antenna_height_m
    if height is None:
        pole, known = meta_number("pole_height_m", 0.0)
        if known and pole > 0:
            height = pole
            report.antenna_height_source = "altura del bastón configurada en el receptor"
            warnings.append(f"No se indicó altura de antena: se usa la del bastón configurada en el "
                            f"receptor ({pole:.3f} m). Compruébala.")
        else:
            raise ConversionError("Falta la altura de antena: la altura vertical desde la marca hasta la base "
                                  "del receptor (el asiento de la rosca de 5/8\"), en metros.")
    else:
        report.antenna_height_source = "indicada al convertir"
    if not math.isfinite(height) or not 0.0 <= height <= MAX_ANTENNA_HEIGHT_M:
        raise ConversionError("La altura de antena tiene que estar entre 0 y 100 m.")
    report.antenna_height_m = height
    report.case_offset_m = case_offset
    report.delta_h_m = height + case_offset

    # Pasada 2.
    step(0.15, "Decodificando observaciones…")
    glonass_fcn: dict[str, int] = {}
    decoder = Decoder(data, frames, source, reference_s, leap, glonass_fcn)
    types: dict[str, dict[str, set[str]]] = {}
    satellites: dict[str, set[str]] = {}
    first_ms = last_ms = None
    previous_ms = None
    deltas: collections.Counter = collections.Counter()
    count = 0
    for epoch in decoder.epochs(collect=True):
        if previous_ms is not None and epoch.time_ms <= previous_ms:
            continue
        if first_ms is None:
            first_ms = epoch.time_ms
        if previous_ms is not None:
            deltas[epoch.time_ms - previous_ms] += 1
        previous_ms = last_ms = epoch.time_ms
        count += 1
        if not count % 500:
            step(0.15 + 0.4 * decoder.progress, f"Decodificando observaciones… {count} épocas")
        for sat, signals in epoch.satellites.items():
            satellites.setdefault(sat[0], set()).add(sat)
            system_types = types.setdefault(sat[0], {})
            for code, signal in signals.items():
                present = system_types.setdefault(code, set())
                if signal.pseudorange is not None:
                    present.add("C")
                if signal.phase is not None:
                    present.add("L")
                if signal.doppler is not None:
                    present.add("D")
                if signal.cn0 is not None:
                    present.add("S")
    if not count:
        raise ConversionError("No hay ninguna época de observación decodificable en la grabación.")

    obs_types: dict[str, list[str]] = {}
    for system in sorted(types, key=SYSTEM_ORDER.find):
        listed = []
        for code in sorted(types[system], key=code_sort_key):
            listed += [kind + code for kind in "CLDS" if kind in types[system][code]]
        if listed:
            obs_types[system] = listed
    report.obs_types = obs_types
    report.satellites = {s: len(v) for s, v in sorted(satellites.items(), key=lambda i: SYSTEM_ORDER.find(i[0]))}
    report.epochs = count
    interval_ms = deltas.most_common(1)[0][0] if deltas else None
    report.interval_s = interval_ms / 1000.0 if interval_ms else None
    report.first = gnsstime.ms_to_datetime(first_ms)
    report.last = gnsstime.ms_to_datetime(last_ms)

    for key, value in decoder.skipped.most_common():
        warnings.append(_skip_text(key, value))
    if decoder.ignored_msm:
        warnings.append(f"Hay MSM en la grabación ({decoder.ignored_msm}) además del OBSVM: se usó el OBSVM.")
    if decoder.bd3eph:
        warnings.append(f"{decoder.bd3eph} efemérides BD3EPH (BDS-3 B1C/B2a/B2b, CNAV) omitidas: RINEX 3.04 "
                        "no tiene registro para ellas. Las D1/D2 de BDSEPH sí van.")
    if decoder.decode_errors:
        warnings.append(f"{decoder.decode_errors} mensajes RTCM cortos o mal formados ignorados.")
    glonass_present = "R" in satellites
    missing_fcn = sorted(s for s in satellites.get("R", ()) if s not in glonass_fcn)
    if glonass_present and missing_fcn:
        warnings.append("Sin canal de frecuencia para " + ", ".join(missing_fcn) +
                        ": su fase no se pudo escribir (falta 1020 o MSM5/7).")

    nav_records = [r for r in decoder.nav.values()]
    for record in nav_records:
        if not isinstance(record, GlonassEphemeris) and record.ttr is None:
            record.ttr = first_ms / 1000.0
    report.nav_records = dict(collections.Counter(r.sat[0] for r in nav_records))
    if not nav_records:
        warnings.append("La grabación no trae efemérides: no se generó archivo de navegación (el "
                        "posproceso puede usar efemérides de otra fuente, p. ej. IGS).")

    # Posición aproximada del punto (marca).
    approx, report.approx_source = _approx_position(survey, report.delta_h_m, pco)
    if approx == (0.0, 0.0, 0.0):
        warnings.append("Sin posición aproximada (no hay GGA con solución ni 1005/1006): la cabecera la "
                        "lleva en 0 y el programa de posproceso tendrá que calcularla.")

    # Cabecera.
    marker = options.marker or meta.get("unit_name") or path.stem
    report.marker = marker
    gnss = meta.get("gnss_receiver") or defaults["gnss_receiver"]
    receiver_type = f"{report.model} {gnss}"
    antenna_type = meta.get("antenna_model") or defaults["antenna_model"]
    radome = meta.get("antenna_radome") or "NONE"
    comments = [
        f"Converted by {PROGRAM} from {path.name}"[:60],
        "Source: Unicore OBSVM (UM980 native binary)" if source == "unicore"
        else "Source: RTCM 10403.3 MSM " + ",".join(str(k) for k in sorted(survey.msm_kinds)),
        f"Mark to receiver base (5/8 seat): {height:.4f} m",
        f"+ case offset base to ARP {case_offset:.4f} m = DELTA H {height + case_offset:.4f} m",
        "ARP = bottom of the antenna; antenna PCO/PCV not applied",
    ]
    # La calibración de la antena (GNS2-02): qué ANTEX corresponde a ANT # / TYPE, o que no hay ninguno.
    calibration = meta.get("antenna_calibration")
    if isinstance(calibration, str) and calibration:
        comments.append(("Antenna PCO/PCV not calibrated (no ANTEX entry)" if calibration == "uncalibrated"
                         else f"Antenna calibration {calibration}: use its ANTEX")[:60])
    if meta.get("session_id"):
        comments.append(f"Session {meta.get('session_id')}"[:60])
    if source == "unicore":
        comments.append("UM980 phase alignment undocumented: no shift applied")
    else:
        comments.append("MSM phases aligned by RTCM; no shift applied here")
    comments.append("GLONASS code-phase bias unknown (record left blank)")
    if report.partial:
        comments.append("Input is an interrupted recording (.part)")
    if report.approx_source:
        comments.append(f"Approx position: {report.approx_source}"[:60])
    # Marco y época que el operador declaró para la coordenada de la base (firmware, auditoría profesional
    # del 20-10-2026, T2): se dejan dichos, sin transformar nada.
    frame, epoch = meta.get("frame"), meta.get("frame_epoch")
    if frame or isinstance(epoch, (int, float)):
        text = "Base frame declared: " + (str(frame) if frame else "?")
        if isinstance(epoch, (int, float)):
            text += f" @{epoch:.4f}"
        comments.append(text[:60])
    header = ObsHeader(
        program=PROGRAM, run_by="", marker_name=marker, observer=options.observer, agency=options.agency,
        receiver_number=str(meta.get("serial_number") or ""), receiver_type=receiver_type,
        receiver_version=str(meta.get("firmware_version") or ""), antenna_number="",
        antenna_type=str(antenna_type).upper(), antenna_radome=str(radome).upper(), approx_xyz=approx,
        delta_hen=(report.delta_h_m, 0.0, 0.0), obs_types=obs_types, first_ms=first_ms, last_ms=last_ms,
        interval_s=report.interval_s,
        phase_shift={s: phase_shift_for(s, [t[1:] for t in obs_types[s] if t[0] == "L"], source == "rtcm")
                     for s in obs_types},
        glonass_slots={int(sat[1:]): fcn for sat, fcn in glonass_fcn.items()
                       if sat in satellites.get("R", ()) or any(r.sat == sat for r in nav_records)},
        comments=comments)

    out_dir = Path(options.out_dir) if options.out_dir else path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.first.strftime("%Y%m%d_%H%M%S")
    base = f"{safe_name(marker)}_{stamp}"
    obs_path = out_dir / f"{base}.obs"
    nav_path = out_dir / f"{base}.nav"

    # Pasada 3.
    step(0.55, "Escribiendo el RINEX de observación…")
    tracker = LockTracker()
    temporary = obs_path.with_name(obs_path.name + ".tmp")
    written = 0
    with open(temporary, "w", encoding="ascii", newline="\n") as out:
        header.write(out)
        previous_ms = None
        decoder3 = Decoder(data, frames, source, reference_s, leap, glonass_fcn)
        for epoch in decoder3.epochs(collect=False):
            if previous_ms is not None and epoch.time_ms <= previous_ms:
                continue
            previous_ms = epoch.time_ms
            time_s = epoch.time_ms / 1000.0
            lines = []
            for sat in sorted(epoch.satellites, key=_sat_key):
                system_types = obs_types.get(sat[0])
                if not system_types:
                    continue
                signals = epoch.satellites[sat]
                values = []
                for obs_type in system_types:
                    signal = signals.get(obs_type[1:])
                    if signal is None:
                        values.append((None, 0))
                        continue
                    kind = obs_type[0]
                    if kind == "C":
                        values.append((signal.pseudorange, 0))
                    elif kind == "L":
                        if signal.phase is None:
                            values.append((None, 0))
                            continue
                        flag = tracker.lli(sat, signal, time_s)
                        phase = signal.phase
                        if abs(phase) >= 1e9:
                            phase = math.fmod(phase, 1e9)
                            flag |= 1
                        values.append((phase, flag))
                    elif kind == "D":
                        values.append((signal.doppler, 0))
                    else:
                        values.append((signal.cn0, 0))
                if all(v is None for v, _ in values):
                    continue
                lines.append(observation_line(sat, values))
            if not lines:
                continue
            out.write(epoch_line(epoch.time_ms, len(lines)))
            out.writelines(lines)
            written += 1
            if not written % 500:
                step(0.55 + 0.35 * decoder3.progress, f"Escribiendo el RINEX de observación… {written} épocas")
    os.replace(temporary, obs_path)
    report.obs_path = obs_path
    report.epochs = written

    if nav_records:
        step(0.9, "Escribiendo el RINEX de navegación…")
        temporary = nav_path.with_name(nav_path.name + ".tmp")
        with open(temporary, "w", encoding="ascii", newline="\n") as out:
            write_nav(out, PROGRAM, "", [f"Converted by {PROGRAM} from {path.name}"[:60],
                                         "Source: " + ("Unicore *EPHB logs" if any(r.source == "unicore" for r in nav_records)
                                                       else "RTCM 10403.3 ephemerides")],
                      leap, nav_records)
        os.replace(temporary, nav_path)
        report.nav_path = nav_path
    step(1.0, "Listo.")
    return report


def _approx_position(survey: Survey, delta_h_m: float, pco_m: float = 0.0) -> tuple[tuple[float, float, float], str]:
    """Posición aproximada de la marca, bajando por la vertical local.

    - La estación RTCM (1005/1006) **es el ARP**: se le resta solo DELTA H (altura de antena + case).
    - Las GGA son la posición del **centro de fase** (el módulo no tiene modelo de antena): se les resta
      DELTA H más el PCO vertical declarado.

    Hasta el 21-10-2026 (GNS2-02, GEO2-4) también al ARP de la estación se le restaba el PCO: no se
    notaba porque el PCO era 0, y la marca habría quedado baja en lo que midiera el PCO."""
    if survey.station is not None:
        lat, lon, h = ecef_to_geodetic(*survey.station.ecef)
        return geodetic_to_ecef(lat, lon, h - delta_h_m), "RTCM 1005/1006 ARP minus DELTA H"
    if survey.gga:
        lat = statistics.median(p[0] for p in survey.gga)
        lon = statistics.median(p[1] for p in survey.gga)
        h = statistics.median(p[2] for p in survey.gga)
        return (geodetic_to_ecef(lat, lon, h - delta_h_m - pco_m),
                "median of NMEA GGA minus DELTA H and PCO" if pco_m else "median of NMEA GGA minus DELTA H")
    return (0.0, 0.0, 0.0), ""


def _skip_text(key: str, count: int) -> str:
    if key.startswith("obsvm_"):
        reason = {"obsvm_sin_tiempo": "sin tiempo GPS (semana 0)",
                  "obsvm_referencia_de_tiempo_desconocida": "con referencia de tiempo distinta de GPST",
                  "obsvm_longitud": "con longitud incoherente"}.get(key, key)
        return f"{count} épocas OBSVM descartadas {reason}."
    if ":" in key:
        system, signal = key.split(":", 1)
        name = SYSTEM_NAMES.get(system, system)
        return f"{count} observaciones de {name} con una señal sin código RINEX 3.04 ({signal}) omitidas."
    texts = {
        "glonass_sin_canal": "observaciones de fase GLONASS sin canal de frecuencia conocido omitidas",
        "glonass_sin_ranura": "observaciones GLONASS sin ranura conocida omitidas",
        "prn_fuera_de_rango": "observaciones con PRN fuera de rango omitidas",
        "sistema_desconocido": "observaciones de un sistema desconocido omitidas",
        "sin_medida_valida": "señales sin pseudodistancia ni fase válidas omitidas",
    }
    return f"{count} {texts.get(key, key)}."
