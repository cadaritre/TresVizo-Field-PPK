"""Modelos de la observación del procesador de líneas largas: troposfera a priori, orientación nominal de los
satélites, «wind-up» de fase y centros de fase combinados en la libre de ionosfera.

**Troposfera.** Retardo hidrostático cenital de Saastamoinen (1972) con la presión de la atmósfera estándar a la
altura de la estación y retardo húmedo cenital a priori de Saastamoinen con 50 % de humedad (≈ 0.1 m); los dos se
llevan a la elevación con la GMF (`iers.GmfSitio`). El húmedo se **estima** por estación (`ajuste`), así que lo que
importa del a priori es el hidrostático: un error de 10 hPa en la presión son 2.3 cm de retardo cenital que el
estimado absorbe casi entero (las funciones de mapeo hidrostática y húmeda difieren < 1 % arriba de 15°).

**Orientación nominal (yaw-steering).** z hacia el centro de la Tierra, y = z × (dirección al Sol), x = y × z. No
se modelan los giros de mediodía y medianoche ni los eclipses: en la diferencia entre dos receptores a ≤ 200 km
el satélite es el mismo, y su giro cambia el wind-up y el PCO casi igual en los dos (< 1 mm a 150 km).

**Wind-up** (Wu et al. 1993, Manuscripta Geodaetica 18:91): rotación relativa de las antenas, en ciclos, igual en
todas las frecuencias; en la libre de ionosfera en metros es λ_NL·ω.
"""
from __future__ import annotations

import math

import numpy as np

from .antena import ANTEX_FREQ, Antenna


def standard_pressure_hpa(height_m: float) -> float:
    h = max(-100.0, min(height_m, 9000.0))
    return 1013.25 * (1.0 - 2.2557e-5 * h) ** 5.2568


def zenith_delays(lat_rad: float, height_m: float, humidity: float = 0.5) -> tuple[float, float]:
    """(hidrostático, húmedo) cenitales a priori en metros (Saastamoinen con atmósfera estándar)."""
    h = max(0.0, min(height_m, 9000.0))
    pressure = standard_pressure_hpa(h)
    temp_c = 15.0 - 6.5e-3 * h
    temp_k = temp_c + 273.16
    e = 6.108 * humidity * math.exp((17.15 * temp_k - 4684.0) / (temp_k - 38.45))
    zhd = 0.0022768 * pressure / (1.0 - 0.00266 * math.cos(2.0 * lat_rad) - 0.00028 * h / 1000.0)
    zwd = 0.002277 * (1255.0 / temp_k + 0.05) * e
    return zhd, zwd


def attitude(sat_pos: np.ndarray, sun_pos: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ejes x, y, z (n×3) del satélite con orientación nominal."""
    ez = -sat_pos / np.linalg.norm(sat_pos, axis=1)[:, None]
    es = sun_pos - sat_pos
    es /= np.linalg.norm(es, axis=1)[:, None]
    ey = np.cross(ez, es)
    ey /= np.linalg.norm(ey, axis=1)[:, None]
    ex = np.cross(ey, ez)
    return ex, ey, ez


def wind_up(los: np.ndarray, ex: np.ndarray, ey: np.ndarray, north: np.ndarray, east: np.ndarray) -> np.ndarray:
    """Wind-up en ciclos (n), continuo a lo largo de la serie. `los`: unitario del satélite al receptor."""
    k = los
    ds = ex - k * np.sum(k * ex, axis=1)[:, None] - np.cross(k, ey)
    xr, yr = np.broadcast_to(north, k.shape), np.broadcast_to(-east, k.shape)
    dr = xr - k * np.sum(k * xr, axis=1)[:, None] + np.cross(k, yr)
    cosp = np.sum(ds * dr, axis=1) / (np.linalg.norm(ds, axis=1) * np.linalg.norm(dr, axis=1))
    ph = np.arccos(np.clip(cosp, -1.0, 1.0)) / (2.0 * math.pi)
    sign = np.sum(k * np.cross(ds, dr), axis=1)
    ph = np.where(sign < 0, -ph, ph)
    # continuidad: out[i] = ph[i] + n[i], n[i] = n[i−1] + round(ph[i−1] − ph[i]) (huecos: se arrastra el último)
    good = np.isfinite(ph)
    if not good.any():
        return ph
    filled = ph.copy()
    idx = np.where(good, np.arange(len(ph)), 0)
    np.maximum.accumulate(idx, out=idx)
    filled = ph[idx]
    first = int(np.argmax(good))
    filled[:first] = ph[first]
    n = np.concatenate([[math.floor(0.5 - filled[0])], np.round(filled[:-1] - filled[1:])])
    out = filled + np.cumsum(n)
    out[~good] = np.nan
    return out


def if_offset(antenna: Antenna | None, system: str, band1: str, band2: str, alpha: float, beta: float
              ) -> tuple[np.ndarray, tuple, list[str]]:
    """PCO combinado (α·PCO1 − β·PCO2) y los patrones para la PCV combinada; notas de frecuencias sustituidas."""
    notes: list[str] = []
    if antenna is None:
        return np.zeros(3), (None, None), notes
    pats = []
    for band in (band1, band2):
        wanted = ANTEX_FREQ.get((system, band), "")
        pat, used = antenna.pattern(wanted)
        if pat is not None and used != wanted:
            notes.append(f"{antenna.type} {antenna.radome}: sin calibración {wanted}, se usa {used}")
        pats.append(pat)
    if pats[0] is None or pats[1] is None:
        notes.append(f"{antenna.type} {antenna.radome}: sin calibración para {system}{band1}/{band2}")
        return np.zeros(3), (None, None), notes
    return alpha * pats[0].pco - beta * pats[1].pco, (pats[0], pats[1]), notes


def if_pcv(patterns: tuple, alpha: float, beta: float, zenith_deg: np.ndarray, azimuth_deg=None) -> np.ndarray:
    p1, p2 = patterns
    if p1 is None or p2 is None:
        return np.zeros(np.shape(zenith_deg))
    return alpha * p1.pcv(zenith_deg, azimuth_deg) - beta * p2.pcv(zenith_deg, azimuth_deg)
