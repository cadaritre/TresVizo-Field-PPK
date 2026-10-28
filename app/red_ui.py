"""Red estática (estático de varios puntos): receptores con sus ocupaciones, estaciones de referencia, ajustes,
proceso y la pantalla de la red (esquema, puntos, líneas, cierres, ajustes y qué estación es fija), como TBC.

Cambiar qué estación es fija (y cómo), dejar fuera una línea o decidir si es trivial vuelve a ajustar la red con las
líneas ya procesadas, sin volver a procesar (`core.readjust_network`)."""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from tresvizo_ppk.model import Ephemeris, NetworkOccupation

from app import calidad_ui, tasks
from app.charts import _Chart, enu_of, nice_step, distance_text
from app.components import CellTips, DropZone, FileList, LabeledEntry, card, fit_columns, wrap_label
from app.export_ui import CUSTOM_CSV_LABEL, POINT_FORMATS, ExportActions, csv_options
from app.formatting import plural, solution_text
from app.red_job import FIX_LABELS, MAX_NETWORK_BASES, MAX_ROVERS, NetworkJob, RoverEntry
from app.settings import format_length
from app.static_ui import RECORDING_TYPES
from app.wizard import BaseEntry, classify
from app.wizard_ui import NETWORK_GUIDE, AntennaPanel, BasePanel, OptionsPanel, WizardPage, choice_text, station_label

CSV_TYPES = [('CSV de TresVizo Field', '*.csv'), ('Todos los archivos', '*')]
ROLE_TEXT = {'independent': 'independiente', 'trivial': 'trivial', 'off': 'fuera'}
LINE_ACTIONS = (('', 'Automático'), ('independent', 'Independiente'), ('trivial', 'Trivial'), ('off', 'Fuera'))


# --- Esquema de la red -----------------------------------------------------------------------------------------------

class NetworkChart(_Chart):
    """Los puntos de la red en planta con sus líneas por su papel (independiente continua, trivial discontinua,
    fuera punteada; la marcada por el ajuste, en el color de aviso), las estaciones fijas como triángulos y la
    elipse al 95 % de cada punto aumentada para verse. Valores escritos: nombre de cada punto y escala."""

    def __init__(self, parent, app, height=360):
        self.points, self.lines = [], []          # antes: el tema dibuja al suscribirse
        super().__init__(parent, app, height)

    def set(self, points, lines, title=''):
        """points: [{'id', 'e', 'n', 'fixed', 'ellipse': (a, b, az) en m}]; lines: [{'a', 'b', 'role', 'flagged'}]."""
        self.points, self.lines, self.title = list(points), list(lines), title
        self.draw()

    def draw(self):
        t = self.tokens
        if not t:
            return
        self.configure(background=t['surface'])
        self.delete('all')
        width, height = max(self.winfo_width(), 320), max(self.winfo_height(), 220)
        if not self.points:
            self.create_text(width/2, height/2, text='Sin datos para dibujar.', fill=t['muted_surface'],
                             font=self.font())
            return
        pad = 40
        es = [p['e'] for p in self.points]
        ns = [p['n'] for p in self.points]
        span = max(max(es)-min(es), max(ns)-min(ns), 1.0)*1.15
        scale = min((width-2*pad)/span, (height-2*pad-20)/span)
        ce, cn = (max(es)+min(es))/2, (max(ns)+min(ns))/2

        def xy(e, n):
            return width/2+(e-ce)*scale, (height-20)/2-(n-cn)*scale
        where = {p['id']: xy(p['e'], p['n']) for p in self.points}
        for line in self.lines:
            if line['a'] not in where or line['b'] not in where:
                continue
            (x0, y0), (x1, y1) = where[line['a']], where[line['b']]
            dash = {'independent': (), 'trivial': (6, 4), 'off': (2, 4)}.get(line['role'], ())
            color = t['warning_fg'] if line.get('flagged') else (t['chart1'] if line['role'] == 'independent'
                                                              else t['border'])
            self.create_line(x0, y0, x1, y1, fill=color, dash=dash, width=2 if line.get('flagged') else 1.4)
        biggest = max((p['ellipse'][0] for p in self.points if p.get('ellipse')), default=0.0)
        exaggeration = 0.06*min(width, height)/(biggest*scale) if biggest > 0 else 0.0
        for p in self.points:
            x, y = where[p['id']]
            a, b, az = p.get('ellipse') or (0.0, 0.0, 0.0)
            if exaggeration and a > 0:
                self._ellipse(x, y, a*scale*exaggeration, b*scale*exaggeration, az, t['chart2'])
            if p.get('fixed'):
                self.create_polygon(x, y-9, x+8, y+6, x-8, y+6, fill=t['chart3'], outline=t['surface'])
            else:
                self.create_oval(x-6, y-6, x+6, y+6, fill=t['chart1'], outline=t['surface'])
            self.create_text(x+10, y-10, text=p['id'], anchor='w', fill=t['text'], font=self.font(-3))
        step = nice_step(span)
        bar = step*scale
        while bar > (width-2*pad)/2.5 and step > 0.01:
            step /= 2
            bar = step*scale
        y = height-14
        self.create_line(pad, y, pad+bar, y, fill=t['text'], width=2)
        self.create_text(pad+bar+8, y, text=distance_text(step), anchor='w', fill=t['text'], font=self.font(-3))
        notes = 'Continua: independiente · discontinua: trivial · punteada: fuera'
        if exaggeration:
            notes += f' · elipses al 95 % ×{exaggeration:,.0f}'.replace(',', ' ')
        self.create_text(width-8, y, text=notes, anchor='e', fill=t['muted_surface'], font=self.font(-4))

    def _ellipse(self, x, y, a, b, azimuth_deg, color):
        angle = math.radians(azimuth_deg)
        pts = []
        for k in range(36):
            u = 2*math.pi*k/36
            de, dn = a*math.cos(u), b*math.sin(u)
            # el semieje mayor apunta al azimut (desde el norte, horario)
            e = de*math.sin(angle)+dn*math.cos(angle)
            n = de*math.cos(angle)-dn*math.sin(angle)
            pts += [x+e, y-n]
        self.create_polygon(*pts, outline=color, fill='', width=1)


