"""Ajuste de red (core/red.py) con redes sintéticas: a mano, contra métodos independientes y por Monte Carlo."""
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tresvizo_ppk.core import adjust, red
from tresvizo_ppk.core.geodesy import (covariance_enu_to_ecef, ecef_delta_to_enu, enu_rotation, enu_to_ecef_delta,
                                       ecef_to_geodetic, geodetic_to_ecef)
from tresvizo_ppk.errors import UserError
from tresvizo_ppk.model import (BaselineSolution, Ecef, Enu, Ephemeris, NetworkConstraint, NetworkOptions,
                                NetworkVector, ProjectTolerances, Solution, Station)

ORIGIN = np.array(geodetic_to_ecef(21.0, -101.3, 2000.0))
T0 = datetime(2026, 7, 15, tzinfo=timezone.utc)


def at(e, n, u=0.0) -> np.ndarray:
    """Un punto a (e, n, u) metros del origen."""
    return ORIGIN + np.array(enu_to_ecef_delta(ORIGIN, (e, n, u)))


def cov_enu(point, sigmas) -> tuple:
    c = covariance_enu_to_ecef(point, np.diag(np.square(sigmas)))
    return tuple(tuple(float(v) for v in row) for row in c)


def vector(ident, a, b, xa, xb, *, noise=(0.0, 0.0, 0.0), sigma=(0.005, 0.005, 0.010), session="", start=None,
           hours=2.0, role="auto"):
    d = (xb - xa) + np.array(enu_to_ecef_delta(xa, noise))
    return NetworkVector(id=ident, from_id=a, to_id=b, vector_ecef=Ecef(*(float(v) for v in d)),
                         covariance_ecef=cov_enu(xa, sigma), session=session, start=start,
                         end=start + timedelta(hours=hours) if start else None, role=role)


def fixed(ident, x, sigma=None, horizontal=True, vertical=True, use=True):
    return NetworkConstraint(ident, Ecef(*(float(v) for v in x)), sigma_enu=sigma, horizontal=horizontal,
                             vertical=vertical, use=use)


def xyz(p) -> np.ndarray:
    return np.array([p.position.x, p.position.y, p.position.z])


def point(adj, ident):
    return next(p for p in adj.points if p.id == ident)


def residual(adj, ident):
    return next(r for r in adj.residuals if r.vector_id == ident)


def residual_ecef(adj, v: NetworkVector) -> np.ndarray:
    """El residuo de una línea en ECEF (red.py lo da en ENU de su punto de inicio ajustado)."""
    r = residual(adj, v.id).residual_enu
    lat, lon, _ = ecef_to_geodetic(*xyz(point(adj, v.from_id)))
    return enu_rotation(lat, lon).T @ np.array([r.e, r.n, r.u])


# --- Distribuciones ---------------------------------------------------------------------------------------------------

def test_student_t_and_tau_against_tables():
    # tablas de t de Student: 97.5 % con 5 gl = 2.5706, con 10 gl = 2.2281; 99.5 % con 20 gl = 2.8453
    assert abs(red.t_ppf(0.975, 5) - 2.5706) < 1e-4
    assert abs(red.t_ppf(0.975, 10) - 2.2281) < 1e-4
    assert abs(red.t_ppf(0.995, 20) - 2.8453) < 1e-4
    assert abs(red.t_cdf(-2.2281, 10) - 0.025) < 1e-5
    # τ tiende a la normal con muchos gl (1.96 con α0 = 5 %) y nunca pasa de √gl
    assert abs(red.tau_critical(100000, 0.05) - 1.95997) < 1e-3
    for dof in (2, 3, 5, 30):
        assert red.tau_critical(dof, 0.001) < math.sqrt(dof)
    with pytest.raises(ValueError):
        red.tau_critical(1, 0.05)


def test_ellipse95_by_hand():
    # σE = 0.02, σN = 0.01 sin correlación: semieje mayor 0.02·√5.991 al este (90°)
    major, minor, az = red.ellipse95(np.diag([0.02 ** 2, 0.01 ** 2]))
    assert abs(major - 0.02 * math.sqrt(5.991465)) < 1e-6 and abs(minor - 0.01 * math.sqrt(5.991465)) < 1e-6
    assert abs(az - 90.0) < 1e-9
    # girada 30° desde el norte
    a = math.radians(30.0)
    rot = np.array([[math.sin(a), math.cos(a)], [math.cos(a), -math.sin(a)]])     # columnas: eje mayor (e, n), menor
    c = rot @ np.diag([0.03 ** 2, 0.01 ** 2]) @ rot.T
    major, minor, az = red.ellipse95(c)
    assert abs(az - 30.0) < 1e-6 and abs(major - 0.03 * math.sqrt(5.991465)) < 1e-6
    assert red.ellipse95(np.zeros((2, 2))) == (0.0, 0.0, 0.0)


