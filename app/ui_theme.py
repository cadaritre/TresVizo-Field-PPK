"""Estilos ttk a partir de los tokens; se cambian en el mismo árbol de widgets, sin reconstruir pantallas."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

from app.themes import derived, mix, readable


def font_families(root) -> tuple[str, str]:
    """Letra del sistema: Segoe UI en Windows (como TresVizo Med), la del sistema en macOS y Linux."""
    if sys.platform == 'win32':
        return 'Segoe UI', 'Segoe UI Semibold'
    family = tkfont.nametofont('TkDefaultFont', root=root).actual('family')
    return family, family


class Fonts:
    def __init__(self, root, scale=1.0):
        self.regular, self.semibold = font_families(root)
        # Tk mide en puntos; macOS dibuja a 72 ppp y Windows a 96: misma altura visual con tamaños distintos.
        self.normal = 13 if sys.platform == 'darwin' else 10
        self.base = round(self.normal*scale)

    def set_scale(self, scale):
        self.base = round(self.normal*scale)
        self.bold_weight = 'normal' if sys.platform == 'win32' else 'bold'

    def body(self, delta=0):
        return (self.regular, self.base+delta)

    def strong(self, delta=0):
        return (self.semibold, self.base+delta, self.bold_weight)

    def mono(self, delta=0):
        family = 'Consolas' if sys.platform == 'win32' else 'Menlo' if sys.platform == 'darwin' else 'DejaVu Sans Mono'
        return (family, self.base+delta-1)


class ThemeManager:
    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(root)
        self.style.theme_use('clam')
        self.fonts = Fonts(root)
        self.tokens = None
        self.callbacks = []
        self.reduce_motion = False

    def subscribe(self, widget, callback):
        self.callbacks.append((widget, callback))
        if self.tokens:
            callback(self.tokens)

    def apply(self, palette):
        t = self.tokens = derived(palette)
        s, f = self.style, self.fonts
        scale = getattr(self.root, 'ui_scale', 1)
        for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
            try:
                tkfont.nametofont(name, root=self.root).configure(family=f.regular, size=f.base)
            except tk.TclError:
                pass
        s.configure('.', font=f.body(), foreground=t['text'], background=t['background'],
                    bordercolor=t['border'], lightcolor=t['border'], darkcolor=t['border'], troughcolor=t['secondary'],
                    focuscolor=t['focus'], selectbackground=t['selection'], selectforeground=t['on_selection'])
        for prefix, bg, fg in [('', 'background', 'text'), ('Card.', 'surface', 'text'),
                               ('Header.', 'header', 'on_header'), ('Nav.', 'sidebar', 'on_sidebar')]:
            s.configure(prefix+'TFrame', background=t[bg])
            s.configure(prefix+'TLabel', background=t[bg], foreground=t[fg])
        s.configure('Card.TFrame', background=t['surface'], bordercolor=t['separator'], relief='solid', borderwidth=1)
        s.configure('Flat.Card.TFrame', background=t['surface'], borderwidth=0)
        s.configure('Title.TLabel', font=f.strong(10), foreground=t['text'])
        s.configure('Section.TLabel', font=f.strong(3), foreground=t['text'])
        s.configure('Card.Section.TLabel', font=f.strong(2), background=t['surface'], foreground=t['text'])
        s.configure('Subtitle.TLabel', foreground=readable(t['muted'], t['background']))
        s.configure('Card.Subtitle.TLabel', background=t['surface'], foreground=t['muted_surface'])
        s.configure('Card.Strong.TLabel', background=t['surface'], foreground=t['text'], font=f.strong())
        s.configure('Metric.TLabel', font=f.strong(9), background=t['surface'], foreground=t['text'])
        s.configure('Mono.TLabel', font=f.mono(), background=t['surface'], foreground=t['text'])
        s.configure('Brand.TLabel', font=f.strong(2), background=t['sidebar'], foreground=t['on_sidebar'])
        s.configure('BrandSub.TLabel', font=f.body(-2), background=t['sidebar'],
                    foreground=readable(t['muted'], t['sidebar']))
        s.configure('Step.TLabel', font=f.body(-1), background=t['background'],
                    foreground=readable(t['muted'], t['background']))
        s.configure('ActiveStep.TLabel', font=f.strong(-1), background=t['background'],
                    foreground=readable(t['primary'], t['background']))
        s.configure('DoneStep.TLabel', font=f.body(-1), background=t['background'], foreground=t['text'])
        s.configure('Drop.TFrame', background=t['surface_secondary'], bordercolor=t['border'], relief='solid',
                    borderwidth=1)
        s.configure('DropActive.TFrame', background=t['selection'], bordercolor=t['focus'], relief='solid',
                    borderwidth=2)
        s.configure('Drop.TLabel', background=t['surface_secondary'], foreground=t['text'])
        s.configure('DropMuted.TLabel', background=t['surface_secondary'],
                    foreground=readable(t['muted'], t['surface_secondary']))
        s.configure('TSeparator', background=t['separator'])
        s.configure('TLabelframe', background=t['background'], bordercolor=t['border'], padding=12)
        s.configure('TLabelframe.Label', foreground=t['text'], background=t['background'])
        for style, key in [('TButton', 'secondary'), ('Primary.TButton', 'button'), ('Nav.TButton', 'sidebar')]:
            s.configure(style, background=t[key], foreground=t['on_'+key], padding=(12, 7), borderwidth=0,
                        focuscolor=t['focus'], focusthickness=2, anchor='center', font=f.body())
            s.map(style, background=[('disabled', t[key+'_disabled']), ('pressed', t[key+'_pressed']),
                                     ('active', t[key+'_hover'])],
                  foreground=[('disabled', t['on_'+key+'_disabled']), ('pressed', t['on_'+key+'_pressed']),
                              ('active', t['on_'+key+'_hover'])],
                  bordercolor=[('focus', t['focus'])])
        s.configure('Nav.TButton', anchor='w', padding=(12, 8))
        s.configure('Active.Nav.TButton', background=t['selection'], foreground=t['on_selection'], font=f.strong(),
                    anchor='w', padding=(12, 8), borderwidth=0, focuscolor=t['focus'], focusthickness=2)
        s.map('Active.Nav.TButton', background=[('pressed', t['selection_pressed']), ('active', t['selection_hover'])],
              foreground=[('pressed', t['on_selection_pressed']), ('active', t['on_selection_hover'])])
        s.configure('Link.TButton', background=t['background'], foreground=readable(t['primary'], t['background']),
                    borderwidth=0, padding=(4, 6))
        s.map('Link.TButton', background=[('disabled', t['background']), ('pressed', t['selection_pressed']),
                                          ('active', t['selection_hover'])],
              foreground=[('disabled', readable(t['muted'], t['background'])), ('pressed', t['on_selection_pressed']),
                          ('active', t['on_selection_hover'])])
        s.configure('Card.Link.TButton', background=t['surface'], foreground=readable(t['primary'], t['surface']))
        s.map('Card.Link.TButton', background=[('disabled', t['surface']), ('pressed', t['selection_pressed']),
                                               ('active', t['selection_hover'])])
        s.configure('Tile.TButton', background=t['surface'], foreground=t['text'], anchor='w', padding=(18, 16),
                    borderwidth=1, bordercolor=t['separator'], relief='solid', font=f.strong(1),
                    focuscolor=t['focus'], focusthickness=2)
        s.map('Tile.TButton', background=[('pressed', t['selection_pressed']), ('active', t['selection'])],
              foreground=[('pressed', t['on_selection_pressed']), ('active', t['on_selection'])],
              bordercolor=[('focus', t['focus']), ('active', t['focus'])])
        for style in ('TEntry', 'TCombobox', 'TSpinbox'):
            s.configure(style, fieldbackground=t['surface'], foreground=t['text'], insertcolor=t['text'], padding=6,
                        selectbackground=t['selection'], selectforeground=t['on_selection'], bordercolor=t['border'],
                        arrowsize=14, arrowcolor=readable(t['muted'], t['surface']), background=t['surface'],
                        lightcolor=t['surface'], darkcolor=t['surface'])
            s.map(style, fieldbackground=[('disabled', t['secondary_disabled']), ('readonly', t['surface'])],
                  foreground=[('disabled', t['on_secondary_disabled']), ('readonly', t['text'])],
                  bordercolor=[('invalid', t['error_fg']), ('focus', t['focus'])],
                  lightcolor=[('focus', t['focus'])], darkcolor=[('focus', t['focus'])])
        s.configure('TSpinbox', arrowsize=11)
        s.configure('TMenubutton', background=t['secondary'], foreground=t['on_secondary'], padding=(12, 7),
                    borderwidth=0, relief='flat', arrowcolor=t['on_secondary'], font=f.body())
        s.map('TMenubutton', background=[('disabled', t['secondary_disabled']), ('pressed', t['secondary_pressed']),
                                         ('active', t['secondary_hover'])],
              foreground=[('disabled', t['on_secondary_disabled']), ('active', t['on_secondary_hover'])])
        s.map('TCombobox', selectbackground=[('!focus', t['surface']), ('focus', t['selection'])],
              selectforeground=[('!focus', t['text']), ('focus', t['on_selection'])])
        s.configure('Treeview', background=t['surface'], fieldbackground=t['surface'], foreground=t['text'],
                    rowheight=self.row_height(), bordercolor=t['separator'],
                    lightcolor=t['separator'], darkcolor=t['separator'], borderwidth=1, font=f.body())
        s.map('Treeview', background=[('selected', t['selection'])], foreground=[('selected', t['on_selection'])])
        # Tablas con dos líneas por fila (la clave y el nombre completo de la estación, por ejemplo).
        s.configure('TwoLine.Treeview', rowheight=self.row_height(2))
        s.configure('Treeview.Heading', background=t['surface'], foreground=t['muted_surface'], padding=6,
                    borderwidth=0, font=f.strong(-1))
        s.map('Treeview.Heading', background=[('active', t['secondary_hover'])],
              foreground=[('active', t['on_secondary_hover'])])
        s.configure('TNotebook', background=t['background'], borderwidth=0)
        s.configure('TNotebook.Tab', background=t['secondary'], foreground=t['on_secondary'], padding=(14, 7))
        s.map('TNotebook.Tab', background=[('selected', t['button']), ('active', t['secondary_hover'])],
              foreground=[('selected', t['on_button']), ('active', t['on_secondary_hover'])])
        self.indicators(t)
        for style, bg in (('TCheckbutton', 'background'), ('TRadiobutton', 'background'),
                          ('Card.TCheckbutton', 'surface'), ('Card.TRadiobutton', 'surface')):
            s.configure(style, background=t[bg], foreground=t['text'], indicatorbackground=t['surface'],
                        indicatorforeground=t['text'], focuscolor=t['focus'], font=f.body())
            s.map(style, background=[('active', t[bg])],
                  foreground=[('disabled', readable(t['muted'], t[bg]))],
                  indicatorbackground=[('selected', t['button']), ('pressed', t['selection'])],
                  indicatorforeground=[('selected', t['on_button'])])
        s.configure('Horizontal.TProgressbar', background=t['button'], troughcolor=t['secondary'],
                    bordercolor=t['secondary'], lightcolor=t['button'], darkcolor=t['button'], thickness=8)
        # Barras de desplazamiento delgadas, sin flechas (como las del sistema).
        if not getattr(self, '_scroll_layout', False):
            s.layout('Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
                ('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
            s.layout('Horizontal.TScrollbar', [('Horizontal.Scrollbar.trough', {'sticky': 'ew', 'children': [
                ('Horizontal.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
            self._scroll_layout = True
        for orient in ('Vertical', 'Horizontal'):
            s.configure(orient+'.TScrollbar', background=mix(t['background'], t['muted'], .35),
                        troughcolor=t['background'], bordercolor=t['background'], lightcolor=t['background'],
                        darkcolor=t['background'], gripcount=0, arrowsize=8, width=8)
            s.map(orient+'.TScrollbar', background=[('active', mix(t['background'], t['muted'], .6))])
        # Sin nada que desplazar, la barra se funde con el fondo pero conserva su lugar.
        s.layout('Idle.Vertical.TScrollbar', s.layout('Vertical.TScrollbar'))
        s.configure('Idle.Vertical.TScrollbar', background=t['background'], troughcolor=t['background'],
                    bordercolor=t['background'], lightcolor=t['background'], darkcolor=t['background'],
                    arrowsize=8, width=8)
        s.map('Idle.Vertical.TScrollbar', background=[('active', t['background'])])
        # Cápsulas de clase del catálogo (color de serie del tema, fondo suave y texto con contraste 4.5:1).
        from app.catalog_view import CLASSES
        for cls, (_, _, token) in CLASSES.items():
            back = mix(t[token], t['surface'], .84)
            fore = readable(t[token], back)
            t['cap_'+cls+'_bg'], t['cap_'+cls+'_fg'] = back, fore
            s.configure(f'Cap.{cls}.TLabel', background=back, foreground=fore, padding=(8, 3), font=f.strong(-2))
        for name, bg, fg in (('Installed', 'success_bg', 'success_fg'), ('Available', 'info_bg', 'info_fg'),
                             ('Update', 'warning_bg', 'warning_fg'), ('Neutral', 'secondary', 'on_secondary')):
            s.configure(f'Cap.{name}.TLabel', background=t[bg], foreground=t[fg], padding=(8, 3), font=f.strong(-2))
        for style, bg, fg in (('Seg.TButton', 'surface', 'text'), ('Seg.Active.TButton', 'selection', 'on_selection')):
            s.configure(style, background=t[bg], foreground=t[fg], padding=(12, 6), borderwidth=1,
                        bordercolor=t['separator'] if bg == 'surface' else t['focus'], relief='solid', font=f.body(-1),
                        focuscolor=t['focus'], focusthickness=2)
            s.map(style, background=[('pressed', t['selection_pressed']), ('active', t['selection_hover'])],
                  foreground=[('pressed', t['on_selection_pressed']), ('active', t['on_selection_hover'])])
        for kind in ('error', 'warning', 'success', 'info'):
            s.configure(kind+'.TLabel', background=t[kind+'_bg'], foreground=t[kind+'_fg'], padding=10)
            s.configure(kind+'.TFrame', background=t[kind+'_bg'])
        self.root.option_add('*TCombobox*Listbox.background', t['surface'])
        self.root.option_add('*TCombobox*Listbox.foreground', t['text'])
        self.root.option_add('*TCombobox*Listbox.selectBackground', t['selection'])
        self.root.option_add('*TCombobox*Listbox.selectForeground', t['on_selection'])
        self.walk(self.root)
        self.callbacks = [(w, fn) for w, fn in self.callbacks if w.winfo_exists()]
        for _, callback in self.callbacks:
            callback(t)

    def row_height(self, lines=1):
        """Alto de fila de las tablas a partir de la letra (crece con «Tamaño de letra»), para `lines` líneas."""
        line = tkfont.Font(root=self.root, font=self.fonts.body()).metrics('linespace')
        return int(max(line*lines+14, 26)*getattr(self.root, 'ui_scale', 1))

    def text_scale(self):
        return self.fonts.base/self.fonts.normal

    def indicators(self, t):
        """Casillas y botones de opción dibujados (marca ✓ y punto), con estados de foco y deshabilitado."""
        from PIL import Image, ImageDraw, ImageTk
        size = int(18*getattr(self.root, 'ui_scale', 1))
        big = size*4

        def make(kind, on, disabled=False):
            image = Image.new('RGBA', (big, big), (0, 0, 0, 0))
            d = ImageDraw.Draw(image)
            accent = t['button_disabled'] if disabled else t['button']
            border = mix(t['border'], t['surface'], .4) if disabled else t['border']
            box = (6, 6, big-6, big-6)
            if kind == 'check':
                if on:
                    d.rounded_rectangle(box, radius=big//5, fill=accent)
                    d.line([(big*.27, big*.52), (big*.43, big*.68), (big*.74, big*.34)], fill=t['on_button'],
                           width=max(4, big//10), joint='curve')
                else:
                    d.rounded_rectangle(box, radius=big//5, fill=t['surface'], outline=border, width=max(3, big//18))
            else:
                if on:
                    d.ellipse(box, fill=accent)
                    c, r = big/2, big*.17
                    d.ellipse((c-r, c-r, c+r, c+r), fill=t['on_button'])
                else:
                    d.ellipse(box, fill=t['surface'], outline=border, width=max(3, big//18))
            framed = Image.new('RGBA', (size+int(7*getattr(self.root, 'ui_scale', 1)), size), (0, 0, 0, 0))
            framed.alpha_composite(image.resize((size, size), Image.Resampling.LANCZOS))
            return framed

        keys = [(k, on, dis) for k in ('check', 'radio') for on in (False, True) for dis in (False, True)]
        first = not hasattr(self, 'indicator_images')
        if first:
            self.indicator_images = {key: ImageTk.PhotoImage(make(*key), master=self.root) for key in keys}
        else:
            # Cambio de tema: se repintan las mismas imágenes; los elementos ttk que las usan se actualizan solos.
            for key in keys:
                self.indicator_images[key].paste(make(*key))
            return
        images, s = self.indicator_images, self.style
        for kind, widget in (('check', 'Checkbutton'), ('radio', 'Radiobutton')):
            element = f'Tv.{widget}.indicator'
            s.element_create(element, 'image', images[(kind, False, False)],
                             ('disabled', 'selected', images[(kind, True, True)]),
                             ('disabled', images[(kind, False, True)]),
                             ('selected', images[(kind, True, False)]), sticky='w', padding=(0, 0, 8, 0))
            for prefix in ('', 'Card.'):
                s.layout(prefix+'T'+widget, [('T'+widget+'.padding', {'sticky': 'nswe', 'children': [
                    (element, {'side': 'left', 'sticky': ''}),
                    ('T'+widget+'.focus', {'side': 'left', 'sticky': 'w', 'children': [
                        ('T'+widget+'.label', {'sticky': 'nswe'})]})]})])

    def walk(self, widget):
        """Colores de los widgets clásicos de Tk (los ttk los toma de los estilos)."""
        t = self.tokens
        if t is None:
            return
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            widget.configure(background=t['background'])
        elif isinstance(widget, tk.Text):
            widget.configure(background=t['surface'], foreground=t['text'], insertbackground=t['text'],
                             selectbackground=t['selection'], selectforeground=t['on_selection'],
                             highlightbackground=t['border'], highlightcolor=t['focus'])
        elif isinstance(widget, tk.Canvas) and not getattr(widget, 'own_palette', False):
            widget.configure(background=t['background' if getattr(widget, 'scroll_surface', False) else 'surface'],
                             highlightbackground=t['border'])
        elif isinstance(widget, tk.Listbox):
            widget.configure(background=t['surface'], foreground=t['text'], selectbackground=t['selection'],
                             selectforeground=t['on_selection'], highlightbackground=t['border'],
                             highlightcolor=t['focus'])
        elif isinstance(widget, tk.Menu):
            widget.configure(background=t['surface'], foreground=t['text'], activebackground=t['selection'],
                             activeforeground=t['on_selection'], disabledforeground=readable(t['muted'], t['surface']))
        for child in widget.winfo_children():
            self.walk(child)
