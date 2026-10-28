"""Informe de ajuste de red (estático de varios puntos), como el «Network Adjustment Report» de TBC: resumen,
coordenadas ajustadas con su orden de exactitud de la NT EEP, esquema, estaciones y ocupaciones, líneas, cierres de
bucle, estadísticas y residuos del mínimamente restringido y del restringido, elipses absolutas y relativas al 95 %,
comprobación del control, control de calidad, avisos, bitácora y la declaración de las normas del INEGI cuando
procede."""
from __future__ import annotations

import math
from pathlib import Path

from ..calidad import duration_text
from ..model import NetworkAdjustment, NetworkStaticResult, Project, Solution, UserProfile
from . import calidad as qa
from . import calculos, estilo, normas, secciones
from .documento import CONTENT_WIDTH, Report, text
from .estilo import num, signed

TITLE = "Informe de ajuste de red"
MODE_TEXT = {"fixed": "fija", "weighted": "ponderada", "check": "comprobación"}
KIND_TEXT = {"free": "nuevo", "datum": "fijo (datum)", "control": "control libre", "fixed": "fija",
             "weighted": "ponderada"}


def _mm(v: float | None, decimals: int = 1) -> str:
    return "—" if v is None else f"{v * 1000:.{decimals}f}"


def _smm(v: float | None, decimals: int = 1) -> str:
    return "—" if v is None else f"{v * 1000:+.{decimals}f}".replace("-", "−")


def _line_role(v) -> str:
    if not v.use:
        return "fuera"
    return "trivial" if v.trivial else "independiente"


def network_quality(result: NetworkStaticResult, project: Project):
    from .. import calidad
    return result.quality or calidad.evaluate_network(result, project.tolerances or
                                                      calidad.default_tolerances("static"), project.control_points,
                                                      project.coordinates, project.control_source)


