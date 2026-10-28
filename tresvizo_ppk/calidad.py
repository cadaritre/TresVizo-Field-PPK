"""Control de calidad del proyecto: tolerancias, puntos de control y ocupaciones repetidas (como los criterios de
Trimble Business Center y Leica Infinity).

- `tolerances_for_order(orden, tipo)`: las tolerancias de un orden de exactitud de la NT EEP (tabla 1).
- `evaluate_points(resultado, tolerancias, control, elecciones, sistema)`: agrupa las ocupaciones del mismo nombre
  (media ponderada o la elegida), compara contra el control y marca cada punto «pasa», «revisar» o «falla».
- `evaluate_static(resultado, tolerancias, control, sistema)`: cada línea base y el vértice.
- `evaluate_network(resultado, tolerancias, control, sistema)`: cada línea, cada bucle y cada punto nuevo de la red.
- `read_control(ruta, orden)`: el CSV de control (PNEZD, PENZD o NXYZD), con el lector de la localización.

No toca el cálculo: es una evaluación de lo que ya salió, que se vuelve a hacer al cambiar las tolerancias, el control
o la ocupación elegida, sin volver a procesar. Se compara la cifra que se imprime (milímetros, décimas), para que el
informe nunca diga «0.020 > 0.020».

Por qué estas cifras por omisión (docs/estado.md, «Tolerancias»): del orden L de la tabla 1 de la NT EEP,
- σH máx. = L / (1.2238·√2): con σE = σN, CEP95 = 1.2238 (σN + σE) = L (art. 6 y 8);
- σV máx. = 2L / 1.96: EPV = 1.96 σV = 2L (art. 7; la vertical del GNSS es del doble de la horizontal);
- diferencia entre ocupaciones y contra control: L en planta y 2L en la cota;
- cierre de bucle de la red: L + 1 ppm en planta y 2L + 2 ppm en la cota (de la longitud del bucle; la NT EEP no fija
  cifra);
- % de épocas fijas 95 %, cociente 3, PDOP 6 y duración mínima de 30 s (PPK) o 1 h (estático), que no dependen del
  orden. Las σ se truncan al milímetro, para no pasar del orden.
"""
from __future__ import annotations

import math
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np

from .core.geodesy import covariance_ecef_to_enu, ecef_delta_to_enu, ecef_to_geodetic, enu_rotation
from .core.points import SIGMA_FLOOR
from .model import (CoordinateChoice, ControlCoordinate, ControlResidual, Ecef, Enu, OccupationGroup, PointQuality,
                    PointsResult, ProcessedPoint, ProjectTolerances, QualityCheck, QualityReport, QualityStatus,
                    Solution, StaticResult, BaselineQuality, TransformedCoordinate, NetworkStaticResult)
from .report.calculos import ACCURACY_ORDERS

LEVELS = ("pass", "review", "fail")
LEVEL_TEXT = {"pass": "Pasa", "review": "Revisar", "fail": "Falla", "na": "Sin evaluar"}
LEVEL_SYMBOL = {"pass": "✓", "review": "▲", "fail": "✕", "na": "–"}
# En el PDF, los que traen Arial, Segoe UI y DejaVu Sans (ninguna de las dos primeras trae ✓ ni ✕)
PDF_SYMBOL = {"pass": "√", "review": "▲", "fail": "×", "na": "–"}
ORDERS = tuple(name for _, name in ACCURACY_ORDERS)
DEFAULT_ORDER = "5 centímetros"
DEFAULT_DURATION_S = {"points": 30.0, "static": 3600.0, "network": 3600.0}
CONTROL_ORDERS = ("pnezd", "penzd", "nxyzd")
EPS = 1e-9

# Qué se muestra de cada criterio: nombre corto, unidades y decimales con que se imprime y se compara.
CRITERIA = {
    "sigma_h": ("σH", "m", 3), "sigma_v": ("σV", "m", 3), "fixed": ("fijas", "%", 1), "ratio": ("cociente", "", 1),
    "pdop": ("PDOP", "", 1), "duration": ("duración", "s", 0), "repeat_h": ("entre ocupaciones ΔH", "m", 3),
    "repeat_v": ("entre ocupaciones ΔV", "m", 3), "control_h": ("control ΔH", "m", 3),
    "control_v": ("control ΔV", "m", 3),
}


