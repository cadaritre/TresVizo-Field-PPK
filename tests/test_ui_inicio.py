"""Inicio con proyectos enteros: tamaño, limpiar intermedios, buscar los archivos que faltan al abrir, empaquetar y
abrir un paquete (la interfaz real con los dobles del motor; ventana oculta)."""
import time
import zipfile
from pathlib import Path

import pytest

tk = pytest.importorskip('tkinter')

import dobles  # noqa: E402
from tresvizo_ppk import project as projects  # noqa: E402
from tresvizo_ppk.model import Project  # noqa: E402


def pump(app, until, timeout=20.0):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        app.update()
        if until():
            return True
        time.sleep(0.01)
    raise AssertionError(f'La interfaz no llegó al estado esperado a tiempo: {app.shown}')


@pytest.fixture
def app(tmp_path, monkeypatch):
    from tkinter import messagebox
    shown, asked = [], []
    for name in ('showerror', 'showinfo', 'showwarning'):
        monkeypatch.setattr(messagebox, name, lambda *a, n=name, **k: shown.append((n, a)))
    for name in ('askyesno', 'askokcancel'):
        monkeypatch.setattr(messagebox, name, lambda *a, n=name, **k: asked.append((n, a)) or True)
    monkeypatch.setattr(messagebox, 'askyesnocancel', lambda *a, **k: False)
    try:
        from app.main_window import Application
        application = Application(tmp_path/'conf', backend=dobles.backend())
    except tk.TclError as exc:
        pytest.skip(f'Sin pantalla para Tk: {exc}')
    application.withdraw()
    application.shown, application.asked = shown, asked
    application.settings.save(data_dir=str(tmp_path/'datos'), company='Empresa de prueba')
    yield application
    for page in application.pages.values():
        if getattr(page, 'worker', None) not in (None, 1):
            page.worker.cancel.set()
    application.destroy()


def static_project(tmp_path) -> Path:
    data = tmp_path/'campo'
    data.mkdir()
    rover = data/'uagu2970.26o'
    rover.write_bytes(b'rover ' * 200)
    base = data/'ineg2970.26d.Z'
    base.write_bytes(b'base ' * 300)
    station = dobles.STATIONS[0]
    p = Project(kind='static', point_name='V1', pole_height_m=1.5, bases=[station])
    folder = tmp_path/'proyectos'
    projects.set_inputs(p, {'rover': [rover], 'base:INEG': [base]}, folder)
    path = projects.save(p, folder/'Vertice uno')
    work = projects.workdir(p, path)
    for rel, size in (('rover/UAGU.obs', 5000), ('INEG/INEG.pos.stat', 3000), ('productos/a.atx', 2000),
                      ('Informe Vertice uno.pdf', 400), ('Bitácora del proceso.txt', 60)):
        f = work/rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'x'*size)
    return path


def test_size_column_and_clean_intermediates(app, tmp_path):
    path = static_project(tmp_path)
    app.settings.add_recent(path)
    home = app.show('Inicio')
    key = str(path.resolve())
    pump(app, lambda: home.tree.exists(key) and '…' not in home.tree.set(key, 'size'))
    assert 'KB' in home.tree.set(key, 'size') and 'intermedios' in home.tree.set(key, 'size')
    home.tree.selection_set(key)
    home.update_buttons()
    assert home.more.instate(['!disabled'])
    home.clean_selected()
    pump(app, lambda: not home.busy() and not (path.with_name('Vertice uno.trabajo')/'rover').exists())
    work = path.with_name('Vertice uno.trabajo')
    assert sorted(p.name for p in work.iterdir()) == ['Bitácora del proceso.txt', 'Informe Vertice uno.pdf']
    assert any('Se borran' in a[1][1] for a in app.asked)
    pump(app, lambda: '…' not in home.tree.set(key, 'size') and 'intermedios' not in home.tree.set(key, 'size'))
    assert 'de intermedios borrados' in app.status.get()
    # sin intermedios ya no hay nada que limpiar: lo dice
    home.clean(path)
    assert app.shown[-1][0] == 'showinfo' and 'no tiene intermedios' in app.shown[-1][1][1]


def test_clean_refused_while_the_project_is_processing(app, tmp_path):
    path = static_project(tmp_path)
    page = app.page('Georreferenciar punto')
    page.path, page.worker = path, 1
    try:
        assert app.page('Inicio').ready(path, 'Limpiar intermedios') is False
        assert 'se está procesando' in app.shown[-1][1][1]
    finally:
        page.worker = None


def test_open_with_missing_files_offers_to_find_them(app, tmp_path, monkeypatch):
    path = static_project(tmp_path)
    moved = tmp_path/'disco'/'campo movido'
    moved.parent.mkdir()
    (tmp_path/'campo').rename(moved)
    from tkinter import filedialog
    monkeypatch.setattr(filedialog, 'askdirectory', lambda **k: str(tmp_path/'disco'))
    app.open_project(path)
    assert any('Faltan archivos' in a[1][0] for a in app.asked)
    page = app.page('Georreferenciar punto')
    pump(app, lambda: page.path is not None and page.job.rover_files
         and Path(page.job.rover_files[0]).parent == moved.resolve())
    saved = projects.load(path)
    assert projects.missing_inputs(saved) == [] and projects.check_inputs(saved) == []
    assert 'disco/campo movido' in saved.inputs['rover'][0]
    assert app.current_page == 'Georreferenciar punto'


def test_pack_and_open_package_elsewhere(app, tmp_path, monkeypatch):
    path = static_project(tmp_path)
    app.settings.add_recent(path)
    home = app.show('Inicio')
    from tkinter import filedialog
    target = tmp_path/'entrega'/'Vertice uno.zip'
    target.parent.mkdir()
    monkeypatch.setattr(filedialog, 'asksaveasfilename', lambda **k: str(target))
    home.pack(path)
    pump(app, lambda: not home.busy() and target.exists())
    with zipfile.ZipFile(target) as z:
        names = z.namelist()
    assert 'Vertice uno/datos/rover/uagu2970.26o' in names and 'paquete.json' in names
    assert not any(n.endswith(('.obs', '.stat', '.atx')) for n in names)
    assert 'Paquete listo' in app.status.get()
    destination = tmp_path/'otro equipo'
    destination.mkdir()
    monkeypatch.setattr(filedialog, 'askopenfilename', lambda **k: str(target))
    monkeypatch.setattr(filedialog, 'askdirectory', lambda **k: str(destination))
    home.ask_open_package()
    page = app.page('Georreferenciar punto')
    opened = destination/'Vertice uno'/'Vertice uno.tvppk'
    pump(app, lambda: page.path is not None and Path(page.path) == opened)
    assert Path(page.job.rover_files[0]) == (opened.parent/'datos'/'rover'/'uagu2970.26o').resolve()
    assert str(opened.resolve()) == app.settings.values.recents[0]
