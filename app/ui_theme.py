"""Estilos ttk a partir de los tokens; se cambian en el mismo árbol de widgets, sin reconstruir pantallas."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

from app.themes import derived, readable


def font_families(root) -> tuple[str, str]:
    """Letra del sistema: Segoe UI en Windows (como TresVizo Med), la del sistema en macOS y Linux."""
    if sys.platform == 'win32':
        return 'Segoe UI', 'Segoe UI Semibold'
    family = tkfont.nametofont('TkDefaultFont', root=root).actual('family')
    return family, family


class Fonts:
    def __init__(self, root):
        self.regular, self.semibold = font_families(root)
        # Tk mide en puntos; macOS dibuja a 72 ppp y Windows a 96: misma altura visual con tamaños distintos.
        self.base = 13 if sys.platform == 'darwin' else 10 if sys.platform == 'win32' else 10
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
                        arrowsize=14, arrowcolor=t['text'])
            s.map(style, fieldbackground=[('disabled', t['secondary_disabled']), ('readonly', t['surface'])],
                  foreground=[('disabled', t['on_secondary_disabled']), ('readonly', t['text'])],
                  bordercolor=[('invalid', t['error_fg']), ('focus', t['focus'])],
                  lightcolor=[('focus', t['focus'])], darkcolor=[('focus', t['focus'])])
        s.map('TCombobox', selectbackground=[('!focus', t['surface']), ('focus', t['selection'])],
              selectforeground=[('!focus', t['text']), ('focus', t['on_selection'])])
        s.configure('Treeview', background=t['surface'], fieldbackground=t['surface'], foreground=t['text'],
                    rowheight=int((30 if sys.platform == 'darwin' else 32)*scale), bordercolor=t['separator'],
                    borderwidth=0, font=f.body())
        s.map('Treeview', background=[('selected', t['selection'])], foreground=[('selected', t['on_selection'])])
        s.configure('Treeview.Heading', background=t['surface'], foreground=t['muted_surface'], padding=6,
                    borderwidth=0, font=f.strong(-1))
        s.map('Treeview.Heading', background=[('active', t['secondary_hover'])],
              foreground=[('active', t['on_secondary_hover'])])
        s.configure('TNotebook', background=t['background'], borderwidth=0)
        s.configure('TNotebook.Tab', background=t['secondary'], foreground=t['on_secondary'], padding=(14, 7))
        s.map('TNotebook.Tab', background=[('selected', t['button']), ('active', t['secondary_hover'])],
              foreground=[('selected', t['on_button']), ('active', t['on_secondary_hover'])])
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
        s.configure('TScrollbar', background=t['secondary'], troughcolor=t['background'], bordercolor=t['background'],
                    arrowcolor=t['text'])
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
