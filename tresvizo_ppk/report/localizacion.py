"""La sección «Localización» de los informes: con qué se pasó de la cuadrícula a las coordenadas locales del cliente.

Lo que dicen las apps TresVizo Field en su hoja y en la cabecera de sus exportaciones (`docs/localizacion.md` §3 y §4):
la cuadrícula y la altura base, los métodos pedido y efectivo, escala, giro, traslación y plano con su precisión, la
tabla de residuos de **todos** los pares (los que no entran son comprobaciones) con el residuo sin el par y el
sospechoso señalado, los avisos y de dónde salió la localización. Un informe en locales que no dijera esto corregiría
en silencio.
"""
from __future__ import annotations

from ..crs import localizacion as L
from ..errors import UserError
from ..model import CoordinateChoice, OFFICIAL_FRAME
from .documento import Report
from .estilo import SEMANTIC, num


def _signed(x: float | None) -> str:
    return "—" if x is None else L.signed_number(x)


def localization_section(r: Report, choice: CoordinateChoice, frame: str = OFFICIAL_FRAME) -> None:
    record = choice.localization
    if record is None:
        return
    r.h1("Localización")
    try:
        computed = L.compute(record, choice.geoid_id, frame)
    except UserError as error:
        r.callout("error", "La localización en uso no se puede calcular", [str(error), L.NOT_COMPUTABLE_IN_USE])
        return
    sol, options = computed.solution, computed.options
    applied = record.applied_at.strftime("%Y-%m-%d %H:%M UTC") if record.applied_at else "—"
    height = ("elipsoidal" if not choice.geoid_id else
              f"sobre el geoide {choice.geoid_id.upper()} del sistema elegido (H = h − N)")
    pairs = [("Coordenadas del resultado", L.system_label(computed.frame.name, record.saved_system_name)),
             ("Origen", record.origin or "—"), ("Aplicada", applied),
             ("Altura base", height)]
    declared_geoid = record.height_base_geoid_id if record.height_base_kind == "orthometric" else ""
    if (declared_geoid or None) != (choice.geoid_id or None):
        then = f"sobre {declared_geoid.upper()}" if declared_geoid else "elipsoidales"
        now = f"usa {choice.geoid_id.upper()}" if choice.geoid_id else "usa alturas elipsoidales"
        pairs.append(("Aviso de alturas", f"Se aplicó con alturas {then} y el sistema ahora {now}: los parámetros "
                                          "ya se recalcularon así."))
    pairs += L.parameter_lines(computed)
    pairs.append(("Tolerancias", f"{L.length(options.horizontal_tolerance_m)} en planta, "
                                 f"{L.length(options.vertical_tolerance_m)} en cota"))
    r.fields(pairs, columns=1, label_width=170)
    notes = L.horizontal_notes(sol) + L.vertical_notes(sol)
    if computed.scale_match:
        notes.append(L.scale_match_text(computed.scale_match))
    if notes:
        r.callout("info", "Notas", notes)

    r.h2("Residuos por par (control menos calculado)")
    rows, highlight = [], {}
    h_suspect, v_suspect = sol.horizontal_suspect_index, sol.vertical_suspect_index
    for res, pair in zip(sol.residuals, record.pairs):
        flag_h = sol.flags_horizontal_suspect(res.index, options.horizontal_tolerance_m)
        flag_v = sol.flags_vertical_suspect(res.index, options.vertical_tolerance_m)
        use = ("H" if pair.uses_horizontal else "") + ("V" if pair.uses_vertical else "")
        role = "en uso" if res.used_h or res.used_v else "comprobación"
        if (res.used_h or res.used_v) and ((pair.uses_horizontal and not res.used_h) or
                                           (pair.uses_vertical and not res.used_v)):
            role = "en uso (parcial)"
        mark = []
        if flag_h or flag_v:
            mark.append("sospechoso")
        elif res.index in (h_suspect, v_suspect):
            mark.append("el mayor")
        rows.append([res.name, use or "—", role, _signed(res.dE), _signed(res.dN), num(res.dH, 3),
                     _signed(res.dZ), num(res.loo_dH, 3), _signed(res.loo_dZ), ", ".join(mark)])
        out_h = res.dH is not None and res.dH > options.horizontal_tolerance_m
        out_v = res.dZ is not None and abs(res.dZ) > options.vertical_tolerance_m
        if flag_h or flag_v:
            highlight[len(rows) - 1] = SEMANTIC["error_bg"]
        elif out_h or out_v:
            highlight[len(rows) - 1] = SEMANTIC["warning_bg"]
    r.table(["Par", "Usa", "Papel", "dE (m)", "dN (m)", "dH (m)", "dZ (m)", "Sin el par dH", "Sin el par dZ", ""],
            rows, widths=[50, 30, 62, 50, 50, 46, 50, 54, 54, 56], right={3, 4, 5, 6, 7, 8}, highlight=highlight)
    r.para("H: entra al ajuste en planta; V: en cota. Un par sin H ni V es una comprobación independiente. «Sin el par»: "
           "el residuo que tendría si la localización se calculara sin él, con los mismos métodos (vacío si el método "
           "cambiaría). Se señala al sospechoso cuando hay 4 pares o más en ese ajuste y su residuo sin él pasa de la "
           "tolerancia.", "small")
    warnings = [L.warning_message(w, options) for w in computed.warnings]
    if warnings:
        r.callout("warning", "Avisos de la localización", warnings)
    r.h2("Pares: lo medido y el control")
    prow = []
    for pair, obs in zip(record.pairs, computed.observations):
        m, c = pair.measured, pair.control
        prow.append([pair.name, m.name or "—", f"{m.latitude_deg:.8f}", f"{m.longitude_deg:.8f}",
                     num(m.ellipsoidal_height_m, 3), num(obs.n, 3), num(obs.e, 3), num(c.northing_m, 3),
                     num(c.easting_m, 3), num(c.elevation_m, 3)])
    r.table(["Par", "Medido", "Latitud", "Longitud", "h (m)", "n base (m)", "e base (m)", "N local (m)",
             "E local (m)", "Z local (m)"], prow, widths=[36, 40, 56, 68, 46, 60, 56, 58, 54, 50],
            right={2, 3, 4, 5, 6, 7, 8, 9})
    r.para("Latitud, longitud y h: lo medido, en el marco de la base. n y e: en la cuadrícula base. Los parámetros se "
           "recalculan siempre de estos pares, como en las apps TresVizo Field.", "small")
