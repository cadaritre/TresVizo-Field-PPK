"""Sistemas y geoides (§1.3): el catálogo firmado de TresVizo Field y la elección de sistema, zona y geoide."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from tresvizo_ppk.model import CoordinateChoice
from tresvizo_ppk.nombres import display_name

from app import tasks
from app.components import Banner, ProgressPanel, ScrollFrame, card, scrolled, wrap_label

KIND_TEXT = {'factory': 'Sistemas de fábrica', 'crs-package': 'Paquete de sistemas', 'geoid': 'Geoide',
             'datum-grid': 'Rejilla de datum', 'cors-network': 'Red de estaciones (CORS)'}
NO_GEOID = 'Sin geoide (solo altura elipsoidal)'
AUTO_ZONE = 'Automática (por la longitud del punto)'
MEXICO_ZONES = [f'{n}N' for n in range(11, 17)]
WORLD_ZONES = [f'{n}{h}' for h in 'NS' for n in range(1, 61)]


def item_state(item) -> str:
    if item.bundled:
        return 'De fábrica'
    if item.installed and item.update_available:
        return 'Actualización disponible'
    if item.installed:
        return 'Instalado'
    return 'Disponible para bajar' if item.downloadable else '—'


class CoordinateChooser(ttk.Frame):
    """Sistema, zona y geoide para mostrar y exportar el resultado. `changed()` avisa de cada cambio."""

    def __init__(self, parent, app, choice: CoordinateChoice, changed=lambda: None, style_prefix='Card.'):
        super().__init__(parent, style='Flat.Card.TFrame' if style_prefix else 'TFrame')
        self.app, self.changed = app, changed
        self.style_prefix = style_prefix
        self.columnconfigure((0, 1, 2), weight=1, uniform='crs')
        self.systems, self.geoids = [], []
        self.system_var, self.zone_var, self.geoid_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        label_style = style_prefix+'Strong.TLabel' if style_prefix else 'TLabel'
        for column, text in enumerate(('Sistema de coordenadas', 'Zona', 'Geoide (cota)')):
            ttk.Label(self, text=text, style=label_style).grid(row=0, column=column, sticky='w', padx=(0, 12),
                                                               pady=(0, 4))
        self.system_box = ttk.Combobox(self, textvariable=self.system_var, state='readonly')
        self.zone_box = ttk.Combobox(self, textvariable=self.zone_var, state='readonly')
        self.geoid_box = ttk.Combobox(self, textvariable=self.geoid_var, state='readonly')
        for column, box in enumerate((self.system_box, self.zone_box, self.geoid_box)):
            box.grid(row=1, column=column, sticky='ew', padx=(0, 12))
            box.bind('<<ComboboxSelected>>', self.on_change)
        self.note = ttk.Label(self, text='', style=style_prefix+'Subtitle.TLabel' if style_prefix else
                              'Subtitle.TLabel', justify='left')
        self.note.grid(row=2, column=0, columnspan=3, sticky='w', pady=(6, 0))
        self.bind('<Configure>', lambda e: self.note.configure(wraplength=max(200, e.width-20)), add='+')
        self.choice = choice
        self.reload()

    def reload(self):
        crs = self.app.backend.crs
        if crs is None:
            for box in (self.system_box, self.zone_box, self.geoid_box):
                box.state(['disabled'])
            self.note.configure(text=self.app.backend.missing_message('crs'))
            return
        try:
            self.systems = crs.systems()
            self.geoids = [i for i in crs.catalog() if i.kind == 'geoid' and i.installed]
        except Exception as exc:  # noqa: BLE001
            kind, message = tasks.classify(exc)
            self.note.configure(text=message)
            return
        self.system_box.configure(values=[self.system_text(s) for s in self.systems])
        self.geoid_box.configure(values=[NO_GEOID]+[g.title for g in self.geoids])
        self.set(self.choice)

    @staticmethod
    def system_text(s):
        return display_name(s.name)+(f' · {s.accuracy_text}' if s.accuracy_text else '')

    def set(self, choice: CoordinateChoice):
        self.choice = choice
        system = next((s for s in self.systems if s.code == choice.crs_id), None)
        if system is None and self.systems:
            system = self.systems[0]
        if system is not None:
            self.system_var.set(self.system_text(system))
        self._zones(system)
        self.zone_var.set(choice.zone if choice.zone else (AUTO_ZONE if system and system.kind == 'family' else '—'))
        geoid = next((g for g in self.geoids if g.id == choice.geoid_id), None)
        self.geoid_var.set(geoid.title if geoid else NO_GEOID)
        self._note(system)

    def _zones(self, system):
        if system is not None and system.kind == 'family':
            zones = MEXICO_ZONES if system.code.startswith('mexico') else WORLD_ZONES
            self.zone_box.configure(values=[AUTO_ZONE]+zones)
            self.zone_box.state(['!disabled', 'readonly'])
        else:
            self.zone_box.configure(values=['—'])
            self.zone_var.set('—')
            self.zone_box.state(['disabled'])

    def _note(self, system):
        if system is None:
            self.note.configure(text='')
            return
        parts = [p for p in (display_name(system.datum), system.frame, system.accuracy_text) if p]
        if system.missing_grids:
            parts.append('Falta la rejilla de transformación: bájala en Sistemas y geoides.')
        if self.geoid_var.get() == NO_GEOID:
            parts.append('Sin geoide no hay cota: el resultado lleva altura elipsoidal.')
        self.note.configure(text=' · '.join(parts))

    def on_change(self, event=None):
        index = self.system_box.current()
        system = self.systems[index] if 0 <= index < len(self.systems) else None
        if event is not None and event.widget is self.system_box:
            self._zones(system)
            if system is not None and system.kind == 'family':
                self.zone_var.set(AUTO_ZONE)
            if system is not None and system.geoid_model_id and any(g.id == system.geoid_model_id for g in self.geoids):
                self.geoid_var.set(next(g.title for g in self.geoids if g.id == system.geoid_model_id))
        self.choice = self.get()
        self._note(system)
        self.changed()

    def get(self) -> CoordinateChoice:
        index = self.system_box.current()
        system = self.systems[index] if 0 <= index < len(self.systems) else None
        zone = self.zone_var.get()
        geoid_index = self.geoid_box.current()
        geoid = self.geoids[geoid_index-1] if geoid_index > 0 and geoid_index-1 < len(self.geoids) else None
        if system is None:
            return self.choice
        return CoordinateChoice(crs_id=system.code, zone='' if zone in (AUTO_ZONE, '—', '') else zone,
                                geoid_id=geoid.id if geoid else None,
                                label=display_name(system.name))


class CrsPage(ttk.Frame):
    """Catálogo profesional: una sección por clase, búsqueda al instante, filtro de estado, agrupado por región y
    país, todo el índice visible (lo no instalado se instala desde aquí) y detalle completo de cada entrada."""

    STATES = [('installed', 'Instalados'), ('available', 'Disponibles'), ('all', 'Todos')]

    def __init__(self, parent, app):
        super().__init__(parent)
        from app import catalog_view
        from app.components import FlowFrame
        self.app, self.cv = app, catalog_view
        self.entries, self.worker, self.cls, self.state = [], None, 'projected', 'all'
        self.loaded_groups: set[str] = set()
        ttk.Label(self, text='Sistemas y geoides', style='Title.TLabel').pack(anchor='w')
        wrap_label(self, 'El catálogo firmado de TresVizo Field: sistemas, geoides, rejillas y redes CORS. Lo de fábrica '
                         'viene incluido; lo demás se baja de github.com/cadaritre/GNSS-TresVizo solo cuando lo pides y '
                         'se comprueba su firma antes de usarlo.').pack(anchor='w', fill='x', pady=(4, 10))
        # Todo lo de abajo se desplaza junto: en ventanas bajas la lista conserva un alto útil.
        self.page_scroll = ScrollFrame(self, padding=(0, 0, 10, 8))
        self.page_scroll.pack(fill='both', expand=True)
        page = self.page_scroll.body
        self.notice = ttk.Frame(page)
        self.notice.pack(fill='x')
        self.segments = FlowFrame(page)
        self.segments.pack(fill='x', pady=(0, 4))
        self.segment_buttons = {}
        for cls, (title, icon, _) in catalog_view.CLASSES.items():
            button = ttk.Button(self.segments, text=title, style='Seg.TButton', command=lambda c=cls: self.pick(c))
            app.icons.bind(button, icon, 16, color_token=self.cv.CLASSES[cls][2])
            self.segments.add(button)
            self.segment_buttons[cls] = button
        tools = FlowFrame(page)
        tools.pack(fill='x', pady=(4, 8))
        search = ttk.Frame(tools)
        app.icons.label(search, 'search', 16).pack(side='left', padx=(0, 6))
        self.query = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.query, width=34)
        entry.pack(side='left')
        from app.components import Tooltip
        Tooltip(entry, 'Nombre, EPSG, país, datum o zona; sin importar acentos ni mayúsculas.', app.theme)
        self.query.trace_add('write', lambda *a: self.after_idle(self.refresh_list))
        tools.add(search)
        states = ttk.Frame(tools)
        self.state_buttons = {}
        for key, label in self.STATES:
            button = ttk.Button(states, text=label, style='Seg.TButton', command=lambda k=key: self.pick_state(k))
            button.pack(side='left', padx=(0, 4))
            self.state_buttons[key] = button
        tools.add(states)
        self.refresh_button = ttk.Button(tools, text='Actualizar catálogo', command=self.download_index)
        app.icons.bind(self.refresh_button, 'refresh-cw', 16)
        tools.add(self.refresh_button)
        self.body = ttk.Frame(page)
        self.body.pack(fill='both', expand=True)
        self.list_card = ttk.Frame(self.body, style='Card.TFrame', padding=1)
        self.tree = ttk.Treeview(self.list_card, columns=('code', 'state'), show='tree headings', selectmode='browse')
        self.tree.heading('#0', text='Nombre', anchor='w')
        self.tree.heading('code', text='Código', anchor='w')
        self.tree.heading('state', text='Estado', anchor='w')
        self.tree.column('#0', width=330, minwidth=200, stretch=True)
        self.tree.column('code', width=110, minwidth=80, stretch=False)
        self.tree.column('state', width=120, minwidth=100, stretch=False)
        scrolled(self.tree, self.list_card)
        from app.components import CellTips
        CellTips(self.tree, app)
        self.tree.bind('<<TreeviewOpen>>', self.expand)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_detail())
        self.detail_scroll = ttk.Frame(self.body)
        self.detail = ttk.Frame(self.detail_scroll, style='Card.TFrame', padding=16)
        self.detail.pack(fill='x', anchor='n')
        self.body.bind('<Configure>', self.arrange, add='+')
        self.bind('<Configure>', self.fit_rows, add='+')
        self.arranged = None
        if app.backend.crs is None:
            Banner(self.notice, app, 'error', app.backend.missing_message('crs')).pack(fill='x', pady=(0, 8))
            self.refresh_button.state(['disabled'])
        self.paint_segments()

    # --- Disposición: lista y detalle lado a lado, o uno sobre otro si la ventana es angosta -----------------------

    def fit_rows(self, event=None):
        """Filas visibles de la lista según el alto de la ventana (entre 8 y 22)."""
        row = int(ttk.Style(self).lookup('Treeview', 'rowheight') or 30)
        rows = max(8, min(22, (self.winfo_height()-300)//max(1, row)))
        if int(self.tree.cget('height')) != rows:
            self.tree.configure(height=rows)

    def arrange(self, event=None):
        wide = self.body.winfo_width() >= 860*self.app.theme.text_scale()
        if wide == self.arranged:
            return
        self.arranged = wide
        self.list_card.grid_forget()
        self.detail_scroll.grid_forget()
        if wide:
            # Proporción fija (uniform): lo que pida la tabla no le quita espacio al detalle.
            self.body.columnconfigure(0, weight=5, uniform='catalogo')
            self.body.columnconfigure(1, weight=3, uniform='catalogo')
            self.body.rowconfigure(0, weight=1)
            self.body.rowconfigure(1, weight=0)
            self.list_card.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
            self.detail_scroll.grid(row=0, column=1, sticky='nsew')
        else:
            self.body.columnconfigure(0, weight=1, uniform='')
            self.body.columnconfigure(1, weight=0, uniform='')
            self.body.rowconfigure(0, weight=3)
            self.body.rowconfigure(1, weight=2)
            self.list_card.grid(row=0, column=0, sticky='nsew', pady=(0, 10))
            self.detail_scroll.grid(row=1, column=0, sticky='nsew')

    # --- Datos ------------------------------------------------------------------------------------------------

    def on_show(self):
        self.reload()

    def reload(self):
        crs = self.app.backend.crs
        if crs is None:
            return
        from app.wizard_ui import network_catalog

        def with_networks(found):
            try:
                self.entries = self.cv.build(crs, found)
            except Exception as exc:  # noqa: BLE001
                kind, message = tasks.classify(exc)
                self.app.show_error(kind, message, repr(exc))
                self.entries = []
            self.paint_segments()
            self.refresh_list()
        network_catalog(self.app, with_networks)

    def pick(self, cls):
        self.cls = cls
        self.paint_segments()
        self.refresh_list()

    def pick_state(self, state):
        self.state = state
        self.paint_segments()
        self.refresh_list()

    def paint_segments(self):
        counts = self.cv.counts(self.entries, self.query.get(), self.state) if self.entries else {}
        for cls, button in self.segment_buttons.items():
            title = self.cv.CLASSES[cls][0]
            button.configure(text=f'{title}  {counts.get(cls, 0)}' if self.entries else title,
                             style='Seg.Active.TButton' if cls == self.cls else 'Seg.TButton')
            button.refresh_icon()
        for key, button in self.state_buttons.items():
            button.configure(style='Seg.Active.TButton' if key == self.state else 'Seg.TButton')

    def refresh_list(self):
        self.paint_segments()
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.loaded_groups = set()
        self.visible = self.cv.select(self.entries, self.cls, self.query.get(), self.state)
        self.groups = self.cv.grouped(self.visible)
        searching = bool(self.query.get().strip())
        for region, countries in self.groups.items():
            total = sum(len(v) for v in countries.values())
            rid = 'r:'+region
            self.tree.insert('', 'end', iid=rid, text=f'{region}  ·  {total}', open=True, tags=('group',))
            for country, items in countries.items():
                code = next((c for c in items[0].countries if self.cv.country_region(c)[0] == country), '')
                cid = f'c:{region}:{country}'
                label = f'{code} · {country}' if code else country
                self.tree.insert(rid, 'end', iid=cid, text=f'{label}  ·  {len(items)}', open=False,
                                 tags=('country',))
                if searching or len(self.visible) <= 60:
                    self.fill_group(cid)
                    self.tree.item(cid, open=True)
                else:
                    self.tree.insert(cid, 'end', iid=cid+':…', text='…')   # se llena al abrir (lista perezosa)
        tokens = self.app.theme.tokens
        self.tree.tag_configure('group', font=self.app.theme.fonts.strong(), foreground=tokens['text'])
        self.tree.tag_configure('country', foreground=tokens['muted_surface'])
        if not self.visible:
            self.tree.insert('', 'end', iid='vacío', text='Nada coincide con la búsqueda y el filtro.')
        from app.components import fit_columns
        fit_columns(self.tree, self.app, stretch='#0')
        keep = [i for i in selected if self.tree.exists(i)]
        if not keep:
            first = next((i for r in self.tree.get_children() for c in self.tree.get_children(r)
                          for i in self.tree.get_children(c) if '|' in i), None)
            keep = [first] if first else []
        if keep:
            self.tree.selection_set(keep)
            self.tree.see(keep[0])
        else:
            self.show_detail()

    def fill_group(self, cid):
        if cid in self.loaded_groups:
            return
        self.loaded_groups.add(cid)
        _, region, country = cid.split(':', 2)
        for child in self.tree.get_children(cid):
            self.tree.delete(child)
        for entry in self.groups.get(region, {}).get(country, []):
            iid = f'{cid}|{entry.key}'
            self.tree.insert(cid, 'end', iid=iid, text=entry.title, values=(entry.code or '—', entry.short_state))

    def expand(self, event=None):
        focus = self.tree.focus()
        if focus.startswith('c:'):
            self.fill_group(focus)

    def selected_entry(self):
        chosen = self.tree.selection()
        if not chosen or '|' not in chosen[0]:
            return None
        key = chosen[0].split('|', 1)[1]
        return next((e for e in self.entries if e.key == key), None)

    # --- Detalle ----------------------------------------------------------------------------------------------

    def show_detail(self):
        from app.components import FlowFrame
        for child in self.detail.winfo_children():
            child.destroy()
        entry = self.selected_entry()
        if entry is None:
            ttk.Label(self.detail, text='Elige una entrada de la lista para ver su detalle.',
                      style='Card.Subtitle.TLabel').pack(anchor='w')
            return
        caps = FlowFrame(self.detail, style='Flat.Card.TFrame')
        caps.pack(fill='x')
        title, icon, _ = self.cv.CLASSES[entry.cls]
        cap = ttk.Label(caps, text=self.cv.SHORT[entry.cls], style=f'Cap.{entry.cls}.TLabel')
        cap.image = self.app.icons.photo(icon, self.app.icons.size(14), self.app.theme.tokens[f'cap_{entry.cls}_fg'])
        cap.configure(image=cap.image, compound='left')
        caps.add(cap)
        state_style = {'De fábrica': 'Neutral', 'Instalado': 'Installed', 'Actualización disponible': 'Update',
                       'Disponible para bajar': 'Available'}.get(entry.state, 'Neutral')
        caps.add(ttk.Label(caps, text=entry.state, style=f'Cap.{state_style}.TLabel'))
        for code in entry.countries:
            caps.add(ttk.Label(caps, text=f'{code} · {self.cv.country_region(code)[0]}', style='Cap.Neutral.TLabel'))
        wrap_label(self.detail, entry.title, style='Card.Section.TLabel', margin=40).pack(fill='x', pady=(10, 2))
        if entry.subtitle:
            wrap_label(self.detail, entry.subtitle, style='Card.Subtitle.TLabel', margin=40).pack(fill='x')
        grid = ttk.Frame(self.detail, style='Flat.Card.TFrame')
        grid.pack(fill='x', pady=(10, 0))
        grid.columnconfigure(1, weight=1)
        values = []
        for row, (label, value) in enumerate(entry.details):
            ttk.Label(grid, text=label, style='Card.Subtitle.TLabel').grid(row=row, column=0, sticky='nw', pady=2,
                                                                          padx=(0, 12))
            v = ttk.Label(grid, text=value, style='Card.TLabel', justify='left')
            v.grid(row=row, column=1, sticky='nw', pady=2)
            values.append(v)
        def wrap(event):
            first = grid.grid_bbox(0, 0)[2] if values else 0
            for v in values:
                if v.winfo_exists():
                    v.configure(wraplength=max(120, event.width-first-16))
        grid.bind('<Configure>', wrap, add='+')
        if entry.package and entry.cls in ('projected', 'geographic') and not entry.key.startswith('pkg:'):
            wrap_label(self.detail, f'Lo trae: {entry.package}', style='Card.Subtitle.TLabel', margin=40).pack(
                fill='x', pady=(8, 0))
        actions = FlowFrame(self.detail, style='Flat.Card.TFrame')
        actions.pack(fill='x', pady=(12, 0))
        busy = self.worker is not None
        if entry.downloadable and (not entry.installed or entry.update):
            install = ttk.Button(actions, text='Actualizar' if entry.installed else 'Bajar e instalar',
                                 style='Primary.TButton', command=lambda: self.install(entry))
            self.app.icons.bind(install, 'download', 16)
            install.state(['disabled'] if busy else ['!disabled'])
            actions.add(install)
        if entry.installed and entry.item_id and not entry.bundled:
            remove = ttk.Button(actions, text='Quitar', command=lambda: self.uninstall(entry))
            self.app.icons.bind(remove, 'trash-2', 16)
            remove.state(['disabled'] if busy else ['!disabled'])
            actions.add(remove)
        self.progress = ProgressPanel(self.detail, self.app, self.cancel)
        self.app.theme.walk(self.detail)

    # --- Acciones ---------------------------------------------------------------------------------------------

    def _run(self, work, message, done_text):
        self.progress.pack(fill='x', pady=(12, 0))
        self.progress.start(message)
        self.refresh_button.state(['disabled'])

        def finish(text=''):
            self.worker = None
            self.refresh_button.state(['!disabled'])
            self.app.networks_cache = None
            self.app.set_status(text)
            self.reload()

        def error(kind, message, detail):
            finish(message)
            self.app.show_error(kind, message, detail)
        self.worker = tasks.run(self, work, on_progress=self.progress.update_progress,
                                on_done=lambda _: finish(done_text), on_error=error,
                                on_cancel=lambda m: finish('Cancelado. No se instaló nada a medias.'))

    def download_index(self):
        crs = self.app.backend.crs
        self.show_detail()   # el panel de progreso vive en el detalle
        self._run(lambda p, c: crs.download_index(p, c),
                  'Bajando el catálogo firmado de github.com/cadaritre/GNSS-TresVizo…',
                  'Catálogo actualizado y verificado (firma Ed25519).')

    def install(self, entry):
        size = f' ({entry.size})' if entry.size else ''
        if not messagebox.askokcancel('Bajar e instalar', f'Se bajará «{entry.package or entry.title}»{size} de '
                                      'github.com/cadaritre/GNSS-TresVizo y se comprobará contra el catálogo firmado '
                                      'antes de usarlo.', parent=self.app):
            return
        crs = self.app.backend.crs
        self._run(lambda p, c: crs.install(entry.item_id, p, c), f'Bajando {entry.package or entry.title}…',
                  f'«{entry.package or entry.title}» instalado.')

    def uninstall(self, entry):
        what = entry.package or entry.title
        if not messagebox.askyesno('Quitar', f'¿Quitar «{what}» de este equipo? Los proyectos que lo usan dirán que '
                                   'falta hasta que lo vuelvas a bajar.', icon='warning', parent=self.app):
            return
        self.app.guard(self.app.backend.crs.uninstall, entry.item_id)
        self.app.networks_cache = None
        self.reload()

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.progress.cancelling()

    def busy(self):
        return self.worker is not None
