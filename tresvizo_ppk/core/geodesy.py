"""Geodesia básica del motor: elipsoide GRS80, ECEF ↔ geodésicas, ENU, acimut y distancia cenital, tiempo GPS.

El marco oficial de México (ITRF2008) usa el elipsoide GRS80. WGS 84 difiere de GRS80 en 0.1 mm en el semieje
menor: para el motor son el mismo.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

GRS80_A = 6378137.0
GRS80_F = 1.0 / 298.257222101
GRS80_E2 = GRS80_F * (2.0 - GRS80_F)

GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)

# Segundos intercalares GPS−UTC (IERS, Bulletin C). El último fue el 1-1-2017; si el IERS anuncia otro, se añade
# aquí (el RINEX de la base también lo trae en LEAP SECONDS, y RTKLIB usa su propia tabla).
_LEAP_SECONDS = [
    (datetime(1981, 7, 1, tzinfo=timezone.utc), 1), (datetime(1982, 7, 1, tzinfo=timezone.utc), 2),
    (datetime(1983, 7, 1, tzinfo=timezone.utc), 3), (datetime(1985, 7, 1, tzinfo=timezone.utc), 4),
    (datetime(1988, 1, 1, tzinfo=timezone.utc), 5), (datetime(1990, 1, 1, tzinfo=timezone.utc), 6),
    (datetime(1991, 1, 1, tzinfo=timezone.utc), 7), (datetime(1992, 7, 1, tzinfo=timezone.utc), 8),
    (datetime(1993, 7, 1, tzinfo=timezone.utc), 9), (datetime(1994, 7, 1, tzinfo=timezone.utc), 10),
    (datetime(1996, 1, 1, tzinfo=timezone.utc), 11), (datetime(1997, 7, 1, tzinfo=timezone.utc), 12),
    (datetime(1999, 1, 1, tzinfo=timezone.utc), 13), (datetime(2006, 1, 1, tzinfo=timezone.utc), 14),
    (datetime(2009, 1, 1, tzinfo=timezone.utc), 15), (datetime(2012, 7, 1, tzinfo=timezone.utc), 16),
    (datetime(2015, 7, 1, tzinfo=timezone.utc), 17), (datetime(2017, 1, 1, tzinfo=timezone.utc), 18),
]


def leap_seconds(utc: datetime) -> int:
    """GPS − UTC en segundos para un instante UTC."""
    count = 0
    for start, value in _LEAP_SECONDS:
        if utc >= start:
            count = value
    return count


def utc_to_gps(utc: datetime) -> datetime:
    """Hora UTC → hora GPS (misma clase datetime, marcada UTC por convención del motor)."""
    return utc + timedelta(seconds=leap_seconds(utc))


def gps_to_utc(gps: datetime) -> datetime:
    utc = gps - timedelta(seconds=leap_seconds(gps))
    # en el instante del salto la corrección cambia: se recalcula con la estimación
    return gps - timedelta(seconds=leap_seconds(utc))


def geodetic_to_ecef(lat_deg: float, lon_deg: float, h: float) -> tuple[float, float, float]:
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    n = GRS80_A / math.sqrt(1.0 - GRS80_E2 * math.sin(lat) ** 2)
    return ((n + h) * math.cos(lat) * math.cos(lon),
            (n + h) * math.cos(lat) * math.sin(lon),
            (n * (1.0 - GRS80_E2) + h) * math.sin(lat))


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """ECEF → (latitud, longitud en grados, altura elipsoidal). Iterativo, exacto al µm en la Tierra."""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p < 1e-9:
        lat = math.copysign(math.pi / 2.0, z)
        return math.degrees(lat), math.degrees(lon), abs(z) - GRS80_A * math.sqrt(1.0 - GRS80_E2)
    lat = math.atan2(z, p * (1.0 - GRS80_E2))
    h = 0.0
    for _ in range(10):
        n = GRS80_A / math.sqrt(1.0 - GRS80_E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        new = math.atan2(z, p * (1.0 - GRS80_E2 * n / (n + h)))
        if abs(new - lat) < 1e-14:
            lat = new
            break
        lat = new
    n = GRS80_A / math.sqrt(1.0 - GRS80_E2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), h


def enu_rotation(lat_deg: float, lon_deg: float) -> np.ndarray:
    """Matriz R (3×3) tal que enu = R · d_ecef."""
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sl, cl, sp, cp = math.sin(lon), math.cos(lon), math.sin(lat), math.cos(lat)
    return np.array([[-sl, cl, 0.0],
                     [-sp * cl, -sp * sl, cp],
                     [cp * cl, cp * sl, sp]])


def ecef_delta_to_enu(origin_xyz, delta_xyz) -> tuple[float, float, float]:
    lat, lon, _ = ecef_to_geodetic(*origin_xyz)
    e, n, u = enu_rotation(lat, lon) @ np.asarray(delta_xyz, dtype=float)
    return float(e), float(n), float(u)


def enu_to_ecef_delta(origin_xyz, enu) -> tuple[float, float, float]:
    lat, lon, _ = ecef_to_geodetic(*origin_xyz)
    dx, dy, dz = enu_rotation(lat, lon).T @ np.asarray(enu, dtype=float)
    return float(dx), float(dy), float(dz)


def covariance_ecef_to_enu(origin_xyz, cov) -> np.ndarray:
    lat, lon, _ = ecef_to_geodetic(*origin_xyz)
    r = enu_rotation(lat, lon)
    return r @ np.asarray(cov, dtype=float) @ r.T


def covariance_enu_to_ecef(origin_xyz, cov_enu) -> np.ndarray:
    lat, lon, _ = ecef_to_geodetic(*origin_xyz)
    r = enu_rotation(lat, lon)
    return r.T @ np.asarray(cov_enu, dtype=float) @ r


def azimuth_zenith(origin_xyz, delta_xyz) -> tuple[float, float]:
    """Acimut geodésico (desde el norte, horario, 0–360°) y distancia cenital (°) del vector visto desde el origen."""
    e, n, u = ecef_delta_to_enu(origin_xyz, delta_xyz)
    azimuth = math.degrees(math.atan2(e, n)) % 360.0
    horizontal = math.hypot(e, n)
    zenith = math.degrees(math.atan2(horizontal, u))
    return azimuth, zenith


def dms_to_degrees(text: str) -> float:
    """«18 29 42.99641» → grados decimales (con signo si lo trae)."""
    parts = text.replace("°", " ").replace("'", " ").replace('"', " ").split()
    if not parts:
        raise ValueError(text)
    sign = -1.0 if parts[0].startswith("-") else 1.0
    values = [abs(float(p)) for p in parts]
    while len(values) < 3:
        values.append(0.0)
    return sign * (values[0] + values[1] / 60.0 + values[2] / 3600.0)


def decimal_year(moment: datetime) -> float:
    start = datetime(moment.year, 1, 1, tzinfo=moment.tzinfo)
    end = datetime(moment.year + 1, 1, 1, tzinfo=moment.tzinfo)
    return moment.year + (moment - start).total_seconds() / (end - start).total_seconds()
