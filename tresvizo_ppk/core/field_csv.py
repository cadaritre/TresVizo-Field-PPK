"""Lector del CSV de puntos de TresVizo Field (formato común iOS/Android, `docs/csv-export.md` del repo TresVizoField).

Lee la disposición «Todas las columnas» (y cualquier otra que conserve los nombres de las columnas que hacen falta),
con encabezados en inglés (Emlid) o en español, separador coma, punto y coma o tabulador, coma decimal con punto y
coma o tabulador, UTF-8 con o sin BOM, UTF-16 con BOM o Windows-1252, y comillas RFC 4180.

Por punto toma: nombre, código, inicio y fin del promedio (hora del receptor, escrita en la zona del teléfono con su
desfase: «2026-10-15 12:44:19.8 UTC-06:00»), altura de antena (vertical del jalón), desplazamiento de antena (case),
PCO, altura total, lo medido en campo (latitud, longitud y altura elipsoidal del suelo, y su marco) y el estado de la
solución. Los puntos sin medición (tecleados o importados) no se posprocesan: se informan aparte.
"""
from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errors import UserError
from ..model import Ecef, FieldPoint
from .geodesy import geodetic_to_ecef

# columna lógica → nombres aceptados (inglés de Emlid y español de la app)
COLUMNS = {
    "name": ("Name", "Nombre"),
    "code": ("Code", "Código"),
    "longitude": ("Longitude", "Longitud"),
    "latitude": ("Latitude", "Latitud"),
    "height": ("Ellipsoidal height", "Altura elipsoidal"),
    "origin": ("Origin", "Origen"),
    "antenna_height": ("Antenna height", "Altura de antena"),
    "solution": ("Solution status", "Estado de la solución"),
    "start": ("Averaging start", "Inicio del promedio"),
    "end": ("Averaging end", "Fin del promedio"),
    "samples": ("Samples", "Épocas"),
    "device": ("Device type", "Receptor"),
    "antenna_offset": ("Antenna offset", "Desplazamiento de antena"),
    "antenna_pco": ("Antenna PCO", "PCO de la antena"),
    "frame": ("Frame", "Marco"),
    "epoch": ("Observation epoch", "Época de observación"),
    "time_source": ("Time source", "Fuente de la hora"),
    "capture_mode": ("Capture mode", "Modo de captura"),
    "duration": ("Averaging duration", "Duración del promedio"),
    "antenna_ellipsoidal": ("Antenna ellipsoidal height", "Altura elipsoidal de la antena"),
    "total_height": ("Total antenna height", "Altura total de la antena"),
    "tilt_compensated": ("Tilt compensated", "Compensada"),
}
SOLUTIONS = {"FIX": "FIJO", "FLOAT": "FLOTANTE", "DGPS": "DGPS", "SINGLE": "AUTÓNOMO", "ESTIMATED": "ESTIMADO",
             "MANUAL": "MANUAL", "SIMULATED": "SIMULADO"}
CASE_OFFSET = {"meridianv": 0.116, "meridianvi": 0.152}
_TIME = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2}):(\d{2}(?:[.,]\d+)?)\s*"
                   r"(?:(?:UTC|GMT|Z)\s*(?:([+-−])(\d{1,2})(?::?(\d{2}))?)?)?\s*$")


@dataclass
class FieldCsv:
    points: list[FieldPoint]
    skipped: list[str] = field(default_factory=list)      # «P7: sin medición (Manual)»
    warnings: list[str] = field(default_factory=list)
    header: list[str] = field(default_factory=list)


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _number(text: str, decimal_comma: bool) -> float | None:
    text = (text or "").strip().lstrip("'")
    if not text:
        return None
    if decimal_comma:
        text = text.replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_time(text: str) -> datetime | None:
    """«2026-10-15 12:44:19.8 UTC-06:00» → datetime UTC. Sin zona se toma UTC."""
    m = _TIME.match(text or "")
    if not m:
        return None
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    sec = float(m.group(6).replace(",", "."))
    sign, oh, om = m.group(7), m.group(8), m.group(9)
    local = datetime(y, mo, d, h, mi, tzinfo=timezone.utc) + timedelta(seconds=sec)
    if sign and oh:
        offset = timedelta(hours=int(oh), minutes=int(om or 0))
        local = local - offset if sign == "+" else local + offset
    return local


