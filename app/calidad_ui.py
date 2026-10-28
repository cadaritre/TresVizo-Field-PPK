"""Control de calidad en la interfaz: las tolerancias del proyecto y los puntos de control (en las opciones del
asistente) y, en el resultado, el estado de cada punto, las ocupaciones repetidas y los residuos contra el control.

El estado siempre va escrito con su símbolo («✓ Pasa», «▲ Revisar», «✕ Falla»); el color de la fila lo acompaña.
"""
from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from tresvizo_ppk import calidad

from app.components import card, wrap_label
from app.formatting import solution_text
from app.settings import format_length

MANUAL = 'Escritas a mano'
CONTROL_ORDERS = (('pnezd', 'PNEZD: nombre, norte, este, cota, descripción'),
                  ('penzd', 'PENZD: nombre, este, norte, cota, descripción'),
                  ('nxyzd', 'NXYZD: nombre, X (este), Y (norte), Z, descripción'))
CSV_TYPES = [('CSV de control', '*.csv *.txt'), ('Todos', '*')]
# atributo, etiqueta, unidad, ayuda
FIELDS = (('max_sigma_h_m', 'σH máxima', 'm', '√(σE² + σN²), 1σ'),
          ('max_sigma_v_m', 'σV máxima', 'm', '1σ'),
          ('min_fixed_percent', 'Épocas fijas mínimas', '%', 'de la ocupación (PPK)'),
          ('min_ratio', 'Cociente mínimo', '', 'de la solución fija'),
          ('max_pdop', 'PDOP máxima', '', 'de las épocas usadas'),
          ('min_duration_s', 'Duración mínima', 's', 'de la ocupación o de la sesión'),
          ('max_repeat_h_m', 'Entre ocupaciones, planta', 'm', 'mismo nombre en el CSV'),
          ('max_repeat_v_m', 'Entre ocupaciones, cota', 'm', ''),
          ('max_control_h_m', 'Contra control, planta', 'm', ''),
          ('max_control_v_m', 'Contra control, cota', 'm', ''))
# Solo en la red: el cierre de bucle, en mm + ppm de la longitud del bucle
NETWORK_FIELDS = (('max_loop_h_m', 'Cierre de bucle, planta', 'm', 'más los ppm de abajo'),
                  ('max_loop_h_ppm', 'Cierre en planta, ppm', 'ppm', 'de la longitud del bucle'),
                  ('max_loop_v_m', 'Cierre de bucle, cota', 'm', 'más los ppm de abajo'),
                  ('max_loop_v_ppm', 'Cierre en cota, ppm', 'ppm', 'de la longitud del bucle'))
DECIMALS = {'m': 3, '%': 1, '': 1, 's': 0, 'ppm': 1}


def fields_for(kind: str) -> tuple:
    return FIELDS + NETWORK_FIELDS if kind == 'network' else FIELDS


def order_label(order: str) -> str:
    return f'Orden {order}' if order else MANUAL


def status_cell(status) -> str:
    """«✓ Pasa», «▲ Revisar», «✕ Falla» (o «—» sin evaluación)."""
    if status is None:
        return '—'
    return f'{calidad.LEVEL_SYMBOL[status.level]} {calidad.LEVEL_TEXT[status.level]}'


def status_tags(tree, app):
    tokens = app.theme.tokens
    tree.tag_configure('q-review', foreground=tokens['warning_fg'], background=tokens['warning_bg'])
    tree.tag_configure('q-fail', foreground=tokens['error_fg'], background=tokens['error_bg'])


def tag_of(status) -> tuple[str, ...]:
    return () if status is None or status.level == 'pass' else (f'q-{status.level}',)


def kind_of(level: str) -> str:
    return {'pass': 'success', 'review': 'warning', 'fail': 'error'}[level]


def counts_text(levels) -> str:
    levels = list(levels)
    return (f'{calidad.LEVEL_SYMBOL["pass"]} {levels.count("pass")} pasan · '
            f'{calidad.LEVEL_SYMBOL["review"]} {levels.count("review")} a revisar · '
            f'{calidad.LEVEL_SYMBOL["fail"]} {levels.count("fail")} fallan')


# --- Opciones: tolerancias -------------------------------------------------------------------------------------------

def _text(value, unit: str) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and not math.isfinite(value):
        return ''
    return f'{value:.{DECIMALS[unit]}f}'