# --- Un receptor ------------------------------------------------------------------------------------------------------

class RoverPanel(ttk.Frame):
    """Un receptor: su grabación (o RINEX), el CSV de TresVizo Field con sus ocupaciones (opcional) o, sin él, el
    punto que ocupó y su altura; y su antena."""

    POLE_HINT = 'Vertical, del punto a la rosca del receptor (la distancia a la ARP la suma el programa).'

    def __init__(self, parent, page, entry: RoverEntry, on_remove=None):
        super().__init__(parent, style='Card.TFrame', padding=18)
        self.page, self.app, self.entry = page, page.app, entry
        head = ttk.Frame(self, style='Flat.Card.TFrame')
        head.pack(fill='x')
        self.app.icons.label(head, 'satellite', 20, token='primary', background='surface',
                             style='Card.TLabel').pack(side='left', padx=(0, 8))
        ttk.Label(head, text=f'Receptor {entry.key}', style='Card.Section.TLabel').pack(side='left')
        if on_remove:
            remove = ttk.Button(head, text='Quitar receptor', style='Card.Link.TButton', command=on_remove)
            self.app.icons.bind(remove, 'x', 16)
            remove.pack(side='right')
        DropZone(self, self.app, 'Arrastra aquí su grabación o sus RINEX',
                 'Una grabación .bin/.part del MeridianV o VI, o los RINEX de observación y navegación.',
                 self.add_files, RECORDING_TYPES).pack(fill='x', pady=(12, 8))
        self.files = FileList(self, self.app, self.set_files)
        self.info = ttk.Label(self, text='', style='Card.Subtitle.TLabel', justify='left')
        self.bind('<Configure>', lambda e: self.info.configure(wraplength=max(200, e.width-40)), add='+')
        self.row = row = ttk.Frame(self, style='Flat.Card.TFrame')
        row.pack(fill='x', pady=(12, 0))
        ttk.Label(row, text='Ocupaciones', style='Card.Strong.TLabel').pack(side='left')
        self.csv_button = ttk.Button(row, text='Usar el CSV de TresVizo Field…', style='Card.Link.TButton',
                                     command=self.ask_csv)
        self.app.icons.bind(self.csv_button, 'file-up', 16)
        self.csv_button.pack(side='left', padx=(12, 0))
        self.csv_remove = ttk.Button(row, text='Quitar el CSV', style='Card.Link.TButton', command=self.remove_csv)
        self.occupations = wrap_label(self, '', style='Card.Subtitle.TLabel', margin=40)
        self.occupations.pack(fill='x', pady=(4, 0))
        self.point = ttk.Frame(self, style='Flat.Card.TFrame')
        self.point.pack(fill='x', pady=(8, 0))
        self.point.columnconfigure((0, 1), weight=1, uniform='point')
        self.name_var = tk.StringVar(value=entry.point_name)
        self.height_var = tk.StringVar(value=entry.pole_height)
        self.code_var = tk.StringVar(value=entry.code)
        self.description_var = tk.StringVar(value=entry.description)
        self.name_field = LabeledEntry(self.point, 'Nombre del punto', self.name_var)
        self.height_field = LabeledEntry(self.point, 'Altura del bastón o trípode (m)', self.height_var,
                                         self.POLE_HINT)
        LabeledEntry(self.point, 'Código (opcional)', self.code_var).grid(row=1, column=0, sticky='new', padx=(0, 12),
                                                                         pady=(12, 0))
        LabeledEntry(self.point, 'Descripción (opcional)', self.description_var).grid(row=1, column=1, sticky='new',
                                                                                     padx=(12, 0), pady=(12, 0))
        self.name_field.grid(row=0, column=0, sticky='new', padx=(0, 12))
        self.height_field.grid(row=0, column=1, sticky='new', padx=(12, 0))
        self.antenna = AntennaPanel(self, self.app, page.job.options, f'rover:{entry.key}', page.changed,
                                    height_var=self.height_var)
        self.antenna.pack(fill='x', pady=(12, 0))
        for var, attr in ((self.name_var, 'point_name'), (self.height_var, 'pole_height'), (self.code_var, 'code'),
                          (self.description_var, 'description')):
            var.trace_add('write', lambda *a, v=var, k=attr: self.edit(k, v.get()))
        self.files.set(entry.files)
        if entry.files:
            self.files.pack(fill='x', pady=(0, 4), before=self.row)
            self.read()
        self.show_csv()

    def edit(self, attr, value):
        setattr(self.entry, attr, value)
        self.page.changed()

    def add_files(self, paths):
        csvs = [p for p in paths if classify(p) == 'csv']
        rest = [p for p in paths if classify(p) != 'csv']
        if csvs:
            self.set_csv(csvs[:1])
        if rest:
            self.set_files(list(dict.fromkeys([*self.entry.files, *rest])))

    def set_files(self, paths):
        self.entry.files = list(paths)
        self.files.set(paths)
        if paths and not self.files.winfo_manager():
            self.files.pack(fill='x', pady=(0, 4), before=self.row)
        elif not paths:
            self.files.pack_forget()
            self.info.pack_forget()
        self.read()
        self.page.changed()

    def read(self):
        if not self.entry.files:
            return

        def shown(obs):
            self.entry.marker = getattr(obs, 'point_name', '') or obs.marker or ''
            self.entry.position = getattr(obs, 'approx_position', None)
            self.entry.label = obs.receiver_model or obs.receiver or self.entry.files[0].name
            self.info.configure(text=f'{self.page.obs_summary(obs)} · {self.page.origin_of(self.entry.files)}')
            self.info.pack(fill='x', pady=(4, 0), before=self.row)
            if not self.entry.uses_csv:
                self.page.autofill(self.name_var, self.name_field, self.entry.marker,
                                   self.page.origin_text(obs, 'point_name', 'de la grabación'))
                pole = self.page.pole_from(obs)
                if pole is not None:
                    self.page.autofill(self.height_var, self.height_field, f'{pole:.3f}',
                                       self.page.origin_text(obs, 'pole_height_m', 'de la grabación'))
            self.antenna.set_data(obs.antenna_type, obs.radome, case_offset=obs.case_offset_m,
                                  recording=bool(obs.receiver_model) or self.page.pole_from(obs) is not None)
            self.rover_obs = obs
            self.read_csv()
        self.page.inspect(f'rover:{self.entry.key}', self.entry.files, shown)

    def ask_csv(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.app, title=f'CSV de TresVizo Field del receptor '
                                                                  f'{self.entry.key}', filetypes=CSV_TYPES)
        if path:
            self.set_csv([Path(path)])

    def set_csv(self, paths):
        self.entry.csv = list(paths)
        self.show_csv()
        self.read_csv()
        self.page.changed()

    def remove_csv(self):
        self.set_csv([])

    def show_csv(self):
        uses = self.entry.uses_csv
        if uses:
            self.csv_button.configure(text=f'CSV: {self.entry.csv[0].name}')
            if not self.csv_remove.winfo_manager():
                self.csv_remove.pack(side='left', padx=(12, 0))
            self.point.pack_forget()
        else:
            self.csv_button.configure(text='Usar el CSV de TresVizo Field…')
            self.csv_remove.pack_forget()
            if not self.point.winfo_manager():
                self.point.pack(fill='x', pady=(8, 0), after=self.occupations)
            self.occupations.configure(text='Sin CSV, toda la grabación es un solo punto: el de abajo. Con el CSV '
                                            'de la jornada, cada ocupación estática (de 10 min o más) es un punto.')

    def read_csv(self):
        core = self.app.backend.core
        if not self.entry.uses_csv or core is None or not callable(getattr(core, 'network_occupations', None)):
            return
        path = self.entry.csv[0]
        obs = getattr(self, 'rover_obs', None)

        def done(found):
            occupations, notes = found
            if not self.winfo_exists() or self.entry.csv[:1] != [path]:
                return
            text = ', '.join(o.name + (f' ({(o.end - o.start).total_seconds() / 60:.0f} min)' if o.start and o.end
                                       else '') for o in occupations)
            self.occupations.configure(text=f'{plural(len(occupations), "ocupación", "ocupaciones")}: {text}.'
                                       + (('\n' + '\n'.join(notes)) if notes else ''))

        def failed(kind, message, detail):
            if self.winfo_exists():
                self.occupations.configure(text=f'No se pudo leer el CSV: {message}')
        self.page.run_task(lambda p, c: core.network_occupations(self.entry.key, path, obs), on_done=done,
                           on_error=failed)


