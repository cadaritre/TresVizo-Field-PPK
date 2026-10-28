"""Estático de varios puntos: las ocupaciones de uno o varios receptores contra las estaciones de una red CORS y entre
sí, y el ajuste de red de todas las líneas (`red.py`), como un levantamiento de control en TBC o Infinity.

**Ocupaciones.** Cada receptor (un MeridianV/VI o un RINEX) ocupó uno o varios puntos: su grabación entera es una
ocupación o, con el CSV de TresVizo Field, cada fila de campo de al menos `MIN_OCCUPATION_S` es una (las más cortas
son puntos de PPK y se avisan). Ocupaciones del mismo nombre son el mismo punto (se ajustan juntas).

**Líneas.** Se procesa cada ocupación contra cada estación con al menos `MIN_COMMON_S` de datos comunes («base»), y
cada par de ocupaciones simultáneas de receptores distintos y puntos distintos («rover»). Las líneas entre
ocupaciones necesitan una coordenada del punto de inicio: la preliminar de sus líneas a las estaciones (el ajuste de
un punto de `adjust.py`; error ≪ 1 ppm en el vector). Las simultáneas de una misma sesión no son todas independientes:
`red.py` marca las triviales.

**Ajuste.** Las estaciones entran como fijas duras, ponderadas con la σ de su coordenada o solo de comprobación
(`NetworkFixChoice`), y el usuario puede dejar fuera una línea o decidir si es trivial (`line_choices`).
`readjust` vuelve a ajustar con otra elección sin volver a procesar. La coordenada final de un punto nuevo es la del
restringido (o la del mínimamente restringido si su parte de la red no tiene fijas, y se avisa).
"""
from __future__ import annotations

import math
import re
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np

from ..errors import Cancelled, UserError
from ..model import (Ecef, Enu, Ephemeris, NetworkConstraint, NetworkFinalPoint, NetworkFixChoice, NetworkLine,
                     NetworkOccupation, NetworkOptions, NetworkStaticResult, NetworkVector, ObservationSet,
                     ProcessingOptions, ProjectTolerances, Station)
from . import adjust, antex, intermedios, networks, precise, quality, red, sesion, tools
from .baseline import process_baseline
from .geodesy import covariance_enu_to_ecef
from .observations import trim
from .static import _baseline_pdop

MIN_COMMON_S = 600.0            # datos comunes mínimos para procesar una línea (10 min)
MIN_OCCUPATION_S = 600.0        # una fila del CSV más corta es un punto de PPK, no una ocupación estática
WEIGHTED_DEFAULT = Enu(0.005, 0.005, 0.010)   # σ de una fija ponderada cuya red no publica la de su coordenada


def occupation_key(o: NetworkOccupation, index: int) -> str:
    return f"{o.name}@{o.receiver}#{index + 1}"


def _overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    return (min(a_end, b_end) - max(a_start, b_start)).total_seconds()


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "linea"


def _xyz(p: Ecef) -> np.ndarray:
    return np.array([p.x, p.y, p.z], dtype=float)


def _station_cov(station: Station) -> np.ndarray:
    s = station.sigma_enu
    return covariance_enu_to_ecef(_xyz(station.position), np.diag([s.e ** 2, s.n ** 2, s.u ** 2]))


def occupations_from_field(points, receiver: str, rover: ObservationSet | None = None
                           ) -> tuple[list[NetworkOccupation], list[str]]:
    """Las ocupaciones estáticas de las filas del CSV de TresVizo Field (`FieldPoint`) de un receptor, con su altura
    (marca → ARP, como el PPK de puntos), y los avisos de las que no sirven."""
    from .points import _height_for
    out, notes = [], []
    for k, p in enumerate(points):
        seconds = (p.end - p.start).total_seconds()
        if seconds < MIN_OCCUPATION_S:
            notes.append(f"{p.name}: la ocupación de {seconds:.0f} s es de PPK, no estática (mínimo "
                         f"{MIN_OCCUPATION_S / 60:.0f} min); no entra a la red.")
            continue
        height, note = _height_for(p, rover) if rover is not None else (p.total_height_m, "")
        out.append(NetworkOccupation(name=p.name, receiver=receiver, start=p.start, end=p.end,
                                     antenna_height_m=height, code=p.code, description=_description(p),
                                     source=f"CSV de TresVizo Field, fila {k + 1}", height_note=note))
        if height is None:
            notes.append(f"{p.name}: {note or 'sin altura de antena'}; se usa la de la grabación.")
    return out, notes


