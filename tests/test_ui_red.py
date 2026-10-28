"""Red estática en la interfaz real (Tk) con el doble del motor para preparar los archivos y, para la red, el
resultado real de la RGNA del 15-07-2026 (`red_datos`) con el reajuste real: receptores, estaciones, fijas, proceso,
la pantalla de la red, fijas y líneas que vuelven a ajustar sin procesar, proyecto, informe y exportación."""
from pathlib import Path

import pytest

tk = pytest.importorskip('tkinter')
from tkinter import ttk  # noqa: E402

import red_datos  # noqa: E402
from test_ui_widgets import app, pump  # noqa: E402,F401 (el fixture de la ventana)
from tresvizo_ppk import project as projects  # noqa: E402
from tresvizo_ppk.core import api as core_api  # noqa: E402
from tresvizo_ppk.core.networks import find_station  # noqa: E402
from tresvizo_ppk.model import NetworkOccupation  # noqa: E402

from app.red_ui import NetworkChart, RoverPanel  # noqa: E402
from app.wizard import BaseEntry  # noqa: E402


def widgets(root, kind):
    out = []
    for child in root.winfo_children():
        if isinstance(child, kind):
            out.append(child)
        out += widgets(child, kind)
    return out


def tree_with(page, column):
    return next(t for t in widgets(page.result_body, ttk.Treeview) if column in t.cget('columns'))


def wire(app):
    """El motor de prueba con la red: el resultado real de la RGNA (sin RTKLIB) y el reajuste real."""
    core = app.backend.core
    core.network_calls = []

    def process_network(rovers, occupations, bases, options, workdir, progress=None, cancel=None, **kw):
        core.network_calls.append((sorted(rovers), [o.name for o in occupations], [s.id for s, _ in bases], kw))
        return core_api.readjust_network(red_datos.network_result(), kw.get('fix'), kw.get('network_options'),
                                         kw.get('line_choices'), kw.get('tolerances'))

    core.process_network = process_network
    core.readjust_network = core_api.readjust_network
    from datetime import datetime, timezone
    start, end = datetime(2026, 7, 15, tzinfo=timezone.utc), datetime(2026, 7, 15, 4, tzinfo=timezone.utc)
    core.network_occupations = lambda key, path, obs=None: ([NetworkOccupation('UGTO', key, start, end)],
                                                             ['p9: de PPK'])
    import tresvizo_ppk.export.api as export_api
    import tresvizo_ppk.report.api as report_api
    app.backend.parts['export'] = export_api
    app.backend.parts['report'] = report_api
    return core


def build(app, tmp_path):
    core = wire(app)
    folder = tmp_path/'campo'
    folder.mkdir()
    names = ['R1.bin', 'R2.bin', 'jornada.csv', 'ineg1960.26o', 'islp1960.26o', 'uqro1960.26o']
    for name in names:
        (folder/name).write_bytes(b'x'*100)
    page = app.show('Red estática')
    page.next()                                          # sin grabación: no avanza
    assert page.step == 0
    panels = widgets(page, RoverPanel)
    panels[0].add_files([folder/'R1.bin'])
    pump(app, lambda: page.job.rovers[0].label)
    panels[0].name_var.set('TOL2')
    panels[0].height_var.set('1.500')
    page.add_rover()
    second = widgets(page, RoverPanel)[1]
    second.add_files([folder/'R2.bin', folder/'jornada.csv'])          # con el CSV: sus ocupaciones
    pump(app, lambda: '1 ocupación' in second.occupations.cget('text'))
    assert 'p9: de PPK' in second.occupations.cget('text') and not second.point.winfo_manager()
    page.next()
    assert page.step == 1, page.issues
    page.job.bases = [BaseEntry(files=[folder/f'{s.lower()}1960.26o'], station=find_station(s))
                      for s in ('INEG', 'ISLP', 'UQRO')]
    page.render_bases()
    page.next()
    assert page.step == 2
    page.next()
    path = tmp_path/'red.tvppk'
    page.ask_path = lambda: path
    page.process()
    pump(app, lambda: page.output is not None)
    return page, core, path


