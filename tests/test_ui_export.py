"""Exportar desde la interfaz: el vértice del estático en los seis formatos, «Opciones de CSV» (los textos de las apps),
la trayectoria y el RINEX de la sesión del PPK, con los dobles del motor y de la exportación."""
from pathlib import Path

import pytest

tk = pytest.importorskip('tkinter')

import dobles  # noqa: E402
from test_ui_widgets import app, files, pump  # noqa: E402,F401 (el fixture de la ventana)
from tresvizo_ppk import project as projects  # noqa: E402

from app import export_ui  # noqa: E402


def static_page(app, tmp_path):
    f = files(tmp_path)
    page = app.show('Georreferenciar punto')
    page.add_rover([f['S-20261024-150214.bin']])
    page.name_var.set('LF-01')
    page.height_var.set('1.8')
    page.code_var.set('BN')
    page.description_var.set('Mojón de concreto, «norte»')
    page.job.bases[0].files = [f['ineg2970.26d.Z']]
    page.job.bases[0].station = dobles.STATIONS[0]
    path = tmp_path/'LF-01.tvppk'
    page.ask_path = lambda: path
    page.go(3)
    page.process()
    pump(app, lambda: page.output is not None)
    return page, path


def save_as(monkeypatch, target):
    from tkinter import filedialog
    asked = []
    monkeypatch.setattr(filedialog, 'asksaveasfilename', lambda **k: asked.append(k) or str(target))
    return asked


def menu_labels(button):
    menu = button.nametowidget(button.cget('menu'))
    return [menu.entrycget(i, 'label') for i in range(menu.index('end')+1) if menu.type(i) == 'command']


def test_estatico_codigo_y_descripcion_en_el_proyecto(app, tmp_path):
    page, path = static_page(app, tmp_path)
    saved = projects.load(path)
    assert saved.point_code == 'BN' and saved.point_description == 'Mojón de concreto, «norte»'
    page.code_var.set('X'*41)
    assert any(i.field == 'point_code' for i in page.job.step_issues(0))
    page.code_var.set('BN')
    app.open_project(path)
    assert page.job.point_code == 'BN' and page.code_var.get() == 'BN'


def test_estatico_exporta_los_seis_formatos_y_el_rinex(app, tmp_path, monkeypatch):
    page, path = static_page(app, tmp_path)
    labels = menu_labels(page.export_button(page))
    assert labels[:2] == ['Copiar coordenadas', 'Guardar informe PDF…']
    assert [label for _, label, _, _ in export_ui.POINT_FORMATS] == labels[2:8]
    assert labels[8:] == [export_ui.CUSTOM_CSV_LABEL, export_ui.RINEX_LABEL]
    export = app.backend.export
    for formato, _, suffix, kind in export_ui.POINT_FORMATS:
        target = tmp_path/'salida'/f'v-{formato}{suffix}'
        target.parent.mkdir(exist_ok=True)
        asked = save_as(monkeypatch, target)
        page.export(formato, suffix, kind)
        pump(app, lambda: target.exists())
        assert asked[-1]['initialfile'] == f'LF-01{suffix}' and asked[-1]['title'] == 'Exportar vértice'
        call = export.calls[-1]
        assert call[:2] == ('static', formato) and call[4]['code'] == 'BN'
        assert call[4]['description'] == 'Mojón de concreto, «norte»'
        assert ('project' in call[4]) == (formato in ('dxf', 'landxml'))
    pump(app, lambda: 'Vértice exportado' in app.status.get())
    from tkinter import filedialog
    folder = tmp_path/'entrega'
    monkeypatch.setattr(filedialog, 'askdirectory', lambda **k: str(folder))
    page.export_rinex()
    pump(app, lambda: (folder/'SESION.26O').exists())
    assert export.calls[-1] == ('rinex', 'entrega')
    assert not app.shown


def test_csv_a_medida_desde_el_estatico_recuerda_las_opciones(app, tmp_path, monkeypatch):
    page, _ = static_page(app, tmp_path)
    target = tmp_path/'pnezd.csv'
    save_as(monkeypatch, target)

    def choose(dialog):
        dialog.layout.set('pnezd')
        dialog.separator.set(';')
        dialog.refresh()
        dialog.decimal_comma.set(True)
        dialog.no_header.set(True)
        dialog.language.set('es')
        dialog.accept()
    monkeypatch.setattr(app, 'wait_window', choose)
    page.export_custom_csv()
    pump(app, lambda: target.exists())
    options = app.backend.export.calls[-1][4]['csv_options']
    assert options == {'layout': 'pnezd', 'separator': ';', 'language': 'es', 'header': False, 'bom': True,
                       'decimal_comma': True, 'custom': ()}
    remembered = app.settings.values.csv_export
    assert remembered['layout'] == 'pnezd' and remembered['header'] is False
    assert export_ui.summary(export_ui.options_from_prefs(remembered)) == 'PNEZD · punto y coma · sin encabezado'
    # Cancelar no exporta nada
    count = len(app.backend.export.calls)
    monkeypatch.setattr(app, 'wait_window', lambda dialog: dialog.destroy())
    page.export_custom_csv()
    assert len(app.backend.export.calls) == count


