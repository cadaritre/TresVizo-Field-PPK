"""Piezas de interfaz compartidas: desplazamiento, ayudas, tarjetas, zonas de arrastre, pasos, avisos y progreso."""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from app.wizard import Issue, classify

KIND_TEXT = {'recording': 'Grabación del receptor', 'obs': 'RINEX de observación', 'nav': 'RINEX de navegación',
             'precise': 'Efemérides precisas', 'csv': 'CSV de puntos', 'project': 'Proyecto', 'zip': 'Archivo ZIP',
             'unknown': 'No reconocido'}


def size_text(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return ''


class ThinScrollbar(tk.Canvas):
    """Barra de desplazamiento delgada con el pulgar redondeado (dibujada; igual en Windows, macOS y Linux).

    Ocupa siempre su lugar; sin nada que desplazar, no se dibuja el pulgar."""

    def __init__(self, parent, command, width=10):
        super().__init__(parent, width=width, highlightthickness=0, borderwidth=0)
        self.scroll_surface = True
        self.command, self.first, self.last, self.drag = command, 0.0, 1.0, None
        self.hover = False
        self.bind('<Configure>', lambda e: self.redraw())
        self.bind('<ButtonPress-1>', self.press)
        self.bind('<B1-Motion>', self.move)
        self.bind('<ButtonRelease-1>', lambda e: setattr(self, 'drag', None))
        self.bind('<Enter>', lambda e: (setattr(self, 'hover', True), self.redraw()))
        self.bind('<Leave>', lambda e: (setattr(self, 'hover', False), self.redraw()))

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self.redraw()

    def redraw(self):
        tokens = getattr(self.winfo_toplevel(), 'theme', None)
        tokens = tokens.tokens if tokens else None
        self.delete('all')
        if not tokens:
            return
        from app.themes import mix
        self.configure(background=tokens['background'])
        if self.first <= 0 and self.last >= 1:
            return
        height, width = self.winfo_height(), self.winfo_width()
        y0, y1 = self.first*height, max(self.first*height+24, self.last*height)
        color = mix(tokens['background'], tokens['muted'], .75 if self.hover or self.drag else .45)
        r = (width-4)/2
        x0, x1 = 2, width-2
        self.create_oval(x0, y0+1, x1, y0+1+2*r, fill=color, outline='')
        self.create_oval(x0, y1-1-2*r, x1, y1-1, fill=color, outline='')
        self.create_rectangle(x0, y0+1+r, x1, y1-1-r, fill=color, outline='')

    def press(self, event):
        height = max(1, self.winfo_height())
        position = event.y/height
        if self.first <= position <= self.last:
            self.drag = (event.y, self.first)
        else:
            self.command('scroll', -1 if position < self.first else 1, 'pages')

    def move(self, event):
        if self.drag:
            start_y, start_first = self.drag
            self.command('moveto', start_first+(event.y-start_y)/max(1, self.winfo_height()))


class ScrollFrame(ttk.Frame):
    """Contenido desplazable con la rueda en Windows, macOS y Linux; la barra solo aparece cuando hace falta."""

    def __init__(self, parent, padding=(0, 0)):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.canvas.scroll_surface = True
        self.bar = ThinScrollbar(self, self.canvas.yview)
        self.body = ttk.Frame(self.canvas, padding=padding)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor='nw')

        # La barra ocupa siempre su lugar: ocultarla cuando sobra cambia el ancho del contenido, que vuelve a
        # acomodar el texto y puede hacer que la barra aparezca y desaparezca sin fin.
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side='right', fill='y', padx=(4, 0))
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=(
            0, 0, e.width, max(e.height, self.canvas.winfo_height()))))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        top = self.winfo_toplevel()
        if getattr(top, 'theme', None) is not None:
            top.theme.subscribe(self.bar, lambda tokens: self.bar.redraw())
        bindings = [('<MouseWheel>', top.bind('<MouseWheel>', self.wheel, add='+'))]
        if sys.platform.startswith('linux'):
            bindings += [(seq, top.bind(seq, self.wheel, add='+')) for seq in ('<Button-4>', '<Button-5>')]

        def cleanup(event):
            if event.widget is self:
                for seq, binding in bindings:
                    try:
                        top.unbind(seq, binding)
                    except tk.TclError:
                        pass
        self.bind('<Destroy>', cleanup, add='+')

    def wheel(self, event):
        if not self.winfo_exists() or not self.winfo_ismapped():
            return
        target = self.winfo_containing(event.x_root, event.y_root)
        if not target or not str(target).startswith(str(self)):
            return
        if isinstance(target, (tk.Text, ttk.Treeview, tk.Listbox)):
            return
        if getattr(event, 'num', None) in (4, 5):
            step = -1 if event.num == 4 else 1
        elif sys.platform == 'darwin':
            step = -event.delta
        else:
            step = -int(event.delta/120) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(step, 'units')

    def to_top(self):
        self.canvas.yview_moveto(0)