# --- Tolerancias ------------------------------------------------------------------------------------------------------

def _order_limit_m(order: str) -> float | None:
    for limit_mm, name in ACCURACY_ORDERS:
        if name == order:
            return limit_mm / 1000.0
    return None


def _floor_mm(value: float) -> float:
    return math.floor(value * 1000.0 + 1e-6) / 1000.0


def tolerances_for_order(order: str = DEFAULT_ORDER, kind: str = "points") -> ProjectTolerances:
    """Las tolerancias que salen de un orden de exactitud de la tabla 1 de la NT EEP («2 centímetros»)."""
    limit = _order_limit_m(order)
    if limit is None:
        raise ValueError(f"Orden de exactitud desconocido: «{order}».")
    return ProjectTolerances(order=order, max_sigma_h_m=_floor_mm(limit / (1.2238 * math.sqrt(2.0))),
                             max_sigma_v_m=_floor_mm(2.0 * limit / 1.96), min_fixed_percent=95.0, min_ratio=3.0,
                             max_pdop=6.0, min_duration_s=DEFAULT_DURATION_S.get(kind, 30.0),
                             max_repeat_h_m=limit, max_repeat_v_m=2.0 * limit, max_control_h_m=limit,
                             max_control_v_m=2.0 * limit, max_loop_h_m=limit, max_loop_h_ppm=1.0,
                             max_loop_v_m=2.0 * limit, max_loop_v_ppm=2.0)


def default_tolerances(kind: str = "points") -> ProjectTolerances:
    return tolerances_for_order(DEFAULT_ORDER, kind)


def matches_order(t: ProjectTolerances, kind: str) -> bool:
    """Las tolerancias son exactamente las de su orden (si no, se escribieron a mano)."""
    if not t.order or _order_limit_m(t.order) is None:
        return False
    ref = tolerances_for_order(t.order, kind)
    return replace(t, fail_factor=ref.fail_factor) == ref


def tolerance_issues(t: ProjectTolerances) -> list[str]:
    """Qué cifra no tiene sentido (vacío si todas sirven)."""
    found = []
    for name, label, low, high in (("max_sigma_h_m", "σH máxima", 0.0005, 100.0),
                                   ("max_sigma_v_m", "σV máxima", 0.0005, 100.0),
                                   ("min_fixed_percent", "épocas fijas mínimas", 0.0, 100.0),
                                   ("min_ratio", "cociente mínimo", 1.0, 1000.0), ("max_pdop", "PDOP máxima", 1.0, 99.0),
                                   ("min_duration_s", "duración mínima", 0.0, 7 * 86400.0),
                                   ("max_repeat_h_m", "diferencia en planta entre ocupaciones", 0.0005, 100.0),
                                   ("max_repeat_v_m", "diferencia en cota entre ocupaciones", 0.0005, 100.0),
                                   ("max_control_h_m", "diferencia en planta contra control", 0.0005, 100.0),
                                   ("max_control_v_m", "diferencia en cota contra control", 0.0005, 100.0),
                                   ("max_loop_h_m", "cierre de bucle en planta", 0.0005, 100.0),
                                   ("max_loop_v_m", "cierre de bucle en cota", 0.0005, 100.0),
                                   ("max_loop_h_ppm", "ppm del cierre de bucle en planta", 0.0, 1000.0),
                                   ("max_loop_v_ppm", "ppm del cierre de bucle en cota", 0.0, 1000.0)):
        value = getattr(t, name)
        if value is not None and not (math.isfinite(value) and low <= value <= high):
            found.append(f"La tolerancia «{label}» va de {low:g} a {high:g}.")
    if not (math.isfinite(t.fail_factor) and 1.0 <= t.fail_factor <= 10.0):
        found.append("El factor de falla va de 1 a 10.")
    return found


# --- Un criterio ------------------------------------------------------------------------------------------------------

