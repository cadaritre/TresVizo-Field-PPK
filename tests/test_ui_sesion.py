"""Pantalla «Sesión» con los widgets reales: la línea de tiempo por satélite, arrastrar o escribir el inicio y el fin,
quitar satélites y sistemas, volver a procesar y la comparación con el resultado anterior (también con letra «muy
grande» en una ventana angosta, sin que nada se corte)."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

tk = pytest.importorskip('tkinter')

import dobles  # noqa: E402
from test_ui_widgets import app, pump  # noqa: E402,F401 (el fixture de la ventana)
from test_ui_export import static_page  # noqa: E402
from test_ui_calidad import points_page  # noqa: E402

from tresvizo_ppk import project as projects  # noqa: E402
from tresvizo_ppk.model import ProcessingOptions, Solution, TimeWindow  # noqa: E402

from app import session_ui  # noqa: E402


def editor_of(page):
    page.open_session()
    editor = page._session_editor
    page.app.update()
    return editor


def click(canvas, x, y):
    event = SimpleNamespace(widget=canvas, x=x, y=y)
    return event


def drag(timeline, handle_time, to_time):
    canvas = timeline.body
    x0 = timeline.to_x(handle_time)
    timeline.press(click(canvas, x0, 5))
    assert timeline.drag is not None
    timeline.motion(click(canvas, timeline.to_x(to_time), 5))
    timeline.release(click(canvas, timeline.to_x(to_time), 5))


def row_y(timeline, key):
    _, _, row_h, _ = timeline.metrics()
    index = next(i for i, (_, k) in enumerate(timeline.rows) if k == key)
    return index*row_h+row_h/2


# --- Comparación con valores sintéticos ----------------------------------------------------------------------------

def test_comparison_static_and_points_with_synthetic_values():
    core = dobles.FakeCore()
    rover = SimpleNamespace(marker='LF-01')
    bases = [(s, None) for s in dobles.STATIONS[:2]]
    before = core.process_static(rover, bases, ProcessingOptions(), Path('.'))
    after = core.process_static(rover, bases, ProcessingOptions(excluded_sats=['G13', 'R'],
                                                                time_window=TimeWindow()), Path('.'))
    after.baselines[1] = replace(after.baselines[1], solution=Solution.FLOAT)
    rows = session_ui.compare_static(before, after)
    assert [r.name for r in rows] == ['Vértice LF-01', 'Línea base INEG', 'Línea base UAGU']
    shift = 0.002*2+0.003                                 # el doble: 2 mm por cada cosa fuera y 3 mm por la ventana
    for r in rows:
        de, dn, du = r.delta
        assert abs(de-shift) < 1e-6 and abs(dn) < 1e-6 and abs(du) < 1e-6
    assert rows[0].sigma_before == rows[0].sigma_after
    assert rows[0].solution_before is Solution.FIXED and rows[0].solution_after is Solution.FLOAT   # la peor
    assert rows[2].solution_before is Solution.FIXED and rows[2].solution_after is Solution.FLOAT
    prefs = SimpleNamespace(units='m', decimals=3)
    cells = session_ui.change_cells(rows[2], prefs)
    assert cells[1] == '+0.007 m' and cells[2] in ('+0.000 m', '-0.000 m') and cells[-1] == 'FIJA → FLOTANTE'
    # PPK: por ocupación; las que no tienen posición quedan con «—»
    points = core.read_field_csv(Path(__file__))
    p0 = core.process_points(None, (dobles.STATIONS[0], None), points, ProcessingOptions(), Path('.'))
    p1 = core.process_points(None, (dobles.STATIONS[0], None), points, ProcessingOptions(excluded_sats=['E']),
                             Path('.'))
    changes = session_ui.compare_points(p0, p1)
    assert len(changes) == len(points)
    located = [c for c in changes if c.delta is not None]
    assert located and all(abs(c.delta[0]-0.002) < 1e-6 for c in located)
    missing = [c for c in changes if c.delta is None]
    assert missing and session_ui.change_cells(missing[0], prefs)[1:4] == ('—', '—', '—')


def test_parse_time():
    ref = dobles.START
    assert session_ui.parse_time('2026-10-24 15:10:00', ref) == ref.replace(minute=10)
    assert session_ui.parse_time('15:10', ref) == ref.replace(minute=10)
    assert session_ui.parse_time(' ', ref) is None
    with pytest.raises(Exception, match='No se entiende'):
        session_ui.parse_time('mañana', ref)


# --- Pantalla real: estático ----------------------------------------------------------------------------------------

def test_static_session_editor_trim_exclude_reprocess_and_compare(app, tmp_path):
    page, path = static_page(app, tmp_path)
    before = page.output['result']
    assert page.session_button.winfo_exists() and not page.session_changes()
    editor = editor_of(page)
    timeline = editor.timeline
    # la línea de tiempo: un renglón por sistema y uno por satélite, con los saltos de G13 y E24
    kinds = [k for k, _ in timeline.rows]
    assert kinds.count('system') == 3 and kinds.count('sat') == len(before.rover_quality.satellites)
    assert editor.receiver_box.cget('values') == ('Rover', 'Base INEG')
    span = editor.span
    assert span == (dobles.START, dobles.START+timedelta(hours=1, minutes=43))
    # arrastrar el inicio 10 min y el fin 15 min hacia dentro
    drag(timeline, span[0], span[0]+timedelta(minutes=10))
    drag(timeline, span[1], span[1]-timedelta(minutes=15))
    # al píxel: lo que mide un píxel de la línea de tiempo en segundos
    x0, x1 = timeline.x_range()
    per_px = (span[1]-span[0]).total_seconds()/(x1-x0)
    start = session_ui.parse_time(editor.start_var.get(), None)
    end = session_ui.parse_time(editor.end_var.get(), None)
    assert abs((start-(span[0]+timedelta(minutes=10))).total_seconds()) <= per_px+1
    assert abs((end-(span[1]-timedelta(minutes=15))).total_seconds()) <= per_px+1
    assert timeline.window == (start, end)
    # escribir el inicio y el fin a mano también vale (al segundo)
    editor.start_var.set('2026-10-24 15:12:00')
    editor.end_var.set('16:29:30')
    assert timeline.window == (span[0]+timedelta(minutes=10), dobles.START.replace(hour=16, minute=29, second=30))
    # quitar G13 con un clic en su nombre y BeiDou con un clic en su grupo
    timeline.press(click(timeline.body, 10, row_y(timeline, 'G13')))
    timeline.press(click(timeline.body, 10, row_y(timeline, 'C')))
    assert editor.sats_var.get() == 'G13' and not editor.system_vars['C'].get()
    assert editor.excluded_list() == ['C', 'G13']
    assert 'BeiDou completo; G13' in editor.summary.cget('text')
    assert timeline.label_of('sat', 'C19').startswith('✕') and timeline.label_of('sat', 'G13').startswith('✕')
    # lo ilegible se dice y no se procesa
    editor.sats_var.set('G13 X9')
    calls = len(app.backend.core.calls)
    editor.reprocess()
    assert editor.winfo_exists() and editor.problem.winfo_manager() and '«X9»' in editor.problem.cget('text')
    editor.sats_var.set('G13 e24')
    editor.reprocess()
    pump(app, lambda: page.output is not None and page.output['result'] is not before)
    assert len(app.backend.core.calls) > calls and not editor.winfo_exists()
    used = app.backend.core.last_options
    assert used.excluded_sats == ['C', 'G13', 'E24']
    assert used.time_window == TimeWindow(span[0]+timedelta(minutes=10),
                                          dobles.START.replace(hour=16, minute=29, second=30))
    # se guardó en el proyecto
    saved = projects.load(path)
    assert saved.options.excluded_sats == ['C', 'G13', 'E24'] and saved.options.time_window == used.time_window
    # la comparación en el resultado
    changes = page.session_changes()
    assert [c.name for c in changes] == ['Vértice LF-01', 'Línea base INEG']
    assert abs(changes[0].delta[0]-(0.002*3+0.003)) < 1e-6
    tree = page.session_table.tree
    assert len(tree.get_children()) == 2 and tree.set(tree.get_children()[0], 'de').startswith('+')
    # al reabrir el editor, la ventana y los satélites fuera son los del proyecto, con la comparación
    editor = editor_of(page)
    assert editor.start_var.get() == '2026-10-24 15:12:00' and editor.sats_var.get() == 'G13 E24'
    assert not editor.system_vars['C'].get()
    # «Toda la sesión» y sin satélites fuera vuelve a lo de antes
    editor.whole_session()
    editor.sats_var.set('')
    editor.system_vars['C'].set(True)
    editor.system_toggled('C')
    assert editor.new_window() is None and editor.excluded_list() == []
    editor.reprocess()
    pump(app, lambda: page.output is not None and page.output['result'].excluded_sats == [] and
         app.backend.core.last_options.time_window is None)
    assert abs(page.session_changes()[0].delta[0]+(0.002*3+0.003)) < 1e-6
    assert not app.shown, app.shown


def test_unchanged_session_does_not_reprocess(app, tmp_path):
    page, path = static_page(app, tmp_path)
    editor = editor_of(page)
    calls = len(app.backend.core.calls)
    editor.reprocess()
    app.update()
    assert len(app.backend.core.calls) == calls and editor.winfo_exists() and page.output is not None
    editor.destroy()


# --- Pantalla real: PPK de puntos ------------------------------------------------------------------------------------

def test_points_session_editor_and_comparison(app, tmp_path):
    page, path = points_page(app, tmp_path)
    before = page.output['result']
    editor = editor_of(page)
    timeline = editor.timeline
    editor.sats_var.set('E24')
    drag(timeline, editor.span[0], editor.span[0]+timedelta(minutes=2))
    editor.start_var.set(session_ui.time_text(editor.span[0]+timedelta(minutes=2)))
    editor.reprocess()
    pump(app, lambda: page.output is not None and page.output['result'] is not before)
    used = app.backend.core.last_options
    assert used.excluded_sats == ['E24'] and used.time_window.start == editor.span[0]+timedelta(minutes=2)
    assert used.time_window.end is None                       # la marca del fin quedó en el fin de los datos
    changes = page.session_changes()
    assert len(changes) == len(before.points)
    assert all(abs(c.delta[0]-0.005) < 1e-6 for c in changes if c.delta is not None)
    assert projects.load(path).options.excluded_sats == ['E24']


# --- Letra muy grande en una ventana angosta: nada se corta --------------------------------------------------------

def test_very_large_text_in_a_narrow_window(app, tmp_path):
    from tkinter import font as tkfont
    from app.settings import TEXT_SIZES
    from app.themes import palette
    app.theme.fonts.set_scale(TEXT_SIZES['muy grande'])
    app.theme.apply(palette('Claro'))
    app.deiconify()
    app.geometry('1000x760')
    page, path = static_page(app, tmp_path)
    editor = editor_of(page)
    editor.sats_var.set('G13 E24')
    editor.geometry('520x700')
    for _ in range(30):
        app.update()
    # los controles se envuelven: ninguno se sale del ancho de su fila
    for flow in [w for w in _descendants(editor) if isinstance(w, session_ui.Flow)]:
        width = flow.winfo_width()
        for item in flow.items:
            assert item.winfo_x()+item.winfo_width() <= width+1, (item, item.winfo_x(), item.winfo_width(), width)
            assert item.winfo_width() >= item.winfo_reqwidth()-1
        assert len({row for row in flow.layout}) >= 1
    controls = editor.receiver_box.master.master
    assert len(set(controls.layout)) >= 2                      # a 520 px y letra muy grande no caben en una fila
    # los nombres de la línea de tiempo caben en su columna
    timeline = editor.timeline
    small, strong, row_h, label_w = timeline.metrics()
    for kind, key in timeline.rows:
        text = timeline.label_of(kind, key)
        assert (strong if kind == 'system' else small).measure(text)+(8 if kind == 'system' else 18) <= label_w
    assert row_h >= small.metrics('linespace')+8
    assert timeline.body.winfo_height() >= 4*row_h            # los controles no se comen la línea de tiempo
    # la comparación: cada columna del ancho de su contenido
    editor.reprocess()
    pump(app, lambda: page.session_changes())
    for _ in range(10):
        app.update()
    tree = page.session_table.tree
    body = tkfont.Font(root=tree, font=app.theme.fonts.body())
    for key, _ in session_ui.COMPARISON_COLUMNS:
        for item in tree.get_children():
            assert body.measure(tree.set(item, key)) < tree.column(key, 'width')
    editor = editor_of(page)
    for _ in range(10):
        app.update()
    labels = [w for w in _descendants(editor) if isinstance(w, tk.ttk.Label) and w.cget('text')]
    for label in labels:
        if label.winfo_ismapped():
            assert label.winfo_width() >= min(label.winfo_reqwidth(), editor.winfo_width()-40)-1, label.cget('text')
    editor.destroy()
    assert not app.shown, app.shown


def _descendants(widget):
    stack, out = list(widget.winfo_children()), []
    while stack:
        w = stack.pop()
        out.append(w)
        stack += w.winfo_children()
    return out