def network_report(result: NetworkStaticResult, project: Project, user: UserProfile, out_pdf: str | Path) -> Path:
    choice = project.coordinates
    net = result.network
    r = Report(TITLE, project, user)
    final = net.constrained or net.minimal
    coords = {}
    problems = []
    for p in result.points:
        c, problem = secciones.transform_or_none(p.position, choice)
        coords[p.name] = c
        if problem:
            problems.append(f"{p.name}: {problem}")
    quality = network_quality(result, project)

    # 1. Resumen ------------------------------------------------------------------------------------------------------
    r.h1("Resumen")
    independent = [v for v in net.vectors if v.use and not v.trivial]
    trivial = [v for v in net.vectors if v.use and v.trivial]
    passing = sum(lp.status.level == "pass" for lp in net.loops)
    r.fields([("Puntos nuevos", str(len(result.points))), ("Estaciones", ", ".join(s.id for s in result.bases)),
              ("Líneas procesadas", f"{len(result.lines)} ({len(independent)} independientes, {len(trivial)} "
                                    f"triviales, {sum(not v.use for v in net.vectors)} fuera)"),
              ("Sesiones", str(len(net.sessions))),
              ("Cierres de bucle", f"{len(net.loops)}, pasan {passing}" if net.loops else "ninguno"),
              ("Mínimamente restringido", _verdict(net.minimal)),
              ("Restringido", _verdict(net.constrained) if net.constrained else "sin fijas en uso"),
              ("Prueba de los residuos", "τ de Pope" if final.test == "tau" else "w de Baarda"),
              ] + qa.summary_fields(quality), columns=2, label_width=110)
    r.space(4)
    if not (net.minimal.passed and (net.constrained is None or net.constrained.passed)):
        r.callout("warning", "El ajuste no pasa todas sus pruebas", _reasons(result))
    else:
        r.callout("success", "Ajuste dentro de sus pruebas",
                  ["Las líneas concuerdan entre sí (mínimamente restringido) y con las fijas (restringido)."])
    qa.summary_callout(r, quality, "Puntos, líneas y bucles dentro de las tolerancias del proyecto")

    # 2. Coordenadas ajustadas ----------------------------------------------------------------------------------------
    sample = next((c for c in coords.values() if c is not None), None)
    r.h1("Coordenadas ajustadas")
    r.para(text(f"Sistema: {secciones.system_name(sample, choice)}"
                + (f" ({sample.crs_code})" if sample is not None and sample.crs_code else "")
                + f". Ajuste {'restringido' if net.constrained else 'mínimamente restringido'}: {final.datum}."),
           "small")
    rows = []
    projected = sample is not None and sample.easting_m is not None
    for p in result.points:
        c = coords[p.name]
        if c is not None and c.easting_m is not None:
            first, second = num(c.easting_m, 3), num(c.northing_m, 3)
        elif c is not None:
            first, second = estilo.lat_text(c.latitude_deg), estilo.lon_text(c.longitude_deg)
        else:
            first = second = "—"
        height = secciones.height_value(c)
        rows.append([p.name, first, second, num(height, 3) if height is not None else
                     (num(c.ellipsoidal_height_m, 3) if c is not None else "—"),
                     f"{_mm(p.sigma_enu.e)} / {_mm(p.sigma_enu.n)} / {_mm(p.sigma_enu.u)}"])
    titles = ["Este (m)", "Norte (m)"] if projected else ["Latitud", "Longitud"]
    height_title = secciones.height_label(sample) + " (m)" if sample is not None and \
        sample.orthometric_height_m is not None else "Altura elipsoidal (m)"
    r.table(["Punto", *titles, height_title, "σ E / N / U (mm, 1σ)"], rows,
            widths=[70, 100, 100, 95, CONTENT_WIDTH - 365], right={1, 2, 3})
    r.h2("Exactitud posicional (NT EEP)")
    rows, notes = [], []
    for p in result.points:
        acc = normas.accuracy(p.sigma_enu, coords[p.name])
        if acc is None:
            continue
        rows.append([p.name, num(acc.cep95_m, 3), normas.order_text(acc), num(acc.epv_ellipsoidal_m, 3),
                     num(acc.epv_orthometric_m, 3) if acc.epv_orthometric_m is not None else "—",
                     f"{_mm(p.ellipse95[0])} × {_mm(p.ellipse95[1])}, {num(p.ellipse95[2], 0)}°"])
        if not notes:
            notes.append(normas.accuracy_note(acc))
    r.table(["Punto", "CEP95 (m)", "Orden (art. 8)", "EPV h (m)", "EPV cota (m)", "Elipse 95 % (mm, az.)"], rows,
            widths=[60, 60, 95, 60, 65, CONTENT_WIDTH - 340], right={1, 3, 4})
    r.para(text("CEP95 = 1.2238 (σφ + σλ) (art. 6), EPV = 1.96 σ (art. 7) y, con cota, 1.96 (σGNSS² + σN²)^½ "
                "(art. 12). " + normas.HORIZONTAL_NOTE + " " + " ".join(n for n in notes if n)), "small")
    r.h2(f"En el marco de las fijas ({secciones.frame_text(result.points[0].position)})" if result.points else
         "En el marco de las fijas")
    rows = []
    for p in result.points:
        lat, lon, h = calculos.geodetic(p.position)
        rows.append([p.name, estilo.lat_text(lat), estilo.lon_text(lon), num(h, 3), num(p.position.x, 3),
                     num(p.position.y, 3), num(p.position.z, 3)])
    r.table(["Punto", "Latitud", "Longitud", "h (m)", "X (m)", "Y (m)", "Z (m)"], rows,
            widths=[50, 85, 85, 55, 70, 75, CONTENT_WIDTH - 420], right={3, 4, 5, 6})
    r.image(network_plan(result), caption="Esquema de la red: líneas independientes (continuas), triviales "
            "(discontinuas) y fuera (punteadas); elipses al 95 % aumentadas para verse.", heading="Esquema")

    # 3. Proyecto, sistema y opciones ---------------------------------------------------------------------------------
    r.h1("Proyecto")
    r.fields([("Proyecto", project.name or "—"), ("Número", project.number or "—"),
              ("Creado", estilo.utc_text(project.created)), ("Procesado", estilo.utc_text(project.processed))],
             columns=2, label_width=80)
    if sample is not None or problems:
        secciones.system_section(r, sample, choice, "; ".join(problems),
                                 secciones.frame_text(result.points[0].position) if result.points else "")
    secciones.options_section(r, project.options, {ln.baseline.ephemeris for ln in result.lines}, result.log)
    o = net.options
    r.fields([("Prueba", "τ de Pope" if o.test == "tau" else f"w de Baarda (crítico {num(o.w_critical, 2)})"),
              ("Significancia", f"{num(o.alpha * 100, 1)} %"),
              ("Escala de covarianzas", {"auto": "×σ0² si no pasa la χ²", "never": "nunca", "always": "siempre ×σ0²",
                                          "value": f"×{num(o.scale_value, 3)}"}[o.scale_covariances]),
              ("Datum del mínimamente restringido", "un punto fijo" if o.minimal_datum == "point" else
               "restricciones internas"),
              ("Líneas triviales", "dentro del ajuste" if o.include_trivial else "fuera del ajuste")],
             columns=2, label_width=130)

    # 4. Estaciones y ocupaciones -------------------------------------------------------------------------------------
    r.h1("Estaciones y ocupaciones")
    r.table(["Clave", "Nombre", "Antena y radomo", "Altura (m)", "Receptor", "Coordenada"],
            secciones.station_rows(result.bases), widths=[40, 85, 105, 50, 80, 155], right={3})
    r.space(4)
    secciones.base_notices(r, result.bases)
    fix = {f.point_id: f for f in result.fix}
    rows = []
    for s in result.bases:
        f = fix.get(s.id)
        mode = MODE_TEXT.get(f.mode if f else "fixed", "fija")
        parts = "planta y cota" if not f or (f.horizontal and f.vertical) else "planta" if f.horizontal else "cota"
        sigma = f"σ {_mm(s.sigma_enu.e)}/{_mm(s.sigma_enu.n)}/{_mm(s.sigma_enu.u)} mm" if s.sigma_enu else "—"
        rows.append([s.id, mode, parts if mode != "comprobación" else "—", sigma])
    r.table(["Estación", "En el ajuste", "Componentes", "σ de su coordenada (E/N/U)"], rows,
            widths=[60, 80, 90, CONTENT_WIDTH - 230])
    r.h2("Ocupaciones")
    rows = []
    for o in result.occupations:
        obs = result.rovers.get(o.receiver)
        start = o.start or (obs.start if obs else None)
        end = o.end or (obs.end if obs else None)
        height = o.antenna_height_m if o.antenna_height_m is not None else (obs.antenna_height_m if obs else None)
        rows.append([o.name, o.receiver, estilo.utc_text(start), estilo.utc_text(end), num(height, 4),
                     o.source or "grabación completa"])
    r.table(["Punto", "Receptor", "Inicio (UTC)", "Fin (UTC)", "Altura ARP (m)", "Origen"], rows,
            widths=[55, 50, 95, 95, 60, CONTENT_WIDTH - 355], right={4})
    secciones.antenna_section(r, result.antennas)

    # 5. Líneas -------------------------------------------------------------------------------------------------------
    r.h1("Líneas base")
    by_id = {v.id: v for v in net.vectors}
    status = {q.base_id: q.status for q in quality.baselines}
    rows, hl = [], {}
    for ln in result.lines:
        b, v = ln.baseline, by_id.get(ln.id)
        cov = calculos.cov_enu(b.covariance_ecef, *calculos.geodetic(b.base.position)[:2])
        sh, sv = math.hypot(math.sqrt(max(cov[0][0], 0)), math.sqrt(max(cov[1][1], 0))), \
            math.sqrt(max(cov[2][2], 0))
        st = status.get(ln.id)
        rows.append([ln.id, v.session if v else "—", num(b.length_m / 1000, 3), _solution(b.solution),
                     "—" if b.ratio is None else num(b.ratio, 1), f"{_mm(sh)} / {_mm(sv)}",
                     duration_text((b.end - b.start).total_seconds()), _line_role(v) if v else "—",
                     qa.level_text(st) if st else "—"])
        if v is not None and (v.trivial or not v.use):
            hl[len(rows) - 1] = estilo.SEMANTIC["warning_bg"] if v.trivial else estilo.SEMANTIC["error_bg"]
    r.table(["Línea", "Sesión", "km", "Solución", "Cociente", "σH / σV (mm)", "Duración", "Papel", "Estado"], rows,
            widths=[118, 34, 46, 46, 38, 56, 50, 58, CONTENT_WIDTH - 446], right={2, 4}, highlight=hl)
    notes = [f"{v.id}: {v.note}" for v in net.vectors if v.note]
    if result.failed_lines:
        notes += [f"Sin solución: {t}" for t in result.failed_lines]
    if notes:
        r.para(text(" ".join(notes)), "small")

    # 6. Cierres de bucle ---------------------------------------------------------------------------------------------
    r.h1("Cierres de bucle")
    t = quality.tolerances
    r.para(text("Cada línea independiente con el bucle más corto que la contiene, entre sesiones independientes. "
                f"Tolerancia en planta {_tol(t.max_loop_h_m, t.max_loop_h_ppm)} y en cota "
                f"{_tol(t.max_loop_v_m, t.max_loop_v_ppm)} de la longitud del bucle."), "small")
    if net.loops:
        rows, hl = [], {}
        for lp in net.loops:
            m = lp.misclosure_enu
            rows.append([" → ".join(lp.points), num(lp.length_m / 1000, 1), _smm(m.e), _smm(m.n), _smm(m.u),
                         _mm(lp.closure_h_m), _mm(lp.closure_v_m), num(lp.ppm_3d, 2),
                         f"{_mm(lp.tolerance_h_m, 0)} / {_mm(lp.tolerance_v_m, 0)}", qa.level_text(lp.status)])
            if lp.status.level != "pass":
                hl[len(rows) - 1] = qa.LEVEL_BG[lp.status.level]
        r.table(["Bucle", "km", "ΔE", "ΔN", "ΔU", "H (mm)", "V (mm)", "ppm", "Tol. H/V", "Estado"], rows,
                widths=[118, 36, 38, 38, 38, 40, 40, 34, 50, CONTENT_WIDTH - 432], right={1, 2, 3, 4, 5, 6, 7},
                highlight=hl)
    else:
        r.para("No hay bucles entre líneas independientes: ninguna línea se comprueba con otras.", "small")
    if net.unchecked:
        r.callout("warning", "Líneas sin comprobación", ["Ningún bucle comprueba: " + ", ".join(net.unchecked)
                                                         + ". Un error en ellas no se detecta."])

    # 7. Ajustes ------------------------------------------------------------------------------------------------------
    _adjustment_section(r, "Ajuste mínimamente restringido", net.minimal, result)
    if net.control_pairs:
        r.h2("Comprobación del control")
        rows, hl = [], {}
        for c in net.control_pairs:
            d, s = c.delta_enu, c.sigma_enu
            rows.append([f"{c.from_id} – {c.to_id}", _smm(d.e), _smm(d.n), _smm(d.u),
                         f"{_mm(s.e)} / {_mm(s.n)} / {_mm(s.u)}", num(c.max_standardized, 1),
                         "concuerdan" if c.consistent else "no concuerdan"])
            if not c.consistent:
                hl[len(rows) - 1] = estilo.SEMANTIC["warning_bg"]
        r.table(["Par de fijas", "ΔE (mm)", "ΔN (mm)", "ΔU (mm)", "σ E/N/U (mm)", "|Δ|/σ máx.", "Resultado"],
                rows, widths=[85, 50, 50, 55, 95, 55, CONTENT_WIDTH - 390], right={1, 2, 3, 5}, highlight=hl)
        r.para(text("Diferencia de lo que las líneas dicen de dos fijas contra lo que dicen sus coordenadas, con "
                    "la σ del ajuste más la de las coordenadas; no concuerdan si pasa de 3.29σ."
                    + (" No concuerda: " + ", ".join(net.inconsistent_control) + "."
                       if net.inconsistent_control else "")), "small")
    if net.constrained is not None:
        _adjustment_section(r, "Ajuste restringido", net.constrained, result)

    # 8. Calidad, avisos, anexo y firma -------------------------------------------------------------------------------
    qa.section(r, quality, "network")
    r.h1("Calidad de los datos")
    for key, q in result.rover_quality.items():
        secciones.quality_section(r, q, f"Receptor {key}")
    warnings = list(result.warnings) + problems
    for c in coords.values():
        if c is not None:
            warnings += list(c.warnings)
    secciones.warnings_section(r, list(dict.fromkeys(warnings)))
    r.brand()
    secciones.annex(r, result.log, project)
    fixed_ids = {f.point_id for f in result.fix if f.mode != "check"} or {s.id for s in result.bases}
    stations = [s for s in result.bases if s.id in fixed_ids]
    usable = bool(result.points) and net.constrained is not None
    first = result.points[0] if result.points else None
    normas.signature_section(r, normas.declaration(stations, first.position if (usable and first) else None,
                                                   coords.get(first.name) if first else None, usable),
                             project, user)
    return r.build(out_pdf, subject=f"Ajuste de red · {len(result.points)} puntos")


