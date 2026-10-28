"""La hoja «Localización» con la ventana real: pares desde un CSV de control, residuos y sospechoso en la tabla,
marcar y quitar pares, importar la de un proyecto de TresVizo Field, aplicarla desde el selector de sistema del
asistente y que el proyecto la guarde."""
from dataclasses import replace
from pathlib import Path

import pytest

tk = pytest.importorskip('tkinter')

from test_ui_widgets import app, pump  # noqa: E402,F401 (el fixture de la ventana)
from test_ui_export import static_page  # noqa: E402
import test_localizacion as T  # noqa: E402
from tresvizo_ppk import project as projects  # noqa: E402
from tresvizo_ppk.crs import localizacion as L  # noqa: E402
from tresvizo_ppk.model import CoordinateChoice  # noqa: E402

from app import localizacion_ui  # noqa: E402


def _csv(tmp_path: Path, blunder=0.0) -> Path:
    pairs = T._site_pairs()
    path = tmp_path/'control.csv'
    path.write_text('\n'.join(f'{p.name},{p.control.northing_m:.4f},{p.control.easting_m + (blunder if i == 3 else 0):.4f},'
                              f'{p.control.elevation_m:.4f},vértice' for i, p in enumerate(pairs)) + '\n',
                    encoding='utf-8')
    return path


def test_la_hoja_con_csv_de_control_residuos_y_sospechoso(app, tmp_path):
    choice = CoordinateChoice('EPSG:32613', '', None, 'WGS 84 / UTM zona 13N')
    dialog = localizacion_ui.LocalizationDialog(app, choice, [p.measured for p in T._site_pairs()])
    app.update()
    assert dialog.title() == 'Localización' and 'disabled' in dialog.apply_button.state()
    dialog.add_control_csv(_csv(tmp_path, blunder=0.080))
    app.update()
    rows = dialog.tree.get_children()
    assert len(rows) == 6
    c4 = dialog.tree.item('3', 'values')
    assert c4[2] == 'C4' and c4[-1] == 'el mayor, y pasa de la tolerancia: el sospechoso'
    assert 'suspect' in dialog.tree.item('3', 'tags')
    params = ' '.join(child.cget('text') for child in dialog.params.winfo_children())
    assert 'Escala' in params and 'Giro (acimut local = de cuadrícula + giro)' in params
    assert dialog.winfo_width() <= 1280 and dialog.winfo_height() <= 800       # cabe en una pantalla chica
    assert 'disabled' not in dialog.apply_button.state()
    # Quitarle la H al malo: queda como comprobación en planta y el ajuste se limpia.
    dialog.tree.selection_set('3')
    dialog.toggle('h')
    app.update()
    assert dialog.tree.item('3', 'values')[0] == '' and dialog.computed.solution.horizontal.pair_count == 5
    assert any(w.code == 'horizontalResidualAboveTolerance' and w.is_check for w in dialog.computed.warnings)
    # Un par escrito a mano sobre un medido de la lista.
    dialog.name_var.set('C9')
    dialog.measured_var.set(dialog.measured_box.cget('values')[0])
    dialog.n_var.set('')
    dialog.e_var.set('')
    dialog.z_var.set('1400.000')
    dialog.add_pair()
    assert dialog.pairs[-1].uses_horizontal is False and dialog.pairs[-1].uses_vertical is True
    dialog.accept()
    action, record = dialog.result
    assert action == 'apply' and record.id and record.applied_at is not None and len(record.pairs) == 7
    new = localizacion_ui.apply_result(app, choice, dialog.result)
    assert new.localization == record and new.localization_history == []
    again = localizacion_ui.apply_result(app, new, ('remove', None))
    assert again.localization is None and again.localization_history[0].id == record.id
    assert again.localization_history[0].removed_at is not None


def test_importa_la_de_un_proyecto_de_tresvizo_field(app, tmp_path):
    path = tmp_path/'Obra.mvz'
    path.write_bytes(T._mvz(T._payload([T._app_record()])))
    dialog = localizacion_ui.LocalizationDialog(app, CoordinateChoice('EPSG:32613', '', 'ggm10'), [])
    dialog.import_project(path)
    app.update()
    assert len(dialog.pairs) == 6 and len(dialog.candidates) >= 6
    assert dialog.keep is not None and dialog.keep.base_grid_crs_code == 'EPSG:32613'
    text = ' '.join(child.label.cget('text') for child in dialog.alerts.winfo_children() if hasattr(child, 'label'))
    assert 'Importada del proyecto «Obra Chihuahua»' in text and 'manda el geoide de ahora' in text
    dialog.accept()
    assert dialog.result[1].origin.startswith('importada de «Obra.mvz»')


def test_desde_el_asistente_se_aplica_y_se_guarda(app, tmp_path, monkeypatch):
    from app.crs_ui import CoordinateChooser
    page, path = static_page(app, tmp_path)

    def find(widget):
        if isinstance(widget, CoordinateChooser):
            return widget
        return next((f for f in map(find, widget.winfo_children()) if f is not None), None)
    chooser = find(page)
    assert chooser.local_row is not None
    assert chooser.local_status.cget('text').startswith('Ninguna')

    def drive(dialog):
        app.update()
        assert [m.source for m in dialog.candidates] == ['estático']     # el vértice del resultado en pantalla
        dialog.name_var.set('LF-01')
        dialog.measured_var.set(dialog.measured_box.cget('values')[0])
        dialog.n_var.set('10000.000')
        dialog.e_var.set('5000.000')
        dialog.z_var.set('1500.000')
        dialog.add_pair()
        dialog.accept()
    monkeypatch.setattr(app, 'wait_window', drive)
    chooser.edit_localization()
    assert page.job.coordinates.localization is not None
    assert chooser.local_status.cget('text').startswith('Local · sobre')
    assert 'disabled' in chooser.system_box.state()                 # el sistema queda fijo con ella
    page.save()
    pump(app, lambda: not page.dirty)
    saved = projects.load(path)
    assert saved.coordinates.localization is not None and saved.coordinates.localization.pairs[0].name == 'LF-01'
    # Y al volver a procesar, el resultado sale en locales.
    page.process()
    pump(app, lambda: page.output is not None)
    c = page.output['coordinate']
    assert c is not None and c.localized and abs(c.northing_m - 10000.0) < 1e-6 and abs(c.easting_m - 5000.0) < 1e-6
