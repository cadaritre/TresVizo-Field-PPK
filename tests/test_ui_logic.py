"""Lógica de la interfaz sin abrir ventanas: asistentes, validaciones, preferencias, tareas y temas."""
import json
import math
import threading
import time
from pathlib import Path

import pytest

from app import wizard as wz
from app.settings import Settings, SettingsError, format_length
from app.tasks import Stages, Worker, classify
from app.themes import BUILTINS, contrast, derived, issues
from tresvizo_ppk.errors import Cancelled, UserError
from tresvizo_ppk.model import Ecef, Ephemeris, ProcessingOptions, Project, Station
from tresvizo_ppk import project as proj


def touch(folder, name, data=b'x'):
    path = Path(folder)/name
    path.write_bytes(data)
    return path


def station(code='CHIH', official=True):
    return Station(id=code, name=f'Estación {code}', position=Ecef(-1.0e6, -5.5e6, 3.0e6), official=official)


@pytest.mark.parametrize('name,kind', [
    ('sesion.bin', 'recording'), ('sesion.part', 'recording'), ('chih2990.24o', 'obs'), ('chih2990.24d.Z', 'obs'),
    ('chih2990.24n.gz', 'nav'), ('CHIH00MEX_R_20242990000_01D_30S_MO.crx.gz', 'obs'),
    ('CHIH00MEX_R_20242990000_01D_MN.rnx', 'nav'), ('rover.obs', 'obs'), ('rover.nav', 'nav'),
    ('IGS0OPSFIN_20242990000_01D_15M_ORB.SP3.gz', 'precise'), ('igs23456.sp3.Z', 'precise'),
    ('puntos.csv', 'csv'), ('trabajo.tvppk', 'project'), ('chih299.zip', 'zip'), ('foto.jpg', 'unknown')])
def test_classify(name, kind):
    assert wz.classify(Path(name)) == kind


def test_guess_marker():
    assert wz.guess_marker(Path('chih2990.24d.Z')) == 'CHIH'
    assert wz.guess_marker(Path('INEG00MEX_R_2024.crx')) == 'INEG'
    assert wz.guess_marker(Path('12345.obs')) == ''


def test_numbers_and_angles():
    assert wz.parse_number(' 1,805 ') == pytest.approx(1.805)
    assert wz.parse_number('2 345 678.125') == pytest.approx(2345678.125)
    with pytest.raises(ValueError):
        wz.parse_number('nan')
    assert wz.parse_angle('19°25\'12.0"N', 'N', 'S') == pytest.approx(19.42)
    assert wz.parse_angle('99 07 48 W', 'E', 'W') == pytest.approx(-99.13)
    assert wz.parse_angle('-99.13', 'E', 'W') == pytest.approx(-99.13)
    assert wz.parse_angle('99 07 48 O', 'E', 'W') == pytest.approx(-99.13)
    with pytest.raises(ValueError):
        wz.parse_angle("19 75 00", 'N', 'S')


def test_geodetic_to_ecef_grs80():
    x, y, z = wz.geodetic_to_ecef(0, 0, 0)
    assert (x, y, z) == pytest.approx((6378137.0, 0, 0))
    x, y, z = wz.geodetic_to_ecef(90, 0, 0)
    assert z == pytest.approx(6356752.3141, abs=1e-3)
    # Ida y vuelta aproximada con el radio.
    x, y, z = wz.geodetic_to_ecef(19.4326, -99.1332, 2240)
    assert 6.37e6 < math.sqrt(x*x+y*y+z*z) < 6.385e6


