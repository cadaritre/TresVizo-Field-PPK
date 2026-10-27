"""Estaciones de referencia (§3.0): las redes CORS instaladas (país, marco y época oficial, fuente y licencia), las que se
pueden bajar del índice firmado, y las estaciones de cada red con su coordenada oficial y su antena."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from app import tasks
from app.components import Banner, CellTips, ProgressPanel, ScrollFrame, card, fit_columns, scrolled, wrap_label
from app.wizard_ui import ecef_text, network_catalog, station_label


class NetworksPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.networks, self.available, self.worker = [], [], None
        ttk.Label(self, text='Estaciones de referencia', style='Title.TLabel').pack(anchor='w')
        wrap_label(self, 'Redes de estaciones GNSS de operación continua (CORS), como la RGNA del INEGI.').pack(
            anchor='w', fill='x', pady=(4, 0))
        wrap_label(self, 'Cada red trae las coordenadas oficiales de sus estaciones, en su marco y época. Al elegir una '
                         'estación en un trabajo se proponen estos datos; si corriges alguno, el informe lo dice. Las '
                         'redes nuevas se bajan del catálogo firmado sin cambiar de versión.').pack(
            anchor='w', fill='x', pady=(4, 14))
        if app.backend.core is None:
            Banner(self, app, 'error', app.backend.missing_message('core')).pack(fill='x', pady=(0, 12))
        scroll = ScrollFrame(self, padding=(0, 0, 12, 12))
        scroll.pack(fill='both', expand=True)
        body = scroll.body
        frame, inner = card(body, 'Redes')
        frame.pack(fill='x')
        holder = ttk.Frame(inner, style='Flat.Card.TFrame')
        holder.pack(fill='x')
        self.net_tree = ttk.Treeview(holder, columns=('name', 'country', 'frame', 'stations', 'state'),
                                     show='headings', height=4, selectmode='browse')
        for key, title, width, stretch in [('name', 'Red', 260, True), ('country', 'País', 90, False),
                                           ('frame', 'Marco y época oficial', 200, False),
                                           ('stations', 'Estaciones', 90, False), ('state', 'Estado', 170, False)]:
            self.net_tree.heading(key, text=title, anchor='w')
            self.net_tree.column(key, width=width, minwidth=200 if key == 'name' else 70, stretch=stretch, anchor='w')
        scrolled(self.net_tree, holder)
        self.net_tree.bind('<<TreeviewSelect>>', lambda e: self.network_selected())
        self.net_detail = ttk.Label(inner, text='', style='Card.Subtitle.TLabel', justify='left')
        self.net_detail.pack(fill='x', pady=(10, 0))
        inner.bind('<Configure>', lambda e: self.net_detail.configure(wraplength=max(200, e.width-20)), add='+')
        actions = ttk.Frame(inner, style='Flat.Card.TFrame')
        actions.pack(fill='x', pady=(10, 0))
        self.install_button = ttk.Button(actions, text='Bajar e instalar', style='Primary.TButton',
                                         command=self.install)
        app.icons.bind(self.install_button, 'download')
        self.install_button.pack(side='left')
        self.page_button = ttk.Button(actions, text='Página de la red', style='Card.Link.TButton',
                                      command=self.open_page)
        app.icons.bind(self.page_button, 'external-link', 16)
        self.page_button.pack(side='left', padx=8)
        self.progress = ProgressPanel(inner, app, self.cancel)
        frame, inner = card(body, 'Estaciones')
        frame.pack(fill='both', expand=True, pady=(16, 0))
        self.stations_title = frame.winfo_children()[0]
        search = ttk.Frame(inner, style='Flat.Card.TFrame')
        search.pack(fill='x', pady=(0, 10))
        app.icons.label(search, 'search', 18, background='surface', style='Card.TLabel').pack(side='left', padx=(0, 8))
        self.query = tk.StringVar()
        ttk.Entry(search, textvariable=self.query).pack(side='left', fill='x', expand=True)
        self.query.trace_add('write', lambda *a: self.fill())
        self.count = ttk.Label(search, text='', style='Card.Subtitle.TLabel')
        self.count.pack(side='left', padx=12)
        panes = ttk.Frame(inner, style='Flat.Card.TFrame')
        panes.pack(fill='both', expand=True)
        panes.columnconfigure(0, weight=3)
        panes.columnconfigure(1, weight=2)
        table = ttk.Frame(panes, style='Flat.Card.TFrame')
        table.grid(row=0, column=0, rowspan=2, sticky='nsew', padx=(0, 12))
        self.tree = ttk.Treeview(table, columns=('id', 'name', 'antenna', 'source'), show='headings', height=12,
                                 selectmode='browse')
        for key, title, width, stretch in [('id', 'Clave', 70, False), ('name', 'Nombre', 220, True),
                                           ('antenna', 'Antena', 170, False), ('source', 'Coordenada', 100, False)]:
            self.tree.heading(key, text=title, anchor='w', command=lambda k=key: self.sort(k))
            self.tree.column(key, width=width, minwidth=60, stretch=stretch, anchor='w')
        scrolled(self.tree, table)
        CellTips(self.tree, app)
        CellTips(self.net_tree, app)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.details())
        self.detail_text = ttk.Label(panes, text='Elige una estación.', style='Card.TLabel', justify='left')
        self.detail_text.grid(row=0, column=1, sticky='nw')
        self.detail_notes = ttk.Label(panes, text='', style='warning.TLabel', justify='left')
        panes.bind('<Configure>', lambda e: (self.detail_text.configure(wraplength=max(200, e.width*2//5-10)),
                                             self.detail_notes.configure(wraplength=max(200, e.width*2//5-10))),
                   add='+')
        self.sort_key = 'id'

    def on_show(self):
        if not self.networks:
            self.reload()

    def reload(self):
        self.app.networks_cache = None
        network_catalog(self.app, self.loaded)

    def loaded(self, networks):
        self.networks = list(networks)
        crs = self.app.backend.crs
        installed = {n.id for n in self.networks}
        try:
            self.available = [i for i in (crs.catalog() if crs else []) if i.kind == 'cors-network'
                              and (not i.installed or i.update_available) and i.id not in installed]
        except Exception:  # noqa: BLE001 - el catálogo descargable es opcional aquí
            self.available = []
        selected = self.net_tree.selection()
        self.net_tree.delete(*self.net_tree.get_children())
        for n in self.networks:
            self.net_tree.insert('', 'end', iid='net:'+n.id, values=(n.name, n.country or '—', n.frame_text,
                                                                    len(n.stations),
                                                                    'Incluida' if n.origin == 'incluida' else
                                                                    'Instalada'))
        for item in self.available:
            self.net_tree.insert('', 'end', iid='pkg:'+item.id, values=(item.title, item.region or '—', '—', '—',
                                                                       'Disponible para bajar'))
        self.net_tree.configure(height=max(2, min(6, len(self.networks)+len(self.available))))
        fit_columns(self.net_tree, self.app, stretch='name')
        keep = [i for i in selected if self.net_tree.exists(i)]
        children = self.net_tree.get_children()
        if keep or children:
            self.net_tree.selection_set(keep or children[0])
        self.network_selected()

    def selected(self):
        chosen = self.net_tree.selection()
        if not chosen:
            return None, None
        kind, key = chosen[0].split(':', 1)
        if kind == 'net':
            return next((n for n in self.networks if n.id == key), None), None
        return None, next((i for i in self.available if i.id == key), None)

    def network_selected(self):
        network, package = self.selected()
        self.install_button.state(['!disabled'] if package and self.worker is None else ['disabled'])
        self.page_button.state(['!disabled'] if network and network.url else ['disabled'])
        if network:
            lines = [x for x in (network.source and f'Fuente: {network.source}', network.attribution,
                                 network.license and f'Licencia: {network.license}') if x]
            self.net_detail.configure(text='\n'.join(lines))
            self.stations_title.configure(text=f'Estaciones · {network.name}')
        elif package:
            self.net_detail.configure(text='\n'.join(x for x in (package.authority and f'Fuente: {package.authority}',
                                                                  package.license and f'Licencia: {package.license}',
                                                                  package.attribution, package.size_text) if x))
            self.stations_title.configure(text='Estaciones')
        self.fill()

    def open_page(self):
        network, _ = self.selected()
        if network and network.url:
            import webbrowser
            webbrowser.open(network.url, new=2)

    def install(self):
        _, package = self.selected()
        if package is None or not messagebox.askokcancel(
                'Bajar e instalar', f'Se bajará «{package.title}» de github.com/cadaritre/GNSS-TresVizo y se comprobará '
                'contra el catálogo firmado antes de usarlo.', parent=self.app):
            return
        crs = self.app.backend.crs
        self.progress.pack(fill='x', pady=(12, 0))
        self.progress.start(f'Bajando {package.title}…')

        def finish(text):
            self.worker = None
            self.progress.stop(text)
            self.reload()

        def failed(kind, message, detail):
            finish(message)
            self.app.show_error(kind, message, detail)
        self.worker = tasks.run(self, lambda p, c: crs.install(package.id, p, c),
                                on_progress=self.progress.update_progress,
                                on_done=lambda _: finish(f'«{package.title}» instalada.'), on_error=failed,
                                on_cancel=lambda m: finish('Cancelado. No se instaló nada a medias.'))
        self.network_selected()

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.progress.cancelling()

    def busy(self):
        return self.worker is not None

    def sort(self, key):
        self.sort_key = key
        self.fill()

    def stations(self):
        network, _ = self.selected()
        return network.stations if network else []

    def fill(self):
        query = self.query.get().casefold().strip()
        stations = self.stations()
        self.tree.delete(*self.tree.get_children())
        rows = [s for s in stations if not query or query in f'{s.id} {s.name} {s.antenna_type}'.casefold()]
        rows.sort(key=lambda s: str({'id': s.id, 'name': s.name, 'antenna': s.antenna_type,
                                     'source': s.official}[self.sort_key]).casefold())
        for s in rows:
            self.tree.insert('', 'end', iid=s.id, values=(s.id, station_label(s).split(' · ', 1)[-1],
                                                         s.antenna_type or '—', 'oficial' if s.official else
                                                         'corregida'))
        self.count.configure(text=f'{len(rows)} de {len(stations)}')
        fit_columns(self.tree, self.app, stretch='name')

    def details(self):
        chosen = self.tree.selection()
        s = next((x for x in self.stations() if chosen and x.id == chosen[0]), None)
        if s is None:
            return
        sigma = (f'σ E {s.sigma_enu.e*1000:.1f} · N {s.sigma_enu.n*1000:.1f} · U {s.sigma_enu.u*1000:.1f} mm'
                 if s.sigma_enu else 'σ no publicada por la red')
        self.detail_text.configure(text='\n'.join([
            station_label(s), '', f'{s.position.frame}, época {s.position.epoch:g}',
            ecef_text(s.position).replace('   ', '\n'), sigma, '',
            f'Antena: {s.antenna_type or "—"} {s.radome if s.radome and s.radome != "NONE" else ""}',
            f'Altura de antena: {s.antenna_height_m:.4f} m', f'Receptor: {s.receiver or "—"}', '',
            f'Fuente: {s.source or "—"}']))
        from app.networks import station_notes
        notes = station_notes(self.app.backend.core, s)
        self.detail_notes.configure(text='\n\n'.join('Aviso de la red: '+n for n in notes))
        if notes:
            self.detail_notes.grid(row=1, column=1, sticky='nw', pady=(10, 0))
        else:
            self.detail_notes.grid_forget()