def _description(point) -> str:
    row = point.row if isinstance(point.row, dict) else {}
    return str(row.get("Description") or row.get("Descripción") or "").strip()


def process_network(rovers: dict[str, ObservationSet], occupations: list[NetworkOccupation],
                    bases: list[tuple[Station, ObservationSet]], options: ProcessingOptions, workdir: Path, *,
                    fix: list[NetworkFixChoice] | None = None, network_options: NetworkOptions | None = None,
                    line_choices: dict[str, str] | None = None, tolerances: ProjectTolerances | None = None,
                    progress: tools.Progress | None = None,
                    cancel: threading.Event | None = None) -> NetworkStaticResult:
    if not occupations:
        raise UserError("No hay ocupaciones: agrega la grabación de al menos un receptor (y su CSV de campo, si "
                        "midió varios puntos).")
    if not bases:
        raise UserError("Elige al menos una estación de referencia con coordenada.")
    ids = [s.id for s, _ in bases]
    if len(set(ids)) != len(ids):
        raise UserError("Hay una estación repetida.")
    names = {o.name for o in occupations}
    clash = sorted(names & set(ids))
    if clash:
        raise UserError(f"El punto {clash[0]} se llama como una estación: cámbiale el nombre.")
    unknown = sorted({o.receiver for o in occupations} - set(rovers))
    if unknown:
        raise UserError(f"La ocupación del receptor {unknown[0]} no tiene grabación.")
    workdir.mkdir(parents=True, exist_ok=True)
    trimmed: list[ObservationSet] = []
    try:
        with intermedios.Products(workdir, cancel) as products:
            result = _process(rovers, occupations, bases, options, workdir, products, trimmed, fix, network_options,
                              line_choices, tolerances, progress, cancel)
    finally:
        intermedios.discard(workdir, keep=list(rovers.values()), drop=[obs for _, obs in bases] + trimmed)
    intermedios.write_log(workdir, f"Estático de varios puntos ({len(result.points)} puntos)", result.log,
                          result.warnings)
    return result


