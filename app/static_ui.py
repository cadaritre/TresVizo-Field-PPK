"""Georreferenciar punto (§1.1): rover, 1 a 3 estaciones de referencia (de una red CORS), ajustes, proceso y resultado."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from tresvizo_ppk.model import Ephemeris, Solution

from app import tasks
from tresvizo_ppk.nombres import display_name
from app.charts import BarChart, PlanChart, enu_of, enu_sigma
from app.export_ui import CUSTOM_CSV_LABEL, POINT_FORMATS, ExportActions, csv_options
from app.components import DropZone, FileList, LabeledEntry, Tooltip, card, later, wrap_label
from app.formatting import coordinate_clipboard, coordinate_rows, dms, horizontal, plural, solution_key, solution_text
from app.settings import format_length
from app.wizard import MAX_BASES, ROVER_STATION_M, BaseEntry, StaticJob, classify
from app.wizard_ui import (NETWORK_GUIDE, RINEX_TYPES, choice_text, BasePanel, OptionsPanel, WizardPage, ecef_text,
                           station_label)

RECORDING_TYPES = [('Grabación del receptor o RINEX', '*.bin *.part *.obs *.nav *.rnx *.crx *.??o *.??d *.??n *.Z '
                    '*.gz *.zip'), ('Todos los archivos', '*')]


def baseline_station_text(station) -> str:
    """«INEG» y, en la segunda línea, el nombre completo sin repetir la clave («Aguascalientes, Ags.»)."""
    label = station_label(station)
    name = label.split(' · ', 1)[1] if ' · ' in label else station.name
    return station.id if not name or name == station.id else f'{station.id}\n{name}'


class StaticPage(ExportActions, WizardPage):
    TITLE = 'Georreferenciar punto'
    ICON = 'map-pin'
    JOB = StaticJob

    def default_name(self):
        return self.job.point_name.strip() or super().default_name()

    # --- Pasos --------------------------------------------------------------------------------------------------

    def build_step(self, index, body):
        if index == 0:
            self.build_rover(body)
        elif index == 1:
            self.build_bases(body)
        elif index == 2:
            OptionsPanel(body, self.app, self.job, self.changed).pack(fill='x')

    def build_rover(self, body):
        frame, inner = card(body, 'Grabación del rover',
                            'La sesión del MeridianV o MeridianVI (.bin o .part) o su RINEX (observación y navegación).')
        frame.pack(fill='x')
        self.rover_drop = DropZone(inner, self.app, 'Arrastra aquí la grabación del punto',
                                   'Una grabación .bin/.part, o los RINEX de observación y navegación.',
                                   self.add_rover, RECORDING_TYPES)
        self.rover_drop.pack(fill='x')
        self.rover_list = FileList(inner, self.app, self.set_rover)
        self.rover_info = ttk.Label(inner, text='', style='Card.Subtitle.TLabel', justify='left')
        inner.bind('<Configure>', lambda e: self.rover_info.configure(wraplength=max(200, e.width-20)), add='+')
        frame, inner = card(body, 'Punto')
        frame.pack(fill='x', pady=(16, 0))
        inner.columnconfigure((0, 1), weight=1, uniform='point')
        self.name_var = tk.StringVar(value=self.job.point_name)
        self.height_var = tk.StringVar(value=self.job.pole_height)
        self.name_field = name = LabeledEntry(inner, 'Nombre del punto', self.name_var,
                                              'Sale en el informe y en el nombre del proyecto.')
        self.height_field = height = LabeledEntry(inner, 'Altura del bastón o trípode (m)', self.height_var,
                                                  self.POLE_HINT)
        self.code_var = tk.StringVar(value=self.job.point_code)
        self.description_var = tk.StringVar(value=self.job.point_description)
        code = LabeledEntry(inner, 'Código (opcional)', self.code_var,
                            'El de la biblioteca de las apps («BN», «VERT»): la capa del DXF y el código del CSV y '
                            'del LandXML.')
        description = LabeledEntry(inner, 'Descripción (opcional)', self.description_var,
                                   'Sale en el CSV, el DXF y el LandXML («Mojón de concreto junto al camino»).')
        self.point_fields = (name, height, code, description)
        inner.bind('<Configure>', lambda e: self.arrange_point(e.width), add='+')
        self.arrange_point(900)
        self.set_rover(self.job.rover_files, notify=False)
        self.name_var.trace_add('write', lambda *a: self.edit(point_name=self.name_var.get()))
        self.height_var.trace_add('write', lambda *a: self.edit(pole_height=self.height_var.get()))
        self.code_var.trace_add('write', lambda *a: self.edit(point_code=self.code_var.get()))
        self.description_var.trace_add('write', lambda *a: self.edit(point_description=self.description_var.get()))

    POLE_HINT = ('Vertical, del punto en el suelo a la rosca del receptor. La distancia de la rosca a la referencia de '
                 'la antena la suma el programa.')
    ARP_HINT = 'Vertical, del punto a la referencia de la antena (ARP), como en el encabezado RINEX.'

    def arrange_point(self, width):
        """Dos columnas si caben; una sola en ventanas angostas o con letra grande."""
        columns = 2 if width >= 620 else 1
        if getattr(self, '_point_columns', None) == columns:
            return
        self._point_columns = columns
        fields = self.point_fields
        master = fields[0].master
        master.columnconfigure((0, 1), weight=1 if columns == 2 else 0, uniform='point' if columns == 2 else '')
        master.columnconfigure(0, weight=1)
        for index, widget in enumerate(fields):
            if columns == 2:
                row, column = divmod(index, 2)
                widget.grid(row=row, column=column, columnspan=1, sticky='nsew', padx=(0, 12) if column == 0 else
                            (12, 0), pady=(0 if row == 0 else 12, 0))
            else:
                widget.grid(row=index, column=0, columnspan=2, sticky='nsew', padx=0, pady=(0 if index == 0 else 12, 0))

    def rover_inspected(self, obs):
        files = self.job.rover_files
        origin = self.origin_of(files)
        recording = origin.startswith('de la sesión')
        self.rover_obs = obs
        self.job.rover_marker = getattr(obs, 'point_name', '') or obs.marker or ''
        self.job.rover_position = getattr(obs, 'approx_position', None)
        approx = self.approx_text(obs)
        self.rover_info.configure(text=f'{self.obs_summary(obs)} · {origin}'+(f'\n{approx}' if approx else ''))
        self.render_nearby()
        self.rover_info.pack(fill='x', pady=(8, 0))
        self.autofill(self.name_var, self.name_field, getattr(obs, 'point_name', '') or obs.marker,
                      self.origin_text(obs, 'point_name', origin))
        pole = self.pole_from(obs)
        if recording or pole is not None:
            self.height_field.title.configure(text='Altura del bastón o trípode (m)')
            self.height_field.hint.configure(text=self.POLE_HINT)
            self.autofill(self.height_var, self.height_field, f'{pole:.3f}' if pole is not None else None,
                          self.origin_text(obs, 'pole_height_m', origin))
        else:
            self.height_field.title.configure(text='Altura de la antena a la ARP (m)')
            self.height_field.hint.configure(text=self.ARP_HINT)
            self.autofill(self.height_var, self.height_field, f'{obs.antenna_height_m:.4f}',
                          self.origin_text(obs, 'antenna_height_m', origin))
        self.refresh()      # la estación del propio rover ya se reconoce en el paso de las bases

    def edit(self, **values):
        for key, value in values.items():
            setattr(self.job, key, value)
        self.changed()

    def add_rover(self, paths):
        projects_dropped = [p for p in paths if classify(p) == 'project']
        if projects_dropped:
            self.app.open_project(projects_dropped[0])
            return
        self.set_rover(list(dict.fromkeys([*self.job.rover_files, *paths])))

    def set_rover(self, paths, notify=True):
        self.job.rover_files = list(paths)
        self.rover_list.set(paths)
        if paths and not self.rover_list.winfo_manager():
            self.rover_list.pack(fill='x', pady=(10, 0))
        elif not paths:
            self.rover_list.pack_forget()
        self.rover_info.pack_forget()
        if not paths:
            self.rover_obs = None
            self.job.rover_marker, self.job.rover_position = '', None
            self.render_nearby()
        if paths:
            self.inspect('rover', paths, self.rover_inspected)
        if notify:
            self.changed()

    def build_bases(self, body):
        self.bases_body = body
        intro, inner = card(body, 'Estaciones de referencia', 'Una a tres, de las redes instaladas (pantalla «Estaciones de referencia»). '
                                                               'Con dos o más, la coordenada sale de un ajuste '
                                                           'por mínimos cuadrados y se comprueba cada línea base.')
        intro.pack(fill='x')
        guide = wrap_label(inner, NETWORK_GUIDE, style='Card.Subtitle.TLabel', margin=40)
        guide.pack(fill='x')
        row = ttk.Frame(inner, style='Flat.Card.TFrame')
        row.pack(fill='x', pady=(10, 0))
        self.add_base_button = ttk.Button(row, text='Agregar estación', command=self.add_base)
        self.app.icons.bind(self.add_base_button, 'plus')
        self.add_base_button.pack(side='left')
        frame, self.nearby = card(body, 'Estaciones cercanas al punto',
                                  'Por la posición aproximada del rover, en las redes instaladas. «Usar» la pone en '
                                  'la primera estación libre; luego «¿Qué archivos bajo?» dice qué pedir a la red.')
        frame.pack(fill='x', pady=(16, 0))
        self.render_nearby()
        self.base_panels = ttk.Frame(body)
        self.base_panels.pack(fill='x')
        if not self.job.bases:
            self.job.bases.append(BaseEntry())
        self.render_bases()

    NEARBY = 5

    def render_nearby(self):
        """Las estaciones de las redes instaladas más cercanas al rover, con su distancia y sus avisos."""
        from app.networks import station_notes
        holder = getattr(self, 'nearby', None)
        if holder is None or not holder.winfo_exists():
            return
        for child in holder.winfo_children():
            child.destroy()
        core = self.app.backend.core
        obs = getattr(self, 'rover_obs', None)
        position = getattr(obs, 'approx_position', None) if obs is not None else None
        if position is None or core is None or not callable(getattr(core, 'nearest_stations', None)):
            ttk.Label(holder, text='Agrega la grabación del rover para ver las estaciones más cercanas.',
                      style='Card.Subtitle.TLabel').pack(anchor='w')
            return
        cached = getattr(self, '_nearby_cache', None)
        if cached and cached[0] == position:
            found = cached[1]
        else:
            try:
                found = list(core.nearest_stations(position, self.NEARBY))
            except Exception as exc:  # noqa: BLE001 - es una ayuda: elegir a mano sigue disponible
                ttk.Label(holder, text=tasks.classify(exc)[1], style='Card.Subtitle.TLabel').pack(anchor='w')
                return
            self._nearby_cache = (position, found)
        if not found:
            ttk.Label(holder, text='No hay estaciones en las redes instaladas.', style='Card.Subtitle.TLabel').pack(
                anchor='w')
            return
        chosen = {b.station.id for b in self.job.bases if b.station}
        self._nearby_chosen = chosen
        grid = ttk.Frame(holder, style='Flat.Card.TFrame')
        grid.pack(fill='x')
        grid.columnconfigure(1, weight=1)
        prefs = self.app.settings.values
        row, notes = 0, []
        for station, distance in found:
            ttk.Label(grid, text=f'{distance/1000:.1f} km' if prefs.units == 'm' else format_length(distance, prefs, 0),
                      style='Card.Strong.TLabel').grid(row=row, column=0, sticky='w', pady=3, padx=(0, 16))
            network = getattr(station, 'network_name', '')
            ttk.Label(grid, text=station_label(station)+(f' · {network}' if network else ''),
                      style='Card.TLabel').grid(row=row, column=1, sticky='w', pady=3)
            # La estación del propio rover (misma clave o a menos de 30 m) no forma línea base: no se ofrece.
            is_rover = self.job.rover_distance(station) is not None or distance < ROVER_STATION_M
            text = 'Es el rover' if is_rover else 'En uso' if station.id in chosen else 'Usar'
            use = ttk.Button(grid, text=text, style='Card.Link.TButton', command=lambda s=station: self.use_station(s))
            use.state(['disabled'] if is_rover or station.id in chosen or len(chosen) >= MAX_BASES else ['!disabled'])
            if is_rover:
                Tooltip(use, f'{station.id} es la estación donde está el rover: no forma línea base con él.',
                        self.app.theme)
            use.grid(row=row, column=2, sticky='e', pady=3)
            row += 1
            for text in station_notes(core, station):
                note = ttk.Label(grid, text='Aviso de la red: '+text, style='Card.Subtitle.TLabel', justify='left',
                                 wraplength=600)
                note.grid(row=row, column=1, sticky='w', pady=(0, 4))
                notes.append(note)
                row += 1

        def wrap(event):
            # Lo que queda entre la distancia y «Usar».
            room = event.width-grid.grid_bbox(0, 0)[2]-grid.grid_bbox(2, 0)[2]-24
            for note in notes:
                if note.winfo_exists():
                    note.configure(wraplength=max(200, room))
        grid.bind('<Configure>', wrap, add='+')
        self.app.theme.walk(holder)

    def changed(self):
        super().changed()
        # Al elegir o quitar una estación en su ficha, la lista de cercanas dice cuál ya está en uso.
        if {b.station.id for b in self.job.bases if b.station} != getattr(self, '_nearby_chosen', None):
            self.render_nearby()

    def use_station(self, station):
        """Pone la estación en la primera ficha libre (o agrega una), sin archivos todavía."""
        if self.job.rover_distance(station) is not None:
            return
        entry = next((b for b in self.job.bases if b.station is None), None)
        if entry is None:
            if len(self.job.bases) >= MAX_BASES:
                return
            entry = BaseEntry()
            self.job.bases.append(entry)
        entry.station, entry.name = station, station.name
        self.render_bases()
        self.render_nearby()
        self.changed()

    def render_bases(self):
        for child in self.base_panels.winfo_children():
            child.destroy()
        for index, entry in enumerate(self.job.bases):
            remove = (lambda i=index: self.remove_base(i)) if len(self.job.bases) > 1 else None
            BasePanel(self.base_panels, self.app, entry, f'Estación {index+1}', self.changed, remove,
                      guide=self.network_guide, inspect=self.inspect).pack(fill='x', pady=(16, 0))
        self.add_base_button.state(['disabled'] if len(self.job.bases) >= MAX_BASES else ['!disabled'])
        self.app.theme.walk(self.base_panels)

    def add_base(self):
        if len(self.job.bases) < MAX_BASES:
            self.job.bases.append(BaseEntry())
            self.render_bases()
            self.changed()

    def remove_base(self, index):
        del self.job.bases[index]
        self.render_bases()
        self.render_nearby()
        self.changed()

    # --- Proceso ------------------------------------------------------------------------------------------------

    def summary_lines(self):
        job = self.job
        # La altura dice lo que de verdad es: del bastón (grabación del receptor) o a la ARP (RINEX).
        height = self.height_field.title.cget('text').replace(' (m)', '') if hasattr(self, 'height_field') \
            else 'Altura del bastón o trípode'
        height = height[:1].lower()+height[1:]
        lines = [f'Punto: {job.point_name.strip() or "—"} · {height} {job.pole_height or "—"} m',
                 f'Rover: {plural(len(job.rover_files), "archivo")}']
        for base in job.bases:
            if base.station:
                lines.append(f'Estación {station_label(replace(base.station, name=base.name or base.station.name))}: '
                             f'{plural(len(base.files), "archivo")} · coordenada {"oficial" if base.station.official else "corregida"}')
        o = job.options
        eph = 'precisas' if o.ephemeris is Ephemeris.PRECISE else 'difundidas'
        lines.append(f'Proceso: estático, {"combinado adelante/atrás" if o.combined else "adelante"}, máscara '
                     f'{o.elevation_mask_deg:g}°, {", ".join(o.constellations)}, efemérides {eph}')
        if len(job.bases) >= 2:
            lines.append(f'Ajuste por mínimos cuadrados con {len(job.bases)} líneas base y prueba chi² al 95 %.')
        lines.append(f'Resultado en {choice_text(self.app, job.coordinates)}')
        if self.path:
            from tresvizo_ppk import project as projects
            lines.append(f'Carpeta de trabajo: {projects.workdir(self.project or self.make_project(self.path.parent), self.path)}')
        return lines

    def work(self, job, project, workdir, progress, cancel):
        core, crs = self.app.backend.core, self.app.backend.crs
        stations = job.stations()
        stages = tasks.Stages(progress, [3]+[2]*len(stations)+[1, 8, 1])
        rover = core.prepare_observations(job.rover_files, workdir/'rover', pole_height_m=job.pole_height_m(),
                                          marker=job.point_name.strip(), progress=stages.stage(0), cancel=cancel)
        bases = []
        entries = [b for b in job.bases if b.station]
        for index, (station, entry) in enumerate(zip(stations, entries)):
            obs = core.prepare_observations(entry.files, workdir/f'base-{station.id}', marker=station.id,
                                            window=self.base_window(rover), progress=stages.stage(1+index),
                                            cancel=cancel)
            bases.append((station, obs))
        options = deepcopy(job.options)
        if options.ephemeris is Ephemeris.PRECISE and not options.precise_files and job.precise_download:
            options.precise_files = list(core.download_precise_products(rover.start, rover.end, workdir/'efemerides',
                                                                        stages.stage(1+len(stations)), cancel))
        result = core.process_static(rover, bases, options, workdir, stages.stage(2+len(stations)), cancel,
                                     point_name=job.point_name.strip())
        final = stages.stage(3+len(stations))
        final(0.2, 'Pasando la coordenada al sistema elegido…')
        coordinate, warning = self.transform(result.position, job.coordinates)
        sigma = None
        if coordinate is not None:
            try:
                covariance = result.adjustment.covariance_ecef if result.adjustment else None
                sigma = crs.transform_sigma(result.position, result.sigma_enu, job.coordinates, covariance)
            except Exception as exc:  # noqa: BLE001
                if tasks.classify(exc)[0] == 'program':
                    raise
        final(1.0, 'Listo.')
        return {'result': result, 'coordinate': coordinate, 'sigma': sigma, 'coordinate_warning': warning,
                'choice': job.coordinates}

    def after_process(self, output):
        result = output['result']
        self.project.position, self.project.sigma_enu = result.position, result.sigma_enu
        super().after_process(output)

    def make_report(self, report, output, project, user, out):
        return report.static_report(output['result'], project, user, out)

    EXPORT_STEM = 'vertice'

    def export_button(self, parent):
        button = ttk.Menubutton(parent, text='Exportar')
        self.app.icons.bind(button, 'share')
        menu = tk.Menu(button, tearoff=False)
        menu.add_command(label='Copiar coordenadas', command=self.copy_coordinates)
        if self.app.backend.available('report'):
            menu.add_command(label='Guardar informe PDF…', command=self.view_report)
        if self.app.backend.available('export'):
            menu.add_separator()
            for formato, label, suffix, kind in POINT_FORMATS:
                menu.add_command(label=label, command=lambda f=formato, s=suffix, k=kind: self.export(f, s, k))
            menu.add_command(label=CUSTOM_CSV_LABEL, command=self.export_custom_csv)
            menu.add_separator()
            self.add_rinex_item(menu)
        button.configure(menu=menu)
        return button

    def export(self, formato, suffix, kind, csv=None):
        """El vértice en un archivo, con los formatos del PPK (`export_static`)."""
        export = self.app.backend.export
        if export is None or self.output is None:
            return
        result, choice = self.output['result'], self.output['choice']
        target = self.ask_export_file('Exportar vértice', suffix, kind,
                                      f'{result.point_name or self.export_stem()}{suffix}')
        if not target:
            return
        options = {'code': self.job.point_code.strip(), 'description': self.job.point_description.strip()}
        if csv is not None:
            options['csv_options'] = csv_options(export, csv)
        if formato in ('dxf', 'landxml'):
            options['project'] = self.project.name if self.project else ''
        self.run_export(lambda p, c: export.export_static(result, choice, formato, Path(target), **options),
                        lambda path: f'Vértice exportado: {path.name}.')

    def export_custom_csv(self):
        export = self.app.backend.export
        options = self.ask_csv_options(getattr(export, 'STATIC_COLUMNS', []))
        if options is not None:
            self.export('csv', '.csv', 'CSV', csv=options)

    def copy_coordinates(self):
        out = self.output
        if out is None:
            return
        result = out['result']
        self.app.clipboard_clear()
        self.app.clipboard_append(coordinate_clipboard(result.point_name, out['coordinate'], result.position,
                                                       out['sigma'] or result.sigma_enu))
        self.app.set_status('Coordenadas copiadas al portapapeles.')

    # --- Resultado ----------------------------------------------------------------------------------------------

    def result_view(self, body, output):
        result, coordinate, prefs = output['result'], output['coordinate'], self.app.settings.values
        sigma = output['sigma'] or result.sigma_enu
        solutions = {b.solution for b in result.baselines}
        all_fixed = solutions == {Solution.FIXED}
        passed = result.adjustment.passed if result.adjustment else all_fixed
        warnings = list(result.warnings)+[w for b in result.baselines for w in b.warnings] + \
            list(result.rover_quality.warnings)+([output['coordinate_warning']] if output['coordinate_warning'] else []) + \
            (list(coordinate.warnings) if coordinate else [])
        top = ttk.Frame(body)
        top.pack(fill='x')
        top.columnconfigure(0, weight=3, uniform='top')
        top.columnconfigure(1, weight=2, uniform='top')
        frame, inner = card(top, f'Punto {result.point_name}', display_name(coordinate.label) if coordinate else
                            'Sin transformar: se muestra el marco oficial.')
        frame.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        status = 'Aprobado' if passed and not warnings else 'Con avisos' if passed else 'Revisar'
        kind = 'success' if passed and not warnings else 'warning' if passed else 'error'
        ttk.Label(inner, text=f'{status} · {plural(len(result.baselines), "línea base", "líneas base")} · '
                              + ', '.join(sorted(solution_text(s) for s in solutions)), style=kind+'.TLabel').pack(
            anchor='w', pady=(0, 10))
        grid = ttk.Frame(inner, style='Flat.Card.TFrame')
        grid.pack(fill='x')
        rows = coordinate_rows(coordinate, prefs, sigma)
        frame_text = f'{result.position.frame} {result.position.epoch:g}'
        rows += [(f'X ({frame_text})', format_length(result.position.x, prefs, 4)),
                 (f'Y ({frame_text})', format_length(result.position.y, prefs, 4)),
                 (f'Z ({frame_text})', format_length(result.position.z, prefs, 4))]
        for index, (label, value) in enumerate(rows):
            ttk.Label(grid, text=label, style='Card.Subtitle.TLabel').grid(row=index, column=0, sticky='w', pady=2)
            ttk.Label(grid, text=value, style='Card.Strong.TLabel').grid(row=index, column=1, sticky='w', padx=(16, 0),
                                                                         pady=2)
        copy = ttk.Button(inner, text='Copiar coordenadas', style='Card.Link.TButton', command=self.copy_coordinates)
        self.app.icons.bind(copy, 'copy', 16)
        copy.pack(anchor='w', pady=(10, 0))
        frame, inner = card(top, 'Esquema', None)
        frame.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
        plan = PlanChart(inner, self.app, height=300)
        plan.pack(fill='both', expand=True)
        items = [{'e': 0.0, 'n': 0.0, 'kind': 'rover', 'label': result.point_name,
                  'solution': solution_key(max(solutions, key=lambda s: list(Solution).index(Solution(s)))
                                           if solutions else 'none')}]
        for b in result.baselines:
            e, n, _ = enu_of(result.position, b.base.position)
            items.append({'e': e, 'n': n, 'kind': 'base', 'label': b.base.id, 'link': (0.0, 0.0)})
        plan.set(items)
        frame, inner = card(body, 'Líneas base')
        frame.pack(fill='x', pady=(16, 0))
        # Estación en dos líneas: la clave y, debajo, el nombre completo (nunca «UQRO · Queréta»). Si aun así la
        # columna no alcanza, los nombres completos salen debajo de la tabla.
        tree = ttk.Treeview(inner, columns=('base', 'length', 'solution', 'ratio', 'rms', 'sigma', 'epochs', 'sats'),
                            show='headings', height=max(1, len(result.baselines)), selectmode='none',
                            style='TwoLine.Treeview')
        for key, title, width in [('base', 'Estación', 190), ('length', 'Longitud', 120), ('solution', 'Solución', 120),
                                  ('ratio', 'Cociente', 80), ('rms', 'RMS', 90), ('sigma', 'σ H / V', 150),
                                  ('epochs', 'Épocas', 80), ('sats', 'Satélites', 80)]:
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=width, minwidth=60, anchor='w', stretch=key == 'base')
        for b in result.baselines:
            sh, sv = enu_sigma(b.base.position, b.covariance_ecef)
            tree.insert('', 'end', values=(baseline_station_text(b.base), format_length(b.length_m, prefs),
                                           solution_text(b.solution), '—' if b.ratio is None else f'{b.ratio:.1f}',
                                           '—' if b.rms_m is None else format_length(b.rms_m, prefs),
                                           f'{format_length(sh, prefs)} / {format_length(sv, prefs)}', b.epochs_used, len(b.satellites)))
        from app.components import CellTips, fit_columns, text_width
        fit_columns(tree, self.app, stretch='base')
        CellTips(tree, self.app)
        tree.pack(fill='x')
        self.station_names = wrap_label(inner, '\n'.join(
            f'{b.base.id}: {b.base.name}' + (f' · {b.base.network_name}' if b.base.network_name else '')
            for b in result.baselines), style='Card.Subtitle.TLabel', margin=40, wraplength=400)
        from tkinter import font as tkfont
        body_font = tkfont.Font(root=tree, font=self.app.theme.fonts.body())

        def names_fit(event=None):
            if not tree.winfo_exists():
                return
            needed = max((text_width(body_font, tree.set(i, 'base')) for i in tree.get_children()), default=0)+24
            cut = tree.column('base', 'width') < needed
            if cut and not self.station_names.winfo_manager():
                # Con el ancho de la tabla: la nota no debe pedir más ancho que ella (si lo pidiera, la tabla se
                # ensancharía, la columna alcanzaría, la nota se quitaría… sin fin).
                self.station_names.configure(wraplength=max(200, tree.winfo_width()-20))
                self.station_names.pack(fill='x', pady=(8, 0), after=tree)
            elif not cut and self.station_names.winfo_manager():
                self.station_names.pack_forget()
        pending = []

        def schedule(event=None):
            if not pending:
                pending.append(later(tree, 'idle', lambda: (pending.clear(), names_fit())))
        tree.bind('<Configure>', schedule, add='+')
        if result.adjustment:
            adj = result.adjustment
            frame, inner = card(body, 'Ajuste por mínimos cuadrados',
                                f'σ₀ a posteriori {adj.sigma0:.2f} · chi² {adj.chi2:.2f} con {adj.degrees_of_freedom} '
                                f'grados de libertad · prueba al 95 %: {"pasa" if adj.passed else "NO pasa"}')
            frame.pack(fill='x', pady=(16, 0))
            if adj.flagged_baselines:
                ttk.Label(inner, text='Línea base que no cuadra: '+', '.join(adj.flagged_baselines),
                          style='warning.TLabel').pack(fill='x', pady=(0, 8))
            bars = BarChart(inner, self.app)
            bars.pack(fill='x')
            bars.set([(f'{key} horizontal', math.hypot(r.e, r.n), 'chart1') for key, r in adj.residuals_enu.items()]
                     + [(f'{key} vertical', abs(r.u), 'chart3') for key, r in adj.residuals_enu.items()],
                     title='Residuos por línea base', value_text=lambda v: format_length(v, prefs))
        q = result.rover_quality
        frame, inner = card(body, 'Calidad de los datos del rover')
        frame.pack(fill='x', pady=(16, 0))
        completeness = f'{q.epochs/q.expected_epochs*100:.1f} %' if q.expected_epochs else '—'
        text = (f'{q.epochs} épocas de {q.expected_epochs or "—"} esperadas ({completeness}) · {plural(len(q.gaps), "hueco")} · '
                f'{len(q.satellites)} satélites · {plural(sum(q.cycle_slips.values()), "salto de ciclo", "saltos de ciclo")}')
        if q.multipath_m:
            text += ' · MP '+', '.join(f'{k} {v:.2f} m' for k, v in q.multipath_m.items())
        wrap_label(inner, text, style='Card.TLabel', margin=40).pack(fill='x')
        if warnings:
            frame, inner = card(body, 'Avisos')
            frame.pack(fill='x', pady=(16, 0))
            for w in dict.fromkeys(warnings):
                wrap_label(inner, '· '+w, style='Card.TLabel', margin=40).pack(fill='x', pady=1)
        if coordinate and coordinate.attribution:
            wrap_label(body, ' · '.join(coordinate.attribution), margin=30).pack(fill='x', pady=(12, 0))