# --- Triángulo resuelto a mano ---------------------------------------------------------------------------------------

def test_triangle_by_hand():
    """A fija, B y C nuevas; tres líneas de la misma σ isótropa y cierre w conocido. A mano: cada residuo
    (observado − ajustado) es w/3, B = A + AB − w/3, χ² = |w|²/(3σ²) con 3 gl, redundancia 1/3 por componente,
    σ²(B) = σ²(C) = σ²(B−C) = 2σ²/3 y w de Baarda w_i/(σ√3)."""
    sigma = 0.01
    xa, xb, xc = at(0, 0), at(8000, 1000, 30), at(3000, 7000, -20)
    w = np.array(enu_to_ecef_delta(xa, (0.012, -0.006, 0.021)))
    iso = tuple(tuple(v) for v in (np.eye(3) * sigma ** 2))
    ab = (xb - xa) + w                     # todo el cierre en una línea: los residuos lo reparten igual
    lines = [NetworkVector("AB", "A", "B", Ecef(*ab), iso, session="1"),
             NetworkVector("BC", "B", "C", Ecef(*(xc - xb)), iso, session="2"),
             NetworkVector("CA", "C", "A", Ecef(*(xa - xc)), iso, session="3")]
    result = red.adjust_network(lines, [fixed("A", xa)], NetworkOptions(test="w"))
    m = result.minimal
    assert m.degrees_of_freedom == 3 and m.observations == 9 and m.unknowns == 9 and m.constraints == 3
    for v in lines:
        assert np.allclose(residual_ecef(m, v), w / 3, atol=1e-9)
    assert np.allclose(xyz(point(m, "B")), xa + ab - w / 3, atol=1e-9)
    assert np.allclose(xyz(point(m, "C")), xa + ab - w / 3 + (xc - xb) - w / 3, atol=1e-9)
    assert abs(m.chi2 - float(w @ w) / (3 * sigma ** 2)) < 1e-6
    assert abs(m.sigma0 - math.sqrt(m.chi2 / 3)) < 1e-12
    for v in lines:
        r = residual(m, v.id)
        assert np.allclose([r.redundancy_enu.e, r.redundancy_enu.n, r.redundancy_enu.u], 1 / 3, atol=1e-9)
        w_enu = ecef_delta_to_enu(xyz(point(m, v.from_id)), w)
        assert np.allclose([r.standardized_enu.e, r.standardized_enu.n, r.standardized_enu.u],
                           np.array(w_enu) / (sigma * math.sqrt(3)), atol=1e-6)
    # σ de B: dos caminos desde A (σ² y 2σ²) → 2σ²/3; igual la relativa B–C
    assert abs(point(m, "B").sigma_enu.e - sigma * math.sqrt(2 / 3)) < 1e-9
    rel = next(e for e in m.relative if {e.from_id, e.to_id} == {"B", "C"})
    assert abs(rel.semi_major_95_m - sigma * math.sqrt(2 / 3) * math.sqrt(5.991465)) < 1e-6
    assert abs(rel.sigma_u95_m - 1.959964 * sigma * math.sqrt(2 / 3)) < 1e-6
    assert abs(rel.ppm_95 - rel.semi_major_95_m / rel.distance_m * 1e6) < 1e-9
    # el bucle: su cierre es w
    assert len(result.loops) == 1
    lp = result.loops[0]
    assert abs(lp.closure_3d_m - float(np.linalg.norm(w))) < 1e-9
    assert abs(lp.length_m - sum(float(np.linalg.norm(red._xyz(v.vector_ecef))) for v in lines)) < 1e-6
    assert abs(lp.chi2 - float(w @ w) / (3 * sigma ** 2)) < 1e-6        # Σ C = 3σ²I
    # con una sola fija, el restringido es el mismo ajuste
    c = result.constrained
    assert abs(c.chi2 - m.chi2) < 1e-9 and np.allclose(xyz(point(c, "B")), xyz(point(m, "B")), atol=1e-9)


# --- Contra métodos independientes -----------------------------------------------------------------------------------

