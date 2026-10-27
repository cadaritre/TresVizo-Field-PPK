"""Inicio: empezar un trabajo nuevo, abrir un proyecto o retomar uno reciente."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from tkinter import ttk

from app.components import Banner, ScrollFrame, card, wrap_label

KINDS = {'static': 'Georreferenciar punto', 'points': 'PPK de puntos'}


def project_summary(path: Path) -> tuple[str, str, str]:
    """(tipo, carpeta, modificado) leyendo solo lo necesario; «no está» si ya no existe."""
    try:
        stat = path.stat()
        kind = '—'
        if stat.st_size < 5_000_000:
            data = json.loads(path.read_text(encoding='utf-8'))
            kind = KINDS.get(data.get('kind'), '—')
        return kind, str(path.parent), datetime.fromtimestamp(stat.st_mtime).strftime('%d/%m/%Y %H:%M')
    except FileNotFoundError:
        return 'No está en su carpeta', str(path.parent), '—'
    except (OSError, ValueError):
        return 'No se pudo leer', str(path.parent), '—'


class HomePage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        scroll = ScrollFrame(self)
        scroll.pack(fill='both', expand=True)
        body = scroll.body
        ttk.Label(body, text='Inicio', style='Title.TLabel').pack(anchor='w')
        wrap_label(body, 'Posproceso de los receptores MeridianV y MeridianVI ligado a la Red Geodésica Nacional '
                         'Activa del INEGI. Elige qué quieres hacer o retoma un proyecto.').pack(anchor='w', fill='x',
                                                                                                 pady=(4, 18))
        self.notices = ttk.Frame(body)
        self.notices.pack(fill='x')
        tiles = ttk.Frame(body)
        tiles.pack(fill='x')
        tiles.columnconfigure((0, 1), weight=1, uniform='tiles')
        for column, (name, icon, text) in enumerate([
                ('Georreferenciar punto', 'map-pin',
                 'Un punto estático contra una a tres estaciones de la RGNA, con ajuste por mínimos cuadrados e informe '
                 'de línea base.'),
                ('PPK de puntos', 'route',
                 'La jornada cinemática del receptor y el CSV de TresVizo Field, con base de la RGNA o propia.')]):
            frame = ttk.Frame(tiles, style='Card.TFrame', padding=18)
            frame.grid(row=0, column=column, sticky='nsew', padx=(0, 12) if column == 0 else (12, 0))
            app.icons.label(frame, icon, 30, token='primary', background='surface', style='Card.TLabel').pack(
                anchor='w')
            ttk.Label(frame, text=name, style='Card.Section.TLabel').pack(anchor='w', pady=(10, 4))
            wrap_label(frame, text, style='Card.Subtitle.TLabel', margin=40).pack(anchor='w', fill='x')
            button = ttk.Button(frame, text='Empezar', style='Primary.TButton',
                                command=lambda n=name: app.new_job(n))
            app.icons.bind(button, 'arrow-right')
            button.pack(anchor='w', pady=(14, 0))
        recents, inner = card(body, 'Proyectos recientes',
                              'Doble clic o Intro para abrir. También puedes arrastrar un archivo .tvppk a la ventana.')
        recents.pack(fill='both', expand=True, pady=(24, 0))
        self.tree = ttk.Treeview(inner, columns=('name', 'kind', 'folder', 'date'), show='headings', height=8,
                                 selectmode='browse')
        for key, title, width, stretch in [('name', 'Proyecto', 220, False), ('kind', 'Tipo', 170, False),
                                           ('folder', 'Carpeta', 320, True), ('date', 'Modificado', 140, False)]:
            self.tree.heading(key, text=title, anchor='w')
            self.tree.column(key, width=width, minwidth=80, stretch=stretch, anchor='w')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<Double-1>', lambda e: self.open_selected())
        self.tree.bind('<Return>', lambda e: self.open_selected())
        self.empty = ttk.Label(inner, text='Todavía no hay proyectos. Empieza uno de los dos trabajos de arriba.',
                               style='Card.Subtitle.TLabel', padding=(0, 8))
        actions = ttk.Frame(inner, style='Flat.Card.TFrame')
        actions.pack(fill='x', pady=(12, 0))
        open_button = ttk.Button(actions, text='Abrir proyecto…', command=app.ask_open_project)
        app.icons.bind(open_button, 'folder-open')
        open_button.pack(side='left')
        self.open_selected_button = ttk.Button(actions, text='Abrir seleccionado', command=self.open_selected)
        app.icons.bind(self.open_selected_button, 'arrow-right')
        self.open_selected_button.pack(side='left', padx=8)
        self.forget_button = ttk.Button(actions, text='Quitar de la lista', style='Card.Link.TButton',
                                        command=self.forget)
        app.icons.bind(self.forget_button, 'x', 16)
        self.forget_button.pack(side='left')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.update_buttons())

    def on_show(self):
        self.refresh()

    def refresh(self):
        for child in self.notices.winfo_children():
            child.destroy()
        for key, error in self.app.backend.errors.items():
            Banner(self.notices, self.app, 'error', self.app.backend.missing_message(key)).pack(fill='x', pady=(0, 12))
        self.tree.delete(*self.tree.get_children())
        for path in self.app.settings.values.recents:
            kind, folder, date = project_summary(Path(path))
            self.tree.insert('', 'end', iid=path, values=(Path(path).stem, kind, folder, date))
        self.tree.configure(height=max(3, min(8, len(self.app.settings.values.recents))))
        if self.app.settings.values.recents:
            self.empty.pack_forget()
            first = self.tree.get_children()[0]
            if not self.tree.selection():
                self.tree.selection_set(first)
                self.tree.focus(first)
        else:
            self.empty.pack(anchor='w', before=self.tree)
        self.update_buttons()

    def update_buttons(self):
        state = ['!disabled'] if self.tree.selection() else ['disabled']
        self.open_selected_button.state(state)
        self.forget_button.state(state)

    def open_selected(self):
        if self.tree.selection():
            self.app.open_project(Path(self.tree.selection()[0]))

    def forget(self):
        if self.tree.selection():
            self.app.settings.remove_recent(self.tree.selection()[0])
            self.refresh()
