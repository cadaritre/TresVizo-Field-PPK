"""Asistente por pasos común a «Georreferenciar punto» y «PPK de puntos»: pasos, validación al momento, proyecto,
proceso en otro hilo con cancelar, resultado con «Ver informe» y «Exportar»."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import time
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


def choice_text(app, choice) -> str:
    """«México ITRF2008 / UTM, zona según la longitud · cota GGM10»: el sistema elegido en español."""
    from tresvizo_ppk.nombres import display_name
    name = choice.label
    crs = app.backend.crs
    if not name and crs is not None:
        try:
            name = next((s.name for s in crs.systems() if s.code == choice.crs_id), '')
        except Exception:  # noqa: BLE001 - es solo el texto del resumen
            name = ''
    text = display_name(name or choice.crs_id)
    return text + (f' · cota {choice.geoid_id.upper()}' if choice.geoid_id else ' · sin cota (altura elipsoidal)')


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
        # Avisos de la red para la estación (p. ej. hundimiento): a la vista al elegirla, no solo en el informe.
        self.notes = ttk.Label(self, text='', style='warning.TLabel', justify='left')
        self.bind('<Configure>', lambda e: (self.info.configure(wraplength=max(200, e.width-20)),
                                            self.notes.configure(wraplength=max(200, e.width-20))), add='+')
        self.fix = ttk.Button(self, text='Corregir coordenada…', style='Card.Link.TButton', command=self.correct)
        app.icons.bind(self.fix, 'pencil', 16)
        self.fix.grid(row=4, column=0, sticky='w', pady=(4, 0))
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
                'Bájalas en «Estaciones de referencia».'))
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
        self.notes.grid_forget()
        if s is None:
            self.var.set('')
            self.info.configure(text='Escribe la clave o el nombre, o elige de la lista.')
            self.fix.state(['disabled'])
            return
        from app.networks import station_notes
        notes = station_notes(self.app.backend.core, s)
        if notes:
            self.notes.configure(text='\n'.join('Aviso de la red: '+n for n in notes))
            self.notes.grid(row=3, column=0, columnspan=2, sticky='w', pady=(6, 0))
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
        # Ancho fijo y legible: el texto se envuelve en vez de estirar la ventana a lo ancho de la pantalla.
        width = int(560*app.ui_scale*app.theme.text_scale())
        ttk.Label(body, text=station_label(station), style='Section.TLabel').pack(anchor='w', pady=(0, 6))
        ttk.Label(body, text=f'Coordenada de la estación en {station.position.frame}, época '
                             f'{station.position.epoch:g}. Cámbiala solo si tienes una más reciente de la red; el '
                             'informe dirá que se corrigió y por qué.', style='Subtitle.TLabel', justify='left',
                  wraplength=width).pack(fill='x', pady=(0, 12))
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
                 inspect=None, options=None, role=None, distance=None):
        super().__init__(parent, style='Card.TFrame', padding=18)
        self.app, self.entry, self.changed, self.inspect = app, entry, changed, inspect
        self.antenna = None
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
        self.picker = StationPicker(self, app, entry, self.station_changed if options is not None else changed)
        self.picker.pack(fill='x', pady=(12, 0))
        if options is not None:
            # Antena y radomo de la estación: por omisión los del catálogo de su red; se pueden cambiar o importar.
            key = role or (lambda: f'base:{entry.station.id}' if entry.station else '')
            self.antenna = AntennaPanel(self, app, options, key, changed,
                                        distance=(lambda: distance(entry)) if distance else None)
            self.antenna.pack(fill='x', pady=(12, 0))
            self.station_data()
        self.files.set(entry.files)
        if not entry.files:
            self.files.pack_forget()
        else:
            self.read_files()

    def station_data(self):
        s = self.entry.station
        if self.antenna is not None:
            self.antenna.set_data(s.antenna_type if s else '', s.radome if s else 'NONE')

    def station_changed(self):
        self.station_data()
        self.changed()

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


# --- Antena y altura (§1.1.1) --------------------------------------------------------------------------------------

ANTEX_TYPES = [('Calibración de antena ANTEX', '*.atx *.ATX'), ('Todos los archivos', '*')]
FROM_DATA = 'La de los datos'
HEIGHT_METHOD_TEXT = (('arp', 'Vertical a la ARP'), ('mount', 'Vertical a la rosca o al gancho'),
                      ('slant', 'Inclinada al borde de la antena'))
UNMODELED_ERROR_KM = 5.0


def antenna_catalog(app, antex_files, callback):
    """(tipo, radomo, fuente) de las antenas con modelo, leídas en segundo plano; las del ANTEX incluido una vez."""
    core = app.backend.core
    if core is None or not callable(getattr(core, 'antenna_catalog', None)):
        callback([])
        return
    key = tuple(str(p) for p in antex_files)
    cache = getattr(app, 'antenna_cache', None)
    if cache is not None and cache[0] == key:
        callback(cache[1])
        return

    def done(found):
        app.antenna_cache = (key, list(found))
        callback(app.antenna_cache[1])
    tasks.run(app, lambda p, c: core.antenna_catalog(list(antex_files)), on_done=done,
              on_error=lambda kind, message, detail: callback([]))


class AntennaPanel(ttk.Frame):
    """Antena, radomo y altura de un extremo («rover» o «base:<clave>»), guardados en `options.antennas`.

    Por omisión, la antena de los datos (encabezado RINEX, producto Meridian o catálogo de la red). Se elige de la
    lista con búsqueda (los nombres de la IGS son largos y parecidos) o se importa un `.atx` (calibración individual
    de laboratorio, del NGS…), que se guarda en el proyecto y gana sobre el incluido. Si la antena no tiene modelo de
    centro de fase, aviso rojo con más de 5 km de línea base y amarillo si es más corta.

    Con `height_var` (el campo de altura de la página), el método de medida: vertical a la ARP, a la rosca o al gancho
    (con su desplazamiento a la ARP; el del case en los Meridian) o inclinada al borde: √(s² − r²) − d, con r y d de
    la tabla de la antena (con su fuente) o escritos por el usuario."""

    def __init__(self, parent, app, options, role, changed, *, height_var=None, distance=None):
        super().__init__(parent, style='Flat.Card.TFrame')
        self.app, self.options, self.changed_cb = app, options, changed
        self._role = role                    # texto o función (la clave de la base puede cambiar)
        self.height_var, self.distance = height_var, distance
        self.data = {'antenna_type': '', 'radome': 'NONE', 'case_offset': None, 'recording': False}
        self.catalog: list[tuple[str, str, str]] = []
        self.types: dict[str, list[tuple[str, str]]] = {}
        self._loading = False
        self._quiet = False                  # cambios hechos por el programa (datos leídos): no son del usuario
        sel = self.selection()
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text='Antena', style='Card.Strong.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 4))
        ttk.Label(self, text='Radomo', style='Card.Strong.TLabel').grid(row=0, column=1, sticky='w', padx=(12, 0),
                                                                       pady=(0, 4))
        self.type_var = tk.StringVar(value=sel.antenna_type if sel and sel.antenna_type else FROM_DATA)
        self.type_box = ttk.Combobox(self, textvariable=self.type_var)
        self.type_box.grid(row=1, column=0, sticky='ew')
        self.type_box.bind('<<ComboboxSelected>>', lambda e: self.pick_type())
        self.type_box.bind('<KeyRelease>', self.filter)
        self.type_box.bind('<Return>', lambda e: self.pick_type())
        self.type_box.bind('<FocusOut>', lambda e: self.pick_type(quiet=True))
        self.dome_var = tk.StringVar(value=sel.radome if sel and sel.radome else FROM_DATA)
        self.dome_box = ttk.Combobox(self, textvariable=self.dome_var, width=10)
        self.dome_box.grid(row=1, column=1, sticky='ew', padx=(12, 0))
        self.dome_box.bind('<<ComboboxSelected>>', lambda e: self.pick_dome())
        self.dome_box.bind('<Return>', lambda e: self.pick_dome())
        self.dome_box.bind('<FocusOut>', lambda e: self.pick_dome())
        actions = ttk.Frame(self, style='Flat.Card.TFrame')
        actions.grid(row=2, column=0, columnspan=2, sticky='w', pady=(4, 0))
        importer = ttk.Button(actions, text='Importar calibración (.atx)…', style='Card.Link.TButton',
                              command=self.import_antex)
        app.icons.bind(importer, 'file-up', 16)
        importer.pack(side='left')
        self.imported = ttk.Label(actions, text='', style='Card.Subtitle.TLabel')
        self.imported.pack(side='left', padx=(12, 0))
        self.forget = ttk.Button(actions, text='Quitar importados', style='Card.Link.TButton',
                                 command=self.remove_imported)
        self.status = wrap_label(self, style='Card.Subtitle.TLabel', margin=24)
        self.status.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        self.method_var = tk.StringVar(value=sel.height_method if sel else '')
        self.mount_var = tk.StringVar(value='' if not sel or sel.mount_offset_m is None else f'{sel.mount_offset_m:.4f}')
        self.radius_var = tk.StringVar(value='' if not sel or sel.slant_radius_m is None else
                                       f'{sel.slant_radius_m:.4f}')
        self.offset_var = tk.StringVar(value='' if not sel or sel.slant_offset_m is None else
                                       f'{sel.slant_offset_m:.4f}')
        self.dims_source = sel.dimensions_source if sel else ''
        if height_var is not None:
            self.build_height()
        self.show_imported()
        self.load()

    # --- estado ---------------------------------------------------------------------------------------------

    @property
    def role(self) -> str:
        return self._role() if callable(self._role) else self._role

    def selection(self):
        return self.options.antennas.get(self.role) if self.role else None

    def set_data(self, antenna_type='', radome='NONE', *, case_offset=None, recording=False):
        """Lo que traen los datos (RINEX, Meridian o catálogo): la antena por omisión y la rosca → ARP."""
        self.data = {'antenna_type': antenna_type or '', 'radome': radome or 'NONE', 'case_offset': case_offset,
                     'recording': recording}
        self._quiet = True
        try:
            if self.height_var is not None and not self.method_var.get():
                self.method_var.set('mount' if recording else 'arp')
            self.show_method()
            self.store()
        finally:
            self._quiet = False

    def chosen(self) -> tuple[str, str]:
        kind = self.type_var.get().strip()
        dome = self.dome_var.get().strip().upper()
        kind = self.data['antenna_type'] if not kind or kind == FROM_DATA else kind.split('·')[0].strip()
        dome = self.data['radome'] if not dome or dome == FROM_DATA.upper() else dome
        return kind, dome or 'NONE'

    def store(self):
        """Guarda la elección en `options.antennas[role]` (o la quita si todo es lo de los datos)."""
        role = self.role
        if not role:
            return
        from tresvizo_ppk.model import AntennaSelection
        kind = self.type_var.get().strip()
        dome = self.dome_var.get().strip().upper()
        sel = AntennaSelection(antenna_type='' if kind in ('', FROM_DATA) else kind.split('·')[0].strip(),
                               radome='' if dome in ('', FROM_DATA.upper()) else dome)
        if self.height_var is not None:
            from app.wizard import parse_number

            def number(var):
                try:
                    return parse_number(var.get()) if var.get().strip() else None
                except ValueError:
                    return None
            sel.height_method = self.method_var.get() or ('mount' if self.data['recording'] else 'arp')
            sel.measured_m = number(self.height_var)
            if sel.height_method == 'mount':
                sel.mount_offset_m = number(self.mount_var)
            elif sel.height_method == 'slant':
                sel.slant_radius_m, sel.slant_offset_m = number(self.radius_var), number(self.offset_var)
                sel.dimensions_source = self.dims_source
        default = (not sel.antenna_type and not sel.radome and sel.measured_m is None)
        last = getattr(self, '_last_role', None)
        if last and last != role:
            self.options.antennas.pop(last, None)      # la base cambió de clave: no dejar la elección vieja
        self._last_role = role
        if default:
            self.options.antennas.pop(role, None)
        elif self.options.antennas.get(role) != sel:
            self.options.antennas[role] = sel
        self.refresh()

    def edited(self):
        self.store()
        self.changed_cb()

    # --- catálogo -------------------------------------------------------------------------------------------

    def load(self):
        self._loading = True
        antenna_catalog(self.app, self.options.antex_files, self.loaded)

    def loaded(self, catalog):
        if not self.winfo_exists():
            return
        self._loading = False
        self.catalog = list(catalog)
        self.types = {}
        for kind, dome, source in self.catalog:
            self.types.setdefault(kind.upper(), []).append((dome, source))
        self.labels = [FROM_DATA] + sorted({f'{k} · {self.types[k.upper()][0][1]}' for k, _, _ in self.catalog})
        self.type_box.configure(values=self.labels)
        self.refresh()

    def filter(self, event):
        if event.keysym in ('Up', 'Down', 'Return', 'Escape', 'Tab'):
            return
        words = self.type_var.get().casefold().replace('·', ' ').split()
        values = [label for label in getattr(self, 'labels', []) if all(w in label.casefold() for w in words)]
        self.type_box.configure(values=values[:400] or [FROM_DATA])

    def pick_type(self, quiet=False):
        text = self.type_var.get().strip()
        kind = text.split('·')[0].strip()
        if text and text != FROM_DATA:
            self.type_var.set(kind.upper())
        domes = [d for d, _ in self.types.get(kind.upper(), [])]
        self.dome_box.configure(values=[FROM_DATA] + sorted(set(domes) | {'NONE'}))
        if self.height_var is not None and self.method_var.get() == 'slant' and \
                self.dims_source != 'escritos por el usuario':
            self.fill_dimensions(force=True)
        self.edited()

    def pick_dome(self):
        text = self.dome_var.get().strip()
        if text and text != FROM_DATA:
            self.dome_var.set(text.upper()[:4])
        self.edited()

    def import_antex(self):
        path = filedialog.askopenfilename(parent=self.app, title='Importar calibración de antena (ANTEX)',
                                          filetypes=ANTEX_TYPES)
        if not path:
            return
        core = self.app.backend.core
        try:
            found = core.read_antex(Path(path))
        except Exception as exc:  # noqa: BLE001 - el motivo va al usuario
            kind, message = tasks.classify(exc)
            self.app.show_error(kind, message, '', 'Importar calibración de antena')
            return
        files = [p for p in self.options.antex_files if Path(p).resolve() != Path(path).resolve()]
        self.options.antex_files[:] = [*files, Path(path)]
        self.app.antenna_cache = None
        if len(found) == 1:
            self.type_var.set(found[0][0])
            self.dome_var.set(found[0][1])
        self.show_imported()
        self.load()
        self.edited()

    def remove_imported(self):
        self.options.antex_files[:] = []
        self.app.antenna_cache = None
        self.show_imported()
        self.load()
        self.edited()

    def show_imported(self):
        names = [Path(p).name for p in self.options.antex_files]
        self.imported.configure(text=('Importados (ganan sobre el incluido): ' + ', '.join(names)) if names else '')
        if names and not self.forget.winfo_manager():
            self.forget.pack(side='left', padx=(12, 0))
        elif not names:
            self.forget.pack_forget()

    def refresh(self):
        """El modelo que se aplicaría y su aviso (rojo sin modelo a más de 5 km, amarillo si es más corta)."""
        kind, dome = self.chosen()
        if self.height_var is not None:
            self.show_height()
        if self._loading:
            self.status.configure(text='Leyendo los modelos de antena…', style='Card.Subtitle.TLabel')
            return
        if not kind:
            self.status.configure(text='Los datos no declaran la antena: elígela de la lista para aplicar su modelo '
                                       'de centro de fase.', style='warning.TLabel')
            return
        entries = dict(self.types.get(kind.upper(), []))
        km = self.distance() / 1000 if callable(self.distance) and self.distance() is not None else None
        if dome.upper() in entries:
            self.status.configure(text=f'Modelo {kind} {dome} · {entries[dome.upper()]}', style='Card.Subtitle.TLabel')
        elif 'NONE' in entries:
            self.status.configure(text=f'{kind} con radomo {dome} no está en ningún ANTEX: se usa {kind} NONE · '
                                       f'{entries["NONE"]} (diferencia típica de 1 a 3 mm en la vertical).',
                                  style='warning.TLabel')
        elif km is not None and km > UNMODELED_ERROR_KM:
            self.status.configure(text=f'{kind} {dome} no tiene modelo de centro de fase y la línea base mide '
                                       f'{km:.1f} km: la vertical puede tener un error de varios centímetros. Elige '
                                       'la antena correcta o importa su calibración (.atx).', style='error.TLabel')
        else:
            self.status.configure(text=f'{kind} {dome} no tiene modelo de centro de fase: se procesa con PCO/PCV 0. '
                                       'En una línea corta con antenas iguales se cancela en parte; si pasa de 5 km, '
                                       'el error es de centímetros en la vertical.', style='warning.TLabel')

    # --- altura ---------------------------------------------------------------------------------------------

    def build_height(self):
        from app.components import FlowFrame, LabeledEntry
        ttk.Label(self, text='Cómo se midió la altura', style='Card.Strong.TLabel').grid(row=4, column=0, columnspan=2,
                                                                                     sticky='w', pady=(12, 4))
        row = FlowFrame(self, style='Flat.Card.TFrame', gap=18)
        row.grid(row=5, column=0, columnspan=2, sticky='ew')
        for value, text in HEIGHT_METHOD_TEXT:
            row.add(ttk.Radiobutton(row, text=text, value=value, variable=self.method_var, style='Card.TRadiobutton',
                                    command=self.method_changed))
        self.fields = ttk.Frame(self, style='Flat.Card.TFrame')
        self.fields.grid(row=6, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        self.fields.columnconfigure((0, 1), weight=1, uniform='antenna')
        self.mount_field = LabeledEntry(self.fields, 'De la rosca o el gancho a la ARP (m)', self.mount_var,
                                        'En los Meridian, el del case (lo pone el programa); en las antenas del ANTEX '
                                        'la ARP suele ser la base de la rosca (0).')
        self.radius_field = LabeledEntry(self.fields, 'Radio r de la antena (m)', self.radius_var,
                                         'Del eje al borde donde se apoya la cinta.')
        self.offset_field = LabeledEntry(self.fields, 'Desfase d del borde a la ARP (m)', self.offset_var,
                                         'Lo que ese borde está arriba de la ARP (negativo si está abajo).')
        self.source_label = wrap_label(self.fields, style='Card.Subtitle.TLabel', margin=8)
        self.result_label = ttk.Label(self, text='', style='Card.TLabel', justify='left')
        self.result_label.grid(row=7, column=0, columnspan=2, sticky='w', pady=(8, 0))
        self.mount_var.trace_add('write', lambda *a: self.store() if self._quiet else self.edited())
        for var in (self.radius_var, self.offset_var):
            var.trace_add('write', lambda *a: self.store() if self._quiet else self.dimensions_edited())
        self.height_var.trace_add('write', lambda *a: self.store())
        self._quiet = True
        try:
            self.show_method()
        finally:
            self._quiet = False

    def dimensions_edited(self):
        """r o d escritos a mano: la fuente es la de la tabla solo si siguen siendo los de la tabla."""
        core = self.app.backend.core
        kind, _ = self.chosen()
        dims = core.slant_dimensions(kind) if core is not None and callable(getattr(core, 'slant_dimensions', None)) \
            else None
        same = dims is not None and self.radius_var.get().strip() == f'{dims["radius_m"]:.4f}' and \
            self.offset_var.get().strip() == f'{dims["offset_m"]:.4f}'
        self.dims_source = dims['source'] if same else 'escritos por el usuario'
        self.edited()

    def method_changed(self):
        if self.method_var.get() == 'slant':
            self.fill_dimensions()
        self.show_method()
        self.edited()

    def fill_dimensions(self, force=False):
        """r y d de la tabla de la antena (con su fuente), si están y el usuario no escribió otros."""
        core = self.app.backend.core
        kind, _ = self.chosen()
        dims = core.slant_dimensions(kind) if core is not None and callable(getattr(core, 'slant_dimensions', None)) \
            else None
        if dims and (force or not (self.radius_var.get().strip() or self.offset_var.get().strip())):
            self.dims_source = dims['source']
            self._quiet = True
            try:
                self.radius_var.set(f'{dims["radius_m"]:.4f}')
                self.offset_var.set(f'{dims["offset_m"]:.4f}')
            finally:
                self._quiet = False
        elif not dims and not self.dims_source:
            self.dims_source = ''

    def show_method(self):
        if not hasattr(self, 'fields'):
            return
        method = self.method_var.get() or 'arp'
        for widget in (self.mount_field, self.radius_field, self.offset_field, self.source_label):
            widget.grid_forget()
        if method == 'mount':
            if not self.mount_var.get().strip() and self.data['case_offset'] is not None:
                self.mount_var.set(f'{self.data["case_offset"]:.4f}')
            self.mount_field.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        elif method == 'slant':
            self.radius_field.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
            self.offset_field.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
            self.source_label.grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))

    def show_height(self):
        """La altura vertical a la ARP que va a usar el proceso, o qué falta para calcularla."""
        if not hasattr(self, 'result_label'):
            return
        method = self.method_var.get() or 'arp'
        sel = self.selection()
        if method == 'slant':
            self.source_label.configure(text=(f'Fuente de r y d: {self.dims_source}' if self.dims_source else
                                              'r y d escritos a mano (la antena no está en la tabla: tómalos de la '
                                              'hoja del fabricante).'))
        core = self.app.backend.core
        if sel is None or sel.measured_m is None or core is None or not callable(getattr(core, 'vertical_height',
                                                                                        None)):
            self.result_label.configure(text='')
            return
        try:
            height = core.vertical_height(sel, self.data['case_offset'] if self.data['case_offset'] is not None
                                          else 0.0)
        except Exception as exc:  # noqa: BLE001 - el motivo (UserError) se muestra junto al campo
            self.result_label.configure(text=str(exc), style='error.TLabel')
            return
        self.result_label.configure(text=f'Altura vertical a la ARP: {height:.4f} m', style='Card.TLabel')


class OptionsPanel(ttk.Frame):
    """Ajustes del proceso (§1.1.3) y sistema de coordenadas del resultado."""

    def __init__(self, parent, app, job, changed):
        super().__init__(parent)
        self.app, self.job, self.changed = app, job, changed
        o = job.options
        frame, body = card(self, 'Proyecto', 'Número o clave que sale en el encabezado del informe y lugar del bloque '
                                             'de firma (los dos opcionales).')
        frame.pack(fill='x', pady=(0, 16))
        from app.components import LabeledEntry
        self.number = tk.StringVar(value=job.number)
        self.place = tk.StringVar(value=job.place)
        self.number_field = LabeledEntry(body, 'Número', self.number, width=24)
        self.place_field = LabeledEntry(body, 'Lugar', self.place, 'Ciudad y estado de la firma, por ejemplo '
                                                                   '«Aguascalientes, Ags.».', width=36)
        body.columnconfigure((0, 1), weight=1, uniform='project')
        self.number_field.grid(row=0, column=0, sticky='new', padx=(0, 12))
        self.place_field.grid(row=0, column=1, sticky='new', padx=(12, 0))

        def arrange(event):
            # Dos columnas si caben; una sola en ventanas angostas o con letra grande.
            if event.width >= 560:
                self.number_field.grid(row=0, column=0, columnspan=1, padx=(0, 12), pady=0)
                self.place_field.grid(row=0, column=1, columnspan=1, padx=(12, 0), pady=0)
            else:
                self.number_field.grid(row=0, column=0, columnspan=2, padx=0, pady=0)
                self.place_field.grid(row=1, column=0, columnspan=2, padx=0, pady=(12, 0))
        body.bind('<Configure>', arrange, add='+')
        self.number.trace_add('write', lambda *a: self.apply())
        self.place.trace_add('write', lambda *a: self.apply())
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
        self.job.place = self.place.get()
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
        # Generación del proyecto en pantalla: cambia al empezar uno nuevo o abrir otro. Lo que termina en segundo plano
        # (proceso, lecturas, guardado, informe) se descarta si su generación ya no es la actual.
        self.generation = 0
        self.background: list[tasks.Worker] = []
        self._stopping = False
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

    def begin_generation(self):
        """Un proyecto nuevo en pantalla: las tareas del anterior se cancelan y sus resultados se descartan."""
        self.generation += 1
        if isinstance(self.worker, tasks.Worker):
            self.worker.cancel.set()     # solo si alguien recarga sin pasar por confirm_discard
        self.worker = None
        for worker in self.background:
            worker.cancel.set()
        self.background = []
        for _key, worker in self.__dict__.get('_inspecting', {}).values():
            worker.cancel.set()
        self.__dict__['_inspecting'] = {}

    def run_task(self, work, **callbacks):
        """`tasks.run` atado a la generación actual: si el proyecto cambió, sus avisos y su resultado no llegan."""
        generation = self.generation

        def guard(callback):
            if callback is None:
                return None

            def call(*args):
                if self.generation == generation and self.winfo_exists():
                    callback(*args)
            return call
        worker = tasks.run(self, work, **{k: guard(v) for k, v in callbacks.items()})
        self.background = [w for w in self.background if not w.finished]+[worker]
        return worker

    def reset(self):
        self.begin_generation()
        self.job = self.new_job()
        self.path: Path | None = None
        self.project: Project | None = None
        self.output = None
        self.dirty = False
        self.touched: set[int] = set()
        self.step = 0
        for child in self.alerts.winfo_children():
            child.destroy()
        self.shrink_alerts()
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
        self.shrink_alerts()

    def shrink_alerts(self):
        """Tk no encoge un marco al quitarle su último hijo: sin avisos, el hueco se cierra a mano."""
        if not self.alerts.winfo_children():
            self.alerts.configure(height=1)

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
        if isinstance(self.worker, tasks.Worker):
            self.worker.cancel.set()
            self.progress.cancelling()

    STOP_TIMEOUT_S = 120.0

    def stop_processing(self) -> bool:
        """Cancela el proceso en marcha y espera a que su hilo termine (la interfaz sigue respondiendo). False si no
        se detuvo a tiempo: entonces no se puede reconstruir la página ni tocar su carpeta de trabajo."""
        worker = self.worker
        if not isinstance(worker, tasks.Worker):
            return worker is None
        self.cancel()
        self.app.set_status('Cancelando el proceso…')
        end = time.monotonic()+self.STOP_TIMEOUT_S
        self._stopping = True
        try:
            self.app.configure(cursor='watch')
            while (self.worker is not None or worker.thread.is_alive()) and time.monotonic() < end:
                self.app.update()
                time.sleep(0.02)
        finally:
            self._stopping = False
            if self.app.winfo_exists():
                self.app.configure(cursor='')
        if worker.thread.is_alive():
            messagebox.showwarning('El proceso sigue', 'El proceso todavía no se detiene. Espera a que termine el paso '
                                   'actual y vuelve a intentarlo.', parent=self.app)
            return False
        if self.worker is worker:
            # El hilo terminó pero su último aviso aún no se entregó: se da por cancelado aquí.
            self.worker = None
            self.progress.stop('Cancelado. Los archivos de entrada no cambiaron.')
        return True

    # --- Lectura de los archivos al soltarlos: los campos se llenan solos ------------------------------------------

    def inspect(self, slot, files, callback, extra=None):
        """Lee los archivos en segundo plano (encabezado y sesión) y llama `callback(ObservationSet)` en el hilo de la
        interfaz. Si los archivos cambian antes de terminar, el resultado viejo se descarta. La copia temporal se
        borra al terminar."""
        import shutil
        import tempfile
        core = self.app.backend.core
        files = [Path(p) for p in files]
        if core is None or not files or not all(p.is_file() for p in files):
            return
        key = tuple((str(p), p.stat().st_mtime_ns) for p in files)
        cache = self.__dict__.setdefault('_inspected', {})
        # Lo leído sin `extra` (p. ej. al soltar los mismos archivos como estación de una red) no sirve cuando ahora
        # se piden las candidatas de la base: se vuelve a leer.
        if key in cache and (extra is None or hasattr(cache[key], 'ui_extra')):
            callback(cache[key])
            return
        workers = self.__dict__.setdefault('_inspecting', {})
        old = workers.get(slot)
        if old is not None:
            old[1].cancel.set()
        temporal = self.app.settings.data_dir()/'Temporal'

        def work(progress, cancel):
            # Una carpeta propia por lectura: dos lecturas de los mismos archivos ya no se borran una a otra.
            temporal.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix='lectura-', dir=temporal))
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
        workers[slot] = (key, self.run_task(work, on_done=done, on_error=failed, on_cancel=lambda m: None))

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
    def approx_text(obs) -> str:
        """«Posición aproximada 19° 03′ 21.4″ N  98° 09′ 30.7″ O · del encabezado RINEX» (o vacío si no la trae)."""
        p = getattr(obs, 'approx_position', None)
        if p is None:
            return ''
        from app.charts import ecef_to_geodetic
        from app.formatting import dms
        lat, lon = ecef_to_geodetic(p)
        source = (getattr(obs, 'approx_position_source', '') or 'encabezado RINEX').strip()
        article = 'de la' if source.split()[0] in ('sesión', 'grabación', 'solución', 'media') else 'del'
        return f'Posición aproximada {dms(lat, "N", "S", 1)}  {dms(lon, "E", "O", 1)} · {article} {source}'

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
        import shutil
        import tempfile
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
        temporal = self.app.settings.data_dir()/'Temporal'
        core = self.app.backend.core

        def work(progress, cancel):
            temporal.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix='sesion-', dir=temporal))
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
        self.run_task(work, on_done=done,
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
        if self._stopping:
            return False
        if self.busy():
            if closing:
                return True      # al salir, la ventana ya preguntó y cancela el proceso
            if not messagebox.askyesno('Hay un proceso en marcha', f'«{self.TITLE}» está procesando. ¿Cancelarlo?',
                                       icon='warning', parent=self.app):
                return False
            if not self.stop_processing():
                return False
        if not self.dirty:
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
            self.app.set_status(f'Proyecto guardado: {path.name}.', path.parent)
            if then:
                then()

        def failed(kind, message, detail):
            self.app.show_error(kind, message, detail, 'No se pudo guardar el proyecto')
        self.run_task(lambda p, c: self._write(path, p, c), on_done=done, on_error=failed)

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
        self.begin_generation()
        self.job = self.JOB.from_project(project, files)
        self.project, self.path, self.output, self.dirty = project, Path(path), None, False
        self.touched = set(range(self.PROCESS_STEP))
        for child in self.alerts.winfo_children():
            child.destroy()
        self.shrink_alerts()
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
        self.run_task(lambda p, c: projects.check_inputs(project, p, c), on_done=checked,
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
        self.worker = self.run_task(work, on_progress=self.progress.update_progress, on_done=done, on_error=failed,
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
            self.app.set_status(f'Informe listo: {Path(path).name}.', Path(path).parent)
            DocumentViewer(self.app, path, f'Informe · {self.path.stem}', f'Informe {self.path.stem}.pdf')

        def failed(kind, message, detail):
            self.app.show_error(kind, message, detail, 'No se pudo armar el informe')
        self.run_task(lambda p, c: self.make_report(report, output, project, user, out), on_done=done,
                      on_error=failed)

    def make_report(self, report, output, project, user, out):  # pragma: no cover - lo implementan las páginas
        raise NotImplementedError

    def result_view(self, body, output):  # pragma: no cover - lo implementan las páginas
        pass

    def on_show(self):
        self.refresh()
