"""Estático de varios puntos (core/static_network.py) con un procesador de líneas base de prueba: el plan de líneas,
las coordenadas preliminares, las líneas entre ocupaciones, las triviales, las fijas y el reajuste sin procesar."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tresvizo_ppk import calidad
from tresvizo_ppk.core import static_network as sn
from tresvizo_ppk.core.geodesy import covariance_enu_to_ecef, ecef_delta_to_enu, enu_to_ecef_delta, geodetic_to_ecef
from tresvizo_ppk.errors import UserError
from tresvizo_ppk.model import (BaselineSolution, DataQuality, Ecef, Enu, FieldPoint, NetworkFixChoice,
                                NetworkOccupation, NetworkOptions, ObservationSet, ProcessingOptions,
                                ProjectTolerances, Solution, Station)

O = np.array(geodetic_to_ecef(21.0, -101.3, 2000.0))
T0 = datetime(2026, 7, 15, tzinfo=timezone.utc)


def at(e, n, u=0.0):
    return O + np.array(enu_to_ecef_delta(O, (e, n, u)))


TRUTH = {"INEG": at(-12000, 9000, 50), "ISLP": at(15000, 11000, -30), "UQRO": at(4000, -14000, 20),
         "P1": at(1000, 2000, 5), "P2": at(6000, 3000, -8), "P3": at(-3000, -4000, 12)}


class Fake:
    """Procesa la línea con la geometría verdadera, la σ dada y, si se pide, un error en una línea."""
    def __init__(self, errors=None, fail=()):
        self.calls = []
        self.errors = errors or {}
        self.fail = set(fail)
        self.rng = np.random.default_rng(3)

    def __call__(self, rover, station, base, options, workdir, **kw):
        label = kw.get("label", "")
        self.calls.append((label, station.id, rover.marker))
        if label in self.fail:
            raise UserError("pocos satélites comunes")
        start, end = max(rover.start, base.start), min(rover.end, base.end)
        cov = covariance_enu_to_ecef(TRUTH[station.id], np.diag([0.004 ** 2, 0.004 ** 2, 0.008 ** 2]))
        noise = np.linalg.cholesky(cov) @ self.rng.normal(0, 1, 3)
        d = TRUTH[rover.marker] - TRUTH[station.id] + noise
        if label in self.errors:
            d = d + np.array(enu_to_ecef_delta(TRUTH[station.id], self.errors[label]))
        # el vector no depende de la coordenada de la base (en la prueba); la posición del rover sí
        position = np.array([station.position.x, station.position.y, station.position.z]) + d
        return BaselineSolution(base=station, rover_marker=rover.marker, start=start, end=end, epochs_used=100,
                                satellites=["G01"], solution=Solution.FIXED, ratio=8.0, rms_m=0.005,
                                vector_ecef=Ecef(*d), vector_enu=Enu(0, 0, 0), length_m=float(np.linalg.norm(d)),
                                azimuth_deg=0, zenith_deg=90, covariance_ecef=tuple(map(tuple, cov)),
                                rover_position=Ecef(*position), ephemeris=options.ephemeris, pdop_max=1.5), None


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(sn, "process_baseline", f)
    monkeypatch.setattr(sn, "trim", lambda obs, start, end, *a, **k: ObservationSet(
        obs=list(obs.obs), marker=obs.marker, start=max(obs.start, start), end=min(obs.end, end),
        antenna_height_m=obs.antenna_height_m, approx_position=obs.approx_position))
    monkeypatch.setattr(sn.quality, "assess", lambda obs, cancel=None: DataQuality())
    monkeypatch.setattr(sn.intermedios, "discard", lambda *a, **k: 0)
    return f


def station(sid, sigma=None):
    return Station(id=sid, name=sid, position=Ecef(*TRUTH[sid]), sigma_enu=sigma, network="rgna-inegi")


def recording(marker, start, hours):
    return ObservationSet(obs=[], marker=marker, start=start, end=start + timedelta(hours=hours),
                          antenna_height_m=1.5)


def day_bases():
    return [(station(s), recording(s, T0, 24)) for s in ("INEG", "ISLP", "UQRO")]


def campaign():
    """Dos receptores: R1 ocupa P1 (0–2 h) y P3 (3–5 h); R2 ocupa P2 todo el tiempo (0–5 h)."""
    rovers = {"R1": recording("R1", T0, 5), "R2": recording("R2", T0, 5)}
    occupations = [NetworkOccupation("P1", "R1", T0, T0 + timedelta(hours=2), antenna_height_m=1.616),
                   NetworkOccupation("P3", "R1", T0 + timedelta(hours=3), T0 + timedelta(hours=5)),
                   NetworkOccupation("P2", "R2")]
    return rovers, occupations


def test_plan_lines_and_adjust(fake, tmp_path):
    rovers, occupations = campaign()
    result = sn.process_network(rovers, occupations, day_bases(), ProcessingOptions(), tmp_path)
    # 3 ocupaciones × 3 estaciones, y P2 (R2) simultánea con P1 y con P3 (R1)
    kinds = sorted((ln.kind, ln.from_id, ln.to_id) for ln in result.lines)
    assert len([k for k in kinds if k[0] == "base"]) == 9
    assert {(a, b) for k, a, b in kinds if k == "rover"} in ({("P1", "P2"), ("P3", "P2")},
                                                             {("P1", "P2"), ("P2", "P3")})
    # sesiones: las de P1 (0–2 h) y de P3 (3–5 h) con su línea a P2, y las de P2 a las estaciones (5 h): la de 5 h no
    # es simultánea de las de 2 h (comparte menos de la mitad de sus datos). Cada sesión es un árbol: sin triviales
    net = result.network
    assert len(net.sessions) == 3 and not any(v.trivial for v in net.vectors)
    assert net.minimal.degrees_of_freedom == 33 - 18 + 3
    # el resultado: cada punto nuevo, del restringido, a pocos mm de la verdad
    assert sorted(p.name for p in result.points) == ["P1", "P2", "P3"]
    for p in result.points:
        assert p.adjustment == "constrained"
        d = ecef_delta_to_enu(TRUTH[p.name], np.array([p.position.x, p.position.y, p.position.z]) - TRUTH[p.name])
        assert np.linalg.norm(d) < 0.03
    assert net.minimal.passed and net.constrained.passed
    p2 = next(p for p in result.points if p.name == "P2")
    assert p2.occupations == 1 and p2.lines >= 3
    # la altura de la ocupación (del CSV) llegó a su línea
    assert any("altura a la ARP 1.6160 m" in line for line in result.log)


def test_simultaneous_receivers_leave_trivial_lines(fake, tmp_path):
    """R1 en P1 y R2 en P2 a la vez (0–2 h) con las tres estaciones: 5 receptores, 7 líneas, 4 independientes."""
    rovers = {"R1": recording("R1", T0, 2), "R2": recording("R2", T0, 2)}
    occupations = [NetworkOccupation("P1", "R1"), NetworkOccupation("P2", "R2")]
    result = sn.process_network(rovers, occupations, day_bases(), ProcessingOptions(), tmp_path)
    net = result.network
    assert len(result.lines) == 7 and len(net.sessions) == 1
    trivial = [v for v in net.vectors if v.trivial]
    assert len(trivial) == 3 and all(v.note.startswith("Trivial: con 5 receptores") for v in trivial)
    # las que quedan unen a los 5 con las más cortas
    kept = [v for v in net.vectors if not v.trivial]
    assert max(v_len(v) for v in kept) <= min(v_len(v) for v in trivial)


def v_len(v):
    return float(np.linalg.norm([v.vector_ecef.x, v.vector_ecef.y, v.vector_ecef.z]))


def test_line_between_occupations_starts_at_the_preliminary_coordinate(fake, tmp_path):
    rovers, occupations = campaign()
    sn.process_network(rovers, occupations, day_bases(), ProcessingOptions(), tmp_path)
    rover_calls = [c for c in fake.calls if c[1] in ("P1", "P2", "P3")]
    assert rover_calls and all(c[2] in ("P1", "P2", "P3") for c in rover_calls)


def test_failed_line_is_left_out_with_its_reason(fake, tmp_path):
    fake.fail = {"ISLP–P1"}
    rovers, occupations = campaign()
    result = sn.process_network(rovers, occupations, day_bases(), ProcessingOptions(), tmp_path)
    assert result.failed_lines == ["ISLP–P1: pocos satélites comunes"]
    assert any("Línea sin solución" in w for w in result.warnings)
    assert len(result.lines) == 10


def test_readjust_changes_fixes_and_lines_without_processing(fake, tmp_path):
    fake.errors = {"INEG–P3": (0.0, 0.0, 0.25)}
    rovers, occupations = campaign()
    result = sn.process_network(rovers, occupations, day_bases(), ProcessingOptions(), tmp_path)
    calls = len(fake.calls)
    bad = next(ln.id for ln in result.lines if ln.from_id == "INEG" and ln.to_id == "P3")
    assert bad in result.network.minimal.flagged or not result.network.minimal.passed
    again = sn.readjust(result, line_choices={bad: "off"})
    assert len(fake.calls) == calls                     # sin volver a procesar
    assert again.network.minimal.passed and again.line_choices == {bad: "off"}
    off = next(v for v in again.network.vectors if v.id == bad)
    assert not off.use and off.note == "Fuera por decisión del usuario."
    assert result.line_choices == {}                    # el de entrada no cambia
    # INEG solo de comprobación, ISLP ponderada sin σ publicada (la de omisión, avisada)
    fix = [NetworkFixChoice("INEG", "check"), NetworkFixChoice("ISLP", "weighted"), NetworkFixChoice("UQRO")]
    third = sn.readjust(result, fix=fix, line_choices={bad: "off"})
    c = third.network.constrained
    assert c.datum == "ISLP ponderada, UQRO fijas"
    assert any("ISLP no publica la σ" in w for w in third.warnings)
    kinds = {p.id: p.kind for p in c.points}
    assert kinds["INEG"] == "free" and kinds["ISLP"] == "weighted" and kinds["UQRO"] == "fixed"
    # todas de comprobación: no hay restringido y la coordenada sale del mínimamente restringido, con aviso
    loose = sn.readjust(result, fix=[NetworkFixChoice(s, "check") for s in ("INEG", "ISLP", "UQRO")])
    assert loose.network.constrained is None and all(p.adjustment == "minimal" for p in loose.points)
    assert any("mínimamente restringido" in w for w in loose.warnings)


def test_quality_of_lines_loops_and_points(fake, tmp_path):
    rovers, occupations = campaign()
    result = sn.process_network(rovers, occupations, day_bases(), ProcessingOptions(), tmp_path)
    t = calidad.tolerances_for_order("5 centímetros", "static")
    report = calidad.evaluate_network(result, t)
    assert {q.base_id for q in report.baselines} == {ln.id for ln in result.lines}
    assert [p.name for p in report.points] == [p.name for p in result.points]
    # 2 h de línea con el mínimo de 1 h del estático: pasa; σ de mm: pasa
    assert all(p.status.level == "pass" for p in report.points), [p.status.reason for p in report.points]
    strict = calidad.tolerances_for_order("1 centímetro", "static")
    strict.min_duration_s = 3 * 3600.0
    report = calidad.evaluate_network(result, strict)
    reasons = {p.name: p.status.reason for p in report.points}
    assert "duración" in reasons["P1"] and "duración" in reasons["P3"] and "duración" not in reasons["P2"]
    assert report.level != "pass"


def test_occupations_from_the_field_csv():
    rows = [FieldPoint("P1", "VERT", T0, T0 + timedelta(minutes=40), 1.5, antenna_offset_m=0.116,
                       row={"Description": "Mojón"}),
            FieldPoint("p9", "", T0 + timedelta(hours=1), T0 + timedelta(hours=1, seconds=30), 1.8,
                       antenna_offset_m=0.116),
            FieldPoint("P2", "", T0 + timedelta(hours=2), T0 + timedelta(hours=3), float("nan"))]
    occupations, notes = sn.occupations_from_field(rows, "R1", ObservationSet(obs=[], case_offset_m=0.116))
    assert [o.name for o in occupations] == ["P1", "P2"]
    assert abs(occupations[0].antenna_height_m - 1.616) < 1e-12 and occupations[0].description == "Mojón"
    assert occupations[0].code == "VERT" and occupations[0].source == "CSV de TresVizo Field, fila 1"
    assert occupations[1].antenna_height_m is None
    assert any("p9" in n and "PPK" in n for n in notes) and any(n.startswith("P2:") for n in notes)


def test_input_errors(fake, tmp_path):
    rovers, occupations = campaign()
    with pytest.raises(UserError, match="No hay ocupaciones"):
        sn.process_network(rovers, [], day_bases(), ProcessingOptions(), tmp_path)
    with pytest.raises(UserError, match="al menos una estación"):
        sn.process_network(rovers, occupations, [], ProcessingOptions(), tmp_path)
    with pytest.raises(UserError, match="se llama como una estación"):
        sn.process_network(rovers, [NetworkOccupation("INEG", "R1")], day_bases(), ProcessingOptions(), tmp_path)
    with pytest.raises(UserError, match="no tiene grabación"):
        sn.process_network(rovers, [NetworkOccupation("P1", "R9")], day_bases(), ProcessingOptions(), tmp_path)
    late = [NetworkOccupation("P1", "R1", T0 + timedelta(hours=8), T0 + timedelta(hours=9))]
    with pytest.raises(UserError, match="no está en la grabación"):
        sn.process_network(rovers, late, day_bases(), ProcessingOptions(), tmp_path)


def test_tolerances_for_order_scale_the_loop_closure():
    five = calidad.tolerances_for_order("5 centímetros", "static")
    assert (five.max_loop_h_m, five.max_loop_h_ppm, five.max_loop_v_m, five.max_loop_v_ppm) == (0.05, 1.0, 0.1, 2.0)
    one = calidad.tolerances_for_order("1 centímetro", "static")
    assert abs(one.max_loop_h_m - 0.01) < 1e-12 and abs(one.max_loop_v_m - 0.02) < 1e-12
    assert calidad.matches_order(five, "static") and five == ProjectTolerances(min_duration_s=3600.0)
    bad = ProjectTolerances(max_loop_h_m=-1.0, max_loop_v_ppm=5000.0)
    issues = calidad.tolerance_issues(bad)
    assert any("cierre de bucle en planta" in i for i in issues) and any("ppm" in i for i in issues)
    assert calidad.tolerance_issues(ProjectTolerances(max_loop_h_m=None)) == []
    assert NetworkOptions().test == "tau"