def _printed(value: float, decimals: int) -> Decimal:
    """La cifra como se imprime (redondeo a la mitad hacia arriba, como `report.calculos.millimeters`)."""
    return Decimal(repr(float(value))).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def value_text(criterion: str, value: float | None) -> str:
    if value is None:
        return "—"
    _, unit, decimals = CRITERIA[criterion]
    if criterion == "duration":
        return duration_text(value)
    text = f"{_printed(value, decimals):f}"
    return f"{text} %" if unit == "%" else text


def duration_text(seconds: float) -> str:
    s = int(_printed(seconds, 0))
    if s < 120:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s" if s % 60 else f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def check(criterion: str, value: float | None, limit: float | None, kind: str, fail_factor: float) -> QualityCheck | None:
    """`kind` «max» (no debe pasar de `limit`) o «min» (no debe quedar debajo). None si el criterio está apagado."""
    if limit is None:
        return None
    label, _, decimals = CRITERIA[criterion]
    if value is None or not math.isfinite(value):
        return QualityCheck(criterion, "na", None, limit, f"{label}: sin dato")
    v, lim = _printed(value, decimals), _printed(limit, decimals)
    factor = Decimal(repr(float(fail_factor)))
    if kind == "max":
        level = "pass" if v <= lim else ("review" if v <= lim * factor else "fail")
        sign = "≤" if level == "pass" else ">"
    else:
        level = "pass" if v >= lim else ("review" if v * factor >= lim else "fail")
        sign = "≥" if level == "pass" else "<"
    return QualityCheck(criterion, level, float(value), float(limit),
                        f"{label} {value_text(criterion, value)} {sign} {value_text(criterion, limit)}")


def status_of(checks: list[QualityCheck | None]) -> QualityStatus:
    real = [c for c in checks if c is not None]
    worst = max((LEVELS.index(c.level) for c in real if c.level in LEVELS), default=0)
    bad = [c.text for c in real if c.level in ("review", "fail")]
    return QualityStatus(level=LEVELS[worst], reason="; ".join(bad), checks=real)


def worst(levels) -> str:
    return LEVELS[max((LEVELS.index(lv) for lv in levels if lv in LEVELS), default=0)]


def status_text(status: QualityStatus) -> str:
    """«✕ Falla: σV 0.034 > 0.020», para la tabla, el informe y la exportación."""
    head = f"{LEVEL_SYMBOL[status.level]} {LEVEL_TEXT[status.level]}"
    return f"{head}: {status.reason}" if status.reason else head


# --- PPK de puntos ----------------------------------------------------------------------------------------------------

def group_key(name: str) -> str:
    """El mismo punto: mismo nombre sin distinguir mayúsculas ni espacios de los extremos (como los pares de la
    localización)."""
    return name.strip().casefold()


def _differential(p: ProcessedPoint) -> bool:
    return p.position is not None and p.sigma_enu is not None and p.solution in (Solution.FIXED, Solution.FLOAT)


def _duration(p: ProcessedPoint) -> float:
    return max(0.0, (p.point.end - p.point.start).total_seconds())


def _xyz(p: Ecef) -> np.ndarray:
    return np.array([p.x, p.y, p.z])


def _floor_sigma(solution: Solution, distance_m: float | None) -> tuple[float, float]:
    floor = SIGMA_FLOOR.get(solution)
    if floor is None or distance_m is None:
        return 0.0, 0.0
    return floor[0][0] + floor[0][1] * distance_m, floor[1][0] + floor[1][1] * distance_m


