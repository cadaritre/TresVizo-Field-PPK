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
    page = app.show('Estaciones de referencia')
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


def test_nearby_stations_with_distance_and_network_notes(app, tmp_path, monkeypatch):
    """Paso de las bases: las estaciones más cercanas al rover con su distancia y los avisos de su red; «Usar» la pone
    en la primera ficha libre. El aviso también sale al elegir la estación y en «Estaciones de referencia»."""
    f = files(tmp_path)
    core = app.backend.core
    ineg, uagu = dobles.STATIONS[0], dobles.STATIONS[1]
    monkeypatch.setattr(core, 'nearest_stations', lambda position, count=3: [(uagu, 7_512.0), (ineg, 12_340.0)],
                        raising=False)
    monkeypatch.setattr(core, 'station_notes', lambda s: ['Hundimiento del terreno.'] if s.id == 'INEG' else [],
                        raising=False)
    page = app.show('Georreferenciar punto')
    page.add_rover([f['S-20261024-150214.bin']])
    pump(app, lambda: page.name_var.get() == 'LF-01')
    assert 'Posición aproximada' in page.rover_info.cget('text')
    page.next()
    app.update()

    def texts():
        out, stack = [], list(page.nearby.winfo_children())
        while stack:
            w = stack.pop()
            stack += w.winfo_children()
            try:
                out.append(str(w.cget('text')))
            except tk.TclError:
                pass
        return out
    assert '7.5 km' in texts() and '12.3 km' in texts()
    assert any('Aviso de la red: Hundimiento' in t for t in texts())
    page.use_station(uagu)
    assert page.job.bases[0].station.id == 'UAGU' and texts().count('En uso') == 1
    page.use_station(ineg)
    assert [b.station.id for b in page.job.bases] == ['UAGU', 'INEG']
    picker = page.base_panels.winfo_children()[1].picker
    assert 'Hundimiento' in picker.notes.cget('text') and picker.notes.winfo_manager()
    networks = app.show('Estaciones de referencia')
    pump(app, lambda: networks.networks)
    networks.tree.selection_set('INEG')
    app.update()
    assert 'Hundimiento' in networks.detail_notes.cget('text')


def test_table_columns_stay_in_view(app):
    """Sin barra horizontal, una tabla más ancha que su espacio reparte el ancho: ninguna columna queda fuera."""
    from app.components import fit_columns
    window = tk.Toplevel(app)
    tree = tk.ttk.Treeview(window, columns=[f'c{i}' for i in range(8)], show='headings')
    for i in range(8):
        tree.heading(f'c{i}', text=f'Columna larga {i}')
    tree.insert('', 'end', values=['valor bastante largo']*8)
    tree.place(x=0, y=0, width=600, height=200)
    fit_columns(tree, app, stretch='c0')
    window.update()
    tree.event_generate('<Configure>')
    window.update()
    assert sum(tree.column(f'c{i}', 'width') for i in range(8)) <= 600
    window.destroy()


def test_own_base_from_rinex_reads_arp_height_and_candidates_after_network_use(app, tmp_path):
    """Los mismos RINEX ya leídos como estación de una red se vuelven a leer con sus candidatas al pasarlos a base
    propia, y la altura de un RINEX es a la ARP (la del encabezado), no del bastón."""
    f = files(tmp_path)
    page = app.show('PPK de puntos')
    page.rgna_panel.add([f['uagu2970.26o']])
    pump(app, lambda: page.job.base.station is not None and page.rgna_panel.rinex.winfo_ismapped())
    page.kind_var.set('own')
    page.switch_base()
    page.add_own_files([f['uagu2970.26o']])
    pump(app, lambda: page.job.candidates)
    assert page.job.candidates[0].id == 'UAGU'
    assert page.own_height_field.title.cget('text') == 'Altura de la antena a la ARP (m)'
    assert page.own_height.get() == '0.0083'
    assert 'oficial (catálogo de su red)' in '\n'.join(page.summary_lines())


