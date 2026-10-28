"""PPK de puntos (§1.2): base de una red CORS o propia, jornada cinemática + CSV de TresVizo Field, proceso y resultado."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from tresvizo_ppk import project as projects
from tresvizo_ppk.model import Ecef, Ephemeris, Solution

from app import tasks
from tresvizo_ppk.nombres import display_name
from app.charts import BarChart, PlanChart, enu_of
from app.components import DropZone, FileList, LabeledEntry, ThinScrollbar, card, wrap_label
from app.formatting import dms, horizontal, plural, solution_key, solution_text
from app.settings import format_length
from app.wizard import COMMON_FRAMES, PointsJob, classify, distance_m, epoch_text
from app.wizard_ui import (NETWORK_GUIDE, RINEX_TYPES, choice_text, AntennaPanel, BasePanel, OptionsPanel, WizardPage,
                           ecef_text, network_catalog, station_label)
from app.static_ui import RECORDING_TYPES
from app.export_ui import CUSTOM_CSV_LABEL, POINT_FORMATS, ExportActions, csv_options
from tresvizo_ppk.export.columnas import PPK_COLUMNS, QUALITY_COLUMNS
from app import calidad_ui

EXPORTS = POINT_FORMATS


class PointsPage(ExportActions, WizardPage):
    TITLE = 'PPK de puntos'
    ICON = 'route'
    JOB = PointsJob

    def default_name(self):
        if self.job.csv_file:
            return Path(self.job.csv_file).stem
        return super().default_name()

    def make_project(self, folder):
        return self.job.to_project(deepcopy(self.project) if self.project else None, folder)

    # --- Paso 1: base -------------------------------------------------------------------------------------------

    def build_step(self, index, body):
        if index == 0:
            self.build_base(body)
        elif index == 1:
            self.build_rover(body)
        elif index == 2:
            OptionsPanel(body, self.app, self.job, self.changed, self.quality_changed).pack(fill='x')

    def build_base(self, body):
        frame, inner = card(body, 'Base', 'Una estación de referencia de una red o tu propio MeridianV grabando como '
                                          'base.')
        frame.pack(fill='x')
        self.kind_var = tk.StringVar(value=self.job.base_kind)
        for value, text in (('rgna', 'Estación de una red'), ('own', 'Base propia (MeridianV)')):
            ttk.Radiobutton(inner, text=text, value=value, variable=self.kind_var, style='Card.TRadiobutton',
                            command=self.switch_base).pack(side='left', padx=(0, 18))
        self.rgna_frame = ttk.Frame(body)
        guide, g = card(self.rgna_frame, None)
        guide.pack(fill='x', pady=(16, 0))
        wrap_label(g, NETWORK_GUIDE, style='Card.Subtitle.TLabel', margin=40).pack(fill='x')
        self.rgna_panel = BasePanel(self.rgna_frame, self.app, self.job.base, 'Estación de referencia', self.changed,
                                    guide=self.network_guide, inspect=self.inspect, options=self.job.options,
                                    role='base')
        self.rgna_panel.pack(fill='x', pady=(16, 0))
        self.own_frame = ttk.Frame(body)
        self.build_own(self.own_frame)
        self.switch_base(notify=False)

    def build_own(self, parent):
        frame, inner = card(parent, 'Grabación de la base', 'La sesión del receptor que estuvo de base (o su RINEX).')
        frame.pack(fill='x', pady=(16, 0))
        DropZone(inner, self.app, 'Arrastra aquí la grabación de la base', 'Una grabación .bin/.part o sus RINEX.',
                 self.add_own_files, RECORDING_TYPES).pack(fill='x')
        self.own_list = FileList(inner, self.app, self.set_own_files)
        self.own_info = ttk.Label(inner, text='', style='Card.Subtitle.TLabel', justify='left')
        inner.bind('<Configure>', lambda e: self.own_info.configure(wraplength=max(200, e.width-20)), add='+')
        frame, inner = card(parent, 'Datos de la base')
        frame.pack(fill='x', pady=(16, 0))
        inner.columnconfigure((0, 1), weight=1, uniform='own')
        self.own_name = tk.StringVar(value=self.job.own_name)
        self.own_height = tk.StringVar(value=self.job.own_height)
        self.own_name_field = LabeledEntry(inner, 'Nombre de la base', self.own_name)
        self.own_name_field.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        self.own_height_field = LabeledEntry(inner, 'Altura del bastón o trípode (m)', self.own_height,
                                             'Vertical, del punto a la rosca del receptor.')
        self.own_height_field.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
        self.own_name.trace_add('write', lambda *a: self.edit(own_name=self.own_name.get()))
        self.own_height.trace_add('write', lambda *a: self.edit(own_height=self.own_height.get()))
        self.own_antenna = AntennaPanel(inner, self.app, self.job.options, 'base', self.changed,
                                        height_var=self.own_height)
        self.own_antenna.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(16, 0))
        frame, inner = card(parent, 'Coordenada de la base',
                            'Por omisión, la aproximada que trae la grabación. Para ligar los puntos a una red, '
                            'georreferencia antes la base con «Georreferenciar punto» y elige ese proyecto.')
        frame.pack(fill='x', pady=(16, 0))
        self.source_var = tk.StringVar(value=self.job.own_source)
        from app.components import FlowFrame
        self.source_row = FlowFrame(inner, style='Flat.Card.TFrame', gap=18)
        self.source_row.pack(fill='x')
        self.from_rinex = ttk.Label(inner, text='', style='Card.TLabel', justify='left', padding=(0, 10, 0, 0))
        inner.bind('<Configure>', lambda e: self.from_rinex.configure(wraplength=max(200, e.width-20)), add='+')
        self.from_project = ttk.Frame(inner, style='Flat.Card.TFrame', padding=(0, 10, 0, 0))
        pick = ttk.Button(self.from_project, text='Elegir proyecto…', command=self.pick_static_project)
        self.app.icons.bind(pick, 'folder-open')
        pick.pack(side='left')
        self.project_info = ttk.Label(self.from_project, text='', style='Card.Subtitle.TLabel', justify='left')
        self.project_info.pack(side='left', padx=12, fill='x', expand=True)
        self.manual = ttk.Frame(inner, style='Flat.Card.TFrame', padding=(0, 10, 0, 0))
        self.format_var = tk.StringVar(value=self.job.coord_format)
        fmt = ttk.Frame(self.manual, style='Flat.Card.TFrame')
        fmt.pack(fill='x')
        for value, text in (('geodetic', 'Latitud, longitud y altura elipsoidal'), ('ecef', 'X, Y, Z geocéntricas')):
            ttk.Radiobutton(fmt, text=text, value=value, variable=self.format_var, style='Card.TRadiobutton',
                            command=self.switch_format).pack(side='left', padx=(0, 18))
        self.coord_vars = [tk.StringVar(value=v) for v in self.job.coord_text]
        self.coord_entries = ttk.Frame(self.manual, style='Flat.Card.TFrame')
        self.coord_entries.pack(fill='x', pady=(8, 0))
        self.coord_entries.columnconfigure((0, 1, 2), weight=1, uniform='xyz')
        self.coord_labels = []
        for column, var in enumerate(self.coord_vars):
            label = ttk.Label(self.coord_entries, text='', style='Card.Strong.TLabel')
            label.grid(row=0, column=column, sticky='w', padx=(0, 12), pady=(0, 4))
            ttk.Entry(self.coord_entries, textvariable=var).grid(row=1, column=column, sticky='ew', padx=(0, 12))
            var.trace_add('write', lambda *a: self.edit(coord_text=tuple(v.get() for v in self.coord_vars)))
            self.coord_labels.append(label)
        self.coord_hint = ttk.Label(self.manual, text='', style='Card.Subtitle.TLabel', justify='left')
        self.coord_hint.pack(anchor='w', fill='x', pady=(6, 0))
        self.manual.bind('<Configure>', lambda e: self.coord_hint.configure(wraplength=max(200, e.width-20)),
                         add='+')
        # Marco y época de la coordenada: se guardan con ella y salen en el informe; no se transforman.
        frame_row = ttk.Frame(self.manual, style='Flat.Card.TFrame')
        frame_row.pack(fill='x', pady=(10, 0))
        ttk.Label(frame_row, text='Marco', style='Card.Strong.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 4))
        ttk.Label(frame_row, text='Época (año decimal)', style='Card.Strong.TLabel').grid(row=0, column=1, sticky='w',
                                                                                         padx=(12, 0), pady=(0, 4))
        self.frame_var = tk.StringVar(value=self.job.coord_frame)
        self.epoch_var = tk.StringVar(value=self.job.coord_epoch)
        self.frame_box = ttk.Combobox(frame_row, textvariable=self.frame_var, values=self.frame_choices(), width=18)
        self.frame_box.grid(row=1, column=0, sticky='w')
        ttk.Entry(frame_row, textvariable=self.epoch_var, width=10).grid(row=1, column=1, sticky='w', padx=(12, 0))
        self.frame_box.bind('<<ComboboxSelected>>', lambda e: self.frame_picked())
        self.frame_var.trace_add('write', lambda *a: self.edit(coord_frame=self.frame_var.get()))
        self.epoch_var.trace_add('write', lambda *a: self.edit(coord_epoch=self.epoch_var.get()))
        network_catalog(self.app, self.networks_loaded)
        self.switch_format(notify=False)
        self.render_candidates()
        self.switch_source(notify=False)
        # Al final: si los archivos ya se leyeron, el resultado llega en el acto y llena los campos de arriba.
        if self.job.base_kind == 'own':
            self.set_own_files(self.job.base.files, notify=False)

    def edit(self, **values):
        for key, value in values.items():
            setattr(self.job, key, value)
        if {'coord_text', 'coord_format', 'coord_frame', 'coord_epoch'} & set(values):
            self.update_network_frame()
        self.changed()

    # --- Marco de la coordenada escrita a mano ----------------------------------------------------------------

    NETWORK_RADIUS_M = 500_000.0      # una red cuenta como «la de la zona» si tiene una estación a menos de esto

    def frame_choices(self) -> list[str]:
        """Los marcos de las redes instaladas primero y luego los usuales; se puede escribir otro."""
        found = [f'{n.frame}' for n in (self.app.networks_cache or []) if n.frame]
        return list(dict.fromkeys([*found, *COMMON_FRAMES]))

    def networks_loaded(self, networks):
        if hasattr(self, 'frame_box') and self.frame_box.winfo_exists():
            self.frame_box.configure(values=self.frame_choices())
            self.update_network_frame()
            self.refresh()

    def frame_picked(self):
        """Al elegir el marco de una red instalada, su época oficial (se puede corregir)."""
        frame = self.frame_var.get()
        net = next((n for n in (self.app.networks_cache or []) if n.frame == frame and n.epoch is not None), None)
        if net is not None:
            self.epoch_var.set(epoch_text(net.epoch))

    def update_network_frame(self):
        """La red con la estación más cercana a la coordenada escrita (a menos de 500 km): su marco y su época."""
        from app.networks import network_of
        self.job.network_frame = None
        core = self.app.backend.core
        if self.job.own_source != 'manual' or core is None or not callable(getattr(core, 'nearest_stations', None)):
            return
        try:
            xyz = self.job.manual_xyz()
            found = list(core.nearest_stations(Ecef(*xyz), 1))
        except Exception:  # noqa: BLE001 - el aviso es una ayuda; la validación de la coordenada sigue
            return
        if not found or found[0][1] > self.NETWORK_RADIUS_M:
            return
        net = network_of(self.app.networks_cache or [], found[0][0])
        if net is not None and net.frame:
            self.job.network_frame = (net.name, net.frame, net.epoch)

    def switch_base(self, notify=True):
        kind = self.kind_var.get()
        if kind != self.job.base_kind:
            # Cada tipo de base tiene sus propios archivos: no se mezclan al cambiar.
            self.job.base.files = []
            self.job.base.station = None
            self.job.base_kind = kind
            self.rgna_panel.set_files([])
            self.rgna_panel.picker.show()
            self.own_list.set([])
            self.own_list.pack_forget()
        self.rgna_frame.pack_forget()
        self.own_frame.pack_forget()
        (self.rgna_frame if kind == 'rgna' else self.own_frame).pack(fill='x')
        if notify:
            self.changed()

    def add_own_files(self, paths):
        self.set_own_files(list(dict.fromkeys([*self.job.base.files, *paths])))

    def set_own_files(self, paths, notify=True):
        # Al reabrir un proyecto se vuelve a elegir la candidata que se guardó (no la primera).
        self._wanted_position = self.job.rinex_position if not notify else None
        self._station_before = self.station_or_none()
        self.job.base.files = list(paths)
        self.own_list.set(paths)
        if paths and not self.own_list.winfo_manager():
            self.own_list.pack(fill='x', pady=(10, 0))
        elif not paths:
            self.own_list.pack_forget()
        self.job.rinex_position, self.job.rinex_missing = None, False
        self.own_info.pack_forget()
        self.job.candidates = []
        if paths:
            core = self.app.backend.core

            def candidates(obs, folder, cancel):
                if not callable(getattr(core, 'base_coordinate_candidates', None)):
                    return None
                try:
                    return core.base_coordinate_candidates(obs, folder, cancel=cancel)
                except Exception as exc:  # noqa: BLE001 - sin candidatas se elige proyecto o punto conocido
                    if tasks.classify(exc)[0] == 'program':
                        raise
                    return []
            self.inspect('own', paths, self.own_inspected, extra=candidates)
        if notify:
            self.changed()

    def own_inspected(self, obs):
        origin = self.origin_of(self.job.base.files)
        self.autofill(self.own_name, self.own_name_field, getattr(obs, 'point_name', '') or obs.marker,
                      self.origin_text(obs, 'point_name', origin))
        pole = self.pole_from(obs)
        self.own_antenna.set_data(obs.antenna_type, obs.radome, case_offset=obs.case_offset_m,
                                  recording=origin.startswith('de la sesión') or pole is not None)
        if origin.startswith('de la sesión') or pole is not None:
            self.own_height_field.title.configure(text='Altura del bastón o trípode (m)')
            self.own_height_field.hint.configure(text='Vertical, del punto a la rosca del receptor.')
            self.autofill(self.own_height, self.own_height_field, f'{pole:.3f}' if pole is not None else None,
                          self.origin_text(obs, 'pole_height_m', origin))
        else:
            # RINEX de otro receptor: la altura que cuenta es la de la marca a la ARP, y la trae el encabezado.
            self.own_height_field.title.configure(text='Altura de la antena a la ARP (m)')
            self.own_height_field.hint.configure(text='Vertical, del punto a la referencia de la antena (ARP), como '
                                                      'en el encabezado RINEX.')
            self.autofill(self.own_height, self.own_height_field, f'{obs.antenna_height_m:.4f}',
                          self.origin_text(obs, 'antenna_height_m', origin))
        before = getattr(self, '_station_before', None)
        self._station_before = None
        self.job.own_receiver = obs.receiver_model or obs.receiver or ''
        found = getattr(obs, 'ui_extra', None)
        wanted = getattr(self, '_wanted_position', None)
        self._wanted_position = None
        if found is None:      # motor sin candidatas: la del encabezado
            self.job.candidates = []
            self.job.rinex_position = obs.approx_position
        else:
            self.job.candidates = list(found)
            self.job.candidate_index = next((i for i, c in enumerate(found) if wanted is not None and
                                             distance_m(c.position, wanted) < 1e-4), 0)
            self.job.rinex_position = found[self.job.candidate_index].position if found else None
        self.job.rinex_missing = self.job.rinex_position is None
        self.render_candidates()
        self.own_info.configure(text=f'{self.obs_summary(obs)} · {origin}')
        self.own_info.pack(fill='x', pady=(8, 0))
        self.show_rinex_position()
        # Leer los archivos de un proyecto recién abierto no es un cambio: solo si la base que se procesaría cambió.
        saved = self.project.bases[0] if self.project is not None and self.project.bases and not self.dirty else None
        if self.station_or_none() not in (before, saved):
            self.changed()
        else:
            self.refresh()

    def station_or_none(self):
        try:
            return self.job.station()
        except (ValueError, TypeError, AttributeError):
            return None

    CANDIDATE_TEXT = {'official': 'Oficial (catálogo de su red)', 'rinex-header': 'Aproximada (encabezado RINEX)',
                      'autonomous': 'Aproximada (solución autónoma de la sesión)'}

    def render_candidates(self):
        """Una opción por candidata del motor (la primera, por omisión) + proyecto + punto conocido."""
        from app.components import FlowFrame
        self.source_row.clear()
        options = [(f'rinex:{i}', self.CANDIDATE_TEXT.get(c.coordinate_origin, c.source or 'Del archivo'))
                   for i, c in enumerate(self.job.candidates)] or [('rinex:0', 'Aproximada (encabezado RINEX)')]
        options += [('project', 'Georreferenciada (proyecto)'), ('manual', 'Punto conocido')]
        current = (f'rinex:{self.job.candidate_index}' if self.job.own_source == 'rinex' else self.job.own_source)
        self.source_var.set(current)
        for value, text in options:
            self.source_row.add(ttk.Radiobutton(self.source_row, text=text, value=value, variable=self.source_var,
                                                style='Card.TRadiobutton', command=self.switch_source))
        self.app.theme.walk(self.source_row)
        self.show_rinex_position()

    def show_rinex_position(self):
        if not hasattr(self, 'from_rinex'):
            return
        p = self.job.rinex_position
        if p is None:
            text = ('La grabación no trae posición aproximada: elige otra fuente.' if self.job.rinex_missing else
                    'Se toma del encabezado al leer la grabación de la base.')
        else:
            from app.charts import ecef_to_geodetic
            from app.formatting import dms
            lat, lon = ecef_to_geodetic(p)
            chosen = (self.job.candidates[min(self.job.candidate_index, len(self.job.candidates)-1)]
                      if self.job.candidates else None)
            note = (chosen.source if chosen is not None else 'Aproximada, del encabezado RINEX (unos metros): los '
                    'puntos quedan bien entre sí, pero desplazados en bloque y sin ligar a ninguna red.')
            text = f'{dms(lat, "N", "S", 3)}  {dms(lon, "E", "O", 3)} · {p.frame} {p.epoch:g}\n{ecef_text(p)}\n{note}'
        self.from_rinex.configure(text=text)

    def switch_source(self, notify=True):
        value = self.source_var.get()
        if value.startswith('rinex'):
            self.job.own_source = 'rinex'
            self.job.candidate_index = int(value.split(':')[1]) if ':' in value else 0
            if self.job.candidates:
                self.job.rinex_position = self.job.candidates[self.job.candidate_index].position
        else:
            self.job.own_source = value
        for frame in (self.from_rinex, self.from_project, self.manual):
            frame.pack_forget()
        {'rinex': self.from_rinex, 'project': self.from_project}.get(self.job.own_source, self.manual).pack(fill='x')
        self.show_project_info()
        self.show_rinex_position()
        self.update_network_frame()
        if notify:
            self.changed()

    def switch_format(self, notify=True):
        self.job.coord_format = self.format_var.get()
        if self.job.coord_format == 'geodetic':
            names = ('Latitud', 'Longitud', 'Altura elipsoidal (m)')
            hint = ('Grados decimales o «19 25 12.3456 N»; longitud oeste con O (o W) o con signo menos. Abajo, el marco y '
                    'la época en que está la coordenada: para ligar los puntos a una red, los de esa red.')
        else:
            names = ('X (m)', 'Y (m)', 'Z (m)')
            hint = ('Geocéntricas. Abajo, el marco y la época en que está la coordenada: para ligar los puntos a una red, '
                    'los de esa red.')
        for label, name in zip(self.coord_labels, names):
            label.configure(text=name)
        self.coord_hint.configure(text=hint)
        if notify:
            self.changed()

    def pick_static_project(self):
        path = filedialog.askopenfilename(parent=self.app, title='Proyecto de la base',
                                          initialdir=str(self.app.settings.projects_dir()),
                                          filetypes=[('Proyecto de TresVizo Field PPK', '*.tvppk')])
        if not path:
            return
        try:
            project = projects.load(Path(path))
        except projects.ProjectError as exc:
            self.app.show_error('user', str(exc))
            return
        if project.kind != 'static':
            self.app.show_error('user', 'Ese proyecto no es de «Georreferenciar punto».')
            return
        self.job.own_project = Path(path)
        self.job.own_position = project.position
        if not self.own_name.get().strip() and project.point_name:
            self.own_name.set(project.point_name)
        if not self.own_height.get().strip() and project.pole_height_m:
            self.own_height.set(f'{project.pole_height_m:.3f}')
        self.show_project_info()
        self.changed()

    def show_project_info(self):
        if not hasattr(self, 'project_info'):
            return
        if self.job.own_project is None:
            self.project_info.configure(text='Ningún proyecto elegido.')
        elif self.job.own_position is None:
            self.project_info.configure(text=f'{Path(self.job.own_project).name}: todavía sin coordenada final.')
        else:
            self.project_info.configure(text=f'{Path(self.job.own_project).name}\n{ecef_text(self.job.own_position)} '
                                             f'({self.job.own_position.frame} {self.job.own_position.epoch:g})')

    # --- Paso 2: rover y puntos ---------------------------------------------------------------------------------

    def build_rover(self, body):
        frame, inner = card(body, 'Jornada del rover', 'La grabación cinemática de toda la jornada (o su RINEX).')
        frame.pack(fill='x')
        DropZone(inner, self.app, 'Arrastra aquí la grabación de la jornada', 'Una grabación .bin/.part o sus RINEX.',
                 self.add_rover, RECORDING_TYPES).pack(fill='x')
        self.rover_list = FileList(inner, self.app, self.set_rover)
        self.rover_info = ttk.Label(inner, text='', style='Card.Subtitle.TLabel', justify='left')
        inner.bind('<Configure>', lambda e: self.rover_info.configure(wraplength=max(200, e.width-20)), add='+')
        self.rover_antenna = AntennaPanel(inner, self.app, self.job.options, 'rover', self.changed)
        self.rover_antenna.pack(fill='x', pady=(12, 0))
        self.set_rover(self.job.rover_files, notify=False)
        frame, inner = card(body, 'Puntos de TresVizo Field',
                            'El CSV que exportó la app de esa jornada: nombre, código, inicio y fin de cada ocupación y '
                            'altura de antena. En posproceso todo se toma plomeado.')
        frame.pack(fill='x', pady=(16, 0))
        DropZone(inner, self.app, 'Arrastra aquí el CSV de puntos', 'Exportado desde TresVizo Field (iOS o Android).',
                 self.set_csv, [('CSV de TresVizo Field', '*.csv'), ('Todos los archivos', '*')], multiple=False,
                 button='Elegir CSV').pack(fill='x')
        self.csv_info = ttk.Label(inner, text='', style='Card.TLabel', justify='left')
        self.csv_info.pack(fill='x', pady=(10, 0))
        inner.bind('<Configure>', lambda e: self.csv_info.configure(wraplength=max(200, e.width-20)), add='+')
        if self.job.csv_file:
            self.set_csv([self.job.csv_file], notify=False)

    def add_rover(self, paths):
        projects_dropped = [p for p in paths if classify(p) == 'project']
        if projects_dropped:
            self.app.open_project(projects_dropped[0])
            return
        csvs = [p for p in paths if classify(p) == 'csv']
        if csvs:
            self.set_csv(csvs[:1])
        rest = [p for p in paths if classify(p) != 'csv']
        if rest:
            self.set_rover(list(dict.fromkeys([*self.job.rover_files, *rest])))

    def set_rover(self, paths, notify=True):
        self.job.rover_files = list(paths)
        self.rover_list.set(paths)
        if paths and not self.rover_list.winfo_manager():
            self.rover_list.pack(fill='x', pady=(10, 0))
        elif not paths:
            self.rover_list.pack_forget()
        self.rover_info.pack_forget()
        if paths:
            def shown(obs, files=list(paths)):
                # la altura del rover es la de cada punto del CSV; de aquí solo cuenta la antena
                self.rover_antenna.set_data(obs.antenna_type, obs.radome)
                approx = self.approx_text(obs)
                self.rover_info.configure(text=f'{self.obs_summary(obs)} · {self.origin_of(files)}'
                                               + (f'\n{approx}' if approx else ''))
                self.rover_info.pack(fill='x', pady=(8, 0))
            self.inspect('rover', paths, shown)
        if notify:
            self.changed()

    def set_csv(self, paths, notify=True):
        path = Path(paths[0]) if paths else None
        self.job.csv_file = path
        self.csv_points = None
        if notify:
            self.changed()
        if path is None:
            self.csv_info.configure(text='')
            return
        core = self.app.backend.core
        if core is None or not callable(getattr(core, 'read_field_csv', None)):
            self.csv_info.configure(text=f'{path.name}')
            return
        self.csv_info.configure(text=f'{path.name} · leyendo…')

        def done(points):
            if self.job.csv_file != path:
                return
            self.csv_points = points
            if not points:
                self.csv_info.configure(text=f'{path.name}: no trae puntos.')
                return
            start = min(p.start for p in points)
            end = max(p.end for p in points)
            self.csv_info.configure(text=f'{path.name} · {len(points)} puntos · {start:%d/%m/%Y %H:%M:%S} a '
                                         f'{end:%H:%M:%S} UTC')

        def failed(kind, message, detail):
            if self.job.csv_file == path:
                self.csv_info.configure(text=f'{path.name}: {message}')
                if kind == 'program':
                    tasks.log_error(self.app.settings.folder, 'Lectura del CSV', detail)
        self.run_task(lambda p, c: core.read_field_csv(path), on_done=done, on_error=failed)

    # --- Proceso ------------------------------------------------------------------------------------------------

    def summary_lines(self):
        job = self.job
        if job.base_kind == 'rgna':
            base = (f'Estación de referencia '
                    f'{station_label(replace(job.base.station, name=job.base.name or job.base.station.name))}'
                    if job.base.station else 'Estación de referencia: —')
        else:
            if job.own_source == 'project' and job.own_project:
                origin = f'del proyecto {Path(job.own_project).name}'
            elif job.own_source == 'rinex':
                chosen = job.candidates[min(job.candidate_index, len(job.candidates)-1)] if job.candidates else None
                text = self.CANDIDATE_TEXT.get(getattr(chosen, 'coordinate_origin', ''), 'Aproximada (encabezado RINEX)')
                origin = text[:1].lower()+text[1:]
            else:
                origin = 'escrita a mano'
            base = f'Base propia {job.own_name or "—"}, altura {job.own_height or "—"} m, coordenada {origin}'
        o = job.options
        lines = [base+f' · {plural(len(job.base.files), "archivo")}', f'Rover: {plural(len(job.rover_files), "archivo")}',
                 f'Puntos: {Path(job.csv_file).name if job.csv_file else "—"}'
                 + (f' ({len(self.csv_points)} puntos)' if getattr(self, 'csv_points', None) else ''),
                 f'Proceso: cinemático, {"combinado adelante/atrás" if o.combined else "adelante"}, máscara '
                 f'{o.elevation_mask_deg:g}°, efemérides {"precisas" if o.ephemeris is Ephemeris.PRECISE else "difundidas"}',
                 'Cada punto: promedio de las épocas fijas de su ocupación (o flotantes, marcadas), todo plomeado.',
                 f'Resultado en {choice_text(self.app, job.coordinates)}']
        if self.path:
            lines.append(f'Carpeta de trabajo: {projects.workdir(self.project or self.make_project(self.path.parent), self.path)}')
        return lines

    def work(self, job, project, workdir, progress, cancel):
        core = self.app.backend.core
        station = job.station()
        stages = tasks.Stages(progress, [3, 2, 1, 1, 8, 2])
        rover = core.prepare_observations(job.rover_files, workdir/'rover', progress=stages.stage(0), cancel=cancel)
        base_obs = core.prepare_observations(job.base.files, workdir/'base', pole_height_m=job.base_height_m(),
                                             marker=station.id, window=self.base_window(rover),
                                             progress=stages.stage(1), cancel=cancel)
        stages.stage(2)(0.0, 'Leyendo los puntos de TresVizo Field…')
        points = core.read_field_csv(job.csv_file)
        options = deepcopy(job.options)
        if options.ephemeris is Ephemeris.PRECISE and not options.precise_files and job.precise_download:
            options.precise_files = list(core.download_precise_products(rover.start, rover.end, workdir/'efemerides',
                                                                        stages.stage(3), cancel))
        result = core.process_points(rover, (station, base_obs), points, options, workdir, stages.stage(4), cancel)
        final = stages.stage(5)
        coordinates, warnings = [], []
        for index, point in enumerate(result.points):
            final(index/max(1, len(result.points)), 'Pasando los puntos al sistema elegido…')
            if point.position is None:
                coordinates.append(None)
                continue
            coordinate, warning = self.transform(point.position, job.coordinates)
            coordinates.append(coordinate)
            if warning and warning not in warnings:
                warnings.append(warning)
        final(1.0, 'Listo.')
        return {'result': result, 'coordinates': coordinates, 'coordinate_warnings': warnings,
                'choice': job.coordinates}

    def session_window(self, callback):
        """Con el CSV ya leído, el día y las horas salen de las ocupaciones (sin leer la grabación)."""
        points = getattr(self, 'csv_points', None)
        if points:
            from datetime import timedelta
            callback(min(p.start for p in points)-timedelta(minutes=30), max(p.end for p in points)+timedelta(minutes=30))
            return
        super().session_window(callback)

    def make_report(self, report, output, project, user, out):
        return report.points_report(output['result'], project, user, out)

    EXPORT_STEM = 'puntos'

    def export_button(self, parent):
        button = ttk.Menubutton(parent, text='Exportar')
        self.app.icons.bind(button, 'share')
        menu = tk.Menu(button, tearoff=False)
        for formato, label, suffix, kind in EXPORTS:
            menu.add_command(label=label, command=lambda f=formato, s=suffix, k=kind: self.export_from_menu(f, s, k))
        menu.add_command(label=CUSTOM_CSV_LABEL, command=self.export_custom_csv)
        menu.add_separator()
        menu.add_command(label='Exportar trayectoria (.pos de RTKLIB)…', command=lambda: self.export_trajectory('pos'))
        menu.add_command(label='Exportar trayectoria (CSV)…', command=lambda: self.export_trajectory('csv'))
        self.add_rinex_item(menu)
        button.configure(menu=menu)
        if not self.app.backend.available('export'):
            button.state(['disabled'])
        return button

    def export(self, formato, suffix, kind, csv=None, dxf=None):
        export = self.app.backend.export
        if export is None or self.output is None:
            return
        target = self.ask_export_file('Exportar puntos', suffix, kind, f'{self.export_stem()} posproceso{suffix}')
        if not target:
            return
        result, choice = self.output['result'], self.output['choice']
        project = self.project.name if self.project else ''
        options = {} if csv is None else {'csv_options': csv_options(export, csv)}
        if formato in ('dxf', 'landxml'):
            options['project'] = project
        if formato == 'dxf':
            options['dxf_options'] = self.export_dxf_options(dxf)
        self.run_export(lambda p, c: export.export_points(result, choice, formato, Path(target), **options),
                        lambda path: f'Puntos exportados: {path.name}.')

    def export_custom_csv(self):
        options = self.ask_csv_options(list(PPK_COLUMNS)+list(QUALITY_COLUMNS))
        if options is not None:
            self.export('csv', '.csv', 'CSV', csv=options)

    def export_trajectory(self, formato):
        export = self.app.backend.export
        if export is None or self.output is None:
            return
        suffix, kind = ('.pos', 'Trayectoria de RTKLIB') if formato == 'pos' else ('.csv', 'CSV')
        target = self.ask_export_file('Exportar trayectoria', suffix, kind, f'{self.export_stem()} trayectoria{suffix}')
        if not target:
            return
        result, choice = self.output['result'], self.output['choice']
        self.run_export(lambda p, c: export.export_trajectory(result, choice, formato, Path(target), progress=p,
                                                              cancel=c),
                        lambda path: f'Trayectoria exportada: {path.name}.')

    # --- Resultado ----------------------------------------------------------------------------------------------

    def evaluate_quality(self, output):
        """Las tolerancias, el control y la ocupación elegida de la pantalla contra el resultado: un resultado por
        nombre con su estado, y su coordenada en el sistema elegido."""
        from tresvizo_ppk import calidad
        result, job = output['result'], self.job
        result.quality = calidad.evaluate_points(result, job.tolerances, job.control_points, job.occupation_choices,
                                                 output['choice'], job.control_source)
        known = {id(p): c for p, c in zip(result.points, output['coordinates'])}
        coordinates = []
        for pq in result.quality.points:
            p = pq.point
            if id(p) in known:
                coordinates.append(known[id(p)])
            elif p.position is None:
                coordinates.append(None)
            else:
                coordinate, _ = self.transform(p.position, output['choice'])
                coordinates.append(coordinate)
        output['effective'] = list(zip(result.quality.points, coordinates))

    def choose_occupation(self, name, index):
        self.job.occupation_choices[name] = index
        self.quality_changed()

    def result_view(self, body, output):
        result, prefs = output['result'], self.app.settings.values
        quality = result.quality
        entries = output.get('effective') or [(None, c) for c in output['coordinates']]
        points = [pq.point for pq, _ in entries] if quality is not None else list(result.points)
        coordinates = [c for _, c in entries]
        counts = {s: sum(1 for p in points if Solution(p.solution) is s) for s in Solution}
        fixed, floating = counts[Solution.FIXED], counts[Solution.FLOAT]
        none = counts[Solution.NONE]+counts[Solution.SINGLE]
        # Éxito con todos fijos; advertencia si hay flotantes o sin solución; error solo si no hay ningún fijo.
        kind = 'success' if none == 0 and floating == 0 else 'error' if fixed == 0 else 'warning'
        top = ttk.Frame(body)
        top.pack(fill='x')
        top.columnconfigure(0, weight=2, uniform='top')
        top.columnconfigure(1, weight=3, uniform='top')
        frame, inner = card(top, 'Resumen de la jornada')
        frame.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        wrap_label(inner, f'{fixed} FIJOS · {floating} FLOTANTES · {none} SIN SOLUCIÓN', style=kind+'.TLabel',
                   margin=40).pack(fill='x')
        lines = [f'Épocas fijas de la trayectoria: {result.fix_rate*100:.1f} %',
                 f'Base: {station_label(result.base)} ({"oficial" if result.base.official else result.base.source})',
                 f'{len(points)} puntos del CSV' + (f' ({len(result.points)} ocupaciones)'
                                                    if len(points) != len(result.points) else '')]
        q = result.rover_quality
        lines.append(f'Rover: {plural(q.epochs, "época")} · {plural(len(q.gaps), "hueco")} · '
                     f'{plural(len(q.satellites), "satélite")}')
        for line in lines:
            wrap_label(inner, line, style='Card.TLabel', margin=40).pack(fill='x', pady=(6, 0))
        frame, inner = card(top, 'Puntos en planta')
        frame.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
        plan = PlanChart(inner, self.app, height=300)
        plan.pack(fill='both', expand=True)
        located = [p for p in points if p.position is not None]
        items = []
        if located:
            origin = located[0].position
            for p in located:
                e, n, _ = enu_of(origin, p.position)
                items.append({'e': e, 'n': n, 'kind': 'point', 'label': p.point.name,
                              'solution': solution_key(p.solution)})
            e, n, _ = enu_of(origin, result.base.position)
            spread = max(max(abs(i['e']) for i in items), max(abs(i['n']) for i in items), 1.0)
            if abs(e) < spread*4 and abs(n) < spread*4:
                items.append({'e': e, 'n': n, 'kind': 'base', 'label': result.base.id})
        plan.set(items)
        if quality is not None:
            calidad_ui.summary_card(body, self.app, quality).pack(fill='x', pady=(16, 0))
        frame, inner = card(body, 'Puntos')
        frame.pack(fill='x', pady=(16, 0))
        projected = any(c is not None and c.northing_m is not None for c in coordinates)
        first, second = ('Este', 'Norte') if projected else ('Latitud', 'Longitud')
        height_title = 'Cota' if any(c is not None and c.orthometric_height_m is not None for c in coordinates) else \
            'Altura elip.'
        # Lo esencial cabe a 1280 px; la distancia a la base y el aviso de cada punto salen al seleccionarlo.
        columns = [('name', 'Punto', 80), ('code', 'Código', 70), ('a', first, 125), ('b', second, 135),
                   ('h', height_title, 100), ('sh', 'σ H', 70), ('sv', 'σ V', 70), ('epochs', 'Épocas', 65),
                   ('solution', 'Solución', 110), ('status', 'Estado', 105), ('delta', 'Δ campo H/V', 135)]
        tree = ttk.Treeview(inner, columns=[c[0] for c in columns], show='headings',
                            height=min(14, max(3, len(points))), selectmode='browse')
        for key, title, width in columns:
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=width, minwidth=width*3//4, anchor='w', stretch=True)
        # Solo se resaltan las filas que piden revisión (por su estado contra las tolerancias, o por su solución si no
        # hay evaluación); el estado y la solución siempre van escritos.
        tokens = self.app.theme.tokens
        tree.tag_configure('float', foreground=tokens['warning_fg'], background=tokens['warning_bg'])
        for key in ('single', 'none'):
            tree.tag_configure(key, foreground=tokens['error_fg'], background=tokens['error_bg'])
        calidad_ui.status_tags(tree, self.app)
        statuses = [pq.status if pq is not None else None for pq, _ in entries]
        for index, (p, c) in enumerate(zip(points, coordinates)):
            if c is not None and projected and c.northing_m is not None:
                a, b = format_length(c.easting_m, prefs), format_length(c.northing_m, prefs)
            elif c is not None:
                a, b = dms(c.latitude_deg, 'N', 'S', 4), dms(c.longitude_deg, 'E', 'O', 4)
            else:
                a = b = '—'
            h = '—' if c is None else format_length(c.orthometric_height_m if c.orthometric_height_m is not None else
                                                   c.ellipsoidal_height_m, prefs)
            delta = ('—' if p.delta_from_field_enu is None else
                     f'{format_length(horizontal(p.delta_from_field_enu), prefs)} / '
                     f'{format_length(p.delta_from_field_enu.u, prefs)}')
            status = statuses[index]
            tags = calidad_ui.tag_of(status) if status is not None else (solution_key(p.solution),)
            tree.insert('', 'end', iid=str(index), tags=tags, values=(
                p.point.name, p.point.code, a, b, h, format_length(horizontal(p.sigma_enu), prefs),
                format_length(None if p.sigma_enu is None else p.sigma_enu.u, prefs),
                f'{p.epochs_fixed}/{p.epochs_total}', solution_text(p.solution), calidad_ui.status_cell(status),
                delta))
        from app.components import CellTips, fit_columns
        fit_columns(tree, self.app, stretch='name')
        CellTips(tree, self.app)
        tree.pack(fill='x')
        scroll = ThinScrollbar(inner, tree.xview, orient='horizontal')
        tree.configure(xscrollcommand=scroll.set)
        scroll.pack(fill='x', pady=(3, 0))
        point_detail = ttk.Label(inner, text='Selecciona un punto para ver su estado, su distancia a la base y sus '
                                             'avisos.',
                                 style='Card.Subtitle.TLabel', justify='left')
        point_detail.pack(fill='x', pady=(6, 0))

        def selected(event=None):
            chosen = tree.selection()
            if not chosen:
                return
            index = int(chosen[0])
            p = points[index]
            parts = [f'{p.point.name}: a {format_length(p.distance_to_base_m, prefs, 0)} de la base'
                     if p.distance_to_base_m is not None else f'{p.point.name}: sin distancia a la base',
                     f'{p.epochs_fixed} épocas fijas de {p.epochs_total}']
            status = statuses[index]
            if status is not None:
                parts.insert(1, calidad_ui.status_cell(status) + (f' ({status.reason})' if status.reason else ''))
            if p.message:
                parts.append(p.message)
            point_detail.configure(text=' · '.join(parts))
        tree.bind('<<TreeviewSelect>>', selected)
        frame, inner = card(body, 'Precisión por punto')
        frame.pack(fill='x', pady=(16, 0))
        bars = BarChart(inner, self.app)
        bars.pack(fill='x')
        bars.set([(p.point.name, horizontal(p.sigma_enu) or 0.0, solution_key(p.solution)) for p in points],
                 title='σ horizontal (color: solución)', value_text=lambda v: format_length(v, prefs))
        if quality is not None and quality.groups:
            calidad_ui.occupations_card(body, self.app, quality, prefs, self.choose_occupation).pack(
                fill='x', pady=(16, 0))
        if quality is not None and (quality.control or quality.control_missing):
            calidad_ui.control_card(body, self.app, quality, prefs).pack(fill='x', pady=(16, 0))
        warnings = list(result.warnings)+list(q.warnings)+output['coordinate_warnings']
        if warnings:
            frame, inner = card(body, 'Avisos')
            frame.pack(fill='x', pady=(16, 0))
            for w in dict.fromkeys(warnings):
                wrap_label(inner, '· '+w, style='Card.TLabel', margin=40).pack(fill='x', pady=1)