def _mean_point(occupations: list[ProcessedPoint]) -> tuple[ProcessedPoint, list[Enu]]:
    """La media ponderada (1/σ² por componente, en el ENU de la primera) y cada ocupación menos la media.

    La σ de la media es la del promedio ponderado, pero nunca menos que la mínima de un punto del motor
    (`core.points.SIGMA_FLOOR` a su distancia): las ocupaciones del mismo día con la misma base comparten la
    coordenada de la base, las órbitas y la troposfera, y eso no se reduce al promediar."""
    first = occupations[0]
    origin = _xyz(first.position)
    lat, lon, _ = ecef_to_geodetic(*origin)
    rot = enu_rotation(lat, lon)
    enus = np.array([rot @ (_xyz(p.position) - origin) for p in occupations])
    sig = np.array([[max(p.sigma_enu.e, 1e-4), max(p.sigma_enu.n, 1e-4), max(p.sigma_enu.u, 1e-4)]
                    for p in occupations])
    w = 1.0 / sig ** 2
    mean = (w * enus).sum(axis=0) / w.sum(axis=0)
    sigma = 1.0 / np.sqrt(w.sum(axis=0))
    solution = Solution.FIXED if all(p.solution == Solution.FIXED for p in occupations) else Solution.FLOAT
    distances = [p.distance_to_base_m for p in occupations if p.distance_to_base_m is not None]
    distance = sum(distances) / len(distances) if distances else None
    fh, fv = _floor_sigma(solution, distance)
    sigma = np.maximum(sigma, [fh, fh, fv])
    xyz = origin + rot.T @ mean
    frame, epoch = first.position.frame, first.position.epoch
    position = Ecef(float(xyz[0]), float(xyz[1]), float(xyz[2]), frame, epoch)
    delta = None
    fields = [p.point.field_position for p in occupations]
    if all(f is not None for f in fields):
        f = np.mean([_xyz(f) for f in fields], axis=0)
        delta = Enu(*ecef_delta_to_enu(tuple(f), xyz - f))
    heights = {p.height_used_m for p in occupations}
    ratios = [p.ratio for p in occupations if p.ratio is not None]
    pdops = [p.pdop_max for p in occupations if p.pdop_max is not None]
    n = len(occupations)
    point = replace(first, solution=solution, position=position, sigma_enu=Enu(*(float(v) for v in sigma)),
                    epochs_fixed=sum(p.epochs_fixed for p in occupations),
                    epochs_total=sum(p.epochs_total for p in occupations),
                    epochs_float=sum(p.epochs_float for p in occupations), distance_to_base_m=distance,
                    delta_from_field_enu=delta, antenna_position=None,
                    height_used_m=heights.pop() if len(heights) == 1 else None,
                    ratio=min(ratios) if ratios else None, pdop_max=max(pdops) if pdops else None,
                    message=f"promedio ponderado de {n} ocupaciones")
    deltas = [Enu(*(float(v) for v in (e - mean))) for e in enus]
    return point, deltas


def _point_checks(p: ProcessedPoint, t: ProjectTolerances, durations: list[float]) -> list[QualityCheck | None]:
    f = t.fail_factor
    if p.position is None or p.sigma_enu is None:
        return [QualityCheck("solution", "fail", text="sin posición" + (f" ({p.message})" if p.message else ""))]
    if p.solution not in (Solution.FIXED, Solution.FLOAT):
        return [QualityCheck("solution", "fail", text="sin solución diferencial")]
    s = p.sigma_enu
    fixed = 100.0 * p.epochs_fixed / p.epochs_total if p.epochs_total else 0.0
    return [check("sigma_h", math.hypot(s.e, s.n), t.max_sigma_h_m, "max", f),
            check("sigma_v", s.u, t.max_sigma_v_m, "max", f),
            check("fixed", fixed, t.min_fixed_percent, "min", f),
            check("ratio", p.ratio, t.min_ratio, "min", f) if p.solution == Solution.FIXED else None,
            check("pdop", p.pdop_max, t.max_pdop, "max", f),
            check("duration", min(durations) if durations else None, t.min_duration_s, "min", f)]


