import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tresvizo_ppk import project as proj
from tresvizo_ppk.model import CoordinateChoice, Ecef, Enu, Ephemeris, ProcessingOptions, Project, Station


def station(code='CHIH'):
    return Station(id=code, name='Chihuahua', position=Ecef(-1_000_000.123, -5_500_000.456, 3_000_000.789),
                   sigma_enu=Enu(0.002, 0.002, 0.005), antenna_type='TRM59800.00', radome='SCIS',
                   antenna_height_m=0.0083, source='INEGI RGNA 2024', official=False)


def make(tmp_path):
    data = tmp_path/'datos'
    data.mkdir()
    rover = data/'rover.obs'
    rover.write_bytes(b'rinex rover')
    base = data/'chih2990.24d.Z'
    base.write_bytes(b'rinex base')
    sp3 = data/'igs.sp3'
    sp3.write_bytes(b'orbitas')
    p = Project(kind='static', point_name='PV-01', pole_height_m=1.8,
                options=ProcessingOptions(elevation_mask_deg=10, ephemeris=Ephemeris.PRECISE, precise_files=[sp3]),
                coordinates=CoordinateChoice('mexico-itrf2008-utm', '13N', 'ggm25', 'UTM 13N'),
                bases=[station()], created=datetime(2026, 10, 26, 18, tzinfo=timezone.utc),
                position=Ecef(-1_010_000.5, -5_490_000.25, 3_010_000.125), sigma_enu=Enu(0.004, 0.005, 0.011))
    return p, rover, base, sp3


def test_roundtrip_relative_paths_and_hashes(tmp_path):
    p, rover, base, sp3 = make(tmp_path)
    folder = tmp_path/'proyectos'
    proj.set_inputs(p, {'rover': [rover], 'base:CHIH': [base]}, folder)
    path = proj.save(p, folder/'Vértice norte', app_version='0.1.0')
    assert path.name == 'Vértice norte.tvppk'
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['inputs']['rover'] == ['../datos/rover.obs']
    assert raw['options']['precise_files'] == ['../datos/igs.sp3']
    assert raw['hashes']['../datos/rover.obs'] == proj.sha256(rover)
    loaded = proj.load(path)
    assert loaded.name == 'Vértice norte' and loaded.point_name == 'PV-01' and loaded.pole_height_m == 1.8
    assert loaded.options.ephemeris is Ephemeris.PRECISE and loaded.options.precise_files == [sp3.resolve()]
    assert loaded.coordinates.zone == '13N' and loaded.coordinates.geoid_id == 'ggm25'
    assert loaded.bases[0].radome == 'SCIS' and loaded.bases[0].official is False
    assert loaded.bases[0].position.x == pytest.approx(-1_000_000.123, abs=1e-9)
    assert loaded.created == p.created
    assert loaded.position == p.position and loaded.sigma_enu == p.sigma_enu
    assert proj.resolve_inputs(loaded)['base:CHIH'] == [base.resolve()]
    assert proj.check_inputs(loaded) == []


def test_moved_project_folder_keeps_working(tmp_path):
    p, rover, base, _ = make(tmp_path)
    proj.set_inputs(p, {'rover': [rover]}, tmp_path)
    proj.save(p, tmp_path/'p.tvppk')
    moved = tmp_path.parent/(tmp_path.name+'-copia')
    tmp_path.rename(moved)
    loaded = proj.load(moved/'p.tvppk')
    assert proj.check_inputs(loaded) == []
    assert proj.resolve_inputs(loaded)['rover'][0] == (moved/'datos'/'rover.obs').resolve()


