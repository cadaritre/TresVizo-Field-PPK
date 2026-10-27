"""Informe de PPK de la jornada (puntos), especificación §5.2."""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from ..crs import api as crs
from ..model import CoordinateChoice, Ecef, Enu, PointsResult, ProcessedPoint, Project, Solution, UserProfile
from . import calculos, estilo, graficas, normas, secciones
from .documento import Report, text
from .estilo import num, signed
from .rtklib_salida import read_pos

TITLE = "Informe de PPK de la jornada"
MAX_TRACK = 20_000


def field_delta(p: ProcessedPoint) -> Enu | None:
    """Posproceso − campo, en ENU del punto. Se calcula de las dos posiciones si están; si no, el que da el motor."""
    if p.position is not None and p.point.field_position is not None:
        f = p.point.field_position
        lat, lon, _ = calculos.geodetic(p.position)
        e, n, u = calculos.to_enu((p.position.x - f.x, p.position.y - f.y, p.position.z - f.z), lat, lon)
        return Enu(e, n, u)
    return p.delta_from_field_enu


def _track(pos_file: Path | None, choice: CoordinateChoice) -> tuple[list[tuple[float, float, int]], int, float]:
    """La trayectoria en el sistema elegido (este/norte o longitud/latitud), sus épocas y su duración en segundos."""
    if not pos_file or not Path(pos_file).is_file():
        return [], 0, 0.0
    pos = read_pos(pos_file)
    epochs = pos.epochs
    if not epochs:
        return [], 0, 0.0
    duration = (epochs[-1].time - epochs[0].time).total_seconds()
    step = max(1, len(epochs) // MAX_TRACK)
    plane = CoordinateChoice(choice.crs_id, choice.zone, None)
    grs80 = crs.definition("EPSG:6365").ellipsoid
    out = []
    zone_fixed = None
    for e in epochs[::step]:
        if pos.geocentric:
            p = Ecef(e.x, e.y, e.z)
        else:
            p = Ecef(*crs.motor.geocentric(e.lat, e.lon, e.height, grs80))
        try:
            c = crs.transform(p, CoordinateChoice(zone_fixed or plane.crs_id, "", None) if zone_fixed else plane)
        except Exception:
            continue
        zone_fixed = zone_fixed or c.crs_code      # toda la trayectoria en la zona de la primera época
        if c.easting_m is not None:
            out.append((c.easting_m, c.northing_m, e.q))
        else:
            out.append((c.longitude_deg, c.latitude_deg, e.q))
    return out, len(epochs), duration


def points_report(result: PointsResult, project: Project, user: UserProfile, out_pdf: str | Path) -> Path:
    choice = project.coordinates
    options = project.options
    r = Report(TITLE, project, user)
    points = result.points
    counts = Counter(p.solution for p in points)

    base_c, base_problem = secciones.transform_or_none(result.base.position, choice)
    # Una sola cuadrícula en todo el informe: la zona del primer punto posprocesado (la misma regla que la exportación).
    point_choice = choice
    ref = next((p.position for p in points if p.position is not None and p.solution != Solution.NONE), None)
    if choice.crs_id in crs.FAMILIES and not choice.zone.strip():
        ref_c, _ = secciones.transform_or_none(ref or result.base.position, CoordinateChoice(choice.crs_id, "", None))
        if ref_c is not None:
            point_choice = CoordinateChoice(choice.crs_id, ref_c.zone, choice.geoid_id, choice.label)
    coords: list[tuple[ProcessedPoint, object, str]] = []
    for p in points:
        c, problem = secciones.transform_or_none(p.position, point_choice)
        coords.append((p, c, problem))
    accs = [normas.accuracy(p.sigma_enu, c) if p.position is not None else None for p, c, _ in coords]
    orders = Counter(normas.order_text(a) for a in accs if a is not None)

    # 1. Resumen ------------------------------------------------------------------------------------------------------
    r.h1("Resumen")
    total = len(points)
    summary = [("Puntos", str(total)), ("Fijos", str(counts[Solution.FIXED])),
               ("Flotantes", str(counts[Solution.FLOAT])),
               ("Autónomos", str(counts[Solution.SINGLE])), ("Sin solución", str(counts[Solution.NONE])),
               ("Épocas fijas de la trayectoria", f"{num(100 * result.fix_rate, 1)} %"),
               ("Base", secciones.station_label(result.base)),
               ("Origen de la base", _base_origin(result.base, project))]
    if orders:
        summary.append(("Orden de exactitud (NT EEP art. 8)", "; ".join(
            f"{name}: {n} punto{'s' if n != 1 else ''}" for name, n in sorted(orders.items(), key=_order_key))))
    r.fields(summary, columns=2, label_width=120)
    if counts[Solution.FIXED] == total and total:
        kind, title = "success", "Todos los puntos fijos"
    elif counts[Solution.FIXED]:
        kind, title = "warning", f"{total - counts[Solution.FIXED]} de {total} puntos sin solución fija"
    else:
        kind, title = "error", "Ningún punto quedó fijo"
    lines = [f"{p.point.name}: {secciones.POINT_SOLUTION_NAMES[p.solution].lower()}"
             + (f" — {p.message}" if p.message else "") for p in points if p.solution != Solution.FIXED]
    r.callout(kind, title, lines or ["Cada punto se promedió con épocas fijas."])
    if result.base.approximate:
        r.callout("warning", "Base con coordenada aproximada", [secciones.approximate_base_text(result.base)])
    r.para("En posproceso todo se asume plomeado: cada punto es la antena menos la altura vertical del bastón; la "
           "compensación de inclinación de campo no interviene.", "small")

    # 2. Base ---------------------------------------------------------------------------------------------------------
    r.h1("Base")
    r.table(["Clave", "Nombre", "Antena y radomo", "Altura (m)", "Receptor", "Coordenada"],
            secciones.station_rows([result.base]), widths=[40, 85, 105, 50, 80, 155], right={3})
    r.space(4)
    secciones.base_notices(r, [result.base])
    r.space(4)
    r.fields(secciones.coordinate_fields(base_c, result.base.position, choice), columns=2, label_width=110)
    r.space(4)
    r.fields(secciones.official_fields(result.base.position), columns=2, label_width=110)

    # 3. Sistema y proceso --------------------------------------------------------------------------------------------
    r.h1("Proyecto")
    r.fields([("Proyecto", project.name or "—"), ("Número", project.number or "—"),
              ("Creado", estilo.utc_text(project.created)), ("Procesado", estilo.utc_text(project.processed)),
              ("Rover", result.rover.marker or "—"), ("Receptor del rover", result.rover.receiver or "—"),
              ("Antena del rover", result.rover.antenna_type or "—"),
              ("Inicio de la grabación", estilo.utc_text(result.rover.start)),
              ("Fin de la grabación", estilo.utc_text(result.rover.end))], columns=2, label_width=110)
    note = secciones.rover_antenna_note(result.rover)
    if note:
        r.para(note, "small")
    r.space(4)
    secciones.antenna_section(r, getattr(result, "antennas", []))
    first = next((c for _, c, _ in coords if c is not None), base_c)
    secciones.system_section(r, first, point_choice, base_problem, secciones.frame_text(result.base.position))
    secciones.options_section(r, options, {options.ephemeris}, result.log, kinematic=True)

    # 4. Tabla de puntos ----------------------------------------------------------------------------------------------
    r.h1("Puntos")
    a1, a2 = ("Norte (m)", "Este (m)") if first is None or first.easting_m is not None else ("Latitud", "Longitud")
    hl = secciones.height_label(first)
    rows, highlight = [], {}
    for i, (p, c, problem) in enumerate(coords):
        order = normas.order_text(accs[i])
        if c is None:
            rows.append([p.point.name, p.point.code, "—", "—", "—", "—",
                         secciones.POINT_SOLUTION_NAMES[p.solution], order])
        elif c.easting_m is not None:
            rows.append([p.point.name, p.point.code, num(c.northing_m, 3), num(c.easting_m, 3),
                         num(secciones.height_value(c), 3), num(c.ellipsoidal_height_m, 3),
                         secciones.POINT_SOLUTION_NAMES[p.solution], order])
        else:
            rows.append([p.point.name, p.point.code, estilo.lat_text(c.latitude_deg), estilo.lon_text(c.longitude_deg),
                         num(secciones.height_value(c), 3), num(c.ellipsoidal_height_m, 3),
                         secciones.POINT_SOLUTION_NAMES[p.solution], order])
        if p.solution != Solution.FIXED:
            highlight[i] = estilo.SEMANTIC["warning_bg"] if p.solution != Solution.NONE else estilo.SEMANTIC["error_bg"]
    r.table(["Punto", "Código", a1, a2, hl.split(" (")[0] + " (m)", "h (m)", "Solución", "Orden de exactitud"], rows,
            widths=[52, 46, 74, 74, 60, 56, 62, 66], right={2, 3, 4, 5}, highlight=highlight)
    r.space(6)
    r.h2("Calidad de cada punto")
    qrows = []
    for p, c, _ in coords:
        s = p.sigma_enu
        d = field_delta(p)
        qrows.append([p.point.name,
                      num(math.hypot(s.e, s.n), 3) if s else "—", num(s.u, 3) if s else "—",
                      f"{p.epochs_fixed} / {p.epochs_total}",
                      estilo.duration_text((p.point.end - p.point.start).total_seconds()),
                      num(p.point.antenna_height_m, 3),
                      num(p.distance_to_base_m / 1000, 3) if p.distance_to_base_m is not None else "—",
                      num(math.hypot(d.e, d.n), 3) if d else "—", signed(d.u, 3) if d else "—",
                      p.point.field_solution or "—"])
    r.table(["Punto", "σH (m)", "σV (m)", "Épocas fijas / total", "Ocupación", "Bastón (m)", "A la base (km)",
             "ΔH campo (m)", "ΔV campo (m)", "En campo"], qrows,
            widths=[55, 42, 42, 58, 58, 44, 48, 48, 48, 56], right={1, 2, 5, 6, 7, 8}, highlight=highlight)
    r.para("σH = √(σE² + σN²) y σV de las épocas promediadas (1σ). ΔH y ΔV: posproceso menos lo medido en campo. "
           "Un punto sin épocas fijas se promedia con las flotantes y queda marcado como FLOTANTE.", "small")
    r.space(6)
    r.h2("Exactitud posicional (NT EEP, DOF 23-12-2010)")
    has_h = any(a is not None and a.epv_orthometric_m is not None for a in accs)
    erows = []
    for (p, c, _), a in zip(coords, accs):
        s_ = p.sigma_enu
        erows.append([p.point.name, num(s_.n, 3) if s_ and a else "—", num(s_.e, 3) if s_ and a else "—",
                      num(calculos.horizontal_95(s_.e, s_.n), 3) if s_ and a else "—",
                      num(a.cep95_m, 3) if a else "—", normas.order_text(a),
                      num(a.epv_ellipsoidal_m, 3) if a else "—"]
                     + ([num(a.epv_orthometric_m, 3) if a and a.epv_orthometric_m is not None else "—"] if has_h
                        else []))
    heads = ["Punto", "σφ = σN (m)", "σλ = σE (m)", "Horizontal 95 % círculo (m)", "CEP95 art. 6 (m)",
             "Orden art. 8", "EPV h art. 7 (m)"] + ([f"EPV {secciones.height_label(first).split(' (')[0].lower()} "
                                                       f"art. 12 (m)"] if has_h else [])
    r.table(heads, erows, widths=[55, 55, 55, 70, 62, 72, 60] + ([62] if has_h else []),
            right={1, 2, 3, 4, 6, 7}, highlight=highlight)
    r.para(text(normas.HORIZONTAL_NOTE + " EPV h: 1.9600·σ de la altura elipsoidal. "
                + normas.accuracy_note(next((a for a in accs if a is not None and a.epv_orthometric_m is not None),
                                            None))), "small")

    # 5. Trayectoria --------------------------------------------------------------------------------------------------
    r.h1("Trayectoria")
    track, n_epochs, duration = _track(result.pos_file, point_choice)
    if track:
        q_counts = Counter(q for _, _, q in track)
        r.fields([("Épocas", str(n_epochs)), ("Duración", estilo.duration_text(duration)),
                  ("Fijas", f"{num(100 * q_counts[1] / len(track), 1)} %"),
                  ("Flotantes", f"{num(100 * q_counts[2] / len(track), 1)} %")], columns=2, label_width=80)
        pts = [(p.point.name, *( (c.easting_m, c.northing_m) if c.easting_m is not None else
                                (c.longitude_deg, c.latitude_deg)), p.solution.value)
               for p, c, _ in coords if c is not None]
        base_xy = None
        if base_c is not None:
            base_xy = (result.base.id, *((base_c.easting_m, base_c.northing_m) if base_c.easting_m is not None
                                         else (base_c.longitude_deg, base_c.latitude_deg)))
            # La base solo se dibuja si queda cerca de la trayectoria (si no, aplasta la figura).
            xs = [t[0] for t in track]
            ys = [t[1] for t in track]
            span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
            if not (min(xs) - span <= base_xy[1] <= max(xs) + span and min(ys) - span <= base_xy[2] <= max(ys) + span):
                base_xy = None
        r.image(graficas.trajectory(track, pts, base_xy, secciones.axis_names(first),
                                    f"Trayectoria y puntos ({secciones.system_name(first, point_choice)})"))
    else:
        r.para("No hay trayectoria que dibujar: falta el archivo de la solución cinemática.", "small")

    # 6. Calidad, gráficas, avisos ------------------------------------------------------------------------------------
    r.h1("Calidad de los datos")
    secciones.quality_section(r, result.rover_quality)
    r.h1("Gráficas")
    stat = Path(str(result.pos_file) + ".stat") if result.pos_file else None
    notes = secciones.rtklib_charts(r, result.pos_file, stat if stat and stat.is_file() else None, options)
    if notes:
        r.callout("info", "Gráficas que faltan", notes)
    warnings = list(result.warnings)
    warnings += [f"{p.point.name}: {p.message}" for p in points if p.message]
    for _, c, problem in coords:
        if problem:
            warnings.append(problem)
        if c is not None:
            warnings += list(c.warnings)
    if base_problem:
        warnings.append(base_problem)
    secciones.warnings_section(r, list(dict.fromkeys(warnings)))
    if first is not None and first.attribution:
        r.para(" ".join(text(a) for a in first.attribution), "small")
    r.brand()
    secciones.annex(r, result.log, project)
    solved = any(p.position is not None and p.solution != Solution.NONE for p in points)
    normas.signature_section(r, normas.declaration([result.base], result.base.position, first, solved), project, user)
    return r.build(out_pdf, subject=f"PPK · {project.name or result.rover.marker}")


def _order_key(item: tuple[str, int]) -> int:
    names = [name for _, name in calculos.ACCURACY_ORDERS]
    return names.index(item[0]) if item[0] in names else len(names)


def _base_origin(base, project: Project) -> str:
    if base.approximate:
        return f"coordenada aproximada ({secciones.ORIGIN_NAMES.get(base.coordinate_origin, base.coordinate_origin)})"
    if project.base_kind == "own":
        if project.source_project:
            return f"base propia, coordenada del proyecto estático {project.source_project}"
        return "base propia" + (f", {base.source}" if base.source else "")
    return ("estación de referencia, coordenada oficial de su red" if base.official else
            "estación de referencia, coordenada corregida por el usuario") + (f" ({base.source})" if base.source else "")
