"""De lo que entrega el usuario a un `ObservationSet` listo para procesar.

Acepta, mezclados: grabaciones del MeridianV/MeridianVI (`.bin`/`.part`, se convierten con el convertidor del
MeridianV), RINEX de observación y navegación 2.11/3.x/4.x sueltos o comprimidos (`.zip`, `.gz`, `.Z`, Hatanaka).
Descomprime, convierte, une los archivos de observación de la estación en uno solo y, si se pide, lo recorta a una
ventana.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errors import UserError
from ..model import Ecef, ObservationSet
from . import compress, tools
from .meridian_rinex import converter as meridian
from .meridian_rinex.products import PRODUCTS
from .rinex import (classify, merge_observations, read_header, rinex_time_to_utc, scan_epochs, utc_to_rinex_time)

MAX_POLE_HEIGHT_M = 100.0


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)[:40] or "OBS"


def meridian_model(receiver_type: str) -> str | None:
    """«MeridianV UM980» → «MeridianV»; «MeridianVI LG290P» → «MeridianVI»; otro receptor → None."""
    first = (receiver_type or "").split(" ")[0].strip()
    for name in PRODUCTS:
        if first.lower() == name.lower():
            return name
    return None


def prepare_observations(paths: list[Path], workdir: Path, *, pole_height_m: float | None = None,
                         marker: str | None = None, window: tuple[datetime, datetime] | None = None,
                         progress: tools.Progress | None = None,
                         cancel: threading.Event | None = None) -> ObservationSet:
    """Descomprime, convierte, une y (si se da `window`, en UTC) recorta las observaciones de UNA estación.

    `pole_height_m`: altura vertical de la marca a la base del receptor (asiento de 5/8") para los Meridian (se le
    suma la altura del case); para otro receptor, de la marca a la ARP. None: la del RINEX o de la grabación."""
    paths = [Path(p) for p in paths]
    if not paths:
        raise UserError("No se entregó ningún archivo.")
    if pole_height_m is not None and not (math.isfinite(pole_height_m) and 0.0 <= pole_height_m <= MAX_POLE_HEIGHT_M):
        raise UserError("La altura de antena tiene que estar entre 0 y 100 m.")
    workdir.mkdir(parents=True, exist_ok=True)
    step = progress or (lambda f, m: None)
    warnings: list[str] = []
    step(0.0, "Revisando los archivos…")
    raw_dir = workdir / "entrada"
    files = compress.unpack(paths, raw_dir, cancel=cancel)
    kinds = {f: classify(f) for f in files}
    recordings = [f for f, k in kinds.items() if k == "meridian"]
    obs_files = [f for f, k in kinds.items() if k == "obs"]
    nav_files = [f for f, k in kinds.items() if k == "nav"]
    unknown = [f.name for f, k in kinds.items() if k in ("unknown", "met")]
    ignored = [f.name for f, k in kinds.items() if k in ("sp3", "clk")]
    if unknown:
        raise UserError("No se reconocen estos archivos (ni RINEX ni grabación del Meridian): " + ", ".join(unknown)
                        + ".")
    if ignored:
        warnings.append("Órbitas o relojes precisos entregados como observación se ignoran aquí ("
                        + ", ".join(ignored) + "); se bajan aparte en «Efemérides precisas».")
    receiver_model = None
    case_offset = None
    if recordings:
        if obs_files:
            raise UserError("Entrega o la grabación del receptor o sus RINEX, no las dos cosas.")
        step(0.05, "Convirtiendo la grabación del receptor a RINEX…")
        meta = meridian.inspect(recordings[0])
        height = pole_height_m
        if height is None and not isinstance(meta.get("pole_height_m"), (int, float)):
            height = 0.0
            warnings.append("Sin altura del bastón: la posición se calcula en la base del receptor (asiento de "
                            "5/8\").")
        options = meridian.Options(antenna_height_m=height, marker=marker or None, out_dir=workdir / "rinex",
                                   join_segments=True, segments=recordings if len(recordings) > 1 else None)
        (workdir / "rinex").mkdir(parents=True, exist_ok=True)
        try:
            report = meridian.convert(recordings[0], options,
                                      progress=lambda f, m: step(0.05 + 0.45 * f, m))
        except meridian.ConversionError as error:
            raise UserError(str(error)) from error
        warnings.extend(report.warnings)
        if report.obs_path is None:
            raise UserError("La conversión de la grabación no produjo observaciones.")
        obs_files = [report.obs_path]
        if report.nav_path:
            nav_files.append(report.nav_path)
        receiver_model = report.model
        case_offset = report.case_offset_m
        pole_height_m = None          # ya va en el RINEX (altura + case)
    if not obs_files:
        raise UserError("No hay observaciones: falta el RINEX de observación (o la grabación del receptor).")
    tools.check(cancel)

    headers = [read_header(f) for f in obs_files]
    first = headers[0]
    station_marker = marker or first.marker or obs_files[0].stem[:4]
    delta_h = None
    model = receiver_model or meridian_model(first.receiver_type)
    if model and case_offset is None:
        case_offset = PRODUCTS[model]["case_offset_m"]
    if pole_height_m is not None:
        delta_h = pole_height_m + (case_offset or 0.0) if model else pole_height_m
        old = first.delta_hen[0]
        if abs(old - delta_h) > 0.0005 and old != 0.0:
            warnings.append(f"La altura del RINEX ({old:.4f} m a la ARP) se cambió por la indicada: "
                            f"{delta_h:.4f} m" + (f" ({pole_height_m:.4f} + {case_offset:.4f} del case)."
                                                  if model else "."))
    start = end = None
    if window is not None:
        start = utc_to_rinex_time(window[0], first.time_system)
        end = utc_to_rinex_time(window[1], first.time_system)
    step(0.55, f"Uniendo {len(obs_files)} archivo(s) de observación…")
    out = workdir / f"{_safe(station_marker)}.obs"
    merged = merge_observations(obs_files, out, start=start, end=end, marker=marker or None,
                                antenna_delta_h=delta_h, cancel=cancel)
    warnings.extend(merged.warnings)
    header = merged.header
    step(0.85, "Revisando las épocas…")
    count, first_t, last_t, interval = scan_epochs(header)
    nav_unique: list[Path] = []
    seen: set[tuple[str, int]] = set()
    for n in nav_files:
        key = (n.name, n.stat().st_size)
        if key not in seen:
            seen.add(key)
            nav_unique.append(n)
    if not nav_unique:
        warnings.append("No hay archivo de navegación de esta estación: se usarán los de la otra estación "
                        "o las órbitas precisas.")
    obs_set = ObservationSet(
        obs=[out], nav=nav_unique, marker=header.marker or station_marker, receiver=header.receiver_type,
        antenna_type=header.antenna_type, radome=header.radome or "NONE", antenna_height_m=header.delta_hen[0],
        start=rinex_time_to_utc(first_t, header.time_system), end=rinex_time_to_utc(last_t, header.time_system),
        interval_s=interval, rinex_version=f"{header.version:.2f}", source_files=paths,
        approx_position=Ecef(*header.approx_xyz) if header.approx_xyz and any(header.approx_xyz) else None,
        receiver_model=model or "", case_offset_m=case_offset, epochs=count, warnings=warnings)
    if model:
        obs_set.antenna_pco_measured = _pco_measured(header)
    step(1.0, f"{obs_set.marker}: {count} épocas listas.")
    return obs_set


def _pco_measured(header) -> bool:
    text = " ".join(line[0:60] for line in header.lines if line[60:].strip() == "COMMENT").lower()
    return "not calibrated" not in text and "uncalibrated" not in text and "sin calibrar" not in text


def trim(obs: ObservationSet, start: datetime, end: datetime, workdir: Path, *, margin_s: float = 120.0,
         cancel: threading.Event | None = None) -> ObservationSet:
    """Copia de `obs` recortada a [start − margen, end + margen] (UTC). Si ya cabe en la ventana, la devuelve igual."""
    if obs.start and obs.end and obs.start >= start - timedelta(seconds=margin_s) and \
            obs.end <= end + timedelta(seconds=margin_s):
        return obs
    header = read_header(obs.obs[0])
    a = utc_to_rinex_time(start - timedelta(seconds=margin_s), header.time_system)
    b = utc_to_rinex_time(end + timedelta(seconds=margin_s), header.time_system)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / f"{obs.obs[0].stem}_ventana.obs"
    merged = merge_observations([obs.obs[0]], out, start=a, end=b, cancel=cancel)
    count, first_t, last_t, interval = scan_epochs(merged.header)
    return ObservationSet(
        obs=[out], nav=list(obs.nav), marker=obs.marker, receiver=obs.receiver, antenna_type=obs.antenna_type,
        radome=obs.radome, antenna_height_m=obs.antenna_height_m,
        start=rinex_time_to_utc(first_t, merged.header.time_system),
        end=rinex_time_to_utc(last_t, merged.header.time_system), interval_s=interval or obs.interval_s,
        rinex_version=obs.rinex_version, source_files=list(obs.source_files),
        antenna_pco_measured=obs.antenna_pco_measured, approx_position=obs.approx_position,
        receiver_model=obs.receiver_model, case_offset_m=obs.case_offset_m, epochs=count,
        warnings=list(obs.warnings))


def utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
