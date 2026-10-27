"""Gráficas del informe con matplotlib (sin interfaz: backend Agg), en PNG a 200 ppp con la letra del informe.

Cada función devuelve los bytes de la imagen y nunca depende solo del color: las series llevan leyenda y los estados
de la solución, su nombre.
"""
from __future__ import annotations

import io
import math
from collections import defaultdict
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from . import estilo  # noqa: E402
from .rtklib_salida import PosFile, StatFile  # noqa: E402

DPI = 200
SYSTEMS = {"G": "GPS", "R": "GLONASS", "E": "Galileo", "C": "BeiDou", "J": "QZSS", "S": "SBAS", "I": "NavIC"}
SYSTEM_COLORS = {"G": estilo.CHART[0], "R": estilo.CHART[4], "E": estilo.CHART[1], "C": estilo.CHART[3],
                 "J": estilo.CHART[2], "S": estilo.CHART[5], "I": "#59636C"}
Q_STYLE = {1: ("Fija", estilo.SOLUTION["fixed"]), 2: ("Flotante", estilo.SOLUTION["float"]),
           4: ("DGPS", estilo.SOLUTION["single"]), 5: ("Autónoma", estilo.SOLUTION["single"]),
           3: ("SBAS", estilo.SOLUTION["single"]), 6: ("PPP", estilo.SOLUTION["none"])}

_ready = False


def _setup() -> None:
    global _ready
    if _ready:
        return
    f = estilo.fonts()
    family = []
    for path in (f.regular_path, f.bold_path):
        try:
            font_manager.fontManager.addfont(path)
        except (OSError, RuntimeError):
            continue
    try:
        family = [font_manager.FontProperties(fname=f.regular_path).get_name()]
    except (OSError, RuntimeError):
        family = []
    matplotlib.rcParams.update({
        "font.family": family + ["DejaVu Sans"], "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8,
        "axes.edgecolor": estilo.BORDER, "axes.labelcolor": estilo.TEXT, "xtick.color": estilo.TEXT,
        "ytick.color": estilo.TEXT, "text.color": estilo.TEXT, "axes.titlecolor": estilo.PRIMARY,
        "axes.grid": True, "grid.color": "#E3E7EC", "grid.linewidth": 0.6, "legend.fontsize": 7,
        "legend.frameon": False, "axes.spines.top": False, "axes.spines.right": False,
        "savefig.facecolor": "white", "axes.unicode_minus": True,
    })
    _ready = True


def _png(fig) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return buffer.getvalue()


def _legend_top(ax, ncol: int, handles=None, labels=None, markerscale: float = 1.0) -> None:
    """La leyenda encima del área de datos (bajo el título), para que nunca tape una serie."""
    kwargs = dict(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=ncol, markerscale=markerscale,
                  borderaxespad=0.3, handlelength=1.6, columnspacing=1.2)
    if handles is not None:
        ax.legend(handles, labels, **kwargs)
    else:
        ax.legend(**kwargs)


def _time_axis(ax) -> None:
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_xlabel("Hora (UTC)")


def _naive(t: datetime) -> datetime:
    return t.replace(tzinfo=None)