def test_matches_single_point_adjustment():
    """Un punto con tres bases fijas duras = el ajuste de un punto de adjust.py (posición, χ², gl, σ)."""
    rng = np.random.default_rng(7)
    xp = at(500, 400, 10)
    bases = {"B1": at(0, 0), "B2": at(1200, -300, 5), "B3": at(-200, 900, -8)}
    sig = {"B1": (0.004, 0.005, 0.009), "B2": (0.006, 0.004, 0.012), "B3": (0.005, 0.005, 0.010)}
    lines, single = [], []
    for k, (name, xb) in enumerate(bases.items()):
        noise = rng.normal(0, 1, 3) * np.array(sig[name])
        v = vector(f"L{k}", name, "P", xb, xp, noise=tuple(noise), sigma=sig[name], session=f"s{k}")
        lines.append(v)
        single.append(adjust.LineObservation(name, xb + red._xyz(v.vector_ecef), np.array(v.covariance_ecef)))
    ref = adjust.adjust(single, "ITRF2008", 2010.0)
    result = red.adjust_network(lines, [fixed(n, x) for n, x in bases.items()], NetworkOptions(test="w"))
    c = result.constrained
    assert np.allclose(xyz(point(c, "P")), [ref.position.x, ref.position.y, ref.position.z], atol=1e-9)
    assert abs(c.chi2 - ref.chi2) < 1e-6 and c.degrees_of_freedom == ref.degrees_of_freedom == 6
    assert abs(c.sigma0 - ref.sigma0) < 1e-7 * ref.sigma0
    # la σ sin escalar: adjust.py escala por σ0² solo si pasa de 1
    factor = max(1.0, ref.sigma0 ** 2)
    assert abs(point(c, "P").sigma_enu.u - ref.sigma_enu.u / math.sqrt(factor)) < 1e-9
    # w por componente: adjust.py lo da en ENU del punto y red.py en ENU de la base (a 1 km: casi lo mismo)
    for v in lines:
        a = ref.standardized_residuals[v.from_id]
        b = residual(c, v.id).standardized_enu
        assert np.allclose([a.e, a.n, a.u], [b.e, b.n, b.u], atol=2e-3)


def _random_network(rng, outlier: dict | None = None):
    """Red de 3 fijas y 4 puntos nuevos con 14 líneas en 5 sesiones y ruido de su propia covarianza."""
    truth = {"F1": at(0, 0), "F2": at(9000, 500, 40), "F3": at(3000, 9500, -25), "P1": at(4000, 3000, 12),
             "P2": at(7000, 6000, -5), "P3": at(1000, 6000, 30), "P4": at(6000, 1500, 18)}
    plan = [("F1", "P1", "a"), ("F2", "P1", "a"), ("F2", "P4", "b"), ("P4", "P1", "b"), ("F3", "P3", "c"),
            ("P3", "P1", "c"), ("F3", "P2", "d"), ("P2", "P1", "d"), ("F2", "P2", "e"), ("F1", "P4", "e"),
            ("P1", "P2", "f"), ("F1", "P3", "f"), ("P3", "P2", "g"), ("P4", "P2", "g")]
    lines = []
    for k, (a, b, s) in enumerate(plan):
        sig = (0.003 + 0.001 * (k % 3), 0.003 + 0.0007 * (k % 4), 0.007 + 0.001 * (k % 5))
        cov = np.array(cov_enu(truth[a], sig))
        # correlación entre componentes, como la de RTKLIB
        corr = np.array([[1, 0.3, -0.2], [0.3, 1, 0.4], [-0.2, 0.4, 1]])
        sd = np.sqrt(np.diag(cov))
        cov = cov * 0.5 + 0.5 * (sd[:, None] * corr * sd[None, :])
        noise = np.linalg.cholesky(cov) @ rng.normal(0, 1, 3)
        if outlier and k in outlier:
            noise = noise + np.array(enu_to_ecef_delta(truth[a], outlier[k]))
        d = truth[b] - truth[a] + noise
        lines.append(NetworkVector(f"V{k}", a, b, Ecef(*d), tuple(tuple(r) for r in cov), session=s))
    return truth, lines