def _process(rovers, occupations, bases, options, workdir, products, trimmed, fix, network_options, line_choices,
             tolerances, progress, cancel) -> NetworkStaticResult:
    step = progress or (lambda f, m: None)
    log = [f"Motor: {tools.RTKLIB_VERSION}.", f"ANTEX: {antex.antex_name()} (IGS, subconjunto incluido).",
           f"Opciones: máscara {options.elevation_mask_deg:g}°, {', '.join(options.constellations)}, "
           f"{options.frequencies} frecuencia(s), ionosfera «{options.ionosphere}», efemérides "
           f"{'precisas' if options.ephemeris == Ephemeris.PRECISE else 'difundidas'}."]
    log += sesion.describe(options)
    sesion.constellations(options)
    warnings: list[str] = []
    if options.ephemeris == Ephemeris.PRECISE:
        log.append("Órbitas precisas: " + precise.describe([Path(p) for p in options.precise_files]))
    step(0.0, "Control de calidad de los receptores…")
    rover_quality = {}
    for key, obs in rovers.items():
        tools.check(cancel)
        rover_quality[key] = quality.assess(obs, cancel=cancel)
        warnings.extend(f"Receptor {key}: {w}" for w in list(obs.warnings) + rover_quality[key].warnings)
    # las ocupaciones, recortadas a su tramo y con su altura
    occ_obs: dict[str, ObservationSet] = {}
    keys: list[str] = []
    for k, o in enumerate(occupations):
        tools.check(cancel)
        key = occupation_key(o, k)
        keys.append(key)
        obs = rovers[o.receiver]
        if not (obs.start and obs.end):
            raise UserError(f"No se conoce el horario de la grabación del receptor {o.receiver}.")
        start, end = o.start or obs.start, o.end or obs.end
        if _overlap(start, end, obs.start, obs.end) <= 0:
            raise UserError(f"La ocupación de {o.name} ({start:%d-%m %H:%M}–{end:%H:%M} UTC) no está en la grabación "
                            f"del receptor {o.receiver}.")
        if o.start is not None or o.end is not None:
            obs = trim(obs, start, end, workdir / "ocupaciones" / _safe(key), margin_s=0.0, cancel=cancel)
            trimmed.append(obs)
        height = o.antenna_height_m if o.antenna_height_m is not None else obs.antenna_height_m
        occ_obs[key] = replace(obs, marker=o.name, point_name=o.name, antenna_height_m=height)
        log.append(f"Ocupación {o.name} ({o.receiver}): {occ_obs[key].start:%d-%m-%Y %H:%M:%S}–"
                   f"{occ_obs[key].end:%H:%M:%S} UTC, altura a la ARP {height:.4f} m"
                   + (f", {o.source}" if o.source else "") + ".")
        if o.height_note:
            warnings.append(f"{o.name}: {o.height_note}")
    by_key = dict(zip(keys, occupations))
    plan_base = []
    for key, o in by_key.items():
        a = occ_obs[key]
        for station, base_obs in bases:
            if base_obs.start and base_obs.end and _overlap(a.start, a.end, base_obs.start, base_obs.end) >= \
                    MIN_COMMON_S:
                plan_base.append((key, station, base_obs))
    plan_rover = []
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            oa, ob = by_key[ka], by_key[kb]
            if oa.receiver == ob.receiver or oa.name == ob.name:
                continue
            a, b = occ_obs[ka], occ_obs[kb]
            if _overlap(a.start, a.end, b.start, b.end) >= MIN_COMMON_S:
                plan_rover.append((ka, kb))
    total = max(1, len(plan_base) + len(plan_rover))
    log.append(f"Líneas a procesar: {len(plan_base)} a las estaciones y {len(plan_rover)} entre ocupaciones "
               f"simultáneas (con al menos {MIN_COMMON_S / 60:.0f} min de datos comunes).")
    run_options = options
    if options.precise_files:
        step(0.02, "Preparando las órbitas y relojes precisos…")
        run_options = replace(options, precise_files=products.precise([Path(p) for p in options.precise_files]))
    antex_path = products.antex()
    lines: list[NetworkLine] = []
    failed: list[str] = []
    base_quality = {}
    done = [0]
    used_ids: set[str] = set()

    def run(rover_obs, station, base_obs, label, from_id, to_id, kind, occupation):
        tools.check(cancel)
        frac0 = 0.05 + 0.9 * done[0] / total
        span = 0.9 / total
        step(frac0, f"Línea {label}…")
        folder = workdir / "lineas" / _safe(label)
        try:
            solution, _ = process_baseline(rover_obs, station, base_obs, run_options, folder,
                                           progress=lambda f, m: step(frac0 + span * f, m), cancel=cancel, log=log,
                                           label=label, antex_path=antex_path)
        except Cancelled:
            raise
        except UserError as error:
            failed.append(f"{label}: {error}")
            log.append(f"  {label}: sin solución ({error}).")
            return None
        finally:
            done[0] += 1
        if solution.pdop_max is None and solution.engine == "RTKLIB":
            solution.pdop_max = _baseline_pdop(solution)
        ident = f"{from_id}–{to_id} · {solution.start:%d-%m %H:%M}"
        while ident in used_ids:
            ident += "′"
        used_ids.add(ident)
        lines.append(NetworkLine(ident, from_id, to_id, kind, solution, occupation))
        log.append(f"  {ident}: {solution.solution.value}, {solution.length_m:.3f} m, cociente "
                   f"{solution.ratio if solution.ratio is not None else '—'}, {solution.epochs_used} épocas.")
        warnings.extend(f"{ident}: {w}" for w in solution.warnings)
        return solution

    for key, station, base_obs in plan_base:
        a = occ_obs[key]
        sid = station.id
        warnings.extend(f"Base {sid}: {note}" for note in networks.station_notes(station))
        base_trim = trim(base_obs, a.start, a.end, workdir / "estaciones" / _safe(f"{sid}_{key}"), cancel=cancel)
        trimmed.append(base_trim)
        if sid not in base_quality:
            base_quality[sid] = quality.assess(base_trim, cancel=cancel)
            warnings.extend(f"Base {sid}: {w}" for w in base_quality[sid].warnings)
        run(a, station, base_trim, f"{sid}–{by_key[key].name}", sid, by_key[key].name, "base", key)
    # coordenada preliminar de cada punto nuevo, de sus líneas a las estaciones
    stations = {s.id: s for s, _ in bases}
    preliminary: dict[str, Ecef] = {}
    for name in dict.fromkeys(o.name for o in occupations):
        mine = [ln for ln in lines if ln.kind == "base" and ln.to_id == name]
        if not mine:
            continue
        if len(mine) == 1:
            preliminary[name] = mine[0].baseline.rover_position
            continue
        obs_lines = []
        for ln in mine:
            cov = np.array(ln.baseline.covariance_ecef)
            st = stations[ln.from_id]
            if st.sigma_enu is not None:
                cov = cov + _station_cov(st)
            obs_lines.append(adjust.LineObservation(ln.id, _xyz(ln.baseline.rover_position), cov))
        preliminary[name] = adjust.adjust(obs_lines, mine[0].baseline.rover_position.frame,
                                          mine[0].baseline.rover_position.epoch).position
    for ka, kb in plan_rover:
        oa, ob = by_key[ka], by_key[kb]
        # el inicio es el de coordenada preliminar (si los dos, el primero)
        if oa.name not in preliminary and ob.name in preliminary:
            ka, kb, oa, ob = kb, ka, ob, oa
        a, b = occ_obs[ka], occ_obs[kb]
        position = preliminary.get(oa.name)
        if position is None:
            position = a.approx_position
            if position is None:
                failed.append(f"{oa.name}–{ob.name}: ninguno de los dos puntos tiene coordenada (ni líneas a las "
                              "estaciones ni posición aproximada).")
                continue
            warnings.append(f"La línea {oa.name}–{ob.name} parte de la posición aproximada de {oa.name} (sin líneas "
                            "a las estaciones): su vector puede tener errores de unos ppm.")
        start_station = Station(id=oa.name, name=oa.name, position=position, antenna_type=a.antenna_type,
                                radome=a.radome or "NONE", antenna_height_m=a.antenna_height_m, receiver=a.receiver,
                                source="coordenada preliminar de sus líneas a las estaciones", official=False,
                                coordinate_origin="project")
        run(b, start_station, a, f"{oa.name}–{ob.name}", oa.name, ob.name, "rover", kb)
    if not lines:
        raise UserError("Ninguna línea base dio solución: " + (failed[0] if failed else "revisa los datos."))
    for text in failed:
        warnings.append(f"Línea sin solución, fuera del ajuste: {text}")
    step(0.96, "Ajuste de la red…")
    result = NetworkStaticResult(occupations=list(occupations), rovers=dict(rovers), bases=[s for s, _ in bases],
                                 lines=lines, network=None, failed_lines=failed, rover_quality=rover_quality,
                                 base_quality=base_quality, log=log, warnings=warnings,
                                 ephemeris=options.ephemeris,
                                 precise_files=[Path(p).name for p in options.precise_files]
                                 if options.ephemeris == Ephemeris.PRECISE else [],
                                 rtklib_version=tools.RTKLIB_VERSION,
                                 time_window=options.time_window if sesion.is_trimmed(options.time_window) else None,
                                 excluded_sats=sesion.normalize_excluded(options.excluded_sats))
    out = readjust(result, fix, network_options, line_choices, tolerances)
    step(1.0, "Listo.")
    return out