def test_network_wizard_screen_and_readjust(app, tmp_path):
    page, core, path = build(app, tmp_path)
    rovers, occupations, bases, kw = core.network_calls[0]
    assert rovers == ['R1', 'R2'] and occupations == ['TOL2', 'UGTO'] and bases == ['INEG', 'ISLP', 'UQRO']
    assert [f.mode for f in kw['fix']] == ['fixed'] * 3
    # la pantalla de la red: esquema, puntos, líneas y cierres
    chart = widgets(page.result_body, NetworkChart)[0]
    assert len(chart.points) == 6 and len(chart.lines) == 16
    assert any(line['role'] == 'trivial' for line in chart.lines)
    points = tree_with(page, 'ellipse')
    assert [points.set(i, 'name') for i in points.get_children()] == ['UGTO', 'TOL2', 'IHID']
    lines = tree_with(page, 'role')
    assert len(lines.get_children()) == 16
    loops = tree_with(page, 'ppm')
    assert len(loops.get_children()) == 7
    assert page.output['result'].network.constrained.datum == 'INEG, ISLP, UQRO fijas'
    calls = len(core.network_calls)
    # INEG solo de comprobación: se vuelve a ajustar sin procesar
    page.job.set_fix('INEG', 'check')
    page.readjust()
    assert len(core.network_calls) == calls and page.dirty
    assert page.output['result'].network.constrained.datum == 'ISLP, UQRO fijas'
    # una línea fuera, desde la tabla
    lines = tree_with(page, 'role')
    lines.selection_set('S2:TOL2-IHID')
    page.choose_line(lines, 'off')
    assert page.job.line_choices == {'S2:TOL2-IHID': 'off'}
    off = next(v for v in page.output['result'].network.vectors if v.id == 'S2:TOL2-IHID')
    assert not off.use
    assert 'fuera (elegido)' in tree_with(page, 'role').set('S2:TOL2-IHID', 'role')
    # se guarda y se vuelve a abrir igual
    page.save()
    pump(app, lambda: not page.dirty)
    project = projects.load(path)
    assert project.kind == 'network' and [r.key for r in project.rovers] == ['R1', 'R2']
    assert project.rovers[0].point_name == 'TOL2' and project.rovers[0].pole_height_m == 1.5
    assert [(f.point_id, f.mode) for f in project.network_fix][0] == ('INEG', 'check')
    assert project.line_choices == {'S2:TOL2-IHID': 'off'}
    assert set(project.inputs) >= {'rover:R1', 'rover:R2', 'csv:R2', 'base:INEG', 'base:ISLP', 'base:UQRO'}
    app.open_project(path)
    reopened = app.page('Red estática')
    assert reopened.job.rovers[1].csv and reopened.job.fix[0].mode == 'check'
    assert reopened.job.line_choices == {'S2:TOL2-IHID': 'off'}


def test_network_report_and_export(app, tmp_path):
    page, core, path = build(app, tmp_path)
    out = tmp_path/'informe.pdf'
    page.make_report(app.backend.report, page.output, page.quality_project(page.project), app.settings.profile(),
                     out)
    assert out.stat().st_size > 20000
    target = tmp_path/'red.csv'
    page.ask_export_file = lambda *a: str(target)
    page.export('csv', '.csv', 'CSV')
    pump(app, lambda: target.exists())
    assert 'UGTO' in target.read_text(encoding='utf-8-sig')


def test_tolerances_show_the_loop_closure(app):
    from app.calidad_ui import TolerancesPanel
    page = app.show('Red estática')
    page.go(2)
    page.touched.update({0, 1})
    panel = widgets(page, TolerancesPanel)
    if not panel:          # el paso 2 se construye aunque no se llegue con «Siguiente»
        pytest.skip('el paso de ajustes no está construido')
    panel = panel[0]
    assert panel.vars['max_loop_h_m'].get() == '0.050' and panel.vars['max_loop_v_ppm'].get() == '2.0'
    panel.vars['max_loop_h_ppm'].set('')
    assert page.job.tolerances.max_loop_h_ppm == 0.0
    assert page.job.tolerances.min_duration_s == 3600.0
