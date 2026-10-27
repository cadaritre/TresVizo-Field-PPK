"""Mínimos cuadrados enteros: LAMBDA (decorrelación de Teunissen) y búsqueda MLAMBDA (Chang et al.), tasa de
éxito del bootstrapping, prueba de razón y resolución parcial por tasa de éxito.

Referencias:
- P. J. G. Teunissen (1995), The least-squares ambiguity decorrelation adjustment: a method for fast GPS integer
  ambiguity estimation, J. Geodesy 70:65–82 (factorización Q = Lᵀ·D·L, transformaciones enteras de Gauss y
  permutaciones).
- X.-W. Chang, X. Yang y T. Zhou (2005), MLAMBDA: a modified LAMBDA method for integer least-squares estimation,
  J. Geodesy 79:552–565 (búsqueda con reducción de la elipse).
- P. J. G. Teunissen (1998), Success probability of integer GPS ambiguity rounding and bootstrapping, J. Geodesy
  72:606–612: P_s = Π (2Φ(1 / (2·σ_{i|I})) − 1), cota inferior de la tasa de éxito del ILS.
- P. J. G. Teunissen, P. Joosten y C. Tiberius (1999), Geometry-free ambiguity success rates in case of partial
  fixing, ION NTM: resolución parcial, fijar el subconjunto decorrelacionado más grande cuya tasa de éxito del
  bootstrapping llega al mínimo pedido.

La estructura sigue `lambda.c` de RTKLIB (T. Takasu, licencia BSD de 2 cláusulas, ver third_party/LICENCIAS.md):
mismas convenciones (Q = Lᵀ·diag(D)·L con L triangular inferior unitaria, búsqueda desde la última componente),
para poder probarla con los casos de su prueba unitaria `t_lambda.c`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

LOOP_MAX = 100_000


class LambdaError(ValueError):
    pass


def _round(x: float) -> float:
    return math.floor(x + 0.5)


def _sgn(x: float) -> float:
    return -1.0 if x <= 0.0 else 1.0


def ld(q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Q = Lᵀ·diag(D)·L (L triangular inferior con diagonal 1)."""
    n = q.shape[0]
    a = np.array(q, dtype=float)
    lmat = np.zeros((n, n))
    d = np.zeros(n)
    for i in range(n - 1, -1, -1):
        d[i] = a[i, i]
        if d[i] <= 0.0:
            raise LambdaError("la covarianza de las ambigüedades no es definida positiva")
        root = math.sqrt(d[i])
        lmat[i, :i + 1] = a[i, :i + 1] / root
        for j in range(i):
            a[j, :j + 1] -= lmat[i, :j + 1] * lmat[i, j]
        lmat[i, :i + 1] /= lmat[i, i]
    return lmat, d


def _gauss(lmat: np.ndarray, z: np.ndarray, i: int, j: int) -> None:
    mu = int(_round(lmat[i, j]))
    if mu:
        lmat[i:, j] -= mu * lmat[i:, i]
        z[:, j] -= mu * z[:, i]


def _perm(lmat: np.ndarray, d: np.ndarray, j: int, delta: float, z: np.ndarray) -> None:
    eta = d[j] / delta
    lam = d[j + 1] * lmat[j + 1, j] / delta
    d[j] = eta * d[j + 1]
    d[j + 1] = delta
    for k in range(j):
        a0, a1 = lmat[j, k], lmat[j + 1, k]
        lmat[j, k] = -lmat[j + 1, j] * a0 + a1
        lmat[j + 1, k] = eta * a0 + lam * a1
    lmat[j + 1, j] = lam
    lmat[j + 2:, [j, j + 1]] = lmat[j + 2:, [j + 1, j]]
    z[:, [j, j + 1]] = z[:, [j + 1, j]]