class TolerancesPanel(ttk.Frame):
    """Tolerancias del proyecto: un orden de exactitud de la NT EEP las llena; cada cifra se puede escribir a mano o
    dejar vacía (ese criterio no se evalúa)."""

    def __init__(self, parent, app, job, kind, changed):
        super().__init__(parent, style='Flat.Card.TFrame')
        self.app, self.job, self.kind, self.changed = app, job, kind, changed
        self.filling = False
        row = ttk.Frame(self, style='Flat.Card.TFrame')
        row.pack(fill='x')
        ttk.Label(row, text='Tolerancias', style='Card.Strong.TLabel').pack(side='left')
        self.order = tk.StringVar()
        values = [order_label(o) for o in calidad.ORDERS] + [MANUAL]
        self.order_box = ttk.Combobox(row, textvariable=self.order, values=values, state='readonly', width=28)
        self.order_box.pack(side='left', padx=(12, 0))
        self.order_box.bind('<<ComboboxSelected>>', lambda e: self.pick_order())
        grid = ttk.Frame(self, style='Flat.Card.TFrame')
        grid.pack(fill='x', pady=(10, 0))
        self.vars: dict[str, tk.StringVar] = {}
        self.entries: dict[str, ttk.Entry] = {}
        self.cells: list[ttk.Frame] = []
        for index, (attr, label, unit, hint) in enumerate(fields_for(kind)):
            cell = ttk.Frame(grid, style='Flat.Card.TFrame')
            self.cells.append(cell)
            ttk.Label(cell, text=label, style='Card.TLabel', width=24).pack(side='left')
            var = tk.StringVar()
            entry = ttk.Entry(cell, textvariable=var, width=8)
            entry.pack(side='left')
            ttk.Label(cell, text=unit, style='Card.TLabel', width=2).pack(side='left', padx=(4, 0))
            if hint:
                ttk.Label(cell, text=hint, style='Card.Subtitle.TLabel').pack(side='left', padx=(6, 0))
            var.trace_add('write', lambda *a, k=attr: self.edited(k))
            self.vars[attr], self.entries[attr] = var, entry
        self.columns = 0
        self.arrange(2)

        def fit(event):
            # dos columnas si caben las dos con su ayuda; si no (ventana angosta o letra grande), una
            need = max(c.winfo_reqwidth() for c in self.cells) * 2 + 24
            self.arrange(2 if event.width >= need else 1)
        grid.bind('<Configure>', fit, add='+')
        foot = ttk.Frame(self, style='Flat.Card.TFrame')
        foot.pack(fill='x', pady=(8, 0))
        ttk.Label(foot, text='Falla al pasar de', style='Card.TLabel').pack(side='left')
        self.factor = tk.StringVar()
        ttk.Spinbox(foot, from_=1, to=10, increment=0.5, textvariable=self.factor, width=5).pack(side='left', padx=6)
        ttk.Label(foot, text='veces la tolerancia (fuera de ella, a revisar).', style='Card.TLabel').pack(side='left')
        self.factor.trace_add('write', lambda *a: self.edited('fail_factor'))
        wrap_label(self, 'Del orden L (NT EEP, tabla 1): σH ≤ L/(1.2238·√2), para que el CEP95 no pase de L; '
                         'σV ≤ 2L/1.96; entre ocupaciones y contra control, L en planta y 2L en cota. Vacío: ese '
                         'criterio no se evalúa.', style='Card.Subtitle.TLabel', margin=40).pack(fill='x', pady=(8, 0))
        self.fill()

    def arrange(self, columns):
        if columns == self.columns:
            return
        self.columns = columns
        for index, cell in enumerate(self.cells):
            r, c = divmod(index, columns)
            cell.grid(row=r, column=c, sticky='w', padx=(0, 24), pady=3)

    def fill(self):
        self.filling = True
        try:
            t = self.job.tolerances
            self.order.set(order_label(t.order) if calidad.matches_order(t, self.kind) else MANUAL)
            for attr, _, unit, _ in fields_for(self.kind):
                self.vars[attr].set(_text(getattr(t, attr), unit))
            self.factor.set(f'{t.fail_factor:g}')
        finally:
            self.filling = False

    def pick_order(self):
        label = self.order.get()
        if label == MANUAL:
            self.job.tolerances = replace(self.job.tolerances, order='')
        else:
            order = label.removeprefix('Orden ')
            self.job.tolerances = replace(calidad.tolerances_for_order(order, self.kind),
                                          fail_factor=self.job.tolerances.fail_factor)
        self.fill()
        self.changed()

    def edited(self, attr):
        if self.filling:
            return
        from app.wizard import parse_number
        t = self.job.tolerances
        var = self.factor if attr == 'fail_factor' else self.vars[attr]
        text = var.get().strip()
        try:
            value = None if not text and attr != 'fail_factor' else parse_number(text)
        except ValueError:
            value = float('nan')        # la validación del paso lo dice
        if attr.endswith('_ppm') and value is None:
            value = 0.0                 # sin ppm: solo la parte fija
        setattr(t, attr, value)
        # sigue siendo el orden si las cifras son las suyas; si no, quedan «escritas a mano»
        if not calidad.matches_order(t, self.kind):
            t.order = ''
            self.order.set(MANUAL)
        self.changed()