def test_condition_equations_give_same_residuals():
    """Método independiente: ajuste por ecuaciones de condición (un bucle por línea fuera de un árbol, y una condición
    por cada fija de más). v = −Σ Bᵀ (B Σ Bᵀ)⁻¹ (B l − c): mismos residuos, χ² y σ0 que el paramétrico."""
    rng = np.random.default_rng(11)
    truth, lines = _random_network(rng)
    fixes = {"F1": truth["F1"], "F2": truth["F2"], "F3": truth["F3"]}
    result = red.adjust_network(lines, [fixed(n, x) for n, x in fixes.items()], NetworkOptions(test="w"))
    c = result.constrained
    # condiciones: caminos entre puntos por un árbol de expansión, cada línea fuera del árbol cierra un bucle; las
    # fijas F2 y F3 dan un camino F1 → F2 y F1 → F3 que debe sumar su diferencia conocida
    n = len(lines)
    l = np.concatenate([red._xyz(v.vector_ecef) for v in lines])
    sigma = np.zeros((3 * n, 3 * n))
    for k, v in enumerate(lines):
        sigma[3 * k:3 * k + 3, 3 * k:3 * k + 3] = np.array(v.covariance_ecef)
    parent = {}
    tree, others = [], []
    for k, v in enumerate(lines):
        a, b = parent.setdefault(v.from_id, v.from_id), parent.setdefault(v.to_id, v.to_id)

        def root(p):
            while parent[p] != p:
                p = parent[p]
            return p
        ra, rb = root(v.from_id), root(v.to_id)
        if ra != rb:
            parent[ra] = rb
            tree.append(k)
        else:
            others.append(k)
    adjacency = {}
    for k in tree:
        v = lines[k]
        adjacency.setdefault(v.from_id, []).append((v.to_id, k, 1))
        adjacency.setdefault(v.to_id, []).append((v.from_id, k, -1))

    def path(a, b):
        stack = [(a, [])]
        seen = {a}
        while stack:
            node, used = stack.pop()
            if node == b:
                return used
            for nxt, k, s in adjacency[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, used + [(k, s)]))
        raise AssertionError

    rows, consts = [], []
    for k in others:
        v = lines[k]
        row = np.zeros((3, 3 * n))
        row[:, 3 * k:3 * k + 3] = np.eye(3)
        for j, s in path(v.to_id, v.from_id):
            row[:, 3 * j:3 * j + 3] += s * np.eye(3)
        rows.append(row)
        consts.append(np.zeros(3))
    for name in ("F2", "F3"):
        row = np.zeros((3, 3 * n))
        for j, s in path("F1", name):
            row[:, 3 * j:3 * j + 3] += s * np.eye(3)
        rows.append(row)
        consts.append(fixes[name] - fixes["F1"])
    b = np.vstack(rows)
    misclosure = b @ l - np.concatenate(consts)
    v_corr = -sigma @ b.T @ np.linalg.solve(b @ sigma @ b.T, misclosure)     # ajustado = l + v
    chi2 = float(v_corr @ np.linalg.solve(sigma, v_corr))
    assert c.degrees_of_freedom == b.shape[0] == 3 * n - 3 * 4
    assert abs(c.chi2 - chi2) < 1e-6 * max(1.0, chi2)
    for k, v in enumerate(lines):
        assert np.allclose(residual_ecef(c, v), -v_corr[3 * k:3 * k + 3], atol=1e-9)
    # y los números de redundancia suman los grados de libertad
    total = sum(r.redundancy_enu.e + r.redundancy_enu.n + r.redundancy_enu.u for r in c.residuals)
    assert abs(total - c.degrees_of_freedom) < 1e-6


def test_minimal_constraint_does_not_depend_on_the_datum():
    """Residuos, χ², σ0, pruebas y elipses relativas iguales con cualquier punto fijo o con restricciones internas;
    las coordenadas solo cambian por una traslación."""
    rng = np.random.default_rng(5)
    truth, lines = _random_network(rng)
    cons = [fixed(n, truth[n]) for n in ("F1", "F2", "F3")]
    runs = [red.adjust_network(lines, cons, NetworkOptions(minimal_datum="point", minimal_point=p)).minimal
            for p in ("F1", "F3")]
    runs.append(red.adjust_network(lines, cons, NetworkOptions(minimal_datum="inner")).minimal)
    base = runs[0]
    assert base.datum == "F1 fijo" and runs[1].datum == "F3 fijo" and runs[2].datum == "restricciones internas"
    shift = None
    for other in runs[1:]:
        assert abs(other.chi2 - base.chi2) < 1e-8 and other.degrees_of_freedom == base.degrees_of_freedom
        for r in base.residuals:
            o = residual(other, r.vector_id)
            assert abs(o.max_standardized - r.max_standardized) < 1e-6
            assert np.allclose([o.redundancy_enu.e, o.redundancy_enu.u], [r.redundancy_enu.e, r.redundancy_enu.u],
                               atol=1e-9)
        for e in base.relative:
            o = next(x for x in other.relative if (x.from_id, x.to_id) == (e.from_id, e.to_id))
            assert abs(o.semi_major_95_m - e.semi_major_95_m) < 1e-9 and abs(o.sigma_u95_m - e.sigma_u95_m) < 1e-9
        shift = xyz(point(other, "P1")) - xyz(point(base, "P1"))
        for p in base.points:
            assert np.allclose(xyz(point(other, p.id)) - xyz(p), shift, atol=1e-8)
    # el punto del datum no tiene σ; con restricciones internas, todos la tienen
    datum_sigma = point(base, "F1").sigma_enu
    assert max(datum_sigma.e, datum_sigma.n, datum_sigma.u) < 1e-9 and point(base, "F1").kind == "datum"
    assert point(runs[2], "F1").sigma_enu.e > 0


