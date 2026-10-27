"""Gráficas en pantalla dibujadas en Canvas con los tokens del tema (valores y etiquetas siempre escritos).

Las gráficas completas (cielo, satélites, residuos, ratio, DOP) van en el informe PDF; aquí se muestra lo necesario
para revisar el resultado de un vistazo: el esquema en planta y las barras de precisión.
"""
from __future__ import annotations

import math
import tkinter as tk

from tresvizo_ppk.model import Ecef

A, F = 6378137.0, 1/298.257222101


def ecef_to_geodetic(p: Ecef) -> tuple[float, float]:
    e2 = F*(2-F)
    lon = math.atan2(p.y, p.x)
    r = math.hypot(p.x, p.y)
    lat = math.atan2(p.z, r*(1-e2))
    for _ in range(6):
        n = A/math.sqrt(1-e2*math.sin(lat)**2)
        h = r/math.cos(lat)-n
        lat = math.atan2(p.z, r*(1-e2*n/(n+h)))
    return math.degrees(lat), math.degrees(lon)


def enu_of(origin: Ecef, point: Ecef) -> tuple[float, float, float]:
    lat, lon = (math.radians(v) for v in ecef_to_geodetic(origin))
    dx, dy, dz = point.x-origin.x, point.y-origin.y, point.z-origin.z
    e = -math.sin(lon)*dx+math.cos(lon)*dy
    n = -math.sin(lat)*math.cos(lon)*dx-math.sin(lat)*math.sin(lon)*dy+math.cos(lat)*dz
    u = math.cos(lat)*math.cos(lon)*dx+math.cos(lat)*math.sin(lon)*dy+math.sin(lat)*dz
    return e, n, u


def enu_sigma(origin: Ecef, covariance) -> tuple[float, float]:
    """(σ horizontal, σ vertical) de una covarianza ECEF girada a ENU en `origin`."""
    lat, lon = (math.radians(v) for v in ecef_to_geodetic(origin))
    r = [(-math.sin(lon), math.cos(lon), 0.0),
         (-math.sin(lat)*math.cos(lon), -math.sin(lat)*math.sin(lon), math.cos(lat)),
         (math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat))]
    c = [[sum(r[i][k]*covariance[k][m]*r[j][m] for k in range(3) for m in range(3)) for j in range(3)]
         for i in range(3)]
    return math.sqrt(max(0.0, c[0][0]+c[1][1])), math.sqrt(max(0.0, c[2][2]))


def nice_step(span: float) -> float:
    if span <= 0:
        return 1.0
    raw = span/4
    power = 10**math.floor(math.log10(raw))
    for k in (1, 2, 5, 10):
        if raw <= k*power:
            return k*power
    return 10*power


def distance_text(m: float) -> str:
    return f'{m/1000:g} km' if m >= 1000 else f'{m:g} m'


class _Chart(tk.Canvas):
    def __init__(self, parent, app, height=300):
        super().__init__(parent, height=height, highlightthickness=0, borderwidth=0)
        self.items, self.rows, self.title, self.limit = [], [], '', None
        self.value_text = lambda v: f'{v:.3f} m'
        self.own_palette = True
        self.app = app
        self.tokens = app.theme.tokens
        self.bind('<Configure>', lambda e: self.draw())
        app.theme.subscribe(self, self.retheme)

    def retheme(self, tokens):
        self.tokens = tokens
        self.draw()

    def font(self, delta=-2, strong=False):
        f = self.app.theme.fonts
        return f.strong(delta) if strong else f.body(delta)

    def draw(self):  # pragma: no cover - lo implementan las subclases
        pass


