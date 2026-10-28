"""Editor de sesión, como el Session Editor de Trimble Business Center: la línea de tiempo de cada satélite (sus tramos
con datos, los huecos y los saltos de ciclo que ya calculó el control de calidad), recortar el inicio y el fin
(arrastrando las marcas o escribiéndolos), dejar fuera satélites o constelaciones, volver a procesar y comparar con el
resultado anterior (ΔE, ΔN, ΔU, σ y fijo o flotante).

Lo editado son `ProcessingOptions.time_window` y `excluded_sats`: se guardan en el proyecto y valen para todas las
líneas base del estático y para la trayectoria del PPK (`tresvizo_ppk.core.sesion`).
"""
from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from tkinter import font as tkfont
from tkinter import ttk

from tresvizo_ppk.core import sesion
from tresvizo_ppk.errors import UserError
from tresvizo_ppk.model import DataQuality, Ecef, Enu, Solution, TimeWindow

from app.charts import enu_of, enu_sigma
from app.components import ScrollFrame, ThinScrollbar, Tooltip, attach_dialog, card, text_width, wrap_label
from app.formatting import plural, solution_text
from app.settings import format_length

UTC = timezone.utc
TITLE = 'Sesión'
SYSTEM_ORDER = 'GRECJSI'
TIME_FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S', '%H:%M:%S', '%H:%M')
# Pasos del eje de tiempo (segundos): se elige el menor cuyas etiquetas no se encimen.
TIME_STEPS = (10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
HANDLE_PX = 8                  # distancia a la que se agarra una marca de inicio o fin
SOLUTION_ORDER = [Solution.FIXED, Solution.FLOAT, Solution.SINGLE, Solution.NONE]


# --- Comparación con el resultado anterior (sin Tk) --------------------------------------------------------------------

@dataclass
class Change:
    """Un renglón de la comparación: ahora − antes, en ENU local del resultado anterior (m)."""
    name: str
    delta: tuple[float, float, float] | None          # ΔE, ΔN, ΔU; None si uno de los dos no tiene posición
    sigma_before: tuple[float, float] | None          # σ H, σ V
    sigma_after: tuple[float, float] | None
    solution_before: Solution
    solution_after: Solution


def _hv(sigma: Enu | None) -> tuple[float, float] | None:
    return None if sigma is None else (math.hypot(sigma.e, sigma.n), sigma.u)


def _delta(before: Ecef | None, after: Ecef | None) -> tuple[float, float, float] | None:
    if before is None or after is None:
        return None
    return enu_of(before, after)


def _worst(solutions) -> Solution:
    found = [Solution(s) for s in solutions]
    return max(found, key=SOLUTION_ORDER.index) if found else Solution.NONE


def compare_static(before, after) -> list[Change]:
    """El vértice (la coordenada final) y cada línea base que está en los dos resultados."""
    rows = [Change(f'Vértice {after.point_name}', _delta(before.position, after.position), _hv(before.sigma_enu),
                   _hv(after.sigma_enu), _worst(b.solution for b in before.baselines),
                   _worst(b.solution for b in after.baselines))]
    old = {(b.base.id or b.base.name): b for b in before.baselines}
    for b in after.baselines:
        key = b.base.id or b.base.name
        a = old.get(key)
        if a is None:
            continue
        rows.append(Change(f'Línea base {key}', _delta(a.rover_position, b.rover_position),
                           enu_sigma(a.rover_position, a.covariance_ecef),
                           enu_sigma(b.rover_position, b.covariance_ecef), Solution(a.solution), Solution(b.solution)))
    return rows


def compare_points(before, after) -> list[Change]:
    """Cada ocupación del CSV (por nombre e inicio), en el orden del resultado nuevo."""
    old = {}
    for p in before.points:
        old.setdefault((p.point.name, p.point.start), p)
    rows = []
    for p in after.points:
        a = old.get((p.point.name, p.point.start))
        if a is None:
            continue
        rows.append(Change(p.point.name, _delta(a.position, p.position), _hv(a.sigma_enu), _hv(p.sigma_enu),
                           Solution(a.solution), Solution(p.solution)))
    return rows


def compare(before, after) -> list[Change]:
    return compare_points(before, after) if hasattr(after, 'points') else compare_static(before, after)


def change_cells(change: Change, prefs) -> tuple[str, ...]:
    """Las celdas de un renglón: nombre, ΔE, ΔN, ΔU, σH antes → ahora, σV antes → ahora, solución."""
    def signed(v):
        text = format_length(v, prefs)
        return text if text.startswith('-') else '+'+text

    def pair(i):
        b = '—' if change.sigma_before is None else format_length(change.sigma_before[i], prefs)
        a = '—' if change.sigma_after is None else format_length(change.sigma_after[i], prefs)
        return f'{b} → {a}'
    delta = change.delta
    cells = ['—', '—', '—'] if delta is None else [signed(v) for v in delta]
    before, after = solution_text(change.solution_before), solution_text(change.solution_after)
    solution = after if before == after else f'{before} → {after}'
    return (change.name, *cells, pair(0), pair(1), solution)


COMPARISON_COLUMNS = (('name', 'Elemento'), ('de', 'ΔE'), ('dn', 'ΔN'), ('du', 'ΔU'), ('sh', 'σ H antes → ahora'),
                      ('sv', 'σ V antes → ahora'), ('solution', 'Solución'))


def comparison_view(parent, app, changes: list[Change], prefs, height: int | None = None) -> ttk.Frame:
    """Tabla de la comparación. Cada columna toma el ancho de su contenido (nada se corta): si la ventana es angosta o
    la letra muy grande, la tabla se desplaza a lo ancho."""
    holder = ttk.Frame(parent, style='Flat.Card.TFrame')
    tree = ttk.Treeview(holder, columns=[k for k, _ in COMPARISON_COLUMNS], show='headings', selectmode='none',
                        height=height or max(1, min(len(changes), 12)))
    for key, title in COMPARISON_COLUMNS:
        tree.heading(key, text=title, anchor='w')
    for change in changes:
        cells = change_cells(change, prefs)
        tag = ('changed',) if change.solution_before != change.solution_after else ()
        tree.insert('', 'end', values=cells, tags=tag)
    tokens = app.theme.tokens
    tree.tag_configure('changed', background=tokens['warning_bg'], foreground=tokens['warning_fg'])
    body = tkfont.Font(root=tree, font=app.theme.fonts.body())
    head = tkfont.Font(root=tree, font=app.theme.fonts.strong(-1))
    for index, (key, title) in enumerate(COMPARISON_COLUMNS):
        widths = [text_width(head, title)]+[text_width(body, tree.set(i, key)) for i in tree.get_children()]
        tree.column(key, width=max(widths)+28, minwidth=max(widths)+28, stretch=False, anchor='w')
    bar = ThinScrollbar(holder, tree.xview, orient='horizontal')
    tree.configure(xscrollcommand=bar.set)
    tree.grid(row=0, column=0, sticky='nsew')
    bar.grid(row=1, column=0, sticky='ew', pady=(3, 0))
    if len(changes) > 12:
        vbar = ThinScrollbar(holder, tree.yview)
        tree.configure(yscrollcommand=vbar.set)
        vbar.grid(row=0, column=1, sticky='ns', padx=(3, 0))
    holder.columnconfigure(0, weight=1)
    holder.tree = tree
    return holder


# --- Fila de controles que se envuelve ---------------------------------------------------------------------------------

class Flow(ttk.Frame):
    """Controles en una fila que pasan a la siguiente cuando no caben (ventana angosta o letra muy grande)."""

    def __init__(self, parent, gap=12, style='TFrame'):
        super().__init__(parent, style=style)
        self.gap, self.style_name, self.items, self.rows, self.layout = gap, style, [], [], None
        self.bind('<Configure>', lambda e: self.arrange(e.width), add='+')

    def add(self, widget):
        self.items.append(widget)
        self.arrange(self.winfo_width() if self.winfo_width() > 1 else 10_000)
        return widget

    def arrange(self, width):
        plan, x, row = [], 0, 0
        for widget in self.items:
            need = widget.winfo_reqwidth()
            if x and x+need > width:
                row, x = row+1, 0
            plan.append(row)
            x += need+self.gap
        if plan == self.layout:
            return
        self.layout = plan
        for widget in self.items:
            widget.pack_forget()
        while len(self.rows) <= (plan[-1] if plan else 0):
            frame = ttk.Frame(self, style=self.style_name)
            self.rows.append(frame)
        for index, frame in enumerate(self.rows):
            if plan and index <= plan[-1]:
                frame.pack(fill='x', anchor='w', pady=(0 if index == 0 else 6, 0))
            else:
                frame.pack_forget()
        for widget, r in zip(self.items, plan):
            widget.pack(in_=self.rows[r], side='left', padx=(0, self.gap), anchor='s')
            widget.lift(self.rows[r])


# --- Línea de tiempo ---------------------------------------------------------------------------------------------------

def _sat_key(sat: str):
    return (SYSTEM_ORDER.find(sat[0]) if sat[0] in SYSTEM_ORDER else 9, sat)


def span_of(q: DataQuality | None) -> tuple[datetime, datetime] | None:
    if q is None or not q.tracks:
        return None
    start = min(a for spans in q.tracks.values() for a, _ in spans)
    end = max(b for spans in q.tracks.values() for _, b in spans)
    return (start, end) if end > start else None


def duration_text(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f'{h} h {m:02d} min'
    return f'{m} min {s:02d} s' if s else f'{m} min'


class Timeline(ttk.Frame):
    """Un satélite por fila (agrupados por sistema) con sus tramos con datos, sus saltos de ciclo y la ventana que se
    procesa. Las marcas de inicio y fin se arrastran; un clic en el nombre de un satélite o de un sistema lo quita o lo
    vuelve a poner."""

    def __init__(self, parent, app, on_window, on_toggle):
        super().__init__(parent, style='Flat.Card.TFrame')
        self.app, self.on_window, self.on_toggle = app, on_window, on_toggle
        self.quality: DataQuality | None = None
        self.span: tuple[datetime, datetime] | None = None
        self.window: tuple[datetime, datetime] | None = None
        self.excluded: set[str] = set()
        self.rows: list[tuple[str, str]] = []     # ('system', 'G') o ('sat', 'G13')
        self.drag: str | None = None
        self.head = tk.Canvas(self, highlightthickness=0, borderwidth=0, takefocus=0)
        self.body = tk.Canvas(self, highlightthickness=0, borderwidth=0, takefocus=0)
        self.head.own_palette = self.body.own_palette = True       # los colores los pone draw()
        bar = ThinScrollbar(self, self.body.yview)
        self.body.configure(yscrollcommand=bar.set)
        self.head.grid(row=0, column=0, sticky='ew')
        self.body.grid(row=1, column=0, sticky='nsew')
        bar.grid(row=1, column=1, sticky='ns', padx=(3, 0))
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        for canvas in (self.head, self.body):
            canvas.bind('<Configure>', lambda e: self.draw(), add='+')
            canvas.bind('<ButtonPress-1>', self.press, add='+')
            canvas.bind('<B1-Motion>', self.motion, add='+')
            canvas.bind('<ButtonRelease-1>', self.release, add='+')
            canvas.bind('<Motion>', self.hover, add='+')
        self.body.bind('<MouseWheel>', self.wheel, add='+')
        self.body.bind('<Button-4>', lambda e: self.body.yview_scroll(-1, 'units'), add='+')
        self.body.bind('<Button-5>', lambda e: self.body.yview_scroll(1, 'units'), add='+')
        app.theme.subscribe(self, lambda tokens: self.draw())

    # geometría -------------------------------------------------------------------------------------------------------

    def fonts(self):
        f = self.app.theme.fonts
        return tkfont.Font(root=self, font=f.body(-1)), tkfont.Font(root=self, font=f.strong(-1))

    def metrics(self):
        small, strong = self.fonts()
        line = max(small.metrics('linespace'), strong.metrics('linespace'))
        labels = [self.label_of(kind, key) for kind, key in self.rows] or ['✕ G00']
        label_w = max(max(strong.measure(t) for t in labels), strong.measure('✕ BeiDou · 00'))+20
        return small, strong, line+8, label_w

    def x_range(self):
        _, _, _, label_w = self.metrics()
        width = max(self.body.winfo_width(), 200)
        return label_w+6, max(label_w+60, width-14)

    def to_x(self, moment: datetime) -> float:
        x0, x1 = self.x_range()
        a, b = self.span
        return x0+(x1-x0)*(moment-a).total_seconds()/max(1.0, (b-a).total_seconds())

    def to_time(self, x: float) -> datetime:
        x0, x1 = self.x_range()
        a, b = self.span
        fraction = min(1.0, max(0.0, (x-x0)/max(1.0, x1-x0)))
        moment = a+timedelta(seconds=fraction*(b-a).total_seconds())
        step = max(1.0, (self.quality.interval_s if self.quality and self.quality.interval_s else 1.0))
        # a la época de registro más cercana (los datos van a tiempos enteros del intervalo)
        offset = round((moment-a).total_seconds()/step)*step
        return a+timedelta(seconds=offset)

    def label_of(self, kind: str, key: str) -> str:
        mark = '✕ ' if self.is_excluded(key) else ''
        if kind == 'system':
            n = sum(1 for k, s in self.rows if k == 'sat' and s[0] == key)
            return f'{mark}{sesion.SYSTEM_LETTERS.get(key, key)} · {n}'
        return mark+key

    def is_excluded(self, key: str) -> bool:
        return key in self.excluded or key[:1] in self.excluded

    # datos -----------------------------------------------------------------------------------------------------------

    def set(self, quality: DataQuality | None, span, window, excluded):
        self.quality, self.span = quality, span
        self.window = window
        self.excluded = set(excluded)
        self.rows = []
        if quality is not None:
            current = None
            for sat in sorted(quality.tracks, key=_sat_key):
                if sat[0] != current:
                    current = sat[0]
                    self.rows.append(('system', current))
                self.rows.append(('sat', sat))
        self.draw()

    def set_window(self, window):
        self.window = window
        self.draw()

    def set_excluded(self, excluded):
        self.excluded = set(excluded)
        self.draw()

    # dibujo ----------------------------------------------------------------------------------------------------------

    def draw(self):
        t = self.app.theme.tokens
        if not t:
            return
        small, strong, row_h, label_w = self.metrics()
        line = small.metrics('linespace')
        head_h = 2*line+28
        for canvas in (self.head, self.body):
            canvas.configure(background=t['surface'])
            canvas.delete('all')
        self.head.configure(height=head_h)
        width = max(self.body.winfo_width(), 200)
        if self.span is None or not self.rows:
            self.body.configure(scrollregion=(0, 0, width, 60))
            self.body.create_text(12, 24, anchor='w', fill=t['muted_surface'], font=small,
                                  text='Sin línea de tiempo de estos datos: vuelve a procesar para verla. Puedes '
                                       'escribir la ventana y los satélites de todos modos.', width=max(200, width-24))
            return
        x0, x1 = self.x_range()
        a, b = self.span
        total = max(1.0, (b-a).total_seconds())
        # eje: el paso menor cuyas etiquetas («15:40») no se encimen
        sample = small.measure('00:00:00' if total < 600 else '00:00')+18
        step = next((s for s in TIME_STEPS if (x1-x0)*s/total >= sample), TIME_STEPS[-1])
        first = a.replace(minute=0, second=0, microsecond=0)
        tick = first
        while tick <= b:
            if tick >= a:
                x = self.to_x(tick)
                self.head.create_line(x, head_h-8, x, head_h, fill=t['border'])
                self.head.create_text(x, head_h-20, anchor='s', fill=t['muted_surface'], font=small,
                                      text=tick.strftime('%H:%M:%S' if total < 600 else '%H:%M'))
            tick += timedelta(seconds=step)
        self.head.create_text(6, line//2+4, anchor='nw', fill=t['muted_surface'], font=small,
                              text=f'UTC · {a:%Y-%m-%d}')
        ws, we = self.window or self.span
        xs, xe = self.to_x(ws), self.to_x(we)
        rows_h = row_h*len(self.rows)
        self.body.configure(scrollregion=(0, 0, width, rows_h+4))
        # lo que queda fuera de la ventana, con el fondo secundario
        outside = t['surface_secondary']
        for left, right in ((x0, xs), (xe, x1)):
            if right-left > 0.5:
                self.body.create_rectangle(left, 0, right, rows_h, fill=outside, outline='')
                self.head.create_rectangle(left, head_h-6, right, head_h, fill=outside, outline='')
        # huecos del receptor (todas las filas)
        for g0, g1 in (self.quality.gaps if self.quality else []):
            gx0, gx1 = self.to_x(g0), self.to_x(g1)
            self.body.create_rectangle(gx0, 0, max(gx1, gx0+2), rows_h, fill=t['warning_bg'], outline='')
        colors = {'G': 'chart1', 'R': 'chart2', 'E': 'chart3', 'C': 'chart4', 'J': 'chart5', 'S': 'chart6',
                  'I': 'chart6'}
        muted = t['separator']
        for index, (kind, key) in enumerate(self.rows):
            y = index*row_h
            excluded = self.is_excluded(key)
            text_color = t['muted_surface'] if excluded else t['text']
            if kind == 'system':
                self.body.create_line(0, y+row_h-1, x1, y+row_h-1, fill=t['separator'])
                self.body.create_text(8, y+row_h/2, anchor='w', text=self.label_of(kind, key), fill=text_color,
                                      font=strong, tags=('label', f'row:{index}'))
                continue
            self.body.create_text(18, y+row_h/2, anchor='w', text=self.label_of(kind, key), fill=text_color,
                                  font=small, tags=('label', f'row:{index}'))
            color = muted if excluded else t[colors.get(key[0], 'chart1')]
            top, bottom = y+row_h*0.25, y+row_h*0.75
            for s0, s1 in self.quality.tracks.get(key, []):
                for p0, p1, inside in ((s0, min(s1, ws), False), (max(s0, ws), min(s1, we), True),
                                       (max(s0, we), s1, False)):
                    if p1 < p0 or (p1 == p0 and not inside):
                        continue
                    fx0, fx1 = self.to_x(p0), self.to_x(p1)
                    self.body.create_rectangle(fx0, top, max(fx1, fx0+1.5), bottom, outline='',
                                               fill=color if inside else muted)
            for moment in self.quality.slip_times.get(key, []):
                x = self.to_x(moment)
                self.body.create_line(x, y+2, x, y+row_h-2, fill=t['error_fg'], width=2)
        # marcas de inicio y fin
        for x, name in ((xs, 'start'), (xe, 'end')):
            self.body.create_line(x, 0, x, rows_h, fill=t['primary'], width=2, tags=('handle', name))
            self.head.create_line(x, head_h-14, x, head_h, fill=t['primary'], width=2)
            self.head.create_polygon(x-6, head_h-16, x+6, head_h-16, x, head_h-6, fill=t['primary'], outline='')
        self.head.create_line(0, head_h-1, width, head_h-1, fill=t['border'])

    # interacción -----------------------------------------------------------------------------------------------------

    def nearest_handle(self, x) -> str | None:
        if self.span is None:
            return None
        ws, we = self.window or self.span
        ds, de = abs(x-self.to_x(ws)), abs(x-self.to_x(we))
        if min(ds, de) > HANDLE_PX:
            return None
        return 'start' if ds <= de else 'end'

    def press(self, event):
        if self.span is None:
            return
        canvas = event.widget
        x = canvas.canvasx(event.x)
        x0, _ = self.x_range()
        self.drag = self.nearest_handle(x)
        if self.drag is not None:
            return
        if canvas is self.body and x < x0:
            _, _, row_h, _ = self.metrics()
            index = int(canvas.canvasy(event.y)//row_h)
            if 0 <= index < len(self.rows):
                self.on_toggle(self.rows[index][1])
            return
        if canvas is self.head and x >= x0:
            # un clic en el eje lleva ahí la marca más cercana
            ws, we = self.window or self.span
            moment = self.to_time(x)
            self.drag = 'start' if abs((moment-ws).total_seconds()) <= abs((moment-we).total_seconds()) else 'end'
            self.motion(event)

    def motion(self, event):
        if self.drag is None or self.span is None:
            return
        moment = self.to_time(event.widget.canvasx(event.x))
        ws, we = self.window or self.span
        minimum = timedelta(seconds=sesion.MIN_WINDOW_S)
        if self.drag == 'start':
            ws = min(moment, we-minimum)
        else:
            we = max(moment, ws+minimum)
        ws, we = max(ws, self.span[0]), min(we, self.span[1])
        self.window = (ws, we)
        self.draw()
        self.on_window(ws, we)

    def release(self, event):
        self.drag = None

    def hover(self, event):
        x = event.widget.canvasx(event.x)
        x0, _ = self.x_range()
        if self.nearest_handle(x) is not None or (event.widget is self.head and x >= x0):
            cursor = 'sb_h_double_arrow'
        elif event.widget is self.body and x < x0:
            cursor = 'hand2'
        else:
            cursor = ''
        if str(event.widget.cget('cursor')) != cursor:
            event.widget.configure(cursor=cursor)

    def wheel(self, event):
        delta = event.delta
        if abs(delta) >= 120:
            delta //= 120
        self.body.yview_scroll(-int(delta) or (-1 if event.delta > 0 else 1), 'units')


# --- La ventana del editor ---------------------------------------------------------------------------------------------

def parse_time(text: str, reference: datetime | None) -> datetime | None:
    """«2026-10-24 15:10:00» o «15:10» (UTC). Solo la hora: el día de la sesión que la contiene (o el del inicio)."""
    text = text.strip().replace('UTC', '').strip()
    if not text:
        return None
    for fmt in TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt.startswith('%H'):
            if reference is None:
                raise UserError('Escribe la fecha completa: AAAA-MM-DD HH:MM:SS.')
            parsed = datetime.combine(reference.date(), parsed.time())
        return parsed.replace(tzinfo=UTC)
    raise UserError(f'No se entiende la hora «{text}»: escribe AAAA-MM-DD HH:MM:SS (UTC) o solo HH:MM:SS.')


def time_text(moment: datetime | None) -> str:
    return '' if moment is None else moment.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')


class SessionEditor(tk.Toplevel):
    """La pantalla «Sesión» de un resultado: una línea de tiempo por receptor (rover y cada base) y una sola edición
    (la ventana y los satélites fuera valen para todas las líneas base)."""

    def __init__(self, page):
        app = page.app
        super().__init__(app)
        self.page, self.app = page, app
        self.title(TITLE)
        attach_dialog(self, app)
        result = page.output['result']
        options = page.job.options
        self.receivers = [('Rover', result.rover_quality)]
        bases = result.base_quality if isinstance(result.base_quality, dict) else (
            {result.base.id: result.base_quality} if getattr(result, 'base_quality', None) is not None else {})
        self.receivers += [(f'Base {sid}', q) for sid, q in bases.items()]
        self.span = span_of(result.rover_quality) or (
            (result.rover.start, result.rover.end) if result.rover.start and result.rover.end else None)
        self.excluded = set(sesion.normalize_excluded(options.excluded_sats))
        self.systems_in_data = sorted({s[0] for s in (result.rover_quality.tracks or {})} |
                                      {s[0] for s in result.rover_quality.satellites}, key=SYSTEM_ORDER.find)
        self.constellations = tuple(options.constellations)

        scale = getattr(app, 'ui_scale', 1)
        width = min(int(1080*scale), self.winfo_screenwidth()-60)
        height = min(int(760*scale), self.winfo_screenheight()-80)
        self.geometry(f'{width}x{height}')
        self.minsize(min(int(520*scale), width), min(int(420*scale), height))
        actions = ttk.Frame(self, padding=(18, 10, 18, 16))
        actions.pack(side='bottom', fill='x')
        # Arriba, los controles (y la comparación) en un área desplazable que no pasa de casi la mitad de la ventana;
        # abajo, la línea de tiempo con el resto: con letra muy grande en una ventana chica las dos siguen a la vista.
        self.top_scroll = ScrollFrame(self, padding=(18, 16, 8, 4))
        self.top_scroll.pack(side='top', fill='x')
        top = self.top_scroll.body
        ttk.Label(top, text=TITLE, style='Section.TLabel').pack(anchor='w')
        wrap_label(top, 'Recorta el inicio y el fin arrastrando las marcas azules o escribiéndolos, y quita un '
                        'satélite o un sistema con un clic en su nombre o escribiéndolo. Vale para todas las líneas '
                        'base; al volver a procesar se compara con el resultado anterior.', margin=10).pack(
            fill='x', pady=(2, 10))

        controls = Flow(top)
        controls.pack(fill='x')
        self.receiver_var = tk.StringVar(value=self.receivers[0][0])
        box = ttk.Frame(controls)
        ttk.Label(box, text='Receptor', style='Strong.TLabel').pack(anchor='w')
        self.receiver_box = ttk.Combobox(box, textvariable=self.receiver_var, state='readonly',
                                         values=[name for name, _ in self.receivers],
                                         width=max(10, max(len(n) for n, _ in self.receivers)+2))
        self.receiver_box.pack(anchor='w', pady=(4, 0))
        self.receiver_box.bind('<<ComboboxSelected>>', lambda e: self.show_receiver())
        controls.add(box)
        window = options.time_window
        self.start_var = tk.StringVar(value=time_text(window.start) if window else '')
        self.end_var = tk.StringVar(value=time_text(window.end) if window else '')
        self.start_entry = self._time_field(controls, 'Inicio (UTC)', self.start_var)
        self.end_entry = self._time_field(controls, 'Fin (UTC)', self.end_var)
        whole = ttk.Button(controls, text='Toda la sesión', command=self.whole_session)
        controls.add(whole)

        systems = Flow(top)
        systems.pack(fill='x', pady=(10, 0))
        systems.add(ttk.Label(systems, text='Sistemas', style='Strong.TLabel'))
        self.system_vars: dict[str, tk.BooleanVar] = {}
        self.system_checks: dict[str, ttk.Checkbutton] = {}
        for letter in self.systems_in_data:
            name = sesion.SYSTEM_LETTERS.get(letter, letter)
            n = sum(1 for s in result.rover_quality.satellites if s[0] == letter)
            var = tk.BooleanVar(value=letter not in self.excluded)
            self.system_vars[letter] = var
            check = ttk.Checkbutton(systems, text=f'{name} ({n})', variable=var,
                                    command=lambda l=letter: self.system_toggled(l))
            if name not in self.constellations:
                var.set(False)
                check.state(['disabled'])
                Tooltip(check, f'{name} no está entre las constelaciones de las opciones del proceso.', app.theme)
            self.system_checks[letter] = check
            systems.add(check)

        sats = ttk.Frame(top)
        sats.pack(fill='x', pady=(10, 0))
        ttk.Label(sats, text='Satélites fuera', style='Strong.TLabel').pack(anchor='w')
        self.sats_var = tk.StringVar(value=' '.join(s for s in sorted(self.excluded, key=_sat_key) if len(s) > 1))
        self.sats_entry = ttk.Entry(sats, textvariable=self.sats_var)
        self.sats_entry.pack(fill='x', pady=(4, 0))
        self.sats_var.trace_add('write', lambda *a: self.sats_typed())
        wrap_label(sats, 'PRN separados por espacio o coma, como G13 R05 E11.', margin=10).pack(fill='x', pady=(4, 0))

        self.summary = wrap_label(top, '', style='Strong.TLabel', margin=10)
        self.summary.pack(fill='x', pady=(10, 0))
        self.problem = wrap_label(top, '', style='error.TLabel', margin=10)

        comparison = page.session_changes()
        if comparison:
            frame, inner = card(top, 'Comparación con el proceso anterior', 'Ahora − antes, en el lugar del '
                                'resultado anterior.', padding=12)
            frame.pack(fill='x', pady=(10, 0))
            comparison_view(inner, app, comparison, app.settings.values, height=min(len(comparison), 4)).pack(
                fill='x')
        middle = ttk.Frame(self, padding=(18, 8, 18, 0))
        middle.pack(side='top', fill='both', expand=True)
        legend = wrap_label(middle, 'Barra: datos del satélite · gris: fuera de la ventana o quitado · rojo: salto de '
                                    'ciclo · amarillo: hueco del receptor.', margin=10)
        legend.pack(side='bottom', fill='x', pady=(6, 0))
        self.timeline = Timeline(middle, app, self.dragged, self.toggle)
        self.timeline.pack(fill='both', expand=True)
        self.bind('<Configure>', self.fit_top, add='+')
        top.bind('<Configure>', self.fit_top, add='+')

        self.reprocess_button = ttk.Button(actions, text='Volver a procesar', style='Primary.TButton',
                                           command=self.reprocess)
        app.icons.bind(self.reprocess_button, 'refresh-cw')
        self.reprocess_button.pack(side='right')
        ttk.Button(actions, text='Cerrar', command=self.destroy).pack(side='right', padx=(0, 8))
        app.theme.walk(self)
        self.bind('<Escape>', lambda e: self.destroy())
        self.show_receiver()
        self.window_typed()

    def fit_top(self, event=None):
        """Los controles ocupan lo que piden, hasta el 40 % del alto de la ventana (el resto, desplazándose)."""
        if event is not None and event.widget not in (self, self.top_scroll.body):
            return
        need = self.top_scroll.body.winfo_reqheight()
        height = min(need, max(140, int(self.winfo_height()*0.4)))
        if getattr(self, '_top_height', None) != height:
            self._top_height = height
            self.top_scroll.canvas.configure(height=height)

    def _time_field(self, flow, label, var):
        box = ttk.Frame(flow)
        ttk.Label(box, text=label, style='Strong.TLabel').pack(anchor='w')
        entry = ttk.Entry(box, textvariable=var, width=20)
        entry.pack(anchor='w', pady=(4, 0))
        var.trace_add('write', lambda *a: self.window_typed())
        flow.add(box)
        return entry

    # estado ----------------------------------------------------------------------------------------------------------

    def quality(self) -> DataQuality | None:
        return dict(self.receivers).get(self.receiver_var.get())

    def shown_excluded(self) -> set[str]:
        """Lo que la línea de tiempo pinta fuera: lo quitado aquí y los sistemas que el proceso no usa."""
        unused = {letter for letter in self.systems_in_data
                  if sesion.SYSTEM_LETTERS.get(letter) not in self.constellations}
        return set(self.excluded) | unused

    def show_receiver(self):
        self.timeline.set(self.quality(), self.span, self.window_or_none(), self.shown_excluded())

    def window_or_none(self):
        try:
            return self.read_window(quiet=True)
        except UserError:
            return None

    def read_window(self, quiet=False) -> tuple[datetime, datetime] | None:
        """(inicio, fin) de los campos; vacíos = los extremos de la sesión. None si es toda la sesión."""
        reference = self.span[0] if self.span else None
        start = parse_time(self.start_var.get(), reference)
        end = parse_time(self.end_var.get(), self.span[1] if self.span else reference)
        if start is None and end is None:
            return None
        if self.span is not None:
            start = start or self.span[0]
            end = end or self.span[1]
        elif start is None or end is None:
            return (start, end)
        if end <= start:
            raise UserError('El fin de la ventana tiene que ser después del inicio.')
        if (end-start).total_seconds() < sesion.MIN_WINDOW_S:
            raise UserError('La ventana tiene que durar al menos un minuto.')
        if self.span is not None and (end <= self.span[0] or start >= self.span[1]):
            raise UserError(f'La ventana queda fuera de los datos ({time_text(self.span[0])} a '
                            f'{time_text(self.span[1])} UTC).')
        return start, end

    def window_typed(self):
        if getattr(self, '_dragging', False):
            return
        try:
            window = self.read_window()
            self.problem.pack_forget()
        except UserError as error:
            self.problem.configure(text=str(error))
            self.problem.pack(fill='x', pady=(6, 0), after=self.summary)
            window = None
        self.timeline.set_window(window if window and None not in window else None)
        self.update_summary()

    def dragged(self, start, end):
        self._dragging = True
        try:
            self.start_var.set(time_text(start))
            self.end_var.set(time_text(end))
        finally:
            self._dragging = False
        self.problem.pack_forget()
        self.update_summary()

    def whole_session(self):
        self.start_var.set('')
        self.end_var.set('')

    def toggle(self, key: str):
        """Clic en la línea de tiempo: un satélite o un sistema entra o sale."""
        if len(key) == 1:
            var = self.system_vars.get(key)
            if var is None or self.system_checks[key].instate(['disabled']):
                return
            var.set(not var.get())
            self.system_toggled(key)
            return
        if key[0] in self.excluded:
            return                      # su sistema entero ya está fuera
        sats = set(self._typed_sats())
        sats ^= {key}
        self.sats_var.set(' '.join(sorted(sats, key=_sat_key)))

    def system_toggled(self, letter):
        if self.system_vars[letter].get():
            self.excluded.discard(letter)
        else:
            self.excluded.add(letter)
        self.sats_typed()

    def _typed_sats(self) -> list[str]:
        try:
            return [s for s in sesion.normalize_excluded(self.sats_var.get()) if len(s) > 1]
        except UserError:
            return []

    def sats_typed(self):
        systems = {s for s in self.excluded if len(s) == 1}
        try:
            typed = sesion.normalize_excluded(self.sats_var.get())
            self.problem.pack_forget()
        except UserError as error:
            self.problem.configure(text=str(error))
            self.problem.pack(fill='x', pady=(6, 0), after=self.summary)
            typed = []
        systems |= {s for s in typed if len(s) == 1}
        for letter in systems:
            if letter in self.system_vars:
                self.system_vars[letter].set(False)
        self.excluded = systems | {s for s in typed if len(s) > 1}
        self.timeline.set_excluded(self.shown_excluded())
        self.update_summary()

    def excluded_list(self) -> list[str]:
        """Lo que se deja fuera, normalizado (sin los sistemas que el proceso ya no usa)."""
        return sesion.normalize_excluded(self.excluded)

    def update_summary(self):
        try:
            window = self.read_window(quiet=True)
        except UserError:
            window = None
        if window is None or self.span is None:
            when = 'Toda la sesión'
            if self.span is not None:
                when += f' ({time_text(self.span[0])} a {self.span[1]:%H:%M:%S} UTC, ' \
                        f'{duration_text((self.span[1]-self.span[0]).total_seconds())})'
        else:
            start, end = window
            when = (f'De {time_text(start)} a {end:%H:%M:%S} UTC: {duration_text((end-start).total_seconds())}'
                    + (f' de {duration_text((self.span[1]-self.span[0]).total_seconds())}' if self.span else ''))
        excluded = sesion.excluded_text(self.excluded_list())
        self.summary.configure(text=f'{when} · fuera: {excluded}')

    # proceso ---------------------------------------------------------------------------------------------------------

    def new_window(self) -> TimeWindow | None:
        window = self.read_window()
        if window is None:
            return None
        start, end = window
        if self.span is not None:
            # una marca en el extremo de los datos es «desde el inicio» o «hasta el fin»
            start = None if start is not None and start <= self.span[0] else start
            end = None if end is not None and end >= self.span[1] else end
        return None if start is None and end is None else TimeWindow(start, end)

    def reprocess(self):
        try:
            window = self.new_window()
            sesion.normalize_excluded(self.sats_var.get())
            excluded = self.excluded_list()
            from dataclasses import replace
            sesion.constellations(replace(self.page.job.options, excluded_sats=excluded))
        except UserError as error:
            self.problem.configure(text=str(error))
            self.problem.pack(fill='x', pady=(6, 0), after=self.summary)
            return
        if self.page.session_reprocess(window, excluded):
            self.destroy()


# --- Lo que comparten las páginas del estático y del PPK ---------------------------------------------------------------

class SessionActions:
    """Tarjeta «Sesión» del resultado, la pantalla del editor y la comparación con el proceso anterior."""

    def session_changes(self) -> list[Change]:
        """La comparación del resultado en pantalla con el anterior, si se volvió a procesar desde el editor."""
        record = getattr(self, '_session_comparison', None)
        if record is None or self.output is None or record[0] is not self.output['result']:
            return []
        return record[1]

    def session_after_process(self, output):
        previous = getattr(self, '_session_previous', None)
        self._session_previous = None
        self._session_comparison = None
        if previous is not None:
            self._session_comparison = (output['result'], compare(previous, output['result']))

    def session_reprocess(self, window: TimeWindow | None, excluded: list[str]) -> bool:
        """Aplica la edición a las opciones del proyecto y vuelve a procesar. False si no cambió nada."""
        options = self.job.options
        if window == options.time_window and excluded == sesion.normalize_excluded(options.excluded_sats):
            self.app.set_status('La sesión no cambió: no hace falta volver a procesar.')
            return False
        if self.busy():
            return False
        previous = self.output['result'] if self.output is not None else None
        options.time_window = window
        options.excluded_sats = list(excluded)
        self.changed()
        self._session_previous = previous
        self.process()
        return True

    def open_session(self):
        if self.output is None:
            return
        editor = getattr(self, '_session_editor', None)
        if editor is not None and editor.winfo_exists():
            editor.lift()
            return
        self._session_editor = SessionEditor(self)

    def session_card(self, body, output):
        """Lo que se procesó (la ventana y lo que quedó fuera), el botón del editor y la comparación."""
        result = output['result']
        window = getattr(result, 'time_window', None)
        excluded = getattr(result, 'excluded_sats', []) or []
        text = (('Ventana: ' + sesion.window_text(window)) if sesion.is_trimmed(window) else 'Toda la sesión') + \
            ' · fuera: ' + sesion.excluded_text(excluded)
        frame, inner = card(body, 'Sesión', text)
        frame.pack(fill='x', pady=(16, 0))
        button = ttk.Button(inner, text='Editar sesión…', command=self.open_session)
        self.app.icons.bind(button, 'clock')
        button.pack(anchor='w')
        Tooltip(button, 'Línea de tiempo por satélite: recortar el tiempo, quitar satélites y volver a procesar.',
                self.app.theme)
        self.session_button = button
        changes = self.session_changes()
        if changes:
            wrap_label(inner, 'Comparación con el proceso anterior (ahora − antes):', style='Card.Strong.TLabel',
                       margin=40).pack(fill='x', pady=(12, 6))
            self.session_table = comparison_view(inner, self.app, changes, self.app.settings.values)
            self.session_table.pack(fill='x')
            moved = [c for c in changes if c.solution_before != c.solution_after]
            if moved:
                wrap_label(inner, plural(len(moved), 'elemento cambió', 'elementos cambiaron') + ' de solución.',
                           style='warning.TLabel', margin=40).pack(fill='x', pady=(6, 0))
        return frame
