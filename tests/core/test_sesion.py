"""Editor de sesión en el motor: la ventana de tiempo y los satélites o sistemas fuera llegan a RTKLIB (-ts/-te,
pos1-exclsats, pos1-navsys) y al procesador propio (inicio, fin y sistemas), se guardan en el proyecto y salen en el
informe. Con RTKLIB de verdad sobre ITLA–ICEP (30.7 km, 20 min a 15 s, 20-10-2026)."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tresvizo_ppk import project as proj
from tresvizo_ppk.core import api, baseline, quality, rtklib, sesion
from tresvizo_ppk.core.geodesy import gps_to_utc
from tresvizo_ppk.errors import UserError
from tresvizo_ppk.model import FieldPoint, ObservationSet, ProcessingOptions, Project, TimeWindow

from motor_util import DATA, needs_tools

ROVER = [DATA / "ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz", DATA / "ITLA00MEX_R_20262931400_01H_MN.rnx.gz"]
BASE = [DATA / "ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz"]
UTC = timezone.utc
# RTKLIB (no el procesador propio): la libre de ionosfera de RTKLIB, para comparar soluciones sin el propio
RTK = ProcessingOptions(ionosphere="iono-free")


# --- Normalizar, validar y recortar ---------------------------------------------------------------------------------

def test_normalize_excluded():
    assert sesion.normalize_excluded(["g5", " G13 ", "r", "R05", "e11", "G05"]) == ["R", "G05", "G13", "E11"]
    assert sesion.normalize_excluded("G13, E24;C19  J") == ["J", "G13", "E24", "C19"]
    assert sesion.normalize_excluded([]) == []
    with pytest.raises(UserError, match="«X9»"):
        sesion.normalize_excluded(["G13", "X9"])
    with pytest.raises(UserError, match="«G0»"):
        sesion.normalize_excluded(["G0"])
    assert sesion.excluded_text(["R", "G13", "E24"]) == "GLONASS completo; G13, E24"
    assert sesion.excluded_text(["R", "C"]) == "GLONASS, BeiDou completos"
    assert sesion.excluded_text([]) == "ninguno"


def test_constellations_and_exclsats():
    o = ProcessingOptions(excluded_sats=["R", "G13", "E24"])
    assert sesion.constellations(o) == ("GPS", "Galileo", "BeiDou", "QZSS")
    assert sesion.rtklib_exclsats(o) == "G13 E24"
    assert rtklib.navsys_mask(sesion.constellations(o)) == 1 | 8 | 16 | 32
    with pytest.raises(UserError, match="todos los sistemas"):
        sesion.constellations(ProcessingOptions(constellations=("GPS",), excluded_sats=["G"]))
    cfg = rtklib.base_config(mode="static", options=o, frequencies=2, ionosphere="dual-freq", troposphere="saas",
                             navsys=1, ambiguity="off", glonass_ar="off", precise=False, antex=None,
                             rover=rtklib.AntennaSetup(), base=rtklib.AntennaSetup(), base_xyz=(0.0, 0.0, 0.0),
                             interval_s=15.0, elevation_mask=15.0, min_ratio=3.0, combined=True)
    assert cfg.values["pos1-exclsats"] == "G13 E24"
    assert cfg.values["pos1-exclsats"] == "G13 E24"
    plain = rtklib.base_config(mode="static", options=ProcessingOptions(), frequencies=2, ionosphere="dual-freq",
                               troposphere="saas", navsys=1, ambiguity="off", glonass_ar="off", precise=False,
                               antex=None, rover=rtklib.AntennaSetup(), base=rtklib.AntennaSetup(),
                               base_xyz=(0.0, 0.0, 0.0), interval_s=15.0, elevation_mask=15.0, min_ratio=3.0,
                               combined=True)
    assert plain.values["pos1-exclsats"] == ""


def test_clamp():
    a, b = datetime(2026, 10, 20, 14, 0, tzinfo=UTC), datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
    assert sesion.clamp(a, b, None) == (a, b)
    assert sesion.clamp(a, b, TimeWindow()) == (a, b)
    w = TimeWindow(a + timedelta(minutes=10), b + timedelta(hours=2))
    assert sesion.clamp(a, b, w) == (a + timedelta(minutes=10), b)
    assert sesion.clamp(a, b, TimeWindow(end=a + timedelta(minutes=5))) == (a, a + timedelta(minutes=5))
    # sin zona se toma como UTC
    naive = TimeWindow(datetime(2026, 10, 20, 14, 30))
    assert sesion.clamp(a, b, naive)[0] == datetime(2026, 10, 20, 14, 30, tzinfo=UTC)
    with pytest.raises(UserError, match="menos de un minuto"):
        sesion.clamp(a, b, TimeWindow(b + timedelta(minutes=1), b + timedelta(minutes=20)), what="con la base X")
    with pytest.raises(UserError, match="termina antes"):
        sesion.clamp(a, b, TimeWindow(b, a))
    assert sesion.window_text(w) == "2026-10-20 14:10:00–17:00:00 UTC"
    assert sesion.window_text(TimeWindow(a, b + timedelta(days=1))) == "2026-10-20 14:00:00 – 2026-10-21 15:00:00 UTC"
    assert sesion.window_text(TimeWindow(a, b)) == "2026-10-20 14:00:00–15:00:00 UTC"
    assert sesion.describe(ProcessingOptions()) == []
    assert len(sesion.describe(ProcessingOptions(time_window=w, excluded_sats=["G13"]))) == 2


def test_project_keeps_window_and_exclusions(tmp_path):
    w = TimeWindow(datetime(2026, 10, 20, 14, 5, tzinfo=UTC), datetime(2026, 10, 20, 14, 15, tzinfo=UTC))
    project = Project(name="sesion", options=ProcessingOptions(time_window=w, excluded_sats=["R", "G13"]))
    path = tmp_path / "sesion.tvppk"
    proj.save(project, path, "prueba")
    again = proj.load(path)
    assert again.options.time_window == w and again.options.excluded_sats == ["R", "G13"]
    # sin editar: toda la sesión y nada fuera
    proj.save(Project(name="limpio"), tmp_path / "limpio.tvppk", "prueba")
    clean = proj.load(tmp_path / "limpio.tvppk")
    assert clean.options.time_window is None and clean.options.excluded_sats == []
    # un proyecto editado a mano: lo ilegible no tumba la apertura
    text = path.read_text(encoding="utf-8").replace('"G13"', '"G13", "basura"').replace(
        '"end": "2026-10-20T14:15:00+00:00"', '"end": "2026-10-20T14:00:00+00:00"')
    path.write_text(text, encoding="utf-8")
    odd = proj.load(path)
    assert odd.options.excluded_sats == ["R", "G13"] and odd.options.time_window is None


def test_quality_timeline_per_satellite(unzip):
    q = quality.assess(ObservationSet(obs=[unzip("ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz")]))
    assert q.interval_s == 15.0
    assert set(q.tracks) == set(q.satellites)
    for sat, spans in q.tracks.items():
        assert all(a.tzinfo is not None and a <= b for a, b in spans), sat
    first = min(a for spans in q.tracks.values() for a, _ in spans)
    last = max(b for spans in q.tracks.values() for _, b in spans)
    assert (last - first).total_seconds() == 80 * 15.0          # 81 épocas
    assert first.second == 42                                    # 14:00:00 GPS = 13:59:42 UTC
    assert {s: len(t) for s, t in q.slip_times.items()} == q.cycle_slips


# --- RTKLIB de verdad ------------------------------------------------------------------------------------------------

def _prepare(tmp_path):
    rover = api.prepare_observations(ROVER, tmp_path / "rover")
    base = api.prepare_observations(BASE, tmp_path / "base", marker="ICEP")
    return rover, base, api.find_station("ICEP")


def _conf(path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value
    return values


def _pos_utc(path) -> list[datetime]:
    return [gps_to_utc(e.time.replace(tzinfo=UTC)) for e in rtklib.parse_pos(path)]


@needs_tools
def test_excluding_a_satellite_changes_conf_and_solution(tmp_path):
    rover, base, station = _prepare(tmp_path)
    full = api.process_static(rover, [(station, base)], RTK, tmp_path / "todo", point_name="ITLA")
    b0 = full.baselines[0]
    assert b0.engine == "RTKLIB" and _conf(b0.config_file)["pos1-exclsats"] == ""
    used = [s for s in b0.satellites if s[0] in "EC"]      # en estos 20 min la libre de ionosfera usa R, E y C
    assert len(used) >= 6
    out = used[0]
    options = replace(RTK, excluded_sats=[out, "R"])
    edited = api.process_static(rover, [(station, base)], options, tmp_path / "sin", point_name="ITLA")
    b1 = edited.baselines[0]
    conf = _conf(b1.config_file)
    assert conf["pos1-exclsats"] == out
    assert int(conf["pos1-navsys"]) & 4 == 0 and int(_conf(b0.config_file)["pos1-navsys"]) & 4 == 4
    assert out not in b1.satellites and not any(s.startswith("R") for s in b1.satellites)
    assert any(s.startswith("R") for s in b0.satellites)
    p0, p1 = (np.array([r.position.x, r.position.y, r.position.z]) for r in (full, edited))
    assert np.linalg.norm(p1 - p0) > 1e-4                         # la solución cambia (más de 0.1 mm)
    assert edited.excluded_sats == ["R", out] and edited.time_window is None
    assert any("fuera del cálculo" in line and out in line for line in edited.log)


@needs_tools
def test_time_window_leaves_only_its_epochs_in_pos(tmp_path):
    rover, base, station = _prepare(tmp_path)
    window = TimeWindow(datetime(2026, 10, 20, 14, 5, tzinfo=UTC), datetime(2026, 10, 20, 14, 15, tzinfo=UTC))
    result = api.process_static(rover, [(station, base)], replace(RTK, time_window=window), tmp_path / "v",
                                point_name="ITLA")
    b = result.baselines[0]
    times = _pos_utc(b.pos_file)
    assert times and all(window.start <= t <= window.end for t in times)
    # todas las épocas del rover dentro de la ventana (a 15 s, en :12, :27, :42 y :57 UTC): 40
    q = result.rover_quality
    inside = sum(1 for t in _epochs_utc(q) if window.start <= t <= window.end)
    assert inside == 40 and len(set(times)) == inside, (len(set(times)), inside)
    assert b.epochs_used <= inside
    assert result.time_window == window
    assert any("ventana de tiempo 2026-10-20 14:05:00–14:15:00 UTC" in line for line in result.log)


def _epochs_utc(q) -> list[datetime]:
    first = min(a for spans in q.tracks.values() for a, _ in spans)
    last = max(b for spans in q.tracks.values() for _, b in spans)
    n = int((last - first).total_seconds() // q.interval_s) + 1
    return [first + timedelta(seconds=k * q.interval_s) for k in range(n)]


@needs_tools
def test_points_window_and_exclusions(tmp_path):
    rover, base, station = _prepare(tmp_path)
    window = TimeWindow(datetime(2026, 10, 20, 14, 0, tzinfo=UTC), datetime(2026, 10, 20, 14, 10, tzinfo=UTC))
    inside = FieldPoint(name="P1", code="", start=datetime(2026, 10, 20, 14, 2, tzinfo=UTC),
                        end=datetime(2026, 10, 20, 14, 5, tzinfo=UTC), antenna_height_m=0.0, total_height_m=0.067)
    outside = FieldPoint(name="P2", code="", start=datetime(2026, 10, 20, 14, 12, tzinfo=UTC),
                         end=datetime(2026, 10, 20, 14, 15, tzinfo=UTC), antenna_height_m=0.0, total_height_m=0.067)
    options = ProcessingOptions(time_window=window, excluded_sats=["G13", "C"])
    result = api.process_points(rover, (station, base), [inside, outside], options, tmp_path / "j")
    times = _pos_utc(result.pos_file)
    assert times and all(window.start <= t <= window.end for t in times)
    conf = _conf(result.config_file)
    assert conf["pos1-exclsats"] == "G13" and int(conf["pos1-navsys"]) & 32 == 0
    p1, p2 = result.points
    assert p1.epochs_total > 0
    assert p2.epochs_total == 0 and "fuera de la ventana de tiempo" in p2.message
    assert result.excluded_sats == ["C", "G13"] and result.time_window == window


# --- Procesador propio de líneas largas ------------------------------------------------------------------------------

@needs_tools
def test_own_processor_gets_window_and_systems(tmp_path, monkeypatch):
    """«auto» a 30.7 km usa el procesador propio: recibe el inicio y el fin de la ventana (segundos GPS) y los
    sistemas sin el que se quitó. Aquí se intercepta la llamada (el propio se prueba aparte) y se deja caer a RTKLIB."""
    from tresvizo_ppk.core.larga import procesador
    from tresvizo_ppk.core.larga.astro import gps_seconds
    from tresvizo_ppk.core.geodesy import utc_to_gps
    seen = {}

    def fake(rover, base, nav, precise, **kwargs):
        seen.update(kwargs)
        raise UserError("interceptado en la prueba")
    monkeypatch.setattr(procesador, "process", fake)
    rover, base, station = _prepare(tmp_path)
    window = TimeWindow(datetime(2026, 10, 20, 14, 3, tzinfo=UTC), datetime(2026, 10, 20, 14, 17, tzinfo=UTC))
    options = ProcessingOptions(time_window=window, excluded_sats=["E"])
    result = api.process_static(rover, [(station, base)], options, tmp_path / "p", point_name="ITLA")
    assert seen["systems"] == "GC"
    assert seen["start"] == gps_seconds(utc_to_gps(window.start).replace(tzinfo=None))
    assert seen["end"] == gps_seconds(utc_to_gps(window.end).replace(tzinfo=None))
    assert "excluded_sats" not in seen                     # sin satélites sueltos no se le pasa nada
    assert any("interceptado" in w for w in result.warnings)


def test_own_processor_exclusions_are_passed_when_supported(monkeypatch):
    from tresvizo_ppk.core.larga import procesador
    original = procesador.process

    def with_exclusions(*args, excluded_sats=None, **kwargs):  # noqa: ARG001
        return original(*args, **kwargs)
    monkeypatch.setattr(procesador, "process", with_exclusions)
    assert baseline.own_takes_exclusions()
    monkeypatch.setattr(procesador, "process", original)
    assert baseline.own_takes_exclusions() == ("excluded_sats" in __import__("inspect").signature(original).parameters)