class PlanChart(_Chart):
    """Esquema en planta (este/norte, misma escala en los dos ejes) con norte, escala y leyenda escrita."""

    def __init__(self, parent, app, height=320):
        super().__init__(parent, app, height)

    def set(self, items, title=''):
        self.items, self.title = list(items), title
        self.draw()

    def draw(self):
        t = self.tokens
        if not t:
            return
        self.configure(background=t['surface'])
        self.delete('all')
        width, height = max(self.winfo_width(), 320), max(self.winfo_height(), 220)
        if not self.items:
            self.create_text(width/2, height/2, text='Sin datos para dibujar.', fill=t['muted_surface'],
                             font=self.font())
            return
        from tkinter import font as tkfont
        small = tkfont.Font(root=self, font=self.font(-3))
        label_w = max([small.measure(i.get('label', '')) for i in self.items]+[0])
        # Márgenes automáticos: las etiquetas caben a los dos lados sin cortarse en la orilla.
        side = min(label_w+18, width*0.3)
        pad_l, pad_r, pad_t, pad_b = max(24, side), max(24, side), 34 if self.title else 22, 46
        es = [i['e'] for i in self.items]
        ns = [i['n'] for i in self.items]
        span = max(max(es)-min(es), max(ns)-min(ns), 1.0)*1.15
        scale = min((width-pad_l-pad_r)/span, (height-pad_t-pad_b)/span)
        ce, cn = (max(es)+min(es))/2, (max(ns)+min(ns))/2
        cx, cy = pad_l+(width-pad_l-pad_r)/2, pad_t+(height-pad_t-pad_b)/2

        def xy(e, n):
            return cx+(e-ce)*scale, cy-(n-cn)*scale
        if self.title:
            self.create_text(pad_l, 14, text=self.title, anchor='w', fill=t['text'], font=self.font(-1, True))
        # Líneas base de cada estación al punto (o a la base).
        for item in self.items:
            if item.get('link') is not None:
                x0, y0 = xy(*item['link'])
                x1, y1 = xy(item['e'], item['n'])
                self.create_line(x0, y0, x1, y1, fill=t['border'], dash=(4, 3), width=1)
        legend = {}
        for item in self.items:
            x, y = xy(item['e'], item['n'])
            if item['kind'] == 'base':
                color = t['chart1']
                self.create_polygon(x, y-9, x+8, y+6, x-8, y+6, fill=color, outline=t['surface'])
                legend['Estación base'] = ('base', color)
            else:
                key = item.get('solution', 'none')
                color = t['solution_'+key]
                r = 5 if item['kind'] == 'point' else 6
                self.create_oval(x-r, y-r, x+r, y+r, fill=color, outline=t['surface'])
                legend[{'fixed': 'Fija', 'float': 'Flotante', 'single': 'Autónoma', 'none': 'Sin solución'}[key]] = \
                    ('point', color)
            if item.get('label'):
                text = item['label']
                w = small.measure(text)
                if x+10+w <= width-4:
                    self.create_text(x+10, y-10, text=text, anchor='w', fill=t['text'], font=self.font(-3))
                else:                                    # sin espacio a la derecha: a la izquierda del punto
                    self.create_text(max(4+w, x-10), y-10, text=text, anchor='e', fill=t['text'], font=self.font(-3))
        # Norte y escala.
        nx, ny = width-pad_r-8, pad_t+6
        self.create_line(nx, ny+26, nx, ny, arrow='last', fill=t['text'], width=2)
        self.create_text(nx, ny+38, text='N', fill=t['text'], font=self.font(-3, True))
        step = nice_step(span)
        bar = step*scale
        while bar > (width-pad_l-pad_r)/2.5 and step > 0.01:
            step /= 2
            bar = step*scale
        y = height-18
        self.create_line(pad_l, y, pad_l+bar, y, fill=t['text'], width=2)
        for x in (pad_l, pad_l+bar):
            self.create_line(x, y-4, x, y+4, fill=t['text'], width=2)
        scale_text = self.create_text(pad_l+bar+8, y, text=distance_text(step), anchor='w', fill=t['text'],
                                      font=self.font(-3))
        scale_right = self.bbox(scale_text)[2]

        def draw_legend(y):
            items, x = [], width-pad_r
            for name, (shape, color) in reversed(list(legend.items())):
                label = self.create_text(x, y, text=name, anchor='e', fill=t['text'], font=self.font(-3))
                left = self.bbox(label)[0]
                if shape == 'base':
                    mark = self.create_polygon(left-12, y-6, left-6, y+5, left-18, y+5, fill=color, outline='')
                else:
                    mark = self.create_oval(left-16, y-5, left-6, y+5, fill=color, outline='')
                items += [label, mark]
                x = left-28
            return items, x+28-18
        items, left = draw_legend(y)
        if left < scale_right+12:
            # En gráficas angostas la leyenda no cabe junto a la escala: va en su propio renglón, encima.
            for item in items:
                self.delete(item)
            draw_legend(y-22)


class BarChart(_Chart):
    """Barras horizontales con el valor escrito; opcionalmente una línea de tolerancia."""

    def __init__(self, parent, app, height=220):
        super().__init__(parent, app, height)

    def set(self, rows, title='', limit=None, value_text=None):
        """rows: [(etiqueta, valor, clave de color)]."""
        self.rows, self.title, self.limit = list(rows), title, limit
        self.value_text = value_text or (lambda v: f'{v:.3f} m')
        self.configure(height=max(120, 46+len(self.rows[:24])*24))
        self.draw()

    def draw(self):
        t = self.tokens
        if not t:
            return
        self.configure(background=t['surface'])
        self.delete('all')
        width = max(self.winfo_width(), 320)
        if self.title:
            self.create_text(12, 14, text=self.title, anchor='w', fill=t['text'], font=self.font(-1, True))
        if not self.rows:
            self.create_text(width/2, 60, text='Sin datos.', fill=t['muted_surface'], font=self.font())
            return
        from tkinter import font as tkfont
        small = tkfont.Font(root=self, font=self.font(-3))
        rows = self.rows[:24]
        maximum = max([v for _, v, _ in rows]+([self.limit] if self.limit else [])) or 1.0
        # Columnas de etiqueta y valor medidas con su letra (la etiqueta, hasta el 40 % del ancho).
        label_w = min(max(small.measure(r[0]) for r in rows)+24, int(width*0.4))
        value_w = max(small.measure(self.value_text(v)) for _, v, _ in rows)+24
        left, right = label_w, width-value_w

        def fit(text):
            if small.measure(text) <= label_w-20:
                return text
            while text and small.measure(text+'…') > label_w-20:
                text = text[:-1]
            return text+'…'
        for index, (label, value, key) in enumerate(rows):
            y = 42+index*24
            self.create_text(12, y, text=fit(label), anchor='w', fill=t['text'], font=self.font(-3))
            length = (right-left)*max(0.0, value)/maximum
            color = t.get('solution_'+key, t.get(key, t['chart1']))
            self.create_rectangle(left, y-7, left+max(2, length), y+7, fill=color, outline='')
            self.create_text(width-10, y, text=self.value_text(value), anchor='e', fill=t['text'], font=self.font(-3))
        if self.limit:
            x = left+(right-left)*self.limit/maximum
            self.create_line(x, 30, x, 42+(len(rows)-1)*24+10, fill=t['text'], dash=(3, 3))
            self.create_text(x, 26, text=f'tolerancia {self.value_text(self.limit)}', fill=t['muted_surface'],
                             font=self.font(-4))
        if len(self.rows) > 24:
            self.create_text(12, 42+24*24, text=f'… y {len(self.rows)-24} más en el informe.', anchor='w',
                             fill=t['muted_surface'], font=self.font(-3))