def default_fix(bases: list[Station]) -> list[NetworkFixChoice]:
    return [NetworkFixChoice(s.id) for s in bases]


def constraints_for(bases: list[Station], fix: list[NetworkFixChoice] | None) -> tuple[list[NetworkConstraint],
                                                                                         list[str]]:
    chosen = {f.point_id: f for f in (fix or [])}
    out, notes = [], []
    for s in bases:
        f = chosen.get(s.id) or NetworkFixChoice(s.id)
        mode = f.mode if f.mode in ("fixed", "weighted", "check") else "fixed"
        sigma = None
        if mode == "weighted":
            sigma = s.sigma_enu
            if sigma is None or min(sigma.e, sigma.n, sigma.u) <= 0:
                sigma = WEIGHTED_DEFAULT
                notes.append(f"{s.id} no publica la σ de su coordenada: se pondera con σ "
                             f"{sigma.e * 1000:.0f}/{sigma.n * 1000:.0f}/{sigma.u * 1000:.0f} mm (E/N/U).")
        horizontal, vertical = bool(f.horizontal), bool(f.vertical)
        use = mode != "check" and (horizontal or vertical)
        out.append(NetworkConstraint(s.id, s.position, sigma_enu=sigma, horizontal=horizontal or not use,
                                     vertical=vertical or not use, use=use))
    return out, notes


