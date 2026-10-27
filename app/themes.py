"""Tokens de color, contraste y estados derivados. Sin dependencias de Tk.

Mismo sistema visual que TresVizo Med (`app/themes.py`, MIT, del mismo propietario): fondo #F5F5F7, tarjetas
blancas, texto #1D1D1F, secundario #626269, azul #0066CC; contraste por luminancia relativa sRGB, 4.5:1 para texto y
3:1 para bordes, foco y series de gráficas.
"""
from __future__ import annotations

from copy import deepcopy
import re

COLORS = {
    'primary': 'Color principal', 'accent': 'Color de acento',
    'background': 'Fondo general', 'surface': 'Tarjetas y paneles',
    'header': 'Barra superior', 'sidebar': 'Navegación lateral',
    'text': 'Texto principal', 'muted': 'Texto secundario',
    'border': 'Bordes y separadores', 'button': 'Botones principales',
    'secondary': 'Botones secundarios', 'selection': 'Selección', 'focus': 'Foco',
    **{f'chart{i}': f'Gráficas · serie {i}' for i in range(1, 7)},
}
BASE = dict(zip(COLORS, ['#0066CC', '#087B86', '#F5F5F7', '#FFFFFF', '#FFFFFF', '#EFEFF2',
                         '#1D1D1F', '#626269', '#85858B', '#0066CC', '#EFEFF2', '#E8F1FC', '#0066CC',
                         '#086A8C', '#197653', '#8055A1', '#9A6200', '#A24468', '#4269A6']))
BUILTINS = {
    'Claro': BASE,
    'Oscuro': {**BASE, 'background': '#101D2E', 'surface': '#1C2D42', 'header': '#132239',
               'sidebar': '#132239', 'text': '#F0F6FC', 'muted': '#B9CDDD', 'border': '#8197AE',
               'primary': '#A7D8FF', 'accent': '#69CBD0', 'button': '#70C7EA', 'secondary': '#304861',
               'selection': '#416185', 'focus': '#70C7EA', 'chart1': '#70C7EA', 'chart2': '#76DBAD',
               'chart3': '#C5A0EA', 'chart4': '#EBC576', 'chart5': '#F29AB9', 'chart6': '#9FB5FF'},
}
SEMANTIC = {'error_bg': '#FDEBEC', 'error_fg': '#9E182C', 'warning_bg': '#FFF2CE',
            'warning_fg': '#714B00', 'success_bg': '#E1F4E8', 'success_fg': '#185631',
            'info_bg': '#E8F1FC', 'info_fg': '#0B4A8B'}
# Estado de la solución GNSS: el mismo código de color que las apps de campo, siempre con texto al lado.
SOLUTION = {'fixed': '#197653', 'float': '#9A6200', 'single': '#A24468', 'none': '#626269'}


def color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('Usa un color hexadecimal completo: #RRGGBB.')
    return value.upper()


def luminance(value):
    rgb = [int(value[i:i+2], 16)/255 for i in (1, 3, 5)]
    linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in rgb]
    return sum(a*b for a, b in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)))
    return (y+.05)/(x+.05)


def on(bg):
    return max(('#FFFFFF', '#000000'), key=lambda fg: contrast(fg, bg))


def mix(a, b, amount):
    return '#'+''.join(f'{round(int(a[i:i+2], 16)*(1-amount)+int(b[i:i+2], 16)*amount):02X}' for i in (1, 3, 5))


def readable(fg, bg, minimum=4.5):
    if contrast(fg, bg) >= minimum:
        return fg
    target = on(bg)
    for n in range(1, 101):
        candidate = mix(fg, target, n/100)
        if contrast(candidate, bg) >= minimum:
            return candidate
    return target


def validate_tokens(tokens):
    if not isinstance(tokens, dict) or set(tokens) != set(COLORS):
        raise ValueError('La paleta debe contener exactamente todos los tokens de color admitidos.')
    return {key: color(value) for key, value in tokens.items()}


def issues(t):
    """Pares que no cumplen el contraste mínimo (lista vacía: la paleta es válida)."""
    found = []
    for fg in ('text', 'muted', 'primary', 'accent'):
        for bg in ('background', 'surface'):
            ratio = contrast(t[fg], t[bg])
            if ratio < 4.5:
                found.append(f'{COLORS[fg]} sobre {COLORS[bg]}: {ratio:.2f}:1 (mínimo 4.5:1).')
    for fg in ('border', 'focus'):
        for bg in ('background', 'surface'):
            if contrast(t[fg], t[bg]) < 3:
                found.append(f'{COLORS[fg]} sobre {COLORS[bg]}: contraste menor que 3:1.')
    for key in (f'chart{i}' for i in range(1, 7)):
        if contrast(t[key], t['surface']) < 3:
            found.append(f'{COLORS[key]}: contraste menor que 3:1 en paneles.')
    return found


def derived(tokens):
    t = {**validate_tokens(tokens), **SEMANTIC}
    dark = luminance(t['surface']) < .2
    if dark:
        # En el tema oscuro, las alertas y los estados usan fondos oscuros con texto claro (mismo contraste).
        t.update({'error_bg': '#4A1820', 'error_fg': '#FFD5DA', 'warning_bg': '#4A3A10', 'warning_fg': '#FFE7A8',
                  'success_bg': '#14402A', 'success_fg': '#C8F1D8', 'info_bg': '#173A5E', 'info_fg': '#D3E8FF'})
    for key, value in SOLUTION.items():
        t['solution_'+key] = readable(value, t['surface'], 3)
    t['separator'] = mix(t['surface'], t['text'], .13)
    t['surface_secondary'] = mix(t['surface'], t['background'], .6)
    t['muted_surface'] = readable(t['muted'], t['surface'])
    for key in ('header', 'sidebar', 'button', 'secondary', 'selection', 'surface'):
        t[f'on_{key}'] = t['text'] if key == 'surface' else on(t[key])
        for state, amount in [('hover', .10), ('pressed', .19), ('disabled', .48)]:
            t[f'{key}_{state}'] = mix(t[key], t['surface'] if state == 'disabled' else on(t[key]), amount)
            t[f'on_{key}_{state}'] = on(t[f'{key}_{state}'])
    return t


def palette(name):
    return deepcopy(BUILTINS.get(name, BUILTINS['Claro']))