class Tooltip:
    """Ayuda al pasar el cursor o recibir el foco con Tab; nunca toma el foco."""

    def __init__(self, widget, text, theme):
        self.widget, self.text, self.theme = widget, text, theme
        self.window = None
        self.pending = None
        for seq in ('<Enter>', '<FocusIn>'):
            widget.bind(seq, self.schedule, add='+')
        for seq in ('<Leave>', '<FocusOut>', '<ButtonPress>', '<Unmap>', '<Escape>', '<Destroy>'):
            widget.bind(seq, self.hide, add='+')

    def schedule(self, event=None):
        self.hide()
        self.pending = self.widget.after(450, self.show)

    def show(self):
        self.hide()
        if not self.widget.winfo_exists() or not self.widget.winfo_viewable() or not self.text:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.withdraw()
        self.window.overrideredirect(True)
        ttk.Label(self.window, text=self.text, style='Card.TLabel', padding=8, wraplength=320).pack()
        self.theme.walk(self.window)
        self.window.update_idletasks()
        x = min(self.widget.winfo_rootx(), self.widget.winfo_screenwidth()-self.window.winfo_reqwidth()-8)
        y = self.widget.winfo_rooty()+self.widget.winfo_height()+6
        if y+self.window.winfo_reqheight() > self.widget.winfo_screenheight():
            y = self.widget.winfo_rooty()-self.window.winfo_reqheight()-6
        self.window.geometry(f'+{max(0, x)}+{max(0, y)}')
        self.window.deiconify()

    def hide(self, event=None):
        if self.pending is not None:
            try:
                self.widget.after_cancel(self.pending)
            except tk.TclError:
                pass
            self.pending = None
        if self.window:
            self.window.destroy()
            self.window = None


def card(parent, title=None, subtitle=None, padding=18):
    """Tarjeta blanca con título opcional; devuelve (marco, cuerpo)."""
    frame = ttk.Frame(parent, style='Card.TFrame', padding=padding)
    if title:
        ttk.Label(frame, text=title, style='Card.Section.TLabel').pack(anchor='w')
    if subtitle:
        label = ttk.Label(frame, text=subtitle, style='Card.Subtitle.TLabel', wraplength=640, justify='left')
        label.pack(anchor='w', fill='x', pady=(2, 0))
        frame.bind('<Configure>', lambda e: label.configure(wraplength=max(200, e.width-40)), add='+')
    body = ttk.Frame(frame, style='Flat.Card.TFrame')
    body.pack(fill='both', expand=True, pady=(10 if title or subtitle else 0, 0))
    return frame, body


def wrap_label(parent, text='', style='Subtitle.TLabel', margin=20, **kwargs):
    """Etiqueta que ajusta su ancho de línea al de su contenedor."""
    label = ttk.Label(parent, text=text, style=style, justify='left', **kwargs)
    parent.bind('<Configure>', lambda e: label.winfo_exists() and label.configure(wraplength=max(200, e.width-margin)),
                add='+')
    return label


def split_drop(widget, data) -> list[Path]:
    return [Path(p) for p in widget.tk.splitlist(data)]


