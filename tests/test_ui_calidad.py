"""El control de calidad en la interfaz real (widgets de Tk) con los dobles del motor: tolerancias en las opciones del
asistente (sin volver a procesar), el estado escrito con su símbolo en la tabla, las ocupaciones repetidas con la
elegida, el control importado de un CSV y todo guardado en el proyecto."""
from pathlib import Path

import pytest

tk = pytest.importorskip('tkinter')
from tkinter import ttk  # noqa: E402

import dobles  # noqa: E402
from test_ui_widgets import app, files, pump  # noqa: E402,F401 (el fixture de la ventana)
from tresvizo_ppk import project as projects  # noqa: E402

from app.calidad_ui import ControlPanel, TolerancesPanel  # noqa: E402


def widgets(root, kind):
    out = []
    for child in root.winfo_children():
        if isinstance(child, kind):
            out.append(child)
        out += widgets(child, kind)
    return out


def status_tree(page):
    return next(t for t in widgets(page.result_body, ttk.Treeview) if 'status' in t.cget('columns')
                and 'delta' in t.cget('columns'))


def column(tree, key):
    return [tree.set(i, key) for i in tree.get_children()]


def points_page(app, tmp_path, repeat=False):
    f = files(tmp_path)
    if repeat:
        core = app.backend.core
        original = core.read_field_csv

        def read(path):
            points = original(path)
            points[1].name = 'p01'             # la misma marca, ocupada otra vez (sin distinguir mayúsculas)
            return points
        core.read_field_csv = read
    page = app.show('PPK de puntos')
    page.rgna_panel.add([f['ineg2970.26d.Z']])
    pump(app, lambda: page.job.base.station is not None)
    page.next()
    page.add_rover([f['S-20261024-150214.bin'], f['puntos.csv']])
    pump(app, lambda: '14 puntos' in page.csv_info.cget('text'))
    page.next()
    page.next()
    path = tmp_path/'jornada.tvppk'
    page.ask_path = lambda: path
    page.process()
    pump(app, lambda: page.output is not None)
    return page, path


def test_status_column_and_tolerances_without_reprocessing(app, tmp_path):
    page, path = points_page(app, tmp_path)
    tree = status_tree(page)
    states = column(tree, 'status')
    # el doble: fijos σ (0.006, 0.007, 0.015), flotantes σE 0.016 sin épocas fijas, y sin solución
    assert states[0] == '✓ Pasa' and states[3] == '✕ Falla' and states[6] == '✕ Falla'
    assert 'q-fail' in tree.item('3', 'tags') and not tree.item('0', 'tags')
    tree.selection_set('3')
    app.update()
    detail = next(w for w in widgets(page.result_body, ttk.Label) if 'épocas fijas de' in str(w.cget('text')))
    assert '✕ Falla (fijas 0.0 % < 95.0 %' in detail.cget('text')
    calls = len(app.backend.core.calls)
    # σV máxima 0.012: los fijos (σV 0.015) quedan a revisar, sin volver a procesar
    page.go(2)
    panel = widgets(page, TolerancesPanel)[0]
    panel.vars['max_sigma_v_m'].set('0,012')
    assert page.output is not None and page.dirty and page.job.tolerances.max_sigma_v_m == 0.012
    assert panel.order.get() == 'Escritas a mano' and page.job.tolerances.order == ''
    assert column(status_tree(page), 'status')[0] == '▲ Revisar'
    assert page.output['result'].quality.points[0].status.reason == 'σV 0.015 > 0.012'
    # un orden de la NT EEP llena todas las cifras
    panel.order.set('Orden 1 decímetro')
    panel.pick_order()
    t = page.job.tolerances
    assert (t.order, t.max_sigma_h_m, t.max_sigma_v_m, t.max_control_h_m) == ('1 decímetro', 0.057, 0.102, 0.1)
    assert panel.vars['max_sigma_h_m'].get() == '0.057'
    # vacío: el criterio no se evalúa; una cifra que no se lee se marca en el paso
    panel.vars['max_pdop'].set('')
    assert page.job.tolerances.max_pdop is None
    panel.vars['min_ratio'].set('abc')
    assert any(i.field == 'tolerances' for i in page.job.step_issues(2))
    panel.vars['min_ratio'].set('3')
    assert len(app.backend.core.calls) == calls
    # se guarda en el proyecto y vuelve al abrirlo
    page.save()
    pump(app, lambda: not page.dirty)
    saved = projects.load(path)
    assert saved.tolerances.order == '' and saved.tolerances.max_pdop is None and saved.tolerances.max_sigma_h_m == 0.057
    app.open_project(path)
    assert page.job.tolerances == saved.tolerances


