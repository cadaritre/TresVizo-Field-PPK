"""La interfaz real de la antena y la altura (AntennaPanel) con los dobles del motor más las funciones de antenas del
motor de verdad: elegir antena y radomo, altura inclinada con la tabla, importar un .atx y el aviso sin modelo."""
import time
from pathlib import Path

import pytest

tk = pytest.importorskip('tkinter')

import dobles  # noqa: E402
from tresvizo_ppk.core import api as core_api  # noqa: E402


def pump(app, until, timeout=20.0):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        app.update()
        if until():
            return True
        time.sleep(0.01)
    raise AssertionError('La interfaz no llegó al estado esperado a tiempo')


@pytest.fixture
def app(tmp_path, monkeypatch):
    from tkinter import messagebox
    shown = []
    for name in ('showerror', 'showinfo', 'showwarning'):
        monkeypatch.setattr(messagebox, name, lambda *a, n=name, **k: shown.append((n, a)))
    for name, value in {'askyesno': True, 'askokcancel': True, 'askyesnocancel': False}.items():
        monkeypatch.setattr(messagebox, name, lambda *a, v=value, **k: v)
    backend = dobles.backend()
    for name in ('antenna_catalog', 'read_antex', 'slant_dimensions', 'vertical_height', 'antenna_status'):
        monkeypatch.setattr(backend.core, name, getattr(core_api, name), raising=False)
    try:
        from app.main_window import Application
        application = Application(tmp_path/'conf', backend=backend)
    except tk.TclError as exc:
        pytest.skip(f'Sin pantalla para Tk: {exc}')
    application.withdraw()
    application.shown = shown
    application.settings.save(data_dir=str(tmp_path/'datos'), company='Empresa de prueba')
    yield application
    application.destroy()


def recording(tmp_path) -> Path:
    folder = tmp_path/'campo'
    folder.mkdir(exist_ok=True)
    path = folder/'S-20261024-150214.bin'
    path.write_bytes(b'x'*100)
    return path


def test_rover_antenna_and_slant_height(app, tmp_path):
    page = app.show('Georreferenciar punto')
    page.add_rover([recording(tmp_path)])
    pump(app, lambda: page.name_var.get() == 'LF-01')
    panel = page.rover_antenna
    pump(app, lambda: panel.catalog)
    # por omisión, la de la grabación (Meridian: sin modelo) y la altura del bastón con el case
    assert panel.method_var.get() == 'mount' and panel.mount_var.get() == '0.1160'
    sel = page.job.options.antennas['rover']
    assert sel.height_method == 'mount' and sel.measured_m == 1.8 and sel.antenna_type == ''
    assert 'no tiene modelo de centro de fase' in panel.status.cget('text')
    # LEIAR10 de la lista, medida inclinada: r y d de la tabla con su fuente
    panel.type_var.set('LEIAR10 · IGS (igs20_2437)')
    panel.pick_type()
    assert panel.type_var.get() == 'LEIAR10' and 'NONE' in panel.dome_box.cget('values')
    assert 'Modelo LEIAR10 NONE · IGS (igs20_2437)' in panel.status.cget('text')
    panel.method_var.set('slant')
    panel.method_changed()
    assert panel.radius_var.get() == '0.1200' and panel.offset_var.get() == '0.0362'
    page.height_var.set('1.500')
    app.update()
    sel = page.job.options.antennas['rover']
    assert sel.antenna_type == 'LEIAR10' and sel.height_method == 'slant' and sel.measured_m == 1.5
    assert sel.slant_radius_m == 0.12 and sel.slant_offset_m == 0.0362 and 'antenna.gra' in sel.dimensions_source
    assert panel.result_label.cget('text') == 'Altura vertical a la ARP: 1.4590 m'
    # r escrito a mano: la fuente ya no es la tabla
    panel.radius_var.set('0.1250')
    assert page.job.options.antennas['rover'].dimensions_source == 'escritos por el usuario'
    # se guarda en el proyecto y vuelve igual
    project = page.make_project(tmp_path)
    assert project.options.antennas['rover'].slant_radius_m == 0.125


def test_base_antenna_import_and_red_warning(app, tmp_path):
    page = app.show('Georreferenciar punto')
    page.add_rover([recording(tmp_path)])
    pump(app, lambda: page.name_var.get() == 'LF-01')
    page.next()
    app.update()
    zac = next(s for s in dobles.STATIONS if s.id == 'ZAC1')       # a ~110 km del rover de los dobles
    page.use_station(zac)
    app.update()
    panel = page.base_panels.winfo_children()[0].antenna
    pump(app, lambda: panel.catalog)
    assert 'LEIAR25.R4 LEIT' in panel.status.cget('text') and panel.status.cget('style') == 'Card.Subtitle.TLabel'
    # una antena sin modelo en una línea de más de 5 km: aviso rojo
    panel.type_var.set('ZZZSINMODELO')
    panel.pick_type()
    assert panel.status.cget('style') == 'error.TLabel' and 'km' in panel.status.cget('text')
    assert page.job.options.antennas['base:ZAC1'].antenna_type == 'ZZZSINMODELO'
    # importar su calibración: queda en el proyecto, se elige y ya tiene modelo
    from tkinter import filedialog
    block = core_api.antex.receiver_block('LEIAR25.R4', 'LEIT').replace('LEIAR25.R4      LEIT', 'ZZZSINMODELO    NONE')
    atx = tmp_path/'lab.atx'
    atx.write_text('     1.4            M                                       ANTEX VERSION / SYST\n'
                   'A                                                           PCV TYPE / REFANT\n'
                   '                                                            END OF HEADER\n' + block,
                   encoding='latin-1')
    original = filedialog.askopenfilename
    filedialog.askopenfilename = lambda **k: str(atx)
    try:
        panel.import_antex()
    finally:
        filedialog.askopenfilename = original
    pump(app, lambda: not panel._loading)
    assert page.job.options.antex_files == [atx]
    assert 'importado (lab.atx)' in panel.status.cget('text') and panel.status.cget('style') == 'Card.Subtitle.TLabel'
    assert 'lab.atx' in panel.imported.cget('text')
    # un archivo que no es ANTEX se rechaza con el motivo
    bad = tmp_path/'nota.atx'
    bad.write_text('hola\n', encoding='latin-1')
    filedialog.askopenfilename = lambda **k: str(bad)
    try:
        panel.import_antex()
    finally:
        filedialog.askopenfilename = original
    assert app.shown and 'no es un archivo ANTEX' in str(app.shown[-1])
    assert page.job.options.antex_files == [atx]