def test_sidebar_fits_every_name_from_the_start(app):
    """La barra lateral mide sus botones ya armados: «Estaciones de referencia» no se corta al abrir."""
    app.geometry('1280x820')
    end = time.monotonic()+1.0
    while time.monotonic() < end:
        app.update()
        time.sleep(0.01)
    app.show('Estaciones de referencia')            # el activo va en negrita y es el nombre más largo
    app.update()
    button = app.nav_buttons['Estaciones de referencia']
    assert int(app.nav.cget('width')) >= button.winfo_reqwidth()+20


def test_cleared_alert_leaves_no_gap(app):
    """Al quitar el último aviso, su espacio se cierra (Tk no encoge solo un marco vacío)."""
    page = app.show('Georreferenciar punto')
    page.show_alert('info', 'Procesado por última vez…', key='processed')
    app.update()
    assert page.alerts.winfo_height() > 20
    page.clear_alert('processed')
    app.update()
    assert page.alerts.winfo_height() <= 2


def static_ready(app, tmp_path, page, name='P1'):
    """Un proyecto estático completo y guardado, listo para procesar."""
    f = files(tmp_path)
    page.job.rover_files = [f['S-20261024-150214.bin']]
    page.job.point_name, page.job.pole_height = name, '2'
    page.job.bases[0].files, page.job.bases[0].station = [f['ineg2970.26d.Z']], dobles.STATIONS[0]
    path = tmp_path/f'{name}.tvppk'
    page.path = path
    page.project, page.dirty = page._write(path), False
    page.build_steps()
    page.go(3)
    return path


def test_reopen_while_processing_asks_cancels_and_drops_the_old_result(app, tmp_path, monkeypatch):
    """Abrir un proyecto (el mismo u otro) mientras se procesa: se pregunta, se cancela, se espera y el resultado viejo
    nunca cae en el proyecto nuevo. Antes: TclError «invalid command name …!progresspanel.!progressbar»."""
    from tkinter import messagebox
    app.backend.parts['core'] = dobles.FakeCore(delay=0.15)
    page = app.show('Georreferenciar punto')
    path = static_ready(app, tmp_path, page)
    asked = []
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a, **k: asked.append(a[:2]) or False)
    page.process()
    pump(app, lambda: page.progress.bar['value'] > 0)
    old = page.worker
    app.open_project(path)                        # «No»: el proceso sigue y el proyecto no se toca
    assert asked and asked[-1][0] == 'Hay un proceso en marcha' and page.worker is old and not old.cancel.is_set()
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a, **k: asked.append(a[:2]) or True)
    app.open_project(path)                        # «Sí»: se cancela, se espera y se abre
    assert old.cancel.is_set() and old.finished and page.worker is None
    assert not old.thread.is_alive() and page.step == 0 and not page.dirty
    end = time.monotonic()+1.0
    while time.monotonic() < end:
        app.update()
        time.sleep(0.01)
    assert page.output is None and not app.shown, app.shown
    # Procesa de nuevo, abre otro proyecto en medio y luego el primero: nada viejo aparece.
    (tmp_path/'otro').mkdir()
    other = static_ready(app, tmp_path/'otro', page, 'P2')
    page.process()
    pump(app, lambda: page.progress.bar['value'] > 0)
    app.open_project(path)
    app.open_project(other)
    page.process()
    pump(app, lambda: page.output is not None)
    assert page.output['result'].point_name == 'P2' and page.path == other and not app.shown, app.shown


def test_reload_cancels_pending_file_reads_and_uses_its_own_temporary_folder(app, tmp_path):
    """Al recargar, las lecturas de archivos en curso se cancelan y su resultado se descarta; cada lectura usa su
    propia carpeta temporal (antes compartían una que la primera borraba al terminar)."""
    app.backend.parts['core'] = dobles.FakeCore(delay=0.1)
    page = app.show('Georreferenciar punto')
    path = static_ready(app, tmp_path, page)
    pending = dict(page._inspecting)
    assert pending                                 # el rover y la estación se están leyendo
    app.open_project(path)
    assert all(w.cancel.is_set() for _, w in pending.values())
    pump(app, lambda: all(w.finished for _, w in pending.values()))
    end = time.monotonic()+0.8
    while time.monotonic() < end:
        app.update()
        time.sleep(0.01)
    assert not page.dirty and not app.shown, app.shown
    temporal = app.settings.data_dir()/'Temporal'
    assert not temporal.exists() or not any(temporal.iterdir())