def test_repeated_occupation_mean_or_chosen(app, tmp_path):
    page, path = points_page(app, tmp_path, repeat=True)
    quality = page.output['result'].quality
    assert len(quality.points) == 13 and len(quality.groups) == 1
    tree = status_tree(page)
    assert len(tree.get_children()) == 13
    # P01 y p01 están a 7.5 m: la media se marca y la diferencia falla
    assert quality.points[0].note == 'promedio de 2 ocupaciones' and quality.points[0].status.level == 'fail'
    assert 'entre ocupaciones ΔH' in quality.points[0].status.reason
    card = [w for w in widgets(page.result_body, ttk.Combobox)]
    assert card and card[0].get().startswith('Media ponderada de 2')
    page.choose_occupation('P01', 2)
    quality = page.output['result'].quality
    assert quality.points[0].note == 'ocupación 2 de 2 (elegida)' and page.job.occupation_choices == {'P01': 2}
    # el informe recibe el proyecto con la ocupación elegida aunque no se haya guardado
    seen = []
    report = app.backend.report
    original = report.points_report
    report.points_report = lambda result, project, user, out: (seen.append(project), original(result, project, user,
                                                                                              out))[1]
    page.view_report()
    pump(app, lambda: seen)
    assert seen[0].occupation_choices == {'P01': 2}
    for window in [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]:
        window.destroy()
    page.save()
    pump(app, lambda: not page.dirty)
    assert projects.load(path).occupation_choices == {'P01': 2}


def test_control_import_and_residuals(app, tmp_path):
    page, path = points_page(app, tmp_path)
    pq, c = page.output['effective'][2]
    csv = tmp_path/'control.csv'
    csv.write_text(f'{pq.name},{c.northing_m + 0.010:.3f},{c.easting_m - 0.020:.3f},\nC-99,1,2,3\n',
                   encoding='utf-8')
    page.go(2)
    panel = widgets(page, ControlPanel)[0]
    panel.import_file(csv)
    assert page.output is not None and page.job.control_source == 'control.csv'
    assert '2 puntos de control de «control.csv»: 2 con norte y este, 1 con cota.' == panel.summary.cget('text')
    quality = page.output['result'].quality
    r = quality.control[0]
    assert abs(r.delta_n_m - (c.northing_m - round(c.northing_m + 0.010, 3))) < 1e-9
    assert quality.control_missing == ['C-99']
    trees = [t for t in widgets(page.result_body, ttk.Treeview) if 'dv' in t.cget('columns')]
    assert trees and len(trees[0].get_children()) == 1
    # un archivo que no sirve se dice y no cambia el control
    bad = tmp_path/'malo.csv'
    bad.write_text('C1,abc,2,3\n', encoding='utf-8')
    panel.import_file(bad)
    assert app.shown and page.job.control_source == 'control.csv'
    page.save()
    pump(app, lambda: not page.dirty)
    saved = projects.load(path)
    assert [p.name for p in saved.control_points] == [pq.name, 'C-99'] and saved.control_source == 'control.csv'
    panel.remove()
    assert page.job.control_points == [] and 'Sin puntos de control.' == panel.summary.cget('text')


def test_static_baseline_status_and_point(app, tmp_path):
    f = files(tmp_path)
    page = app.show('Georreferenciar punto')
    page.add_rover([f['S-20261024-150214.bin']])
    page.name_var.set('LF-01')
    page.height_var.set('1.8')
    page.job.bases[0].files = [f['ineg2970.26d.Z']]
    page.job.bases[0].station = dobles.STATIONS[0]
    page.ask_path = lambda: tmp_path/'LF-01.tvppk'
    page.go(3)
    page.process()
    pump(app, lambda: page.output is not None)
    quality = page.output['result'].quality
    assert quality is not None and quality.points[0].name == 'LF-01'
    trees = [t for t in widgets(page.result_body, ttk.Treeview) if 'status' in t.cget('columns')]
    assert trees and all(v.split(' ')[1] in ('Pasa', 'Revisar', 'Falla') for v in column(trees[0], 'status'))
    labels = [str(w.cget('text')) for w in widgets(page.result_body, ttk.Label)]
    assert any(text.startswith('Tolerancias del proyecto: ') for text in labels)
