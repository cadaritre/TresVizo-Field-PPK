"""Estaciones RGNA: el catálogo oficial con coordenadas (ITRF2008 época 2010.0), antenas y su fuente."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.components import Banner, card, scrolled, wrap_label
from app.wizard_ui import ecef_text, rgna_catalog


class StationsPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.stations = []
        ttk.Label(self, text='Estaciones RGNA', style='Title.TLabel').pack(anchor='w')
        wrap_label(self, 'Red Geodésica Nacional Activa del INEGI: coordenadas oficiales en ITRF2008, época 2010.0. '
                         'Al elegir una estación en un trabajo se proponen estos datos; si corriges alguno, el informe '
                         'lo dice.').pack(anchor='w', fill='x', pady=(4, 14))
        if app.backend.core is None:
            Banner(self, app, 'error', app.backend.missing_message('core')).pack(fill='x', pady=(0, 12))
        search = ttk.Frame(self)
        search.pack(fill='x', pady=(0, 10))
        app.icons.label(search, 'search', 18).pack(side='left', padx=(0, 8))
        self.query = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.query)
        entry.pack(side='left', fill='x', expand=True)
        self.query.trace_add('write', lambda *a: self.fill())
        self.count = ttk.Label(search, text='', style='Subtitle.TLabel')
        self.count.pack(side='left', padx=12)
        panes = ttk.Frame(self)
        panes.pack(fill='both', expand=True)
        panes.columnconfigure(0, weight=3)
        panes.columnconfigure(1, weight=2)
        panes.rowconfigure(0, weight=1)
        table = ttk.Frame(panes, style='Card.TFrame', padding=1)
        table.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        self.tree = ttk.Treeview(table, columns=('id', 'name', 'antenna', 'source'), show='headings',
                                 selectmode='browse')
        for key, title, width, stretch in [('id', 'Clave', 70, False), ('name', 'Nombre', 220, True),
                                           ('antenna', 'Antena', 170, False), ('source', 'Coordenada', 110, False)]:
            self.tree.heading(key, text=title, anchor='w', command=lambda k=key: self.sort(k))
            self.tree.column(key, width=width, minwidth=60, stretch=stretch, anchor='w')
        scrolled(self.tree, table)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.details())
        frame, self.detail = card(panes, 'Detalle')
        frame.grid(row=0, column=1, sticky='nsew')
        self.detail_text = ttk.Label(self.detail, text='Elige una estación.', style='Card.TLabel', justify='left')
        self.detail_text.pack(fill='x', anchor='n')
        self.detail.bind('<Configure>', lambda e: self.detail_text.configure(wraplength=max(200, e.width-10)), add='+')
        self.sort_key = 'id'
        entry.focus_set()

    def on_show(self):
        if not self.stations:
            rgna_catalog(self.app, self.loaded)

    def loaded(self, stations):
        self.stations = list(stations)
        self.fill()

    def sort(self, key):
        self.sort_key = key
        self.fill()

    def fill(self):
        query = self.query.get().casefold().strip()
        self.tree.delete(*self.tree.get_children())
        rows = [s for s in self.stations if not query or query in f'{s.id} {s.name} {s.antenna_type}'.casefold()]
        rows.sort(key=lambda s: str({'id': s.id, 'name': s.name, 'antenna': s.antenna_type,
                                     'source': s.source}[self.sort_key]).casefold())
        for s in rows:
            self.tree.insert('', 'end', iid=s.id, values=(s.id, s.name, s.antenna_type or '—',
                                                         'oficial' if s.official else 'corregida'))
        self.count.configure(text=f'{len(rows)} de {len(self.stations)}')

    def details(self):
        chosen = self.tree.selection()
        s = next((x for x in self.stations if chosen and x.id == chosen[0]), None)
        if s is None:
            return
        sigma = (f'σ E {s.sigma_enu.e*1000:.1f} · N {s.sigma_enu.n*1000:.1f} · U {s.sigma_enu.u*1000:.1f} mm'
                 if s.sigma_enu else 'σ no publicada')
        self.detail_text.configure(text='\n'.join([
            f'{s.id} · {s.name}', '', f'{s.position.frame}, época {s.position.epoch:g}',
            ecef_text(s.position).replace('   ', '\n'), sigma, '',
            f'Antena: {s.antenna_type or "—"} {s.radome if s.radome and s.radome != "NONE" else ""}',
            f'Altura de antena: {s.antenna_height_m:.4f} m', f'Receptor: {s.receiver or "—"}', '',
            f'Fuente: {s.source or "—"}']))
