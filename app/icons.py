"""Iconos Lucide incluidos localmente (assets/icons, licencia ISC), coloreados con los tokens del tema."""
from __future__ import annotations

from PIL import Image, ImageTk

from app.paths import ASSETS
from app.themes import readable

# Estilo del botón → token de fondo del que salen los colores del icono.
STYLE_KEYS = {'Active.Nav.TButton': 'selection', 'Nav.TButton': 'sidebar', 'Primary.TButton': 'button',
              'TButton': 'secondary', 'TMenubutton': 'secondary', '': 'secondary'}


class Icons:
    def __init__(self, theme):
        self.theme, self.cache = theme, {}

    def photo(self, name, size=18, color=None):
        color = color or self.theme.tokens['text']
        key = (name, size, color)
        if key not in self.cache:
            with Image.open(ASSETS/'icons'/(name+'.png')) as source:
                alpha = source.convert('RGBA').getchannel('A').resize((size, size), Image.Resampling.LANCZOS)
            result = Image.new('RGBA', (size, size), color)
            result.putalpha(alpha)
            self.cache[key] = ImageTk.PhotoImage(result, master=self.theme.root)
        return self.cache[key]

    def size(self, base=18):
        return int(base*getattr(self.theme.root, 'ui_scale', 1))

    def bind(self, widget, name, size=18, show_text=True):
        """Icono con los colores de cada estado del botón (reposo, hover, presionado, deshabilitado)."""
        widget.icon_name = name

        def update(tokens):
            if not widget.winfo_exists():
                return
            style = str(widget.cget('style'))
            pixels = self.size(size)
            if style.endswith('Link.TButton'):
                bg = tokens['surface'] if style.startswith('Card.') else tokens['background']
                states = [self.photo(widget.icon_name, pixels, readable(tokens['primary'], bg)),
                          'disabled', self.photo(widget.icon_name, pixels, readable(tokens['muted'], bg)),
                          'pressed', self.photo(widget.icon_name, pixels, tokens['on_selection_pressed']),
                          'active', self.photo(widget.icon_name, pixels, tokens['on_selection_hover'])]
            elif style == 'Tile.TButton':
                states = [self.photo(widget.icon_name, pixels, readable(tokens['primary'], tokens['surface'])),
                          'pressed', self.photo(widget.icon_name, pixels, tokens['on_selection_pressed']),
                          'active', self.photo(widget.icon_name, pixels, readable(tokens['primary'], tokens['selection']))]
            else:
                key = STYLE_KEYS.get(style, 'secondary')
                states = [self.photo(widget.icon_name, pixels, tokens['on_'+key])]
                for state, variant in [('disabled', 'disabled'), ('pressed', 'pressed'), ('active', 'hover')]:
                    states.extend((state, self.photo(widget.icon_name, pixels, tokens[f'on_{key}_{variant}'])))
            widget.icon_states = states
            widget.configure(image=tuple(states), compound='left' if show_text else 'image')

        widget.refresh_icon = lambda: update(self.theme.tokens)
        self.theme.subscribe(widget, update)
        return widget

    def label(self, parent, name, size=18, token='text', background='background', **kwargs):
        """Icono suelto en una etiqueta (encabezados, avisos)."""
        from tkinter import ttk
        label = ttk.Label(parent, **kwargs)

        def update(tokens):
            if label.winfo_exists():
                label.image = self.photo(name, self.size(size), readable(tokens[token], tokens[background], 3))
                label.configure(image=label.image)
        self.theme.subscribe(label, update)
        return label
