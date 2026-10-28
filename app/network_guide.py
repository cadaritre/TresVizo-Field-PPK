"""Guía para bajar los RINEX de una estación de referencia desde la página de su red (no hay descarga automática
documentada para la RGNA; si una red la ofrece, el motor la expondrá y aquí se agregará)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.components import attach_dialog


class GuideDialog(tk.Toplevel):
    """Qué pedir en la página de la red para cubrir la sesión (lo calcula el motor), con su enlace."""

    def __init__(self, app, network, station, text, download=None, on_files=None):
        super().__init__(app)
        self.app, self.url, self.text = app, network.url, text
        self.download, self.on_files, self.worker = download, on_files, None
        self.title(f'RINEX de {station.id}')
        attach_dialog(self, app)
        body = ttk.Frame(self, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f'{station.id} · {network.name}', style='Section.TLabel').pack(anchor='w')
        box = tk.Text(body, wrap='word', height=8, width=72, padx=12, pady=10, relief='flat', highlightthickness=1)
        box.insert('1.0', text)
        box.configure(state='disabled')
        box.pack(fill='both', expand=True, pady=(10, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(14, 0))
        if self.url:
            web = ttk.Button(buttons, text='Abrir la página de la red', style='Primary.TButton', command=self.open_web)
            app.icons.bind(web, 'external-link')
            web.pack(side='left')
        if download is not None:
            get = ttk.Button(buttons, text='Bajar ahora', style='Primary.TButton', command=self.fetch)
            app.icons.bind(get, 'download')
            get.pack(side='left', padx=(8, 0))
            self.get_button = get
        copy = ttk.Button(buttons, text='Copiar', command=self.copy)
        app.icons.bind(copy, 'copy')
        copy.pack(side='left', padx=8)
        ttk.Button(buttons, text='Cerrar', command=self.close).pack(side='right')
        from app.components import ProgressPanel
        self.progress = ProgressPanel(body, app, self.cancel)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda e: self.close())
        app.theme.walk(self)
        app.set_window_icon(self)

    def open_web(self):
        import webbrowser
        webbrowser.open(self.url, new=2)
        self.app.set_status('Se abrió la página de la red en el navegador.')

    def copy(self):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.text)
        self.app.set_status('Indicaciones copiadas.')

    def fetch(self):
        from app import tasks
        self.get_button.state(['disabled'])
        self.progress.pack(fill='x', pady=(12, 0))
        self.progress.start('Bajando los RINEX de la estación…')

        def done(paths):
            self.worker = None
            from app.formatting import plural
            self.progress.stop(f'{plural(len(paths), "archivo bajado", "archivos bajados")}.')
            if self.on_files:
                self.on_files(list(paths))
            self.destroy()

        def failed(kind, message, detail):
            self.worker = None
            self.progress.stop(message)
            self.get_button.state(['!disabled'])
            if kind == 'program':
                self.app.show_error(kind, message, detail)
        self.worker = tasks.run(self, self.download, on_progress=self.progress.update_progress, on_done=done,
                                on_error=failed, on_cancel=lambda m: (self.progress.stop('Cancelado.'),
                                                                      self.get_button.state(['!disabled'])))

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.progress.cancelling()

    def close(self):
        self.cancel()
        self.destroy()
