"""«Localización»: calcular, importar, revisar y aplicar la localización de proyecto (calibración local), como la hoja
de las apps TresVizo Field (`docs/localizacion.md` de las apps).

Los pares unen lo medido (puntos del PPK, el vértice del estático, los medidos de un CSV de TresVizo Field o los
puntos de un proyecto de las apps) con la coordenada local del cliente (escrita o de un CSV PNEZD o NXYZD). La tabla
enseña los residuos de todos los pares, el residuo sin el par y el sospechoso; se marcan o se quitan pares (H planta,
V cota) y la localización se recalcula al momento. También se importa la de un proyecto de las apps (`.mvz` o
respaldo ZIP) y se aplica igual. Lo que no es interfaz está en `tresvizo_ppk.crs.localizacion`.
"""
from __future__ import annotations

import tkinter as tk
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tresvizo_ppk.model import (CoordinateChoice, Localization, LocalizationControl, LocalizationMeasured,
                                LocalizationPair)

from app import tasks
from app.components import Banner, Collapsible, ScrollFrame, attach_dialog, scrolled

TITLE = 'Localización'
COLUMNS = (('h', 'H', 30, 'center'), ('v', 'V', 30, 'center'), ('name', 'Par', 70, 'w'),
           ('measured', 'Medido', 92, 'w'), ('N', 'Norte local', 96, 'e'), ('E', 'Este local', 96, 'e'),
           ('Z', 'Cota local', 78, 'e'), ('dE', 'dE', 58, 'e'), ('dN', 'dN', 58, 'e'), ('dH', 'dH', 58, 'e'),
           ('dZ', 'dZ', 58, 'e'), ('loo_h', 'Sin el par H', 80, 'e'), ('loo_v', 'Sin el par V', 80, 'e'),
           ('mark', '', 150, 'w'))
# Un CSV sin encabezado se lee en este orden (con encabezado mandan los nombres de sus columnas).
CSV_ORDERS = (('pnezd', 'PNEZD'), ('nxyzd', 'NXYZD o PENZD'))


def _loc(app):
    """El módulo de la localización (por la parte de sistemas del programa)."""
    crs = app.backend.crs
    return getattr(crs, 'localizacion', None) if crs is not None else None


def available(app) -> bool:
    return _loc(app) is not None


def candidates_from_outputs(app, choice: CoordinateChoice, pages) -> list[LocalizationMeasured]:
    """Los medidos que hay a mano: los puntos posprocesados del PPK y el vértice del estático de los resultados en
    pantalla, como fotos de su posición en el suelo (marco de la base)."""
    crs, loc = app.backend.crs, _loc(app)
    if crs is None or loc is None:
        return []
    plain = replace(choice, localization=None, localization_history=[])
    out: list[LocalizationMeasured] = []
    for page in pages:
        output = getattr(page, 'output', None)
        if not isinstance(output, dict) or 'result' not in output:
            continue
        result = output['result']
        if hasattr(result, 'points'):
            items = [(p.point.name, p.position, 'PPK') for p in result.points
                     if p.position is not None and getattr(p.solution, 'value', 'none') != 'none']
        elif getattr(result, 'position', None) is not None:
            items = [(getattr(result, 'point_name', '') or 'Vértice', result.position, 'estático')]
        else:
            items = []
        for name, position, source in items:
            try:
                c = crs.transform(position, plain)
            except Exception as exc:  # noqa: BLE001
                if tasks.classify(exc)[0] == 'program':
                    raise
                continue
            out.append(loc.measured_from_coordinate(name, c, source))
    return out


def status_text(app, choice: CoordinateChoice) -> tuple[str, str]:
    """(texto, tipo) del renglón «Localización» del selector de sistema: 'info', 'success' o 'error'."""
    loc = _loc(app)
    record = choice.localization
    if record is None or loc is None:
        return 'Ninguna: el resultado sale en la cuadrícula del sistema elegido.', 'info'
    try:
        computed = loc.compute(record, choice.geoid_id)
    except Exception as exc:  # noqa: BLE001
        kind, message = tasks.classify(exc)
        if kind == 'program':
            raise
        return f'{loc.NOT_COMPUTABLE_IN_USE} {message}', 'error'
    h, v = computed.solution.horizontal, computed.solution.vertical
    parts = [loc.system_label(computed.frame.name, record.saved_system_name), f'{len(record.pairs)} pares',
             loc.HORIZONTAL_SHORT[h.method].lower(), loc.VERTICAL_SHORT[v.method].lower()]
    if h.sigma0_m is not None:
        parts.append(f'RMS planta {h.sigma0_m:.3f} m')
    warnings = len(computed.warnings)
    if warnings:
        parts.append(f'{warnings} aviso' + ('s' if warnings > 1 else ''))
    return ' · '.join(parts), 'success'


