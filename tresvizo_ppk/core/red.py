"""Ajuste de red 3D de vectores GNSS, cierres de bucle y líneas triviales (estático de varios puntos).

Como Trimble Business Center (ajuste de red, «GNSS Loop Closure», líneas dependientes) y Leica Infinity («inner
constrained» y luego «fully constrained or weighted»).

**Modelo.** Cada línea base k es una observación del vector b_k = X_to − X_from (ECEF, en el marco de las fijas) con
su covarianza completa C_k (3×3). Incógnitas: las coordenadas de todos los puntos de la red; el problema es lineal
(no hace falta iterar) y se resuelve en correcciones a coordenadas aproximadas (las fijas propagadas por las líneas)
para no perder cifras. Restricciones:

- **duras**, como ecuaciones de condición con multiplicadores de Lagrange (matriz normal orlada). Una fija dura en
  planta, en cota o en las dos se escribe en ENU de su coordenada;
- **ponderadas**, como seudo-observaciones de la coordenada con su σ (en las componentes elegidas).

El vector no tiene orientación ni escala propias que fijar: el defecto de datum de una red de vectores es la
traslación (3 por cada parte conexa de la red). El ajuste **mínimamente restringido** lo resuelve con un punto fijo
(«point», como TBC) o con restricciones internas Σ dX = 0 («inner», como Infinity): los residuos, σ0, χ², las
pruebas de los residuos y las elipses **relativas** no dependen de cuál (prueba `test_red.py`). El **restringido**
usa las fijas en uso.

**Estadística** (con σ0 a priori 1): χ² = vᵀ P v con gl = observaciones − incógnitas + restricciones duras;
σ0 = √(χ²/gl); la prueba χ² es de una cola al 1 − α, como `adjust.py` (por debajo de χ²_α solo se avisa que el modelo
de error es pesimista). Por componente en ENU del punto de inicio de cada línea: residuo (observado − ajustado, el
mismo signo que `adjust.py`), su varianza Σ_vv = C − A Σ_xx Aᵀ, el estadístico w = v/σ_v (Baarda, crítico 3.29 con
α0 = 0.1 %, β = 20 %) o τ = v/(σ̂0 σ_v) (Pope, crítico de la distribución τ con α0 = 1 − (1 − α)^(1/n)), y el número de
redundancia diag(Σ_vv P) (suman los gl). Los residuos se dan ordenados de mayor a menor estadístico.

**Escala de las covarianzas** (`NetworkOptions.scale_covariances`, como TBC): «auto» multiplica las covarianzas de
las líneas por σ0² del mínimamente restringido si no pasa la χ²; «always» siempre; «value», por el factor del
usuario; «never», nunca. El mismo factor va al restringido (las fijas ponderadas no se escalan).

**Elipses al 95 %**: absolutas de cada punto y **relativas** entre pares de puntos (los unidos por una línea, o
todos): semiejes √(λ·χ²₂(0.95)) de la covarianza horizontal en ENU, cota 1.96σ, y el semieje mayor en ppm de la
distancia.

**Líneas triviales.** En una sesión simultánea con n receptores solo n − 1 líneas son independientes. Criterio
declarado: en cada sesión se quedan las del árbol de expansión mínima por longitud (Kruskal, primero las que el
usuario marcó independientes); cada una de las demás es la más larga de un ciclo de su sesión y queda trivial,
fuera del ajuste y de los bucles. Las sesiones se toman de `NetworkVector.session` o, si no viene, del horario: dos
líneas son de la misma sesión si comparten más de `session_overlap` de la más larga.

**Cierres de bucle** entre líneas independientes (como no hay ciclos dentro de una sesión, todo bucle junta
sesiones independientes): para cada línea, el bucle más corto (en metros) que la contiene, sin repetir bucles; las
líneas repetidas en otra sesión dan bucles de dos. Cierre = Σ ±vector en ENU del primer punto, en planta y en cota
contra la tolerancia del proyecto (mm + ppm de la longitud del bucle, `ProjectTolerances.max_loop_*`), con los
niveles de `calidad` (pasa, revisar, falla), y su χ² con las covarianzas a priori (3 gl). Las líneas que ningún
bucle comprueba se avisan.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal

import numpy as np

from ..errors import UserError
from ..model import (OFFICIAL_EPOCH, OFFICIAL_FRAME, BaselineSolution, ControlPairCheck, Ecef, Enu, LoopClosure,
                     NetworkAdjustment,
                     NetworkConstraint, NetworkOptions, NetworkPoint, NetworkResidual, NetworkResult, NetworkVector,
                     ProjectTolerances, QualityCheck, QualityStatus, RelativeEllipse)
from .adjust import chi2_ppf
from .geodesy import ecef_to_geodetic, enu_rotation

CHI2_2D_95 = chi2_ppf(0.95, 2)          # 5.991: elipse al 95 %
CONTROL_CRITICAL = 3.29                 # comprobación del control: |Δ|/σ por componente (como la w de Baarda)
Z_95 = 1.959963984540054                # cota al 95 %
_LEVELS = ("pass", "review", "fail")


# --- Distribuciones t y τ (sin scipy) --------------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Fracción continua de la beta incompleta (Numerical Recipes §6.4)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (tiny if abs(d) < tiny else d)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (tiny if abs(d) < tiny else d)
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (tiny if abs(d) < tiny else d)
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """I_x(a, b) regularizada."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, dof: float) -> float:
    tail = 0.5 * betainc(dof / 2.0, 0.5, dof / (dof + t * t))
    return 1.0 - tail if t >= 0 else tail


def t_ppf(p: float, dof: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError("p fuera de (0, 1)")
    if p < 0.5:
        return -t_ppf(1.0 - p, dof)
    lo, hi = 0.0, 10.0
    while t_cdf(hi, dof) < p:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, dof) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def tau_critical(dof: int, alpha0: float) -> float:
    """Valor crítico de τ (Pope, 1976) con gl ≥ 2 y riesgo α0 por observación (dos colas):
    τ = t·√gl / √(gl − 1 + t²), t de Student con gl − 1 al 1 − α0/2."""
    if dof < 2:
        raise ValueError("τ necesita al menos 2 grados de libertad")
    t = t_ppf(1.0 - alpha0 / 2.0, dof - 1)
    return t * math.sqrt(dof) / math.sqrt(dof - 1 + t * t)


