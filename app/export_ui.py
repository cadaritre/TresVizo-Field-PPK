"""Exportar desde el resultado (estático y PPK): el menú, los diálogos de archivo y carpeta, «Opciones de CSV» y
«Opciones de DXF».

«Opciones de CSV» lleva las mismas opciones y los mismos textos que la hoja de las apps TresVizo Field
(`PointCSV.Text` de iOS, `strings_csv.xml` de Android): disposición, columnas de «Personalizada», separador, coma
decimal, encabezados y codificación.

«Opciones de DXF»: «Nombres de los puntos» con el texto de la hoja «Exportar» de las apps
(`SurveyExport.Text.dxfPointNames` y su detalle); la altura del texto, que en las apps es la opción
`dxfTextHeightMeters` del motor (0.5 m, sin control en su hoja), y el rótulo de la cota, que añade la oficina
(`tresvizo_ppk/export/dxf.py`). Lo elegido en los dos diálogos se recuerda en las preferencias.
"""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from tresvizo_ppk.export import columnas
from tresvizo_ppk.export.dxf import DxfOptions

from app import tasks

# Los formatos de puntos del menú «Exportar»: (formato, texto del menú, extensión, nombre del tipo de archivo).
POINT_FORMATS = [
    ('field_csv', 'CSV de TresVizo Field…', '.csv', 'CSV de TresVizo Field'),
    ('csv', 'CSV simple (nombre, X, Y, Z, código)…', '.csv', 'CSV'),
    ('geojson', 'GeoJSON…', '.geojson', 'GeoJSON'),
    ('kml', 'KML (Google Earth)…', '.kml', 'KML'),
    ('dxf', 'DXF R12 (AutoCAD, con su .prj)…', '.dxf', 'DXF'),
    ('landxml', 'LandXML 1.2 (Civil 3D)…', '.xml', 'LandXML'),
]
CUSTOM_CSV_LABEL = 'CSV a medida (PNEZD, PENZD…)…'
RINEX_LABEL = 'Exportar RINEX de la sesión…'

LAYOUTS = [
    ('all', 'Todas las columnas', 'Las de Emlid Flow en su orden, y después las de TresVizo Field.'),
    ('penzd', 'PENZD', 'Nombre, Este, Norte, Cota, Descripción'),
    ('pnezd', 'PNEZD', 'Nombre, Norte, Este, Cota, Descripción'),
    ('nxyzd', 'NXYZD', 'Nombre, X (Este), Y (Norte), Z (Cota), Descripción'),
    ('custom', 'Personalizada', 'Elige, ordena y renombra las columnas.'),
]
SHORT_LAYOUTS = ('penzd', 'pnezd', 'nxyzd')
SEPARATORS = [(',', 'Coma'), (';', 'Punto y coma'), ('\t', 'Tabulador')]
LANGUAGES = [('en', 'Inglés (Emlid)'), ('es', 'Español')]
TEXT = {
    'title': 'Opciones de CSV', 'layout': 'Disposición', 'columns': 'Columnas', 'select_all': 'Seleccionar todas',
    'reset': 'Restablecer', 'rename': 'Nombre de la columna', 'separator': 'Separador',
    'separator_footer': 'El separador decimal siempre es el punto.', 'decimal_comma': 'Coma decimal',
    'decimal_comma_footer': 'Los números salen con coma decimal, como los lee Excel en países que la usan.',
    'header': 'Encabezados',
    'header_footer': 'En inglés, el archivo entra sin cambios en Civil 3D, Global Mapper, QGIS o Emlid Studio.',
    'no_header': 'Sin encabezado', 'encoding': 'Codificación', 'with_bom': 'UTF-8 con BOM',
    'without_bom': 'UTF-8 sin BOM', 'encoding_footer': 'Con BOM, Excel en Windows abre bien los acentos.',
    'no_columns': 'Elige al menos una columna.',
}
DEFAULTS = {'layout': 'all', 'separator': ',', 'language': 'en', 'header': True, 'bom': True, 'decimal_comma': False,
            'custom': []}