def sky_plot(stat: StatFile, mask_deg: float, title: str = "Cielo: trayectoria de los satélites") -> bytes:
    _setup()
    tracks: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in stat.satellites:
        if r.frequency == 1 and r.elevation > 0:
            tracks[r.sat].append((math.radians(r.azimuth), 90.0 - r.elevation))
    fig = plt.figure(figsize=(4.6, 4.6))
    ax = fig.add_subplot(projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_rlim(0, 90)
    ax.set_rticks([30, 60, 90 - mask_deg])
    ax.set_yticklabels(["60°", "30°", f"{mask_deg:g}°"], fontsize=6, color=estilo.MUTED)
    ax.set_rlabel_position(202.5)
    ax.set_xticks([math.radians(a) for a in range(0, 360, 45)])
    ax.set_xticklabels(["N", "45°", "E", "135°", "S", "225°", "O", "315°"])
    theta = [i * 2 * math.pi / 360 for i in range(361)]
    ax.fill_between(theta, [90 - mask_deg] * len(theta), [90] * len(theta), color="#EEF0F3", zorder=0,
                    label=f"Bajo la máscara ({mask_deg:g}°)")
    seen = set()
    for sat, pts in sorted(tracks.items()):
        s = sat[0]
        az, zen = zip(*pts)
        label = SYSTEMS.get(s, s) if s not in seen else None
        seen.add(s)
        ax.plot(az, zen, ".", ms=1.6, color=SYSTEM_COLORS.get(s, "#59636C"), label=label)
        ax.annotate(sat, (az[-1], zen[-1]), fontsize=5.5, color=estilo.TEXT)
    ax.set_title(title, pad=14)
    ax.legend(loc="center left", bbox_to_anchor=(1.08, 0.5), markerscale=5)
    return _png(fig)


def satellites_time(stat: StatFile, title: str = "Satélites usados en el tiempo") -> bytes:
    _setup()
    rows: dict[str, list[datetime]] = defaultdict(list)
    slips: dict[str, list[datetime]] = defaultdict(list)
    for r in stat.satellites:
        if r.frequency != 1:
            continue
        if r.used:
            rows[r.sat].append(_naive(r.time))
        if r.slip:
            slips[r.sat].append(_naive(r.time))
    sats = sorted(rows, key=lambda s: ("GRECJSI".find(s[0]) if s[0] in "GRECJSI" else 9, s))
    times = sorted({t for v in rows.values() for t in v})
    step = min((b - a).total_seconds() for a, b in zip(times, times[1:])) if len(times) > 1 else 1.0
    fig, ax = plt.subplots(figsize=(7.2, max(2.0, 0.17 * len(sats) + 0.9)))
    for i, sat in enumerate(sats):
        color = SYSTEM_COLORS.get(sat[0], "#59636C")
        ts = sorted(rows[sat])
        start = prev = ts[0]
        for t in ts[1:] + [None]:
            if t is None or (t - prev).total_seconds() > 1.5 * step:
                ax.plot([start, prev], [i, i], "-", lw=3.2, color=color, solid_capstyle="butt")
                if t is not None:
                    start = t
            prev = t if t is not None else prev
        if slips.get(sat):
            ax.plot(slips[sat], [i] * len(slips[sat]), "|", color="#9E182C", ms=7, mew=1.2)
    ax.set_yticks(range(len(sats)))
    ax.set_yticklabels(sats, fontsize=6)
    ax.set_ylim(-0.8, len(sats) - 0.2)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    _time_axis(ax)
    handles = [plt.Line2D([], [], color=SYSTEM_COLORS[s], lw=3, label=SYSTEMS[s])
               for s in "GRECJSI" if any(x[0] == s for x in sats)]
    if any(slips.values()):
        handles.append(plt.Line2D([], [], color="#9E182C", marker="|", ls="", ms=7, label="Salto de ciclo"))
    _legend_top(ax, len(handles), handles, [h.get_label() for h in handles])
    ax.set_title(title, pad=22)
    return _png(fig)


def residuals(stat: StatFile, title: str = "Residuos de fase por satélite (L1)") -> bytes:
    _setup()
    by_sat: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for r in stat.satellites:
        if r.frequency == 1 and r.used and r.residual_phase != 0.0:
            by_sat[r.sat].append((_naive(r.time), r.residual_phase * 1000.0))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.2, 4.6), gridspec_kw={"height_ratios": [3, 2]})
    seen = set()
    for sat in sorted(by_sat):
        t, v = zip(*by_sat[sat])
        s = sat[0]
        ax1.plot(t, v, ".", ms=1.4, color=SYSTEM_COLORS.get(s, "#59636C"),
                 label=SYSTEMS.get(s, s) if s not in seen else None)
        seen.add(s)
    ax1.axhline(0, color=estilo.MUTED, lw=0.6)
    ax1.set_ylabel("Residuo (mm)")
    _time_axis(ax1)
    _legend_top(ax1, 4, markerscale=5)
    ax1.set_title(title, pad=20)
    sats = sorted(by_sat, key=lambda s: ("GRECJSI".find(s[0]) if s[0] in "GRECJSI" else 9, s))
    rms = [math.sqrt(sum(v * v for _, v in by_sat[s]) / len(by_sat[s])) for s in sats]
    ax2.bar(range(len(sats)), rms, color=[SYSTEM_COLORS.get(s[0], "#59636C") for s in sats])
    ax2.set_xticks(range(len(sats)))
    ax2.set_xticklabels(sats, rotation=90, fontsize=5.5)
    ax2.set_ylabel("RMS (mm)")
    ax2.grid(axis="x", visible=False)
    fig.tight_layout()
    return _png(fig)


