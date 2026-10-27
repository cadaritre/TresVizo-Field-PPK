"""PPK de puntos (§1.2): base de la RGNA o propia, jornada cinemática + CSV de TresVizo Field, proceso y resultado."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from tresvizo_ppk import project as projects
from tresvizo_ppk.model import Ephemeris, Solution

from app import tasks
from app.charts import BarChart, PlanChart, enu_of
from app.components import DropZone, FileList, LabeledEntry, ThinScrollbar, card, wrap_label
from app.formatting import dms, horizontal, solution_key, solution_text
from app.settings import format_length
from app.wizard import PointsJob, classify
from app.wizard_ui import INEGI_GUIDE, RINEX_TYPES, BasePanel, OptionsPanel, WizardPage, ecef_text
from app.static_ui import RECORDING_TYPES

EXPORTS = [('field_csv', 'CSV de TresVizo Field…', '.csv', 'CSV de TresVizo Field'),
           ('csv', 'CSV simple (nombre, X, Y, Z, código)…', '.csv', 'CSV'),
           ('geojson', 'GeoJSON…', '.geojson', 'GeoJSON'), ('kml', 'KML (Google Earth)…', '.kml', 'KML')]


class PointsPage(WizardPage):
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
            OptionsPanel(body, self.app, self.job, self.changed).pack(fill='x')

    def build_base(self, body):
        frame, inner = card(body, 'Base', 'Una estación de la RGNA o tu propio MeridianV grabando como base.')
        frame.pack(fill='x')
        self.kind_var = tk.StringVar(value=self.job.base_kind)
        for value, text in (('rgna', 'Estación de la RGNA'), ('own', 'Base propia (MeridianV)')):
            ttk.Radiobutton(inner, text=text, value=value, variable=self.kind_var, style='Card.TRadiobutton',
                            command=self.switch_base).pack(side='left', padx=(0, 18))
        self.rgna_frame = ttk.Frame(body)
        guide, g = card(self.rgna_frame, None)
        guide.pack(fill='x', pady=(16, 0))
        wrap_label(g, INEGI_GUIDE, style='Card.Subtitle.TLabel', margin=40).pack(fill='x')
        if self.app.backend.available('core', 'download_rgna'):
            download = ttk.Button(g, text='Descargar de la RGNA…', command=self.download_rgna)
            self.app.icons.bind(download, 'download')
            download.pack(anchor='w', pady=(10, 0))
        self.rgna_panel = BasePanel(self.rgna_frame, self.app, self.job.base, 'Estación de la RGNA', self.changed)
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
        if self.job.base_kind == 'own':
            self.set_own_files(self.job.base.files, notify=False)
        frame, inner = card(parent, 'Datos de la base')
        frame.pack(fill='x', pady=(16, 0))
        inner.columnconfigure((0, 1), weight=1, uniform='own')
        self.own_name = tk.StringVar(value=self.job.own_name)
        self.own_height = tk.StringVar(value=self.job.own_height)
        LabeledEntry(inner, 'Nombre de la base', self.own_name).grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        LabeledEntry(inner, 'Altura del bastón o trípode (m)', self.own_height,
                     'Vertical, del punto a la rosca del receptor.').grid(row=0, column=1, sticky='nsew', padx=(12, 0))
        self.own_name.trace_add('write', lambda *a: self.edit(own_name=self.own_name.get()))
        self.own_height.trace_add('write', lambda *a: self.edit(own_height=self.own_height.get()))
        frame, inner = card(parent, 'Coordenada de la base',
                            'Para ligar los puntos a la RGNA, la base debe estar en ITRF2008 época 2010.0: lo mejor es '
                            'georreferenciarla antes con «Georreferenciar punto» y encadenar ese proyecto.')
        frame.pack(fill='x', pady=(16, 0))
        self.source_var = tk.StringVar(value=self.job.own_source)
        row = ttk.Frame(inner, style='Flat.Card.TFrame')
        row.pack(fill='x')
        for value, text in (('project', 'De un proyecto «Georreferenciar punto»'), ('manual', 'Escrita a mano')):
            ttk.Radiobutton(row, text=text, value=value, variable=self.source_var, style='Card.TRadiobutton',
                            command=self.switch_source).pack(side='left', padx=(0, 18))
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
        self.coord_hint = ttk.Label(self.manual, text='', style='Card.Subtitle.TLabel')
        self.coord_hint.pack(anchor='w', pady=(6, 0))
        self.switch_format(notify=False)
        self.switch_source(notify=False)

    def edit(self, **values):
        for key, value in values.items():
            setattr(self.job, key, value)
        self.changed()

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
        self.job.base.files = list(paths)
        self.own_list.set(paths)
        if paths and not self.own_list.winfo_manager():
            self.own_list.pack(fill='x', pady=(10, 0))
        elif not paths:
            self.own_list.pack_forget()
        if notify:
            self.changed()

    def switch_source(self, notify=True):
        self.job.own_source = self.source_var.get()
        self.from_project.pack_forget()
        self.manual.pack_forget()
        (self.from_project if self.job.own_source == 'project' else self.manual).pack(fill='x')
        self.show_project_info()
        if notify:
            self.changed()

    def switch_format(self, notify=True):
        self.job.coord_format = self.format_var.get()
        if self.job.coord_format == 'geodetic':
            names = ('Latitud', 'Longitud', 'Altura elipsoidal (m)')
            hint = 'Grados decimales o «19 25 12.3456 N»; longitud oeste con W o con signo menos. ITRF2008 2010.0.'
        else:
            names = ('X (m)', 'Y (m)', 'Z (m)')
            hint = 'Geocéntricas en ITRF2008, época 2010.0.'
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

    def download_rgna(self):
        from app.rgna_download import RgnaDownloadDialog

        def downloaded(station, paths):
            self.job.base.station, self.job.base.name = station, station.name
            self.rgna_panel.set_files(list(paths))
            self.rgna_panel.picker.name_var.set(station.name)
            self.rgna_panel.picker.show()
        RgnaDownloadDialog(self.app, downloaded)

    # --- Paso 2: rover y puntos ---------------------------------------------------------------------------------

    def build_rover(self, body):
        frame, inner = card(body, 'Jornada del rover', 'La grabación cinemática de toda la jornada (o su RINEX).')
        frame.pack(fill='x')
        DropZone(inner, self.app, 'Arrastra aquí la grabación de la jornada', 'Una grabación .bin/.part o sus RINEX.',
                 self.add_rover, RECORDING_TYPES).pack(fill='x')
        self.rover_list = FileList(inner, self.app, self.set_rover)
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
        tasks.run(self, lambda p, c: core.read_field_csv(path), on_done=done, on_error=failed)

    # --- Proceso ------------------------------------------------------------------------------------------------

    def summary_lines(self):
        job = self.job
        if job.base_kind == 'rgna':
            base = (f'Base RGNA {job.base.station.id} ({job.base.name or job.base.station.name})'
                    if job.base.station else 'Base RGNA: —')
        else:
            origin = (f'del proyecto {Path(job.own_project).name}' if job.own_source == 'project' and job.own_project
                      else 'escrita a mano')
            base = f'Base propia {job.own_name or "—"}, altura {job.own_height or "—"} m, coordenada {origin}'
        o = job.options
        lines = [base+f' · {len(job.base.files)} archivo(s)', f'Rover: {len(job.rover_files)} archivo(s)',
                 f'Puntos: {Path(job.csv_file).name if job.csv_file else "—"}'
                 + (f' ({len(self.csv_points)} puntos)' if getattr(self, 'csv_points', None) else ''),
                 f'Proceso: cinemático, {"combinado adelante/atrás" if o.combined else "adelante"}, máscara '
                 f'{o.elevation_mask_deg:g}°, efemérides {"precisas" if o.ephemeris is Ephemeris.PRECISE else "difundidas"}',
                 'Cada punto: promedio de las épocas fijas de su ocupación (o flotantes, marcadas), todo plomeado.',
                 f'Resultado en {job.coordinates.label or job.coordinates.crs_id}'
                 + (f' + {job.coordinates.geoid_id.upper()}' if job.coordinates.geoid_id else '')]
        if self.path:
            lines.append(f'Carpeta de trabajo: {projects.workdir(self.project or self.make_project(self.path.parent), self.path)}')
        return lines

    def work(self, job, project, workdir, progress, cancel):
        core = self.app.backend.core
        station = job.station()
        stages = tasks.Stages(progress, [2, 3, 1, 1, 8, 2])
        base_obs = core.prepare_observations(job.base.files, workdir/'base', pole_height_m=job.base_height_m(),
                                             marker=station.id, progress=stages.stage(0), cancel=cancel)
        rover = core.prepare_observations(job.rover_files, workdir/'rover', progress=stages.stage(1), cancel=cancel)
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

    def make_report(self, report, output, project, user, out):
        return report.points_report(output['result'], project, user, out)

    def export_button(self, parent):
        button = ttk.Menubutton(parent, text='Exportar')
        self.app.icons.bind(button, 'share')
        menu = tk.Menu(button, tearoff=False)
        for formato, label, suffix, kind in EXPORTS:
            menu.add_command(label=label, command=lambda f=formato, s=suffix, k=kind: self.export(f, s, k))
        button.configure(menu=menu)
        if not self.app.backend.available('export'):
            button.state(['disabled'])
        return button

    def export(self, formato, suffix, kind):
        export = self.app.backend.export
        if export is None or self.output is None:
            return
        stem = self.path.stem if self.path else 'puntos'
        target = filedialog.asksaveasfilename(parent=self.app, title='Exportar puntos', defaultextension=suffix,
                                              initialdir=str(self.path.parent if self.path else Path.home()),
                                              initialfile=f'{stem} posproceso{suffix}',
                                              filetypes=[(kind, '*'+suffix)])
        if not target:
            return
        result, choice = self.output['result'], self.output['choice']

        def done(path):
            self.app.set_status(f'Puntos exportados en {path}.')

        def failed(kind_, message, detail):
            self.app.show_error(kind_, message, detail, 'No se pudo exportar')
        tasks.run(self, lambda p, c: export.export_points(result, choice, formato, Path(target)), on_done=done,
                  on_error=failed)

    # --- Resultado ----------------------------------------------------------------------------------------------

    def result_view(self, body, output):
        result, prefs = output['result'], self.app.settings.values
        counts = {s: sum(1 for p in result.points if Solution(p.solution) is s) for s in Solution}
        fixed, floating = counts[Solution.FIXED], counts[Solution.FLOAT]
        none = counts[Solution.NONE]+counts[Solution.SINGLE]
        kind = 'success' if none == 0 and floating == 0 else 'warning' if none == 0 else 'error'
        top = ttk.Frame(body)
        top.pack(fill='x')
        top.columnconfigure(0, weight=2, uniform='top')
        top.columnconfigure(1, weight=3, uniform='top')
        frame, inner = card(top, 'Resumen de la jornada')
        frame.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        wrap_label(inner, f'{fixed} FIJOS · {floating} FLOTANTES · {none} SIN SOLUCIÓN', style=kind+'.TLabel',
                   margin=40).pack(fill='x')
        lines = [f'Épocas fijas de la trayectoria: {result.fix_rate*100:.1f} %',
                 f'Base: {result.base.id} · {result.base.name} ({"oficial" if result.base.official else result.base.source})',
                 f'{len(result.points)} puntos del CSV']
        q = result.rover_quality
        lines.append(f'Rover: {q.epochs} épocas · {len(q.gaps)} hueco(s) · {len(q.satellites)} satélites')
        for line in lines:
            wrap_label(inner, line, style='Card.TLabel', margin=40).pack(fill='x', pady=(6, 0))
        frame, inner = card(top, 'Puntos en planta')
        frame.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
        plan = PlanChart(inner, self.app, height=300)
        plan.pack(fill='both', expand=True)
        located = [p for p in result.points if p.position is not None]
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
        frame, inner = card(body, 'Puntos')
        frame.pack(fill='x', pady=(16, 0))
        coordinates = output['coordinates']
        projected = any(c is not None and c.northing_m is not None for c in coordinates)
        first, second = ('Este', 'Norte') if projected else ('Latitud', 'Longitud')
        height_title = 'Cota' if any(c is not None and c.orthometric_height_m is not None for c in coordinates) else \
            'Altura elip.'
        columns = [('name', 'Punto', 110), ('code', 'Código', 80), ('a', first, 150), ('b', second, 160),
                   ('h', height_title, 120), ('sh', 'σ H', 80), ('sv', 'σ V', 80), ('epochs', 'Épocas', 90),
                   ('solution', 'Solución', 110), ('delta', 'Δ campo H/V', 130), ('dist', 'A la base', 100),
                   ('message', 'Aviso', 220)]
        tree = ttk.Treeview(inner, columns=[c[0] for c in columns], show='headings',
                            height=min(14, max(3, len(result.points))), selectmode='browse')
        for key, title, width in columns:
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=width, minwidth=50, anchor='w', stretch=key == 'message')
        # Solo se resaltan las filas que piden revisión; la solución siempre va escrita.
        tokens = self.app.theme.tokens
        tree.tag_configure('float', foreground=tokens['warning_fg'], background=tokens['warning_bg'])
        for key in ('single', 'none'):
            tree.tag_configure(key, foreground=tokens['error_fg'], background=tokens['error_bg'])
        for index, (p, c) in enumerate(zip(result.points, coordinates)):
            if c is not None and projected and c.northing_m is not None:
                a, b = format_length(c.easting_m, prefs), format_length(c.northing_m, prefs)
            elif c is not None:
                a, b = dms(c.latitude_deg, 'N', 'S', 4), dms(c.longitude_deg, 'E', 'W', 4)
            else:
                a = b = '—'
            h = '—' if c is None else format_length(c.orthometric_height_m if c.orthometric_height_m is not None else
                                                   c.ellipsoidal_height_m, prefs)
            delta = ('—' if p.delta_from_field_enu is None else
                     f'{format_length(horizontal(p.delta_from_field_enu), prefs)} / '
                     f'{format_length(p.delta_from_field_enu.u, prefs)}')
            tree.insert('', 'end', iid=str(index), tags=(solution_key(p.solution),), values=(
                p.point.name, p.point.code, a, b, h, format_length(horizontal(p.sigma_enu), prefs),
                format_length(None if p.sigma_enu is None else p.sigma_enu.u, prefs),
                f'{p.epochs_fixed}/{p.epochs_total}', solution_text(p.solution), delta,
                format_length(p.distance_to_base_m, prefs, 0) if p.distance_to_base_m is not None else '—',
                p.message))
        tree.pack(fill='x')
        scroll = ThinScrollbar(inner, tree.xview, orient='horizontal')
        tree.configure(xscrollcommand=scroll.set)
        scroll.pack(fill='x', pady=(3, 0))
        frame, inner = card(body, 'Precisión por punto')
        frame.pack(fill='x', pady=(16, 0))
        bars = BarChart(inner, self.app)
        bars.pack(fill='x')
        bars.set([(p.point.name, horizontal(p.sigma_enu) or 0.0, solution_key(p.solution)) for p in result.points],
                 title='σ horizontal (color: solución)', value_text=lambda v: format_length(v, prefs))
        warnings = list(result.warnings)+list(q.warnings)+output['coordinate_warnings']
        if warnings:
            frame, inner = card(body, 'Avisos')
            frame.pack(fill='x', pady=(16, 0))
            for w in dict.fromkeys(warnings):
                wrap_label(inner, '· '+w, style='Card.TLabel', margin=40).pack(fill='x', pady=1)
