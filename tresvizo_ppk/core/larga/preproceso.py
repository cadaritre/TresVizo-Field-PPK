"""Preproceso: combinaciones por satélite, saltos de ciclo y arcos.

**Combinaciones** (fase L en ciclos, código P en metros, por receptor):
- libre de ionosfera: Φ_IF = α·λ1·L1 − β·λ2·L2 y P_IF = α·P1 − β·P2 (m);
- Melbourne-Wübbena: MW = (L1 − L2) − (f1·P1 + f2·P2) / ((f1 + f2)·λ_WL), en ciclos del ancho de carril
  (λ_WL = c/(f1 − f2), 86 cm en GPS): no depende de la geometría ni de la ionosfera; su ruido es el del código;
- libre de geometría: GF = λ1·L1 − λ2·L2 (m): la ionosfera más una constante, suave entre épocas.

**Saltos de ciclo** por receptor y satélite (como TurboEdit, Blewitt 1990, Manuscripta Geodaetica 15:199):
1. indicador de pérdida de enganche (LLI, bit 0) en cualquiera de las dos fases;
2. hueco de más de 3 intervalos;
3. Melbourne-Wübbena: la época se aleja de la media del arco más de max(2σ, 0.5 ciclos) y la mediana de ella y las
   tres siguientes lo confirma (se aleja más de max(4σ/√n, 0.6) y del mismo lado): así se ve un salto de un ciclo del ancho de carril
   aunque el ruido de una época sea de 0.25–0.3 ciclos; si las siguientes vuelven y la época sola se aleja más de
   max(4σ, 0.75), era un dato aislado y se descarta;
4. libre de geometría: se aleja más de 3.5 cm de la predicción cuadrática de las últimas épocas (un salto 1/1 la
   mueve 5.4 cm en GPS y 6.4 cm en Galileo E1/E5a).
MW ve los saltos que cambian N1 − N2 y GF los que lo dejan igual (un salto 1/1 mueve GF 5.4 cm en GPS).

**Arcos.** Un arco es un tramo sin salto de un satélite en un receptor; en la diferencia entre receptores, un arco
nuevo empieza cuando cualquiera de los dos empieza uno. Los arcos de menos de `min_arc_s` se descartan (no
aportan a la resolución de ambigüedades y sí parámetros).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .senales import Pair

MW_SIGMA_FLOOR = 0.25
MW_MIN_JUMP = 0.75
MW_WINDOW = 4
MW_CONFIRM = 0.6
GF_THRESHOLD_M = 0.035


@dataclass
class Combos:
    phase_if: np.ndarray            # m (T)
    code_if: np.ndarray             # m
    mw: np.ndarray                  # ciclos WL
    gf: np.ndarray                  # m
    code1: np.ndarray               # m (para el instante de emisión)
    lli: np.ndarray                 # bool: pérdida de enganche en alguna fase
    ok: np.ndarray                  # bool: las cuatro observaciones están


def combos(pair: Pair, c1: np.ndarray, l1: np.ndarray, c2: np.ndarray, l2: np.ndarray, lli1: np.ndarray,
           lli2: np.ndarray) -> Combos:
    ok = np.isfinite(c1) & np.isfinite(l1) & np.isfinite(c2) & np.isfinite(l2)
    phase_if = pair.alpha * pair.lam1 * l1 - pair.beta * pair.lam2 * l2
    code_if = pair.alpha * c1 - pair.beta * c2
    mw = (l1 - l2) - (pair.f1 * c1 + pair.f2 * c2) / ((pair.f1 + pair.f2) * pair.lam_wl)
    gf = pair.lam1 * l1 - pair.lam2 * l2
    lli = ((lli1 & 1) != 0) | ((lli2 & 1) != 0)
    return Combos(phase_if, code_if, mw, gf, c1, lli, ok)


def detect_arcs(times: np.ndarray, comb: Combos, use: np.ndarray, interval: float) -> tuple[np.ndarray, np.ndarray]:
    """Número de arco por época (−1 donde no hay dato o se descartó) y máscara de datos aislados descartados."""
    n = len(times)
    arc = np.full(n, -1, dtype=int)
    bad = np.zeros(n, dtype=bool)
    valid = use & comb.ok & np.isfinite(comb.mw) & np.isfinite(comb.gf)
    idx = np.flatnonzero(valid)
    current = -1
    mw_vals: list[float] = []
    gf_t: list[float] = []
    gf_v: list[float] = []
    last_t = None
    max_gap = 3.0 * interval + 1e-6
    k = 0
    while k < len(idx):
        i = idx[k]
        t = times[i]
        new = current < 0 or bool(comb.lli[i]) or last_t is None or (t - last_t) > max_gap
        if not new:
            mean = float(np.mean(mw_vals))
            sigma = max(float(np.std(mw_vals)) if len(mw_vals) >= 5 else 0.5, MW_SIGMA_FLOOR)
            # libre de geometría: predicción cuadrática de las últimas épocas
            jump_gf = False
            if len(gf_t) >= 2:
                tt = np.array(gf_t[-6:]) - t
                vv = np.array(gf_v[-6:])
                coef = np.polyfit(tt, vv, 2 if len(tt) >= 4 else 1)
                jump_gf = abs(comb.gf[i] - coef[-1]) > GF_THRESHOLD_M
            # Melbourne-Wübbena: candidato con 3σ; se confirma con la media de las épocas siguientes (sin hueco)
            jump_mw = False
            dev = abs(comb.mw[i] - mean)
            if dev > max(2.0 * sigma, MW_MIN_JUMP / 1.5):
                ahead = [i]
                for j in idx[k + 1:k + MW_WINDOW]:
                    if comb.lli[j] or times[j] - times[ahead[-1]] > max_gap:
                        break
                    ahead.append(j)
                d_ahead = comb.mw[ahead] - mean
                m_ahead = float(np.median(d_ahead))            # mediana: un dato aislado no la mueve
                same_side = int(np.sum(np.sign(d_ahead) == np.sign(m_ahead)))
                if abs(m_ahead) > max(4.0 * sigma / np.sqrt(len(ahead)), MW_CONFIRM) and \
                        same_side >= len(ahead) - 1 and (len(ahead) > 1 or dev > max(4.0 * sigma, MW_MIN_JUMP)):
                    jump_mw = True
                elif dev > max(4.0 * sigma, MW_MIN_JUMP) and not jump_gf:
                    bad[i] = True                       # dato aislado: se descarta
                    k += 1
                    continue
            new = jump_mw or jump_gf
        if new:
            current = current + 1 if current >= 0 else 0
            mw_vals, gf_t, gf_v = [], [], []
        arc[i] = current
        mw_vals.append(float(comb.mw[i]))
        gf_t.append(float(t))
        gf_v.append(float(comb.gf[i]))
        last_t = t
        k += 1
    return arc, bad


def sd_arcs(arc_r: np.ndarray, arc_b: np.ndarray, times: np.ndarray, min_arc_s: float, interval: float
            ) -> np.ndarray:
    """Arcos de la diferencia entre receptores (−1 fuera); los cortos quedan en −1."""
    n = len(arc_r)
    out = np.full(n, -1, dtype=int)
    current = -1
    prev = None
    for i in range(n):
        if arc_r[i] < 0 or arc_b[i] < 0:
            continue
        key = (arc_r[i], arc_b[i])
        if key != prev:
            current += 1
            prev = key
        out[i] = current
    for a in range(current + 1):
        mask = out == a
        if not mask.any():
            continue
        span = times[mask][-1] - times[mask][0] + interval
        if span < min_arc_s or mask.sum() < 10:
            out[mask] = -1
    return out


def autocorr_factor(series: list[np.ndarray]) -> float:
    """Factor de inflación de la varianza por correlación temporal: (1 + ρ)/(1 − ρ) con ρ la autocorrelación de
    retardo 1 (modelo AR(1)) promediada sobre las series."""
    num = den = 0.0
    for s in series:
        s = np.asarray(s, dtype=float)
        s = s[np.isfinite(s)]
        if len(s) < 5:
            continue
        d = s - s.mean()
        num += float(np.sum(d[1:] * d[:-1]))
        den += float(np.sum(d * d))
    if den <= 0:
        return 1.0
    rho = min(max(num / den, 0.0), 0.98)
    return (1.0 + rho) / (1.0 - rho)
