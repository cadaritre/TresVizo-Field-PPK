"""La interfaz real (widgets de Tk) con los dobles del motor: asistentes, proyecto, proceso, cancelar, informe y
exportación. La ventana queda oculta; en Linux sin pantalla la CI usa xvfb. Se omite si no hay pantalla."""
import time
from pathlib import Path

import pytest

tk = pytest.importorskip('tkinter')

import dobles  # noqa: E402
from tresvizo_ppk import project as projects  # noqa: E402


def pump(app, until, timeout=20.0):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        app.update()
        if until():
            return True
        time.sleep(0.01)
    raise AssertionError(f"La interfaz no llegó al estado esperado a tiempo: {getattr(app, "shown", None)}")


@pytest.fixture
def app(tmp_path, monkeypatch):
    from tkinter import messagebox
    answers = {'askyesno': True, 'askokcancel': True, 'askyesnocancel': False}
    shown = []
    for name in ('showerror', 'showinfo', 'showwarning'):
        monkeypatch.setattr(messagebox, name, lambda *a, n=name, **k: shown.append((n, a)))
    for name, value in answers.items():
        monkeypatch.setattr(messagebox, name, lambda *a, v=value, **k: v)
    try:
        from app.main_window import Application
        application = Application(tmp_path/'conf', backend=dobles.backend())
    except tk.TclError as exc:
        pytest.skip(f'Sin pantalla para Tk: {exc}')
    application.withdraw()
    application.shown = shown
    application.settings.save(data_dir=str(tmp_path/'datos'), company='Empresa de prueba')
    yield application
    for page in application.pages.values():
        if getattr(page, 'worker', None) not in (None, 1):
            page.worker.cancel.set()
    application.destroy()


def files(tmp_path):
    folder = tmp_path/'campo'
    folder.mkdir(exist_ok=True)
    out = {}
    for name in ('S-20261024-150214.bin', 'ineg2970.26d.Z', 'ineg2970.26n.Z', 'uagu2970.26o', 'puntos.csv'):
        (folder/name).write_bytes(b'x'*100)
        out[name] = folder/name
    return out


def test_every_page_builds_and_theme_switch(app):
    from app.themes import palette
    for name in app.builders():
        app.show(name)
        app.update()
    app.theme.apply(palette('Oscuro'))
    app.update()
    app.theme.apply(palette('Claro'))
    app.update()
    assert app.current_page == 'Acerca de' and not app.shown


def test_static_wizard_full_flow(app, tmp_path):
    f = files(tmp_path)
    page = app.show('Georreferenciar punto')
    page.next()                                   # sin datos: no avanza y dice qué falta
    assert page.step == 0 and page.issues.winfo_children()
    page.add_rover([f['S-20261024-150214.bin']])
    page.name_var.set('LF-01')
    page.height_var.set('1,8')
    page.next()
    assert page.step == 1
    panel = page.base_panels.winfo_children()[0]
    panel.add([f['ineg2970.26d.Z'], f['ineg2970.26n.Z']])
    pump(app, lambda: page.job.bases[0].station is not None)   # se propone INEG por el nombre del archivo
    assert page.job.bases[0].station.id == 'INEG'
    page.add_base()
    page.base_panels.winfo_children()[1].add([f['uagu2970.26o']])
    pump(app, lambda: page.job.bases[1].station is not None)
    page.next()
    assert page.step == 2
    page.next()
    assert page.step == 3
    path = tmp_path/'proyectos'/'LF-01.tvppk'
    path.parent.mkdir()
    page.ask_path = lambda: path
    page.process()                                # guarda primero y luego procesa
    pump(app, lambda: page.output is not None)
    assert page.step == page.RESULT_STEP and path.exists()
    saved = projects.load(path)
    assert saved.processed is not None and saved.position == page.output['result'].position
    assert [b.id for b in saved.bases] == ['INEG', 'UAGU'] and saved.pole_height_m == 1.8
    assert page.output['coordinate'] is not None and page.output['coordinate'].zone == '13N'
    core = app.backend.core
    assert ('prepare', ['S-20261024-150214.bin'], 1.8, 'LF-01') in core.calls
    page.view_report()
    pump(app, lambda: any(w.winfo_class() == 'Toplevel' and 'Informe' in w.title() for w in app.winfo_children()))
    assert app.backend.report.calls[0][:2] == ('static', 'LF-01')
    assert page.report_path().exists()
    page.copy_coordinates()
    assert 'LF-01' in app.clipboard_get()


def test_rgna_guide_from_rover_session(app, tmp_path):
    f = files(tmp_path)
    page = app.show('Georreferenciar punto')
    page.add_rover([f['S-20261024-150214.bin']])
    entry = page.job.bases[0]
    page.network_guide(entry)                          # sin estación: lo dice
    assert app.shown[-1][1][1].startswith('Elige primero')
    entry.station = dobles.STATIONS[0]
    page.network_guide(entry)
    from app.network_guide import GuideDialog as RgnaGuideDialog
    pump(app, lambda: any(isinstance(w, RgnaGuideDialog) for w in app.winfo_children()))
    dialog = next(w for w in app.winfo_children() if isinstance(w, RgnaGuideDialog))
    assert 'INEG' in dialog.text and '15:02' in dialog.text and dialog.url
    assert not (app.settings.data_dir()/'Temporal').exists() or not any((app.settings.data_dir()/'Temporal').iterdir())