# --- Utilidades -------------------------------------------------------------------------------------------------------

def _xyz(p: Ecef) -> np.ndarray:
    return np.array([p.x, p.y, p.z], dtype=float)


def _rot(xyz: np.ndarray) -> np.ndarray:
    lat, lon, _ = ecef_to_geodetic(*xyz)
    return enu_rotation(lat, lon)


def _enu(v) -> Enu:
    return Enu(float(v[0]), float(v[1]), float(v[2]))


def _matrix3(m: np.ndarray):
    return tuple(tuple(float(v) for v in row) for row in m)


def ellipse95(cov_en: np.ndarray) -> tuple[float, float, float]:
    """Semieje mayor y menor (m) y azimut del mayor (° desde el norte, 0–180) de la elipse al 95 % de una covarianza
    2×2 en (E, N)."""
    cov_en = (np.asarray(cov_en, dtype=float) + np.asarray(cov_en, dtype=float).T) / 2.0
    values, vectors = np.linalg.eigh(cov_en)
    big, small = max(values[1], 0.0), max(values[0], 0.0)
    if big <= 1e-16:
        return 0.0, 0.0, 0.0
    e, n = vectors[0, 1], vectors[1, 1]
    azimuth = math.degrees(math.atan2(e, n)) % 180.0
    return math.sqrt(big * CHI2_2D_95), math.sqrt(small * CHI2_2D_95), azimuth