def _group(name: str, occupations: list[ProcessedPoint], choice: int,
           t: ProjectTolerances) -> tuple[OccupationGroup, ProcessedPoint, str]:
    """Las ocupaciones de un nombre: cuáles entran (las fijas si hay; si no, las flotantes), su media, la mayor
    diferencia entre dos y el resultado que queda (la media o la elegida)."""
    usable = [i for i, p in enumerate(occupations) if _differential(p)]
    fixed = [i for i in usable if occupations[i].solution == Solution.FIXED]
    used = fixed or usable
    group = OccupationGroup(name=name, occupations=list(occupations), choice=choice, used=used,
                            deltas_enu=[None] * len(occupations))
    n = len(occupations)
    checks: list[QualityCheck | None] = []
    if len(used) >= 2:
        mean, deltas = _mean_point([occupations[i] for i in used])
        for i, d in zip(used, deltas):
            group.deltas_enu[i] = d
        pairs = [(a, b) for k, a in enumerate(used) for b in used[k + 1:]]
        diffs = [_xyz(occupations[a].position) - _xyz(occupations[b].position) for a, b in pairs]
        rot = enu_rotation(*ecef_to_geodetic(*_xyz(mean.position))[:2])
        enu = [rot @ d for d in diffs]
        group.spread_h_m = max(math.hypot(d[0], d[1]) for d in enu)
        group.spread_v_m = max(abs(d[2]) for d in enu)
        checks += [check("repeat_h", group.spread_h_m, t.max_repeat_h_m, "max", t.fail_factor),
                   check("repeat_v", group.spread_v_m, t.max_repeat_v_m, "max", t.fail_factor)]
        if len(used) < len(usable):
            mean.message += f" fijas (sin {len(usable) - len(used)} flotante{'s' if len(usable) - len(used) > 1 else ''})"
    else:
        mean = None
        checks.append(QualityCheck("repeat_h", "review", text=f"{n} ocupaciones y solo {len(used)} con solución "
                                                              "diferencial: no hay con qué comparar"))
    group.status = status_of(checks)
    if 1 <= choice <= n:
        chosen = occupations[choice - 1]
        note = f"ocupación {choice} de {n} (elegida)"
        effective = replace(chosen, message="; ".join(x for x in (note, chosen.message) if x))
    elif mean is not None:
        group.choice = 0
        note = f"promedio de {len(used)} ocupaciones"
        effective = mean
    else:
        # sin media posible: la que tiene solución (o la primera)
        index = used[0] if used else 0
        group.choice = index + 1
        note = f"ocupación {index + 1} de {n} (la única con solución)" if used else f"{n} ocupaciones sin solución"
        effective = replace(occupations[index], message="; ".join(x for x in (note, occupations[index].message) if x))
    return group, effective, note


def evaluate_points(result: PointsResult, tolerances: ProjectTolerances | None = None,
                    control: list[ControlCoordinate] | None = None, choices: dict[str, int] | None = None,
                    choice: CoordinateChoice | None = None, control_source: str = "") -> QualityReport:
    """La evaluación del PPK. `choices`: nombre → 0 (media ponderada) o k (ocupación k, desde 1)."""
    t = tolerances or default_tolerances("points")
    chosen = {group_key(k): v for k, v in (choices or {}).items()}
    order: list[str] = []
    by_key: dict[str, list[ProcessedPoint]] = {}
    for p in result.points:
        key = group_key(p.point.name)
        if key not in by_key:
            order.append(key)
            by_key[key] = []
        by_key[key].append(p)
    report = QualityReport(tolerances=t, control_source=control_source)
    effective: list[tuple[PointQuality, list[QualityCheck | None]]] = []
    for key in order:
        occupations = by_key[key]
        name = occupations[0].point.name
        if len(occupations) == 1:
            p = occupations[0]
            checks = _point_checks(p, t, [_duration(p)])
            effective.append((PointQuality(name=name, point=p), checks))
            continue
        group, point, note = _group(name, occupations, chosen.get(key, 0), t)
        report.groups.append(group)
        if group.choice == 0:
            durations = [_duration(occupations[i]) for i in group.used]
        else:
            durations = [_duration(occupations[group.choice - 1])]
        checks = _point_checks(point, t, durations) + list(group.status.checks)
        effective.append((PointQuality(name=name, point=point, occupations=len(occupations),
                                       averaged=group.choice == 0, note=note), checks))
    located = [pq.point.position for pq, _ in effective if pq.point.position is not None
               and pq.point.solution != Solution.NONE]
    residuals = _control([(pq.name, pq.point.position) for pq, _ in effective], control or [], t, choice,
                         located[0] if located else result.base.position, report)
    for pq, checks in effective:
        r = residuals.get(group_key(pq.name))
        pq.status = status_of(checks + (list(r.status.checks) if r is not None else []))
        report.points.append(pq)
    report.level = worst(pq.status.level for pq in report.points)
    return report


