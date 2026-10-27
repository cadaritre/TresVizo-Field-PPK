"""Cálculos del informe: covarianzas en ENU, elipse de error, precisión al 95 %, acimut y distancia cenital, y los
valores críticos de la prueba χ² del ajuste. Sin dependencias fuera de la biblioteca estándar."""
from __future__ import annotations

import math
from decimal import Decimal

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


# --- Norma Técnica de Estándares de Exactitud Posicional (INEGI, DOF 23-12-2010) -----------------------------------
# Factores tal como los escribe la norma (no los redondeados de arriba), para que la cifra sea la de su fórmula.
CEP95_FACTOR = 1.2238       # art. 6: CEP95 = 1.2238 (σφ + σλ), σ de la latitud y de la longitud en metros
EPV_FACTOR = 1.9600         # art. 7: EPV = 1.9600 σ; art. 12: EPV = 1.9600 (σGNSS² + σN²)^½

# Art. 8, tabla 1 «Órdenes de Exactitud Posicional Horizontal»: el límite superior de cada rango, en milímetros
# («Hasta 0.010 metros», «De 0.011 a 0.020 metros», …, «De 200.001 a 500.000 metros»).
ACCURACY_ORDERS: tuple[tuple[int, str], ...] = (
    (10, "1 centímetro"), (20, "2 centímetros"), (50, "5 centímetros"), (100, "1 decímetro"),
    (200, "2 decímetros"), (500, "5 decímetros"), (1_000, "1 metro"), (2_000, "2 metros"), (5_000, "5 metros"),
    (10_000, "10 metros"), (20_000, "20 metros"), (50_000, "50 metros"), (100_000, "100 metros"),
    (200_000, "200 metros"), (500_000, "500 metros"))


def cep95(sigma_lat_m: float, sigma_lon_m: float) -> float:
    """NT EEP art. 6: CEP95 = 1.2238 (σφ + σλ), con σφ y σλ las desviaciones estándar (1σ) de la latitud y la
    longitud en metros (σ Norte y σ Este). La norma no pone condición sobre su cociente."""
    return CEP95_FACTOR * (sigma_lat_m + sigma_lon_m)


def epv(sigma_m: float) -> float:
    """NT EEP art. 7: EPV = 1.9600 σ de la altura geodésica (u ortométrica)."""
    return EPV_FACTOR * sigma_m


def epv_gnss_leveling(sigma_gnss_m: float, sigma_geoid_m: float) -> float:
    """NT EEP art. 12, nivelación GNSS: EPV = 1.9600 (σGNSS² + σN²)^½, con σN la del modelo geoidal del INEGI."""
    return EPV_FACTOR * math.sqrt(sigma_gnss_m ** 2 + sigma_geoid_m ** 2)


def millimeters(value_m: float) -> int:
    """La cifra en milímetros tal como sale impresa con tres decimales (la misma con la que se clasifica)."""
    return int(Decimal(f"{value_m:.3f}") * 1000)


def accuracy_order(cep95_m: float | None) -> str | None:
    """NT EEP art. 8, tabla 1: el orden de exactitud horizontal del CEP95, comparado al milímetro como lo da la
    tabla (0.0104 m → 0.010 → «1 centímetro»; 0.0106 m → 0.011 → «2 centímetros»). None sin dato o pasado de 500 m."""
    if cep95_m is None or not math.isfinite(cep95_m) or cep95_m < 0:
        return None
    mm = millimeters(cep95_m)
    for limit, name in ACCURACY_ORDERS:
        if mm <= limit:
            return name
    return None


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
