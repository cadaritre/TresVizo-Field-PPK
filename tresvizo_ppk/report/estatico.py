"""Informe de posproceso de línea base (estático), especificación §5.1."""
from __future__ import annotations

import math
from pathlib import Path

from reportlab.platypus import Paragraph

from ..crs import api as crs
from ..model import Ecef, Enu, Project, Solution, StaticResult, UserProfile
from . import calculos, estilo, secciones
from .documento import Report, text
from .estilo import num, signed

TITLE = "Informe de posproceso de línea base"


def _status(result: StaticResult, min_ratio: float) -> tuple[str, str, list[str]]:
    """(«success»/«warning»/«error», título, motivos)."""
    reasons: list[str] = []
    if not result.baselines or all(b.solution == Solution.NONE for b in result.baselines):
        return "error", "Sin solución", ["Ninguna línea base dio solución."]
    for b in result.baselines:
        if b.solution != Solution.FIXED:
            reasons.append(f"La línea base desde {b.base.id} quedó {secciones.SOLUTION_NAMES[b.solution].lower()}.")
        elif b.ratio is not None and b.ratio < min_ratio:
            reasons.append(f"El cociente de la línea base desde {b.base.id} ({num(b.ratio, 1)}) es menor que el mínimo "
                           f"({min_ratio:g}).")
    adj = result.adjustment
    if adj is not None and not adj.passed:
        reasons.append("El ajuste de red no pasa la prueba χ² al 95 %.")
    if adj is not None and adj.flagged_baselines:
        reasons.append("Líneas base que no cuadran con las demás: " + ", ".join(adj.flagged_baselines) + ".")
    if len(result.baselines) == 1:
        reasons.append("Con una sola base no hay redundancia: la coordenada sale de esa línea base y no se puede "
                       "comprobar con otra.")
    reasons += result.warnings
    if any(b.solution == Solution.FIXED for b in result.baselines) and not [
            x for x in reasons if not x.startswith("Con una sola base")]:
        return ("success", "Aprobado", reasons)
    return "warning", "Con avisos", reasons


