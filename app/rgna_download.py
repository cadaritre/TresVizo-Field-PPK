"""Descarga de RINEX de una estación de la RGNA (§3.2), solo si el motor la ofrece y siempre a petición."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import tkinter as tk
from tkinter import ttk

from app import tasks
from app.components import ProgressPanel, wrap_label
from app.wizard_ui import rgna_catalog, station_label


class RgnaDownloadDialog(tk.Toplevel):
    def __init__(self, app, on_done):
        super().__init__(app)
        self.app, self.on_done, self.worker, self.stations = app, on_done, None, []
        self.title('Descargar de la RGNA')
        self.transient(app)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=20)
        body.pack(fill='both', expand=True)
        wrap_label(body, 'Se bajan del servicio del INEGI los RINEX de la estación para el día y las horas UTC de tu '
                         'sesión (con una hora de margen). Se guardan en tu carpeta de datos, en «Descargas/RGNA».',
                   margin=10).pack(fill='x', pady=(0, 12))
        grid = ttk.Frame(body)
        grid.pack(fill='x')
        self.station = tk.StringVar()
        self.date = tk.StringVar(value=datetime.now(timezone.utc).strftime('%d/%m/%Y'))
        self.start = tk.StringVar(value='00:00')
        self.end = tk.StringVar(value='23:59')
        self.box = ttk.Combobox(grid, textvariable=self.station, width=36)
        for row, (label, widget) in enumerate([('Estación', self.box),
                                               ('Fecha (UTC)', ttk.Entry(grid, textvariable=self.date, width=14)),
                                               ('Desde (hh:mm UTC)', ttk.Entry(grid, textvariable=self.start, width=8)),
                                               ('Hasta (hh:mm UTC)', ttk.Entry(grid, textvariable=self.end, width=8))]):
            ttk.Label(grid, text=label).grid(row=row, column=0, sticky='w', pady=4, padx=(0, 12))
            widget.grid(row=row, column=1, sticky='w', pady=4)
        self.error = ttk.Label(body, text='', style='error.TLabel')
        self.progress = ProgressPanel(body, app, self.cancel)
        buttons = ttk.Frame(body)
        buttons.pack(side='bottom', fill='x', pady=(16, 0))
        ttk.Button(buttons, text='Cerrar', command=self.close).pack(side='right')
        self.go_button = ttk.Button(buttons, text='Descargar', style='Primary.TButton', command=self.download)
        app.icons.bind(self.go_button, 'download')
        self.go_button.pack(side='right', padx=8)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda e: self.close())
        app.theme.walk(self)
        app.set_window_icon(self)
        rgna_catalog(app, self.loaded)
        self.box.focus_set()

    def loaded(self, stations):
        if self.winfo_exists():
            self.stations = sorted(stations, key=lambda s: s.id)
            self.box.configure(values=[station_label(s) for s in self.stations])

    def parse(self):
        text = self.station.get().strip()
        key = text.split('·')[0].strip().upper()
        station = next((s for s in self.stations if s.id.upper() == key), None)
        if station is None:
            raise ValueError('Elige una estación del catálogo de la RGNA.')
        try:
            day = datetime.strptime(self.date.get().strip(), '%d/%m/%Y').replace(tzinfo=timezone.utc)
            h1, m1 = (int(x) for x in self.start.get().split(':'))
            h2, m2 = (int(x) for x in self.end.get().split(':'))
            start = day+timedelta(hours=h1, minutes=m1)
            end = day+timedelta(hours=h2, minutes=m2)
        except ValueError:
            raise ValueError('Escribe la fecha como dd/mm/aaaa y las horas como hh:mm.') from None
        if end <= start:
            raise ValueError('La hora final debe ser posterior a la inicial.')
        if start > datetime.now(timezone.utc):
            raise ValueError('La fecha es futura.')
        return station, start-timedelta(hours=1), end+timedelta(hours=1)

    def download(self):
        try:
            station, start, end = self.parse()
        except ValueError as exc:
            self.error.configure(text=str(exc))
            self.error.pack(fill='x', pady=(10, 0))
            return
        self.error.pack_forget()
        folder = self.app.settings.downloads_dir()/'RGNA'/station.id
        core = self.app.backend.core
        self.progress.pack(fill='x', pady=(12, 0))
        self.progress.start(f'Bajando {station.id}…')
        self.go_button.state(['disabled'])

        def done(paths):
            self.worker = None
            self.progress.stop(f'{len(paths)} archivo(s) bajados.')
            self.on_done(station, list(paths))
            self.destroy()

        def failed(kind, message, detail):
            self.worker = None
            self.progress.stop(message)
            self.go_button.state(['!disabled'])
            if kind == 'program':
                self.app.show_error(kind, message, detail)

        def cancelled(message):
            self.worker = None
            self.progress.stop('Cancelado.')
            self.go_button.state(['!disabled'])
        self.worker = tasks.run(self, lambda p, c: core.download_rgna(station.id, start, end, folder, p, c),
                                on_progress=self.progress.update_progress, on_done=done, on_error=failed,
                                on_cancel=cancelled)

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.progress.cancelling()

    def close(self):
        self.cancel()
        self.destroy()


class RgnaGuideDialog(tk.Toplevel):
    """Qué pedir en la página de RINEX de la RGNA para cubrir la sesión (lo calcula el motor)."""

    URL = 'https://www.inegi.org.mx/app/geo2/rgna/'

    def __init__(self, app, station, text):
        super().__init__(app)
        self.app = app
        self.title(f'RINEX de {station.id}')
        self.transient(app)
        body = ttk.Frame(self, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f'{station.id} · {station.name}', style='Section.TLabel').pack(anchor='w')
        box = tk.Text(body, wrap='word', height=8, width=72, padx=12, pady=10, relief='flat', highlightthickness=1)
        box.insert('1.0', text)
        box.configure(state='disabled')
        box.pack(fill='both', expand=True, pady=(10, 0))
        self.text = text
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(14, 0))
        web = ttk.Button(buttons, text='Abrir la página del INEGI', style='Primary.TButton', command=self.open_web)
        app.icons.bind(web, 'external-link')
        web.pack(side='left')
        copy = ttk.Button(buttons, text='Copiar', command=self.copy)
        app.icons.bind(copy, 'copy')
        copy.pack(side='left', padx=8)
        ttk.Button(buttons, text='Cerrar', command=self.destroy).pack(side='right')
        self.bind('<Escape>', lambda e: self.destroy())
        app.theme.walk(self)
        app.set_window_icon(self)

    def open_web(self):
        import webbrowser
        webbrowser.open(self.URL, new=2)
        self.app.set_status('Se abrió la página de la RGNA en el navegador.')

    def copy(self):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.text)
        self.app.set_status('Indicaciones copiadas.')