def test_monte_carlo_sigma0_ellipses_and_chi2_rate():
    """Con el ruido de su propia covarianza: E[σ0²] = 1, la elipse absoluta al 95 % contiene la verdad el 95 % de las
    veces, la cota al 95 % igual, y la prueba χ² rechaza el 5 %."""
    rng = np.random.default_rng(2026)
    trials = 400
    s02, inside, inside_u, rejected = [], 0, 0, 0
    for _ in range(trials):
        truth, lines = _random_network(rng)
        result = red.adjust_network(lines, [fixed(n, truth[n]) for n in ("F1", "F2", "F3")],
                                    NetworkOptions(scale_covariances="never"))
        c = result.constrained
        s02.append(c.sigma0 ** 2)
        rejected += not c.passed
        p = point(c, "P2")
        cov = np.array(p.covariance_ecef)
        lat, lon, _ = ecef_to_geodetic(*truth["P2"])
        rot = enu_rotation(lat, lon)
        err = rot @ (xyz(p) - truth["P2"])
        cov_enu_ = rot @ cov @ rot.T
        inside += float(err[:2] @ np.linalg.solve(cov_enu_[:2, :2], err[:2])) <= red.CHI2_2D_95
        inside_u += abs(err[2]) <= p.sigma_u95_m
    assert abs(np.mean(s02) - 1.0) < 0.06
    assert abs(inside / trials - 0.95) < 0.03 and abs(inside_u / trials - 0.95) < 0.03
    assert abs(rejected / trials - 0.05) < 0.03


# --- Pruebas de los residuos, escala, fijas ponderadas y parciales ---------------------------------------------------

def test_blunder_is_flagged_first_with_w_and_tau():
    rng = np.random.default_rng(3)
    truth, lines = _random_network(rng, outlier={10: (0.0, 0.0, 0.12)})    # 12 cm en la cota de V10 (P1–P2)
    cons = [fixed(n, truth[n]) for n in ("F1", "F2", "F3")]
    for test in ("w", "tau"):
        result = red.adjust_network(lines, cons, NetworkOptions(test=test, scale_covariances="never"))
        m = result.minimal
        assert not m.passed and m.residuals[0].vector_id == "V10" and "V10" in m.flagged
        assert m.test == test
        if test == "w":
            assert m.critical_value == 3.29
        else:
            alpha0 = 1 - 0.95 ** (1 / m.observations)
            assert abs(m.critical_value - red.tau_critical(m.degrees_of_freedom, alpha0)) < 1e-12
        assert m.residuals[0].max_standardized == max(r.max_standardized for r in m.residuals)
    # sin el error, pasa y nada queda marcada
    truth, lines = _random_network(np.random.default_rng(3))
    clean = red.adjust_network(lines, cons, NetworkOptions(scale_covariances="never")).minimal
    assert clean.passed and not clean.flagged


def test_covariance_scaling_like_tbc():
    """Covarianzas 3 veces optimistas en σ (×1/9): «auto» las multiplica por σ0² del mínimamente restringido y el
    restringido usa el mismo factor; «never» no; «value», el del usuario."""
    rng = np.random.default_rng(8)
    truth, lines = _random_network(rng)
    optimistic = [NetworkVector(v.id, v.from_id, v.to_id, v.vector_ecef,
                                tuple(tuple(x / 9.0 for x in row) for row in v.covariance_ecef), session=v.session)
                  for v in lines]
    cons = [fixed(n, truth[n]) for n in ("F1", "F2", "F3")]
    auto = red.adjust_network(optimistic, cons)
    m = auto.minimal
    assert m.scale_factor > 4 and abs(m.scale_factor - m.sigma0_unscaled ** 2) < 1e-12
    assert m.chi2_unscaled > m.chi2_upper and abs(m.sigma0 - 1.0) < 1e-9 and m.passed
    assert auto.constrained.scale_factor == m.scale_factor
    never = red.adjust_network(optimistic, cons, NetworkOptions(scale_covariances="never")).minimal
    assert never.scale_factor == 1.0 and not never.passed and abs(never.sigma0 - m.sigma0_unscaled) < 1e-12
    # las σ escaladas son √factor veces las de sin escalar
    assert abs(point(m, "P1").sigma_enu.u / point(never, "P1").sigma_enu.u - math.sqrt(m.scale_factor)) < 1e-9
    value = red.adjust_network(optimistic, cons, NetworkOptions(scale_covariances="value", scale_value=4.0)).minimal
    assert value.scale_factor == 4.0
    # «auto» con un ajuste que pasa no escala
    truth, lines = _random_network(np.random.default_rng(1))
    cons = [fixed(n, truth[n]) for n in ("F1", "F2", "F3")]
    passing = red.adjust_network(lines, cons).minimal
    assert passing.passed and passing.scale_factor == 1.0
    with pytest.raises(UserError):
        red.adjust_network(lines, cons, NetworkOptions(scale_covariances="value", scale_value=0.0))