def static_report(result: StaticResult, project: Project, user: UserProfile, out_pdf: str | Path) -> Path:
    choice = project.coordinates
    options = project.options
    r = Report(TITLE, project, user)
    c, problem = secciones.transform_or_none(result.position, choice)
    cov = result.adjustment.covariance_ecef if result.adjustment else (
        result.baselines[0].covariance_ecef if len(result.baselines) == 1 else None)
    grid_sigma = None
    if c is not None:
        grid_sigma = crs.transform_sigma(result.position, result.sigma_enu, choice, cov)

    # 1. Resumen ------------------------------------------------------------------------------------------------------
    kind, status, reasons = _status(result, options.min_ratio)
    r.h1(f"Resumen · {result.point_name}")
    solution = _overall_solution(result)
    summary = [("Punto", result.point_name), ("Solución", solution),
               ("Bases", ", ".join(f"{b.id} ({b.name})" for b in result.bases) or "—"),
               ("Cálculo", "ajuste por mínimos cuadrados de las líneas base" if result.adjustment else
                "una sola línea base, sin ajuste")]
    r.fields(summary, columns=2, label_width=70)
    r.space(4)
    r.callout(kind, f"Resultado: {status}", reasons or ["Todas las líneas base fijas y el ajuste dentro de "
                                                         "tolerancia."])
    r.h2("Coordenada final en el sistema elegido")
    r.fields(secciones.coordinate_fields(c, result.position, choice), columns=2, label_width=110)
    r.h2(f"Coordenada final en el marco oficial ({result.position.frame}, época {result.position.epoch:.1f})")
    r.fields(secciones.official_fields(result.position), columns=2, label_width=110)
    r.h2("Precisión")
    s = result.sigma_enu
    prec = [("σ Este / σ Norte (1σ)", f"{num(s.e, 3)} / {num(s.n, 3)} m"), ("σ Vertical (1σ)", num(s.u, 3, "m")),
            ("Horizontal al 95 %", num(calculos.horizontal_95(s.e, s.n), 3, "m")),
            ("Vertical al 95 %", num(calculos.vertical_95(s.u), 3, "m"))]
    if grid_sigma is not None and c is not None and c.easting_m is not None:
        prec.append(("σ en la cuadrícula (E / N)", f"{num(grid_sigma.e, 3)} / {num(grid_sigma.n, 3)} m"))
    if cov is not None:
        lat, lon, _ = calculos.geodetic(result.position)
        ce = calculos.cov_enu(cov, lat, lon)
        a, b, az = calculos.error_ellipse(ce[0][0], ce[1][1], ce[0][1])
        prec.append(("Elipse de error (1σ)", f"{num(a, 3)} × {num(b, 3)} m, acimut del eje mayor {num(az, 1)}°"))
    if c is not None and c.orthometric_height_m is not None:
        h_sigma = crs.height_sigma(s.u, c)
        prec.append((f"σ de la cota (con el geoide ±{num(c.geoid_uncertainty_m, 2)} m)", num(h_sigma, 3, "m")))
    r.fields(prec, columns=2, label_width=120)
    r.para("σ es la desviación típica (1σ) del proceso. Al 95 %: 1.96σ en la vertical y 2.45σ (círculo de una "
           "normal bidimensional) en la horizontal. La cota hereda además la incertidumbre del geoide.", "small")

    # 2. Proyecto y sistema -------------------------------------------------------------------------------------------
    r.h1("Proyecto")
    proj_pairs = [("Proyecto", project.name or "—"), ("Número", project.number or "—"),
                  ("Creado", estilo.utc_text(project.created)), ("Procesado", estilo.utc_text(project.processed)),
                  ("Altura del bastón", num(project.pole_height_m, 3, "m")),
                  ("Carpeta", str(project.folder) if project.folder else "—")]
    r.fields(proj_pairs, columns=2, label_width=80)
    secciones.system_section(r, c, choice, problem, f"{result.position.frame}, época {result.position.epoch:.1f}")
    secciones.options_section(r, options, {b.ephemeris for b in result.baselines}, result.log)

    # 3. Estaciones ---------------------------------------------------------------------------------------------------
    r.h1("Estaciones")
    r.table(["Clave", "Nombre", "Antena y radomo", "Altura (m)", "Receptor", "Coordenada"],
            secciones.station_rows(result.bases), widths=[40, 85, 105, 50, 80, 155], right={3})
    rover = result.rover
    r.space(4)
    r.fields([("Rover", rover.marker or result.point_name), ("Receptor", rover.receiver or "—"),
              ("Antena", rover.antenna_type or "—"), ("Altura a la ARP", num(rover.antenna_height_m, 3, "m")),
              ("RINEX", rover.rinex_version or "—"), ("Intervalo", f"{rover.interval_s:g} s" if rover.interval_s
                                                      else "—")], columns=2, label_width=80)
    note = secciones.rover_antenna_note(rover)
    if note:
        r.para(note, "small")

    # 4. Líneas base --------------------------------------------------------------------------------------------------
    for b in result.baselines:
        _baseline(r, b, options)

    # 5. Ajuste de red ------------------------------------------------------------------------------------------------
    adj = result.adjustment
    if adj is not None:
        r.h1("Ajuste de red")
        lo, hi = calculos.chi2_bounds(adj.degrees_of_freedom) if adj.degrees_of_freedom > 0 else (None, None)
        head = r.st["cell_head"]
        pairs = [("Grados de libertad", str(adj.degrees_of_freedom)),
                 (Paragraph("σ<sub>0</sub> a priori", head), "1.000"),
                 (Paragraph("σ<sub>0</sub> a posteriori", head), num(adj.sigma0, 3)), ("χ²", num(adj.chi2, 3)),
                 ("Intervalo de aceptación al 95 %", f"{num(lo, 3)} – {num(hi, 3)}" if lo is not None else
                  "sin redundancia"),
                 ("Prueba χ²", "pasa" if adj.passed else "no pasa")]
        r.fields(pairs, columns=2, label_width=120)
        rows, hl = [], {}
        for i, (bid, res) in enumerate(sorted(adj.residuals_enu.items())):
            flagged = bid in adj.flagged_baselines
            rows.append([bid, signed(res.e, 3), signed(res.n, 3), signed(res.u, 3), num(math.hypot(res.e, res.n), 3),
                         "no cuadra" if flagged else "bien"])
            if flagged:
                hl[i] = estilo.SEMANTIC["warning_bg"]
        r.space(4)
        r.table(["Línea base desde", "Residuo E (m)", "Residuo N (m)", "Residuo U (m)", "Horizontal (m)", "Estado"],
                rows, widths=[90, 75, 75, 75, 75, 70], right={1, 2, 3, 4}, highlight=hl)
        r.space(4)
        r.fields([("σ E / σ N / σ U ajustadas", f"{num(adj.sigma_enu.e, 4)} / {num(adj.sigma_enu.n, 4)} / "
                   f"{num(adj.sigma_enu.u, 4)} m")], columns=1, label_width=150)
        _covariance(r, adj.covariance_ecef, "Matriz de covarianza de la coordenada ajustada (ECEF, m²)")

    # 6. Calidad de los datos -----------------------------------------------------------------------------------------
    r.h1("Calidad de los datos")
    secciones.quality_section(r, result.rover_quality)

    # 7. Gráficas -----------------------------------------------------------------------------------------------------
    r.h1("Gráficas")
    chart_notes: list[str] = []
    for b in result.baselines:
        chart_notes += secciones.rtklib_charts(r, b.pos_file, b.stat_file, options, b.base.id,
                                               heading=f"Línea base desde {b.base.id}")
    if chart_notes:
        r.callout("info", "Gráficas que faltan", chart_notes)

    # 8. Avisos y anexo -----------------------------------------------------------------------------------------------
    all_warnings = list(result.warnings) + [w for b in result.baselines for w in b.warnings]
    if c is not None:
        all_warnings += list(c.warnings)
    if problem:
        all_warnings.append(problem)
    secciones.warnings_section(r, list(dict.fromkeys(all_warnings)))
    r.brand()
    secciones.annex(r, result.log)
    return r.build(out_pdf, subject=f"Línea base · {result.point_name}")


