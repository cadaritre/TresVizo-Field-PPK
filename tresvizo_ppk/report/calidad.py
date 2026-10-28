"""Secciones del control de calidad en los informes: el resumen del proyecto contra sus tolerancias, el estado de cada
punto (y de cada línea base en el estático), las ocupaciones repetidas y la sección «Control».

La evaluación sale siempre del proyecto (sus tolerancias, su control y la ocupación elegida de cada nombre), como en
la pantalla, para que el informe diga lo mismo que la tabla de resultados.
"""
from __future__ import annotations

import math

from .. import calidad
from ..model import PointsResult, Project, QualityReport, QualityStatus, StaticResult
from . import estilo, secciones
from .documento import CONTENT_WIDTH, Report
from .estilo import num, signed

LEVEL_BG = {"pass": estilo.SEMANTIC["success_bg"], "review": estilo.SEMANTIC["warning_bg"],
            "fail": estilo.SEMANTIC["error_bg"]}
LEVEL_KIND = {"pass": "success", "review": "warning", "fail": "error"}


def points_quality(result: PointsResult, project: Project) -> QualityReport:
    return calidad.evaluate_points(result, project.tolerances or calidad.default_tolerances("points"),
                                   project.control_points, project.occupation_choices, project.coordinates,
                                   project.control_source)


def static_quality(result: StaticResult, project: Project) -> QualityReport:
    return calidad.evaluate_static(result, project.tolerances or calidad.default_tolerances("static"),
                                   project.control_points, project.coordinates, project.control_source)


def _symbol(level: str) -> str:
    """El símbolo del estado si la letra del informe lo trae (si no, la palabra sola)."""
    symbol = calidad.PDF_SYMBOL.get(level, "")
    return f"{symbol} " if symbol and estilo.has_glyph(symbol) else ""


def level_text(status: QualityStatus) -> str:
    return _symbol(status.level) + calidad.LEVEL_TEXT[status.level]


def counts_text(levels: list[str]) -> str:
    n = {lv: levels.count(lv) for lv in calidad.LEVELS}
    return f"{n['pass']} pasan · {n['review']} a revisar · {n['fail']} fallan"


def tolerances_text(q: QualityReport) -> str:
    t = q.tolerances
    return f"orden de {t.order} (NT EEP, tabla 1)" if t.order else "escritas a mano"


def summary_fields(q: QualityReport) -> list[tuple[str, str]]:
    return [("Tolerancias", tolerances_text(q)),
            ("Estado de los puntos", counts_text([p.status.level for p in q.points]))]


def summary_callout(r: Report, q: QualityReport, title_all: str = "Todos los puntos dentro de tolerancia") -> None:
    bad = [p for p in q.points if p.status.level != "pass"]
    bad_lines = [f"{p.name}: {calidad.LEVEL_TEXT[p.status.level].lower()} — {p.status.reason}" for p in bad]
    if q.baselines:
        bad_lines += [f"Línea base {b.base_id}: {calidad.LEVEL_TEXT[b.status.level].lower()} — {b.status.reason}"
                      for b in q.baselines if b.status.level != "pass"]
    if not bad_lines:
        r.callout("success", title_all, [f"Tolerancias del {tolerances_text(q)}."])
        return
    title = (f"Control de calidad: {calidad.LEVEL_TEXT[q.level].lower()}" if q.level != "pass" else
             "Control de calidad")
    r.callout(LEVEL_KIND[q.level], title, bad_lines[:12] + ([f"… y {len(bad_lines) - 12} más (sección «Control de "
                                                               "calidad»)."] if len(bad_lines) > 12 else []))