def _n(x, decimals=3):
    return '' if x is None else f'{x:.{decimals}f}'


def _s(loc, x):
    return '' if x is None else loc.signed_number(x)


class LocalizationDialog(tk.Toplevel):
    """La hoja de la localización. `result`: ('apply', Localization), ('remove', None) o None si se canceló."""

    def __init__(self, app, choice: CoordinateChoice, candidates: list[LocalizationMeasured]):
        super().__init__(app)
        self.app, self.loc = app, _loc(app)
        self.result = None
        self.title(TITLE)
        attach_dialog(self, app)
        self.base_choice = replace(choice, localization=None, localization_history=[])
        self.current = choice.localization
        self.history = list(choice.localization_history)
        self.keep = self.current                 # su cuadrícula base se conserva mientras sus medidos sean los mismos
        self.origin = self.current.origin if self.current else 'calculada en la oficina'
        self.pairs: list[LocalizationPair] = deepcopy(self.current.pairs) if self.current else []
        self.candidates: list[LocalizationMeasured] = []
        for m in list(candidates) + [p.measured for p in self.pairs]:
            self._add_candidate(m)
        self.computed = None
        self.notes: list[str] = []

        scale = app.ui_scale
        # Cabe en una pantalla de 1280 × 720: lo de abajo se desplaza y los botones quedan siempre a la vista.
        width = min(int(1100*scale), self.winfo_screenwidth()-60)
        height = min(int(780*scale), self.winfo_screenheight()-80)
        self.geometry(f'{width}x{height}')
        self.minsize(min(int(760*scale), width), min(int(480*scale), height))
        self.text_width = width-int(110*scale)
        actions = ttk.Frame(self, padding=(18, 10, 18, 16))
        actions.pack(side='bottom', fill='x')
        holder_all = ScrollFrame(self, padding=(18, 18, 12, 0))
        holder_all.pack(fill='both', expand=True)
        outer = holder_all.body
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text=TITLE, style='Section.TLabel').grid(row=0, column=0, sticky='w')
        self.frame_label = ttk.Label(outer, text='', style='Subtitle.TLabel', justify='left')
        self.frame_label.grid(row=1, column=0, sticky='ew', pady=(2, 8))

        tools = ttk.Frame(outer)
        tools.grid(row=2, column=0, sticky='ew', pady=(0, 6))
        self.csv_order = tk.StringVar(value=CSV_ORDERS[0][1])
        add_menu = tk.Menu(self, tearoff=False)
        add_menu.add_command(label='Control de un CSV (PNEZD o NXYZD)…', command=self.add_control_csv)
        add_menu.add_command(label='Medidos de un CSV de TresVizo Field…', command=self.add_field_csv)
        add_menu.add_separator()
        add_menu.add_command(label='Importar de un proyecto de TresVizo Field (.mvz o respaldo .zip)…',
                             command=self.import_project)
        add = ttk.Menubutton(tools, text='Agregar o importar', menu=add_menu)
        add.pack(side='left')
        ttk.Label(tools, text='Orden del CSV sin encabezado', style='Subtitle.TLabel').pack(side='left', padx=(14, 6))
        ttk.Combobox(tools, textvariable=self.csv_order, values=[t for _, t in CSV_ORDERS], state='readonly',
                     width=16).pack(side='left')
        if self.history:
            self.history_var = tk.StringVar(value='Partir de una anterior…')
            box = ttk.Combobox(tools, textvariable=self.history_var, state='readonly', width=30,
                               values=[self._history_text(r) for r in reversed(self.history)])
            box.pack(side='right')
            box.bind('<<ComboboxSelected>>', lambda e: self.start_from_history(box.current()))

        holder = ttk.Frame(outer)
        holder.grid(row=3, column=0, sticky='nsew')
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(holder, columns=[c[0] for c in COLUMNS], show='headings', height=8,
                                 selectmode='browse')
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title, anchor=anchor if anchor != 'e' else 'e')
            self.tree.column(key, width=int(width*scale), anchor=anchor, stretch=key in ('name', 'measured', 'mark'))
        scrolled(self.tree, holder, horizontal=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.selected())
        self.tree.bind('<Double-1>', self.double_click)
        tokens = app.theme.tokens
        self.tree.tag_configure('bad', foreground=tokens['warning_fg'], background=tokens['warning_bg'])
        self.tree.tag_configure('suspect', foreground=tokens['error_fg'], background=tokens['error_bg'])

        edit = ttk.Frame(outer)
        edit.grid(row=4, column=0, sticky='ew', pady=(8, 0))
        self.name_var, self.measured_var = tk.StringVar(), tk.StringVar()
        self.n_var, self.e_var, self.z_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        fields = (('Par', ttk.Entry(edit, textvariable=self.name_var, width=12)),
                  ('Medido', ttk.Combobox(edit, textvariable=self.measured_var, width=22, state='readonly')),
                  ('Norte', ttk.Entry(edit, textvariable=self.n_var, width=14)),
                  ('Este', ttk.Entry(edit, textvariable=self.e_var, width=14)),
                  ('Cota', ttk.Entry(edit, textvariable=self.z_var, width=11)))
        for column, (label, widget) in enumerate(fields):
            ttk.Label(edit, text=label, style='Strong.TLabel').grid(row=0, column=column, sticky='w', padx=(0, 8))
            widget.grid(row=1, column=column, sticky='w', padx=(0, 8))
        self.measured_box = fields[1][1]
        buttons = ttk.Frame(edit)
        buttons.grid(row=1, column=len(fields), sticky='w')
        for text, command in (('Agregar par', self.add_pair), ('Guardar cambios', self.save_pair),
                              ('Planta (H)', lambda: self.toggle('h')), ('Cota (V)', lambda: self.toggle('v')),
                              ('Quitar', self.remove_pair)):
            ttk.Button(buttons, text=text, command=command).pack(side='left', padx=(0, 6))
        ttk.Label(edit, text='Norte y este locales en metros; vacíos en un banco de nivel. Cota vacía en un vértice '
                             'sin cota. Doble clic en H o V marca o quita el par de ese ajuste.',
                  style='Subtitle.TLabel').grid(row=2, column=0, columnspan=len(fields)+1, sticky='w', pady=(4, 0))

        opts = ttk.Frame(outer)
        opts.grid(row=5, column=0, sticky='ew', pady=(10, 0))
        record = self.current
        self.h_method = tk.StringVar(value=self.loc.HORIZONTAL_TITLES[record.horizontal_method if record
                                                                      else 'similarity'])
        self.v_method = tk.StringVar(value=self.loc.VERTICAL_TITLES[record.vertical_method if record else 'automatic'])
        self.tol_h = tk.StringVar(value=f'{record.horizontal_tolerance_m if record else 0.020:.3f}')
        self.tol_v = tk.StringVar(value=f'{record.vertical_tolerance_m if record else 0.040:.3f}')
        for column, (label, widget) in enumerate((
                ('Planta', ttk.Combobox(opts, textvariable=self.h_method, state='readonly', width=34,
                                        values=list(self.loc.HORIZONTAL_TITLES.values()))),
                ('Cota', ttk.Combobox(opts, textvariable=self.v_method, state='readonly', width=30,
                                      values=list(self.loc.VERTICAL_TITLES.values()))),
                ('Tolerancia en planta (m)', ttk.Entry(opts, textvariable=self.tol_h, width=8)),
                ('Tolerancia en cota (m)', ttk.Entry(opts, textvariable=self.tol_v, width=8)))):
            ttk.Label(opts, text=label, style='Strong.TLabel').grid(row=0, column=column, sticky='w', padx=(0, 12))
            widget.grid(row=1, column=column, sticky='w', padx=(0, 12))
            if isinstance(widget, ttk.Combobox):
                widget.bind('<<ComboboxSelected>>', lambda e: self.recompute())
        for var in (self.tol_h, self.tol_v):
            var.trace_add('write', lambda *a: self.recompute())

        self.params = ttk.Frame(outer)
        self.params.grid(row=6, column=0, sticky='ew', pady=(12, 0))
        self.params.columnconfigure(1, weight=1)
        self.alerts = ttk.Frame(outer)
        self.alerts.grid(row=7, column=0, sticky='ew', pady=(6, 0))
        help_box = Collapsible(outer, app, 'Cómo medir una buena localización', style_prefix='')
        help_box.grid(row=8, column=0, sticky='ew', pady=(6, 0))
        for line in self.loc.FIELD_HELP:
            ttk.Label(help_box.body, text='• '+line, style='Subtitle.TLabel', wraplength=int(760*scale),
                      justify='left').pack(anchor='w')

        ttk.Button(actions, text='Cancelar', command=self.destroy).pack(side='right')
        self.apply_button = ttk.Button(actions, text='Aplicar', style='Primary.TButton', command=self.accept)
        self.apply_button.pack(side='right', padx=8)
        if self.current is not None:
            ttk.Button(actions, text='Quitar localización', command=self.remove).pack(side='left')
        self.bind('<Escape>', lambda e: self.destroy())
        self.refresh_candidates()
        self.recompute()
        app.theme.walk(self)
        app.set_window_icon(self)
        self.grab_set()

    # --- Datos ------------------------------------------------------------------------------------------------------

    @staticmethod
    def _key(m: LocalizationMeasured):
        return (m.name, round(m.latitude_deg, 10), round(m.longitude_deg, 10))

    def _add_candidate(self, m: LocalizationMeasured):
        if all(self._key(c) != self._key(m) for c in self.candidates):
            self.candidates.append(m)

    def _candidate_text(self, m: LocalizationMeasured) -> str:
        return f'{m.name} ({m.source})' if m.source else m.name

    def refresh_candidates(self):
        self.measured_box.configure(values=[self._candidate_text(m) for m in self.candidates])

    def _history_text(self, r: Localization) -> str:
        when = r.applied_at.strftime('%Y-%m-%d %H:%M') if r.applied_at else 'sin fecha'
        return f'{when} · {len(r.pairs)} pares'

    def options(self) -> dict:
        def number(var, default):
            try:
                value = float(var.get().replace(',', '.'))
                return value if value > 0 else default
            except ValueError:
                return default
        h = next(k for k, t in self.loc.HORIZONTAL_TITLES.items() if t == self.h_method.get())
        v = next(k for k, t in self.loc.VERTICAL_TITLES.items() if t == self.v_method.get())
        return dict(horizontal_method=h, vertical_method=v, horizontal_tolerance_m=number(self.tol_h, 0.020),
                    vertical_tolerance_m=number(self.tol_v, 0.040))

    def draft(self) -> Localization:
        return self.loc.draft(self.pairs, self.base_choice, keep=self.keep, origin=self.origin, **self.options())

    # --- Calcular y enseñar -------------------------------------------------------------------------------------

    def recompute(self):
        self.computed, error = None, ''
        record = None
        if self.pairs:
            try:
                record = self.draft()
                self.computed = self.loc.compute(record, self.base_choice.geoid_id)
            except Exception as exc:  # noqa: BLE001
                kind, error = tasks.classify(exc)
                if kind == 'program':
                    raise
        else:
            error = 'Agrega pares: de los puntos procesados, de un CSV de control o de un proyecto de TresVizo Field.'
        self.fill_tree()
        self.show(record, error)

    def fill_tree(self):
        selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        sol = self.computed.solution if self.computed else None
        o = self.options()
        for index, pair in enumerate(self.pairs):
            r = sol.residuals[index] if sol else None
            mark, tags = '', ()
            if sol:
                flag = (sol.flags_horizontal_suspect(index, o['horizontal_tolerance_m']) or
                        sol.flags_vertical_suspect(index, o['vertical_tolerance_m']))
                largest = index in (sol.horizontal_suspect_index, sol.vertical_suspect_index)
                mark = self.loc.suspect_note(largest, flag)
                if not (pair.uses_horizontal or pair.uses_vertical):
                    mark = ('comprobación · ' + mark) if mark else 'comprobación'
                out = ((r.dH is not None and r.dH > o['horizontal_tolerance_m']) or
                       (r.dZ is not None and abs(r.dZ) > o['vertical_tolerance_m']))
                tags = ('suspect',) if flag else (('bad',) if out else ())
            c = pair.control
            self.tree.insert('', 'end', iid=str(index), tags=tags, values=(
                '✓' if pair.uses_horizontal else '', '✓' if pair.uses_vertical else '', pair.name,
                self._candidate_text(pair.measured), _n(c.northing_m), _n(c.easting_m), _n(c.elevation_m),
                _s(self.loc, r.dE if r else None), _s(self.loc, r.dN if r else None), _n(r.dH if r else None),
                _s(self.loc, r.dZ if r else None), _n(r.loo_dH if r else None), _s(self.loc, r.loo_dZ if r else None),
                mark))
        if selection and self.tree.exists(selection[0]):
            self.tree.selection_set(selection[0])

    def show(self, record, error):
        for child in self.alerts.winfo_children():
            child.destroy()
        base = self.base_choice
        height = f'sobre el geoide {base.geoid_id.upper()} (H = h − N)' if base.geoid_id else 'elipsoidal'
        if self.computed is not None:
            frame = self.computed.frame
            self.frame_label.configure(text=f'Cuadrícula base: {frame.name}'
                                       + (f' ({frame.crs_code})' if frame.crs_code else
                                          f' (meridiano central {record.base_grid_central_meridian_deg:.6f}°)')
                                       + f' · Altura base: {height} · {self.origin}')
            self.show_parameters(self.loc.parameter_lines(self.computed))
            notes = self.loc.horizontal_notes(self.computed.solution) + self.loc.vertical_notes(self.computed.solution)
            if self.computed.scale_match:
                notes.append(self.loc.scale_match_text(self.computed.scale_match))
            for text in self.notes + notes:
                self.banner('info', text)
            options = self.computed.options
            for w in self.computed.warnings:
                self.banner('warning', self.loc.warning_message(w, options))
        else:
            self.frame_label.configure(text=f'Sistema: {base.label or base.crs_id} · Altura base: {height}')
            self.show_parameters([])
            for text in self.notes:
                self.banner('info', text)
        if error:
            self.banner('error', error)
        self.apply_button.state(['!disabled'] if self.computed is not None else ['disabled'])
        self.app.theme.walk(self.alerts)

    def banner(self, kind, text):
        banner = Banner(self.alerts, self.app, kind, text)
        banner.label.configure(wraplength=self.text_width)
        banner.pack(fill='x', pady=(0, 4))

    def show_parameters(self, pairs):
        for child in self.params.winfo_children():
            child.destroy()
        for row, (label, value) in enumerate(pairs):
            ttk.Label(self.params, text=label, style='Strong.TLabel').grid(row=row, column=0, sticky='nw',
                                                                          padx=(0, 14), pady=1)
            ttk.Label(self.params, text=value, wraplength=self.text_width-int(260*self.app.ui_scale),
                      justify='left').grid(row=row, column=1, sticky='w', pady=1)
        self.app.theme.walk(self.params)

    # --- Pares ------------------------------------------------------------------------------------------------------

    def index(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def selected(self):
        index = self.index()
        if index is None:
            return
        p = self.pairs[index]
        self.name_var.set(p.name)
        self.measured_var.set(self._candidate_text(p.measured))
        self.n_var.set(_n(p.control.northing_m, 4))
        self.e_var.set(_n(p.control.easting_m, 4))
        self.z_var.set(_n(p.control.elevation_m, 4))

    def double_click(self, event):
        column = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        key = COLUMNS[int(column[1:]) - 1][0] if column else ''
        if key in ('h', 'v'):
            self.toggle(key)

    def toggle(self, which):
        index = self.index()
        if index is None:
            return
        p = self.pairs[index]
        if which == 'h':
            self.pairs[index] = replace(p, uses_horizontal=not p.uses_horizontal)
        else:
            self.pairs[index] = replace(p, uses_vertical=not p.uses_vertical)
        self.recompute()

    def _read_editor(self) -> LocalizationPair | None:
        def value(var, what):
            text = var.get().strip().replace(' ', '')
            if not text:
                return None
            try:
                return float(text.replace(',', '.'))
            except ValueError:
                raise ValueError(f'{what}: «{var.get()}» no es un número.') from None
        name = self.name_var.get().strip()
        try:
            n, e, z = value(self.n_var, 'Norte'), value(self.e_var, 'Este'), value(self.z_var, 'Cota')
        except ValueError as error:
            messagebox.showwarning(TITLE, str(error), parent=self)
            return None
        index = next((i for i, m in enumerate(self.candidates)
                      if self._candidate_text(m) == self.measured_var.get()), None)
        if not name or index is None:
            messagebox.showwarning(TITLE, 'Escribe el nombre del par y elige el punto medido.', parent=self)
            return None
        if (n is None) != (e is None):
            messagebox.showwarning(TITLE, 'Escribe norte y este, o ninguno de los dos (banco de nivel).', parent=self)
            return None
        if n is None and z is None:
            messagebox.showwarning(TITLE, 'El par necesita norte y este locales, o una cota.', parent=self)
            return None
        m = self.candidates[index]
        return LocalizationPair(name=name, control=LocalizationControl(n, e, z), measured=m,
                                uses_horizontal=n is not None,
                                uses_vertical=z is not None and m.ellipsoidal_height_m is not None,
                                id=self.loc.new_uuid())

    def add_pair(self):
        pair = self._read_editor()
        if pair is None:
            return
        if any(p.name == pair.name for p in self.pairs):
            messagebox.showwarning(TITLE, f'Ya hay un par «{pair.name}»: elige otro nombre o usa «Guardar cambios».',
                                   parent=self)
            return
        self.pairs.append(pair)
        self.recompute()
        self.tree.selection_set(str(len(self.pairs)-1))

    def save_pair(self):
        index = self.index()
        pair = self._read_editor()
        if index is None or pair is None:
            return
        old = self.pairs[index]
        self.pairs[index] = replace(pair, id=old.id or pair.id,
                                    uses_horizontal=old.uses_horizontal and pair.control.northing_m is not None,
                                    uses_vertical=old.uses_vertical and pair.uses_vertical)
        self.recompute()

    def remove_pair(self):
        index = self.index()
        if index is not None:
            del self.pairs[index]
            self.recompute()

    def merge(self, pairs: list[LocalizationPair]):
        by_name = {p.name.casefold(): i for i, p in enumerate(self.pairs)}
        for p in pairs:
            i = by_name.get(p.name.casefold())
            if i is None:
                self.pairs.append(p)
            else:
                self.pairs[i] = p

    # --- Importar ---------------------------------------------------------------------------------------------------

    def _ask(self, title, types):
        path = filedialog.askopenfilename(parent=self, title=title, filetypes=types)
        return Path(path) if path else None

    def _failed(self, exc):
        kind, message = tasks.classify(exc)
        if kind == 'program':
            raise exc
        messagebox.showerror(TITLE, message, parent=self)

    def add_control_csv(self, path=None):
        path = path or self._ask('Coordenadas locales de control', [('CSV', '*.csv *.txt'), ('Todos', '*')])
        if path is None:
            return
        order = next(k for k, t in CSV_ORDERS if t == self.csv_order.get())
        try:
            control, notes = self.loc.read_control_csv(path, order)
        except Exception as exc:  # noqa: BLE001
            return self._failed(exc)
        pairs, missing = self.loc.pairs_from(control, self.candidates)
        self.merge(pairs)
        self.notes = [f'Del CSV «{path.name}»: {len(pairs)} pares por nombre.'] + notes
        if missing:
            self.notes.append(f'Sin punto medido con el mismo nombre (no entran): {", ".join(missing[:12])}'
                              + ('…' if len(missing) > 12 else '') + '. Agrega sus medidos o escríbelos a mano.')
        self.recompute()

    def add_field_csv(self, path=None):
        path = path or self._ask('CSV de TresVizo Field con los puntos medidos', [('CSV', '*.csv'), ('Todos', '*')])
        if path is None:
            return
        core, crs = self.app.backend.core, self.app.backend.crs
        try:
            points = core.read_field_csv(path)
        except Exception as exc:  # noqa: BLE001
            return self._failed(exc)
        added = 0
        for p in points:
            if p.field_position is None:
                continue
            try:
                c = crs.transform(p.field_position, self.base_choice)
            except Exception as exc:  # noqa: BLE001
                if tasks.classify(exc)[0] == 'program':
                    raise
                continue
            self._add_candidate(self.loc.measured_from_coordinate(p.name, c, 'CSV de campo'))
            added += 1
        self.refresh_candidates()
        self.notes = [f'{added} puntos medidos de «{path.name}» (lo medido en campo): ya se pueden usar en los pares.']
        self.recompute()

    def import_project(self, path=None, which: int | None = None):
        path = path or self._ask('Proyecto de TresVizo Field', [('Proyecto o respaldo', '*.mvz *.zip'),
                                                                 ('Todos', '*')])
        if path is None:
            return
        try:
            projects = self.loc.read_field_project(path)
        except Exception as exc:  # noqa: BLE001
            return self._failed(exc)
        with_loc = [p for p in projects if p.in_use is not None]
        for project in projects:
            for point in project.points:
                self._add_candidate(LocalizationMeasured(point.name, point.latitude_deg, point.longitude_deg,
                                                         point.ellipsoidal_height_m, point.point_id or None,
                                                         f'proyecto {project.name}'))
        self.refresh_candidates()
        if not with_loc:
            self.notes = [f'«{path.name}» no trae una localización en uso. Sus {sum(len(p.points) for p in projects)} '
                          'puntos quedan como medidos para armar los pares.']
            self.recompute()
            return
        if len(with_loc) == 1:
            project = with_loc[0]
        else:
            project = with_loc[which] if which is not None else self._pick_project(with_loc)
        if project is None:
            return
        record = self.loc.import_localization(project)
        self.pairs = deepcopy(record.pairs)
        self.keep, self.origin = record, record.origin
        self.h_method.set(self.loc.HORIZONTAL_TITLES[record.horizontal_method])
        self.v_method.set(self.loc.VERTICAL_TITLES[record.vertical_method])
        self.tol_h.set(f'{record.horizontal_tolerance_m:.3f}')
        self.tol_v.set(f'{record.vertical_tolerance_m:.3f}')
        for p in self.pairs:
            self._add_candidate(p.measured)
        self.refresh_candidates()
        self.notes = [f'Importada del proyecto «{project.name}» de TresVizo Field, con su cuadrícula base tal cual.']
        app_geoid = project.geoid_id or (record.height_base_geoid_id if record.height_base_kind == 'orthometric'
                                         else None)
        if (app_geoid or None) != (self.base_choice.geoid_id or None):
            there = f'el geoide {app_geoid.upper()}' if app_geoid else 'alturas elipsoidales'
            here = f'el geoide {self.base_choice.geoid_id.upper()}' if self.base_choice.geoid_id else \
                'alturas elipsoidales'
            self.notes.append(f'El proyecto de las apps usa {there} y aquí está elegido {here}: la cota local se '
                              'calcula con lo de aquí (manda el geoide de ahora). Para la misma cota que en campo, '
                              'elige el mismo en «Geoide».')
        self.recompute()

    def _pick_project(self, projects):
        names = [f'{p.name} · {len(p.in_use.pairs)} pares' for p in projects]
        dialog = tk.Toplevel(self)
        dialog.title('Elige el proyecto')
        attach_dialog(dialog, self)
        box = tk.Listbox(dialog, height=min(10, len(names)), width=50)
        for n in names:
            box.insert('end', n)
        box.pack(padx=16, pady=12)
        chosen = []
        ttk.Button(dialog, text='Usar su localización', style='Primary.TButton',
                   command=lambda: (chosen.extend(box.curselection()), dialog.destroy())).pack(pady=(0, 12))
        self.app.theme.walk(dialog)
        dialog.grab_set()
        self.wait_window(dialog)
        return projects[chosen[0]] if chosen else None

    def start_from_history(self, index):
        record = list(reversed(self.history))[index]
        self.pairs = deepcopy(record.pairs)
        self.keep, self.origin = record, record.origin
        self.h_method.set(self.loc.HORIZONTAL_TITLES[record.horizontal_method])
        self.v_method.set(self.loc.VERTICAL_TITLES[record.vertical_method])
        self.notes = ['Borrador con los pares y las opciones de una localización anterior. No se aplica nada hasta '
                      '«Aplicar».']
        self.recompute()

    # --- Terminar ---------------------------------------------------------------------------------------------------

    def accept(self):
        if self.computed is None:
            return
        self.result = ('apply', self.loc.applied(self.draft()))
        self.destroy()

    def remove(self):
        if messagebox.askyesno(TITLE, 'El resultado vuelve a la cuadrícula del sistema elegido. La localización queda '
                                      'en el historial del proyecto.', parent=self):
            self.result = ('remove', None)
            self.destroy()


def edit(app, choice: CoordinateChoice, candidates) -> CoordinateChoice | None:
    """Abre la hoja y devuelve la elección con la localización nueva (o sin ella) y su historial; None si se canceló."""
    dialog = LocalizationDialog(app, choice, candidates)
    app.wait_window(dialog)
    return apply_result(app, choice, dialog.result)


def apply_result(app, choice: CoordinateChoice, result) -> CoordinateChoice | None:
    if result is None:
        return None
    loc = _loc(app)
    action, record = result
    history = list(choice.localization_history)
    if choice.localization is not None:
        history.append(loc.removed(choice.localization))
    return replace(choice, localization=record if action == 'apply' else None, localization_history=history)
