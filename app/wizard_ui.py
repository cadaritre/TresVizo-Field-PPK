"""Asistente por pasos común a «Georreferenciar punto» y «PPK de puntos»: pasos, validación al momento, proyecto,
proceso en otro hilo con cancelar, resultado con «Ver informe» y «Exportar»."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from tresvizo_ppk import project as projects
from tresvizo_ppk.model import Ecef, Ephemeris, Project, Station
from tresvizo_ppk.version import __version__

from app import tasks
from app.components import (Banner, Collapsible, DropZone, FileList, IssueList, ProgressPanel, ScrollFrame,
                            StepHeader, Tooltip, card, wrap_label)
from app.crs_ui import CoordinateChooser
from app.wizard import CONSTELLATIONS, BaseEntry, classify as classify_file, errors, guess_marker

RINEX_TYPES = [('Grabación o RINEX', '*.bin *.part *.obs *.nav *.rnx *.crx *.?? *.??o *.??d *.??n *.??g *.Z *.gz *.zip'),
               ('Todos los archivos', '*')]
PRECISE_TYPES = [('Efemérides SP3/CLK', '*.sp3 *.SP3 *.clk *.CLK *.Z *.gz'), ('Todos los archivos', '*')]
IONO = [('auto', 'Automática según la longitud de la línea base (recomendada)'),
        ('iono-free', 'Libre de ionosfera siempre (flotante)'),
        ('broadcast', 'Ionosfera del mensaje de navegación (líneas cortas)'),
        ('estimated', 'Estimada por satélite')]
AMBIGUITY = [('fix-and-hold', 'Fijar y mantener (recomendado)'), ('continuous', 'Continua'),
             ('instantaneous', 'Instantánea (época a época)')]
NETWORK_GUIDE = ('Baja los RINEX de la estación desde la página de su red para el día de tu sesión (con una hora de '
                 'margen) y arrástralos aquí tal como vienen: horarios o diarios, comprimidos, Hatanaka o RINEX 2/3/4. '
                 'La app los une y los recorta. «¿Qué archivos bajo?» te dice cuáles pedir.')


def station_label(station: Station) -> str:
    """«INEG · Aguascalientes» (sin repetir la clave si el nombre ya empieza con ella)."""
    name = station.name
    if name.upper().startswith(station.id.upper()):
        name = name[len(station.id):].lstrip(' —-·:') or station.name
    return f'{station.id} · {name}'


def ecef_text(p: Ecef) -> str:
    return f'X {p.x:,.4f}   Y {p.y:,.4f}   Z {p.z:,.4f} m'.replace(',', ' ')


# --- Redes de estaciones de referencia (compartido) ----------------------------------------------------------------

def network_catalog(app, callback):
    """Redes instaladas con sus estaciones (app/networks.py), leídas una vez en segundo plano."""
    from app import networks
    if app.networks_cache is not None:
        callback(app.networks_cache)
        return
    if app.backend.core is None:
        callback([])
        return
    waiting = getattr(app, 'networks_waiting', None)
    if waiting is not None:
        waiting.append(callback)
        return
    app.networks_waiting = [callback]

    def done(found):
        app.networks_cache = list(found)
        for cb in app.networks_waiting:
            cb(app.networks_cache)
        app.networks_waiting = None

    def failed(kind, message, detail):
        app.networks_waiting = None
        app.show_error(kind, message, detail, 'Redes de estaciones')
    tasks.run(app, lambda p, c: networks.load(app.backend.core), on_done=done, on_error=failed)


class StationPicker(ttk.Frame):
    """Estación de referencia de una red instalada: se propone por el nombre de los archivos y se puede elegir o
    corregir. Con varias redes, la lista dice la red de cada estación."""

    def __init__(self, parent, app, entry: BaseEntry, changed):
        super().__init__(parent, style='Flat.Card.TFrame')
        self.app, self.entry, self.changed = app, entry, changed
        self.stations: list[Station] = []
        self.networks = []
        self.labels: dict[str, Station] = {}
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text='Estación', style='Card.Strong.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 4))
        ttk.Label(self, text='Nombre en el informe', style='Card.Strong.TLabel').grid(row=0, column=1, sticky='w',
                                                                                      padx=(12, 0), pady=(0, 4))
        self.var = tk.StringVar()
        self.box = ttk.Combobox(self, textvariable=self.var)
        self.box.grid(row=1, column=0, sticky='ew')
        self.box.bind('<<ComboboxSelected>>', lambda e: self.pick())
        self.box.bind('<KeyRelease>', self.filter)
        self.box.bind('<Return>', lambda e: self.pick())
        self.box.bind('<FocusOut>', lambda e: self.pick(quiet=True))
        self.name_var = tk.StringVar(value=entry.name)
        name = ttk.Entry(self, textvariable=self.name_var)
        name.grid(row=1, column=1, sticky='ew', padx=(12, 0))
        self.name_var.trace_add('write', self.rename)
        self.info = ttk.Label(self, text='', style='Card.Subtitle.TLabel', justify='left')
        self.info.grid(row=2, column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.bind('<Configure>', lambda e: self.info.configure(wraplength=max(200, e.width-20)), add='+')
        self.fix = ttk.Button(self, text='Corregir coordenada…', style='Card.Link.TButton', command=self.correct)
        app.icons.bind(self.fix, 'pencil', 16)
        self.fix.grid(row=3, column=0, sticky='w', pady=(4, 0))
        self.show()
        network_catalog(app, self.loaded)

    def label(self, station: Station) -> str:
        text = station_label(station)
        if len(self.networks) > 1:
            from app.networks import network_of
            network = network_of(self.networks, station)
            text += f'  ({network.id.upper()})' if network else ''
        return text

    def loaded(self, networks):
        if not self.winfo_exists():
            return
        self.networks = list(networks)
        self.stations = sorted((s for n in self.networks for s in n.stations), key=lambda s: s.id)
        self.labels = {self.label(s): s for s in self.stations}
        self.box.configure(values=list(self.labels))
        if not self.stations:
            self.info.configure(text='No hay redes de estaciones instaladas. ' + (
                self.app.backend.missing_message('core') if self.app.backend.core is None else
                'Bájalas en «Redes y estaciones».'))
        self.propose()
        self.show()

    def propose(self, marker=None):
        """Si aún no hay estación, la propone por la clave del encabezado o del nombre del archivo
        («chih2990.24d» → CHIH)."""
        if self.entry.station is not None or not self.entry.files:
            return
        marker = (marker or self.entry.marker).upper()
        found = next((s for s in self.stations if s.id.upper() == marker), None)
        core = self.app.backend.core
        if found is None and marker and core is not None and callable(getattr(core, 'find_station', None)):
            try:
                found = core.find_station(marker)
            except Exception:  # noqa: BLE001 - proponer es una ayuda; elegir a mano sigue disponible
                found = None
        if found is not None:
            self.entry.station = found
            self.entry.name = self.entry.name or found.name
            self.name_var.set(self.entry.name)
            self.show()
            self.changed()

    def filter(self, event):
        if event.keysym in ('Up', 'Down', 'Return', 'Escape', 'Tab'):
            return
        query = self.var.get().casefold()
        self.box.configure(values=[label for label in self.labels if query in label.casefold()])

    def pick(self, quiet=False):
        text = self.var.get().strip()
        if not text or (self.entry.station is not None and text == self.label(self.entry.station)):
            return
        key = text.split('·')[0].strip().upper()
        found = self.labels.get(text) or next((s for s in self.stations if s.id.upper() == key), None)
        if found is None:
            if not quiet:
                self.info.configure(text=f'«{text}» no está en las redes instaladas.')
            return
        self.entry.station = found
        self.entry.name = found.name
        self.name_var.set(found.name)
        self.show()
        self.changed()

    def rename(self, *args):
        self.entry.name = self.name_var.get()
        self.changed()

    def show(self):
        s = self.entry.station
        if s is None:
            self.var.set('')
            self.info.configure(text='Escribe la clave o el nombre, o elige de la lista.')
            self.fix.state(['disabled'])
            return
        self.var.set(self.label(s))
        from app.networks import network_of
        network = network_of(self.networks, s)
        sigma = (f' · σ E {s.sigma_enu.e*1000:.0f} N {s.sigma_enu.n*1000:.0f} U {s.sigma_enu.u*1000:.0f} mm'
                 if s.sigma_enu else '')
        antenna = ' '.join(x for x in (s.antenna_type, s.radome if s.radome != 'NONE' else '') if x) or 'sin dato'
        origin = 'oficial' if s.official else 'corregida a mano'
        catalog = f'del catálogo {network.name}' if network else 'del catálogo'
        self.info.configure(text=f'Coordenada {origin} {catalog} · {s.position.frame}, época {s.position.epoch:g}\n'
                                 f'{ecef_text(s.position)}{sigma}\n'
                                 f'Antena {antenna} · altura {s.antenna_height_m:.4f} m · Fuente: {s.source or "—"}')
        self.fix.state(['!disabled'])

    def correct(self):
        s = self.entry.station
        if s is None:
            return
        dialog = CoordinateDialog(self.app, s)
        self.wait_window(dialog)
        if dialog.result is not None:
            position, reason = dialog.result
            self.entry.station = replace(s, position=position, official=False,
                                         source=f'Corregida por el usuario: {reason}')
            self.show()
            self.changed()


class CoordinateDialog(tk.Toplevel):
    """Corregir la coordenada de una estación para este proyecto (queda en el informe con el motivo)."""

    def __init__(self, app, station: Station):
        super().__init__(app)
        self.result = None
        self.title(f'Corregir coordenada de {station.id}')
        self.transient(app)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=20)
        body.pack(fill='both', expand=True)
        wrap_label(body, f'Coordenada de la estación en {station.position.frame}, época {station.position.epoch:g}. '
                         'Cámbiala solo si tienes una más reciente de la red; el informe dirá que se corrigió y por '
                         'qué.').pack(fill='x', pady=(0, 12))
        self.vars = []
        for axis, value in zip('XYZ', (station.position.x, station.position.y, station.position.z)):
            row = ttk.Frame(body)
            row.pack(fill='x', pady=3)
            ttk.Label(row, text=f'{axis} (m)', width=8).pack(side='left')
            var = tk.StringVar(value=f'{value:.4f}')
            ttk.Entry(row, textvariable=var, width=22).pack(side='left', fill='x', expand=True)
            self.vars.append(var)
        ttk.Label(body, text='Motivo o fuente').pack(anchor='w', pady=(10, 4))
        self.reason = tk.StringVar()
        reason = ttk.Entry(body, textvariable=self.reason, width=48)
        reason.pack(fill='x')
        self.error = ttk.Label(body, text='', style='error.TLabel')
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(16, 0))
        ttk.Button(buttons, text='Cancelar', command=self.destroy).pack(side='right')
        ok = ttk.Button(buttons, text='Usar esta coordenada', style='Primary.TButton', command=self.accept)
        app.icons.bind(ok, 'check')
        ok.pack(side='right', padx=8)
        self.bind('<Return>', lambda e: self.accept())
        self.bind('<Escape>', lambda e: self.destroy())
        self.frame_epoch = (station.position.frame, station.position.epoch)
        app.theme.walk(self)
        app.set_window_icon(self)
        reason.focus_set()
        self.grab_set()

    def accept(self):
        from app.wizard import parse_number
        try:
            x, y, z = (parse_number(v.get()) for v in self.vars)
            radius = (x*x+y*y+z*z)**.5
            if not 6_300_000 < radius < 6_400_000:
                raise ValueError('La coordenada no está sobre la superficie de la Tierra.')
            if not self.reason.get().strip():
                raise ValueError('Escribe el motivo o la fuente de la corrección.')
        except ValueError as exc:
            self.error.configure(text=str(exc) if 'Tierra' in str(exc) or 'motivo' in str(exc) else
                                 'Escribe X, Y y Z en metros.')
            self.error.pack(fill='x', pady=(10, 0))
            return
        self.result = (Ecef(x, y, z, *self.frame_epoch), self.reason.get().strip())
        self.destroy()


class BasePanel(ttk.Frame):
    """Una estación: sus archivos (arrastrar y soltar) y su estación del catálogo."""

    def __init__(self, parent, app, entry: BaseEntry, title, changed, on_remove=None, kinds_hint='', guide=None,
                 inspect=None):
        super().__init__(parent, style='Card.TFrame', padding=18)
        self.app, self.entry, self.changed, self.inspect = app, entry, changed, inspect
        head = ttk.Frame(self, style='Flat.Card.TFrame')
        head.pack(fill='x')
        app.icons.label(head, 'radio-tower', 20, token='primary', background='surface', style='Card.TLabel').pack(
            side='left', padx=(0, 8))
        ttk.Label(head, text=title, style='Card.Section.TLabel').pack(side='left')
        if on_remove:
            remove = ttk.Button(head, text='Quitar estación', style='Card.Link.TButton', command=on_remove)
            app.icons.bind(remove, 'x', 16)
            remove.pack(side='right')
        if guide is not None and app.backend.core is not None:
            which = ttk.Button(head, text='¿Qué archivos bajo?', style='Card.Link.TButton',
                               command=lambda: guide(entry))
            app.icons.bind(which, 'download', 16)
            which.pack(side='right', padx=(0, 12))
        self.drop = DropZone(self, app, 'Arrastra aquí los RINEX de la estación',
                             kinds_hint or 'Como los bajaste de la red: horarios o diarios, .Z, .gz, .zip, Hatanaka; '
                                           'RINEX 2.11, 3 o 4.', self.add, RINEX_TYPES)
        self.drop.pack(fill='x', pady=(12, 8))
        self.files = FileList(self, app, self.set_files)
        self.files.pack(fill='x')
        self.rinex = ttk.Label(self, text='', style='Card.Subtitle.TLabel', justify='left')
        self.bind('<Configure>', lambda e: self.rinex.configure(wraplength=max(200, e.width-40)), add='+')
        self.picker = StationPicker(self, app, entry, changed)
        self.picker.pack(fill='x', pady=(12, 0))
        self.files.set(entry.files)
        if not entry.files:
            self.files.pack_forget()
        else:
            self.read_files()

    def read_files(self):
        """Lo que trae el RINEX de la estación (clave, receptor, antena, altura) se muestra con su origen y la
        estación se propone por la clave del encabezado."""
        if self.inspect is None or not self.entry.files:
            return

        def shown(obs):
            if not self.winfo_exists():
                return
            parts = [x for x in (obs.marker and f'Clave {obs.marker}', obs.receiver and f'receptor {obs.receiver}',
                                 obs.antenna_type and f'antena {obs.antenna_type} {obs.radome or ""}'.strip(),
                                 f'altura {obs.antenna_height_m:.4f} m a la ARP',
                                 obs.start and obs.end and f'{obs.start:%d/%m/%Y %H:%M}–{obs.end:%H:%M} UTC') if x]
            self.rinex.configure(text='Del RINEX: '+' · '.join(parts))
            self.rinex.pack(fill='x', pady=(8, 0), after=self.files)
            if self.entry.station is None and obs.marker:
                self.picker.propose(obs.marker)
        self.inspect(f'base-{id(self.entry)}', self.entry.files, shown)

    def add(self, paths):
        self.set_files(list(dict.fromkeys([*self.entry.files, *paths])))
        self.picker.propose()
        self.read_files()

    def set_files(self, paths):
        self.entry.files = list(paths)
        self.files.set(self.entry.files)
        if self.entry.files and not self.files.winfo_manager():
            self.files.pack(fill='x', after=self.drop)
        elif not self.entry.files:
            self.files.pack_forget()
        self.changed()


class OptionsPanel(ttk.Frame):
    """Ajustes del proceso (§1.1.3) y sistema de coordenadas del resultado."""

    def __init__(self, parent, app, job, changed):
        super().__init__(parent)
        self.app, self.job, self.changed = app, job, changed
        o = job.options
        frame, body = card(self, 'Proyecto', 'Número o clave que sale en el encabezado del informe (opcional).')
        frame.pack(fill='x', pady=(0, 16))
        self.number = tk.StringVar(value=job.number)
        ttk.Entry(body, textvariable=self.number, width=30).pack(anchor='w')
        self.number.trace_add('write', lambda *a: self.apply())
        frame, body = card(self, 'Satélites', 'Por omisión: máscara de 15°, todas las constelaciones y frecuencias '
                                              'que tengan el rover y la base.')
        frame.pack(fill='x')
        row = ttk.Frame(body, style='Flat.Card.TFrame')
        row.pack(fill='x')
        ttk.Label(row, text='Máscara de elevación', style='Card.Strong.TLabel').pack(side='left')
        self.mask = tk.StringVar(value=f'{o.elevation_mask_deg:g}')
        ttk.Spinbox(row, from_=0, to=30, increment=1, textvariable=self.mask, width=5).pack(side='left', padx=8)
        ttk.Label(row, text='grados', style='Card.TLabel').pack(side='left')
        self.mask.trace_add('write', lambda *a: self.apply())
        cons = ttk.Frame(body, style='Flat.Card.TFrame')
        cons.pack(fill='x', pady=(12, 0))
        self.cons = {}
        for name in CONSTELLATIONS:
            var = tk.BooleanVar(value=name in o.constellations)
            ttk.Checkbutton(cons, text=name, variable=var, style='Card.TCheckbutton',
                            command=self.apply).pack(side='left', padx=(0, 16))
            self.cons[name] = var
        frame, body = card(self, 'Efemérides', 'Las difundidas sirven para líneas base cortas; para líneas de decenas '
                                               'o cientos de kilómetros conviene usar las precisas del IGS.')
        frame.pack(fill='x', pady=(16, 0))
        self.eph = tk.StringVar(value=o.ephemeris.value)
        ttk.Radiobutton(body, text='Difundidas (las que transmiten los satélites)', value='broadcast',
                        variable=self.eph, style='Card.TRadiobutton', command=self.apply).pack(anchor='w')
        ttk.Radiobutton(body, text='Precisas del IGS (órbitas y relojes SP3/CLK)', value='precise',
                        variable=self.eph, style='Card.TRadiobutton', command=self.apply).pack(anchor='w', pady=(4, 0))
        self.precise = ttk.Frame(body, style='Flat.Card.TFrame', padding=(24, 8, 0, 0))
        self.download = tk.BooleanVar(value=job.precise_download)
        can_download = app.backend.available('core', 'download_precise_products')
        self.download_check = ttk.Checkbutton(
            self.precise, text='Descargarlas al procesar (productos públicos del IGS; se dice el servidor y los '
                               'archivos en la bitácora)', variable=self.download, style='Card.TCheckbutton',
            command=self.apply)
        self.download_check.pack(anchor='w')
        if not can_download:
            self.download.set(False)
            self.download_check.state(['disabled'])
        self.precise_drop = DropZone(self.precise, app, 'O arrastra aquí tus archivos SP3 y CLK',
                                     'Finales, rápidos o ultrarrápidos; también comprimidos.', self.add_precise,
                                     PRECISE_TYPES)
        self.precise_drop.pack(fill='x', pady=(8, 6))
        self.precise_files = FileList(self.precise, app, self.set_precise)
        self.precise_files.pack(fill='x')
        self.precise_files.set(o.precise_files)
        frame, body = card(self, 'Sistema de coordenadas del resultado',
                           'El cálculo se hace en el marco y la época oficiales de la red de las bases; aquí eliges '
                           'cómo se muestra y se exporta.')
        frame.pack(fill='x', pady=(16, 0))
        self.chooser = CoordinateChooser(body, app, job.coordinates, changed=self.apply)
        self.chooser.pack(fill='x')
        frame, body = card(self, None)
        frame.pack(fill='x', pady=(16, 0))
        advanced = Collapsible(body, app, 'Opciones avanzadas de RTKLIB')
        advanced.pack(fill='x')
        grid = advanced.body
        grid.columnconfigure(1, weight=1)
        self.freq = tk.StringVar(value=str(o.frequencies))
        self.iono = tk.StringVar(value=dict(IONO).get(o.ionosphere, IONO[0][1]))
        self.amb = tk.StringVar(value=dict(AMBIGUITY).get(o.ambiguity_mode, AMBIGUITY[0][1]))
        self.ratio = tk.StringVar(value=f'{o.min_ratio:g}')
        self.combined = tk.BooleanVar(value=o.combined)
        self.interval = tk.StringVar(value='' if o.interval_s is None else f'{o.interval_s:g}')
        rows = [('Frecuencias', ttk.Combobox(grid, textvariable=self.freq, values=['1', '2', '3'], state='readonly',
                                             width=6)),
                ('Ionosfera', ttk.Combobox(grid, textvariable=self.iono, values=[t for _, t in IONO],
                                           state='readonly', width=44)),
                ('Ambigüedades', ttk.Combobox(grid, textvariable=self.amb, values=[t for _, t in AMBIGUITY],
                                              state='readonly', width=44)),
                ('Cociente mínimo para fijar', ttk.Spinbox(grid, from_=1.5, to=10, increment=.5,
                                                           textvariable=self.ratio, width=6)),
                ('Intervalo de proceso (s)', ttk.Entry(grid, textvariable=self.interval, width=8))]
        for index, (label, widget) in enumerate(rows):
            ttk.Label(grid, text=label, style='Card.TLabel').grid(row=index, column=0, sticky='w', pady=4,
                                                                 padx=(0, 12))
            widget.grid(row=index, column=1, sticky='w', pady=4)
            if isinstance(widget, ttk.Combobox):
                widget.bind('<<ComboboxSelected>>', lambda e: self.apply())
        ttk.Label(grid, text='Vacío: el intervalo de los datos.', style='Card.Subtitle.TLabel').grid(
            row=len(rows), column=1, sticky='w')
        ttk.Checkbutton(grid, text='Combinado adelante y atrás (recomendado)', variable=self.combined,
                        style='Card.TCheckbutton', command=self.apply).grid(row=len(rows)+1, column=0, columnspan=2,
                                                                           sticky='w', pady=(8, 0))
        for var in (self.ratio, self.interval):
            var.trace_add('write', lambda *a: self.apply())
        self.toggle_precise()

    def toggle_precise(self):
        if self.eph.get() == 'precise':
            if not self.precise.winfo_manager():
                self.precise.pack(fill='x')
        else:
            self.precise.pack_forget()

    def add_precise(self, paths):
        self.set_precise(list(dict.fromkeys([*self.job.options.precise_files, *paths])))

    def set_precise(self, paths):
        self.job.options.precise_files = list(paths)
        self.precise_files.set(paths)
        self.changed()

    def apply(self):
        from app.wizard import parse_number
        o = self.job.options
        try:
            o.elevation_mask_deg = parse_number(self.mask.get())
        except ValueError:
            o.elevation_mask_deg = -1.0   # la validación lo marca
        o.constellations = tuple(n for n in CONSTELLATIONS if self.cons[n].get())
        o.ephemeris = Ephemeris(self.eph.get())
        self.job.precise_download = self.download.get()
        o.frequencies = int(self.freq.get() or 2)
        o.ionosphere = next(k for k, t in IONO if t == self.iono.get())
        o.ambiguity_mode = next(k for k, t in AMBIGUITY if t == self.amb.get())
        try:
            o.min_ratio = parse_number(self.ratio.get())
        except ValueError:
            o.min_ratio = 0.0
        try:
            o.interval_s = parse_number(self.interval.get()) if self.interval.get().strip() else None
            if o.interval_s is not None and o.interval_s <= 0:
                o.interval_s = None
        except ValueError:
            o.interval_s = None
        o.combined = self.combined.get()
        self.job.number = self.number.get()
        self.job.coordinates = self.chooser.get()
        self.toggle_precise()
        self.changed()


# --- Página del asistente -------------------------------------------------------------------------------------------

class WizardPage(ttk.Frame):
    TITLE = ''
    SUBTITLE = ''
    ICON = 'map-pin'
    JOB = None
    PROCESS_STEP = 3
    RESULT_STEP = 4

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.worker = None
        head = ttk.Frame(self)
        head.pack(fill='x')
        head.columnconfigure(0, weight=1)
        titles = ttk.Frame(head)
        titles.grid(row=0, column=0, sticky='ew')
        ttk.Label(titles, text=self.TITLE, style='Title.TLabel').pack(anchor='w')
        self.project_label = ttk.Label(titles, text='', style='Subtitle.TLabel', justify='left')
        self.project_label.pack(anchor='w', fill='x', pady=(2, 0))
        actions = ttk.Frame(head)
        actions.grid(row=0, column=1, sticky='ne', padx=(12, 0))

        def fit(event):
            # Botones solo con icono si la página es angosta; el nombre del proyecto se envuelve.
            compact = event.width < 860
            for button in (self.new_button, self.save_button):
                button.set_compact(compact)
            self.project_label.configure(wraplength=max(200, event.width-actions.winfo_reqwidth()-24))
        head.bind('<Configure>', fit, add='+')
        self.new_button = ttk.Button(actions, text='Nuevo', command=self.ask_new)
        app.icons.bind(self.new_button, 'file-plus')
        Tooltip(self.new_button, 'Proyecto nuevo', app.theme)
        self.new_button.pack(side='left')
        self.save_button = ttk.Button(actions, text='Guardar proyecto', command=self.save)
        app.icons.bind(self.save_button, 'save')
        Tooltip(self.save_button, f'Guardar ({app.mod_text}S) · Guardar como ({app.mod_text}⇧S)', app.theme)
        self.save_button.pack(side='left', padx=(8, 0))
        self.steps_header = StepHeader(self, app, self.JOB.STEPS, self.go)
        self.steps_header.pack(fill='x', pady=(14, 10))
        self.alerts = ttk.Frame(self)
        self.alerts.pack(fill='x')
        self.footer = ttk.Frame(self, padding=(0, 10, 0, 0))
        self.footer.pack(side='bottom', fill='x')
        self.back_button = ttk.Button(self.footer, text='Atrás', command=lambda: self.go(self.step-1))
        app.icons.bind(self.back_button, 'arrow-left')
        self.back_button.pack(side='left')
        self.next_button = ttk.Button(self.footer, text='Siguiente', style='Primary.TButton', command=self.next)
        app.icons.bind(self.next_button, 'arrow-right')
        self.next_button.pack(side='right')
        self.issues = IssueList(self, app)
        self.issues.pack(side='bottom', fill='x')
        self.stack = ttk.Frame(self)
        self.stack.pack(fill='both', expand=True)
        self.stack.rowconfigure(0, weight=1)
        self.stack.columnconfigure(0, weight=1)
        self.bind_all('<Alt-Right>', lambda e: self.app.current_page == self.TITLE and self.next(), add='+')
        self.bind_all('<Alt-Left>', lambda e: self.app.current_page == self.TITLE and self.go(self.step-1), add='+')
        self.reset()

    # --- Estado -------------------------------------------------------------------------------------------------

    def new_job(self):
        job = self.JOB()
        job.coordinates = self.app.settings.coordinates()
        return job

    def reset(self):
        self.job = self.new_job()
        self.path: Path | None = None
        self.project: Project | None = None
        self.output = None
        self.dirty = False
        self.touched: set[int] = set()
        self.step = 0
        for child in self.alerts.winfo_children():
            child.destroy()
        self.build_steps()
        self.go(0)
        self.update_title()

    def build_steps(self):
        for child in self.stack.winfo_children():
            child.destroy()
        self.frames = []
        for index in range(len(self.JOB.STEPS)):
            scroll = ScrollFrame(self.stack, padding=(0, 2, 12, 12))
            scroll.grid(row=0, column=0, sticky='nsew')
            self.frames.append(scroll)
            if index == self.PROCESS_STEP:
                self.build_process(scroll.body)
            elif index == self.RESULT_STEP:
                self.result_body = scroll.body
            else:
                self.build_step(index, scroll.body)
        self.app.theme.walk(self.stack)

    def update_title(self):
        name = self.path.stem if self.path else 'Proyecto sin guardar'
        where = ''
        if self.path:
            parts = self.path.parent.parts
            where = ' · '+(str(self.path.parent) if len(parts) <= 3 else '…/'+'/'.join(parts[-2:]))
        self.project_label.configure(text=('● ' if self.dirty else '')+name+where)

    def changed(self):
        if not self.dirty:
            self.dirty = True
            self.update_title()
        if self.output is not None:
            self.output = None
            self.show_alert('info', 'Cambiaste los datos o los ajustes: vuelve a procesar para tener el resultado '
                                    'al día.', key='stale')
        self.refresh()

    def show_alert(self, kind, text, key=None):
        for child in self.alerts.winfo_children():
            if key and getattr(child, 'key', None) == key:
                child.destroy()
        banner = Banner(self.alerts, self.app, kind, text)
        banner.key = key
        banner.pack(fill='x', pady=(0, 8))

    def clear_alert(self, key):
        for child in self.alerts.winfo_children():
            if getattr(child, 'key', None) == key:
                child.destroy()

    def reachable(self) -> int:
        invalid = self.job.first_invalid_step()
        last = self.PROCESS_STEP if invalid is None else invalid
        return self.RESULT_STEP if self.output is not None else last

    def go(self, index):
        if index < 0 or index >= len(self.frames) or self.busy():
            return
        if index > self.reachable() and index != self.step:
            # No se salta un paso incompleto: se lleva al primero que falta y se dice qué falta.
            target = self.job.first_invalid_step()
            if target is not None:
                self.touched.add(target)
                index = target
            elif index == self.RESULT_STEP:
                return
        if index > self.step:
            self.touched.update(range(self.step, min(index, self.PROCESS_STEP)))
        self.step = index
        self.frames[index].tkraise()
        self.frames[index].to_top()
        self.refresh()
        if index == self.PROCESS_STEP:
            self.update_summary()

    def refresh(self):
        step = self.step
        issues = self.job.step_issues(step) if step < self.PROCESS_STEP else []
        if step not in self.touched:
            issues = [i for i in issues if i.level != 'error']
        self.issues.show(issues)
        self.steps_header.update_state(step, self.reachable())
        running = self.busy()
        self.back_button.state(['disabled'] if step == 0 or running else ['!disabled'])
        self.new_button.state(['disabled'] if running else ['!disabled'])
        self.save_button.state(['disabled'] if running else ['!disabled'])
        if step < self.PROCESS_STEP:
            self.next_button.configure(text='Siguiente')
            self.next_button.icon_name = 'arrow-right'
            self.next_button.pack(side='right')
        elif step == self.PROCESS_STEP:
            self.next_button.configure(text='Procesar')
            self.next_button.icon_name = 'play'
            self.next_button.pack(side='right')
            self.next_button.state(['disabled'] if running else ['!disabled'])
        else:
            self.next_button.pack_forget()
        self.next_button.refresh_icon()

    def next(self):
        if self.step < self.PROCESS_STEP:
            self.touched.add(self.step)
            if errors(self.job.step_issues(self.step)):
                self.refresh()
                return
            self.go(self.step+1)
        elif self.step == self.PROCESS_STEP:
            self.process()

    def busy(self):
        return self.worker is not None

    def cancel(self):
        if self.worker is not None:
            self.worker.cancel.set()
            self.progress.cancelling()

    # --- Lectura de los archivos al soltarlos: los campos se llenan solos ------------------------------------------

    def inspect(self, slot, files, callback, extra=None):
        """Lee los archivos en segundo plano (encabezado y sesión) y llama `callback(ObservationSet)` en el hilo de la
        interfaz. Si los archivos cambian antes de terminar, el resultado viejo se descarta. La copia temporal se
        borra al terminar."""
        import hashlib
        import shutil
        core = self.app.backend.core
        files = [Path(p) for p in files]
        if core is None or not files or not all(p.is_file() for p in files):
            return
        key = tuple((str(p), p.stat().st_mtime_ns) for p in files)
        cache = self.__dict__.setdefault('_inspected', {})
        if key in cache:
            callback(cache[key])
            return
        workers = self.__dict__.setdefault('_inspecting', {})
        old = workers.get(slot)
        if old is not None:
            old[1].cancel.set()
        folder = self.app.settings.data_dir()/'Temporal'/hashlib.sha256(repr(key).encode()).hexdigest()[:16]

        def work(progress, cancel):
            try:
                obs = core.prepare_observations(files, folder, progress=progress, cancel=cancel)
                if extra is not None:
                    # Lo que necesita los archivos ya preparados (p. ej. las candidatas de coordenada de la base).
                    obs.ui_extra = extra(obs, folder/'extra', cancel)
                return obs
            finally:
                shutil.rmtree(folder, ignore_errors=True)

        def done(obs):
            current = workers.get(slot)
            if current is None or current[0] != key:
                return
            workers.pop(slot, None)
            cache[key] = obs
            self.app.set_status('Archivos leídos: se llenaron los datos que traen.')
            callback(obs)

        def failed(kind, message, detail):
            if workers.get(slot, (None,))[0] == key:
                workers.pop(slot, None)
            self.app.set_status(message)
            if kind == 'program':
                tasks.log_error(self.app.settings.folder, 'Lectura de archivos', detail)
        self.app.set_status('Leyendo los archivos para llenar los datos que traen…')
        workers[slot] = (key, tasks.run(self, work, on_done=done, on_error=failed,
                                        on_cancel=lambda m: None))

    @staticmethod
    def origin_text(obs, key, fallback='') -> str:
        """Etiqueta de origen de un campo a partir de `ObservationSet.field_sources` («del encabezado RINEX»…)."""
        source = (getattr(obs, 'field_sources', {}) or {}).get(key, '')
        if not source:
            return fallback
        if source.startswith(('indicad', 'sin ')):
            return ''
        article = 'de la' if source.split()[0] in ('sesión', 'grabación') else 'del'
        return f'{article} {source}'

    @staticmethod
    def origin_of(files) -> str:
        return 'de la sesión del receptor' if any(classify_file(p) == 'recording' for p in files) else 'del RINEX'

    @staticmethod
    def obs_summary(obs) -> str:
        parts = []
        receiver = obs.receiver_model or obs.receiver
        if receiver:
            parts.append(f'Receptor {receiver}')
        if obs.antenna_type:
            parts.append(f'antena {obs.antenna_type}' + (f' {obs.radome}' if obs.radome and obs.radome != 'NONE'
                                                         else ''))
        if obs.start and obs.end:
            parts.append(f'{obs.start:%d/%m/%Y %H:%M}–{obs.end:%H:%M} UTC')
        if obs.interval_s:
            parts.append(f'cada {obs.interval_s:g} s')
        if obs.epochs:
            parts.append(f'{obs.epochs:,} épocas'.replace(',', ' '))
        return ' · '.join(parts)

    @staticmethod
    def pole_from(obs) -> float | None:
        """Altura del bastón que trae la sesión (la que da el motor, o ARP − desplazamiento del case), o None."""
        if getattr(obs, 'pole_height_m', None) is not None:
            return obs.pole_height_m
        if obs.case_offset_m is not None and obs.antenna_height_m and obs.antenna_height_m > obs.case_offset_m:
            return obs.antenna_height_m-obs.case_offset_m
        return None

    def autofill(self, var, widget, value, origin):
        """Llena un campo con lo que traen los archivos si está vacío o lo había llenado la app; si el usuario lo
        escribió, se respeta."""
        auto = self.__dict__.setdefault('_auto', {})
        current = var.get().strip()
        if value in (None, '') or (current and auto.get(str(var)) != current):
            if current and auto.get(str(var)) != current:
                widget.set_origin('escrito por ti')
            return
        auto[str(var)] = str(value)
        var.set(str(value))
        widget.set_origin(origin)

        def edited(*args):
            if var.get().strip() != auto.get(str(var)):
                widget.set_origin('escrito por ti' if var.get().strip() else '')
        if not getattr(widget, 'origin_traced', False):
            var.trace_add('write', edited)
            widget.origin_traced = True

    # --- Guía de descarga de la red ---------------------------------------------------------------------------

    def session_window(self, callback):
        """Día y horas UTC de la sesión del rover (se leen de la grabación; la copia temporal se borra)."""
        import hashlib
        import shutil
        files = [Path(p) for p in self.job.rover_files]
        if not files:
            self.app.show_error('user', 'Agrega primero la grabación del rover: de ella salen el día y las horas de '
                                        'la sesión.')
            return
        try:
            key = tuple((str(p), p.stat().st_mtime_ns) for p in files)
        except OSError as exc:
            self.app.show_error('user', f'No se puede leer «{Path(exc.filename or "").name}».')
            return
        cache = getattr(self, '_window_cache', None)
        if cache and cache[0] == key:
            callback(*cache[1])
            return
        folder = self.app.settings.data_dir()/'Temporal'/hashlib.sha256(repr(key).encode()).hexdigest()[:16]
        core = self.app.backend.core

        def work(progress, cancel):
            try:
                obs = core.prepare_observations(files, folder, progress=progress, cancel=cancel)
                return obs.start, obs.end
            finally:
                shutil.rmtree(folder, ignore_errors=True)

        def done(window):
            self.app.set_status('Sesión del rover leída.')
            if window[0] is None or window[1] is None:
                self.app.show_error('user', 'La grabación del rover no trae épocas: revisa el archivo.')
                return
            self._window_cache = (key, window)
            callback(*window)
        self.app.set_status('Leyendo la grabación del rover para saber el día y las horas de la sesión…')
        tasks.run(self, work, on_done=done,
                  on_error=lambda kind, message, detail: self.app.show_error(kind, message, detail),
                  on_cancel=lambda m: self.app.set_status(m))

    def network_guide(self, entry: BaseEntry):
        """Qué archivos pedir en la página de la red de la estación para cubrir la sesión."""
        from app import networks
        if entry.station is None:
            self.app.show_error('user', 'Elige primero la estación de referencia.')
            return
        core = self.app.backend.core

        def show(found):
            network = networks.network_of(found, entry.station)
            if network is None:
                self.app.show_error('user', f'La estación {entry.station.id} no está en las redes instaladas.')
                return

            def with_window(start, end):
                from app.network_guide import GuideDialog
                try:
                    text = networks.guide_text(core, network, entry.station, start, end)
                except Exception as exc:  # noqa: BLE001
                    kind, message = tasks.classify(exc)
                    self.app.show_error(kind, message, repr(exc))
                    return
                download = None
                if networks.can_download(core, entry.station):
                    def download(progress, cancel, station=entry.station, start=start, end=end):
                        folder = self.app.settings.downloads_dir()/'Redes'/network.id/station.id
                        return core.download_station_rinex(station, start, end, folder, progress, cancel)
                GuideDialog(self.app, network, entry.station, text or NETWORK_GUIDE, download,
                            lambda paths: self.add_downloaded(entry, paths))
            self.session_window(with_window)
        network_catalog(self.app, show)

    def add_downloaded(self, entry: BaseEntry, paths):
        entry.files = list(dict.fromkeys([*entry.files, *paths]))
        self.build_steps()
        self.go(self.step)
        self.changed()

    # --- Proyecto -----------------------------------------------------------------------------------------------

    def default_name(self) -> str:
        return datetime.now().strftime('%Y-%m-%d %H%M')

    def make_project(self, folder: Path) -> Project:
        return self.job.to_project(deepcopy(self.project) if self.project else Project())

    def ask_new(self):
        if self.confirm_discard():
            self.reset()

    def confirm_discard(self, closing=False) -> bool:
        if not self.dirty or self.busy():
            return True
        answer = messagebox.askyesnocancel('Cambios sin guardar', f'«{self.TITLE}» tiene cambios sin guardar. '
                                           '¿Guardar el proyecto?', parent=self.app)
        if answer is None:
            return False
        if answer:
            return self.save_sync()
        return True

    def ask_path(self) -> Path | None:
        folder = self.app.settings.projects_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = Path.home()
        target = filedialog.asksaveasfilename(parent=self.app, title='Guardar proyecto', defaultextension='.tvppk',
                                              initialdir=str(self.path.parent if self.path else folder),
                                              initialfile=(self.path.stem if self.path else self.default_name()),
                                              filetypes=[('Proyecto de TresVizo Field PPK', '*.tvppk')])
        return Path(target) if target else None

    def _write(self, path: Path, progress=None, cancel=None) -> Project:
        project = self.make_project(path.parent)
        projects.set_inputs(project, self.job.files(), path.parent, progress=progress, cancel=cancel)
        projects.save(project, path, __version__)
        return project

    def save(self, as_new=False, then=None):
        if self.busy():
            return
        for index in range(self.PROCESS_STEP):
            self.touched.add(index)
        try:
            self.make_project(Path('.'))
        except ValueError:
            # Faltan datos que el proyecto necesita (p. ej. la altura): se dice dónde.
            self.go(self.job.first_invalid_step() or 0)
            messagebox.showinfo('Faltan datos', 'Completa lo que se indica antes de guardar el proyecto.',
                                parent=self.app)
            return
        path = self.path if self.path and not as_new else self.ask_path()
        if path is None:
            return
        self.app.set_status('Guardando el proyecto y calculando la huella de cada archivo…')

        def done(project):
            self.project, self.path, self.dirty = project, path, False
            self.update_title()
            self.app.remember(path)
            self.app.set_status(f'Proyecto guardado en {path}.')
            if then:
                then()

        def failed(kind, message, detail):
            self.app.show_error(kind, message, detail, 'No se pudo guardar el proyecto')
        tasks.run(self, lambda p, c: self._write(path, p, c), on_done=done, on_error=failed)

    def save_sync(self) -> bool:
        path = self.path or self.ask_path()
        if path is None:
            return False
        try:
            self.config(cursor='watch')
            self.update_idletasks()
            self.project = self._write(path)
            self.path, self.dirty = path, False
            self.update_title()
            self.app.remember(path)
            return True
        except Exception as exc:  # noqa: BLE001
            kind, message = tasks.classify(exc)
            self.app.show_error(kind, message, '', 'No se pudo guardar el proyecto')
            return False
        finally:
            self.config(cursor='')

    def load_project(self, project: Project, path: Path):
        files = projects.resolve_inputs(project)
        self.job = self.JOB.from_project(project, files)
        self.project, self.path, self.output, self.dirty = project, Path(path), None, False
        self.touched = set(range(self.PROCESS_STEP))
        for child in self.alerts.winfo_children():
            child.destroy()
        self.build_steps()
        self.step = 0
        self.go(0)
        self.update_title()
        if project.processed:
            when = project.processed.astimezone().strftime('%d/%m/%Y %H:%M')
            self.show_alert('info', f'Procesado por última vez el {when}. Revisa los datos y pulsa Procesar para '
                                    'volver a calcular con los mismos ajustes.', key='processed')

        def checked(issues):
            if not issues:
                self.app.set_status(f'Proyecto abierto: los {sum(len(v) for v in files.values())} archivos de '
                                    'entrada coinciden con los guardados.')
                return
            text = '\n'.join('· '+i.message for i in issues)
            self.show_alert('warning', 'Algunos archivos de entrada no son los que se guardaron en el proyecto:\n'
                            + text, key='inputs')
            self.touched = set(range(self.PROCESS_STEP))
            self.refresh()
        tasks.run(self, lambda p, c: projects.check_inputs(project, p, c), on_done=checked,
                  on_error=lambda kind, message, detail: self.app.show_error(kind, message, detail))

    # --- Proceso ------------------------------------------------------------------------------------------------

    def build_process(self, body):
        frame, inner = card(body, 'Antes de procesar', 'Revisa el resumen. El proceso corre aparte: puedes '
                                                       'cancelarlo cuando quieras.')
        frame.pack(fill='x')
        self.summary = ttk.Label(inner, text='', style='Card.TLabel', justify='left')
        self.summary.pack(fill='x')
        inner.bind('<Configure>', lambda e: self.summary.configure(wraplength=max(200, e.width-20)), add='+')
        self.progress_card, inner = card(body, 'Proceso')
        self.progress = ProgressPanel(inner, self.app, self.cancel)
        self.progress.pack(fill='x')

    def update_summary(self):
        self.summary.configure(text='\n'.join(self.summary_lines()))

    def summary_lines(self) -> list[str]:  # pragma: no cover - lo implementan las páginas
        return []

    def process(self):
        target = self.job.first_invalid_step()
        if target is not None:
            self.touched.update(range(self.PROCESS_STEP))
            self.go(target)
            return
        missing = self.required_parts()
        if missing:
            messagebox.showerror('No se puede procesar', self.app.backend.missing_message(missing), parent=self.app)
            return
        if self.path is None or self.dirty:
            if self.path is None and not messagebox.askokcancel(
                    'Guardar antes de procesar', 'El proyecto se guarda primero: la carpeta de trabajo con los '
                    'intermedios (RINEX unidos, configuración de RTKLIB, .pos y .stat) queda junto a él.',
                    parent=self.app):
                return
            self.save(then=self.process)
            return
        workdir = projects.workdir(self.project, self.path)
        job = deepcopy(self.job)
        project = deepcopy(self.project)
        self.progress_card.pack(fill='x', pady=(16, 0))
        self.progress.start('Preparando…')
        self.worker = 1   # marca de ocupado mientras arranca
        self.clear_alert('stale')
        self.refresh()

        def done(output):
            self.worker = None
            self.output = output
            self.progress.stop('Listo.')
            self.after_process(output)
            self.build_result(output)
            self.go(self.RESULT_STEP)
            self.app.set_status('Proceso terminado.')

        def failed(kind, message, detail):
            self.worker = None
            self.progress.stop(message)
            self.refresh()
            self.app.show_error(kind, message, detail, 'El proceso no terminó')

        def cancelled(message):
            self.worker = None
            self.progress.stop('Cancelado. Los archivos de entrada no cambiaron.')
            self.refresh()
            self.app.set_status('Proceso cancelado.')

        def work(progress, cancel):
            workdir.mkdir(parents=True, exist_ok=True)
            return self.work(job, project, workdir, progress, cancel)
        self.worker = tasks.run(self, work, on_progress=self.progress.update_progress, on_done=done, on_error=failed,
                                on_cancel=cancelled)

    @staticmethod
    def base_window(rover):
        """Ventana del rover para recortar las bases (con cinco minutos de margen), si se conoce."""
        from datetime import timedelta
        if rover.start is None or rover.end is None:
            return None
        return rover.start-timedelta(minutes=5), rover.end+timedelta(minutes=5)

    def required_parts(self) -> str | None:
        for key in ('core', 'crs'):
            if self.app.backend.parts.get(key) is None:
                return key
        return None

    def after_process(self, output):
        """Guarda la fecha del proceso (y lo que haga falta) en el proyecto, sin tocar las entradas."""
        self.project.processed = datetime.now(timezone.utc)
        try:
            projects.save(self.project, self.path, __version__)
        except OSError as exc:
            self.app.show_error('user', f'El resultado está listo, pero no se pudo actualizar el proyecto: {exc}')
        self.clear_alert('processed')

    def transform(self, position, choice):
        """Coordenada en el sistema elegido; un problema del sistema se vuelve aviso, no tira el proceso."""
        try:
            return self.app.backend.crs.transform(position, choice), None
        except Exception as exc:  # noqa: BLE001
            kind, message = tasks.classify(exc)
            if kind == 'program':
                raise
            return None, message

    # --- Resultado ----------------------------------------------------------------------------------------------

    def build_result(self, output):
        for child in self.result_body.winfo_children():
            child.destroy()
        actions = ttk.Frame(self.result_body)
        actions.pack(fill='x', pady=(0, 14))
        report = ttk.Button(actions, text='Ver informe', style='Primary.TButton', command=self.view_report)
        self.app.icons.bind(report, 'file-text')
        report.pack(side='left')
        if not self.app.backend.available('report'):
            report.state(['disabled'])
            Tooltip(report, self.app.backend.missing_message('report'), self.app.theme)
        export = self.export_button(actions)
        if export is not None:
            export.pack(side='left', padx=8)
        folder = ttk.Button(actions, text='Abrir carpeta de trabajo', style='Link.TButton',
                            command=lambda: self.app.guard(self.open_workdir))
        self.app.icons.bind(folder, 'folder-open', 16)
        folder.pack(side='left', padx=(8, 0))
        self.result_view(self.result_body, output)
        self.app.theme.walk(self.result_body)

    def open_workdir(self):
        from app.main_window import open_in_system
        open_in_system(projects.workdir(self.project, self.path))

    def export_button(self, parent):
        return None

    def report_path(self) -> Path:
        return projects.workdir(self.project, self.path)/f'Informe {self.path.stem}.pdf'

    def view_report(self):
        report = self.app.backend.report
        if report is None or self.output is None:
            return
        out = self.report_path()
        project, user, output = deepcopy(self.project), self.app.settings.profile(), self.output
        self.app.set_status('Armando el informe…')

        def done(path):
            from app.viewer import DocumentViewer
            self.app.set_status(f'Informe listo: {path}')
            DocumentViewer(self.app, path, f'Informe · {self.path.stem}', f'Informe {self.path.stem}.pdf')

        def failed(kind, message, detail):
            self.app.show_error(kind, message, detail, 'No se pudo armar el informe')
        tasks.run(self, lambda p, c: self.make_report(report, output, project, user, out), on_done=done,
                  on_error=failed)

    def make_report(self, report, output, project, user, out):  # pragma: no cover - lo implementan las páginas
        raise NotImplementedError

    def result_view(self, body, output):  # pragma: no cover - lo implementan las páginas
        pass

    def on_show(self):
        self.refresh()