# --- Opciones: puntos de control -------------------------------------------------------------------------------------

class ControlPanel(ttk.Frame):
    """Importar un CSV de control (PNEZD, PENZD o NXYZD) para comparar los puntos por nombre."""

    def __init__(self, parent, app, job, changed):
        super().__init__(parent, style='Flat.Card.TFrame')
        self.app, self.job, self.changed = app, job, changed
        row = ttk.Frame(self, style='Flat.Card.TFrame')
        row.pack(fill='x')
        ttk.Label(row, text='Orden sin encabezado', style='Card.TLabel').pack(side='left')
        self.order = tk.StringVar(value=CONTROL_ORDERS[0][1])
        ttk.Combobox(row, textvariable=self.order, values=[t for _, t in CONTROL_ORDERS], state='readonly',
                     width=44).pack(side='left', padx=(8, 0))
        buttons = ttk.Frame(self, style='Flat.Card.TFrame')
        buttons.pack(fill='x', pady=(10, 0))
        self.import_button = ttk.Button(buttons, text='Importar CSV de control…', command=self.ask_import)
        self.app.icons.bind(self.import_button, 'file-up')
        self.import_button.pack(side='left')
        self.remove_button = ttk.Button(buttons, text='Quitar el control', style='Card.Link.TButton',
                                        command=self.remove)
        self.remove_button.pack(side='left', padx=(12, 0))
        self.summary = wrap_label(self, '', style='Card.TLabel', margin=40)
        self.summary.pack(fill='x', pady=(8, 0))
        self.notes = wrap_label(self, '', style='Card.Subtitle.TLabel', margin=40)
        self.notes.pack(fill='x')
        self.show()

    def order_key(self) -> str:
        return next(k for k, t in CONTROL_ORDERS if t == self.order.get())

    def ask_import(self):
        path = filedialog.askopenfilename(parent=self.app, title='Coordenadas de control', filetypes=CSV_TYPES)
        if path:
            self.import_file(Path(path))

    def import_file(self, path: Path):
        try:
            points, notes = calidad.read_control(path, self.order_key())
        except Exception as exc:  # noqa: BLE001
            from app import tasks
            kind, message = tasks.classify(exc)
            self.app.show_error(kind, message, '', 'No se pudo leer el control')
            return
        if not points:
            self.app.show_error('user', f'«{path.name}» no trae coordenadas de control.', '',
                                'No se pudo leer el control')
            return
        self.job.control_points, self.job.control_source = points, path.name
        self.show(notes)
        self.changed()

    def remove(self):
        self.job.control_points, self.job.control_source = [], ''
        self.show()
        self.changed()

    def show(self, notes=()):
        points = self.job.control_points
        if not points:
            self.summary.configure(text='Sin puntos de control.')
            self.remove_button.state(['disabled'])
        else:
            planar = sum(1 for p in points if p.northing_m is not None)
            heights = sum(1 for p in points if p.elevation_m is not None)
            source = f' de «{self.job.control_source}»' if self.job.control_source else ''
            self.summary.configure(text=f'{len(points)} puntos de control{source}: {planar} con norte y este, '
                                        f'{heights} con cota.')
            self.remove_button.state(['!disabled'])
        self.notes.configure(text='\n'.join(list(notes)[:6]))


def options_cards(parent, app, job, kind, changed):
    """Las dos tarjetas de las opciones del asistente."""
    frame, body = card(parent, 'Tolerancias del proyecto',
                       'Cada punto y cada línea base sale «pasa», «a revisar» o «falla» contra ellas, en la tabla de '
                       'resultados, el informe y la exportación. Cambiarlas no obliga a volver a procesar.')
    frame.pack(fill='x', pady=(16, 0))
    TolerancesPanel(body, app, job, kind, changed).pack(fill='x')
    frame, body = card(parent, 'Puntos de control',
                       'Coordenadas conocidas en el sistema del resultado (las locales si hay una localización en '
                       'uso). Se comparan por nombre con lo medido: residuos ΔN, ΔE y de cota contra la tolerancia.')
    frame.pack(fill='x', pady=(16, 0))
    ControlPanel(body, app, job, changed).pack(fill='x')


# --- Resultado ------------------------------------------------------------------------------------------------------