# --- La página --------------------------------------------------------------------------------------------------------

class NetworkPage(ExportActions, WizardPage):
    TITLE = 'Red estática'
    ICON = 'chart-scatter'
    JOB = NetworkJob
    EXPORT_STEM = 'red'

    def default_name(self):
        names = self.job.point_names()
        return f'Red {", ".join(names[:3])}' if names else super().default_name()

    # --- Pasos --------------------------------------------------------------------------------------------------

    def build_step(self, index, body):
        if index == 0:
            self.build_rovers(body)
        elif index == 1:
            self.build_bases(body)
        elif index == 2:
            OptionsPanel(body, self.app, self.job, self.changed, self.quality_changed,
                         self.presentation_changed).pack(fill='x')
            self.build_network_options(body)

    def build_rovers(self, body):
        frame, inner = card(body, 'Receptores de los puntos nuevos',
                            'Cada receptor con su grabación (o RINEX). Si midió varios puntos en la jornada, agrega el '
                            'CSV de TresVizo Field: cada ocupación estática es un punto. Las ocupaciones simultáneas '
                            'de receptores distintos forman líneas entre ellas.')
        frame.pack(fill='x')
        self.add_rover_button = ttk.Button(inner, text='Agregar receptor', command=self.add_rover)
        self.app.icons.bind(self.add_rover_button, 'plus')
        self.add_rover_button.pack(anchor='w')
        self.rover_panels = ttk.Frame(body)
        self.rover_panels.pack(fill='x')
        self.render_rovers()

    def render_rovers(self):
        for child in self.rover_panels.winfo_children():
            child.destroy()
        for index, entry in enumerate(self.job.rovers):
            remove = (lambda i=index: self.remove_rover(i)) if len(self.job.rovers) > 1 else None
            RoverPanel(self.rover_panels, self, entry, remove).pack(fill='x', pady=(16, 0))
        self.add_rover_button.state(['disabled'] if len(self.job.rovers) >= MAX_ROVERS else ['!disabled'])
        self.app.theme.walk(self.rover_panels)

    def add_rover(self):
        if len(self.job.rovers) < MAX_ROVERS:
            self.job.new_rover()
            self.render_rovers()
            self.changed()

    def remove_rover(self, index):
        entry = self.job.rovers.pop(index)
        self.job.options.antennas.pop(f'rover:{entry.key}', None)
        self.render_rovers()
        self.changed()

    def build_bases(self, body):
        frame, inner = card(body, 'Estaciones de referencia',
                            f'Hasta {MAX_NETWORK_BASES}, de las redes instaladas. Cada ocupación se procesa contra '
                            'cada estación con datos a la misma hora.')
        frame.pack(fill='x')
        wrap_label(inner, NETWORK_GUIDE, style='Card.Subtitle.TLabel', margin=40).pack(fill='x')
        self.add_base_button = ttk.Button(inner, text='Agregar estación', command=self.add_base)
        self.app.icons.bind(self.add_base_button, 'plus')
        self.add_base_button.pack(anchor='w', pady=(10, 0))
        self.fix_card, self.fix_body = card(body, 'Cómo entra cada estación al ajuste',
                                            'Fija: su coordenada no se mueve. Ponderada: con la σ de su coordenada. '
                                            'Comprobación: no restringe; el ajuste dice cuánto la contradicen las '
                                            'líneas. Se puede cambiar en el resultado sin volver a procesar.')
        self.fix_card.pack(fill='x', pady=(16, 0))
        self.base_panels = ttk.Frame(body)
        self.base_panels.pack(fill='x')
        if not self.job.bases:
            self.job.bases.append(BaseEntry())
        self.render_bases()

    def render_bases(self):
        for child in self.base_panels.winfo_children():
            child.destroy()
        for index, entry in enumerate(self.job.bases):
            remove = (lambda i=index: self.remove_base(i)) if len(self.job.bases) > 1 else None
            BasePanel(self.base_panels, self.app, entry, f'Estación {index+1}', self.base_changed, remove,
                      guide=self.network_guide, inspect=self.inspect, options=self.job.options).pack(
                fill='x', pady=(16, 0))
        self.add_base_button.state(['disabled'] if len(self.job.bases) >= MAX_NETWORK_BASES else ['!disabled'])
        self.render_fix(self.fix_body)
        self.app.theme.walk(self.base_panels)

    def base_changed(self):
        self.render_fix(self.fix_body)
        self.changed()

    def add_base(self):
        if len(self.job.bases) < MAX_NETWORK_BASES:
            self.job.bases.append(BaseEntry())
            self.render_bases()
            self.changed()

    def remove_base(self, index):
        del self.job.bases[index]
        self.render_bases()
        self.changed()

    def render_fix(self, holder, on_change=None):
        """Una fila por estación: fija, ponderada o de comprobación, y en planta y en cota."""
        for child in holder.winfo_children():
            child.destroy()
        choices = self.job.fix_choices()
        if not choices:
            ttk.Label(holder, text='Elige primero las estaciones.', style='Card.Subtitle.TLabel').pack(anchor='w')
            return
        grid = ttk.Frame(holder, style='Flat.Card.TFrame')
        grid.pack(fill='x')
        labels = list(FIX_LABELS.values())
        for row, f in enumerate(choices):
            ttk.Label(grid, text=f.point_id, style='Card.Strong.TLabel').grid(row=row, column=0, sticky='w', pady=3,
                                                                             padx=(0, 16))
            mode = tk.StringVar(value=FIX_LABELS[f.mode])
            box = ttk.Combobox(grid, textvariable=mode, values=labels, state='readonly', width=16)
            box.grid(row=row, column=1, sticky='w', pady=3)
            h, v = tk.BooleanVar(value=f.horizontal), tk.BooleanVar(value=f.vertical)
            hb = ttk.Checkbutton(grid, text='Planta', variable=h)
            vb = ttk.Checkbutton(grid, text='Cota', variable=v)
            hb.grid(row=row, column=2, sticky='w', padx=(16, 0))
            vb.grid(row=row, column=3, sticky='w', padx=(12, 0))
            for w in (hb, vb):
                w.state(['disabled'] if f.mode == 'check' else ['!disabled'])

            def apply(pid=f.point_id, mode=mode, h=h, v=v):
                key = next(k for k, label in FIX_LABELS.items() if label == mode.get())
                if key != 'check' and not (h.get() or v.get()):
                    h.set(True)
                self.job.set_fix(pid, key, h.get(), v.get())
                (on_change or self.fix_changed)()
            box.bind('<<ComboboxSelected>>', lambda e, a=apply: a())
            hb.configure(command=apply)
            vb.configure(command=apply)
        self.app.theme.walk(holder)

    def fix_changed(self):
        """Antes de procesar es un dato más del proyecto; con resultado, se vuelve a ajustar sin procesar."""
        if self.output is not None:
            self.readjust()
        else:
            self.mark_dirty()
            self.render_fix(self.fix_body)

    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self.update_title()
        self.refresh()

    def build_network_options(self, body):
        o = self.job.network_options
        frame, inner = card(body, 'Ajuste de la red',
                            'Como TBC: primero el mínimamente restringido (prueba las líneas sin que las fijas las '
                            'deformen) y luego el restringido a las fijas. Cambiarlo no obliga a volver a procesar.')
        frame.pack(fill='x', pady=(16, 0))
        grid = ttk.Frame(inner, style='Flat.Card.TFrame')
        grid.pack(fill='x')
        tests = {'tau': 'τ de Pope (como TBC)', 'w': 'w de Baarda (crítico 3.29)'}
        scales = {'auto': '×σ0² si no pasa la χ² (como TBC)', 'never': 'Nunca', 'always': 'Siempre ×σ0²',
                  'value': 'Por un factor…'}
        datums = {'point': 'Un punto fijo (la primera estación)', 'inner': 'Restricciones internas'}
        self.net_vars = {}

        def combo(row, label, mapping, attr):
            ttk.Label(grid, text=label, style='Card.TLabel').grid(row=row, column=0, sticky='w', pady=3, padx=(0, 12))
            var = tk.StringVar(value=mapping[getattr(o, attr)])
            box = ttk.Combobox(grid, textvariable=var, values=list(mapping.values()), state='readonly', width=34)
            box.grid(row=row, column=1, sticky='w', pady=3)
            box.bind('<<ComboboxSelected>>', lambda e: self.set_network_option(
                attr, next(k for k, v in mapping.items() if v == var.get())))
            self.net_vars[attr] = var
        combo(0, 'Prueba de los residuos', tests, 'test')
        combo(1, 'Escala de las covarianzas', scales, 'scale_covariances')
        combo(2, 'Datum del mínimamente restringido', datums, 'minimal_datum')
        self.scale_var = tk.StringVar(value=f'{o.scale_value:g}')
        ttk.Label(grid, text='Factor (de la varianza)', style='Card.TLabel').grid(row=3, column=0, sticky='w', pady=3)
        ttk.Entry(grid, textvariable=self.scale_var, width=8).grid(row=3, column=1, sticky='w', pady=3)
        self.scale_var.trace_add('write', lambda *a: self.set_scale_value())
        self.trivial_var = tk.BooleanVar(value=o.include_trivial)
        ttk.Checkbutton(inner, text='Meter las líneas triviales al ajuste (no son independientes: las σ salen '
                                    'optimistas)', variable=self.trivial_var,
                        command=lambda: self.set_network_option('include_trivial', self.trivial_var.get())).pack(
            anchor='w', pady=(8, 0))
        ttk.Label(inner, text='Cierre de bucle y tolerancias de los puntos: en «Tolerancias del proyecto».',
                  style='Card.Subtitle.TLabel').pack(anchor='w', pady=(8, 0))

    def set_network_option(self, attr, value):
        setattr(self.job.network_options, attr, value)
        if self.output is not None:
            self.readjust()
        else:
            self.mark_dirty()

    def set_scale_value(self):
        from app.wizard import parse_number
        try:
            value = parse_number(self.scale_var.get())
        except ValueError:
            return
        if value > 0:
            self.set_network_option('scale_value', value)

    # --- Proceso ------------------------------------------------------------------------------------------------

    def summary_lines(self):
        job = self.job
        lines = []
        for r in job.rovers:
            what = (f'ocupaciones del CSV «{r.csv[0].name}»' if r.uses_csv else
                    f'punto {r.point_name.strip() or "—"}, altura {r.pole_height or "—"} m')
            lines.append(f'Receptor {r.key}: {plural(len(r.files), "archivo")} · {what}')
        for base in job.bases:
            if base.station:
                f = next((x for x in job.fix_choices() if x.point_id == base.station.id), None)
                lines.append(f'Estación {station_label(base.station)}: {plural(len(base.files), "archivo")} · '
                             f'{FIX_LABELS[f.mode].lower() if f else "fija"}')
        o = job.options
        eph = 'precisas' if o.ephemeris is Ephemeris.PRECISE else 'difundidas'
        lines.append(f'Proceso: estático, máscara {o.elevation_mask_deg:g}°, {", ".join(o.constellations)}, '
                     f'efemérides {eph}; cada ocupación contra cada estación y entre ocupaciones simultáneas.')
        lines.append('Ajuste de red: mínimamente restringido y restringido, cierres de bucle y líneas triviales.')
        lines.append(f'Resultado en {choice_text(self.app, job.coordinates)}')
        return lines

    def work(self, job, project, workdir, progress, cancel):
        core = self.app.backend.core
        stations = job.stations()
        stages = tasks.Stages(progress, [2]*len(job.rovers)+[2]*len(stations)+[1, 10, 1])
        rovers, occupations, notes = {}, [], []
        for index, r in enumerate(job.rovers):
            obs = core.prepare_observations(r.files, workdir/f'receptor-{r.key}', pole_height_m=r.pole_height_m(),
                                            marker=None if r.uses_csv else r.point_name.strip(),
                                            progress=stages.stage(index), cancel=cancel)
            rovers[r.key] = obs
            if r.uses_csv:
                found, skipped = core.network_occupations(r.key, r.csv[0], obs)
                occupations += found
                notes += [f'{r.key}: {n}' for n in skipped]
            else:
                occupations.append(NetworkOccupation(r.point_name.strip(), r.key, code=r.code.strip(),
                                                     description=r.description.strip(), source='grabación completa'))
        starts = [o.start for o in rovers.values() if o.start]
        ends = [o.end for o in rovers.values() if o.end]
        from datetime import timedelta
        window = (min(starts)-timedelta(minutes=5), max(ends)+timedelta(minutes=5)) if starts and ends else None
        bases = []
        entries = [b for b in job.bases if b.station]
        offset = len(job.rovers)
        for index, (station, entry) in enumerate(zip(stations, entries)):
            bases.append((station, core.prepare_observations(entry.files, workdir/f'base-{station.id}',
                                                             marker=station.id, window=window,
                                                             progress=stages.stage(offset+index), cancel=cancel)))
        options = deepcopy(job.options)
        if options.ephemeris is Ephemeris.PRECISE and not options.precise_files and job.precise_download and window:
            options.precise_files = list(core.download_precise_products(
                window[0], window[1], workdir/'efemerides', stages.stage(offset+len(stations)), cancel))
        result = core.process_network(rovers, occupations, bases, options, workdir,
                                      stages.stage(offset+len(stations)+1), cancel, fix=job.fix_choices(),
                                      network_options=job.network_options, line_choices=job.line_choices,
                                      tolerances=job.tolerances)
        result.warnings = notes + list(result.warnings)
        final = stages.stage(offset+len(stations)+2)
        output = {'result': result, **self.convert(result, job.coordinates, final)}
        final(1.0, 'Listo.')
        return output

    def convert(self, result, choice, progress=None):
        """Cada punto ajustado en el sistema elegido, con su σ, de la solución ya calculada."""
        if progress:
            progress(0.2, 'Pasando las coordenadas al sistema elegido…')
        coordinates, warning = {}, None
        for p in result.points:
            c, problem = self.transform(p.position, choice)
            sigma = None
            if c is not None:
                try:
                    sigma = self.app.backend.crs.transform_sigma(p.position, p.sigma_enu, choice, p.covariance_ecef)
                except Exception as exc:  # noqa: BLE001
                    if tasks.classify(exc)[0] == 'program':
                        raise
            coordinates[p.name] = (c, sigma)
            warning = warning or problem
        return {'coordinates': coordinates, 'coordinate_warning': warning, 'choice': choice}

    def readjust(self):
        """Vuelve a ajustar con las fijas, las líneas y las opciones de la pantalla, sin procesar."""
        if self.output is None:
            return
        core = self.app.backend.core
        try:
            result = core.readjust_network(self.output['result'], fix=self.job.fix_choices(),
                                           network_options=self.job.network_options,
                                           line_choices=self.job.line_choices, tolerances=self.job.tolerances)
        except Exception as exc:  # noqa: BLE001
            kind, message = tasks.classify(exc)
            if kind == 'program':
                raise
            self.app.show_error(kind, message, '', 'No se pudo ajustar la red')
            return
        self.output = {**self.output, 'result': result, **self.convert(result, self.job.coordinates)}
        self.mark_dirty()
        self.build_result(self.output)
        self.app.set_status('Red ajustada de nuevo con las líneas ya procesadas.')

    def make_report(self, report, output, project, user, out):
        return report.network_report(output['result'], project, user, out)

    def quality_project(self, project):
        project = super().quality_project(project)
        project.network_fix = list(self.job.fix_choices())
        project.network_options = deepcopy(self.job.network_options)
        project.line_choices = dict(self.job.line_choices)
        return project

    def export_button(self, parent):
        button = ttk.Menubutton(parent, text='Exportar')
        self.app.icons.bind(button, 'share')
        menu = tk.Menu(button, tearoff=False)
        if self.app.backend.available('report'):
            menu.add_command(label='Guardar informe PDF…', command=self.view_report)
        if self.app.backend.available('export', 'export_network'):
            menu.add_separator()
            for formato, label, suffix, kind in POINT_FORMATS:
                menu.add_command(label=label, command=lambda f=formato, s=suffix, k=kind: self.export_from_menu(f, s, k))
            menu.add_command(label=CUSTOM_CSV_LABEL, command=self.export_custom_csv)
        button.configure(menu=menu)
        return button

    def export(self, formato, suffix, kind, csv=None, dxf=None):
        export = self.app.backend.export
        if export is None or self.output is None:
            return
        result, choice = self.output['result'], self.output['choice']
        target = self.ask_export_file('Exportar puntos de la red', suffix, kind, f'{self.export_stem()}{suffix}')
        if not target:
            return
        options = {}
        if csv is not None:
            options['csv_options'] = csv_options(export, csv)
        if formato in ('dxf', 'landxml', 'kml'):
            options['project'] = self.project.name if self.project else ''
        if formato == 'dxf':
            options['dxf_options'] = self.export_dxf_options(dxf)
        self.run_export(lambda p, c: export.export_network(result, choice, formato, Path(target), **options),
                        lambda path: f'Puntos de la red exportados: {path.name}.')

    def export_custom_csv(self):
        export = self.app.backend.export
        options = self.ask_csv_options(getattr(export, 'NETWORK_CUSTOM_COLUMNS', []))
        if options is not None:
            self.export('csv', '.csv', 'CSV', csv=options)

    # --- Resultado: la pantalla de la red ---------------------------------------------------------------------

    def evaluate_quality(self, output):
        from tresvizo_ppk import calidad
        job = self.job
        output['result'].quality = calidad.evaluate_network(output['result'], job.tolerances, job.control_points,
                                                            output['choice'], job.control_source)

    def result_view(self, body, output):
        result, prefs = output['result'], self.app.settings.values
        net = result.network
        final = net.constrained or net.minimal
        quality = result.quality
        top = ttk.Frame(body)
        top.pack(fill='x')
        top.columnconfigure(0, weight=2, uniform='top')
        top.columnconfigure(1, weight=3, uniform='top')
        frame, inner = card(top, 'Red', f'{plural(len(result.points), "punto nuevo", "puntos nuevos")} · '
                            f'{plural(len(result.lines), "línea", "líneas")} · '
                            f'{plural(len(net.loops), "bucle", "bucles")}')
        frame.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        passed = net.minimal.passed and (net.constrained is None or net.constrained.passed)
        level = quality.level if quality is not None else ('pass' if passed else 'review')
        wrap_label(inner, ('Ajuste dentro de sus pruebas' if passed else 'El ajuste no pasa todas sus pruebas')
                   + (f' · tolerancias: {calidad_ui.counts_text([p.status.level for p in quality.points])}'
                      if quality is not None else ''), style=calidad_ui.kind_of(level)+'.TLabel',
                   margin=40).pack(fill='x', pady=(0, 8))
        rows = [('Mínimamente restringido', self.verdict(net.minimal)),
                ('Restringido', self.verdict(net.constrained) if net.constrained else 'sin fijas en uso'),
                ('Datum', final.datum),
                ('Prueba', ('τ de Pope' if final.test == 'tau' else 'w de Baarda') +
                 f', crítico {final.critical_value:.2f}'),
                ('Escala', 'sin escalar' if final.scale_factor == 1.0 else f'×{final.scale_factor:.3f}'),
                ('Triviales', str(sum(v.use and v.trivial for v in net.vectors))),
                ('Cierres', f'{sum(lp.status.level == "pass" for lp in net.loops)} de {len(net.loops)} pasan')]
        if net.inconsistent_control:
            rows.append(('Control que no concuerda', ', '.join(net.inconsistent_control)))
        grid = ttk.Frame(inner, style='Flat.Card.TFrame')
        grid.pack(fill='x')
        for index, (label, value) in enumerate(rows):
            ttk.Label(grid, text=label, style='Card.Subtitle.TLabel').grid(row=index, column=0, sticky='nw', pady=2)
            wrap_label(grid, value, style='Card.Strong.TLabel', margin=40, wraplength=260).grid(
                row=index, column=1, sticky='w', padx=(16, 0), pady=2)
        ttk.Label(inner, text='Cómo entra cada estación', style='Card.Strong.TLabel').pack(anchor='w', pady=(12, 4))
        fix_holder = ttk.Frame(inner, style='Flat.Card.TFrame')
        fix_holder.pack(fill='x')
        self.render_fix(fix_holder, on_change=self.readjust)
        frame, inner = card(top, 'Esquema', None)
        frame.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
        chart = NetworkChart(inner, self.app)
        chart.pack(fill='both', expand=True)
        origin = final.points[0].position if final.points else None
        flagged = set(final.flagged)
        chart.set([{'id': p.id, **dict(zip(('e', 'n'), enu_of(origin, p.position)[:2])),
                    'fixed': p.kind in ('fixed', 'datum', 'weighted'), 'ellipse': p.ellipse95}
                   for p in final.points] if origin else [],
                  [{'a': v.from_id, 'b': v.to_id, 'role': self.role_of(v), 'flagged': v.id in flagged}
                   for v in net.vectors])
        self.points_card(body, output)
        self.lines_card(body, output)
        self.loops_card(body, output)
        if quality is not None and (quality.control or quality.control_missing):
            calidad_ui.control_card(body, self.app, quality, prefs).pack(fill='x', pady=(16, 0))
        warnings = list(result.warnings)+([output['coordinate_warning']] if output['coordinate_warning'] else [])
        if warnings:
            frame, inner = card(body, 'Avisos')
            frame.pack(fill='x', pady=(16, 0))
            for w in dict.fromkeys(warnings):
                wrap_label(inner, '· '+w, style='Card.TLabel', margin=40).pack(fill='x', pady=1)

    @staticmethod
    def verdict(a):
        return (f'σ0 {a.sigma0:.2f} · χ² {a.chi2:.1f} ≤ {a.chi2_upper:.1f}: pasa' if a.passed else
                f'σ0 {a.sigma0:.2f} · χ² {a.chi2:.1f} > {a.chi2_upper:.1f}: NO pasa'
                + (f' · señaladas {len(a.flagged)}' if a.flagged else ''))

    @staticmethod
    def role_of(v) -> str:
        return 'off' if not v.use else 'trivial' if v.trivial else 'independent'

    def points_card(self, body, output):
        result, prefs = output['result'], self.app.settings.values
        quality = result.quality
        frame, inner = card(body, 'Puntos ajustados')
        frame.pack(fill='x', pady=(16, 0))
        columns = [('name', 'Punto', 90), ('a', 'Este / Lat.', 130), ('b', 'Norte / Lon.', 130), ('h', 'Cota', 100),
                   ('sigma', 'σ H / V', 130), ('ellipse', 'Elipse 95 %', 130), ('lines', 'Líneas', 60),
                   ('status', 'Estado', 110)]
        tree = ttk.Treeview(inner, columns=[c[0] for c in columns], show='headings', selectmode='none',
                            height=min(12, max(1, len(result.points))))
        for key, title, width in columns:
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=width, minwidth=60, anchor='w', stretch=key == 'name')
        calidad_ui.status_tags(tree, self.app)
        statuses = {q.name: q.status for q in (quality.points if quality is not None else [])}
        for p in result.points:
            c, sigma = output['coordinates'].get(p.name, (None, None))
            sigma = sigma or p.sigma_enu
            if c is not None and c.easting_m is not None:
                a, b = format_length(c.easting_m, prefs, 3), format_length(c.northing_m, prefs, 3)
            elif c is not None:
                a, b = f'{c.latitude_deg:.8f}°', f'{c.longitude_deg:.8f}°'
            else:
                a = b = '—'
            height = c.orthometric_height_m if c is not None and c.orthometric_height_m is not None else (
                c.ellipsoidal_height_m if c is not None else None)
            status = statuses.get(p.name)
            tree.insert('', 'end', tags=calidad_ui.tag_of(status), values=(
                p.name + (' (mín.)' if p.adjustment == 'minimal' else ''), a, b, format_length(height, prefs, 3),
                f'{format_length(math.hypot(sigma.e, sigma.n), prefs)} / {format_length(sigma.u, prefs)}',
                f'{format_length(p.ellipse95[0], prefs)} × {format_length(p.ellipse95[1], prefs)}', p.lines,
                calidad_ui.status_cell(status)))
        fit_columns(tree, self.app, stretch='name')
        CellTips(tree, self.app)
        tree.pack(fill='x')
        reasons = [f'{q.name}: {calidad_ui.status_cell(q.status)} — {q.status.reason}'
                   for q in (quality.points if quality is not None else []) if q.status.reason]
        if reasons:
            wrap_label(inner, '\n'.join(reasons), style='Card.Subtitle.TLabel', margin=40).pack(fill='x', pady=(8, 0))

    def lines_card(self, body, output):
        result, prefs = output['result'], self.app.settings.values
        net = result.network
        final = net.constrained or net.minimal
        quality = result.quality
        frame, inner = card(body, 'Líneas base', 'Elige una línea para dejarla fuera o decidir si es trivial: la red '
                                                 'se vuelve a ajustar sin procesar.')
        frame.pack(fill='x', pady=(16, 0))
        columns = [('line', 'Línea', 200), ('session', 'Sesión', 70), ('length', 'Longitud', 110),
                   ('solution', 'Solución', 90), ('sigma', 'σ H / V', 130), ('stat', 'Estadístico', 90),
                   ('role', 'Papel', 110), ('status', 'Estado', 110)]
        tree = ttk.Treeview(inner, columns=[c[0] for c in columns], show='headings', selectmode='browse',
                            height=min(14, max(1, len(result.lines))))
        for key, title, width in columns:
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=width, minwidth=60, anchor='w', stretch=key == 'line')
        calidad_ui.status_tags(tree, self.app)
        vectors = {v.id: v for v in net.vectors}
        residuals = {r.vector_id: r for r in final.residuals}
        statuses = {q.base_id: q.status for q in (quality.baselines if quality is not None else [])}
        from app.charts import enu_sigma
        for ln in result.lines:
            b, v, r = ln.baseline, vectors.get(ln.id), residuals.get(ln.id)
            sh, sv = enu_sigma(b.base.position, b.covariance_ecef)
            status = statuses.get(ln.id)
            role = ROLE_TEXT[self.role_of(v)] if v else '—'
            if self.job.line_choices.get(ln.id) in ('independent', 'trivial', 'off'):
                role += ' (elegido)'
            tree.insert('', 'end', iid=ln.id, tags=calidad_ui.tag_of(status), values=(
                ln.id, v.session if v else '—', format_length(b.length_m, prefs), solution_text(b.solution),
                f'{format_length(sh, prefs)} / {format_length(sv, prefs)}',
                f'{r.max_standardized:.2f}' + (' ▲' if r.flagged else '') if r else '—', role,
                calidad_ui.status_cell(status)))
        fit_columns(tree, self.app, stretch='line')
        CellTips(tree, self.app)
        tree.pack(fill='x')
        actions = ttk.Frame(inner, style='Flat.Card.TFrame')
        actions.pack(fill='x', pady=(8, 0))
        ttk.Label(actions, text='Línea elegida:', style='Card.TLabel').pack(side='left')
        for key, label in LINE_ACTIONS:
            ttk.Button(actions, text=label, style='Card.Link.TButton',
                       command=lambda k=key: self.choose_line(tree, k)).pack(side='left', padx=(8, 0))
        notes = [f'{v.id}: {v.note}' for v in net.vectors if v.note and v.trivial]
        notes += [f'Sin solución: {t}' for t in result.failed_lines]
        if notes:
            wrap_label(inner, '\n'.join(notes), style='Card.Subtitle.TLabel', margin=40).pack(fill='x', pady=(8, 0))

    def choose_line(self, tree, choice):
        selected = tree.selection()
        if not selected:
            self.app.set_status('Elige primero una línea de la tabla.')
            return
        line = selected[0]
        if choice:
            self.job.line_choices[line] = choice
        else:
            self.job.line_choices.pop(line, None)
        self.readjust()

    def loops_card(self, body, output):
        result, prefs = output['result'], self.app.settings.values
        net = result.network
        t = self.job.tolerances
        tol = (f'{format_length(t.max_loop_h_m, prefs)} + {t.max_loop_h_ppm:g} ppm en planta, '
               f'{format_length(t.max_loop_v_m, prefs)} + {t.max_loop_v_ppm:g} ppm en cota'
               if t.max_loop_h_m is not None else 'sin tolerancia')
        frame, inner = card(body, 'Cierres de bucle', f'Entre sesiones independientes · tolerancia {tol}.')
        frame.pack(fill='x', pady=(16, 0))
        if not net.loops:
            wrap_label(inner, 'No hay bucles entre líneas independientes: ninguna línea se comprueba con otras.',
                       style='Card.TLabel', margin=40).pack(fill='x')
        else:
            columns = [('loop', 'Bucle', 220), ('length', 'Longitud', 110), ('h', 'Cierre H', 100),
                       ('v', 'Cierre V', 100), ('ppm', 'ppm', 70), ('status', 'Estado', 110)]
            tree = ttk.Treeview(inner, columns=[c[0] for c in columns], show='headings', selectmode='none',
                                height=min(10, len(net.loops)))
            for key, title, width in columns:
                tree.heading(key, text=title, anchor='w')
                tree.column(key, width=width, minwidth=60, anchor='w', stretch=key == 'loop')
            calidad_ui.status_tags(tree, self.app)
            for lp in net.loops:
                tree.insert('', 'end', tags=calidad_ui.tag_of(lp.status), values=(
                    ' → '.join(lp.points), format_length(lp.length_m, prefs), format_length(lp.closure_h_m, prefs),
                    format_length(lp.closure_v_m, prefs), f'{lp.ppm_3d:.2f}', calidad_ui.status_cell(lp.status)))
            fit_columns(tree, self.app, stretch='loop')
            CellTips(tree, self.app)
            tree.pack(fill='x')
        if net.unchecked:
            wrap_label(inner, 'Ningún bucle comprueba: '+', '.join(net.unchecked)+'. Un error en ellas no se detecta.',
                       style='warning.TLabel', margin=40).pack(fill='x', pady=(8, 0))