def effective_points(result: PointsResult) -> list[ProcessedPoint]:
    """El resultado de cada nombre: el de la evaluación si la hay (promedio o la ocupación elegida), si no todos."""
    if result.quality is not None and result.quality.points:
        return [pq.point for pq in result.quality.points if pq.point is not None]
    return list(result.points)


def point_status(result: PointsResult, point: ProcessedPoint) -> QualityStatus | None:
    """El estado del nombre de `point` (None sin evaluación)."""
    if result.quality is None:
        return None
    key = group_key(point.point.name)
    return next((pq.status for pq in result.quality.points if group_key(pq.name) == key), None)


# --- Control ----------------------------------------------------------------------------------------------------------

def fixed_choice(reference: Ecef, choice: CoordinateChoice) -> CoordinateChoice:
    """Una sola cuadrícula: la zona del primer punto (la misma regla que el informe y la exportación)."""
    from .crs import api as crs
    if choice.crs_id not in crs.FAMILIES or choice.zone.strip():
        return choice
    zone = crs.transform(reference, CoordinateChoice(choice.crs_id, "", None)).zone
    return replace(choice, zone=zone)


def _transform(position: Ecef, choice: CoordinateChoice) -> TransformedCoordinate | None:
    from .crs import api as crs
    try:
        return crs.transform(position, choice)
    except Exception:       # noqa: BLE001 - un punto fuera del sistema no tumba la evaluación
        return None


def control_residual(name: str, position: Ecef | None, c: ControlCoordinate, t: ProjectTolerances,
                     measured: TransformedCoordinate | None) -> ControlResidual:
    """Lo medido menos el control, con sus dos criterios (planta y cota)."""
    r = ControlResidual(name=name, control=c)
    notes = []
    checks: list[QualityCheck | None] = []
    if position is None or measured is None:
        r.status = QualityStatus("fail", "control: el punto no tiene posición en el sistema elegido",
                                 [QualityCheck("control_h", "fail", text="control: sin posición")])
        r.note = "sin posición posprocesada"
        return r
    r.northing_m, r.easting_m, r.elevation_m = measured.northing_m, measured.easting_m, measured.orthometric_height_m
    if c.northing_m is not None and c.easting_m is not None:
        if measured.northing_m is None:
            notes.append("el sistema elegido es geográfico: el control en planta necesita uno proyectado")
        else:
            r.delta_n_m = measured.northing_m - c.northing_m
            r.delta_e_m = measured.easting_m - c.easting_m
            checks.append(check("control_h", math.hypot(r.delta_e_m, r.delta_n_m), t.max_control_h_m, "max",
                                t.fail_factor))
    if c.elevation_m is not None:
        if measured.orthometric_height_m is None:
            notes.append("sin cota en el sistema elegido (elige un geoide): la cota de control no se compara")
        else:
            r.delta_h_m = measured.orthometric_height_m - c.elevation_m
            checks.append(check("control_v", abs(r.delta_h_m), t.max_control_v_m, "max", t.fail_factor))
    r.note = "; ".join(notes)
    r.status = status_of(checks)
    return r


def _control(points: list[tuple[str, Ecef | None]], control: list[ControlCoordinate], t: ProjectTolerances,
             choice: CoordinateChoice | None, reference: Ecef, report: QualityReport) -> dict[str, ControlResidual]:
    if not control:
        return {}
    measured = {group_key(name): (name, position) for name, position in points}
    plane = fixed_choice(reference, choice or CoordinateChoice())
    out: dict[str, ControlResidual] = {}
    for c in control:
        key = group_key(c.name)
        if key not in measured:
            report.control_missing.append(c.name)
            continue
        name, position = measured[key]
        r = control_residual(name, position, c, t, _transform(position, plane) if position is not None else None)
        report.control.append(r)
        out[key] = r
    if report.control_missing:
        report.notes.append(f"{len(report.control_missing)} punto(s) de control sin medición del mismo nombre: "
                            + ", ".join(report.control_missing) + ".")
    return out


