"""Cambiar el sistema, la zona, el geoide o la localización vuelve a convertir el resultado ya calculado (coordenadas,
σ, control de calidad, informe y exportación) sin llamar al motor, y da lo mismo que procesar de cero con ese
sistema. Lo que cambia la solución (opciones del motor, antenas, alturas, bases) sí pide volver a procesar."""
from dataclasses import replace

import pytest

tk = pytest.importorskip('tkinter')

from test_ui_widgets import app, pump  # noqa: E402,F401 (el fixture de la ventana)
from test_ui_export import static_page  # noqa: E402
from test_ui_calidad import points_page  # noqa: E402

from app.crs_ui import CoordinateChooser  # noqa: E402


def chooser_of(page):
    def find(widget):
        if isinstance(widget, CoordinateChooser):
            return widget
        return next((f for f in map(find, widget.winfo_children()) if f is not None), None)
    return find(page)


def choose(page, **changes):
    """Lo que hace el selector al elegir otro sistema, zona o geoide en pantalla."""
    chooser = chooser_of(page)
    chooser.set(replace(chooser.get(), **changes))
    chooser.changed()


def reprocess(app, page):
    page.output = None
    page.process()
    pump(app, lambda: page.output is not None)
    return page.output


def test_estatico_utm_13n_14n_y_localizacion_sin_volver_a_procesar(app, tmp_path, monkeypatch):
    page, path = static_page(app, tmp_path)
    core = app.backend.core
    choose(page, zone='13N')
    assert page.output['coordinate'].zone == '13N'
    first = reprocess(app, page)
    assert first['coordinate'].zone == '13N'
    calls = len(core.calls)
    # A 14N: se convierte, sin motor y sin aviso de «vuelve a procesar».
    choose(page, zone='14N')
    converted = dict(page.output)
    assert len(core.calls) == calls and page.output is not None and page.dirty
    assert converted['choice'].zone == '14N' and converted['coordinate'].zone == '14N'
    assert converted['coordinate'].easting_m != first['coordinate'].easting_m
    assert not any(getattr(c, 'key', None) == 'stale' for c in page.alerts.winfo_children())
    # El informe y la exportación usan el sistema nuevo aunque el proyecto no se haya guardado.
    seen = {}

    def static_report(result, project, user, out):
        seen['report'] = project.coordinates
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b'%PDF-1.4\n')
        return out
    monkeypatch.setattr(app.backend.report, 'static_report', static_report)
    monkeypatch.setattr('app.viewer.DocumentViewer', lambda *a, **k: None)
    page.view_report()
    pump(app, lambda: 'report' in seen)
    assert seen['report'].zone == '14N'
    from tkinter import filedialog
    target = tmp_path/'v.csv'
    monkeypatch.setattr(filedialog, 'asksaveasfilename', lambda **k: str(target))
    page.export('csv', '.csv', 'CSV')
    pump(app, lambda: target.exists())
    assert app.backend.export.calls[-1][2] == converted['choice'].crs_id
    # Lo mismo que procesar de cero con 14N.
    again = reprocess(app, page)
    assert len(core.calls) > calls            # este sí llamó al motor
    assert again['coordinate'] == converted['coordinate'] and again['sigma'] == converted['sigma']
    assert again['choice'] == converted['choice']

    # Una localización: se aplica sobre el resultado en pantalla y se convierte sin motor.
    calls = len(core.calls)

    def drive(dialog):
        app.update()
        dialog.name_var.set('LF-01')
        dialog.measured_var.set(dialog.measured_box.cget('values')[0])
        dialog.n_var.set('10000.000')
        dialog.e_var.set('5000.000')
        dialog.z_var.set('1500.000')
        dialog.add_pair()
        dialog.accept()
    monkeypatch.setattr(app, 'wait_window', drive)
    chooser_of(page).edit_localization()
    local = dict(page.output)
    assert len(core.calls) == calls and local['coordinate'].localized
    assert abs(local['coordinate'].northing_m - 10000.0) < 1e-6 and abs(local['coordinate'].easting_m - 5000.0) < 1e-6
    again = reprocess(app, page)
    assert again['coordinate'] == local['coordinate'] and again['sigma'] == local['sigma']


def test_ppk_cambia_de_zona_y_de_geoide_sin_volver_a_procesar(app, tmp_path):
    page, path = points_page(app, tmp_path)
    core = app.backend.core
    choose(page, zone='13N')
    first = reprocess(app, page)
    calls = len(core.calls)
    choose(page, zone='14N', geoid_id=None)
    converted = dict(page.output)
    assert len(core.calls) == calls
    located = [c for c in converted['coordinates'] if c is not None]
    assert located and all(c.zone == '14N' and c.orthometric_height_m is None for c in located)
    assert [c is None for c in converted['coordinates']] == [c is None for c in first['coordinates']]
    # El control de calidad y la tabla se rehacen con el sistema nuevo.
    assert converted['effective'] and all(c is None or c.zone == '14N' for _, c in converted['effective'])
    again = reprocess(app, page)
    assert again['coordinates'] == converted['coordinates'] and again['choice'] == converted['choice']


def test_lo_que_cambia_la_solucion_si_pide_volver_a_procesar(app, tmp_path):
    page, path = static_page(app, tmp_path)
    from app.wizard_ui import OptionsPanel

    def panel(widget):
        if isinstance(widget, OptionsPanel):
            return widget
        return next((f for f in map(panel, widget.winfo_children()) if f is not None), None)
    options = panel(page)
    # El número y el lugar del informe tampoco obligan a volver a procesar.
    options.number.set('P-2026-17')
    assert page.output is not None and page.job.number == 'P-2026-17'
    options.mask.set('20')
    assert page.output is None
    assert any(getattr(c, 'key', None) == 'stale' for c in page.alerts.winfo_children())