def _tolerance_rows(q: QualityReport, kind: str) -> list[tuple[str, str]]:
    t = q.tolerances

    def m(v):
        return "no se evalúa" if v is None else num(v, 3, "m")

    rows = [("σH máxima (1σ)", m(t.max_sigma_h_m)), ("σV máxima (1σ)", m(t.max_sigma_v_m))]
    if kind == "points":
        rows.append(("Épocas fijas mínimas", "no se evalúa" if t.min_fixed_percent is None else
                     f"{num(t.min_fixed_percent, 1)} %"))
    rows += [("Cociente mínimo", "no se evalúa" if t.min_ratio is None else num(t.min_ratio, 1)),
             ("PDOP máxima", "no se evalúa" if t.max_pdop is None else num(t.max_pdop, 1)),
             ("Duración mínima" + (" de la ocupación" if kind == "points" else " de la sesión"),
              "no se evalúa" if t.min_duration_s is None else calidad.duration_text(t.min_duration_s))]
    if kind == "points":
        rows += [("Entre ocupaciones repetidas (planta / cota)", f"{m(t.max_repeat_h_m)} / {m(t.max_repeat_v_m)}")]
    if kind == "network":
        def loop(fixed, ppm):
            return "no se evalúa" if fixed is None else f"{num(fixed, 3, 'm')} + {num(ppm, 1)} ppm"
        rows += [("Cierre de bucle en planta", loop(t.max_loop_h_m, t.max_loop_h_ppm)),
                 ("Cierre de bucle en cota", loop(t.max_loop_v_m, t.max_loop_v_ppm))]
    rows += [("Contra control (planta / cota)", f"{m(t.max_control_h_m)} / {m(t.max_control_v_m)}"),
             ("Falla", f"al pasar de {num(t.fail_factor, 1)} veces la tolerancia")]
    return rows


def section(r: Report, q: QualityReport, kind: str) -> None:
    """«Control de calidad»: las tolerancias, el estado de cada punto (y de cada línea base) y las ocupaciones
    repetidas. Luego, si hay control, la sección «Control»."""
    r.h1("Control de calidad")
    r.para(f"Tolerancias del proyecto: {tolerances_text(q)}. Cada criterio pasa dentro de su tolerancia, queda «a "
           f"revisar» fuera de ella y falla al pasar de {num(q.tolerances.fail_factor, 1)} veces (o, en los "
           "mínimos, por debajo de la tolerancia entre ese factor). Se compara la cifra impresa.", "small")
    r.fields(_tolerance_rows(q, kind), columns=2, label_width=150)
    r.space(4)
    r.fields(summary_fields(q)[1:], columns=1, label_width=150)
    if q.baselines:
        r.h2("Líneas base")
        rows = [[b.base_id, level_text(b.status), b.status.reason or "dentro de tolerancia"] for b in q.baselines]
        first = 120 if kind == "network" else 55
        r.table(["Línea" if kind == "network" else "Base", "Estado", "Razón"], rows,
                widths=[first, 60, CONTENT_WIDTH - first - 60],
                highlight={i: LEVEL_BG[b.status.level] for i, b in enumerate(q.baselines)})
    r.h2("Vértice" if kind == "static" else "Puntos")
    rows = []
    for p in q.points:
        reason = p.status.reason or "dentro de tolerancia"
        if p.note:
            reason = f"{reason} ({p.note})"
        rows.append([p.name, level_text(p.status), reason])
    r.table(["Punto", "Estado", "Razón"], rows, widths=[60, 60, CONTENT_WIDTH - 120],
            highlight={i: LEVEL_BG[p.status.level] for i, p in enumerate(q.points)})
    na = sorted({calidad.CRITERIA[c.criterion][0] for p in q.points for c in p.status.checks
                 if c.level == "na" and c.criterion in calidad.CRITERIA})
    if na:
        r.para("Sin dato en algún punto (ese criterio no cuenta para su estado): " + ", ".join(na) + ".", "small")
    if q.groups:
        _groups(r, q)
    if q.control or q.control_missing or q.control_source:
        control_section(r, q)