def reduction(q: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decorrelación LAMBDA: devuelve (Z, L, D) con Zᵀ·Q·Z = Lᵀ·diag(D)·L, Z entera unimodular."""
    lmat, d = ld(q)
    n = len(d)
    z = np.eye(n)
    j = k = n - 2
    while j >= 0:
        if j <= k:
            for i in range(j + 1, n):
                _gauss(lmat, z, i, j)
        delta = d[j] + lmat[j + 1, j] ** 2 * d[j + 1]
        if delta + 1e-6 < d[j + 1]:
            _perm(lmat, d, j, delta, z)
            k = j
            j = n - 2
        else:
            j -= 1
    return z, lmat, d


def search(lmat: np.ndarray, d: np.ndarray, zs: np.ndarray, m: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Búsqueda MLAMBDA de los m mejores vectores enteros: (candidatos n×m, distancias cuadradas m)."""
    n = len(d)
    s_mat = np.zeros((n, n))
    dist = np.zeros(n)
    zb = np.zeros(n)
    zc = np.zeros(n)
    step = np.zeros(n)
    zn = np.zeros((n, m))
    s = np.zeros(m)
    nn = imax = 0
    maxdist = 1e99
    k = n - 1
    zb[k] = zs[k]
    zc[k] = _round(zb[k])
    y = zb[k] - zc[k]
    step[k] = _sgn(y)
    for _ in range(LOOP_MAX):
        newdist = dist[k] + y * y / d[k]
        if newdist < maxdist:
            if k != 0:
                k -= 1
                dist[k] = newdist
                s_mat[k, :k + 1] = s_mat[k + 1, :k + 1] + (zc[k + 1] - zb[k + 1]) * lmat[k + 1, :k + 1]
                zb[k] = zs[k] + s_mat[k, k]
                zc[k] = _round(zb[k])
                y = zb[k] - zc[k]
                step[k] = _sgn(y)
            else:
                if nn < m:
                    if nn == 0 or newdist > s[imax]:
                        imax = nn
                    zn[:, nn] = zc
                    s[nn] = newdist
                    nn += 1
                else:
                    if newdist < s[imax]:
                        zn[:, imax] = zc
                        s[imax] = newdist
                        imax = int(np.argmax(s))
                    maxdist = s[imax]
                zc[0] += step[0]
                y = zb[0] - zc[0]
                step[0] = -step[0] - _sgn(step[0])
        else:
            if k == n - 1:
                break
            k += 1
            zc[k] += step[k]
            y = zb[k] - zc[k]
            step[k] = -step[k] - _sgn(step[k])
    else:
        raise LambdaError("la búsqueda de enteros no terminó")
    order = np.argsort(s[:nn], kind="stable")
    return zn[:, order], s[order]


def lambda_ils(a: np.ndarray, q: np.ndarray, m: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Solución entera de mínimos cuadrados: los m mejores vectores (n×m) y sus distancias cuadradas."""
    a = np.asarray(a, dtype=float)
    z, lmat, d = reduction(np.asarray(q, dtype=float))
    zs = z.T @ a
    e, s = search(lmat, d, zs, m)
    f = np.linalg.solve(z.T, e)
    return np.round(f), s


def bootstrap_success(d: np.ndarray) -> float:
    """Tasa de éxito del bootstrapping con varianzas condicionales d (cota inferior de la del ILS)."""
    p = 1.0
    for v in d:
        p *= math.erf(1.0 / (2.0 * math.sqrt(2.0 * v)))
    return p


@dataclass
class PartialFix:
    ok: bool
    n_total: int
    n_fixed: int = 0
    success: float = 0.0                      # tasa de éxito del bootstrapping del subconjunto fijado
    ratio: float = 0.0                        # s2 / s1 del subconjunto
    z_cols: np.ndarray | None = None          # columnas de Z del subconjunto (combinaciones enteras de a), n×k
    z_float: np.ndarray | None = None         # ẑ del subconjunto
    z_fixed: np.ndarray | None = None         # ž
    sq_norm: float = 0.0                      # (ẑ−ž)ᵀ Qz⁻¹ (ẑ−ž)
    reason: str = ""
    full_success: float = 0.0                 # tasa de éxito con todas
    candidates: list[float] = field(default_factory=list)


def partial_fix(a: np.ndarray, q: np.ndarray, *, min_success: float = 0.999, min_ratio: float = 3.0,
                min_count: int = 4, max_sq_prob: float | None = 0.999, chi2_ppf=None) -> PartialFix:
    """Resolución parcial: se decorrelaciona, se toma el subconjunto más grande (desde las combinaciones más
    precisas) con tasa de éxito del bootstrapping ≥ `min_success` y se busca la solución entera; si no pasa la
    prueba de razón (≥ `min_ratio`) o la distancia entera no cuadra con su covarianza (χ² al `max_sq_prob`), se
    quita la combinación menos precisa y se repite, hasta `min_count`."""
    a = np.asarray(a, dtype=float)
    n = len(a)
    if n == 0:
        return PartialFix(False, 0, reason="no hay ambigüedades")
    z, lmat, d = reduction(np.asarray(q, dtype=float))
    zs = z.T @ a
    full = bootstrap_success(d)
    k = n
    p = 1.0
    while k > 0:
        nxt = p * math.erf(1.0 / (2.0 * math.sqrt(2.0 * d[k - 1])))
        if nxt < min_success:
            break
        p = nxt
        k -= 1
    if n - k < min_count:
        return PartialFix(False, n, n_fixed=0, success=p if n - k else 0.0, full_success=full,
                          reason=f"solo {n - k} combinaciones llegan a la tasa de éxito pedida "
                                 f"({100 * min_success:.1f} %)")
    last = None
    for start in range(k, n - min_count + 1):
        sub_l, sub_d, sub_z = lmat[start:, start:], d[start:], zs[start:]
        cand, s = search(sub_l, sub_d, sub_z, 2)
        ratio = float(s[1] / s[0]) if len(s) > 1 and s[0] > 0 else 999.0
        ratio = min(ratio, 999.0)
        count = n - start
        limit = chi2_ppf(max_sq_prob, count) if (max_sq_prob and chi2_ppf) else float("inf")
        fix = PartialFix(ratio >= min_ratio and float(s[0]) <= limit, n, n_fixed=count,
                         success=bootstrap_success(sub_d), ratio=ratio, z_cols=z[:, start:], z_float=sub_z,
                         z_fixed=cand[:, 0], sq_norm=float(s[0]), full_success=full,
                         candidates=[float(v) for v in s])
        if fix.ok:
            return fix
        if last is None:
            last = fix
            last.reason = (f"la prueba de razón no pasa ({ratio:.2f} < {min_ratio:g})" if ratio < min_ratio else
                           f"la distancia entera ({s[0]:.1f}) no cuadra con la precisión de las flotantes "
                           f"(límite {limit:.1f})")
    last.ok = False
    last.n_fixed = 0
    return last
