"""Guía para bajar los RINEX de una estación de referencia desde la página de su red (no hay descarga automática
documentada para la RGNA; si una red la ofrece, el motor la expondrá y aquí se agregará)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class GuideDialog(tk.Toplevel):
    """Qué pedir en la página de la red para cubrir la sesión (lo calcula el motor), con su enlace."""

    def __init__(self, app, network, station, text):
        super().__init__(app)
        self.app, self.url, self.text = app, network.url, text
        self.title(f'RINEX de {station.id}')
        self.transient(app)
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
        copy = ttk.Button(buttons, text='Copiar', command=self.copy)
        app.icons.bind(copy, 'copy')
        copy.pack(side='left', padx=8)
        ttk.Button(buttons, text='Cerrar', command=self.destroy).pack(side='right')
        self.bind('<Escape>', lambda e: self.destroy())
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