def _groups(r: Report, q: QualityReport) -> None:
    r.h2("Ocupaciones repetidas")
    rows, highlight = [], {}
    for g in q.groups:
        result = "media ponderada" if g.choice == 0 else f"ocupación {g.choice}"
        for i, p in enumerate(g.occupations):
            d = g.deltas_enu[i]
            used = "sí" if i in g.used else "no"
            rows.append([g.name if i == 0 else "", str(i + 1), estilo.utc_text(p.point.start),
                         estilo.duration_text((p.point.end - p.point.start).total_seconds()),
                         secciones.POINT_SOLUTION_NAMES[p.solution] if p.position is not None else "—",
                         num(math.hypot(d.e, d.n), 3) if d else "—", signed(d.u, 3) if d else "—", used,
                         result if i == 0 else ""])
        rows.append(["", "", "", "", "Mayor diferencia",
                     num(g.spread_h_m, 3) if g.spread_h_m is not None else "—",
                     num(g.spread_v_m, 3) if g.spread_v_m is not None else "—",
                     level_text(g.status), g.status.reason or "dentro de tolerancia"])
        highlight[len(rows) - 1] = LEVEL_BG[g.status.level]
    r.table(["Punto", "Ocup.", "Inicio", "Duración", "Solución", "ΔH a la media (m)", "ΔV a la media (m)",
             "En la media", "Resultado"], rows, widths=[46, 30, 92, 50, 52, 52, 52, 48, 82], right={5, 6},
            highlight=highlight)
    r.para("Las ocupaciones del mismo nombre (sin distinguir mayúsculas) se promedian con pesos 1/σ² por componente; "
           "entran las fijas (las flotantes solo si no hay fijas). La σ de la media no baja de la mínima de un punto "
           "fijo a esa distancia (8 mm + 1 ppm en planta, 15 mm + 2 ppm en vertical), porque las ocupaciones con la "
           "misma base comparten su coordenada, las órbitas y la troposfera. ΔH y ΔV: cada ocupación menos la media.",
           "small")


def control_section(r: Report, q: QualityReport) -> None:
    r.h1("Control")
    source = f" de «{q.control_source}»" if q.control_source else ""
    r.para(f"Coordenadas de control{source}, en el sistema del resultado; se unen por el nombre del punto. Residuo: "
           "medido menos control. Tolerancia: "
           f"{num(q.tolerances.max_control_h_m, 3) if q.tolerances.max_control_h_m is not None else '—'} m en planta y "
           f"{num(q.tolerances.max_control_v_m, 3) if q.tolerances.max_control_v_m is not None else '—'} m en cota.",
           "small")
    if q.control:
        rows, highlight = [], {}
        for i, c in enumerate(q.control):
            dh = math.hypot(c.delta_e_m, c.delta_n_m) if c.delta_e_m is not None else None
            rows.append([c.name, num(c.control.northing_m, 3) if c.control.northing_m is not None else "—",
                         num(c.control.easting_m, 3) if c.control.easting_m is not None else "—",
                         num(c.control.elevation_m, 3) if c.control.elevation_m is not None else "—",
                         signed(c.delta_n_m, 3) if c.delta_n_m is not None else "—",
                         signed(c.delta_e_m, 3) if c.delta_e_m is not None else "—",
                         num(dh, 3) if dh is not None else "—",
                         signed(c.delta_h_m, 3) if c.delta_h_m is not None else "—", level_text(c.status)])
            highlight[i] = LEVEL_BG[c.status.level]
        r.table(["Punto", "Norte control (m)", "Este control (m)", "Cota control (m)", "ΔN (m)", "ΔE (m)",
                 "ΔH planta (m)", "ΔV cota (m)", "Estado"], rows, widths=[50, 66, 62, 56, 46, 46, 50, 48, 58],
                right={1, 2, 3, 4, 5, 6, 7}, highlight=highlight)
        reasons = [f"{c.name}: {c.status.reason}" for c in q.control if c.status.reason]
        notes = [f"{c.name}: {c.note}" for c in q.control if c.note]
        if reasons:
            r.callout(LEVEL_KIND[calidad.worst(c.status.level for c in q.control)], "Fuera de tolerancia", reasons)
        for note in notes:
            r.para(note + ".", "small")
    else:
        r.para("Ningún punto de control coincide por nombre con un punto medido.", "small")
    if q.control_missing:
        r.para("Control sin punto medido del mismo nombre: " + ", ".join(q.control_missing) + ".", "small")