def options_from_prefs(data) -> dict:
    """Lo guardado en las preferencias, campo a campo: lo que no se entiende vuelve al valor por omisión."""
    data = data if isinstance(data, dict) else {}
    out = dict(DEFAULTS)
    if data.get('layout') in dict((k, 1) for k, _, _ in LAYOUTS):
        out['layout'] = data['layout']
    if data.get('separator') in dict(SEPARATORS):
        out['separator'] = data['separator']
    if data.get('language') in dict(LANGUAGES):
        out['language'] = data['language']
    for key in ('header', 'bom', 'decimal_comma'):
        if isinstance(data.get(key), bool):
            out[key] = data[key]
    custom = data.get('custom')
    if isinstance(custom, list):
        out['custom'] = [[str(k), str(t)] for k, t in (c for c in custom if isinstance(c, list) and len(c) == 2)]
    return out


def summary(options: dict) -> str:
    """«Todas las columnas · coma · inglés» (`PointCSV.Text.summary`)."""
    layout = dict((k, t) for k, t, _ in LAYOUTS)[options['layout']]
    separator = dict(SEPARATORS)[options['separator']].lower()
    if options['layout'] in SHORT_LAYOUTS and not options['header']:
        header = 'sin encabezado'
    else:
        header = 'inglés' if options['language'] == 'en' else 'español'
    return ' · '.join((layout, separator, header))


def column_titles(extras, language: str) -> dict[str, str]:
    return {k: (en if language == 'en' else es) for k, en, es in list(columnas.COLUMNS) + list(extras)}


def default_custom(extras) -> list[list]:
    """«Restablecer»: todas encendidas, en el orden de «Todas», con su nombre."""
    return [[k, True, ''] for k, _, _ in list(columnas.COLUMNS) + list(extras)]


def custom_rows(options: dict, extras) -> list[list]:
    """La lista de «Personalizada»: la recordada (sin lo que ya no existe ni repetidos) y lo que falta, apagado."""
    known = [k for k, _, _ in list(columnas.COLUMNS) + list(extras)]
    if not options['custom']:
        return default_custom(extras)
    rows, seen = [], set()
    for key, title in options['custom']:
        if key in known and key not in seen:
            rows.append([key, True, title])
            seen.add(key)
    return rows + [[k, False, ''] for k in known if k not in seen]


