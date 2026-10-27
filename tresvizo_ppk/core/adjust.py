"""Ajuste por mínimos cuadrados de un punto nuevo a partir de 2 o más líneas base con las bases fijas.

Modelo: cada línea base k da una observación de la posición del punto, R_k = B_k + v_k (B_k fija en su coordenada
oficial, v_k el vector con su covarianza C_k, más la de la coordenada de la base si se conoce). Incógnita: x (3).
Solución: x = (Σ C_k⁻¹)⁻¹ Σ C_k⁻¹ R_k; residuos r_k = R_k − x (en ENU del punto); σ0² = Σ rᵀC⁻¹r / gl con
gl = 3(n − 1).

Pruebas: chi² global al 95 %, de una cola (Σ rᵀC⁻¹r ≤ χ²₀.₉₅(gl); por debajo de χ²₀.₀₅ solo se avisa que el
modelo de error es pesimista); prueba w de Baarda (data snooping) de cada componente
de cada línea base con su varianza de residuo, valor crítico 3.29 (α = 0.1 %, β = 20 %, el habitual en geodesia).
Con dos bases, los residuos de las dos líneas son iguales y opuestos: se detecta que no cuadran pero no cuál falla.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..model import AdjustmentResult, Ecef, Enu
from .geodesy import covariance_ecef_to_enu, enu_rotation, ecef_to_geodetic

W_CRITICAL = 3.29


@dataclass
class LineObservation:
    base_id: str
    position: np.ndarray        # B_k + v_k (ECEF)
    covariance: np.ndarray      # 3×3 ECEF, m²


# --- Distribución chi² (sin scipy) -----------------------------------------------------------------------------------

def _gammainc_lower(a: float, x: float) -> float:
    """P(a, x) regularizada (serie o fracción continua, Numerical Recipes §6.2)."""
    if x <= 0:
        return 0.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        term = total = 1.0 / a
        ap = a
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + a * math.log(x) - gln)
    b = x + 1.0 - a
    c = 1.0 / 1e-300
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - gln) * h


def chi2_cdf(x: float, dof: int) -> float:
    return _gammainc_lower(dof / 2.0, x / 2.0)


def chi2_ppf(p: float, dof: int) -> float:
    lo, hi = 0.0, max(10.0, dof * 10.0)
    while chi2_cdf(hi, dof) < p:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if chi2_cdf(mid, dof) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --- Ajuste -----------------------------------------------------------------------------------------------------------

def adjust(lines: list[LineObservation], frame: str, epoch: float) -> AdjustmentResult:
    if len(lines) < 2:
        raise ValueError("el ajuste necesita al menos dos líneas base")
    weights = [np.linalg.inv(l.covariance) for l in lines]
    normal = sum(weights)
    rhs = sum(w @ l.position for w, l in zip(weights, lines))
    qxx = np.linalg.inv(normal)
    x = qxx @ rhs
    dof = 3 * (len(lines) - 1)
    lat, lon, _ = ecef_to_geodetic(*x)
    rot = enu_rotation(lat, lon)
    chi2 = 0.0
    residuals: dict[str, Enu] = {}
    standardized: dict[str, Enu] = {}
    for w, l in zip(weights, lines):
        r = l.position - x                      # observación − ajustada (ECEF)
        chi2 += float(r @ w @ r)
        r_enu = rot @ r
        residuals[l.base_id] = Enu(*(float(v) for v in r_enu))
        # covarianza del residuo: Q_rr = C_k − Q_xx (la observación y la ajustada están correlacionadas así)
        q_rr = rot @ (l.covariance - qxx) @ rot.T
        w_test = [float(r_enu[i] / math.sqrt(q_rr[i, i])) if q_rr[i, i] > 1e-14 else 0.0 for i in range(3)]
        standardized[l.base_id] = Enu(*w_test)
    sigma0 = math.sqrt(chi2 / dof) if dof > 0 else 0.0
    lower = chi2_ppf(0.05, dof)            # por debajo: los errores a priori son pesimistas (no es falla)
    upper = chi2_ppf(0.95, dof)            # por encima: las líneas base no cuadran entre sí con su precisión
    passed = chi2 <= upper
    # covarianza del punto: a priori, escalada por σ0² si el ajuste dice que los datos son peores que su modelo
    scale = max(1.0, sigma0 ** 2)
    cov = qxx * scale
    cov_enu = covariance_ecef_to_enu(x, cov)
    flagged: list[str] = []
    worst = max(standardized.items(), key=lambda kv: max(abs(kv[1].e), abs(kv[1].n), abs(kv[1].u)))
    worst_w = max(abs(worst[1].e), abs(worst[1].n), abs(worst[1].u))
    if len(lines) == 2:
        if not passed or worst_w > W_CRITICAL:
            flagged = [l.base_id for l in lines]
    elif worst_w > W_CRITICAL:
        flagged = [worst[0]]
    return AdjustmentResult(
        position=Ecef(float(x[0]), float(x[1]), float(x[2]), frame, epoch),
        covariance_ecef=_matrix3(cov),
        sigma_enu=Enu(*(math.sqrt(max(0.0, float(cov_enu[i, i]))) for i in range(3))),
        residuals_enu=residuals, sigma0=sigma0, chi2=chi2, degrees_of_freedom=dof, passed=passed,
        flagged_baselines=flagged, standardized_residuals=standardized, critical_value=W_CRITICAL,
        chi2_lower=lower, chi2_upper=upper)


def _matrix3(m: np.ndarray):
    return tuple(tuple(float(v) for v in row) for row in m)
