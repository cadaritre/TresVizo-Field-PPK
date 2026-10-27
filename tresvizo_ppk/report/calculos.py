"""Cálculos del informe: covarianzas en ENU, elipse de error, precisión al 95 %, acimut y distancia cenital, y los
valores críticos de la prueba χ² del ajuste. Sin dependencias fuera de la biblioteca estándar."""
from __future__ import annotations

import math

from ..model import Ecef, Matrix3

# Factores al 95 %: 1.96 en una dimensión; 2.4477 = √(χ²₂;0.95) para el círculo de una normal bidimensional.
K95_1D = 1.959964
K95_2D = 2.447747

GRS80_A = 6378137.0
GRS80_F = 1 / 298.257222101


def geodetic(p: Ecef) -> tuple[float, float, float]:
    """ECEF → latitud y longitud geodésicas (grados) y altura sobre GRS 1980, por iteración (para orientar ENU)."""
    a, f = GRS80_A, GRS80_F
    e2 = f * (2 - f)
    lon = math.atan2(p.y, p.x)
    r = math.hypot(p.x, p.y)
    lat = math.atan2(p.z, r * (1 - e2))
    h = 0.0
    for _ in range(10):
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = r / math.cos(lat) - n if abs(lat) < math.radians(89) else p.z / math.sin(lat) - n * (1 - e2)
        lat = math.atan2(p.z, r * (1 - e2 * n / (n + h)))
    return math.degrees(lat), math.degrees(lon), h


def rotation(lat_deg: float, lon_deg: float) -> list[list[float]]:
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    return [[-so, co, 0.0], [-sl * co, -sl * so, cl], [cl * co, cl * so, sl]]


def to_enu(v: tuple[float, float, float], lat: float, lon: float) -> tuple[float, float, float]:
    r = rotation(lat, lon)
    return tuple(sum(r[i][k] * v[k] for k in range(3)) for i in range(3))   # type: ignore[return-value]


def cov_enu(cov: Matrix3, lat: float, lon: float) -> list[list[float]]:
    r = rotation(lat, lon)
    rc = [[sum(r[i][k] * cov[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return [[sum(rc[i][k] * r[j][k] for k in range(3)) for j in range(3)] for i in range(3)]


def correlation(cov) -> list[list[float]]:
    s = [math.sqrt(max(cov[i][i], 0.0)) for i in range(3)]
    return [[cov[i][j] / (s[i] * s[j]) if s[i] > 0 and s[j] > 0 else 0.0 for j in range(3)] for i in range(3)]


def error_ellipse(c_ee: float, c_nn: float, c_en: float) -> tuple[float, float, float]:
    """Semiejes mayor y menor (1σ) y acimut del mayor (grados desde el norte) de la elipse de error horizontal."""
    mean = (c_ee + c_nn) / 2
    diff = math.hypot((c_ee - c_nn) / 2, c_en)
    a = math.sqrt(max(mean + diff, 0.0))
    b = math.sqrt(max(mean - diff, 0.0))
    azimuth = math.degrees(0.5 * math.atan2(2 * c_en, c_nn - c_ee)) % 180.0
    return a, b, azimuth


def horizontal_95(sigma_e: float, sigma_n: float) -> float:
    return K95_2D * math.sqrt((sigma_e ** 2 + sigma_n ** 2) / 2)


def vertical_95(sigma_u: float) -> float:
    return K95_1D * sigma_u


def azimuth_zenith(base: Ecef, vector: tuple[float, float, float]) -> tuple[float, float]:
    """Acimut geodésico (desde el norte, en el horizonte de la base) y distancia cenital del vector, en grados."""
    lat, lon, _ = geodetic(base)
    e, n, u = to_enu(vector, lat, lon)
    az = math.degrees(math.atan2(e, n)) % 360.0
    zen = math.degrees(math.atan2(math.hypot(e, n), u))
    return az, zen


def length_sigma(vector: tuple[float, float, float], cov: Matrix3) -> float:
    length = math.sqrt(sum(v * v for v in vector))
    if length == 0:
        return 0.0
    u = [v / length for v in vector]
    return math.sqrt(max(sum(u[i] * cov[i][j] * u[j] for i in range(3) for j in range(3)), 0.0))


# --- χ² --------------------------------------------------------------------------------------------------------------

def _gamma_p(a: float, x: float) -> float:
    """Gamma incompleta regularizada inferior P(a, x) (serie o fracción continua, Numerical Recipes §6.2)."""
    if x <= 0:
        return 0.0
    gln = math.lgamma(a)
    if x < a + 1:
        ap, s, d = a, 1.0 / a, 1.0 / a
        for _ in range(500):
            ap += 1
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-15:
                break
        return s * math.exp(-x + a * math.log(x) - gln)
    b = x + 1 - a
    c, d = 1e300, 1 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return 1 - math.exp(-x + a * math.log(x) - gln) * h


def chi2_cdf(x: float, dof: int) -> float:
    return _gamma_p(dof / 2, x / 2)


def chi2_quantile(p: float, dof: int) -> float:
    lo, hi = 0.0, max(10.0, dof * 10.0 + 100)
    for _ in range(200):
        mid = (lo + hi) / 2
        if chi2_cdf(mid, dof) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def chi2_bounds(dof: int, alpha: float = 0.05) -> tuple[float, float]:
    """Intervalo de aceptación de dos colas de la prueba de bondad del ajuste (χ² con `dof` grados de libertad)."""
    return chi2_quantile(alpha / 2, dof), chi2_quantile(1 - alpha / 2, dof)