def ratio_state(pos: PosFile, min_ratio: float, title: str = "Estado de la solución y cociente (ratio)") -> bytes:
    _setup()
    t = [_naive(e.time) for e in pos.epochs]
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(7.2, 4.6), sharex=True,
                                        gridspec_kw={"height_ratios": [1, 2.2, 1.4]})
    # El estado como banda de tramos: cada tramo del color de su solución, con su nombre en la leyenda.
    step = min(((b - a).total_seconds() for a, b in zip(t, t[1:]) if b > a), default=1.0)
    spans: dict[int, list[tuple[float, float]]] = defaultdict(list)
    start = 0
    for i in range(1, len(t) + 1):
        if i == len(t) or pos.epochs[i].q != pos.epochs[start].q or (t[i] - t[i - 1]).total_seconds() > 1.5 * step:
            x0 = mdates.date2num(t[start])
            x1 = mdates.date2num(t[i - 1]) + step / 86400
            spans[pos.epochs[start].q].append((x0, x1 - x0))
            start = i
    handles = []
    for q, (name, color) in Q_STYLE.items():
        if spans.get(q):
            ax1.broken_barh(spans[q], (0, 1), color=color)
            n = sum(1 for e in pos.epochs if e.q == q)
            handles.append(plt.Rectangle((0, 0), 1, 1, color=color, label=f"{name}: {n} épocas"))
    ax1.set_yticks([])
    ax1.set_ylim(0, 1)
    ax1.grid(False)
    ax1.set_ylabel("Estado")
    _legend_top(ax1, max(len(handles), 1), handles, [h.get_label() for h in handles])
    ax1.set_title(title, pad=22)
    ratios = [(ti, e.ratio) for ti, e in zip(t, pos.epochs) if e.ratio and e.ratio > 0]
    if ratios:
        rt, rv = zip(*ratios)
        ax2.semilogy(rt, rv, ".", ms=1.6, color=estilo.CHART[0], label="Cociente")
    ax2.axhline(min_ratio, color="#9E182C", lw=0.9, ls="--", label=f"Mínimo para fijar ({min_ratio:g})")
    ax2.set_ylabel("Cociente")
    ax2.legend(loc="lower right", frameon=True, facecolor="white", edgecolor="none", framealpha=0.85)
    ax3.plot(t, [e.ns for e in pos.epochs], "-", lw=0.9, color=estilo.CHART[1])
    ax3.set_ylabel("Satélites")
    _time_axis(ax3)
    fig.tight_layout()
    return _png(fig)


def dop(series, title: str = "Dilución de la precisión (DOP) de la solución") -> bytes:
    _setup()
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    if series:
        t = [_naive(s[0]) for s in series]
        for i, (name, idx) in enumerate((("PDOP", 1), ("HDOP", 2), ("VDOP", 3))):
            ax.plot(t, [s[idx] for s in series], "-", lw=0.9, color=estilo.CHART[i], label=name)
    ax.set_ylabel("DOP")
    ax.set_ylim(bottom=0)
    _time_axis(ax)
    _legend_top(ax, 3)
    ax.set_title(title, pad=20)
    return _png(fig)


def trajectory(track: list[tuple[float, float, int]], points: list[tuple[str, float, float, str]],
               base: tuple[str, float, float] | None, axis_names: tuple[str, str],
               title: str = "Trayectoria y puntos") -> bytes:
    """`track`: (este, norte, Q) por época; `points`: (nombre, este, norte, solución); en el sistema elegido."""
    _setup()
    fig, ax = plt.subplots(figsize=(7.2, 5.4))
    for q, (name, color) in Q_STYLE.items():
        pts = [(e, n) for e, n, qq in track if qq == q]
        if pts:
            e, n = zip(*pts)
            ax.plot(e, n, ".", ms=0.9, color=color, label=f"Trayectoria {name.lower()}")
    marker = {"fixed": ("o", estilo.SOLUTION["fixed"]), "float": ("s", estilo.SOLUTION["float"]),
              "single": ("^", estilo.SOLUTION["single"]), "none": ("x", estilo.SOLUTION["none"])}
    for name, e, n, sol in points:
        m, c = marker.get(sol, ("o", estilo.TEXT))
        ax.plot([e], [n], m, ms=4.5, color=c, mec="white", mew=0.5, zorder=3)
        ax.annotate(name, (e, n), xytext=(3, 3), textcoords="offset points", fontsize=6, color=estilo.TEXT)
    if base:
        ax.plot([base[1]], [base[2]], "*", ms=9, color=estilo.PRIMARY, zorder=4, label=f"Base {base[0]}")
    ax.set_xlabel(axis_names[0])
    ax.set_ylabel(axis_names[1])
    ax.set_aspect("equal", adjustable="datalim")
    ax.ticklabel_format(useOffset=False, style="plain")
    for label in ax.get_xticklabels():
        label.set_rotation(30)
    handles, labels = ax.get_legend_handles_labels()
    for sol, text in (("fixed", "Punto fijo"), ("float", "Punto flotante"), ("none", "Punto sin solución")):
        if any(p[3] == sol for p in points):
            m, c = marker[sol]
            handles.append(plt.Line2D([], [], marker=m, color=c, ls="", ms=5))
            labels.append(text)
    ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3, markerscale=1.5)
    ax.set_title(title)
    return _png(fig)