def read_control(path: str | Path, order: str = "pnezd") -> tuple[list[ControlCoordinate], list[str]]:
    """El CSV de control: con encabezado se lee por nombre de columna; sin él, en el orden elegido (PNEZD, PENZD o
    NXYZD). Los mismos separadores, coma decimal y avisos que el control de la localización."""
    from .crs.localizacion import read_control_csv
    rows, notes = read_control_csv(path, order if order in CONTROL_ORDERS else "pnezd")
    return [ControlCoordinate(r.name, r.northing_m, r.easting_m, r.elevation_m, r.description) for r in rows], notes


# --- Estático ---------------------------------------------------------------------------------------------------------

def _baseline_checks(b, t: ProjectTolerances) -> list[QualityCheck | None]:
    f = t.fail_factor
    if b.solution not in (Solution.FIXED, Solution.FLOAT):
        return [QualityCheck("solution", "fail", text="sin solución diferencial")]
    p = b.rover_position
    cov = covariance_ecef_to_enu((p.x, p.y, p.z), np.array(b.covariance_ecef))
    se, sn, su = (math.sqrt(max(0.0, float(cov[i, i]))) for i in range(3))
    return [check("sigma_h", math.hypot(se, sn), t.max_sigma_h_m, "max", f),
            check("sigma_v", su, t.max_sigma_v_m, "max", f),
            check("ratio", b.ratio, t.min_ratio, "min", f) if b.solution == Solution.FIXED else None,
            check("pdop", b.pdop_max, t.max_pdop, "max", f),
            check("duration", (b.end - b.start).total_seconds(), t.min_duration_s, "min", f)]


def evaluate_static(result: StaticResult, tolerances: ProjectTolerances | None = None,
                    control: list[ControlCoordinate] | None = None, choice: CoordinateChoice | None = None,
                    control_source: str = "") -> QualityReport:
    """Cada línea base (σ del vector, cociente, PDOP y duración) y el vértice (σ final, ajuste, líneas usadas y
    control por el nombre del punto)."""
    t = tolerances or default_tolerances("static")
    report = QualityReport(tolerances=t, control_source=control_source)
    excluded = set(result.adjustment.excluded_baselines) if result.adjustment else set()
    failing = []
    for b in result.baselines:
        sid = b.base.id or b.base.name
        status = status_of(_baseline_checks(b, t))
        report.baselines.append(BaselineQuality(sid, status))
        if status.level == "fail" and sid not in excluded:
            failing.append(sid)
    s = result.sigma_enu
    f = t.fail_factor
    checks: list[QualityCheck | None] = [check("sigma_h", math.hypot(s.e, s.n), t.max_sigma_h_m, "max", f),
                                         check("sigma_v", s.u, t.max_sigma_v_m, "max", f)]
    used = [b for b in result.baselines if (b.base.id or b.base.name) not in excluded]
    if used:
        checks.append(check("duration", max((b.end - b.start).total_seconds() for b in used), t.min_duration_s,
                            "min", f))
    if result.adjustment is not None and not result.adjustment.passed:
        a = result.adjustment
        checks.append(QualityCheck("adjustment", "review", a.chi2, a.chi2_upper,
                                   f"el ajuste no pasa la prueba χ² ({a.chi2:.1f} > {a.chi2_upper:.1f})"))
    if failing:
        checks.append(QualityCheck("baseline", "review", text="falla la línea base con " + ", ".join(failing)))
    residuals = _control([(result.point_name, result.position)], control or [], t, choice, result.position, report)
    r = residuals.get(group_key(result.point_name))
    status = status_of(checks + (list(r.status.checks) if r is not None else []))
    report.points.append(PointQuality(name=result.point_name, status=status))
    # una línea que el ajuste dejó fuera no cuenta para el proyecto (sale con su estado en la tabla de líneas)
    report.level = worst([status.level] + [q.status.level for q in report.baselines if q.base_id not in excluded])
    return report