class CsvOptionsDialog(tk.Toplevel):
    """«Opciones de CSV» antes de elegir el archivo. `result`: el diccionario de opciones, o None si se canceló."""

    def __init__(self, app, extras, initial: dict):
        super().__init__(app)
        self.app, self.extras, self.result = app, list(extras), None
        self.title(TEXT['title'])
        self.transient(app)
        self.resizable(False, True)
        width = int(300*app.ui_scale*app.theme.text_scale())
        body = ttk.Frame(self, padding=20)
        body.pack(fill='both', expand=True)
        body.columnconfigure((0, 1), weight=1)
        self.layout = tk.StringVar(value=initial['layout'])
        self.separator = tk.StringVar(value=initial['separator'])
        self.language = tk.StringVar(value=initial['language'])
        self.no_header = tk.BooleanVar(value=not initial['header'])
        self.bom = tk.BooleanVar(value=initial['bom'])
        self.decimal_comma = tk.BooleanVar(value=initial['decimal_comma'])
        self.rows = custom_rows(initial, self.extras)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 20))
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky='nsew')

        def section(parent, title, pady=(0, 4)):
            ttk.Label(parent, text=title, style='Section.TLabel').pack(anchor='w', pady=pady)

        def footer(parent, text):
            ttk.Label(parent, text=text, style='Subtitle.TLabel', wraplength=width, justify='left').pack(
                anchor='w', pady=(2, 8))

        section(left, TEXT['layout'])
        for key, title, detail in LAYOUTS:
            ttk.Radiobutton(left, text=title, value=key, variable=self.layout, command=self.refresh).pack(anchor='w')
            ttk.Label(left, text=detail, style='Subtitle.TLabel', wraplength=width, justify='left').pack(
                anchor='w', padx=(24, 0), pady=(0, 4))
        self.columns_frame = ttk.Frame(left)
        section(self.columns_frame, TEXT['columns'], (8, 4))
        holder = ttk.Frame(self.columns_frame)
        holder.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(holder, columns=('on', 'name'), show='headings', height=9, selectmode='browse')
        self.tree.heading('on', text='')
        self.tree.heading('name', text='Columna')
        self.tree.column('on', width=int(28*app.ui_scale), stretch=False, anchor='center')
        self.tree.column('name', width=width, stretch=True)
        bar = ttk.Scrollbar(holder, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side='left', fill='both', expand=True)
        bar.pack(side='left', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.selected())
        self.tree.bind('<space>', lambda e: self.toggle())
        self.tree.bind('<Double-1>', lambda e: self.toggle())
        buttons = ttk.Frame(self.columns_frame)
        buttons.pack(fill='x', pady=(6, 0))
        for text, command in (('Subir', lambda: self.move(-1)), ('Bajar', lambda: self.move(1)),
                              ('Encender o apagar', self.toggle)):
            ttk.Button(buttons, text=text, command=command).pack(side='left', padx=(0, 6))
        more = ttk.Frame(self.columns_frame)
        more.pack(fill='x', pady=(6, 0))
        ttk.Button(more, text=TEXT['select_all'], command=self.select_all).pack(side='left', padx=(0, 6))
        ttk.Button(more, text=TEXT['reset'], command=self.reset).pack(side='left')
        ttk.Label(self.columns_frame, text=TEXT['rename'], style='Strong.TLabel').pack(anchor='w', pady=(10, 4))
        self.rename = tk.StringVar()
        self.rename_entry = ttk.Entry(self.columns_frame, textvariable=self.rename)
        self.rename_entry.pack(fill='x')
        self.rename_note = ttk.Label(self.columns_frame, text='', style='Subtitle.TLabel')
        self.rename_note.pack(anchor='w', pady=(2, 0))
        self.rename.trace_add('write', lambda *a: self.renamed())

        section(right, TEXT['separator'])
        for value, title in SEPARATORS:
            ttk.Radiobutton(right, text=title, value=value, variable=self.separator, command=self.refresh).pack(
                anchor='w')
        footer(right, TEXT['separator_footer'])
        self.comma_check = ttk.Checkbutton(right, text=TEXT['decimal_comma'], variable=self.decimal_comma)
        self.comma_check.pack(anchor='w')
        footer(right, TEXT['decimal_comma_footer'])
        section(right, TEXT['header'], (8, 4))
        for value, title in LANGUAGES:
            ttk.Radiobutton(right, text=title, value=value, variable=self.language, command=self.refresh).pack(
                anchor='w')
        self.header_check = ttk.Checkbutton(right, text=TEXT['no_header'], variable=self.no_header)
        self.header_check.pack(anchor='w', pady=(4, 0))
        footer(right, TEXT['header_footer'])
        section(right, TEXT['encoding'], (8, 4))
        for value, title in ((True, TEXT['with_bom']), (False, TEXT['without_bom'])):
            ttk.Radiobutton(right, text=title, value=value, variable=self.bom).pack(anchor='w')
        footer(right, TEXT['encoding_footer'])

        self.error = ttk.Label(body, text='', style='error.TLabel')
        self.error.grid(row=1, column=0, columnspan=2, sticky='w')
        actions = ttk.Frame(body)
        actions.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(12, 0))
        ttk.Button(actions, text='Cancelar', command=self.destroy).pack(side='right')
        ok = ttk.Button(actions, text='Exportar CSV…', style='Primary.TButton', command=self.accept)
        app.icons.bind(ok, 'share')
        ok.pack(side='right', padx=8)
        self.bind('<Escape>', lambda e: self.destroy())
        self.fill_tree()
        self.refresh()
        app.theme.walk(self)
        app.set_window_icon(self)
        self.grab_set()

    # --- Columnas de «Personalizada» ----------------------------------------------------------------------------

    def titles(self):
        return column_titles(self.extras, self.language.get())

    def fill_tree(self, keep=None):
        titles = self.titles()
        self.tree.delete(*self.tree.get_children())
        for index, (key, on, title) in enumerate(self.rows):
            self.tree.insert('', 'end', iid=str(index), values=('✓' if on else '', title.strip() or titles[key]))
        if keep is not None:
            self.tree.selection_set(str(keep))
            self.tree.see(str(keep))

    def index(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def selected(self):
        index = self.index()
        if index is None:
            return
        key, _, title = self.rows[index]
        self._renaming = True
        self.rename.set(title)
        self._renaming = False
        self.rename_note.configure(text=f'Vacío vuelve a {self.titles()[key]}.')

    def renamed(self):
        index = self.index()
        if index is None or getattr(self, '_renaming', False):
            return
        self.rows[index][2] = self.rename.get()
        self.tree.set(str(index), 'name', self.rename.get().strip() or self.titles()[self.rows[index][0]])

    def toggle(self):
        index = self.index()
        if index is not None:
            self.rows[index][1] = not self.rows[index][1]
            self.tree.set(str(index), 'on', '✓' if self.rows[index][1] else '')

    def move(self, step):
        index = self.index()
        if index is None or not 0 <= index+step < len(self.rows):
            return
        self.rows[index], self.rows[index+step] = self.rows[index+step], self.rows[index]
        self.fill_tree(keep=index+step)

    def select_all(self):
        for row in self.rows:
            row[1] = True
        self.fill_tree(self.index())

    def reset(self):
        self.rows = default_custom(self.extras)
        self.fill_tree()

    # --- Estado y resultado -------------------------------------------------------------------------------------

    def refresh(self):
        layout = self.layout.get()
        if layout == 'custom':
            self.columns_frame.pack(fill='both', expand=True)
        else:
            self.columns_frame.pack_forget()
        self.header_check.state(['!disabled'] if layout in SHORT_LAYOUTS else ['disabled'])
        self.comma_check.state(['!disabled'] if self.separator.get() != ',' else ['disabled'])
        self.fill_tree(self.index())

    def options(self) -> dict:
        layout = self.layout.get()
        return {'layout': layout, 'separator': self.separator.get(), 'language': self.language.get(),
                'header': not (layout in SHORT_LAYOUTS and self.no_header.get()), 'bom': bool(self.bom.get()),
                'decimal_comma': bool(self.decimal_comma.get()) and self.separator.get() != ',',
                'custom': [[k, t] for k, on, t in self.rows if on] if layout == 'custom' else []}

    def accept(self):
        options = self.options()
        if options['layout'] == 'custom' and not options['custom']:
            self.error.configure(text=TEXT['no_columns'])
            return
        self.result = options
        self.destroy()


def csv_options(export, options: dict):
    """El `CsvOptions` del motor a partir de las opciones del diálogo."""
    return export.CsvOptions(layout=options['layout'], separator=options['separator'], language=options['language'],
                             header=options['header'], bom=options['bom'], decimal_comma=options['decimal_comma'],
                             custom=tuple((k, t) for k, t in options['custom']))


# --- «Opciones de DXF» -------------------------------------------------------------------------------------------

DXF_TEXT = {
    'title': 'Opciones de DXF', 'text_height': 'Altura del texto',
    'text_height_footer': 'En metros del dibujo. Por omisión 0.5 m, como las apps.',
    # Los dos de la hoja «Exportar» de las apps (`SurveyExport.Text`).
    'point_names': 'Nombres de los puntos',
    'point_names_detail': 'En el DXF, el nombre en la capa TEXTO y la descripción en DESCRIPCION.',
    'elevation': 'Cota como rótulo', 'elevation_detail': 'La cota de cada punto debajo de su nombre, en la capa COTA.',
    'bad_height': 'Escribe una altura del texto de 0.01 a 100 m.',
}
DXF_DEFAULTS = {'text_height_m': 0.5, 'point_names': True, 'elevation_labels': True}
DXF_HEIGHT_RANGE = (0.01, 100.0)


def dxf_options_from_prefs(data) -> dict:
    """Lo guardado en las preferencias, campo a campo: lo que no se entiende vuelve al valor por omisión."""
    data = data if isinstance(data, dict) else {}
    out = dict(DXF_DEFAULTS)
    height = data.get('text_height_m')
    if isinstance(height, (int, float)) and not isinstance(height, bool) and \
            DXF_HEIGHT_RANGE[0] <= height <= DXF_HEIGHT_RANGE[1]:
        out['text_height_m'] = float(height)
    for key in ('point_names', 'elevation_labels'):
        if isinstance(data.get(key), bool):
            out[key] = data[key]
    return out


def dxf_options(options: dict) -> DxfOptions:
    """El `DxfOptions` del motor a partir de las opciones del diálogo (o de las preferencias)."""
    options = dxf_options_from_prefs(options)
    return DxfOptions(text_height_m=options['text_height_m'], point_names=options['point_names'],
                      elevation_labels=options['point_names'] and options['elevation_labels'])


class DxfOptionsDialog(tk.Toplevel):
    """«Opciones de DXF» antes de elegir el archivo. `result`: el diccionario de opciones, o None si se canceló."""

    def __init__(self, app, initial: dict):
        super().__init__(app)
        self.app, self.result = app, None
        self.title(DXF_TEXT['title'])
        self.transient(app)
        self.resizable(False, False)
        width = int(320*app.ui_scale*app.theme.text_scale())
        body = ttk.Frame(self, padding=20)
        body.pack(fill='both', expand=True)
        self.height = tk.StringVar(value=f"{initial['text_height_m']:g}")
        self.names = tk.BooleanVar(value=initial['point_names'])
        self.elevation = tk.BooleanVar(value=initial['elevation_labels'])

        def footer(text, padx=0):
            ttk.Label(body, text=text, style='Subtitle.TLabel', wraplength=width, justify='left').pack(
                anchor='w', padx=(padx, 0), pady=(2, 10))

        ttk.Label(body, text=DXF_TEXT['text_height'], style='Section.TLabel').pack(anchor='w', pady=(0, 4))
        row = ttk.Frame(body)
        row.pack(anchor='w')
        self.height_entry = ttk.Spinbox(row, from_=0.1, to=10, increment=0.1, textvariable=self.height, width=8,
                                        format='%.2f')
        self.height_entry.pack(side='left')
        ttk.Label(row, text='m').pack(side='left', padx=(6, 0))
        footer(DXF_TEXT['text_height_footer'])
        self.names_check = ttk.Checkbutton(body, text=DXF_TEXT['point_names'], variable=self.names,
                                           command=self.refresh)
        self.names_check.pack(anchor='w')
        footer(DXF_TEXT['point_names_detail'], 24)
        self.elevation_check = ttk.Checkbutton(body, text=DXF_TEXT['elevation'], variable=self.elevation)
        self.elevation_check.pack(anchor='w')
        footer(DXF_TEXT['elevation_detail'], 24)
        self.error = ttk.Label(body, text='', style='error.TLabel', wraplength=width, justify='left')
        self.error.pack(anchor='w')
        actions = ttk.Frame(body)
        actions.pack(fill='x', pady=(12, 0))
        ttk.Button(actions, text='Cancelar', command=self.destroy).pack(side='right')
        ok = ttk.Button(actions, text='Exportar DXF…', style='Primary.TButton', command=self.accept)
        app.icons.bind(ok, 'share')
        ok.pack(side='right', padx=8)
        self.bind('<Escape>', lambda e: self.destroy())
        self.bind('<Return>', lambda e: self.accept())
        self.refresh()
        app.theme.walk(self)
        app.set_window_icon(self)
        self.height_entry.focus_set()
        self.grab_set()

    def refresh(self):
        # Sin nombres no hay rótulos (el motor tampoco escribe la cota ni la descripción).
        self.elevation_check.state(['!disabled'] if self.names.get() else ['disabled'])

    def options(self) -> dict | None:
        from app.wizard import parse_number
        try:
            height = parse_number(self.height.get())
        except ValueError:
            return None
        if not DXF_HEIGHT_RANGE[0] <= height <= DXF_HEIGHT_RANGE[1]:
            return None
        return {'text_height_m': height, 'point_names': bool(self.names.get()),
                'elevation_labels': bool(self.elevation.get())}

    def accept(self):
        options = self.options()
        if options is None:
            self.error.configure(text=DXF_TEXT['bad_height'])
            self.height_entry.focus_set()
            return
        self.result = options
        self.destroy()


# --- Lo común de las dos páginas --------------------------------------------------------------------------------

class ExportActions:
    """Diálogos de archivo y carpeta y la tarea en segundo plano (mezclado en StaticPage y PointsPage)."""

    EXPORT_TITLE = 'Exportar'
    EXPORT_STEM = 'resultado'
    EXPORT_DONE = 'Exportado'
    CSV_EXTRAS: list = []

    def export_folder(self) -> Path:
        return self.path.parent if getattr(self, 'path', None) else Path.home()

    def export_stem(self) -> str:
        return self.path.stem if getattr(self, 'path', None) else self.EXPORT_STEM

    def ask_export_file(self, title, suffix, kind, initial):
        return filedialog.asksaveasfilename(parent=self.app, title=title, defaultextension=suffix,
                                            initialdir=str(self.export_folder()), initialfile=initial,
                                            filetypes=[(kind, '*'+suffix)])

    def run_export(self, work, done_text):
        def done(path):
            path = Path(path[0] if isinstance(path, list) else path)
            self.app.set_status(done_text(path), path.parent)

        def failed(kind_, message, detail):
            self.app.show_error(kind_, message, detail, 'No se pudo exportar')
        self.app.set_status('Exportando…')
        return tasks.run(self, work, on_done=done, on_error=failed)

    def ask_csv_options(self, extras):
        export = self.app.backend.export
        if export is None or self.output is None:
            return None
        dialog = CsvOptionsDialog(self.app, extras, options_from_prefs(self.app.settings.values.csv_export))
        self.app.wait_window(dialog)
        if dialog.result is None:
            return None
        try:
            self.app.settings.save(csv_export=dialog.result)
        except Exception:  # noqa: BLE001 — si no se pueden guardar las preferencias, se exporta igual
            pass
        return dialog.result

    def ask_dxf_options(self):
        """«Opciones de DXF»; lo elegido queda en las preferencias para la próxima vez."""
        if self.app.backend.export is None or self.output is None:
            return None
        dialog = DxfOptionsDialog(self.app, dxf_options_from_prefs(self.app.settings.values.dxf_export))
        self.app.wait_window(dialog)
        if dialog.result is None:
            return None
        try:
            self.app.settings.save(dxf_export=dialog.result)
        except Exception:  # noqa: BLE001 — si no se pueden guardar las preferencias, se exporta igual
            pass
        return dialog.result

    def export_dxf_options(self, chosen) -> DxfOptions:
        """Las del diálogo o, si se exporta sin él, las recordadas."""
        return dxf_options(chosen if chosen is not None else self.app.settings.values.dxf_export)

    def export_from_menu(self, formato, suffix, kind):
        """Un formato del menú «Exportar»: el DXF pregunta antes sus opciones."""
        if formato != 'dxf':
            self.export(formato, suffix, kind)
            return
        options = self.ask_dxf_options()
        if options is not None:
            self.export(formato, suffix, kind, dxf=options)

    def export_rinex(self):
        """Copia el RINEX de la sesión (el convertido de la grabación) a una carpeta que elige el usuario."""
        export = self.app.backend.export
        if export is None or self.output is None:
            return
        folder = filedialog.askdirectory(parent=self.app, title='Exportar RINEX de la sesión', mustexist=False,
                                         initialdir=str(self.export_folder()))
        if not folder:
            return
        rover = self.output['result'].rover
        self.run_export(lambda p, c: export.export_rinex(rover, Path(folder)),
                        lambda path: f'RINEX de la sesión exportado en «{path.parent.name}».')

    def add_rinex_item(self, menu):
        menu.add_command(label=RINEX_LABEL, command=self.export_rinex)
