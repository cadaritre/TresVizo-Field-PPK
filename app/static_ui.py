"""Georreferenciar punto (§1.1): rover, 1 a 3 estaciones de referencia (de una red CORS), ajustes, proceso y resultado."""
from __future__ import annotations

from copy import deepcopy
import math
import tkinter as tk
from tkinter import ttk

from tresvizo_ppk.model import Ephemeris, Solution

from app import tasks
from app.charts import BarChart, PlanChart, enu_of, enu_sigma
from app.components import DropZone, FileList, LabeledEntry, card, wrap_label
from app.formatting import coordinate_clipboard, coordinate_rows, dms, horizontal, solution_key, solution_text
from app.settings import format_length
from app.wizard import MAX_BASES, BaseEntry, StaticJob, classify
from app.wizard_ui import NETWORK_GUIDE, RINEX_TYPES, BasePanel, OptionsPanel, WizardPage, ecef_text

RECORDING_TYPES = [('Grabación del receptor o RINEX', '*.bin *.part *.obs *.nav *.rnx *.crx *.??o *.??d *.??n *.Z '
                    '*.gz *.zip'), ('Todos los archivos', '*')]


class StaticPage(WizardPage):
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
        self.set_rover(self.job.rover_files, notify=False)
        frame, inner = card(body, 'Punto')
        frame.pack(fill='x', pady=(16, 0))
        inner.columnconfigure((0, 1), weight=1, uniform='point')
        self.name_var = tk.StringVar(value=self.job.point_name)
        self.height_var = tk.StringVar(value=self.job.pole_height)
        name = LabeledEntry(inner, 'Nombre del punto', self.name_var, 'Sale en el informe y en el nombre del '
                                                                      'proyecto.')
        name.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        height = LabeledEntry(inner, 'Altura del bastón o trípode (m)', self.height_var,
                              'Vertical, del punto en el suelo a la rosca del receptor. La distancia de la rosca a '
                              'la referencia de la antena la suma el programa.')
        height.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
        for widget in height.winfo_children():
            if isinstance(widget, ttk.Label) and widget.cget('style') == 'Card.Subtitle.TLabel':
                inner.bind('<Configure>', lambda e, w=widget: w.configure(wraplength=max(160, e.width//2-24)),
                           add='+')
        self.name_var.trace_add('write', lambda *a: self.edit(point_name=self.name_var.get()))
        self.height_var.trace_add('write', lambda *a: self.edit(pole_height=self.height_var.get()))

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
        if notify:
            self.changed()

    def build_bases(self, body):
        self.bases_body = body
        intro, inner = card(body, 'Estaciones de referencia', 'Una a tres, de las redes instaladas (Redes y estaciones). '
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
            BasePanel(self.base_panels, self.app, entry, f'Estación {index+1}', self.changed, remove,
                      guide=self.network_guide).pack(fill='x', pady=(16, 0))
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
        self.changed()

    # --- Proceso ------------------------------------------------------------------------------------------------

    def summary_lines(self):
        job = self.job
        lines = [f'Punto: {job.point_name.strip() or "—"} · altura del bastón o trípode {job.pole_height or "—"} m',
                 f'Rover: {len(job.rover_files)} archivo(s)']
        for base in job.bases:
            if base.station:
                lines.append(f'Estación {base.station.id} ({base.name or base.station.name}): {len(base.files)} '
                             f'archivo(s) · coordenada {"oficial" if base.station.official else "corregida"}')
        o = job.options
        eph = 'precisas' if o.ephemeris is Ephemeris.PRECISE else 'difundidas'
        lines.append(f'Proceso: estático, {"combinado adelante/atrás" if o.combined else "adelante"}, máscara '
                     f'{o.elevation_mask_deg:g}°, {", ".join(o.constellations)}, efemérides {eph}')
        if len(job.bases) >= 2:
            lines.append(f'Ajuste por mínimos cuadrados con {len(job.bases)} líneas base y prueba chi² al 95 %.')
        lines.append(f'Resultado en {job.coordinates.label or job.coordinates.crs_id}'
                     + (f' + {job.coordinates.geoid_id.upper()}' if job.coordinates.geoid_id else ''))
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

    def export_button(self, parent):
        button = ttk.Menubutton(parent, text='Exportar')
        self.app.icons.bind(button, 'share')
        menu = tk.Menu(button, tearoff=False)
        menu.add_command(label='Copiar coordenadas', command=self.copy_coordinates)
        if self.app.backend.available('report'):
            menu.add_command(label='Guardar informe PDF…', command=self.view_report)
        button.configure(menu=menu)
        return button

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
        frame, inner = card(top, f'Punto {result.point_name}', coordinate.label if coordinate else
                            'Sin transformar: se muestra el marco oficial.')
        frame.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        status = 'Aprobado' if passed and not warnings else 'Con avisos' if passed else 'Revisar'
        kind = 'success' if passed and not warnings else 'warning' if passed else 'error'
        ttk.Label(inner, text=f'{status} · {len(result.baselines)} línea(s) base · '
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
        tree = ttk.Treeview(inner, columns=('base', 'length', 'solution', 'ratio', 'rms', 'sigma', 'epochs', 'sats'),
                            show='headings', height=max(1, len(result.baselines)), selectmode='none')
        for key, title, width in [('base', 'Estación', 190), ('length', 'Longitud', 120), ('solution', 'Solución', 120),
                                  ('ratio', 'Cociente', 80), ('rms', 'RMS', 90), ('sigma', 'σ H / V', 150),
                                  ('epochs', 'Épocas', 80), ('sats', 'Satélites', 80)]:
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=width, minwidth=60, anchor='w', stretch=key == 'base')
        for b in result.baselines:
            sh, sv = enu_sigma(b.base.position, b.covariance_ecef)
            tree.insert('', 'end', values=(f'{b.base.id} · {b.base.name}', format_length(b.length_m, prefs),
                                           solution_text(b.solution), '—' if b.ratio is None else f'{b.ratio:.1f}',
                                           '—' if b.rms_m is None else format_length(b.rms_m, prefs),
                                           f'{format_length(sh, prefs)} / {format_length(sv, prefs)}', b.epochs_used, len(b.satellites)))
        tree.pack(fill='x')
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
        text = (f'{q.epochs} épocas de {q.expected_epochs or "—"} esperadas ({completeness}) · {len(q.gaps)} hueco(s) · '
                f'{len(q.satellites)} satélites · {sum(q.cycle_slips.values())} saltos de ciclo')
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