def test_weighted_constraints_between_free_and_hard():
    rng = np.random.default_rng(4)
    truth, lines = _random_network(rng)
    hard = red.adjust_network(lines, [fixed(n, truth[n]) for n in ("F1", "F2", "F3")]).constrained
    tiny = red.adjust_network(lines, [fixed(n, truth[n], Enu(1e-6, 1e-6, 1e-6)) for n in ("F1", "F2", "F3")])
    loose = red.adjust_network(lines, [fixed(n, truth[n], Enu(1e3, 1e3, 1e3)) for n in ("F1", "F2", "F3")])
    t = tiny.constrained
    assert t.datum == "F1 ponderada, F2 ponderada, F3 ponderada fijas" and point(t, "F1").kind == "weighted"
    assert np.allclose(xyz(point(t, "P2")), xyz(point(hard, "P2")), atol=1e-5)
    assert abs(t.chi2 - hard.chi2) < 1e-3 * hard.chi2
    # σ enormes: las líneas mandan, como en el mínimamente restringido (salvo una traslación de nada)
    lo, mi = loose.constrained, loose.minimal
    assert abs(lo.chi2 - mi.chi2) < 1e-4
    rel = xyz(point(lo, "P2")) - xyz(point(lo, "P1"))
    assert np.allclose(rel, xyz(point(mi, "P2")) - xyz(point(mi, "P1")), atol=1e-6)
    # las seudo-observaciones tienen su residuo
    assert any(r.is_constraint and r.vector_id == "F2" for r in t.residuals)
    with pytest.raises(UserError):
        red.adjust_network(lines, [fixed("F1", truth["F1"], Enu(0.0, 0.01, 0.01))])


def test_horizontal_and_vertical_only_constraints():
    rng = np.random.default_rng(9)
    truth, lines = _random_network(rng)
    shifted = truth["F2"] + np.array(enu_to_ecef_delta(truth["F2"], (0.0, 0.0, 0.5)))   # cota mala, planta buena
    cons = [fixed("F1", truth["F1"], horizontal=True, vertical=False), fixed("F2", shifted, horizontal=True,
                                                                             vertical=False),
            fixed("F3", truth["F3"], horizontal=False, vertical=True)]
    c = red.adjust_network(lines, cons).constrained
    assert c.datum == "F1 (planta), F2 (planta), F3 (cota) fijas"
    assert c.constraints == 2 + 2 + 1 and c.passed               # la cota mala de F2 no se usa
    up = ecef_delta_to_enu(truth["F3"], xyz(point(c, "F3")) - truth["F3"])
    assert abs(up[2]) < 1e-8                                       # ruido numérico de 1e-9 según la plataforma
    # solo planta en toda la red: el restringido no se puede y se avisa
    only_h = red.adjust_network(lines, [fixed("F1", truth["F1"], vertical=False)])
    assert only_h.constrained is None
    assert any("les falta una fija en cota" in w for w in only_h.warnings)


# --- Control ----------------------------------------------------------------------------------------------------------

def test_control_check_finds_the_moved_station_whatever_the_datum():
    rng = np.random.default_rng(12)
    truth, lines = _random_network(rng)
    moved = truth["F3"] + np.array(enu_to_ecef_delta(truth["F3"], (0.02, -0.01, -0.30)))   # hundimiento
    cons = [fixed("F1", truth["F1"]), fixed("F2", truth["F2"]), fixed("F3", moved)]
    for datum in ("F1", "F3"):
        result = red.adjust_network(lines, cons, NetworkOptions(minimal_point=datum))
        assert result.inconsistent_control == ["F3"]
        text = next(w for w in result.warnings if w.startswith("La coordenada de F3 no concuerda con las de F1 y F2"))
        up = float(text.split(" U ")[1].split(" mm")[0])
        assert abs(up - 300) < 15             # las líneas ponen a F3 30 cm arriba de su coordenada (hundida)
        pair = next(p for p in result.control_pairs if {p.from_id, p.to_id} == {"F1", "F2"})
        assert pair.consistent
        assert not result.constrained.passed and result.minimal.passed
    # sin F3 como fija (sigue de comprobación): pasa y F3 sale con su corrimiento
    cons[2].use = False
    result = red.adjust_network(lines, cons)
    assert result.constrained.passed and result.inconsistent_control == ["F3"]
    f3 = point(result.constrained, "F3")
    assert f3.kind == "free" and abs(f3.shift_enu.u - 0.30) < 0.03
    # con dos fijas que no concuerdan no se sabe cuál
    two = red.adjust_network(lines, [fixed("F1", truth["F1"]), fixed("F3", moved)])
    assert sorted(two.inconsistent_control) == ["F1", "F3"]
    assert any("con dos no se puede saber cuál falla" in w for w in two.warnings)


# --- Líneas triviales y sesiones --------------------------------------------------------------------------------------