def read_field_csv_detailed(path: Path) -> FieldCsv:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise UserError(f"No se pudo leer «{path.name}»: {error.strerror or error}.") from error
    if len(raw) > 20 * 1024 * 1024:
        raise UserError("El CSV pasa de 20 MB: divídelo en partes.")
    text = _decode(raw)
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise UserError("El CSV está vacío.")
    first = lines[0]
    separator = max([",", ";", "\t"], key=lambda s: first.count(s))
    rows = list(csv.reader(io.StringIO(text), delimiter=separator))
    rows = [r for r in rows if any(c.strip() for c in r)]
    header = [h.strip().lstrip("﻿") for h in rows[0]]
    index: dict[str, int] = {}
    lowered = [h.lower() for h in header]
    for key, names in COLUMNS.items():
        for name in names:
            if name.lower() in lowered:
                index[key] = lowered.index(name.lower())
                break
    missing = [COLUMNS[k][0] for k in ("name", "end") if k not in index]
    if missing:
        raise UserError("El CSV no es de «Todas las columnas» de TresVizo Field: le faltan " + ", ".join(missing)
                        + ". Exporta otra vez con «Todas las columnas».")
    decimal_comma = separator in (";", "\t") and _looks_decimal_comma(rows[1:], index)
    result = FieldCsv(points=[], header=header)
    seen: set[str] = set()
    for number, row in enumerate(rows[1:], start=2):
        def col(key: str) -> str:
            k = index.get(key)
            return row[k].strip() if k is not None and k < len(row) else ""
        name = col("name").lstrip("'")
        if not name:
            result.skipped.append(f"Línea {number}: sin nombre.")
            continue
        origin = col("origin")
        end = parse_time(col("end"))
        start = parse_time(col("start")) or end
        if end is None:
            reason = f"sin medición ({origin})" if origin and origin.lower() not in ("global", "") else \
                "sin hora de medición"
            result.skipped.append(f"{name}: {reason}.")
            continue
        if start > end:
            start, end = end, start
        if name in seen:
            result.warnings.append(f"El nombre {name} se repite en el CSV.")
        seen.add(name)
        pole = _number(col("antenna_height"), decimal_comma)
        offset = _number(col("antenna_offset"), decimal_comma)
        total = _number(col("total_height"), decimal_comma)
        device = col("device")
        if offset is None:
            offset = CASE_OFFSET.get(device.replace(" ", "").lower())
        if total is None and pole is not None:
            total = pole + (offset or 0.0)
        lat = _number(col("latitude"), decimal_comma)
        lon = _number(col("longitude"), decimal_comma)
        h = _number(col("height"), decimal_comma)
        frame = col("frame") or "ITRF2008"
        field_position = None
        if lat is not None and lon is not None and h is not None:
            x, y, z = geodetic_to_ecef(lat, lon, h)
            field_position = Ecef(x, y, z, frame, _number(col("epoch"), decimal_comma) or 2010.0)
        solution = col("solution").upper()
        point = FieldPoint(name=name, code=col("code"), start=start, end=end,
                           antenna_height_m=pole if pole is not None else float("nan"),
                           field_position=field_position, field_solution=SOLUTIONS.get(solution, solution),
                           row={header[i]: (row[i] if i < len(row) else "") for i in range(len(header))},
                           antenna_offset_m=offset, antenna_pco_m=_number(col("antenna_pco"), decimal_comma) or 0.0,
                           total_height_m=total)
        result.points.append(point)
    if not result.points:
        raise UserError("El CSV no tiene puntos medidos con su hora: no hay nada que posprocesar.")
    return result


def _looks_decimal_comma(rows: list[list[str]], index: dict[str, int]) -> bool:
    for key in ("latitude", "longitude", "height", "antenna_height"):
        k = index.get(key)
        if k is None:
            continue
        for row in rows[:20]:
            if k < len(row) and re.fullmatch(r"\s*-?\d+,\d+\s*", row[k] or ""):
                return True
    return False


def read_field_csv(path: Path) -> list[FieldPoint]:
    """Los puntos medidos del CSV de TresVizo Field (§8). `antenna_height_m` es la altura del jalón (NaN si no la
    trae) y `total_height_m` la de la marca a la ARP (jalón más el case del receptor)."""
    return read_field_csv_detailed(path).points