def summary_card(parent, app, report, kind_label='puntos'):
    frame, inner = card(parent, 'Control de calidad')
    t = report.tolerances
    levels = [p.status.level for p in report.points]
    wrap_label(inner, counts_text(levels), style=kind_of(report.level)+'.TLabel', margin=40).pack(fill='x')
    wrap_label(inner, f'Tolerancias: {order_label(t.order).lower() if t.order else "escritas a mano"} · σH ≤ '
                      f'{_text(t.max_sigma_h_m, "m") or "—"} m · σV ≤ {_text(t.max_sigma_v_m, "m") or "—"} m',
               style='Card.Subtitle.TLabel', margin=40).pack(fill='x', pady=(6, 0))
    for note in report.notes:
        wrap_label(inner, '· '+note, style='Card.Subtitle.TLabel', margin=40).pack(fill='x', pady=(4, 0))
    return frame


def occupations_card(parent, app, report, prefs, on_choice):
    """Las ocupaciones repetidas: cada una menos la media, la mayor diferencia y qué resultado se queda el punto."""
    frame, inner = card(parent, 'Ocupaciones repetidas',
                        'Mismo nombre en el CSV de campo: el resultado es la media ponderada (1/σ²) de las fijas, o '
                        'la ocupación que elijas.')
    for g in report.groups:
        head = ttk.Frame(inner, style='Flat.Card.TFrame')
        head.pack(fill='x', pady=(10, 4))
        ttk.Label(head, text=g.name, style='Card.Strong.TLabel').pack(side='left')
        ttk.Label(head, text=status_cell(g.status), style=kind_of(g.status.level)+'.TLabel').pack(side='left',
                                                                                                padx=(10, 0))
        spread = (f'mayor diferencia {format_length(g.spread_h_m, prefs)} en planta y '
                  f'{format_length(g.spread_v_m, prefs)} en cota' if g.spread_h_m is not None else g.status.reason)
        ttk.Label(head, text=spread, style='Card.Subtitle.TLabel').pack(side='left', padx=(10, 0))
        options = [f'Media ponderada de {len(g.used)}' if len(g.used) >= 2 else 'Media (no hay dos con solución)']
        for i, p in enumerate(g.occupations):
            options.append(f'Ocupación {i + 1} · {p.point.start.astimezone():%H:%M:%S} · '
                           f'{solution_text(p.solution)}')
        var = tk.StringVar(value=options[min(g.choice, len(options) - 1)])
        box = ttk.Combobox(head, textvariable=var, values=options, state='readonly', width=34)
        box.pack(side='right')
        box.bind('<<ComboboxSelected>>', lambda e, name=g.name, v=var, o=options: on_choice(name, o.index(v.get())))
        for i, p in enumerate(g.occupations):
            d = g.deltas_enu[i]
            text = (f'  {i + 1}. {p.point.start.astimezone():%H:%M:%S} · {solution_text(p.solution)} · '
                    f'{p.epochs_fixed}/{p.epochs_total} épocas fijas · '
                    + (f'a la media {format_length(math.hypot(d.e, d.n), prefs)} / {format_length(d.u, prefs)}'
                       if d is not None else 'no entra a la media'))
            ttk.Label(inner, text=text, style='Card.TLabel').pack(anchor='w')
    return frame


def control_card(parent, app, report, prefs):
    frame, inner = card(parent, 'Control', 'Medido menos control, en el sistema del resultado'
                        + (f' («{report.control_source}»)' if report.control_source else '') + '.')
    if report.control:
        columns = [('name', 'Punto', 90), ('dn', 'ΔN', 90), ('de', 'ΔE', 90), ('dh', 'ΔH planta', 100),
                   ('dv', 'ΔV cota', 90), ('status', 'Estado', 110), ('reason', 'Razón', 260)]
        tree = ttk.Treeview(inner, columns=[c[0] for c in columns], show='headings', selectmode='none',
                            height=min(10, max(1, len(report.control))))
        for key, title, width in columns:
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=width, minwidth=width * 3 // 4, anchor='w', stretch=key == 'reason')
        status_tags(tree, app)
        for c in report.control:
            dh = math.hypot(c.delta_e_m, c.delta_n_m) if c.delta_e_m is not None else None
            tree.insert('', 'end', tags=tag_of(c.status), values=(
                c.name, format_length(c.delta_n_m, prefs), format_length(c.delta_e_m, prefs),
                format_length(dh, prefs), format_length(c.delta_h_m, prefs), status_cell(c.status),
                c.status.reason or c.note or 'dentro de tolerancia'))
        from app.components import CellTips
        CellTips(tree, app)
        tree.pack(fill='x')
    else:
        wrap_label(inner, 'Ningún punto de control coincide por nombre con un punto medido.', style='Card.TLabel',
                   margin=40).pack(fill='x')
    if report.control_missing:
        wrap_label(inner, 'Sin medición del mismo nombre: ' + ', '.join(report.control_missing),
                   style='Card.Subtitle.TLabel', margin=40).pack(fill='x', pady=(6, 0))
    return frame