def _overall_solution(result: StaticResult) -> str:
    sols = {b.solution for b in result.baselines}
    if sols == {Solution.FIXED}:
        return "FIJA (todas las líneas base)"
    if Solution.FIXED in sols:
        return "MIXTA: " + ", ".join(f"{b.base.id} {secciones.SOLUTION_NAMES[b.solution]}" for b in result.baselines)
    if sols:
        return secciones.SOLUTION_NAMES[sorted(sols, key=lambda s: ["fixed", "float", "single", "none"].index(
            s.value))[0]]
    return "SIN SOLUCIÓN"


def _covariance(r: Report, cov, title: str) -> None:
    r.h2(title)
    rows = [[axis] + [f"{cov[i][j]:.4e}" for j in range(3)] for i, axis in enumerate("XYZ")]
    corr = calculos.correlation(cov)
    rows_c = [[axis] + [num(corr[i][j], 4) for j in range(3)] for i, axis in enumerate("XYZ")]
    r.table(["", "X", "Y", "Z"], rows, widths=[30, 100, 100, 100], right={1, 2, 3}, repeat=False)
    r.space(3)
    r.para("Correlaciones:", "small")
    r.table(["", "X", "Y", "Z"], rows_c, widths=[30, 100, 100, 100], right={1, 2, 3}, repeat=False)


def _baseline(r: Report, b, options) -> None:
    r.h1(f"Línea base {b.base.id} → {b.rover_marker}")
    duration = (b.end - b.start).total_seconds()
    sx, sy, sz = secciones.ecef_sigma(b.covariance_ecef)
    lat, lon, _ = calculos.geodetic(b.base.position)
    cenu = calculos.cov_enu(b.covariance_ecef, lat, lon)
    se, sn, su = (math.sqrt(max(cenu[i][i], 0.0)) for i in range(3))
    vec = (b.vector_ecef.x, b.vector_ecef.y, b.vector_ecef.z)
    az, zen = b.azimuth_deg, b.zenith_deg
    sigma_len = calculos.length_sigma(vec, b.covariance_ecef)
    r.fields([("Base", f"{b.base.id} · {b.base.name}"), ("Rover", b.rover_marker),
              ("Inicio", estilo.utc_text(b.start)), ("Fin", estilo.utc_text(b.end)),
              ("Duración", estilo.duration_text(duration)), ("Épocas usadas", str(b.epochs_used)),
              ("Solución", secciones.SOLUTION_NAMES[b.solution]),
              ("Cociente (ratio)", num(b.ratio, 1) if b.ratio is not None else "—"),
              ("RMS", num(b.rms_m, 4, "m") if b.rms_m is not None else "—"),
              ("Efemérides", secciones.EPHEMERIS_NAMES[b.ephemeris])], columns=2, label_width=80)
    r.para(f"Satélites usados ({len(b.satellites)}): {text(', '.join(b.satellites))}", "small")
    r.h2("Vector")
    r.table(["Componente", "Valor (m)", "σ (m)", "Componente", "Valor (m)", "σ (m)"],
            [["ΔX", signed(b.vector_ecef.x, 4), num(sx, 4), "ΔE", signed(b.vector_enu.e, 4), num(se, 4)],
             ["ΔY", signed(b.vector_ecef.y, 4), num(sy, 4), "ΔN", signed(b.vector_enu.n, 4), num(sn, 4)],
             ["ΔZ", signed(b.vector_ecef.z, 4), num(sz, 4), "ΔU", signed(b.vector_enu.u, 4), num(su, 4)]],
            widths=[65, 90, 65, 65, 90, 65], right={1, 2, 4, 5}, repeat=False)
    r.space(4)
    r.fields([("Longitud", f"{num(b.length_m, 4, 'm')} ± {num(sigma_len, 4)} m"),
              ("Acimut geodésico", f"{estilo.azimuth_dms(az)} ({num(az, 6)}°)"),
              ("Distancia cenital", f"{estilo.azimuth_dms(zen)} ({num(zen, 6)}°)"),
              ("Desnivel elipsoidal", signed(b.vector_enu.u, 4, "m"))], columns=2, label_width=90)
    _covariance(r, b.covariance_ecef, "Matriz de covarianza del vector (ECEF, m²)")
    if b.warnings:
        r.callout("warning", "Avisos de la línea base", b.warnings)