def test_static_steps_validation(tmp_path):
    job = wz.StaticJob()
    assert job.first_invalid_step() == 0
    messages = [i.message for i in job.step_issues(0)]
    assert any('Agrega la grabación' in m for m in messages)
    assert any('nombre del punto' in m for m in messages)
    job.rover_files = [touch(tmp_path, 'sesion.bin')]
    job.point_name, job.pole_height = 'PV-01', '2,0'
    assert wz.errors(job.step_issues(0)) == []
    job.pole_height = '-1'
    assert wz.errors(job.step_issues(0))
    job.pole_height = '6'
    assert [i.level for i in job.step_issues(0)] == ['warning']
    job.pole_height = '1.8'
    job.point_name = 'a/b'
    assert wz.errors(job.step_issues(0))
    job.point_name = 'PV-01'
    job.rover_files.append(touch(tmp_path, 'rover.24o'))
    assert any('Mezclaste' in i.message for i in job.step_issues(0))
    job.rover_files = [touch(tmp_path, 'rover.24o')]
    assert [i.level for i in job.step_issues(0)] == ['warning']   # sin navegación
    assert job.first_invalid_step() == 1
    job.bases = [wz.BaseEntry(files=[touch(tmp_path, 'chih2990.24d.Z')])]
    assert any('redes instaladas' in i.message for i in wz.errors(job.step_issues(1)))
    job.bases[0].station = station()
    assert wz.errors(job.step_issues(1)) == []
    assert any('una sola estación' in i.message for i in job.step_issues(1))
    job.bases.append(wz.BaseEntry(files=[touch(tmp_path, 'chih2990.24n')], station=station()))
    assert any('repetida' in i.message for i in job.step_issues(1))
    job.bases[1].station = station('INEG', official=False)
    assert wz.errors(job.step_issues(1)) == []
    assert any('corregida a mano' in i.message for i in job.step_issues(1))
    job.bases += [wz.BaseEntry(files=[touch(tmp_path, f'b{i}.24o')], station=station(f'B00{i}')) for i in range(2)]
    assert any('máximo 3' in i.message for i in job.step_issues(1))
    job.bases = job.bases[:2]
    assert job.first_invalid_step() is None


def test_options_validation(tmp_path):
    options = ProcessingOptions(elevation_mask_deg=40)
    assert wz.errors(wz.options_issues(options, False))
    options = ProcessingOptions(elevation_mask_deg=5, constellations=('Galileo',))
    assert [i.level for i in wz.options_issues(options, False)] == ['warning', 'warning']
    options = ProcessingOptions(ephemeris=Ephemeris.PRECISE)
    assert wz.errors(wz.options_issues(options, False))
    assert wz.errors(wz.options_issues(options, True)) == []
    options.precise_files = [tmp_path/'falta.sp3']
    assert wz.errors(wz.options_issues(options, False))
    assert wz.errors(wz.options_issues(ProcessingOptions(constellations=()), False))


def test_static_project_roundtrip(tmp_path):
    rover, base = touch(tmp_path, 'sesion.bin'), touch(tmp_path, 'chih2990.24o')
    job = wz.StaticJob(rover_files=[rover], point_name='PV-01', pole_height='1.8', number='TOP-2026-114',
                       bases=[wz.BaseEntry([base], station(), name='Chihuahua (INEGI)')])
    project = job.to_project()
    assert project.bases[0].name == 'Chihuahua (INEGI)' and project.pole_height_m == 1.8
    proj.set_inputs(project, job.files(), tmp_path)
    path = proj.save(project, tmp_path/'pv01')
    loaded = proj.load(path)
    again = wz.StaticJob.from_project(loaded, proj.resolve_inputs(loaded))
    assert again.rover_files == [rover.resolve()] and again.bases[0].station.id == 'CHIH'
    assert again.bases[0].name == 'Chihuahua (INEGI)' and again.pole_height == '1.800'
    assert loaded.number == 'TOP-2026-114' and again.number == 'TOP-2026-114'
    assert again.first_invalid_step() is None
    again.number = 'x'*41
    assert wz.errors(again.step_issues(2))