def _tol(fixed_m: float | None, ppm: float) -> str:
    return "no se evalúa" if fixed_m is None else f"{num(fixed_m * 1000, 0)} mm + {num(ppm, 1)} ppm"


def _solution(s: Solution) -> str:
    return {Solution.FIXED: "fija", Solution.FLOAT: "flotante", Solution.SINGLE: "autónoma"}.get(s, "sin solución")


def _verdict(a: NetworkAdjustment | None) -> str:
    if a is None:
        return "—"
    return (f"σ0 {num(a.sigma0, 3)}, χ² {num(a.chi2, 2)} ≤ {num(a.chi2_upper, 2)}: pasa" if a.passed else
            f"σ0 {num(a.sigma0, 3)}, χ² {num(a.chi2, 2)} > {num(a.chi2_upper, 2)}: NO pasa")


def _reasons(result: NetworkStaticResult) -> list[str]:
    net = result.network
    out = []
    for title, a in (("Mínimamente restringido", net.minimal), ("Restringido", net.constrained)):
        if a is not None and not a.passed:
            out.append(f"{title}: {_verdict(a)}" + (f"; señaladas: {', '.join(a.flagged)}" if a.flagged else ""))
    if net.inconsistent_control:
        out.append("Fijas que no concuerdan con las demás: " + ", ".join(net.inconsistent_control) + ".")
    return out