class DropZone(ttk.Frame):
    """Zona para arrastrar archivos (tkinterdnd2) con el botón equivalente para quien usa el teclado."""

    def __init__(self, parent, app, title, hint, on_files, filetypes=None, multiple=True, button='Agregar archivos'):
        super().__init__(parent, style='Drop.TFrame', padding=16)
        self.app, self.on_files, self.filetypes, self.multiple = app, on_files, filetypes, multiple
        self.columnconfigure(1, weight=1)
        self.icon = app.icons.label(self, 'file-up', 30, token='primary', background='surface_secondary',
                                    style='Drop.TLabel')
        self.icon.grid(row=0, column=0, rowspan=2, padx=(0, 14))
        self.title = ttk.Label(self, text=title, style='Drop.TLabel', font=app.theme.fonts.strong())
        self.title.grid(row=0, column=1, sticky='w')
        self.hint = ttk.Label(self, text=hint, style='DropMuted.TLabel', justify='left')
        self.hint.grid(row=1, column=1, sticky='w')
        self.bind('<Configure>', lambda e: self.hint.configure(wraplength=max(200, e.width-260)), add='+')
        self.button = ttk.Button(self, text=button, command=self.choose)
        app.icons.bind(self.button, 'folder-open')
        self.button.grid(row=0, column=2, rowspan=2, padx=(12, 0))
        if hasattr(self, 'drop_target_register'):
            for widget in (self, self.icon, self.title, self.hint):
                widget.drop_target_register('DND_Files')
                widget.dnd_bind('<<DropEnter>>', self.enter)
                widget.dnd_bind('<<DropLeave>>', self.leave)
                widget.dnd_bind('<<Drop>>', self.drop)

    def enter(self, event):
        self.configure(style='DropActive.TFrame')
        return event.action

    def leave(self, event):
        self.configure(style='Drop.TFrame')
        return event.action

    def drop(self, event):
        self.configure(style='Drop.TFrame')
        files = split_drop(self, event.data)
        if not self.multiple:
            files = files[:1]
        self.on_files(files)
        return event.action

    def choose(self):
        options = dict(parent=self.winfo_toplevel(), initialdir=self.app.settings.values.last_folder or None)
        if self.filetypes:
            options['filetypes'] = self.filetypes
        if self.multiple:
            chosen = filedialog.askopenfilenames(**options)
        else:
            one = filedialog.askopenfilename(**options)
            chosen = [one] if one else []
        if chosen:
            paths = [Path(p) for p in chosen]
            try:
                self.app.settings.save(last_folder=str(paths[0].parent))
            except Exception:  # noqa: BLE001 - recordar la carpeta es una comodidad, nunca bloquea
                pass
            self.on_files(paths)


class FileList(ttk.Frame):
    """Archivos elegidos con su tipo y tamaño; Suprimir o «Quitar» los saca de la lista."""

    def __init__(self, parent, app, on_remove, height=4):
        super().__init__(parent, style='Flat.Card.TFrame')
        self.app, self.on_remove = app, on_remove
        self.tree = ttk.Treeview(self, columns=('name', 'kind', 'size'), show='headings', height=height,
                                 selectmode='extended')
        for key, title, width, stretch in [('name', 'Archivo', 300, True), ('kind', 'Tipo', 210, False),
                                           ('size', 'Tamaño', 90, False)]:
            self.tree.heading(key, text=title, anchor='w')
            self.tree.column(key, width=width, minwidth=60, stretch=stretch, anchor='w')
        self.tree.pack(side='left', fill='both', expand=True)
        self.remove_button = ttk.Button(self, text='Quitar', style='Card.Link.TButton', command=self.remove)
        app.icons.bind(self.remove_button, 'trash-2', 16)
        self.remove_button.pack(side='left', anchor='n', padx=(8, 0))
        self.tree.bind('<Delete>', lambda e: self.remove())
        self.tree.bind('<BackSpace>', lambda e: self.remove())
        self.paths: list[Path] = []

    def set(self, paths):
        self.paths = list(paths)
        self.tree.delete(*self.tree.get_children())
        for index, path in enumerate(self.paths):
            path = Path(path)
            size = size_text(path.stat().st_size) if path.is_file() else 'no está'
            self.tree.insert('', 'end', iid=str(index), values=(path.name, KIND_TEXT[classify(path)], size))
        self.tree.configure(height=max(1, min(6, len(self.paths))))
        self.remove_button.state(['!disabled'] if self.paths else ['disabled'])

    def remove(self):
        chosen = {int(i) for i in self.tree.selection()}
        if not chosen and len(self.paths) == 1:
            chosen = {0}
        if chosen:
            self.on_remove([p for i, p in enumerate(self.paths) if i not in chosen])