def test_changed_and_missing_inputs_are_reported(tmp_path):
    p, rover, base, _ = make(tmp_path)
    proj.set_inputs(p, {'rover': [rover], 'base:CHIH': [base]}, tmp_path)
    proj.save(p, tmp_path/'p.tvppk')
    rover.write_bytes(b'otro contenido')
    base.unlink()
    issues = proj.check_inputs(proj.load(tmp_path/'p.tvppk'))
    assert {(i.role, i.kind) for i in issues} == {('rover', 'changed'), ('base:CHIH', 'missing')}
    assert 'cambió' in issues[0].message or 'cambió' in issues[1].message


def test_rejects_foreign_newer_and_damaged_files(tmp_path):
    bad = tmp_path/'x.tvppk'
    bad.write_text('{"formato": "otro"}', encoding='utf-8')
    with pytest.raises(proj.ProjectError, match='no es un proyecto'):
        proj.load(bad)
    bad.write_text(json.dumps({'formato': proj.FORMAT, 'version': 99, 'kind': 'static'}), encoding='utf-8')
    with pytest.raises(proj.ProjectError, match='más nueva'):
        proj.load(bad)
    bad.write_text('{roto', encoding='utf-8')
    with pytest.raises(proj.ProjectError):
        proj.load(bad)
    with pytest.raises(proj.ProjectError, match='No se encuentra'):
        proj.load(tmp_path/'no.tvppk')


def test_missing_file_on_save_is_a_user_error(tmp_path):
    p = Project()
    with pytest.raises(proj.ProjectError, match='No se encuentra'):
        proj.set_inputs(p, {'rover': [tmp_path/'nada.obs']}, tmp_path)


def test_workdir_next_to_project(tmp_path):
    p = Project(name='Jornada', folder=tmp_path)
    assert proj.workdir(p) == tmp_path/'Jornada.trabajo'
    assert proj.workdir(p, tmp_path/'otra.tvppk') == tmp_path/'otra.trabajo'
    with pytest.raises(proj.ProjectError):
        proj.workdir(Project())


def test_atomic_save_keeps_previous_on_failure(tmp_path, monkeypatch):
    p = Project(name='a')
    path = proj.save(p, tmp_path/'a.tvppk')
    before = path.read_bytes()
    def boom(*a, **k):
        raise OSError('disco lleno')
    monkeypatch.setattr(proj.os, 'replace', boom)
    with pytest.raises(OSError):
        proj.save(Project(name='b', point_name='nuevo'), path)
    assert path.read_bytes() == before
    assert [f.name for f in tmp_path.iterdir()] == ['a.tvppk']


def test_quality_settings_roundtrip(tmp_path):
    from tresvizo_ppk.model import ControlCoordinate, ProjectTolerances
    p, rover, base, sp3 = make(tmp_path)
    proj.set_inputs(p, {'rover': [rover], 'base:CHIH': [base]}, tmp_path)
    p.tolerances = ProjectTolerances(order='', max_sigma_h_m=0.015, max_pdop=None, min_duration_s=900.0)
    p.control_points = [ControlCoordinate('C1', 2419000.123, 780000.456, None, 'mojonera'),
                        ControlCoordinate('BN', None, None, 1885.1)]
    p.control_source = 'datos/control.csv'
    p.occupation_choices = {'P07': 2, 'P09': 0}
    path = proj.save(p, tmp_path/'obra.tvppk')
    q = proj.load(path)
    assert q.tolerances == p.tolerances and q.tolerances.max_pdop is None
    assert q.control_points == p.control_points and q.control_source == 'datos/control.csv'
    assert q.occupation_choices == {'P07': 2, 'P09': 0}
    # sin tolerancias guardadas (proyecto de antes) quedan las de omisión; lo que no se entiende se ignora
    data = json.loads(path.read_text(encoding='utf-8'))
    data.pop('tolerances')
    data['control_points'].append({'name': 'X', 'northing_m': 'no'})
    data['occupation_choices']['P10'] = 'dos'
    path.write_text(json.dumps(data), encoding='utf-8')
    q = proj.load(path)
    assert q.tolerances is None and len(q.control_points) == 2 and 'P10' not in q.occupation_choices