def readjust(result: NetworkStaticResult, fix: list[NetworkFixChoice] | None = None,
             network_options: NetworkOptions | None = None, line_choices: dict[str, str] | None = None,
             tolerances: ProjectTolerances | None = None) -> NetworkStaticResult:
    """El ajuste de red (y las coordenadas finales) con las líneas ya procesadas: otra elección de fijas, de líneas o de
    opciones, sin volver a procesar. Devuelve un resultado nuevo; el de entrada no cambia."""
    fix = list(fix) if fix is not None else default_fix(result.bases)
    choices = dict(line_choices or {})
    vectors = []
    for ln in result.lines:
        v = red.vector_from_baseline(ln.baseline, from_id=ln.from_id, to_id=ln.to_id, vector_id=ln.id)
        choice = choices.get(ln.id, "")
        if choice == "off":
            v.use = False
        elif choice in ("independent", "trivial"):
            v.role = choice
        vectors.append(v)
    constraints, notes = constraints_for(result.bases, fix)
    network = red.adjust_network(vectors, constraints, network_options or NetworkOptions(), tolerances)
    warnings = [w for w in result.warnings if not w.startswith("Red: ")]
    warnings += [f"Red: {w}" for w in notes + network.warnings]
    log = [line for line in result.log if not line.startswith("Red: ")] + [f"Red: {line}" for line in network.log]
    points = _final_points(result, network, warnings)
    return replace(result, network=network, points=points, warnings=list(dict.fromkeys(warnings)), log=log, fix=fix,
                   line_choices=choices, quality=None)


def _final_points(result: NetworkStaticResult, network, warnings: list[str]) -> list[NetworkFinalPoint]:
    base_ids = {s.id for s in result.bases}
    out = []
    used_lines = {v.id for v in network.vectors if v.use and (not v.trivial or network.options.include_trivial)}
    constrained = {p.id: p for p in (network.constrained.points if network.constrained else [])}
    minimal = {p.id: p for p in (network.minimal.points if network.minimal else [])}
    for name in dict.fromkeys(o.name for o in result.occupations):
        if name in base_ids:
            continue
        p, kind = constrained.get(name), "constrained"
        if p is None:
            p, kind = minimal.get(name), "minimal"
            if p is not None:
                warnings.append(f"Red: {name} no se une a ninguna fija en uso: su coordenada es la del ajuste "
                                f"mínimamente restringido ({network.minimal.datum}).")
        if p is None:
            warnings.append(f"Red: {name} no tiene ninguna línea en uso: no tiene coordenada.")
            continue
        occs = [o for o in result.occupations if o.name == name]
        out.append(NetworkFinalPoint(name=name, position=p.position, sigma_enu=p.sigma_enu,
                                     covariance_ecef=p.covariance_ecef, adjustment=kind,
                                     code=next((o.code for o in occs if o.code), ""),
                                     description=next((o.description for o in occs if o.description), ""),
                                     occupations=len(occs),
                                     lines=sum(1 for ln in result.lines if ln.id in used_lines and
                                               name in (ln.from_id, ln.to_id)),
                                     ellipse95=p.ellipse95))
    return out


def sigma_text(s: Enu) -> str:
    return f"{s.e * 1000:.1f}/{s.n * 1000:.1f}/{s.u * 1000:.1f} mm"


def horizontal(s: Enu) -> float:
    return math.hypot(s.e, s.n)