def test_trivial_line_is_the_longest_of_the_simultaneous_triangle():
    xa, xb, xc = at(0, 0), at(10000, 0), at(4000, 9000)     # AB 10 km, AC 9.8 km, BC 10.8 km
    lines = [vector("AB", "A", "B", xa, xb, start=T0), vector("AC", "A", "C", xa, xc, start=T0),
             vector("BC", "B", "C", xb, xc, start=T0 + timedelta(minutes=10)),
             vector("AB2", "A", "B", xa, xb, start=T0 + timedelta(hours=5))]
    result = red.adjust_network(lines, [fixed("A", xa)])
    trivial = {v.id for v in result.vectors if v.trivial}
    assert trivial == {"BC"}
    bc = next(v for v in result.vectors if v.id == "BC")
    assert "3 receptores" in bc.note and "2 líneas son independientes" in bc.note
    # sesiones deducidas del horario: las tres primeras juntas, AB2 aparte
    assert result.sessions == {"S1": ["AB", "AC", "BC"], "S2": ["AB2"]}
    # la trivial no entra al ajuste (3 líneas de 4) ni a los bucles; AB y AB2 dan un bucle de dos
    assert result.minimal.observations == 9
    assert [sorted(lp.vectors) for lp in result.loops] == [["AB", "AB2"]]
    assert sorted(result.unchecked) == ["AC"]
    assert any("Ningún bucle comprueba la línea AC" in w for w in result.warnings)
    # el usuario puede decidir: BC independiente → la trivial es la siguiente más larga del ciclo (AB)
    lines[2].role = "independent"
    result = red.adjust_network(lines, [fixed("A", xa)])
    assert {v.id for v in result.vectors if v.trivial} == {"AB"}
    # meterlas todas: entran y se avisa
    lines[2].role = "auto"
    result = red.adjust_network(lines, [fixed("A", xa)], NetworkOptions(include_trivial=True))
    assert result.minimal.observations == 12
    assert any("σ salen optimistas" in w for w in result.warnings)
    # sin horario ni sesión: cada una es su propia sesión, y se avisa
    plain = [vector(v.id, v.from_id, v.to_id, {"A": xa, "B": xb, "C": xc}[v.from_id],
                    {"A": xa, "B": xb, "C": xc}[v.to_id]) for v in lines[:3]]
    result = red.adjust_network(plain, [fixed("A", xa)])
    assert not any(v.trivial for v in result.vectors)
    assert any("no tiene sesión ni horario" in w for w in result.warnings)


def test_four_receivers_leave_three_independent_lines():
    pts = {"A": at(0, 0), "B": at(10000, 0), "C": at(10000, 10000), "D": at(0, 11000)}
    names = list(pts)
    lines = [vector(f"{a}{b}", a, b, pts[a], pts[b], session="S") for k, a in enumerate(names)
             for b in names[k + 1:]]
    result = red.adjust_network(lines, [fixed("A", pts["A"])])
    independent = [v.id for v in result.vectors if not v.trivial]
    assert len(independent) == 3 and sorted(independent) == ["AB", "BC", "CD"]   # las más cortas que unen a los 4


# --- Cierres de bucle -------------------------------------------------------------------------------------------------

def test_loop_closure_against_mm_plus_ppm_tolerance():
    xa, xb, xc = at(0, 0), at(20000, 0), at(10000, 15000)
    lines = [vector("AB", "A", "B", xa, xb, session="1"), vector("BC", "B", "C", xb, xc, session="2"),
             vector("CA", "C", "A", xc, xa, session="3", noise=(0.030, 0.040, -0.060))]
    length = sum(float(np.linalg.norm(red._xyz(v.vector_ecef))) for v in lines)
    tolerances = ProjectTolerances(max_loop_h_m=0.010, max_loop_h_ppm=1.0, max_loop_v_m=0.020, max_loop_v_ppm=2.0)
    lp = red.adjust_network(lines, [fixed("A", xa)], tolerances=tolerances).loops[0]
    assert lp.points == ["A", "B", "C"] and lp.directions == [1, 1, 1] and len(lp.sessions) == 3
    # el cierre es el error metido en CA, en ENU de A (a 18 km la rotación cambia muy poco)
    assert abs(lp.closure_h_m - 0.050) < 2e-4 and abs(lp.closure_v_m - 0.060) < 2e-4
    assert abs(lp.tolerance_h_m - (0.010 + 1e-6 * length)) < 1e-12
    assert abs(lp.tolerance_v_m - (0.020 + 2e-6 * length)) < 1e-12
    assert abs(lp.ppm_h - lp.closure_h_m / length * 1e6) < 1e-9
    # 0.050 ≤ 0.064 (tolerancia en planta con 54 km) y 0.060 ≤ 0.128 en cota: pasa
    assert lp.status.level == "pass"
    first = lp
    strict = ProjectTolerances(max_loop_h_m=0.005, max_loop_h_ppm=0.0, max_loop_v_m=0.020, max_loop_v_ppm=0.0)
    lp = red.adjust_network(lines, [fixed("A", xa)], tolerances=strict).loops[0]
    assert lp.status.level == "fail"                 # 0.050 > 2 × 0.005 en planta; 0.060 > 2 × 0.020 en cota
    assert "cierre en planta 0.050 > 0.005 m" in lp.status.reason
    border = ProjectTolerances(max_loop_h_m=round(lp.closure_h_m, 3), max_loop_h_ppm=0.0, max_loop_v_m=None)
    lp = red.adjust_network(lines, [fixed("A", xa)], tolerances=border).loops[0]
    assert lp.status.level == "pass" and lp.tolerance_v_m is None and len(lp.status.checks) == 1
    # el sentido de las líneas no cambia el cierre
    flipped = [lines[0], lines[1], NetworkVector("AC", "A", "C", Ecef(*(-red._xyz(lines[2].vector_ecef))),
                                                 lines[2].covariance_ecef, session="3")]
    lp2 = red.adjust_network(flipped, [fixed("A", xa)], tolerances=tolerances).loops[0]
    assert abs(lp2.closure_3d_m - first.closure_3d_m) < 1e-9 and -1 in lp2.directions
    assert abs(lp2.closure_h_m - 0.050) < 2e-4