def _printed(value: float) -> Decimal:
    return Decimal(repr(float(value))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _tolerance_check(criterion: str, label: str, value: float, limit: float | None, fail_factor: float):
    """Como `calidad.check` («max»): se compara la cifra impresa al milímetro; pasa, revisar o falla (más de
    `fail_factor` veces la tolerancia)."""
    if limit is None:
        return None
    v, lim = _printed(value), _printed(limit)
    factor = Decimal(repr(float(fail_factor)))
    level = "pass" if v <= lim else ("review" if v <= lim * factor else "fail")
    sign = "≤" if level == "pass" else ">"
    return QualityCheck(criterion, level, float(value), float(limit), f"{label} {v:f} {sign} {lim:f} m")


def _status(checks) -> QualityStatus:
    real = [c for c in checks if c is not None]
    worst = max((_LEVELS.index(c.level) for c in real), default=0)
    return QualityStatus(level=_LEVELS[worst], reason="; ".join(c.text for c in real if c.level != "pass"),
                         checks=real)


def _length(v: NetworkVector) -> float:
    return float(np.linalg.norm(_xyz(v.vector_ecef)))


def vector_from_baseline(b: BaselineSolution, *, from_id: str | None = None, to_id: str | None = None,
                         session: str = "", vector_id: str | None = None) -> NetworkVector:
    """La línea base procesada (base → rover) como línea de la red."""
    start = from_id or b.base.id or b.base.name
    end = to_id or b.rover_marker
    return NetworkVector(id=vector_id or f"{start}-{end}", from_id=start, to_id=end, vector_ecef=b.vector_ecef,
                         covariance_ecef=b.covariance_ecef, session=session, start=b.start, end=b.end,
                         solution=b.solution, engine=b.engine)


# --- Sesiones y líneas triviales -------------------------------------------------------------------------------------

def assign_sessions(vectors: list[NetworkVector], overlap: float = 0.5) -> tuple[dict[str, list[str]], list[str]]:
    """Sesión de cada línea en uso: la que trae o, sin ella, la deducida del horario: dos líneas son de la misma
    sesión si sus datos comunes pasan de `overlap` de la más larga (una línea de 5 h no es simultánea de una de 2 h
    dentro de ella: la mayor parte de sus datos no los comparte). Pone `session` en las líneas (copias del ajuste) y
    devuelve sesión → ids y avisos."""
    warnings: list[str] = []
    named = {v.session for v in vectors if v.session}
    pending = [v for v in vectors if not v.session and v.start and v.end]
    parent = list(range(len(pending)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(pending):
        for j in range(i + 1, len(pending)):
            b = pending[j]
            common = (min(a.end, b.end) - max(a.start, b.start)).total_seconds()
            longer = max((a.end - a.start).total_seconds(), (b.end - b.start).total_seconds())
            if longer > 0 and common >= overlap * longer:
                parent[find(i)] = find(j)
    groups: dict[int, list[NetworkVector]] = {}
    for i, v in enumerate(pending):
        groups.setdefault(find(i), []).append(v)
    k = 0
    for members in sorted(groups.values(), key=lambda m: (min(v.start for v in m), m[0].id)):
        k += 1
        label = f"S{k}"
        while label in named:
            k += 1
            label = f"S{k}"
        named.add(label)
        for v in members:
            v.session = label
    for v in vectors:
        if not v.session:
            v.session = f"línea {v.id}"
            warnings.append(f"La línea {v.id} no tiene sesión ni horario: no se puede saber con qué otras se midió a "
                            "la vez, y se toma como independiente.")
    sessions: dict[str, list[str]] = {}
    for v in sorted(vectors, key=lambda v: (v.start is None, v.start.timestamp() if v.start else 0.0, v.id)):
        sessions.setdefault(v.session, []).append(v.id)
    return sessions, warnings


def mark_trivial(vectors: list[NetworkVector], sessions: dict[str, list[str]]) -> list[str]:
    """Marca las triviales de cada sesión (ver el criterio arriba). Devuelve avisos."""
    warnings: list[str] = []
    by_id = {v.id: v for v in vectors}
    for session, ids in sessions.items():
        lines = [by_id[i] for i in ids if by_id[i].use]
        points = sorted({p for v in lines for p in (v.from_id, v.to_id)})
        parent = {p: p for p in points}

        def find(p: str) -> str:
            while parent[p] != p:
                parent[p] = parent[parent[p]]
                p = parent[p]
            return p

        order = sorted(lines, key=lambda v: ({"independent": 0, "auto": 1, "trivial": 2}.get(v.role, 1),
                                             _length(v), v.id))
        forced_cycle: list[str] = []
        for v in order:
            v.trivial, v.note = False, ""
            if v.role == "trivial":
                v.trivial = True
                v.note = "Marcada trivial por el usuario."
                continue
            a, b = find(v.from_id), find(v.to_id)
            if a != b:
                parent[a] = b
            elif v.role == "independent":
                forced_cycle.append(v.id)
            else:
                v.trivial = True
                v.note = (f"Trivial: con {len(points)} receptores a la vez en la sesión {session} solo "
                          f"{len(points) - 1} líneas son independientes; queda fuera la más larga de cada ciclo "
                          f"({_length(v) / 1000:.3f} km).")
        if forced_cycle:
            warnings.append(f"En la sesión {session}, las líneas marcadas independientes {', '.join(forced_cycle)} "
                            "cierran un ciclo con otras de la misma sesión: no son independientes y las σ del ajuste "
                            "salen optimistas.")
    return warnings


# --- Cierres de bucle -------------------------------------------------------------------------------------------------

def find_loops(vectors: list[NetworkVector], approx: dict[str, np.ndarray], tolerances: ProjectTolerances,
               alpha: float = 0.05) -> tuple[list[LoopClosure], list[str]]:
    """Bucles entre las líneas dadas (las independientes en uso) y las que ningún bucle comprueba."""
    adjacency: dict[str, list[tuple[str, NetworkVector, int]]] = {}
    for v in vectors:
        adjacency.setdefault(v.from_id, []).append((v.to_id, v, 1))
        adjacency.setdefault(v.to_id, []).append((v.from_id, v, -1))
    lengths = {v.id: _length(v) for v in vectors}
    seen: set[frozenset[str]] = set()
    loops: list[LoopClosure] = []
    checked: set[str] = set()
    limit = chi2_ppf(1.0 - alpha, 3)
    for e in vectors:
        # el camino más corto de vuelta, de e.to a e.from, sin usar e
        best = {e.to_id: 0.0}
        back: dict[str, tuple[str, NetworkVector, int]] = {}
        heap = [(0.0, e.to_id)]
        while heap:
            dist, node = heapq.heappop(heap)
            if node == e.from_id:
                break
            if dist > best.get(node, math.inf):
                continue
            for nxt, v, direction in adjacency.get(node, []):
                if v.id == e.id:
                    continue
                nd = dist + lengths[v.id]
                if nd < best.get(nxt, math.inf) - 1e-9:
                    best[nxt] = nd
                    back[nxt] = (node, v, direction)
                    heapq.heappush(heap, (nd, nxt))
        if e.from_id not in best:
            continue
        path: list[tuple[NetworkVector, int]] = []
        node = e.from_id
        while node != e.to_id:
            prev, v, direction = back[node]
            path.append((v, direction))
            node = prev
        path.reverse()
        members = [(e, 1)] + path
        key = frozenset(v.id for v, _ in members)
        if key in seen:
            continue
        seen.add(key)
        checked.update(key)
        loops.append(_closure(members, approx, tolerances, limit))
    loops.sort(key=lambda lp: (_LEVELS.index(lp.status.level) * -1, -lp.closure_3d_m))
    unchecked = [v.id for v in vectors if v.id not in checked]
    return loops, unchecked


def _closure(members: list[tuple[NetworkVector, int]], approx: dict[str, np.ndarray], t: ProjectTolerances,
             limit: float) -> LoopClosure:
    first = members[0][0]
    points = [first.from_id]
    total = np.zeros(3)
    cov = np.zeros((3, 3))
    for v, direction in members:
        total += direction * _xyz(v.vector_ecef)
        cov += np.array(v.covariance_ecef, dtype=float)
        points.append(v.to_id if direction > 0 else v.from_id)
    points = points[:-1]
    rot = _rot(approx[first.from_id])
    m = rot @ total
    length = sum(_length(v) for v, _ in members)
    h, u = math.hypot(m[0], m[1]), abs(m[2])
    d3 = float(np.linalg.norm(total))
    tol_h = None if t.max_loop_h_m is None else t.max_loop_h_m + t.max_loop_h_ppm * 1e-6 * length
    tol_v = None if t.max_loop_v_m is None else t.max_loop_v_m + t.max_loop_v_ppm * 1e-6 * length
    try:
        chi2 = float(total @ np.linalg.solve(cov, total))
    except np.linalg.LinAlgError:
        chi2 = math.inf
    status = _status([_tolerance_check("loop_h", "cierre en planta", h, tol_h, t.fail_factor),
                      _tolerance_check("loop_v", "cierre en cota", u, tol_v, t.fail_factor)])
    return LoopClosure(vectors=[v.id for v, _ in members], directions=[d for _, d in members], points=points,
                       sessions=list(dict.fromkeys(v.session for v, _ in members)), length_m=length,
                       misclosure_enu=_enu(m), closure_h_m=h, closure_v_m=u, closure_3d_m=d3,
                       ppm_h=h / length * 1e6 if length > 0 else 0.0, ppm_3d=d3 / length * 1e6 if length > 0 else 0.0,
                       tolerance_h_m=tol_h, tolerance_v_m=tol_v, chi2=chi2, chi2_limit=limit, status=status)


# --- Ajuste -----------------------------------------------------------------------------------------------------------

@dataclass
class _Pseudo:
    point: str
    rows: np.ndarray             # k×3 (filas ENU elegidas)
    value: np.ndarray            # k: rows · X_fija
    cov: np.ndarray              # k×k
    components: list[int]        # 0 E, 1 N, 2 U


@dataclass
class _Problem:
    points: list[str]
    vectors: list[NetworkVector]
    pseudo: list[_Pseudo] = field(default_factory=list)
    hard: list[tuple[dict[str, np.ndarray], np.ndarray]] = field(default_factory=list)   # {punto: k×3}, valor k
    kinds: dict[str, str] = field(default_factory=dict)
    frame: str = OFFICIAL_FRAME
    epoch: float = OFFICIAL_EPOCH


def _solve(problem: _Problem, approx: dict[str, np.ndarray], scale: float, options: NetworkOptions,
           kind: str, datum: str) -> tuple[NetworkAdjustment, np.ndarray]:
    """El ajuste y la covarianza completa de las coordenadas (3m × 3m, en el orden de `problem.points`)."""
    index = {p: k for k, p in enumerate(problem.points)}
    n_u = 3 * len(problem.points)
    normal = np.zeros((n_u, n_u))
    rhs = np.zeros(n_u)
    obs = []
    for v in problem.vectors:
        f, t = 3 * index[v.from_id], 3 * index[v.to_id]
        cov = scale * np.array(v.covariance_ecef, dtype=float)
        weight = np.linalg.inv(cov)
        reduced = _xyz(v.vector_ecef) - (approx[v.to_id] - approx[v.from_id])
        normal[f:f + 3, f:f + 3] += weight
        normal[t:t + 3, t:t + 3] += weight
        normal[f:f + 3, t:t + 3] -= weight
        normal[t:t + 3, f:f + 3] -= weight
        rhs[f:f + 3] -= weight @ reduced
        rhs[t:t + 3] += weight @ reduced
        obs.append((v, f, t, cov, weight, reduced))
    pseudo = []
    for ps in problem.pseudo:
        i = 3 * index[ps.point]
        weight = np.linalg.inv(ps.cov)
        reduced = ps.value - ps.rows @ approx[ps.point]
        normal[i:i + 3, i:i + 3] += ps.rows.T @ weight @ ps.rows
        rhs[i:i + 3] += ps.rows.T @ weight @ reduced
        pseudo.append((ps, i, weight, reduced))
    rows = []
    values = []
    for blocks, value in problem.hard:
        k = len(value)
        row = np.zeros((k, n_u))
        for point, block in blocks.items():
            i = 3 * index[point]
            row[:, i:i + 3] = block
        reduced = value - sum(block @ approx[point] for point, block in blocks.items())
        rows.append(row)
        values.append(reduced)
    h = np.vstack(rows) if rows else np.zeros((0, n_u))
    c = np.concatenate(values) if values else np.zeros(0)
    a = math.sqrt(max(float(np.mean(np.diag(normal))), 1e-30))     # equilibra la matriz orlada
    bordered = np.block([[normal, a * h.T], [a * h, np.zeros((len(c), len(c)))]])
    singular = np.linalg.svd(bordered, compute_uv=False)
    if singular[-1] <= singular[0] * 1e-13:
        raise UserError(f"El ajuste {'mínimamente restringido' if kind == 'minimal' else 'restringido'} no tiene "
                        "solución única: a alguna parte de la red le falta un punto fijo en planta o en cota.")
    dx = np.linalg.solve(bordered, np.concatenate([rhs, a * c]))[:n_u]
    inverse = np.linalg.inv(bordered)
    qxx = inverse[:n_u, :n_u]
    qxx = (qxx + qxx.T) / 2.0
    position = {p: approx[p] + dx[3 * k:3 * k + 3] for k, p in enumerate(problem.points)}
    # residuos (observado − ajustado) y su covarianza
    chi2 = 0.0
    raw = []
    n_comp = 0
    for v, f, t, cov, weight, reduced in obs:
        adjusted = dx[t:t + 3] - dx[f:f + 3]
        r = reduced - adjusted
        q_adj = qxx[t:t + 3, t:t + 3] + qxx[f:f + 3, f:f + 3] - qxx[t:t + 3, f:f + 3] - qxx[f:f + 3, t:t + 3]
        q_vv = cov - q_adj
        chi2 += float(r @ weight @ r)
        rot = _rot(position[v.from_id])
        raw.append((v.id, rot @ r, rot @ q_vv @ rot.T, np.diag(rot @ q_vv @ weight @ rot.T), [0, 1, 2], False))
        n_comp += 3
    for ps, i, weight, reduced in pseudo:
        r = reduced - ps.rows @ dx[i:i + 3]
        q_vv = ps.cov - ps.rows @ qxx[i:i + 3, i:i + 3] @ ps.rows.T
        chi2 += float(r @ weight @ r)
        raw.append((ps.point, r, q_vv, np.diag(q_vv @ weight), ps.components, True))
        n_comp += len(ps.components)
    dof = n_comp - n_u + len(c)
    sigma0 = math.sqrt(chi2 / dof) if dof > 0 else 0.0
    warnings: list[str] = []
    test = options.test if options.test in ("tau", "w") else "tau"
    if dof <= 0:
        lower = upper = 0.0
        passed = True
        critical = 0.0
        warnings.append("La red no tiene redundancia (0 grados de libertad): no hay prueba de las líneas.")
    else:
        lower, upper = chi2_ppf(options.alpha, dof), chi2_ppf(1.0 - options.alpha, dof)
        passed = chi2 <= upper
        if test == "tau" and dof < 2:
            test = "w"
            warnings.append("Con 1 grado de libertad no hay prueba τ: se usa la w de Baarda.")
        if test == "tau":
            alpha0 = 1.0 - (1.0 - options.alpha) ** (1.0 / max(n_comp, 1))
            critical = tau_critical(dof, alpha0)
        else:
            critical = options.w_critical
    residuals: list[NetworkResidual] = []
    for ident, r, q_vv, redundancy, components, is_constraint in raw:
        stats = [0.0, 0.0, 0.0]
        res = [0.0, 0.0, 0.0]
        red = [0.0, 0.0, 0.0]
        for k, comp in enumerate(components):
            res[comp] = float(r[k])
            red[comp] = float(max(redundancy[k], 0.0))
            var = float(q_vv[k, k])
            if dof > 0 and var > 1e-16:
                w = float(r[k]) / math.sqrt(var)
                stats[comp] = w / sigma0 if test == "tau" and sigma0 > 0 else w
        peak = max(abs(s) for s in stats)
        residuals.append(NetworkResidual(vector_id=ident, residual_enu=_enu(res), standardized_enu=_enu(stats),
                                         redundancy_enu=_enu(red), max_standardized=peak,
                                         flagged=dof > 0 and peak > critical, is_constraint=is_constraint))
    residuals.sort(key=lambda r: -r.max_standardized)
    points = []
    for k, p in enumerate(problem.points):
        cov = qxx[3 * k:3 * k + 3, 3 * k:3 * k + 3]
        rot = _rot(position[p])
        cov_enu = rot @ cov @ rot.T
        sigma = [math.sqrt(max(0.0, float(cov_enu[i, i]))) for i in range(3)]
        points.append(NetworkPoint(id=p, position=Ecef(*(float(x) for x in position[p]), problem.frame, problem.epoch),
                                   covariance_ecef=_matrix3(cov), sigma_enu=_enu(sigma),
                                   kind=problem.kinds.get(p, "free"), ellipse95=ellipse95(cov_enu[:2, :2]),
                                   sigma_u95_m=Z_95 * sigma[2]))
    pairs = _pairs(problem, options)
    relative = []
    for i, j in pairs:
        a_, b_ = 3 * index[i], 3 * index[j]
        q = qxx[a_:a_ + 3, a_:a_ + 3] + qxx[b_:b_ + 3, b_:b_ + 3] - qxx[a_:a_ + 3, b_:b_ + 3] - \
            qxx[b_:b_ + 3, a_:a_ + 3]
        rot = _rot((position[i] + position[j]) / 2.0)
        q_enu = rot @ q @ rot.T
        if float(np.trace(q_enu)) < 1e-14:
            continue                    # dos fijas duras: sin incertidumbre relativa
        distance = float(np.linalg.norm(position[j] - position[i]))
        major, minor, azimuth = ellipse95(q_enu[:2, :2])
        relative.append(RelativeEllipse(i, j, distance, major, minor, azimuth,
                                        Z_95 * math.sqrt(max(0.0, float(q_enu[2, 2]))),
                                        major / distance * 1e6 if distance > 0 else 0.0))
    return NetworkAdjustment(kind=kind, datum=datum, points=points, residuals=residuals, relative=relative,
                             observations=n_comp, unknowns=n_u, constraints=len(c), degrees_of_freedom=dof,
                             chi2=chi2, chi2_lower=lower, chi2_upper=upper, passed=passed, sigma0=sigma0, test=test,
                             critical_value=critical, scale_factor=scale, chi2_unscaled=chi2,
                             sigma0_unscaled=sigma0, flagged=[r.vector_id for r in residuals if r.flagged],
                             warnings=warnings), qxx


def _pairs(problem: _Problem, options: NetworkOptions) -> list[tuple[str, str]]:
    if options.relative_pairs == "all":
        return [(a, b) for k, a in enumerate(problem.points) for b in problem.points[k + 1:]]
    out: list[tuple[str, str]] = []
    seen: set[frozenset[str]] = set()
    for v in problem.vectors:
        key = frozenset((v.from_id, v.to_id))
        if key not in seen:
            seen.add(key)
            out.append((v.from_id, v.to_id))
    return out


def _components(points: list[str], vectors: list[NetworkVector]) -> list[list[str]]:
    parent = {p: p for p in points}

    def find(p: str) -> str:
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    for v in vectors:
        parent[find(v.from_id)] = find(v.to_id)
    groups: dict[str, list[str]] = {}
    for p in points:
        groups.setdefault(find(p), []).append(p)
    return list(groups.values())


def _approximate(component: list[str], root: str, root_xyz: np.ndarray,
                 vectors: list[NetworkVector]) -> dict[str, np.ndarray]:
    adjacency: dict[str, list[tuple[str, np.ndarray]]] = {}
    for v in vectors:
        d = _xyz(v.vector_ecef)
        adjacency.setdefault(v.from_id, []).append((v.to_id, d))
        adjacency.setdefault(v.to_id, []).append((v.from_id, -d))
    out = {root: root_xyz}
    queue = [root]
    while queue:
        node = queue.pop(0)
        for nxt, d in adjacency.get(node, []):
            if nxt not in out:
                out[nxt] = out[node] + d
                queue.append(nxt)
    return out


def _constraint_rows(c: NetworkConstraint) -> tuple[np.ndarray, list[int]]:
    rot = _rot(_xyz(c.position))
    comps = ([0, 1] if c.horizontal else []) + ([2] if c.vertical else [])
    return rot[comps], comps


def _constraint_cov_enu(c: NetworkConstraint) -> np.ndarray:
    if c.sigma_enu is None:
        return np.zeros((3, 3))
    s = c.sigma_enu
    return np.diag([s.e ** 2, s.n ** 2, s.u ** 2])


def adjust_network(vectors: list[NetworkVector], constraints: list[NetworkConstraint],
                   options: NetworkOptions | None = None,
                   tolerances: ProjectTolerances | None = None) -> NetworkResult:
    """Sesiones, líneas triviales, cierres de bucle, ajuste mínimamente restringido y restringido."""
    options = options or NetworkOptions()
    tolerances = tolerances or ProjectTolerances()
    vectors = [replace(v) for v in vectors]
    constraints = [replace(c) for c in constraints]
    log: list[str] = []
    warnings: list[str] = []
    _validate(vectors, constraints, options)
    frame = constraints[0].position.frame if constraints else (vectors[0].vector_ecef.frame if vectors
                                                               else OFFICIAL_FRAME)
    epoch = constraints[0].position.epoch if constraints else (vectors[0].vector_ecef.epoch if vectors
                                                               else OFFICIAL_EPOCH)
    if any(c.position.frame != frame or c.position.epoch != epoch for c in constraints):
        warnings.append("Las fijas tienen coordenadas en marcos o épocas distintos: el ajuste los mezcla.")
    in_use = [v for v in vectors if v.use]
    for v in vectors:
        if not v.use:
            v.trivial, v.note = False, "Fuera por decisión del usuario."
    sessions, notes = assign_sessions(in_use, options.session_overlap)
    warnings += notes
    warnings += mark_trivial(in_use, sessions)
    independent = [v for v in in_use if not v.trivial]
    active = in_use if options.include_trivial else independent
    trivial = [v.id for v in in_use if v.trivial]
    log.append(f"Red: {len(vectors)} líneas, {len(in_use)} en uso en {len(sessions)} sesiones; "
               f"{len(trivial)} triviales" + (f" ({', '.join(trivial)})" if trivial else "") +
               (", que entran al ajuste por elección del usuario" if options.include_trivial and trivial else
                ", fuera del ajuste y de los bucles") + ".")
    if options.include_trivial and trivial:
        warnings.append("Las líneas triviales entran al ajuste: no son independientes y las σ salen optimistas.")
    if not active:
        raise UserError("No hay líneas en uso para ajustar la red.")
    points = list(dict.fromkeys(p for v in active for p in (v.from_id, v.to_id)))
    known = {c.point_id: c for c in constraints}
    for c in constraints:
        if c.point_id not in points:
            warnings.append(f"La fija {c.point_id} no tiene líneas en uso: no entra a la red.")
    components = _components(points, active)
    approx: dict[str, np.ndarray] = {}
    roots: dict[int, str] = {}
    for k, comp in enumerate(components):
        root = _datum_point(comp, constraints, options)
        if root is None:
            raise UserError("Los puntos " + ", ".join(sorted(comp)) + " no se unen por ninguna línea con un punto de "
                            "coordenada conocida: agrega una línea que los una a la red o una fija.")
        roots[k] = root
        approx.update(_approximate(comp, root, _xyz(known[root].position), active))
    if len(components) > 1:
        warnings.append(f"La red tiene {len(components)} partes sin líneas que las unan: cada una lleva su propio "
                        "punto fijo.")
    loops, unchecked = find_loops(independent, approx, tolerances, options.alpha)
    for lp in loops:
        if lp.status.level != "pass":
            warnings.append(f"El bucle {' → '.join(lp.points)} ({lp.length_m / 1000:.3f} km) no cierra dentro de la "
                            f"tolerancia: {lp.status.reason}.")
    if unchecked:
        warnings.append("Ningún bucle comprueba " + ("la línea " if len(unchecked) == 1 else "las líneas ") +
                        ", ".join(unchecked) + ": un error en ella no se detecta (mídela otra vez en otra sesión o "
                        "cierra un bucle).")
    log.append(f"Cierres de bucle: {len(loops)}; pasan {sum(lp.status.level == 'pass' for lp in loops)}.")

    # Mínimamente restringido
    minimal_problem = _Problem(points=points, vectors=active, frame=frame, epoch=epoch)
    datum_names = []
    for k, comp in enumerate(components):
        if options.minimal_datum == "inner":
            minimal_problem.hard.append(({p: np.eye(3) for p in comp}, np.array(
                sum(approx[p] for p in comp), dtype=float)))
            datum_names.append("restricciones internas" + (f" ({', '.join(comp)})" if len(components) > 1 else ""))
        else:
            root = roots[k]
            minimal_problem.hard.append(({root: np.eye(3)}, approx[root].copy()))
            minimal_problem.kinds[root] = "datum"
            datum_names.append(f"{root} fijo")
    for p in points:
        if p in known and minimal_problem.kinds.get(p) != "datum":
            minimal_problem.kinds[p] = "control"
    datum = "; ".join(datum_names)
    first, qxx = _solve(minimal_problem, approx, 1.0, options, "minimal", datum)
    scale = _scale_factor(first, options)
    minimal = first
    if scale != 1.0:
        minimal, qxx = _solve(minimal_problem, approx, scale, options, "minimal", datum)
        minimal.chi2_unscaled, minimal.sigma0_unscaled = first.chi2, first.sigma0
        log.append(f"Covarianzas de las líneas multiplicadas por {scale:.3f} (σ0² del mínimamente restringido: "
                   f"{first.sigma0 ** 2:.3f}).")
    _shifts(minimal, known)
    log.append(_summary("Mínimamente restringido", minimal))
    warnings += [f"Mínimamente restringido: {w}" for w in minimal.warnings]
    if not first.passed:
        warnings.append(f"El ajuste mínimamente restringido no pasa la prueba χ² (χ² = {first.chi2:.1f} > "
                        f"{first.chi2_upper:.1f}, σ0 = {first.sigma0:.2f}): las líneas no concuerdan entre sí con "
                        "su precisión" + (f"; las covarianzas se escalaron por {scale:.2f}." if scale != 1.0 else
                                          "."))
    if first.degrees_of_freedom > 0 and first.chi2 < first.chi2_lower:
        log.append("  χ² por debajo del 5 %: el modelo de error a priori de las líneas es pesimista.")
    if minimal.flagged:
        warnings.append(f"Mínimamente restringido: {_flag_text(minimal)}")
    control_pairs, inconsistent, notes = _control_check(minimal, qxx, minimal_problem.points, known, components)
    warnings += notes
    if len([p for p in points if p in known]) >= 2:
        log.append(f"Comprobación del control: {len(control_pairs)} pares, "
                   f"{sum(not c.consistent for c in control_pairs)} no concuerdan" +
                   (f"; no concuerda{'n' if len(inconsistent) > 1 else ''} {', '.join(inconsistent)}."
                    if inconsistent else "."))

    # Restringido
    constrained = None
    used = [c for c in constraints if c.use and c.point_id in points and (c.horizontal or c.vertical)]
    if used:
        constrained = _constrained(points, active, components, used, approx, scale, options, warnings,
                                   frame, epoch)
        if constrained is not None:
            _shifts(constrained, known)
            log.append(_summary("Restringido", constrained))
            warnings += [f"Restringido: {w}" for w in constrained.warnings]
            if not constrained.passed and minimal.passed:
                warnings.append(f"El ajuste restringido no pasa la prueba χ² (χ² = {constrained.chi2:.1f} > "
                                f"{constrained.chi2_upper:.1f}) y el mínimamente restringido sí: las coordenadas de "
                                "las fijas no concuerdan con las líneas (revisa cuál con su corrimiento en el "
                                "mínimamente restringido).")
            if constrained.flagged:
                warnings.append(f"Restringido: {_flag_text(constrained)}")
    else:
        log.append("Sin fijas en uso: solo el ajuste mínimamente restringido.")
    return NetworkResult(vectors=vectors, constraints=constraints, options=options, sessions=sessions,
                         minimal=minimal, constrained=constrained, loops=loops, unchecked=unchecked,
                         control_pairs=control_pairs, inconsistent_control=inconsistent, frame=frame, epoch=epoch,
                         log=log, warnings=list(dict.fromkeys(warnings)))


def _validate(vectors: list[NetworkVector], constraints: list[NetworkConstraint], options: NetworkOptions) -> None:
    ids = [v.id for v in vectors]
    if len(set(ids)) != len(ids):
        raise UserError("Hay dos líneas con el mismo nombre en la red.")
    for v in vectors:
        if v.from_id == v.to_id:
            raise UserError(f"La línea {v.id} empieza y termina en {v.from_id}.")
        values = [v.vector_ecef.x, v.vector_ecef.y, v.vector_ecef.z]
        cov = np.array(v.covariance_ecef, dtype=float)
        if not all(math.isfinite(x) for x in values) or not np.all(np.isfinite(cov)):
            raise UserError(f"La línea {v.id} tiene un vector o una covarianza sin valor.")
        sym = (cov + cov.T) / 2.0
        if np.max(np.abs(cov - sym)) > 1e-9 * max(np.max(np.abs(cov)), 1e-30) + 1e-18 or \
                np.min(np.linalg.eigvalsh(sym)) <= 0:
            raise UserError(f"La covarianza de la línea {v.id} no es válida (no es simétrica definida positiva).")
        if v.role not in ("auto", "independent", "trivial"):
            raise UserError(f"Papel desconocido de la línea {v.id}: «{v.role}».")
    names = [c.point_id for c in constraints]
    if len(set(names)) != len(names):
        raise UserError("Hay un punto fijo repetido.")
    for c in constraints:
        if c.sigma_enu is not None:
            s = c.sigma_enu
            needed = ([s.e, s.n] if c.horizontal else []) + ([s.u] if c.vertical else [])
            if any(not (math.isfinite(x) and x > 0) for x in needed):
                raise UserError(f"La σ de la fija ponderada {c.point_id} debe ser mayor que cero.")
    if options.test not in ("tau", "w"):
        raise UserError(f"Prueba desconocida: «{options.test}».")
    if options.scale_covariances not in ("auto", "never", "always", "value"):
        raise UserError(f"Modo de escala desconocido: «{options.scale_covariances}».")
    if options.scale_covariances == "value" and not (math.isfinite(options.scale_value) and options.scale_value > 0):
        raise UserError("El factor de escala de las covarianzas debe ser mayor que cero.")
    if options.minimal_datum not in ("point", "inner"):
        raise UserError(f"Datum desconocido del mínimamente restringido: «{options.minimal_datum}».")
    if not 0.0 < options.alpha < 0.5:
        raise UserError("El nivel de significancia va de 0 a 0.5.")


def _datum_point(component: list[str], constraints: list[NetworkConstraint], options: NetworkOptions) -> str | None:
    if options.minimal_point and options.minimal_point in component and \
            any(c.point_id == options.minimal_point for c in constraints):
        return options.minimal_point
    for wanted_use in (True, False):
        for c in constraints:
            if c.point_id in component and c.use == wanted_use:
                return c.point_id
    return None


def _scale_factor(first: NetworkAdjustment, options: NetworkOptions) -> float:
    mode = options.scale_covariances
    if mode == "value":
        return float(options.scale_value)
    if first.degrees_of_freedom <= 0 or first.sigma0 <= 0:
        return 1.0
    if mode == "always" or (mode == "auto" and not first.passed):
        return first.sigma0 ** 2
    return 1.0


def _constrained(points, active, components, used, approx, scale, options, warnings, frame,
                 epoch) -> NetworkAdjustment | None:
    good_points: list[str] = []
    names = []
    for comp in components:
        here = [c for c in used if c.point_id in comp]
        has_h = any(c.horizontal for c in here)
        has_v = any(c.vertical for c in here)
        if not here:
            warnings.append("Los puntos " + ", ".join(comp) + " no tienen fija en uso: solo salen en el "
                            "mínimamente restringido.")
            continue
        if not (has_h and has_v):
            warnings.append("A los puntos " + ", ".join(comp) + " les falta una fija en " +
                            ("cota" if has_h else "planta") + ": solo salen en el mínimamente restringido.")
            continue
        good_points += comp
    if not good_points:
        return None
    vectors = [v for v in active if v.from_id in good_points]
    problem = _Problem(points=[p for p in points if p in good_points], vectors=vectors, frame=frame, epoch=epoch)
    for c in used:
        if c.point_id not in good_points:
            continue
        rows, comps = _constraint_rows(c)
        target = rows @ _xyz(c.position)
        if c.sigma_enu is None:
            problem.hard.append(({c.point_id: rows}, target))
            problem.kinds[c.point_id] = "fixed"
        else:
            cov = _constraint_cov_enu(c)[np.ix_(comps, comps)]
            problem.pseudo.append(_Pseudo(c.point_id, rows, target, cov, comps))
            problem.kinds[c.point_id] = "weighted"
        names.append(c.point_id + ("" if c.horizontal and c.vertical else
                                   " (planta)" if c.horizontal else " (cota)") +
                     ("" if c.sigma_enu is None else " ponderada"))
    datum = ", ".join(names) + (" fija" if len(names) == 1 else " fijas")
    return _solve(problem, approx, scale, options, "constrained", datum)[0]


def _shifts(adjustment: NetworkAdjustment, known: dict[str, NetworkConstraint]) -> None:
    for pt in adjustment.points:
        c = known.get(pt.id)
        if c is None:
            continue
        reference = _xyz(c.position)
        rot = _rot(reference)
        delta = rot @ (_xyz(pt.position) - reference)
        cov = rot @ np.array(pt.covariance_ecef) @ rot.T + _constraint_cov_enu(c)
        pt.shift_enu = _enu(delta)
        pt.shift_sigma_enu = _enu([math.sqrt(max(0.0, float(cov[i, i]))) for i in range(3)])


def _control_check(adjustment: NetworkAdjustment, qxx: np.ndarray, order: list[str],
                   known: dict[str, NetworkConstraint], components: list[list[str]]):
    """Comprobación del control en el mínimamente restringido: para cada par de puntos de control de una misma parte
    de la red, la diferencia de sus corrimientos (ajustada − coordenada) contra su σ (relativa del ajuste más la de
    las dos coordenadas). Si algún par no concuerda (|Δ|/σ > 3.29), se quita la fija que está en más pares que no
    concuerdan (y, a la par, la de mayor suma de estadísticos en ellos), hasta que las que quedan concuerdan; con dos
    que no concuerdan no se puede saber cuál es. No depende de qué punto fija el datum."""
    index = {p: k for k, p in enumerate(order)}
    points = {pt.id: pt for pt in adjustment.points}
    pairs: list[ControlPairCheck] = []
    inconsistent: list[str] = []
    notes: list[str] = []
    for comp in components:
        ids = [p for p in comp if p in known and p in points]
        stats: dict[frozenset[str], float] = {}
        deltas: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
        for k, a in enumerate(ids):
            for b in ids[k + 1:]:
                i, j = 3 * index[a], 3 * index[b]
                q = qxx[i:i + 3, i:i + 3] + qxx[j:j + 3, j:j + 3] - qxx[i:i + 3, j:j + 3] - qxx[j:j + 3, i:i + 3]
                xa, xb = _xyz(known[a].position), _xyz(known[b].position)
                rot = _rot((xa + xb) / 2.0)
                d = rot @ ((_xyz(points[a].position) - xa) - (_xyz(points[b].position) - xb))
                cov = rot @ q @ rot.T + _constraint_cov_enu(known[a]) + _constraint_cov_enu(known[b])
                sigma = np.sqrt(np.maximum(np.diag(cov), 0.0))
                peak = max((abs(d[c]) / sigma[c] for c in range(3) if sigma[c] > 0), default=0.0)
                stats[frozenset((a, b))] = peak
                deltas[(a, b)] = (d, sigma)
                pairs.append(ControlPairCheck(a, b, _enu(d), _enu(sigma), float(peak), peak <= CONTROL_CRITICAL))
        remaining = list(ids)
        removed: list[str] = []
        undecided: list[str] = []
        while True:
            bad = [key for key, peak in stats.items() if peak > CONTROL_CRITICAL and key <= set(remaining)]
            if not bad:
                break
            if len(remaining) == 2:
                a, b = remaining
                d, _ = deltas[(a, b)] if (a, b) in deltas else (-deltas[(b, a)][0], None)
                undecided.append(f"Las coordenadas de {a} y {b} no concuerdan entre sí con las líneas medidas (E "
                             f"{d[0] * 1000:+.0f}, N {d[1] * 1000:+.0f}, U {d[2] * 1000:+.0f} mm, "
                             f"{stats[frozenset((a, b))]:.0f}σ) y con dos no se puede saber cuál falla: agrega otra "
                             "fija o revisa las dos.")
                break
            count = {p: sum(1 for key in bad if p in key) for p in remaining}
            worst = max(remaining, key=lambda p: (count[p], sum(stats[key] for key in bad if p in key)))
            remaining.remove(worst)
            removed.append(worst)
        for p in removed:
            others = [q for q in remaining]
            mean = np.mean([deltas[(p, q)][0] if (p, q) in deltas else -deltas[(q, p)][0] for q in others], axis=0)
            peak = min(stats[frozenset((p, q))] for q in others)
            notes.append(f"La coordenada de {p} no concuerda con las de {_join(others)}: las líneas medidas desde "
                         f"{'ella' if len(others) == 1 else 'ellas'} ponen a {p} a E {mean[0] * 1000:+.0f}, N "
                         f"{mean[1] * 1000:+.0f}, U {mean[2] * 1000:+.0f} mm de su coordenada (al menos {peak:.0f}σ). "
                         "Revisa su coordenada (movimiento de la estación desde la época de la coordenada) o quítala "
                         "de las fijas.")
            inconsistent.append(p)
        if undecided:
            notes += undecided
            inconsistent += remaining
    return pairs, inconsistent, notes


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def _flag_text(adjustment: NetworkAdjustment) -> str:
    name = "τ" if adjustment.test == "tau" else "w"
    items = [f"{r.vector_id} ({name} {r.max_standardized:.1f})" for r in adjustment.residuals if r.flagged]
    return (f"no pasan la prueba {name} (crítico {adjustment.critical_value:.2f}): " + ", ".join(items) +
            ". Revísalas o déjalas fuera.")


def _summary(title: str, a: NetworkAdjustment) -> str:
    verdict = "pasa" if a.passed else "NO PASA"
    return (f"{title} ({a.datum}): {a.observations} observaciones, {a.unknowns} incógnitas, {a.constraints} "
            f"restricciones, {a.degrees_of_freedom} gl; χ² = {a.chi2:.2f} (límite {a.chi2_upper:.2f}), σ0 = "
            f"{a.sigma0:.3f}, {verdict}; {len(a.flagged)} marcadas.")
