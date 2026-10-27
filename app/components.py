"""Piezas de interfaz compartidas: desplazamiento, ayudas, tarjetas, zonas de arrastre, pasos, avisos y progreso."""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from app.wizard import Issue, classify

def later(widget, ms, callback):
    """`after` seguro: se programa en la ventana principal y no llama si `widget` ya no existe.

    tkinter borra el comando de un `after` pendiente al destruir el widget que lo programó, pero Tcl lo deja en su
    lista; un comando nuevo puede heredar ese nombre (sale del `id` del objeto) y recibir la llamada con otros
    argumentos («missing 1 required positional argument»). La ventana principal no se destruye mientras haya
    interfaz, así que su `after` siempre llega a la función correcta. `ms='idle'` equivale a `after_idle`."""
    def run():
        try:
            alive = bool(widget.winfo_exists())
        except tk.TclError:
            alive = False
        if alive:
            callback()
    root = widget._root()
    return root.after_idle(run) if ms == 'idle' else root.after(ms, run)


def cancel_later(widget, pending):
    if pending is None:
        return
    try:
        widget._root().after_cancel(pending)
    except (tk.TclError, ValueError):
        pass


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

    Ocupa siempre su lugar; sin nada que desplazar, no se dibuja el pulgar. `command` es el `yview`/`xview` del
    widget que desplaza, y su `set` va en el `yscrollcommand`/`xscrollcommand` de ese widget."""

    def __init__(self, parent, command, orient='vertical', thickness=10, surface=None):
        self.surface_key = surface or ('surface' if str(getattr(parent, 'cget', lambda k: '')('style')).startswith(
            ('Card', 'Flat.Card')) else 'background')
        size = {'width': thickness} if orient == 'vertical' else {'height': thickness}
        super().__init__(parent, highlightthickness=0, borderwidth=0, **size)
        self.own_palette = True
        self.vertical = orient == 'vertical'
        self.command, self.first, self.last, self.drag, self.hover = command, 0.0, 1.0, None, False
        self.bind('<Configure>', lambda e: self.redraw())
        self.bind('<ButtonPress-1>', self.press)
        self.bind('<B1-Motion>', self.move)
        self.bind('<ButtonRelease-1>', self.release)
        self.bind('<Enter>', lambda e: self._hover(True))
        self.bind('<Leave>', lambda e: self._hover(False))
        theme = getattr(self.winfo_toplevel(), 'theme', None) or getattr(parent.winfo_toplevel(), 'theme', None)
        if theme is not None:
            theme.subscribe(self, lambda tokens: self.redraw())

    def _hover(self, value):
        self.hover = value
        self.redraw()

    def release(self, event):
        self.drag = None
        self.redraw()

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self.redraw()

    def length(self):
        return max(1, self.winfo_height() if self.vertical else self.winfo_width())

    def redraw(self):
        if not self.winfo_exists():
            return
        app = self.winfo_toplevel()
        theme = getattr(app, 'theme', None) or getattr(getattr(app, 'master', None), 'theme', None)
        tokens = theme.tokens if theme else None
        self.delete('all')
        if not tokens:
            return
        from app.themes import mix
        back = tokens[self.surface_key]
        self.configure(background=back)
        if self.first <= 0 and self.last >= 1:
            return
        n = self.length()
        a, b = self.first*n, max(self.first*n+24, self.last*n)
        cross = self.winfo_width() if self.vertical else self.winfo_height()
        color = mix(back, tokens['muted'], .75 if self.hover or self.drag else .45)
        r = (cross-4)/2
        if self.vertical:
            self.create_oval(2, a+1, cross-2, a+1+2*r, fill=color, outline='')
            self.create_oval(2, b-1-2*r, cross-2, b-1, fill=color, outline='')
            self.create_rectangle(2, a+1+r, cross-2, b-1-r, fill=color, outline='')
        else:
            self.create_oval(a+1, 2, a+1+2*r, cross-2, fill=color, outline='')
            self.create_oval(b-1-2*r, 2, b-1, cross-2, fill=color, outline='')
            self.create_rectangle(a+1+r, 2, b-1-r, cross-2, fill=color, outline='')

    def press(self, event):
        position = (event.y if self.vertical else event.x)/self.length()
        if self.first <= position <= self.last:
            self.drag = (event.y if self.vertical else event.x, self.first)
        else:
            self.command('scroll', -1 if position < self.first else 1, 'pages')

    def move(self, event):
        if self.drag:
            start, first = self.drag
            self.command('moveto', first+((event.y if self.vertical else event.x)-start)/self.length())


def scrolled(widget, parent, horizontal=False):
    """Barras delgadas para un Treeview, Text o Canvas ya creado dentro de `parent` (que se acomoda con grid)."""
    bar = ThinScrollbar(parent, widget.yview)
    widget.configure(yscrollcommand=bar.set)
    widget.grid(row=0, column=0, sticky='nsew')
    bar.grid(row=0, column=1, sticky='ns', padx=(3, 0))
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)
    if horizontal:
        hbar = ThinScrollbar(parent, widget.xview, orient='horizontal')
        widget.configure(xscrollcommand=hbar.set)
        hbar.grid(row=1, column=0, sticky='ew', pady=(3, 0))
    return widget


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
        self.pending = later(self.widget, 450, self.show)

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
            cancel_later(self.widget, self.pending)
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
    seen: list[int] = []

    def fit(event):
        if not label.winfo_exists():
            return
        # Si el ancho del contenedor depende del de la etiqueta (rejillas «uniform», letra muy grande), envolver al
        # ancho nuevo puede devolver el ancho anterior y así sin fin (la ventana se congela). En un rebote A→B→A se
        # queda el menor de los dos, que cabe en ambos.
        width = event.width
        if len(seen) >= 2 and width == seen[-2] and width != seen[-1]:
            width = min(width, seen[-1])
        seen[:] = (seen+[event.width])[-2:]
        target = max(200, width-margin)
        if str(label.cget('wraplength')) != str(target):
            label.configure(wraplength=target)
    parent.bind('<Configure>', fit, add='+')
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
        for key, title, width, stretch in [('name', 'Archivo', 300, True), ('kind', 'Tipo', 190, True),
                                           ('size', 'Tamaño', 80, False)]:
            self.tree.heading(key, text=title, anchor='w')
            self.tree.column(key, width=width, minwidth=90 if key == 'size' else 70, stretch=stretch, anchor='w')
        self.tree.pack(fill='both', expand=True)
        self.remove_button = ttk.Button(self, text='Quitar seleccionados', style='Card.Link.TButton',
                                        command=self.remove)
        app.icons.bind(self.remove_button, 'trash-2', 16)
        self.remove_button.pack(anchor='e', pady=(2, 0))
        CellTips(self.tree, app)
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
        fit_columns(self.tree, self.app, stretch='name')
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
        later(self, 500, self._tick)

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
        head = ttk.Frame(self, style='Flat.Card.TFrame' if style_prefix else 'TFrame')
        head.pack(fill='x', pady=(0, 4))
        self.title = ttk.Label(head, text=label, style=style_prefix+'Strong.TLabel' if style_prefix else 'TLabel')
        self.title.pack(side='left')
        # Origen del valor («del RINEX», «del catálogo…», «escrito por ti»): discreto, a la derecha del título.
        self.origin = ttk.Label(head, text='', style=style_prefix+'Subtitle.TLabel' if style_prefix else
                                'Subtitle.TLabel')
        self.origin.configure(justify='left')
        self.origin.pack(side='left', padx=(8, 0), fill='x', expand=True)
        self.entry = ttk.Entry(self, textvariable=variable, width=width)
        self.entry.pack(fill='x')
        self.hint = ttk.Label(self, text=hint, style=style_prefix+'Subtitle.TLabel' if style_prefix else
                              'Subtitle.TLabel', justify='left')
        if hint:
            self.hint.pack(anchor='w', pady=(4, 0))

        def fit(event):
            # El origen largo («del catálogo RGNA — INEGI, México») se envuelve junto al título en vez de cortarse.
            self.hint.configure(wraplength=max(140, event.width-8))
            self.origin.configure(wraplength=max(100, event.width-self.title.winfo_reqwidth()-16))
        self.bind('<Configure>', fit, add='+')

    def set_origin(self, text):
        self.origin.configure(text=f'· {text}' if text else '')


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
            cancel_later(self, self.animation)
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
                self.animation = later(self, 15, lambda: step(index+1))
            else:
                self.animation = None
                self.body.pack_propagate(True)
                if not self.opened:
                    self.body.pack_forget()
        step()


class FlowFrame(ttk.Frame):
    """Acomoda sus hijos en filas que se envuelven según el ancho disponible (chips, cápsulas, acciones).

    Los hijos se agregan con `add(widget)`; nunca se recortan al angostar la ventana."""

    def __init__(self, parent, gap=8, row_gap=6, **kwargs):
        super().__init__(parent, **kwargs)
        self.gap, self.row_gap, self.items, self._width = gap, row_gap, [], None
        self.bind('<Configure>', self._layout)

    def add(self, widget, stretch=False):
        widget.flow_stretch = stretch
        self.items.append(widget)
        later(self, 'idle', self._layout)
        return widget

    def clear(self):
        for widget in self.items:
            widget.destroy()
        self.items = []

    def _layout(self, event=None):
        if not self.winfo_exists():
            return
        width = max(1, self.winfo_width())
        if width <= 1:
            width = max(1, self.winfo_reqwidth())
        row, x = 0, 0
        positions = []
        for widget in self.items:
            if not widget.winfo_exists():
                continue
            w = widget.winfo_reqwidth()
            if x and x+w > width:
                row, x = row+1, 0
            positions.append((widget, row, x))
            x += w+self.gap
        signature = tuple((str(w), r) for w, r, _ in positions)
        if signature == self._width:
            return
        self._width = signature
        for widget in self.items:
            if widget.winfo_exists():
                widget.grid_forget()
        columns: dict[int, int] = {}
        for widget, r, _ in positions:
            c = columns.get(r, 0)
            widget.grid(row=r, column=c, sticky='w', padx=(0, self.gap), pady=(0, self.row_gap))
            columns[r] = c+1


def text_width(font, text: str) -> int:
    """Ancho de un texto de una o varias líneas (el de su línea más larga)."""
    return max((font.measure(line) for line in str(text).split('\n')), default=0)


def fit_columns(tree, app, stretch=None, limit=400, maximum=520):
    """Ancho de cada columna según su contenido (encabezado y filas), sin cortar; `stretch` absorbe el sobrante.

    Lo que aun así no cabe se ve completo en la ayuda emergente de la celda (`CellTips`)."""
    from tkinter import font as tkfont
    body = tkfont.Font(root=tree, font=app.theme.fonts.body())
    head = tkfont.Font(root=tree, font=app.theme.fonts.strong(-1))
    items, stack = [], list(tree.get_children(''))
    while stack and len(items) < limit:
        item = stack.pop(0)
        items.append(item)
        if tree.item(item, 'open'):
            stack[0:0] = list(tree.get_children(item))
    columns = list(tree['columns'])
    show = str(tree.cget('show'))
    if 'tree' in show:
        columns = ['#0']+columns
    for column in columns:
        texts = [str(tree.heading(column, 'text'))]
        if column == '#0':
            texts += [str(tree.item(i, 'text')) for i in items]
            extra = 46     # sangría de los grupos y el indicador
        else:
            texts += [str(tree.set(i, column)) for i in items]
            extra = 24
        width = max([head.measure(texts[0])]+[text_width(body, t) for t in texts[1:]])+extra
        width = min(maximum, max(56, width))
        tree.column(column, width=width, minwidth=min(width, 90), stretch=column == stretch)
    tree.fit_natural = {c: tree.column(c, 'width') for c in columns}
    tree.fit_stretch = stretch
    if not getattr(tree, 'fit_bound', False):
        # Las demás columnas conservan su ancho natural y la elástica toma lo que queda. Si no cabe todo y la tabla no
        # tiene barra horizontal, la elástica baja hasta 120 px y las demás se reparten el resto en proporción: ninguna
        # columna queda fuera de la vista (lo cortado se lee en la ayuda de la celda).
        def fit(event=None):
            natural = getattr(tree, 'fit_natural', {})
            column = getattr(tree, 'fit_stretch', None)
            if not natural or column not in natural:
                return
            total = tree.winfo_width()-4
            if total <= 1:
                return
            others = {c: w for c, w in natural.items() if c != column}
            room = total-sum(others.values())
            if room >= natural[column]:
                widths = {**others, column: room}
            elif str(tree.cget('xscrollcommand')):
                # Con barra horizontal nada se corta: cada columna con su ancho natural y se desplaza.
                widths = dict(natural)
            else:
                stretch = max(min(natural[column], 120), room)
                factor = min(1.0, (total-stretch)/max(1, sum(others.values())))
                widths = {c: max(48, int(w*factor)) for c, w in others.items()}
                widths[column] = max(stretch, total-sum(widths.values()))
            for c, w in widths.items():
                tree.column(c, width=w, minwidth=min(w, 40))
        tree.bind('<Configure>', fit, add='+')
        tree.fit_bound = True
    later(tree, 'idle', lambda: tree.event_generate('<Configure>'))


class CellTips:
    """Ayuda emergente con el texto completo de la celda bajo el cursor cuando la columna lo corta."""

    def __init__(self, tree, app):
        from tkinter import font as tkfont
        self.tree, self.app, self.tip, self.key = tree, app, None, None
        self.font = tkfont.Font(root=tree, font=app.theme.fonts.body())
        tree.bind('<Motion>', self.motion, add='+')
        tree.bind('<Leave>', lambda e: self.hide(), add='+')
        tree.bind('<ButtonPress>', lambda e: self.hide(), add='+')

    def motion(self, event):
        row, column = self.tree.identify_row(event.y), self.tree.identify_column(event.x)
        if not row or not column:
            self.hide()
            return
        if (row, column) == self.key:
            return
        self.hide()
        if column == '#0':
            text = str(self.tree.item(row, 'text'))
            width = self.tree.column('#0', 'width')-40
        else:
            name = self.tree['columns'][int(column[1:])-1]
            text = str(self.tree.set(row, name))
            width = self.tree.column(name, 'width')-16
        if not text or text_width(self.font, text) <= width:
            return
        self.key = (row, column)
        self.tip = tk.Toplevel(self.tree)
        self.tip.overrideredirect(True)
        ttk.Label(self.tip, text=text, style='Card.TLabel', padding=6, wraplength=420).pack()
        self.app.theme.walk(self.tip)
        self.tip.geometry(f'+{event.x_root+14}+{event.y_root+16}')

    def hide(self):
        self.key = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