def test_station_label():
    from app.wizard_ui import station_label
    from tresvizo_ppk.model import Ecef, Station
    assert station_label(Station('INEG', 'INEG — Aguascalientes, Ags.', Ecef(0, 0, 0))) == 'INEG · Aguascalientes, Ags.'
    assert station_label(Station('UAGU', 'Universidad', Ecef(0, 0, 0))) == 'UAGU · Universidad'


def test_cancel_keeps_wizard_usable(app, tmp_path):
    app.backend.parts['core'] = dobles.FakeCore(delay=0.2)
    f = files(tmp_path)
    page = app.show('Georreferenciar punto')
    page.add_rover([f['S-20261024-150214.bin']])
    page.name_var.set('P1')
    page.height_var.set('2')
    page.job.bases[0].files = [f['ineg2970.26d.Z']]
    page.job.bases[0].station = dobles.STATIONS[0]
    page.path = tmp_path/'p.tvppk'
    page.project = page._write(page.path)
    page.dirty = False
    page.go(3)
    page.process()
    pump(app, lambda: page.progress.bar['value'] > 0)
    page.cancel()
    pump(app, lambda: page.worker is None)
    assert page.output is None and page.step == 3 and 'Cancelado' in page.progress.message.cget('text')


def test_engine_error_is_shown_to_user(app, tmp_path):
    app.backend.parts['core'] = dobles.FakeCore(fail='Las estaciones no se traslapan con el rover.')
    f = files(tmp_path)
    page = app.show('Georreferenciar punto')
    page.job.rover_files = [f['S-20261024-150214.bin']]
    page.job.point_name, page.job.pole_height = 'P1', '2'
    page.job.bases[0].files, page.job.bases[0].station = [f['ineg2970.26d.Z']], dobles.STATIONS[0]
    page.path = tmp_path/'p.tvppk'
    page.project, page.dirty = page._write(page.path), False
    page.go(3)
    page.process()
    pump(app, lambda: page.worker is None)
    assert ('showerror', ('El proceso no terminó', 'Las estaciones no se traslapan con el rover.')) in app.shown


def test_points_flow_export_and_reopen(app, tmp_path):
    f = files(tmp_path)
    page = app.show('PPK de puntos')
    page.rgna_panel.add([f['ineg2970.26d.Z']])
    pump(app, lambda: page.job.base.station is not None)
    page.next()
    page.add_rover([f['S-20261024-150214.bin'], f['puntos.csv']])   # el CSV se reconoce en la misma zona
    assert page.job.csv_file == f['puntos.csv']
    pump(app, lambda: '14 puntos' in page.csv_info.cget('text'))
    page.next()
    page.next()
    path = tmp_path/'jornada.tvppk'
    page.ask_path = lambda: path
    page.process()
    pump(app, lambda: page.output is not None)
    assert len(page.output['coordinates']) == 14 and page.output['coordinates'][6] is None   # sin solución
    from tkinter import filedialog
    target = tmp_path/'salida.geojson'
    original = filedialog.asksaveasfilename
    filedialog.asksaveasfilename = lambda **k: str(target)
    try:
        page.export('geojson', '.geojson', 'GeoJSON')
        pump(app, lambda: target.exists())
    finally:
        filedialog.asksaveasfilename = original
    assert app.backend.export.calls[0][0] == 'geojson'
    # Abrir de nuevo: el asistente vuelve con los mismos datos y avisa si un archivo cambió.
    f['puntos.csv'].write_bytes(b'otro contenido')
    app.open_project(path)
    pump(app, lambda: any(getattr(c, 'key', None) == 'inputs' for c in page.alerts.winfo_children()))
    assert page.job.base.station.id == 'INEG' and page.job.csv_file == f['puntos.csv'].resolve()
    assert str(path.resolve()) in app.settings.values.recents[0]


def test_points_own_base_and_switch_keeps_kinds_apart(app, tmp_path):
    f = files(tmp_path)
    page = app.show('PPK de puntos')
    page.rgna_panel.add([f['ineg2970.26d.Z']])
    page.kind_var.set('own')
    page.switch_base()
    assert page.job.base.files == [] and page.job.base.station is None
    page.add_own_files([f['S-20261024-150214.bin']])
    page.own_name.set('Base 1')
    page.own_height.set('1.5')
    page.source_var.set('manual')
    page.switch_source()
    for var, value in zip(page.coord_vars, ('21 51 21.6 N', '102 17 2.4 W', '1888')):
        var.set(value)
    from app.wizard import errors
    assert errors(page.job.step_issues(0)) == []
    assert page.job.station().official is False


