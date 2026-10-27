"""Tiempo y posiciones del Sol y la Luna para los modelos del procesador de líneas largas.

Las posiciones son las de baja precisión de Montenbruck y Gill (Satellite Orbits, 2000, §3.3.2): el Sol a ~0.01°
y la Luna a ~0.1° (unos minutos de arco). Bastan para lo que se usan aquí: la marea terrestre sólida (un error de
0.1° en la dirección de la Luna mueve el desplazamiento de marea menos de 0.5 mm) y la orientación nominal de los
satélites (eje hacia el Sol) para el centro de fase del satélite y el «wind-up» de fase.

Tiempo: segundos GPS desde el 6-1-1980 (GPST) como `float`; UTC = GPST − segundos intercalares.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from ..geodesy import GPS_EPOCH, leap_seconds

AU = 149_597_870_700.0
J2000_JD = 2451545.0
GPS_EPOCH_JD = 2444244.5          # 6-1-1980 00:00
OBLIQUITY_RAD = math.radians(23.43929111)


def gps_seconds(moment: datetime) -> float:
    """`datetime` en tiempo GPS (ingenuo o con zona, por convención del motor) → segundos GPS."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment - GPS_EPOCH).total_seconds()


def gps_datetime(seconds: float) -> datetime:
    return GPS_EPOCH + timedelta(seconds=float(seconds))


def leap_at_gps(seconds: float) -> int:
    approx = gps_datetime(seconds)
    return leap_seconds(approx - timedelta(seconds=leap_seconds(approx)))


def utc_jd(gps_s: float) -> float:
    """Día juliano UTC de un instante GPS."""
    return GPS_EPOCH_JD + (gps_s - leap_at_gps(gps_s)) / 86400.0


def gmst_rad(jd_ut1: float) -> float:
    """Tiempo sidéreo medio de Greenwich (IAU 1982, suficiente al nivel de este módulo)."""
    t = (jd_ut1 - J2000_JD) / 36525.0
    deg = 280.46061837 + 360.98564736629 * (jd_ut1 - J2000_JD) + 0.000387933 * t * t - t ** 3 / 38710000.0
    return math.radians(deg % 360.0)


def _eci_to_ecef(vec: np.ndarray, jd_ut1: float) -> np.ndarray:
    g = gmst_rad(jd_ut1)
    c, s = math.cos(g), math.sin(g)
    return np.array([c * vec[0] + s * vec[1], -s * vec[0] + c * vec[1], vec[2]])


def _ecliptic_to_equatorial(lon: float, lat: float, r: float) -> np.ndarray:
    x = r * math.cos(lat) * math.cos(lon)
    y = r * math.cos(lat) * math.sin(lon)
    z = r * math.sin(lat)
    ce, se = math.cos(OBLIQUITY_RAD), math.sin(OBLIQUITY_RAD)
    return np.array([x, ce * y - se * z, se * y + ce * z])


def sun_moon_ecef(gps_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Posiciones geocéntricas (m) del Sol y la Luna en el marco terrestre en el instante GPS dado."""
    jd = utc_jd(gps_s)
    jd_tt = jd + (leap_at_gps(gps_s) + 19.0 + 32.184) / 86400.0
    t = (jd_tt - J2000_JD) / 36525.0
    arcsec = math.pi / (180.0 * 3600.0)
    # Sol
    m = math.radians(357.5256 + 35999.049 * t)
    lon = math.radians(282.9400) + m + (6892.0 * math.sin(m) + 72.0 * math.sin(2 * m)) * arcsec
    lon += math.radians(1.3972 * t)                       # precesión a la época de la fecha
    r = (149.619 - 2.499 * math.cos(m) - 0.021 * math.cos(2 * m)) * 1e9
    sun = _ecliptic_to_equatorial(lon, 0.0, r)
    # Luna
    l0 = math.radians(218.31617 + 481267.88088 * t)
    l = math.radians(134.96292 + 477198.86753 * t)
    lp = math.radians(357.52543 + 35999.04944 * t)
    f = math.radians(93.27283 + 483202.01873 * t)
    d = math.radians(297.85027 + 445267.11135 * t)
    dlon = (22640 * math.sin(l) + 769 * math.sin(2 * l) - 4586 * math.sin(l - 2 * d) + 2370 * math.sin(2 * d)
            - 668 * math.sin(lp) - 412 * math.sin(2 * f) - 212 * math.sin(2 * l - 2 * d)
            - 206 * math.sin(l + lp - 2 * d) + 192 * math.sin(l + 2 * d) - 165 * math.sin(lp - 2 * d)
            + 148 * math.sin(l - lp) - 125 * math.sin(d) - 110 * math.sin(l + lp) - 55 * math.sin(2 * f - 2 * d))
    mlon = l0 + dlon * arcsec + math.radians(1.3972 * t)
    mlat = (18520 * math.sin(f + (mlon - l0 - math.radians(1.3972 * t)) + (412 * math.sin(2 * f)
                                                                           + 541 * math.sin(lp)) * arcsec)
            - 526 * math.sin(f - 2 * d) + 44 * math.sin(l + f - 2 * d) - 31 * math.sin(-l + f - 2 * d)
            - 25 * math.sin(-2 * l + f) - 23 * math.sin(lp + f - 2 * d) + 21 * math.sin(-l + f)
            + 11 * math.sin(-lp + f - 2 * d)) * arcsec
    mr = (385000 - 20905 * math.cos(l) - 3699 * math.cos(2 * d - l) - 2956 * math.cos(2 * d)
          - 570 * math.cos(2 * l) + 246 * math.cos(2 * l - 2 * d) - 205 * math.cos(lp - 2 * d)
          - 171 * math.cos(l + 2 * d) - 152 * math.cos(l + lp - 2 * d)) * 1000.0
    moon = _ecliptic_to_equatorial(mlon, mlat, mr)
    return _eci_to_ecef(sun, jd), _eci_to_ecef(moon, jd)


def mjd_and_hour_utc(gps_s: float) -> tuple[int, float]:
    """(día juliano modificado entero, hora del día) en UTC, para la marea terrestre."""
    mjd = utc_jd(gps_s) - 2400000.5
    day = math.floor(mjd)
    return int(day), (mjd - day) * 24.0
