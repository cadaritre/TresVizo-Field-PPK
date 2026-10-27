"""LAMBDA contra casos publicados: la prueba unitaria de RTKLIB (`test/utest/t_lambda.c`, matrices de 6 y 10
ambigüedades con sus dos mejores soluciones y distancias) y el ejemplo de tres dimensiones de Teunissen (de Jonge y
Tiberius 1996, LGR-19), comprobado además por búsqueda exhaustiva."""
from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from tresvizo_ppk.core.larga import lambda_ils as lam

A1 = [1585184.171, -6716599.430, 3915742.905, 7627233.455, 9565990.879, 989457273.200]
Q1 = [[0.227134, 0.112202, 0.112202, 0.112202, 0.112202, 0.103473],
      [0.112202, 0.227134, 0.112202, 0.112202, 0.112202, 0.103473],
      [0.112202, 0.112202, 0.227134, 0.112202, 0.112202, 0.103473],
      [0.112202, 0.112202, 0.112202, 0.227134, 0.112202, 0.103473],
      [0.112202, 0.112202, 0.112202, 0.112202, 0.227134, 0.103473],
      [0.103473, 0.103473, 0.103473, 0.103473, 0.103473, 0.434339]]
F1 = [[1585184, 1585184], [-6716599, -6716600], [3915743, 3915743], [7627234, 7627233], [9565991, 9565991],
      [989457273, 989457273]]
S1 = [3.507984, 3.708456]

A2 = [-13324172.755747, -10668894.713608, -7157225.010770, -6149367.974367, -7454133.571066, -5969200.494550,
      8336734.058423, 6186974.084502, -17549093.883655, -13970158.922370]
_q2a, _q2b, _q2c, _q2d, _q2e, _q2f = 0.446320, 0.223160, 0.572775, 0.286388, 0.735063, 0.367531


def _q2() -> np.ndarray:
    q = np.zeros((10, 10))
    for i in range(10):
        for j in range(10):
            bi, bj = i // 5, j // 5
            same = (i % 5) == (j % 5)
            if bi == 0 and bj == 0:
                q[i, j] = _q2a if same else _q2b
            elif bi == 1 and bj == 1:
                q[i, j] = _q2e if same else _q2f
            else:
                q[i, j] = _q2c if same else _q2d
    return q


F2 = [[-13324188, -13324188], [-10668901, -10668908], [-7157236, -7157236], [-6149379, -6149379],
      [-7454143, -7454143], [-5969220, -5969220], [8336726, 8336717], [6186960, 6186960],
      [-17549108, -17549108], [-13970171, -13970171]]
S2 = [1506.435789, 1612.811795]


@pytest.mark.parametrize("a, q, f, s", [(A1, np.array(Q1), F1, S1), (A2, _q2(), F2, S2)])
def test_rtklib_cases(a, q, f, s):
    fixed, dist = lam.lambda_ils(np.array(a), q, 2)
    assert np.allclose(fixed, np.array(f), atol=1e-4)
    assert np.allclose(dist, s, atol=1e-4)


TEUNISSEN_A = np.array([5.45, 3.10, 2.97])
TEUNISSEN_Q = np.array([[6.290, 5.978, 0.544], [5.978, 6.292, 2.340], [0.544, 2.340, 6.288]])


def test_teunissen_three_dimensional_example():
    fixed, dist = lam.lambda_ils(TEUNISSEN_A, TEUNISSEN_Q, 2)
    assert list(fixed[:, 0]) == [5, 3, 4]
    qi = np.linalg.inv(TEUNISSEN_Q)
    best = sorted(((np.array(c) - TEUNISSEN_A) @ qi @ (np.array(c) - TEUNISSEN_A), c)
                  for c in itertools.product(range(-5, 15), repeat=3))
    assert tuple(fixed[:, 0].astype(int)) == best[0][1]
    assert tuple(fixed[:, 1].astype(int)) == best[1][1]
    assert dist[0] == pytest.approx(best[0][0], rel=1e-9)
    assert dist[1] == pytest.approx(best[1][0], rel=1e-9)


def test_reduction_is_unimodular_and_decorrelates():
    z, lmat, d = lam.reduction(TEUNISSEN_Q)
    assert abs(round(np.linalg.det(z))) == 1 and np.allclose(z, np.round(z))
    qz = z.T @ TEUNISSEN_Q @ z
    assert np.allclose(qz, lmat.T @ np.diag(d) @ lmat)
    corr = lambda m: np.abs(m / np.sqrt(np.outer(np.diag(m), np.diag(m))) - np.eye(len(m))).max()
    assert corr(qz) < corr(TEUNISSEN_Q)


def test_random_cases_against_exhaustive_search():
    rng = np.random.default_rng(7)
    for _ in range(20):
        g = rng.normal(size=(4, 4))
        q = g @ g.T * 0.05 + np.eye(4) * 0.01
        a = rng.normal(size=4) * 3
        fixed, dist = lam.lambda_ils(a, q, 2)
        qi = np.linalg.inv(q)
        centre = np.round(a).astype(int)
        cands = sorted(((np.array(c) - a) @ qi @ (np.array(c) - a), c)
                       for c in itertools.product(*[range(v - 4, v + 5) for v in centre]))
        assert tuple(fixed[:, 0].astype(int)) == cands[0][1]
        assert dist[0] == pytest.approx(cands[0][0], rel=1e-9)
        assert dist[1] == pytest.approx(cands[1][0], rel=1e-9)


def test_bootstrap_success_rate():
    # una sola ambigüedad con σ = 0.1 ciclos: P = 2Φ(5) − 1
    assert lam.bootstrap_success(np.array([0.01])) == pytest.approx(math.erf(5 / math.sqrt(2)), rel=1e-12)
    assert lam.bootstrap_success(np.array([0.25])) == pytest.approx(math.erf(1 / math.sqrt(2)), rel=1e-12)


def test_partial_fix_drops_weak_ambiguities():
    rng = np.random.default_rng(3)
    truth = rng.integers(-20, 20, size=8).astype(float)
    sig = np.array([0.03, 0.03, 0.04, 0.04, 0.05, 0.05, 0.6, 0.8])      # dos muy débiles
    q = np.diag(sig ** 2)
    a = truth + rng.normal(size=8) * sig * 0.3
    fix = lam.partial_fix(a, q, min_success=0.999, min_ratio=3.0)
    assert fix.ok and fix.n_fixed == 6 and fix.success >= 0.999
    # las combinaciones fijadas reproducen la verdad
    assert np.allclose(fix.z_cols.T @ truth, fix.z_fixed)


def test_partial_fix_refuses_when_nothing_is_reliable():
    q = np.diag([0.3, 0.3, 0.4, 0.5, 0.6]) ** 2
    fix = lam.partial_fix(np.array([0.5, 1.4, 2.5, 3.3, 4.6]), q)
    assert not fix.ok and fix.n_fixed == 0