def test_each_line_gets_its_shortest_loop():
    rng = np.random.default_rng(1)
    truth, lines = _random_network(rng)
    result = red.adjust_network(lines, [fixed(n, truth[n]) for n in ("F1", "F2", "F3")])
    independent = {v.id for v in result.vectors if not v.trivial}
    covered = {i for lp in result.loops for i in lp.vectors}
    assert covered | set(result.unchecked) == independent
    assert len({frozenset(lp.vectors) for lp in result.loops}) == len(result.loops)
    for lp in result.loops:
        total = sum(d * red._xyz(next(v for v in lines if v.id == i).vector_ecef)
                    for i, d in zip(lp.vectors, lp.directions))
        assert abs(float(np.linalg.norm(total)) - lp.closure_3d_m) < 1e-9
        assert lp.chi2 < 30                          # sin errores groseros


# --- Entradas y errores -----------------------------------------------------------------------------------------------

def test_input_errors_and_disconnected_parts():
    xa, xb, xc, xd = at(0, 0), at(5000, 0), at(0, 5000), at(5000, 5000)
    ab = vector("AB", "A", "B", xa, xb)
    cd = vector("CD", "C", "D", xc, xd)
    with pytest.raises(UserError, match="no se unen"):
        red.adjust_network([ab, cd], [fixed("A", xa)])
    two = red.adjust_network([ab, cd], [fixed("A", xa), fixed("C", xc)])
    assert two.minimal.datum == "A fijo; C fijo" and any("2 partes" in w for w in two.warnings)
    with pytest.raises(UserError, match="empieza y termina"):
        red.adjust_network([vector("AA", "A", "A", xa, xb)], [fixed("A", xa)])
    with pytest.raises(UserError, match="mismo nombre"):
        red.adjust_network([ab, vector("AB", "A", "C", xa, xc)], [fixed("A", xa)])
    bad = NetworkVector("X", "A", "B", ab.vector_ecef, ((1, 0, 0), (0, -1, 0), (0, 0, 1)))
    with pytest.raises(UserError, match="covarianza"):
        red.adjust_network([bad], [fixed("A", xa)])
    with pytest.raises(UserError, match="No hay líneas"):
        red.adjust_network([replace_use(vector("AB", "A", "B", xa, xb))], [fixed("A", xa)])
    # una fija sin líneas se avisa; sin fijas en uso solo hay mínimamente restringido
    result = red.adjust_network([ab], [fixed("A", xa, use=False), fixed("Z", xd)])
    assert result.constrained is None and any("La fija Z no tiene líneas" in w for w in result.warnings)
    assert result.minimal.degrees_of_freedom == 0
    assert any("0 grados de libertad" in w for w in result.warnings)


def replace_use(v):
    v.use = False
    return v


def test_vector_from_baseline():
    xb, xr = at(0, 0), at(3000, 4000, 10)
    station = Station("INEG", "INEG", Ecef(*xb))
    cov = cov_enu(xb, (0.003, 0.003, 0.008))
    b = BaselineSolution(base=station, rover_marker="P1", start=T0, end=T0 + timedelta(hours=1), epochs_used=120,
                         satellites=[], solution=Solution.FIXED, ratio=9.0, rms_m=0.01,
                         vector_ecef=Ecef(*(xr - xb)), vector_enu=Enu(3000, 4000, 10), length_m=5000.0,
                         azimuth_deg=36.87, zenith_deg=89.9, covariance_ecef=cov, rover_position=Ecef(*xr),
                         ephemeris=Ephemeris.BROADCAST, engine="RTKLIB")
    v = red.vector_from_baseline(b, session="S1")
    assert (v.id, v.from_id, v.to_id, v.session, v.solution, v.engine) == ("INEG-P1", "INEG", "P1", "S1",
                                                                           Solution.FIXED, "RTKLIB")
    assert v.covariance_ecef == cov and v.start == T0
