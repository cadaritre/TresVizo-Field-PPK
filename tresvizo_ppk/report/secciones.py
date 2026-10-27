"""Secciones comunes a los dos informes: sistema de coordenadas, ajustes del proceso, estaciones, calidad de los datos,
gráficas de RTKLIB, avisos y bitácora."""
from __future__ import annotations

import math
from pathlib import Path

from ..crs import api as crs
from ..errors import UserError
from ..model import (CoordinateChoice, DataQuality, Ecef, Ephemeris, ObservationSet, ProcessingOptions, Project,
                     Solution, Station, TransformedCoordinate)
from . import estilo, graficas
from .documento import CONTENT_WIDTH, Report, text
from .estilo import num
from .rtklib_salida import dop_series, read_pos, read_stat

SOLUTION_NAMES = {Solution.FIXED: "FIJA", Solution.FLOAT: "FLOTANTE", Solution.SINGLE: "AUTÓNOMA",
                  Solution.NONE: "SIN SOLUCIÓN"}
POINT_SOLUTION_NAMES = {Solution.FIXED: "FIJO", Solution.FLOAT: "FLOTANTE", Solution.SINGLE: "AUTÓNOMO",
                        Solution.NONE: "SIN SOLUCIÓN"}
EPHEMERIS_NAMES = {Ephemeris.BROADCAST: "difundidas (de navegación)", Ephemeris.PRECISE: "precisas (IGS)"}


def transform_or_none(position: Ecef | None, choice: CoordinateChoice) -> tuple[TransformedCoordinate | None, str]:
    """La coordenada en el sistema elegido, o el motivo por el que no se pudo (va al informe como aviso)."""
    if position is None:
        return None, ""
    try:
        return crs.transform(position, choice), ""
    except UserError as error:
        return None, str(error)


def system_name(c: TransformedCoordinate | None, choice: CoordinateChoice) -> str:
    if c is None:
        return choice.label or choice.crs_id
    return choice.label or c.crs_name


def height_label(c: TransformedCoordinate | None) -> str:
    if c is not None and c.orthometric_height_m is not None:
        return "Cota normal (H*)" if c.height_kind == "normal" else "Cota ortométrica (H)"
    return "Altura elipsoidal (h)"


def height_value(c: TransformedCoordinate | None) -> float | None:
    if c is None:
        return None
    return c.orthometric_height_m if c.orthometric_height_m is not None else c.ellipsoidal_height_m


def axis_names(c: TransformedCoordinate | None) -> tuple[str, str]:
    if c is not None and c.easting_m is not None:
        return "Este (m)", "Norte (m)"
    return "Longitud (°)", "Latitud (°)"


def coordinate_fields(c: TransformedCoordinate | None, position: Ecef, choice: CoordinateChoice) -> list[tuple[str, str]]:
    """Los pares de la coordenada en el sistema elegido y en el marco oficial."""
    rows: list[tuple[str, str]] = []
    if c is not None:
        rows.append(("Sistema", system_name(c, choice) + (f" ({c.crs_code})" if c.crs_code else "")))
        if c.easting_m is not None:
            rows += [("Norte", num(c.northing_m, 3, "m")), ("Este", num(c.easting_m, 3, "m"))]
            if c.zone:
                rows.append(("Zona", c.zone))
        rows += [("Latitud", estilo.lat_text(c.latitude_deg)), ("Longitud", estilo.lon_text(c.longitude_deg))]
        if c.orthometric_height_m is not None:
            rows.append((height_label(c), num(c.orthometric_height_m, 3, "m")))
            rows.append(("Ondulación N" if c.height_kind != "normal" else "Anomalía de altura ζ",
                         num(c.undulation_m, 3, "m")))
        rows.append(("Altura elipsoidal (h)", num(c.ellipsoidal_height_m, 3, "m")))
        if c.convergence_deg is not None:
            sign = "−" if c.convergence_deg < 0 else "+"
            rows.append(("Convergencia", f"{sign}{estilo.azimuth_dms(abs(c.convergence_deg))} "
                                         f"({estilo.signed(c.convergence_deg, 6)}°)"))
            rows.append(("Factor de escala", num(c.scale_factor, 8)))
    return rows