def _adjustment_section(r: Report, title: str, a: NetworkAdjustment, result: NetworkStaticResult) -> None:
    r.h1(title)
    name = "τ" if a.test == "tau" else "w"
    r.fields([("Datum", a.datum), ("Observaciones", str(a.observations)), ("Incógnitas", str(a.unknowns)),
              ("Restricciones", str(a.constraints)), ("Grados de libertad", str(a.degrees_of_freedom)),
              ("σ0 a posteriori", num(a.sigma0, 3)), ("χ²", num(a.chi2, 3)),
              ("Intervalo al 95 %", f"{num(a.chi2_lower, 2)} – {num(a.chi2_upper, 2)}"),
              ("Prueba χ²", "pasa" if a.passed else "no pasa"),
              (f"Crítico de {name}", num(a.critical_value, 2)),
              ("Escala de covarianzas", "sin escalar" if a.scale_factor == 1.0 else
               f"×{num(a.scale_factor, 3)} (σ0 sin escalar {num(a.sigma0_unscaled, 3)})")], columns=2,
             label_width=110)
    for w in a.warnings:
        r.para(text(w), "small")
    r.h2("Residuos (observado − ajustado), de mayor a menor estadístico")
    rows, hl = [], {}
    for res in a.residuals:
        v, s, red = res.residual_enu, res.standardized_enu, res.redundancy_enu
        rows.append([res.vector_id + (" (fija)" if res.is_constraint else ""), _smm(v.e), _smm(v.n), _smm(v.u),
                     f"{signed(s.e, 2)} / {signed(s.n, 2)} / {signed(s.u, 2)}",
                     f"{num(red.e, 2)} / {num(red.n, 2)} / {num(red.u, 2)}", "sí" if res.flagged else ""])
        if res.flagged:
            hl[len(rows) - 1] = estilo.SEMANTIC["warning_bg"]
    r.table(["Línea", "vE (mm)", "vN (mm)", "vU (mm)", f"{name} E / N / U", "Redundancia E/N/U", "Marcada"], rows,
            widths=[120, 42, 42, 42, 100, 90, CONTENT_WIDTH - 436], right={1, 2, 3}, highlight=hl)
    r.h2("Puntos y elipses absolutas al 95 %")
    rows = []
    for p in a.points:
        shift = p.shift_enu
        rows.append([p.id, KIND_TEXT.get(p.kind, p.kind), f"{_mm(p.sigma_enu.e)} / {_mm(p.sigma_enu.n)} / "
                     f"{_mm(p.sigma_enu.u)}", f"{_mm(p.ellipse95[0])} × {_mm(p.ellipse95[1])}",
                     num(p.ellipse95[2], 0), _mm(p.sigma_u95_m),
                     "—" if shift is None else f"{_smm(shift.e)} / {_smm(shift.n)} / {_smm(shift.u)}"])
    r.table(["Punto", "Tipo", "σ E/N/U (mm)", "Elipse 95 % (mm)", "Az. (°)", "U 95 % (mm)",
             "Ajustada − coordenada (mm)"], rows, widths=[50, 60, 80, 70, 35, 50, CONTENT_WIDTH - 345],
            right={4, 5})
    if a.relative:
        r.h2("Elipses relativas al 95 %")
        rows = [[f"{e.from_id} – {e.to_id}", num(e.distance_m / 1000, 3), _mm(e.semi_major_95_m),
                 _mm(e.semi_minor_95_m), num(e.azimuth_deg, 0), _mm(e.sigma_u95_m), num(e.ppm_95, 3)]
                for e in a.relative]
        r.table(["Par", "Distancia (km)", "Semieje mayor (mm)", "Semieje menor (mm)", "Az. (°)", "U 95 % (mm)",
                 "ppm"], rows, widths=[90, 60, 70, 70, 40, 60, CONTENT_WIDTH - 390], right={1, 2, 3, 4, 5, 6})