class IssueList(ttk.Frame):
    """Lo que falta (error) o conviene revisar (aviso), con icono y texto: nunca solo con color."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

    def show(self, issues: list[Issue]):
        for child in self.winfo_children():
            child.destroy()
        for issue in issues:
            kind = 'error' if issue.level == 'error' else 'warning'
            row = ttk.Frame(self, style=kind+'.TFrame', padding=(10, 6))
            row.pack(fill='x', pady=2)
            self.app.icons.label(row, 'circle-alert' if kind == 'error' else 'triangle-alert', 16,
                                 token=kind+'_fg', background=kind+'_bg', style=kind+'.TLabel',
                                 padding=0).pack(side='left', padx=(0, 8))
            label = ttk.Label(row, text=issue.message, style=kind+'.TLabel', padding=0, justify='left')
            label.pack(side='left', fill='x', expand=True)
            row.bind('<Configure>', lambda e, l=label: l.configure(wraplength=max(200, e.width-60)), add='+')


class Banner(ttk.Frame):
    ICONS = {'error': 'circle-alert', 'warning': 'triangle-alert', 'success': 'circle-check', 'info': 'info'}

    def __init__(self, parent, app, kind, text):
        super().__init__(parent, style=kind+'.TFrame', padding=(12, 10))
        app.icons.label(self, self.ICONS[kind], 18, token=kind+'_fg', background=kind+'_bg', style=kind+'.TLabel',
                        padding=0).pack(side='left', anchor='n', padx=(0, 10))
        self.label = ttk.Label(self, text=text, style=kind+'.TLabel', padding=0, justify='left')
        self.label.pack(side='left', fill='x', expand=True)
        self.bind('<Configure>', lambda e: self.label.configure(wraplength=max(200, e.width-60)), add='+')


class StepHeader(ttk.Frame):
    """Pasos del asistente: hecho, actual y pendientes. Se puede volver a un paso ya visitado."""

    def __init__(self, parent, app, steps, on_select):
        super().__init__(parent)
        self.app, self.steps, self.on_select = app, steps, on_select
        self.buttons = []
        for index, name in enumerate(steps):
            if index:
                ttk.Label(self, text='›', style='Step.TLabel').pack(side='left', padx=6)
            label = ttk.Label(self, text=f'{index+1}  {name}', style='Step.TLabel', cursor='hand2', takefocus=0)
            label.pack(side='left')
            label.bind('<Button-1>', lambda e, i=index: self.on_select(i))
            self.buttons.append(label)

    def update_state(self, current, reachable):
        for index, label in enumerate(self.buttons):
            done = index < current
            style = 'ActiveStep.TLabel' if index == current else 'DoneStep.TLabel' if done else 'Step.TLabel'
            prefix = '✓' if done else str(index+1)
            label.configure(style=style, text=f'{prefix}  {self.steps[index]}',
                            cursor='hand2' if index <= reachable and index != current else '')


class ProgressPanel(ttk.Frame):
    """Barra, mensaje, tiempo transcurrido y Cancelar (el trabajo corre en otro hilo)."""

    def __init__(self, parent, app, on_cancel):
        super().__init__(parent, style='Flat.Card.TFrame')
        self.app = app
        self.bar = ttk.Progressbar(self, maximum=1.0, mode='determinate')
        self.bar.pack(fill='x', pady=(0, 8))
        row = ttk.Frame(self, style='Flat.Card.TFrame')
        row.pack(fill='x')
        self.message = ttk.Label(row, text='', style='Card.TLabel', justify='left')
        self.message.pack(side='left', fill='x', expand=True)
        self.elapsed = ttk.Label(row, text='', style='Card.Subtitle.TLabel')
        self.elapsed.pack(side='left', padx=12)
        self.cancel_button = ttk.Button(row, text='Cancelar', command=on_cancel)
        app.icons.bind(self.cancel_button, 'circle-stop')
        self.cancel_button.pack(side='left')
        self.started = None
        self.bind('<Configure>', lambda e: self.message.configure(wraplength=max(200, e.width-260)), add='+')

    def start(self, message='Preparando…'):
        self.started = time.monotonic()
        self.bar.configure(value=0)
        self.message.configure(text=message)
        self.cancel_button.state(['!disabled'])
        self._tick()

    def _tick(self):
        if self.started is None or not self.winfo_exists():
            return
        seconds = int(time.monotonic()-self.started)
        self.elapsed.configure(text=f'{seconds//60:d}:{seconds % 60:02d}')
        self.after(500, self._tick)

    def update_progress(self, fraction, message):
        self.bar.configure(value=fraction)
        if message:
            self.message.configure(text=message)

    def stop(self, message=''):
        self.started = None
        self.cancel_button.state(['disabled'])
        if message:
            self.message.configure(text=message)

    def cancelling(self):
        self.cancel_button.state(['disabled'])
        self.message.configure(text='Cancelando… se detiene en cuanto el paso actual lo permite.')


class LabeledEntry(ttk.Frame):
    """Etiqueta arriba, campo y ayuda debajo (como los formularios de TresVizo Med)."""

    def __init__(self, parent, label, variable, hint='', width=24, style_prefix='Card.'):
        super().__init__(parent, style='Flat.Card.TFrame' if style_prefix else 'TFrame')
        ttk.Label(self, text=label, style=style_prefix+'Strong.TLabel' if style_prefix else 'TLabel').pack(
            anchor='w', pady=(0, 4))
        self.entry = ttk.Entry(self, textvariable=variable, width=width)
        self.entry.pack(fill='x')
        if hint:
            ttk.Label(self, text=hint, style=style_prefix+'Subtitle.TLabel' if style_prefix else 'Subtitle.TLabel',
                      justify='left').pack(anchor='w', pady=(4, 0))


class Collapsible(ttk.Frame):
    """Sección desplegable con transición de 150 ms que respeta «Reducir movimiento»."""

    def __init__(self, parent, app, title, opened=False, style_prefix='Card.'):
        super().__init__(parent, style='Flat.Card.TFrame' if style_prefix else 'TFrame')
        self.app, self.title, self.opened, self.animation = app, title, opened, None
        self.button = ttk.Button(self, text=title, style=style_prefix+'Link.TButton' if style_prefix else
                                 'Link.TButton', command=self.toggle)
        self.button.pack(anchor='w')
        self.body = ttk.Frame(self, style='Flat.Card.TFrame' if style_prefix else 'TFrame', padding=(8, 6, 0, 0))
        if opened:
            self.body.pack(fill='x')
        self._icon()

    def _icon(self):
        self.button.configure(text=('▾  ' if self.opened else '▸  ')+self.title)

    def toggle(self):
        if self.animation:
            self.after_cancel(self.animation)
            self.animation = None
        self.opened = not self.opened
        self._icon()
        if self.app.theme.reduce_motion:
            self.body.pack_propagate(True)
            self.body.pack(fill='x') if self.opened else self.body.pack_forget()
            return
        self.body.pack_propagate(True)
        self.body.pack(fill='x')
        self.body.update_idletasks()
        height = max(1, self.body.winfo_reqheight())
        self.body.pack_propagate(False)

        def step(index=0):
            portion = index/10
            self.body.configure(height=max(1, int(height*(portion if self.opened else 1-portion))))
            if index < 10:
                self.animation = self.after(15, lambda: step(index+1))
            else:
                self.animation = None
                self.body.pack_propagate(True)
                if not self.opened:
                    self.body.pack_forget()
        step()