def official_fields(position: Ecef) -> list[tuple[str, str]]:
    lat, lon, h = crs.motor.geodetic(position.x, position.y, position.z, crs.definition("EPSG:6365").ellipsoid)
    frame = f"{position.frame}, época {position.epoch:.1f}"
    return [("Marco", frame), ("Latitud", estilo.lat_text(lat)), ("Longitud", estilo.lon_text(lon)),
            ("Altura elipsoidal (GRS 1980)", num(h, 3, "m")), ("X", num(position.x, 3, "m")),
            ("Y", num(position.y, 3, "m")), ("Z", num(position.z, 3, "m"))]


def system_section(r: Report, c: TransformedCoordinate | None, choice: CoordinateChoice, problem: str,
                   result_frame: str) -> None:
    r.h1("Sistema de coordenadas")
    pairs = [("Sistema elegido", system_name(c, choice))]
    if c is not None:
        pairs += [("Código", c.crs_code or "—"), ("Marco del sistema", c.frame_text or "—"),
                  ("Marco del resultado", result_frame)]
        definition = crs.definition(c.crs_code)
        if definition is not None:
            if definition.is_projected:
                pairs.append(("Proyección", definition.projection_name))
            pairs.append(("Datum y elipsoide", f"{definition.datum} · {definition.ellipsoid.name}"))
        if c.transformation:
            pairs.append(("Transformación de datum", c.transformation))
        if choice.geoid_id:
            pairs.append(("Geoide", c.geoid_name or f"{choice.geoid_id} (no aplicado)"))
        else:
            pairs.append(("Geoide", "ninguno: las alturas son elipsoidales"))
    r.fields(pairs, columns=1, label_width=150)
    r.para("El programa no transforma entre marcos ni épocas: con la estación de la RGNA fija en su coordenada oficial, "
           "el resultado queda en el marco y la época de esa coordenada (ITRF2008, época 2010.0), que es lo que pide la "
           "norma del INEGI para ligarse a la RGNA.", "small")
    if problem:
        r.callout("error", "No se pudo expresar en el sistema elegido", [problem])
    if c is not None and c.attribution:
        r.para(" ".join(text(a) for a in c.attribution), "small")


def options_section(r: Report, options: ProcessingOptions, ephemeris_used: set[Ephemeris], log: list[str],
                    kinematic: bool = False) -> None:
    r.h1("Ajustes del proceso")
    engine = next((line for line in log if "RTKLIB" in line), "RTKLIB-EX (versión en la bitácora)")
    eph = ", ".join(EPHEMERIS_NAMES[e] for e in sorted(ephemeris_used, key=lambda e: e.value)) or \
        EPHEMERIS_NAMES[options.ephemeris]
    pairs = [
        ("Motor", engine.strip()),
        ("Modo", ("cinemático" if kinematic else "estático") + (", combinado adelante y atrás" if options.combined
                                                                  else ", solo hacia adelante")),
        ("Máscara de elevación", f"{options.elevation_mask_deg:g}°"),
        ("Constelaciones", ", ".join(options.constellations)),
        ("Frecuencias", {1: "L1", 2: "L1 + L2", 3: "L1 + L2 + L5"}.get(options.frequencies, str(options.frequencies))),
        ("Ionosfera", {"auto": "automática (libre de ionosfera en líneas largas)"}.get(options.ionosphere,
                                                                                      options.ionosphere)),
        ("Ambigüedades", f"{options.ambiguity_mode}, cociente mínimo {options.min_ratio:g}"),
        ("Intervalo", "el de los datos" if options.interval_s is None else f"{options.interval_s:g} s"),
        ("Efemérides", eph),
    ]
    if options.precise_files:
        pairs.append(("Productos precisos", ", ".join(Path(p).name for p in options.precise_files)))
    r.fields(pairs, columns=1, label_width=150)


def station_rows(stations: list[Station]) -> list[list[str]]:
    rows = []
    for s in stations:
        origin = "oficial" if s.official else "corregida por el usuario o propia"
        rows.append([f"{s.id}", s.name, f"{s.antenna_type or '—'} {s.radome or ''}".strip(),
                     num(s.antenna_height_m, 3), s.receiver or "—", f"{s.source or '—'} ({origin})"])
    return rows


def rover_antenna_note(rover: ObservationSet) -> str:
    if rover.antenna_pco_measured is False:
        return "PCO de la antena del rover sin medir."
    if rover.antenna_pco_measured is True:
        return "PCO de la antena del rover medido."
    return ""


