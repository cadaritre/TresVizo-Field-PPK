"""GGA y RMC, solo para la posición aproximada de la cabecera y la fecha de las MSM.

La altura de la GGA es la del receptor sobre su geoide más la separación que informa (campo 11):
en el MeridianV (UM980 con `CONFIG UNDULATION 0.0000`) la separación llega en 0 y la altura ya es
elipsoidal; en el MeridianVI (LG290P) se suman. Las dos dan altura elipsoidal WGS84.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from .framing import nmea_checksum_ok


@dataclass
class GgaFix:
    utc_tod_s: float
    lat: float
    lon: float
    height: float
    quality: int


def _coordinate(value: bytes, hemisphere: bytes, degree_digits: int) -> float | None:
    try:
        degrees = float(value[:degree_digits])
        minutes = float(value[degree_digits:])
    except ValueError:
        return None
    result = degrees + minutes / 60.0
    return -result if hemisphere in (b"S", b"W") else result


def _fields(line: bytes) -> list[bytes] | None:
    if not nmea_checksum_ok(line):
        return None
    return line[1:line.rfind(b"*")].split(b",")


def parse_gga(line: bytes) -> GgaFix | None:
    fields = _fields(line)
    if not fields or len(fields) < 12 or not fields[0].endswith(b"GGA"):
        return None
    try:
        quality = int(fields[6] or b"0")
        if quality == 0 or not fields[1] or not fields[2] or not fields[4] or not fields[9]:
            return None
        hhmmss = fields[1]
        tod = int(hhmmss[0:2]) * 3600 + int(hhmmss[2:4]) * 60 + float(hhmmss[4:])
        lat = _coordinate(fields[2], fields[3], 2)
        lon = _coordinate(fields[4], fields[5], 3)
        altitude = float(fields[9])
        separation = float(fields[11]) if fields[11] else 0.0
    except (ValueError, IndexError):
        return None
    if lat is None or lon is None:
        return None
    return GgaFix(tod, lat, lon, altitude + separation, quality)


def parse_rmc_datetime(line: bytes) -> _dt.datetime | None:
    """Fecha y hora UTC de una RMC válida (estado «A»)."""
    fields = _fields(line)
    if not fields or len(fields) < 10 or not fields[0].endswith(b"RMC"):
        return None
    if fields[2] != b"A" or len(fields[9]) != 6 or len(fields[1]) < 6:
        return None
    try:
        day, month, year = int(fields[9][0:2]), int(fields[9][2:4]), 2000 + int(fields[9][4:6])
        seconds = float(fields[1][4:])
        return _dt.datetime(year, month, day, int(fields[1][0:2]), int(fields[1][2:4])) + \
            _dt.timedelta(seconds=seconds)
    except ValueError:
        return None