def test_points_own_base_from_project_and_manual(tmp_path):
    job = wz.PointsJob(base_kind='own')
    assert job.own_source == 'rinex'          # por omisión no se pide escribir la coordenada
    found = wz.errors(job.step_issues(0))
    assert {i.field for i in found} >= {'base', 'own_name', 'own_height'}
    job.base.files = [touch(tmp_path, 'base.bin')]
    job.own_name, job.own_height = 'Base Norte', '1.500'
    assert any('Leyendo' in i.message for i in wz.errors(job.step_issues(0)))
    job.rinex_missing = True
    assert any('no trae posición' in i.message for i in wz.errors(job.step_issues(0)))
    job.rinex_position, job.rinex_missing = Ecef(-1.0e6, -5.5e6, 3.0e6), False
    assert [i.level for i in job.step_issues(0)] == ['warning']
    assert job.station().source == wz.RINEX_SOURCE and not job.station().official
    job.own_source = 'project'
    job.own_project = tmp_path/'pv.tvppk'
    assert any('todavía no tiene coordenada' in i.message for i in job.step_issues(0))
    job.own_position = Ecef(-1.0e6, -5.5e6, 3.0e6)
    assert wz.errors(job.step_issues(0)) == []
    st = job.station()
    assert st.id == 'BASENORTE' and not st.official and st.antenna_height_m == 1.5 and 'pv.tvppk' in st.source
    assert st.source.startswith('Georreferenciada')
    job.own_source, job.coord_text = 'manual', ('19 25 12 N', '99 07 48 W', '2240')
    assert [i.level for i in job.step_issues(0)] == ['warning']
    job.coord_format, job.coord_text = 'ecef', ('1', '2', '3')
    assert any('superficie' in i.message for i in job.step_issues(0))
    job.coord_text = ('', '', '')
    assert any('completa' in i.message for i in job.step_issues(0))


def test_points_steps_and_roundtrip(tmp_path):
    job = wz.PointsJob(base=wz.BaseEntry([touch(tmp_path, 'chih2990.24o')], station(), ''))
    assert wz.errors(job.step_issues(0)) == []
    job.rover_files = [touch(tmp_path, 'jornada.bin')]
    assert any('CSV' in i.message for i in job.step_issues(1))
    job.csv_file = touch(tmp_path, 'puntos.txt')
    assert any('CSV de TresVizo Field' in i.message for i in job.step_issues(1))
    job.csv_file = touch(tmp_path, 'puntos.csv')
    assert job.first_invalid_step() is None
    project = job.to_project(folder=tmp_path)
    proj.set_inputs(project, job.files(), tmp_path)
    loaded = proj.load(proj.save(project, tmp_path/'jornada'))
    again = wz.PointsJob.from_project(loaded, proj.resolve_inputs(loaded))
    assert again.base.station.id == 'CHIH' and again.csv_file == job.csv_file.resolve()
    assert again.first_invalid_step() is None
    # Base propia encadenada: la ruta del proyecto estático va relativa.
    own = wz.PointsJob(base_kind='own', base=wz.BaseEntry([touch(tmp_path, 'base.bin')]), own_name='B1',
                       own_height='1.2', own_source='project', own_project=tmp_path/'sub'/'pv.tvppk', own_position=Ecef(-1e6, -5.5e6, 3e6),
                       rover_files=job.rover_files, csv_file=job.csv_file)
    project = own.to_project(folder=tmp_path)
    assert project.source_project == 'sub/pv.tvppk' and project.pole_height_m == 1.2
    proj.set_inputs(project, own.files(), tmp_path)
    loaded = proj.load(proj.save(project, tmp_path/'propia'))
    again = wz.PointsJob.from_project(loaded, proj.resolve_inputs(loaded))
    assert again.own_source == 'project' and again.own_project == (tmp_path/'sub'/'pv.tvppk').resolve()
    assert again.own_position == own.own_position and again.own_height == '1.200'


def test_settings_roundtrip_recents_and_damaged_file(tmp_path):
    s = Settings(tmp_path)
    s.save(company='Topografía del Norte', surveyor='Ing. Ana Pérez', units='ftUS')
    for i in range(15):
        s.add_recent(tmp_path/f'p{i}.tvppk')
    s.add_recent(tmp_path/'p3.tvppk')
    again = Settings(tmp_path)
    assert again.values.company == 'Topografía del Norte' and again.values.units == 'ftUS'
    assert len(again.values.recents) == 12 and again.values.recents[0].endswith('p3.tvppk')
    assert again.values.recents.count(again.values.recents[0]) == 1
    with pytest.raises(SettingsError):
        s.save(units='leguas')
    with pytest.raises(SettingsError):
        s.save(company='x'*200)
    (tmp_path/'preferencias.json').write_text('{roto', encoding='utf-8')
    damaged = Settings(tmp_path)
    assert damaged.values.company == '' and damaged.recovered_from and damaged.recovered_from.exists()