def network_plan(result: NetworkStaticResult) -> bytes:
    """El esquema de la red en ENU del primer punto (km), con las líneas por su papel y las elipses del ajuste final
    aumentadas para verse."""
    from . import graficas
    graficas._setup()
    import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse
    from ..core.geodesy import ecef_delta_to_enu
    net = result.network
    final = net.constrained or net.minimal
    positions = {p.id: p for p in final.points}
    origin = next(iter(positions.values())).position
    o = (origin.x, origin.y, origin.z)

    def en(p):
        e, n, _ = ecef_delta_to_enu(o, (p.x - o[0], p.y - o[1], p.z - o[2]))
        return e / 1000.0, n / 1000.0

    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    for v in net.vectors:
        if v.from_id not in positions or v.to_id not in positions:
            continue
        a, b = en(positions[v.from_id].position), en(positions[v.to_id].position)
        style = ":" if not v.use else "--" if v.trivial else "-"
        color = estilo.MUTED if (not v.use or v.trivial) else estilo.PRIMARY
        ax.plot([a[0], b[0]], [a[1], b[1]], style, color=color, linewidth=0.9, zorder=1)
    spans = [en(p.position) for p in positions.values()]
    size = max(1e-3, max(max(x for x, _ in spans) - min(x for x, _ in spans),
                         max(y for _, y in spans) - min(y for _, y in spans)))
    biggest = max((p.ellipse95[0] for p in positions.values()), default=0.0)
    scale = 0.04 * size / biggest if biggest > 0 else 0.0
    for p in positions.values():
        x, y = en(p.position)
        fixed = p.kind in ("fixed", "datum", "weighted")
        ax.scatter([x], [y], marker="^" if fixed else "o", s=40, color=estilo.CHART[3] if fixed else estilo.CHART[0],
                   zorder=3)
        ax.annotate(p.id, (x, y), textcoords="offset points", xytext=(5, 5), fontsize=7)
        if scale and p.ellipse95[0] > 0:
            ax.add_patch(Ellipse((x, y), 2 * p.ellipse95[0] * scale, 2 * p.ellipse95[1] * scale,
                                 angle=90.0 - p.ellipse95[2], fill=False, color=estilo.CHART[1], linewidth=0.8,
                                 zorder=2))
    if scale:
        ax.set_title(f"Elipses al 95 % exageradas: 1 mm del semieje se dibuja como {scale / 1000:.2f} km",
                     fontsize=8)
    ax.set_xlabel("Este (km)")
    ax.set_ylabel("Norte (km)")
    ax.set_aspect("equal", adjustable="datalim")
    return graficas._png(fig)
