"""Ajuste por lotes de las diferencias simples entre receptores con los relojes eliminados época por época.

Observaciones: diferencias simples rover − base (libre de ionosfera, código y fase) de cada satélite, en metros,
menos el modelo (`geometria`). Por cada época, grupo de señales (`senales.group_key`) y tipo (código o fase) hay un
reloj de receptor diferencial que se elimina de las normales (reducción de Schur por celda: P = W − w·wᵀ/Σw). Es
equivalente, número por número, a formar las dobles diferencias dentro del grupo con su correlación completa, sin
elegir satélite de referencia por época (Schaffrin y Grafarend 1986, Manuscripta Geodaetica 11:1).

Parámetros: corrección de la posición del rover (3, ECEF), retardo húmedo cenital de cada estación como función
lineal por tramos (nodos cada `trop_step_s`, con restricción a priori respecto del modelo y paseo aleatorio entre
nodos) y una ambigüedad por arco de la diferencia simple (en metros de libre de ionosfera) salvo el arco de
referencia de cada grupo y componente conexa, que fija el datum (sus diferencias son las dobles diferencias).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Rows:
    """Observaciones aplanadas (una fila por diferencia simple)."""
    cell: np.ndarray                # índice de celda (época, grupo, tipo)
    value: np.ndarray               # observado − calculado (m)
    var: np.ndarray                 # varianza (m²)
    los: np.ndarray                 # (n, 3) unitario rover → satélite: derivada de la distancia = −los
    trop_idx: np.ndarray            # (n, k) índices dentro del bloque de troposfera (cenital y gradientes)
    trop_coef: np.ndarray           # (n, k) coeficientes (función de mapeo × peso de interpolación, signo)
    amb: np.ndarray                 # índice de ambigüedad (−1: ninguna)
    phase: np.ndarray               # bool
    sat: np.ndarray                 # índice de satélite (para diagnóstico)
    epoch: np.ndarray


@dataclass
class Layout:
    n_trop: int                     # parámetros de troposfera (retardo cenital y gradientes de las dos estaciones)
    n_amb: int

    @property
    def trop0(self) -> int:
        return 3

    @property
    def amb0(self) -> int:
        return 3 + self.n_trop

    @property
    def n(self) -> int:
        return self.amb0 + self.n_amb


@dataclass
class Solution:
    x: np.ndarray                   # parámetros
    n_inv: np.ndarray               # inversa de las normales reducidas (sin escalar)
    vtpv: float
    dof: int
    residuals: np.ndarray           # por fila (NaN en las que no aportan)
    used: np.ndarray                # bool por fila
    n_obs: int

    @property
    def sigma0(self) -> float:
        return float(np.sqrt(self.vtpv / self.dof)) if self.dof > 0 else 1.0


def _design(rows: Rows, layout: Layout) -> tuple[np.ndarray, np.ndarray]:
    """Índices y valores de los no ceros de cada fila: (n, k) y (n, k)."""
    n = len(rows.value)
    cols = [np.broadcast_to(np.arange(3), (n, 3)), layout.trop0 + rows.trop_idx]
    vals = [-rows.los, rows.trop_coef]
    amb_col = np.where(rows.amb >= 0, layout.amb0 + rows.amb, 0)
    amb_val = np.where(rows.amb >= 0, 1.0, 0.0)
    cols.append(amb_col[:, None])
    vals.append(amb_val[:, None])
    return np.concatenate(cols, axis=1).astype(int), np.concatenate(vals, axis=1)


def normals(rows: Rows, layout: Layout, mask: np.ndarray, constraints: list[tuple[np.ndarray, float, float]]
            ) -> tuple[np.ndarray, np.ndarray, float, int, int]:
    """Normales reducidas (N, u), lᵀPl reducida, observaciones efectivas y celdas efectivas."""
    idx, val = _design(rows, layout)
    w = np.where(mask, 1.0 / rows.var, 0.0)
    l = rows.value
    n_par = layout.n
    big_n = np.zeros((n_par, n_par))
    u = np.zeros(n_par)
    k = idx.shape[1]
    # AᵀWA y AᵀWl
    wv = val * w[:, None]
    for a in range(k):
        np.add.at(u, idx[:, a], wv[:, a] * l)
        for b in range(k):
            np.add.at(big_n, (idx[:, a], idx[:, b]), wv[:, a] * val[:, b])
    ltpl = float(np.sum(w * l * l))
    # eliminación de los relojes por celda
    cells, inverse = np.unique(rows.cell[mask], return_inverse=True)
    sel = np.flatnonzero(mask)
    wsum = np.bincount(inverse, weights=w[sel], minlength=len(cells))
    wl = np.bincount(inverse, weights=w[sel] * l[sel], minlength=len(cells))
    counts = np.bincount(inverse, minlength=len(cells))
    chunk = 4000
    for c0 in range(0, len(cells), chunk):
        c1 = min(len(cells), c0 + chunk)
        rows_in = (inverse >= c0) & (inverse < c1)
        r = sel[rows_in]
        ci = inverse[rows_in] - c0
        s = np.zeros((c1 - c0, n_par))
        for a in range(k):
            np.add.at(s, (ci, idx[r, a]), w[r] * val[r, a])
        inv_w = 1.0 / wsum[c0:c1]
        big_n -= (s * inv_w[:, None]).T @ s
        u -= s.T @ (wl[c0:c1] * inv_w)
        ltpl -= float(np.sum(wl[c0:c1] ** 2 * inv_w))
    for vec, sigma, target in constraints:
        wt = 1.0 / sigma ** 2
        big_n += wt * np.outer(vec, vec)
        u += wt * vec * target
        ltpl += wt * target * target
    n_eff = int(np.sum(counts[counts > 1]))
    cells_eff = int(np.sum(counts > 1))
    return big_n, u, ltpl, n_eff, cells_eff


def solve(rows: Rows, layout: Layout, mask: np.ndarray, constraints) -> Solution:
    big_n, u, ltpl, n_eff, cells_eff = normals(rows, layout, mask, constraints)
    # parámetros sin observaciones (ambigüedades de arcos descartados): se anclan para no singularizar
    diag = np.diag(big_n).copy()
    empty = diag <= 1e-12
    big_n[empty, empty] = 1.0
    n_inv = np.linalg.inv(big_n)
    x = n_inv @ u
    vtpv = float(ltpl - x @ u)
    dof = n_eff - cells_eff - int(np.sum(~empty)) + len(constraints)
    res = residuals(rows, layout, mask, x)
    return Solution(x=x, n_inv=n_inv, vtpv=max(vtpv, 0.0), dof=dof, residuals=res, used=mask.copy(), n_obs=n_eff)


def residuals(rows: Rows, layout: Layout, mask: np.ndarray, x: np.ndarray) -> np.ndarray:
    idx, val = _design(rows, layout)
    pred = np.sum(val * x[idx], axis=1)
    e = rows.value - pred
    w = np.where(mask, 1.0 / rows.var, 0.0)
    cells, inverse = np.unique(rows.cell, return_inverse=True)
    wsum = np.bincount(inverse, weights=w, minlength=len(cells))
    we = np.bincount(inverse, weights=w * e, minlength=len(cells))
    clock = np.divide(we, wsum, out=np.zeros_like(we), where=wsum > 0)
    v = e - clock[inverse]
    v[~mask] = np.nan
    counts = np.bincount(inverse, weights=mask.astype(float), minlength=len(cells))
    v[counts[inverse] < 2] = np.nan
    return v


def node_constraints(layout: Layout, first: int, count: int, sigma_abs: float, sigma_rw: float
                     ) -> list[tuple[np.ndarray, float, float]]:
    """Restricciones de una serie de nodos del bloque de troposfera: cada nodo a 0 ± sigma_abs (respecto del a
    priori) y paseo aleatorio entre nodos consecutivos (± sigma_rw)."""
    out = []
    base = layout.trop0 + first
    for i in range(count):
        vec = np.zeros(layout.n)
        vec[base + i] = 1.0
        out.append((vec, sigma_abs, 0.0))
    for i in range(count - 1):
        vec = np.zeros(layout.n)
        vec[base + i] = -1.0
        vec[base + i + 1] = 1.0
        out.append((vec, sigma_rw, 0.0))
    return out
