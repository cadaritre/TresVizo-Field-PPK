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


def _names(names: list[str], limit: int = 4) -> str:
    """Lista corta de nombres de archivo para un aviso («A, B, C, D y 20 más»)."""
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" y {len(names) - limit} más"


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
    files, damaged = compress.unpack_each(paths, raw_dir, cancel=cancel)
    if damaged and not files:
        raise UserError(damaged[0][1])
    for path, why in damaged:
        # p. ej. un ZIP horario dañado en el servidor del INEGI: se usan las demás horas y se avisa del hueco
        warnings.append(f"{why} Se dejó fuera: faltan las observaciones de ese archivo (hueco en los datos).")
    kinds = {f: classify(f) for f in files}
    recordings = [f for f, k in kinds.items() if k == "meridian"]
    obs_files = [f for f, k in kinds.items() if k == "obs"]
    nav_files = [f for f, k in kinds.items() if k == "nav"]
    unknown = [f.name for f, k in kinds.items() if k in ("unknown", "met")]
    ignored = [f.name for f, k in kinds.items() if k in ("sp3", "clk")]
    if unknown and not (obs_files or recordings):
        raise UserError("No se reconocen estos archivos (ni RINEX ni grabación del Meridian): " + _names(unknown)
                        + ".")
    if unknown:
        # lo que acompaña a las observaciones y no se usa (almanaques como el .26a de los ZIP del INEGI, datos
        # meteorológicos, notas): se ignora con una nota en vez de rechazar todo el conjunto
        warnings.append("Se ignoran archivos que no son observación ni navegación (almanaques, datos "
                        "meteorológicos u otros): " + _names(unknown) + ".")
    if ignored:
        warnings.append("Órbitas o relojes precisos entregados como observación se ignoran aquí ("
                        + ", ".join(ignored) + "); se bajan aparte en «Efemérides precisas».")
    receiver_model = None
    case_offset = None
    sources: dict[str, str] = {}
    session_pole: float | None = None
    session_name = ""
    approx_source = ""
    frame_declared, frame_epoch_declared = "", None
    if recordings:
        if obs_files:
            raise UserError("Entrega o la grabación del receptor o sus RINEX, no las dos cosas.")
        step(0.05, "Convirtiendo la grabación del receptor a RINEX…")
        meta = meridian.inspect(recordings[0])
        height = pole_height_m
        meta_pole = meta.get("pole_height_m")
        if isinstance(meta_pole, (int, float)) and not isinstance(meta_pole, bool) and meta_pole > 0:
            session_pole = float(meta_pole)
        if height is None:
            if session_pole is not None:
                height = session_pole
                sources["pole_height_m"] = "sesión del receptor"
            else:
                height = 0.0
                sources["pole_height_m"] = "sin altura (0)"
                warnings.append("Sin altura del bastón: la posición se calcula en la base del receptor (asiento de "
                                "5/8\").")
        else:
            sources["pole_height_m"] = "indicada"
        name = meta.get("session_name") if isinstance(meta.get("session_name"), str) else ""
        if not marker and name:
            marker = meridian.safe_name(name)
            sources["point_name"] = "sesión del receptor"
        session_name = name or ""
        if isinstance(meta.get("frame"), str) and meta.get("frame"):
            frame_declared = meta["frame"]
            if isinstance(meta.get("frame_epoch"), (int, float)) and meta["frame_epoch"] > 0:
                frame_epoch_declared = float(meta["frame_epoch"])
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
        approx_source = {"": ""}.get(report.approx_source, f"grabación del receptor ({report.approx_source})")
        session_pole = height if sources.get("pole_height_m") != "sin altura (0)" else session_pole
        for key in ("marker", "receiver", "antenna_type", "radome", "antenna_height_m", "interval_s", "start", "end"):
            sources[key] = "sesión del receptor"
        if "point_name" not in sources:
            sources["point_name"] = "indicado" if options.marker else "sesión del receptor (nombre de la unidad)"
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
    for key in ("marker", "receiver", "antenna_type", "radome", "antenna_height_m", "interval_s", "start", "end",
                "point_name"):
        sources.setdefault(key, "encabezado RINEX")
    if delta_h is not None:
        sources["antenna_height_m"] = "indicada"
    antenna_type, radome, antenna_height = header.antenna_type, header.radome or "NONE", header.delta_hen[0]
    catalog = None
    if not recordings:
        from .networks import find_station          # estación de una red CORS: su antena y altura oficiales
        near = Ecef(*header.approx_xyz) if header.approx_xyz else None
        catalog = find_station(header.marker or station_marker, near=near)
    if catalog is not None:
        label = f"catálogo {catalog.network_name or catalog.network}"
        diffs = []
        if catalog.antenna_type and catalog.antenna_type.replace(" ", "").upper() != \
                (antenna_type or "").replace(" ", "").upper():
            diffs.append(f"antena {antenna_type or '—'} → {catalog.antenna_type}")
        if abs(catalog.antenna_height_m - antenna_height) > 0.0005 and delta_h is None:
            diffs.append(f"altura {antenna_height:.4f} → {catalog.antenna_height_m:.4f} m")
        if diffs:
            warnings.append(f"{catalog.id}: el encabezado RINEX no coincide con el {label} ("
                            + "; ".join(diffs) + "): se usa el catálogo.")
        if catalog.antenna_type:
            antenna_type, radome = catalog.antenna_type, catalog.radome or "NONE"
            sources["antenna_type"] = sources["radome"] = label
        if delta_h is None:
            antenna_height = catalog.antenna_height_m
            sources["antenna_height_m"] = label
    approx = Ecef(*header.approx_xyz) if header.approx_xyz and any(header.approx_xyz) else None
    obs_set = ObservationSet(
        obs=[out], nav=nav_unique, marker=header.marker or station_marker, receiver=header.receiver_type,
        antenna_type=antenna_type, radome=radome, antenna_height_m=antenna_height,
        start=rinex_time_to_utc(first_t, header.time_system), end=rinex_time_to_utc(last_t, header.time_system),
        interval_s=interval, rinex_version=f"{header.version:.2f}", source_files=paths,
        approx_position=Ecef(*header.approx_xyz) if header.approx_xyz and any(header.approx_xyz) else None,
        receiver_model=model or "", case_offset_m=case_offset, epochs=count, warnings=warnings,
        point_name=session_name or header.marker or station_marker,
        pole_height_m=session_pole if recordings else (pole_height_m if pole_height_m is not None else
                                                       (antenna_height - case_offset if model and case_offset
                                                        is not None else None)),
        field_sources=sources, approx_position_source=(approx_source or "encabezado RINEX") if approx else "",
        frame_declared=frame_declared, frame_epoch_declared=frame_epoch_declared)
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
        warnings=list(obs.warnings), point_name=obs.point_name, pole_height_m=obs.pole_height_m,
        field_sources=dict(obs.field_sources), approx_position_source=obs.approx_position_source,
        frame_declared=obs.frame_declared, frame_epoch_declared=obs.frame_epoch_declared)


def utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