def test_updates_in_settings(app, tmp_path, monkeypatch):
    from app import updates
    import app.main_window as main_window
    page = app.show('Ajustes')
    release = updates.parse_release({'tag_name': 'v99.0.0', 'body': '- Algo nuevo', 'assets': []})
    page.show_release(release)
    assert '99.0.0' in page.update_status.cget('text')
    opened = []
    monkeypatch.setattr(updates, 'fetch_verified_installer', lambda r, folder, p, c: tmp_path/'instalador.dmg')
    monkeypatch.setattr(main_window, 'open_in_system', lambda path: opened.append(path))
    if updates.installer_name(release.version):
        page.install_release(release)
        pump(app, lambda: opened)
        assert opened == [tmp_path/'instalador.dmg'] and 'verificados' in page.update_progress.message.cget('text')
    page.show_release(updates.parse_release({'tag_name': 'v0.0.1', 'assets': []}))
    assert 'más reciente' in page.update_status.cget('text')
    page.check_updates.set(True)
    page.save()
    assert app.settings.values.check_updates is True


def test_networks_page_lists_network_and_stations(app):
    page = app.show('Redes y estaciones')
    pump(app, lambda: page.networks)
    assert page.networks[0].id == 'rgna-inegi' and page.networks[0].frame_text == 'ITRF2008, época 2010'
    assert len(page.tree.get_children()) == len(dobles.STATIONS)
    page.query.set('zaca')
    assert page.tree.get_children() == ('ZAC1',)
    page.tree.selection_set('ZAC1')
    app.update()
    assert 'LEIAR25.R4' in page.detail_text.cget('text')


def test_networks_adapter_with_engine_api():
    """Con `cors_networks()` del motor (paquetes de redes), el adaptador toma red, marco, época y estaciones."""
    from types import SimpleNamespace
    from app import networks
    net = SimpleNamespace(id='rbmc-ibge', title='RBMC — IBGE, Brasil', countries=['BR'], frame='SIRGAS2000',
                          epoch=2000.4, attribution='Fonte: IBGE', license='CC BY', bundled=False,
                          source={'title': 'IBGE', 'cutoff': '2026'}, download={'url': 'https://ibge.example/rbmc'})
    station = dobles.STATIONS[0]
    core = SimpleNamespace(cors_networks=lambda: [net], station_catalog=lambda nid: [station],
                           station_download_guide=lambda s, a, b: f'guía {s.id}', can_download_station=lambda s: True)
    found = networks.load(core)
    assert found[0].frame_text == 'SIRGAS2000, época 2000.4' and found[0].url == 'https://ibge.example/rbmc'
    assert found[0].origin == 'instalada' and found[0].country == 'BR' and found[0].stations == [station]
    assert networks.guide_text(core, found[0], station, None, None) == 'guía INEG'
    assert networks.can_download(core, station)


def test_catalog_page_sections_search_and_detail(app):
    page = app.show('Sistemas y geoides')
    pump(app, lambda: page.entries)
    page.pick('geoid')
    assert 'Geoides' in page.segment_buttons['geoid'].cget('text')
    page.query.set('ggm25')
    app.update()
    entry = page.selected_entry()
    assert entry is not None and entry.cls == 'geoid' and 'GGM25' in entry.title
    page.pick_state('available')
    app.update()
    assert all(not e.installed or e.update for e in page.visible)


def test_dropping_files_fills_fields_with_their_origin(app, tmp_path):
    f = files(tmp_path)
    page = app.show('Georreferenciar punto')
    page.add_rover([f['S-20261024-150214.bin']])
    pump(app, lambda: page.name_var.get() == 'LF-01')
    assert page.height_var.get() == '1.800'
    assert 'de la sesión del receptor' in page.name_field.origin.cget('text')
    page.name_var.set('Vértice norte')
    assert 'escrito por ti' in page.name_field.origin.cget('text')
    panel = page.base_panels.winfo_children()[0]
    panel.add([f['uagu2970.26o']])
    pump(app, lambda: page.job.bases[0].station is not None and panel.rinex.winfo_ismapped())
    assert page.job.bases[0].station.id == 'UAGU' and 'Del RINEX' in panel.rinex.cget('text')
    assert 'del catálogo' in panel.picker.info.cget('text')
    # Base propia: nombre, altura y coordenada aproximada salen de la grabación; no se pide escribirla.
    points = app.show('PPK de puntos')
    points.kind_var.set('own')
    points.switch_base()
    points.add_own_files([f['S-20261024-150214.bin']])
    pump(app, lambda: points.job.rinex_position is not None)
    assert points.own_height.get() == '1.800' and points.job.own_source == 'rinex'
    assert 'aproximada' in points.from_rinex.cget('text').lower()
    assert points.job.station().coordinate_origin == 'rinex-header' and points.job.station().approximate
    from app.wizard import errors
    assert errors(points.job.step_issues(0)) == []