def quality_section(r: Report, q: DataQuality, title: str = "Calidad de los datos del rover") -> None:
    r.h2(title)
    completeness = (100.0 * q.epochs / q.expected_epochs) if q.expected_epochs else None
    gap_total = sum((b - a).total_seconds() for a, b in q.gaps)
    pairs = [("Épocas", f"{q.epochs} de {q.expected_epochs} esperadas" +
              (f" ({num(completeness, 1)} %)" if completeness is not None else "")),
             ("Huecos", f"{len(q.gaps)}" + (f", {estilo.duration_text(gap_total)} en total" if q.gaps else "")),
             ("Satélites", f"{len(q.satellites)}"),
             ("Saltos de ciclo", f"{sum(q.cycle_slips.values())}")]
    for system, sig in sorted(q.signals.items()):
        pairs.append((f"Señales {system}", ", ".join(sig)))
    r.fields(pairs, columns=2, label_width=90)
    if q.multipath_m:
        r.space(4)
        r.table(["Señal", "Multitrayectoria (RMS)"],
                [[k, num(v, 3, "m")] for k, v in sorted(q.multipath_m.items())], widths=[120, 140], right={1})
    if q.cycle_slips:
        worst = sorted(q.cycle_slips.items(), key=lambda kv: (-kv[1], kv[0]))
        r.space(4)
        r.para("<b>Saltos de ciclo por satélite</b> (de más a menos): " +
               text(" · ".join(f"{sat} {n}" for sat, n in worst)), "small")
    if q.gaps:
        r.space(4)
        r.table(["Hueco desde", "hasta", "Duración"],
                [[estilo.utc_text(a), estilo.utc_text(b), estilo.duration_text((b - a).total_seconds())]
                 for a, b in q.gaps[:40]], widths=[150, 150, 100])
        if len(q.gaps) > 40:
            r.para(f"… y {len(q.gaps) - 40} huecos más (lista completa en la bitácora).", "small")
    if q.warnings:
        r.callout("warning", "Avisos del control de calidad", q.warnings)


def rtklib_charts(r: Report, pos_file: Path | None, stat_file: Path | None, options: ProcessingOptions,
                  label: str = "", heading: str = "") -> list[str]:
    """Las gráficas de una solución de RTKLIB. Devuelve los avisos (archivo que falta o no se pudo leer)."""
    notes: list[str] = []
    suffix = f" · {label}" if label else ""
    pos = stat = None
    if pos_file and Path(pos_file).is_file():
        pos = read_pos(pos_file)
    elif pos_file:
        notes.append(f"No se encontró la solución {Path(pos_file).name}: sin gráficas de estado ni de cociente.")
    if stat_file and Path(stat_file).is_file():
        stat = read_stat(stat_file)
    elif stat_file:
        notes.append(f"No se encontró {Path(stat_file).name}: sin gráficas de cielo, satélites, residuos ni DOP.")
    images: list[tuple[bytes, float]] = []
    if stat is not None and stat.satellites:
        images.append((graficas.sky_plot(stat, options.elevation_mask_deg, f"Cielo{suffix}"), 380))
    if pos is not None and pos.epochs:
        images.append((graficas.ratio_state(pos, options.min_ratio, f"Estado de la solución y cociente{suffix}"),
                       CONTENT_WIDTH))
    if stat is not None and stat.satellites:
        images.append((graficas.satellites_time(stat, f"Satélites usados en el tiempo{suffix}"), CONTENT_WIDTH))
        images.append((graficas.dop(dop_series(stat, options.elevation_mask_deg), f"DOP de la solución{suffix}"),
                       CONTENT_WIDTH))
        images.append((graficas.residuals(stat, f"Residuos de fase por satélite (L1){suffix}"), CONTENT_WIDTH))
    for i, (png, width) in enumerate(images):
        r.image(png, width=width, heading=heading if i == 0 else "")
    if not images and heading:
        r.h2(heading)
    return notes


def warnings_section(r: Report, warnings: list[str]) -> None:
    r.h1("Avisos")
    if warnings:
        r.callout("warning", f"{len(warnings)} aviso{'s' if len(warnings) != 1 else ''}", warnings)
    else:
        r.para("Sin avisos.")


def annex(r: Report, log: list[str]) -> None:
    r.page_break()
    r.h1("Anexo: bitácora del proceso")
    r.para("Versión de RTKLIB, configuración completa y avisos, tal como quedaron en el proyecto.", "small")
    r.log(log)


def ecef_sigma(cov) -> tuple[float, float, float]:
    return tuple(math.sqrt(max(cov[i][i], 0.0)) for i in range(3))   # type: ignore[return-value]