def test_dialogo_de_csv_personalizada(app):
    dialog = export_ui.CsvOptionsDialog(app, [('ppkSolution', 'PPK solution', 'Solución PPK')],
                                        export_ui.options_from_prefs({}))
    app.update()
    assert dialog.title() == 'Opciones de CSV'
    assert str(dialog.header_check.cget('state')) == 'disabled' or 'disabled' in dialog.header_check.state()
    assert 'disabled' in dialog.comma_check.state()          # con coma de separador no hay coma decimal
    dialog.layout.set('custom')
    dialog.refresh()
    assert dialog.columns_frame.winfo_manager() == 'pack'
    assert len(dialog.tree.get_children()) == 94+1
    dialog.select_all()
    for index in range(len(dialog.rows)):          # todas apagadas: no deja exportar
        dialog.rows[index][1] = False
    dialog.accept()
    assert dialog.result is None and dialog.error.cget('text') == 'Elige al menos una columna.'
    dialog.reset()
    dialog.tree.selection_set('1')                 # «Code»
    app.update()
    dialog.rename.set('Código Civil 3D')
    assert dialog.rename_note.cget('text') == 'Vacío vuelve a Code.'
    dialog.move(-1)                                # sube por encima de «Name»
    dialog.tree.selection_set('2')                 # «Code description»: se apaga
    app.update()
    dialog.toggle()
    dialog.accept()
    custom = dialog.result['custom']
    assert custom[:3] == [['code', 'Código Civil 3D'], ['name', ''], ['easting', '']]
    assert len(custom) == 94 and ['ppkSolution', ''] in custom


def test_opciones_guardadas_raras_vuelven_a_las_de_omision():
    options = export_ui.options_from_prefs({'layout': 'xyz', 'separator': '|', 'language': 'fr', 'header': 'no',
                                            'custom': [['name', 'N'], 'basura', ['solo']]})
    assert options == {**export_ui.DEFAULTS, 'custom': [['name', 'N']]}
    assert export_ui.summary(export_ui.DEFAULTS) == 'Todas las columnas · coma · inglés'
    rows = export_ui.custom_rows({**export_ui.DEFAULTS, 'custom': [['easting', 'E'], ['nada', ''], ['easting', '']]},
                                 [])
    assert rows[0] == ['easting', True, 'E'] and len(rows) == 94 and not rows[1][1]


def test_ppk_exporta_cad_trayectoria_y_rinex(app, tmp_path, monkeypatch):
    f = files(tmp_path)
    page = app.show('PPK de puntos')
    page.rgna_panel.add([f['ineg2970.26d.Z']])
    pump(app, lambda: page.job.base.station is not None)
    page.next()
    page.add_rover([f['S-20261024-150214.bin'], f['puntos.csv']])
    pump(app, lambda: '14 puntos' in page.csv_info.cget('text'))
    page.next()
    page.next()
    page.ask_path = lambda: tmp_path/'jornada.tvppk'
    page.process()
    pump(app, lambda: page.output is not None)
    labels = menu_labels(page.export_button(page))
    assert 'DXF R12 (AutoCAD, con su .prj)…' in labels and 'LandXML 1.2 (Civil 3D)…' in labels
    assert labels[-3:] == ['Exportar trayectoria (.pos de RTKLIB)…', 'Exportar trayectoria (CSV)…',
                           export_ui.RINEX_LABEL]
    export = app.backend.export
    for formato, suffix in (('dxf', '.dxf'), ('landxml', '.xml')):
        target = tmp_path/f'p{suffix}'
        save_as(monkeypatch, target)
        page.export(formato, suffix, formato)
        pump(app, lambda: target.exists())
        assert export.calls[-1][0] == formato and export.calls[-1][3] == {'project': 'jornada'}
    for formato, suffix in (('pos', '.pos'), ('csv', '.csv')):
        target = tmp_path/f'trayectoria{suffix}'
        asked = save_as(monkeypatch, target)
        page.export_trajectory(formato)
        pump(app, lambda: target.exists())
        assert export.calls[-1] == ('trajectory', formato, target.name)
        assert asked[-1]['initialfile'] == f'jornada trayectoria{suffix}'
    target = tmp_path/'medida.csv'
    save_as(monkeypatch, target)
    monkeypatch.setattr(app, 'wait_window', lambda dialog: (dialog.layout.set('penzd'), dialog.accept()))
    page.export_custom_csv()
    pump(app, lambda: target.exists())
    assert export.calls[-1][3]['csv_options']['layout'] == 'penzd'
    from tkinter import filedialog
    monkeypatch.setattr(filedialog, 'askdirectory', lambda **k: str(tmp_path/'rinex'))
    page.export_rinex()
    pump(app, lambda: (tmp_path/'rinex'/'SESION.26O').exists())
    assert not app.shown