def test_settings_logo_is_copied_normalized(tmp_path):
    from PIL import Image
    source = tmp_path/'logo.jpg'
    Image.new('RGB', (3000, 1000), 'navy').save(source)
    s = Settings(tmp_path/'conf')
    copy = s.set_logo(source)
    assert copy.parent == tmp_path/'conf' and copy.suffix == '.png'
    with Image.open(copy) as image:
        assert max(image.size) == 1200
    assert s.profile().logo == copy
    second = s.set_logo(source)
    assert not copy.exists() and second.exists()
    bad = tmp_path/'x.png'
    bad.write_bytes(b'no es imagen')
    with pytest.raises(SettingsError):
        s.set_logo(bad)


def test_format_length_units(tmp_path):
    s = Settings(tmp_path)
    assert format_length(1234.5678, s.values) == '1 234.568 m'
    s.save(units='ftUS', decimals=2)
    assert format_length(1.0, s.values) == '3.28 ftUS'
    assert format_length(None, s.values) == '—'


def test_worker_progress_done_error_cancel():
    def work(progress, cancel):
        for i in range(5):
            progress(i/4, f'paso {i}')
        return 42
    w = Worker(work).start()
    w.thread.join(2)
    events = w.poll()
    assert events[-1] == ('done', 42) and events[0][0] == 'progress' and events[0][1] == 1.0
    w = Worker(lambda p, c: (_ for _ in ()).throw(UserError('Faltan las efemérides.'))).start()
    w.thread.join(2)
    assert w.poll()[-1][:3][0] == 'user'
    started = threading.Event()

    def slow(progress, cancel):
        started.set()
        while not cancel.is_set():
            time.sleep(.01)
        raise Cancelled()
    w = Worker(slow).start()
    started.wait(2)
    w.cancel.set()
    w.thread.join(2)
    assert w.poll()[-1][0] == 'cancelled' and w.finished
    w = Worker(lambda p, c: 1/0).start()
    w.thread.join(2)
    kind, exc, message, detail = w.poll()[-1]
    assert kind == 'program' and isinstance(exc, ZeroDivisionError) and 'ZeroDivisionError' in detail


def test_classify_errors():
    assert classify(UserError('x')) == ('user', 'x')
    assert classify(Cancelled())[0] == 'cancelled'
    assert classify(proj.ProjectError('dañado')) == ('user', 'dañado')
    assert classify(OSError(28, 'No space'))[0] == 'user'
    assert classify(KeyError('k'))[0] == 'program'


def test_stages_split_progress():
    seen = []
    stages = Stages(lambda f, m: seen.append((round(f, 3), m)), [1, 3])
    stages.stage(0)(1.0, 'a')
    stages.stage(1)(0.5, 'b')
    stages.stage(1)(2.0, 'c')
    assert seen == [(0.25, 'a'), (0.625, 'b'), (1.0, 'c')]


def test_builtin_themes_meet_contrast():
    for name, palette in BUILTINS.items():
        assert issues(palette) == [], name
        t = derived(palette)
        for kind in ('error', 'warning', 'success', 'info'):
            assert contrast(t[kind+'_fg'], t[kind+'_bg']) >= 4.5, (name, kind)
        for key in ('fixed', 'float', 'single', 'none'):
            assert contrast(t['solution_'+key], t['surface']) >= 3, (name, key)
        for key in ('button', 'secondary', 'sidebar', 'selection'):
            assert contrast(t['on_'+key], t[key]) >= 4.5, (name, key)