# --- Estático de varios puntos (red) ------------------------------------------------------------------------------------

def evaluate_network(result: NetworkStaticResult, tolerances: ProjectTolerances | None = None,
                     control: list[ControlCoordinate] | None = None, choice: CoordinateChoice | None = None,
                     control_source: str = "") -> QualityReport:
    """Cada línea (σ del vector, cociente, PDOP, duración y, si el ajuste la señala, su τ o w), cada bucle (su
    tolerancia, ya evaluada por `red.py`) y cada punto nuevo (σ final, el ajuste, sus líneas y bucles y el control)."""
    t = tolerances or default_tolerances("static")
    report = QualityReport(tolerances=t, control_source=control_source)
    network = result.network
    adjustment = (network.constrained or network.minimal) if network is not None else None
    flagged = {r.vector_id: r for r in (adjustment.residuals if adjustment else []) if r.flagged}
    in_use = set()
    if network is not None:
        in_use = {v.id for v in network.vectors if v.use and (not v.trivial or network.options.include_trivial)}
    line_status = {}
    for ln in result.lines:
        checks = _baseline_checks(ln.baseline, t)
        r = flagged.get(ln.id)
        if r is not None:
            name = "τ" if adjustment.test == "tau" else "w"
            checks.append(QualityCheck("adjustment", "review", r.max_standardized, adjustment.critical_value,
                                       f"{name} {r.max_standardized:.1f} > {adjustment.critical_value:.2f} en el "
                                       "ajuste"))
        status = status_of(checks)
        line_status[ln.id] = status
        report.baselines.append(BaselineQuality(ln.id, status))
    loops = network.loops if network is not None else []
    for p in result.points:
        s = p.sigma_enu
        f = t.fail_factor
        checks: list[QualityCheck | None] = [check("sigma_h", math.hypot(s.e, s.n), t.max_sigma_h_m, "max", f),
                                             check("sigma_v", s.u, t.max_sigma_v_m, "max", f)]
        mine = [ln for ln in result.lines if ln.id in in_use and p.name in (ln.from_id, ln.to_id)]
        if mine:
            checks.append(check("duration", max((ln.baseline.end - ln.baseline.start).total_seconds() for ln in mine),
                                t.min_duration_s, "min", f))
        if adjustment is not None and not adjustment.passed:
            checks.append(QualityCheck("adjustment", "review", adjustment.chi2, adjustment.chi2_upper,
                                       f"el ajuste no pasa la prueba χ² ({adjustment.chi2:.1f} > "
                                       f"{adjustment.chi2_upper:.1f})"))
        if p.adjustment == "minimal":
            checks.append(QualityCheck("adjustment", "review", text="sin fija en su parte de la red"))
        bad = [ln.id for ln in mine if line_status[ln.id].level == "fail"]
        if bad:
            checks.append(QualityCheck("baseline", "review", text="falla la línea " + ", ".join(bad)))
        open_loops = [lp for lp in loops if p.name in lp.points and lp.status.level != "pass"]
        if open_loops:
            worst_loop = max(open_loops, key=lambda lp: LEVELS.index(lp.status.level))
            checks.append(QualityCheck("loop", "review", text="bucle " + " → ".join(worst_loop.points)
                                       + f": {worst_loop.status.reason}"))
        report.points.append(PointQuality(name=p.name, status=status_of(checks), occupations=p.occupations,
                                          averaged=p.occupations > 1,
                                          note=f"{p.occupations} ocupaciones" if p.occupations > 1 else ""))
    if result.points:
        residuals = _control([(p.name, p.position) for p in result.points], control or [], t, choice,
                             result.points[0].position, report)
        for pq in report.points:
            r = residuals.get(group_key(pq.name))
            if r is not None:
                pq.status = status_of(list(pq.status.checks) + list(r.status.checks))
    report.level = worst([pq.status.level for pq in report.points]
                         + [q.status.level for q in report.baselines if q.base_id in in_use]
                         + [lp.status.level for lp in loops])
    return report
