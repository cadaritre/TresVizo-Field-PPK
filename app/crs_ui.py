"""Sistemas y geoides (§1.3): el catálogo firmado de TresVizo Field y la elección de sistema, zona y geoide."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from tresvizo_ppk.model import CoordinateChoice

from app import tasks
from app.components import Banner, ProgressPanel, ScrollFrame, card, wrap_label

KIND_TEXT = {'factory': 'Sistemas de fábrica', 'crs-package': 'Paquete de sistemas', 'geoid': 'Geoide',
             'datum-grid': 'Rejilla de datum'}
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
        return s.name+(f' · {s.accuracy_text}' if s.accuracy_text else '')

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
        parts = [p for p in (system.datum, system.frame, system.accuracy_text) if p]
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
                                label=system.name)


class CrsPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.worker = None
        self.items = []
        ttk.Label(self, text='Sistemas y geoides', style='Title.TLabel').pack(anchor='w')
        wrap_label(self, 'El mismo catálogo firmado que las apps TresVizo Field. Vienen incluidos WGS 84, Mexico ITRF2008 '
                         'con UTM 11N a 16N, GGM10 y GGM25; lo demás se baja de github.com/cadaritre/GNSS-TresVizo '
                         'solo cuando lo pides, y se comprueba su firma antes de usarlo.').pack(anchor='w', fill='x',
                                                                                                 pady=(4, 14))
        self.notice = ttk.Frame(self)
        self.notice.pack(fill='x')
        scroll = ScrollFrame(self)
        scroll.pack(fill='both', expand=True)
        body = scroll.body
        frame, inner = card(body, 'Catálogo')
        frame.pack(fill='x')
        actions = ttk.Frame(inner, style='Flat.Card.TFrame')
        actions.pack(fill='x', pady=(0, 10))
        self.refresh_button = ttk.Button(actions, text='Actualizar catálogo', command=self.download_index)
        app.icons.bind(self.refresh_button, 'refresh-cw')
        self.refresh_button.pack(side='left')
        self.install_button = ttk.Button(actions, text='Bajar e instalar', style='Primary.TButton',
                                         command=self.install)
        app.icons.bind(self.install_button, 'download')
        self.install_button.pack(side='left', padx=8)
        self.remove_button = ttk.Button(actions, text='Quitar', style='Card.Link.TButton', command=self.uninstall)
        app.icons.bind(self.remove_button, 'trash-2', 16)
        self.remove_button.pack(side='left')
        self.tree = ttk.Treeview(inner, columns=('title', 'kind', 'region', 'version', 'size', 'state'),
                                 show='headings', height=9, selectmode='browse')
        for key, title, width, stretch in [('title', 'Nombre', 300, True), ('kind', 'Tipo', 150, False),
                                           ('region', 'Región', 140, False), ('version', 'Versión', 90, False),
                                           ('size', 'Tamaño', 80, False), ('state', 'Estado', 170, False)]:
            self.tree.heading(key, text=title, anchor='w')
            self.tree.column(key, width=width, minwidth=60, stretch=stretch, anchor='w')
        self.tree.pack(fill='x')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_details())
        self.details = ttk.Label(inner, text='', style='Card.Subtitle.TLabel', justify='left')
        self.details.pack(fill='x', pady=(10, 0))
        inner.bind('<Configure>', lambda e: self.details.configure(wraplength=max(200, e.width-20)), add='+')
        self.progress = ProgressPanel(inner, app, self.cancel)
        frame, inner = card(body, 'Sistema por omisión',
                            'El que se propone en cada proyecto nuevo; cada proyecto guarda el suyo.')
        frame.pack(fill='x', pady=(18, 0))
        self.chooser = CoordinateChooser(inner, app, app.settings.coordinates(), changed=self.default_changed)
        self.chooser.pack(fill='x')
        self.saved_label = ttk.Label(inner, text='', style='Card.Subtitle.TLabel')
        self.saved_label.pack(anchor='w', pady=(8, 0))
        if app.backend.crs is None:
            Banner(self.notice, app, 'error', app.backend.missing_message('crs')).pack(fill='x', pady=(0, 12))
            for button in (self.refresh_button, self.install_button, self.remove_button):
                button.state(['disabled'])

    def on_show(self):
        self.refresh()

    def refresh(self):
        crs = self.app.backend.crs
        if crs is None:
            return
        selected = self.tree.selection()
        self.items = crs.catalog()
        self.tree.delete(*self.tree.get_children())
        for item in self.items:
            self.tree.insert('', 'end', iid=item.id, values=(item.title, KIND_TEXT.get(item.kind, item.kind),
                                                             item.region, item.version or '—', item.size_text or '—',
                                                             item_state(item)))
        keep = [i for i in selected if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        elif self.items:
            self.tree.selection_set(self.items[0].id)
        self.show_details()
        self.chooser.reload()

    def selected(self):
        chosen = self.tree.selection()
        return next((i for i in self.items if chosen and i.id == chosen[0]), None)

    def show_details(self):
        item = self.selected()
        busy = self.worker is not None
        if item is None:
            self.details.configure(text='')
            self.install_button.state(['disabled'])
            self.remove_button.state(['disabled'])
            return
        lines = [x for x in (item.authority and f'Fuente: {item.authority}',
                             item.license and f'Licencia: {item.license}' + (f' ({item.license_url})'
                                                                             if item.license_url else ''),
                             item.attribution, item.transformation_notice,
                             item.systems and f'{item.systems} sistemas',
                             item.requires and 'Necesita: '+', '.join(item.requires)) if x]
        self.details.configure(text='\n'.join(lines))
        can_install = item.downloadable and (not item.installed or item.update_available) and not busy
        self.install_button.configure(text='Actualizar' if item.installed and item.update_available else
                                      'Bajar e instalar')
        self.install_button.refresh_icon()
        self.install_button.state(['!disabled'] if can_install else ['disabled'])
        self.remove_button.state(['!disabled'] if item.installed and not item.bundled and not busy else ['disabled'])

    def _run(self, work, message, done_text):
        self.progress.pack(fill='x', pady=(12, 0))
        self.progress.start(message)
        for button in (self.refresh_button, self.install_button, self.remove_button):
            button.state(['disabled'])

        def finish(text=''):
            self.worker = None
            self.progress.stop(text)
            self.refresh_button.state(['!disabled'])
            self.refresh()

        def done(_):
            finish(done_text)
            self.app.set_status(done_text)

        def error(kind, message, detail):
            finish(message)
            self.app.show_error(kind, message, detail)

        self.worker = tasks.run(self, work, on_progress=self.progress.update_progress, on_done=done, on_error=error,
                                on_cancel=lambda m: finish('Cancelado. No se instaló nada a medias.'))

    def download_index(self):
        crs = self.app.backend.crs
        self._run(lambda p, c: crs.download_index(p, c),
                  'Bajando el catálogo firmado de github.com/cadaritre/GNSS-TresVizo…', 'Catálogo actualizado y '
                  'verificado (firma Ed25519).')

    def install(self):
        item = self.selected()
        if item is None:
            return
        size = f' ({item.size_text})' if item.size_text else ''
        if not messagebox.askokcancel('Bajar e instalar', f'Se bajará «{item.title}»{size} de '
                                      'github.com/cadaritre/GNSS-TresVizo y se comprobará contra el catálogo firmado '
                                      'antes de usarlo.', parent=self):
            return
        crs = self.app.backend.crs
        self._run(lambda p, c: crs.install(item.id, p, c), f'Bajando {item.title}…', f'«{item.title}» instalado.')

    def uninstall(self):
        item = self.selected()
        if item is None or not messagebox.askyesno('Quitar', f'¿Quitar «{item.title}» de este equipo? Los proyectos '
                                                   'que lo usan dirán que falta hasta que lo vuelvas a bajar.',
                                                   icon='warning', parent=self):
            return
        self.app.guard(self.app.backend.crs.uninstall, item.id)
        self.refresh()

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.progress.cancelling()

    def busy(self):
        return self.worker is not None

    def default_changed(self):
        choice = self.chooser.get()
        self.app.settings.save(crs_id=choice.crs_id, zone=choice.zone, geoid_id=choice.geoid_id)
        self.saved_label.configure(text='Guardado como sistema por omisión.')