def test_catalog_view_classes_search_and_groups():
    from types import SimpleNamespace as N
    from app import catalog_view as cv
    item = lambda **k: N(**{**dict(id='', kind='', title='', region='', countries=(), authority='A', license='L',
                                  license_url='', attribution='', transformation_notice='', version='1', revision=1,
                                  bytes=1, size_text='1 KB', bundled=False, installed=False, update_available=False,
                                  downloadable=True, requires=(), systems=0), **k})
    items = [item(id='crs-br', kind='crs-package', title='Brasil', countries=('BR',), systems=40),
             item(id='ggm10', kind='geoid', title='GGM10 · Geoide Gravimétrico Mexicano', countries=('MX',),
                  bundled=True, installed=True, downloadable=False),
             item(id='ntv2-es', kind='datum-grid', title='ED50 → ETRS89 · España', countries=('ES',)),
             item(id='rbmc', kind='cors-network', title='RBMC — IBGE, Brasil', countries=('BR',))]
    systems = [N(code='EPSG:6369', name='Mexico ITRF2008 / UTM zona 14N', kind='projected', datum='Mexico ITRF2008',
                 countries=(), frame='', legacy=False, accuracy_text='', missing_grids=(), geoid_model_id=None,
                 origin='fábrica', area='México'),
               N(code='EPSG:4326', name='WGS 84', kind='geographic2D', datum='World Geodetic System 1984',
                 countries=(), frame='', legacy=False, accuracy_text='', missing_grids=(), geoid_model_id=None,
                 origin='fábrica', area='Mundo')]
    crs = N(catalog=lambda: items, systems=lambda: systems, definition=lambda code: None)
    entries = cv.build(crs)
    # Las redes de estaciones viven en «Estaciones de referencia», no en este catálogo.
    assert cv.counts(entries) == {'projected': 2, 'geographic': 2, 'geoid': 1, 'grid': 1}
    assert not any(e.item_id == 'rbmc' or e.cls == 'cors' for e in entries)
    assert cv.CLASSES['grid'][0] == 'Transformaciones de datum' and cv.SHORT['grid'] == 'Transformación de datum'
    assert [e.title for e in cv.select(entries, 'projected', 'méxico')] == ['México ITRF2008 / UTM zona 14N']
    assert [e.title for e in cv.select(entries, None, 'EPSG:6369')] == ['México ITRF2008 / UTM zona 14N']
    assert ('Nombre EPSG', 'Mexico ITRF2008 / UTM zona 14N') in cv.select(entries, None, 'EPSG:6369')[0].details
    assert [e.cls for e in cv.select(entries, None, 'brasil')] == ['projected', 'geographic']
    assert all(not e.installed for e in cv.select(entries, None, state='available'))
    assert {e.title for e in cv.select(entries, None, state='installed')} >= {'WGS 84', 'GGM10 · Geoide Gravimétrico Mexicano'}
    groups = cv.grouped(cv.select(entries, 'projected'))
    assert list(groups) == ['Norteamérica', 'Sudamérica'] and list(groups['Sudamérica']) == ['Brasil']
    assert cv.grouped(cv.select(entries, 'geographic'))['Mundo']['Mundo'][0].title == 'WGS 84'
    assert cv.normalize('Ñandú México') == 'nandu mexico'



def test_every_country_of_the_bundled_index_has_its_name_and_region():
    """Un país nuevo en el índice firmado no puede salir como código suelto bajo «Mundo» en el catálogo."""
    import base64
    from app import catalog_view as cv
    from tresvizo_ppk.datos import biblioteca
    envelope = json.loads(Path(biblioteca.BUNDLED_INDEX).read_text(encoding='utf-8'))
    payload = json.loads(base64.b64decode(envelope['payload']))
    countries = {c for item in payload['items'] for c in item.get('countries', [])}
    assert len(countries) >= 33
    for code in countries:
        name, region = cv.country_region(code)
        assert name != code and region in cv.REGIONS and region != 'Mundo', code


def test_station_label_never_repeats_the_code():
    from types import SimpleNamespace as N
    from tresvizo_ppk.nombres import station_label
    assert station_label(N(id='ICEP', name='ICEP — Puebla, Pue.')) == 'ICEP — Puebla, Pue.'
    assert station_label(N(id='ICEP', name='Puebla, Pue.')) == 'ICEP (Puebla, Pue.)'
    assert station_label(N(id='BASE', name='')) == 'BASE'

def test_spanish_display_names_and_plurals():
    from tresvizo_ppk.nombres import display_name
    from app.formatting import plural
    assert display_name('Mexico ITRF2008 / UTM zona 14N + GGM10 height') == 'México ITRF2008 / UTM zona 14N · cota GGM10'
    assert display_name('NAD27 / UTM zone 13N') == 'NAD27 / UTM zona 13N'
    assert display_name('SIRGAS 2000 / UTM zone 23S', spanish='SIRGAS 2000 / UTM zona 23S') == 'SIRGAS 2000 / UTM zona 23S'
    assert display_name('') == ''
    assert plural(1, 'línea base', 'líneas base') == '1 línea base' and plural(2, 'línea base', 'líneas base') == '2 líneas base'
    assert plural(0, 'hueco') == '0 huecos'


def test_manual_base_frame_epoch_receiver_and_place_roundtrip(tmp_path):
    """Base propia escrita a mano: marco y época elegidos, aviso si no son los de la red de la zona, receptor del
    ObservationSet, y todo (más el lugar de la firma) se guarda y se reabre igual."""
    job = wz.PointsJob(base_kind='own', base=wz.BaseEntry([touch(tmp_path, 'base.bin')]), own_name='B1',
                       own_height='1.5', own_source='manual', coord_format='ecef',
                       coord_text=('-1000000.1234', '-5500000.5678', '3000000.9012'), own_receiver='MeridianVI',
                       rover_files=[touch(tmp_path, 'jornada.bin')], csv_file=touch(tmp_path, 'puntos.csv'),
                       place='Aguascalientes, Ags.')
    job.coord_frame, job.coord_epoch = 'NAD83(2011)', '2010,0'
    st = job.station()
    assert (st.position.frame, st.position.epoch) == ('NAD83(2011)', 2010.0)
    assert st.receiver == 'MeridianVI' and st.coordinate_origin == 'manual'
    assert [i.level for i in job.step_issues(0)] == ['warning']
    assert 'NAD83(2011), época 2010' in job.step_issues(0)[0].message
    job.network_frame = ('RGNA — INEGI, México', 'ITRF2008', 2010.0)
    issue = job.step_issues(0)[0]
    assert issue.level == 'warning' and 'marcos distintos' in issue.message and 'no se transforma' in issue.message
    job.coord_frame = 'itrf 2008'
    assert 'marcos distintos' not in job.step_issues(0)[0].message     # mismo marco, escrito distinto
    job.coord_epoch = '2024.5'
    assert 'marcos distintos' in job.step_issues(0)[0].message          # misma red, otra época
    for bad in ('', 'año', '1800'):
        job.coord_epoch = bad
        assert any(i.field == 'own_frame' and i.level == 'error' for i in job.step_issues(0))
    job.coord_frame, job.coord_epoch = 'ETRS89', '1989.0'
    project = job.to_project(folder=tmp_path)
    proj.set_inputs(project, job.files(), tmp_path)
    loaded = proj.load(proj.save(project, tmp_path/'propia'))
    assert loaded.place == 'Aguascalientes, Ags.'
    again = wz.PointsJob.from_project(loaded, proj.resolve_inputs(loaded))
    assert again.own_source == 'manual' and (again.coord_frame, again.coord_epoch) == ('ETRS89', '1989.0')
    assert again.coord_text == job.coord_text and again.own_receiver == 'MeridianVI' and again.place == job.place
    assert again.station().position == job.station().position
    # Las candidatas aproximadas se reabren como aproximadas (antes volvían como «escrita a mano»).
    approx = wz.PointsJob(base_kind='own', base=wz.BaseEntry([touch(tmp_path, 'base.bin')]), own_name='B2',
                          own_height='1.5', candidates=[Station('B2', 'B2', Ecef(-1e6, -5.5e6, 3e6), official=False,
                                                                source='Media autónoma', coordinate_origin='autonomous',
                                                                approximate=True)],
                          rinex_position=Ecef(-1e6, -5.5e6, 3e6))
    project = approx.to_project(folder=tmp_path)
    proj.set_inputs(project, approx.files(), tmp_path)
    loaded = proj.load(proj.save(project, tmp_path/'aprox'))
    assert loaded.bases[0].coordinate_origin == 'autonomous' and loaded.bases[0].approximate
    again = wz.PointsJob.from_project(loaded, proj.resolve_inputs(loaded))
    assert again.own_source == 'rinex' and again.rinex_position == Ecef(-1e6, -5.5e6, 3e6)
    # Estático: el lugar se guarda y vuelve.
    static = wz.StaticJob(rover_files=[touch(tmp_path, 'p.bin')], point_name='P1', pole_height='1.8',
                          bases=[wz.BaseEntry([touch(tmp_path, 'chih2990.24o')], station(), '')], place='León, Gto.')
    assert static.to_project().place == 'León, Gto.'
    assert wz.StaticJob.from_project(static.to_project(), static.files()).place == 'León, Gto.'
    static.place = 'x'*81
    assert any(i.field == 'place' for i in static.step_issues(2))
