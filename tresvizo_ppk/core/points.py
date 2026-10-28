"""PPK de varios puntos (§1.2): trayectoria cinemática combinada de la jornada y, por punto del CSV de TresVizo Field,
el promedio de las épocas de su ocupación.

Todo plomeado (decisión del propietario): el punto en el suelo es la antena (ARP) menos la altura total vertical
(jalón + case) a lo largo de la normal al elipsoide, y menos el PCO si está medido. La compensación de inclinación de
campo no interviene.
"""
from __future__ import annotations

import math
import statistics
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from ..errors import UserError
from ..model import (Ecef, Enu, Ephemeris, FieldPoint, ObservationSet, PointsResult, ProcessedPoint,
                     ProcessingOptions, Solution, Station)
from . import antex, dop, networks, quality, rtklib, tools
from .baseline import Q_TO_SOLUTION, approx_position, base_antenna, strategies
from .geodesy import (covariance_ecef_to_enu, ecef_delta_to_enu, ecef_to_geodetic, enu_rotation, gps_to_utc,
                      utc_to_gps)
from .observations import trim

# σ mínima de un punto (1σ por componente: a + b·L): la formal de RTKLIB y la dispersión de unas épocas seguidas
# son optimistas. FIJA: la especificación usual de un RTK geodésico (8 mm + 1 ppm H, 15 mm + 2 ppm V). FLOTANTE:
# holgada a propósito: en la validación, a 30 km y pocos minutos, las flotantes cinemáticas erraron 0.1–0.5 m.
SIGMA_FLOOR = {Solution.FIXED: ((0.008, 1e-6), (0.015, 2e-6)), Solution.FLOAT: ((0.10, 5e-6), (0.20, 10e-6))}
TIME_TOLERANCE_S = 0.6         # el CSV redondea a la décima; las épocas del receptor van al segundo
MAX_TRAJECTORY_POINTS = 20000  # para la gráfica: se diezma si hay más


def process_points(rover: ObservationSet, base: tuple[Station, ObservationSet], points: list[FieldPoint],
                   options: ProcessingOptions, workdir: Path, progress: tools.Progress | None = None,
                   cancel: threading.Event | None = None) -> PointsResult:
    station, base_obs = base
    if not points:
        raise UserError("No hay puntos que posprocesar.")
    if not (rover.start and rover.end):
        raise UserError("No se conoce el horario del rover.")
    workdir.mkdir(parents=True, exist_ok=True)
    step = progress or (lambda f, m: None)
    log: list[str] = [f"Motor: {tools.RTKLIB_VERSION}.", f"ANTEX: {antex.antex_name()} (IGS, subconjunto incluido).",
                      f"Base {station.id}: {station.name}, coordenada {station.source or 'manual'} "
                      f"({station.position.frame}, época {station.position.epoch:g}).",
                      f"Rover: {rover.marker}, {rover.receiver}, {rover.start:%Y-%m-%d %H:%M:%S}–"
                      f"{rover.end:%H:%M:%S} UTC, intervalo {rover.interval_s or 0:g} s."]
    warnings: list[str] = list(rover.warnings)
    if options.ephemeris == Ephemeris.PRECISE and not options.precise_files:
        raise UserError("Se pidieron efemérides precisas pero no hay archivos SP3: bájalos primero.")
    step(0.0, "Control de calidad…")
    rover_quality = quality.assess(rover, cancel=cancel)
    warnings.extend(f"Rover: {w}" for w in rover_quality.warnings)
    warnings.extend(f"Base {station.id}: {note}" for note in networks.station_notes(station))
    if station.approximate:
        warnings.append(f"Base {station.id}: {station.source}.")
    warnings.extend(quality.overlap_warnings(rover, base_obs, station.id))
    base_trim = trim(base_obs, rover.start, rover.end, workdir / "base", cancel=cancel)
    base_quality = quality.assess(base_trim, cancel=cancel)
    warnings.extend(f"Base {station.id}: {w}" for w in base_quality.warnings)
    nav = list(dict.fromkeys(list(rover.nav) + list(base_trim.nav)))
    if not nav and options.ephemeris != Ephemeris.PRECISE:
        raise UserError("No hay archivo de navegación (ni del rover ni de la base).")
    start = max(rover.start, base_trim.start or rover.start)
    end = min(rover.end, base_trim.end or rover.end)
    if end <= start:
        raise UserError(f"La base {station.id} no tiene datos en el horario del rover.")

    # Antenas: el rover se procesa en su ARP (altura 0) y cada punto se baja con su propia altura.
    rover_choice = antex.choose(rover.antenna_type, rover.radome or "NONE", "Rover")
    base_type, base_radome, base_h = base_antenna(station, base_trim, warnings)
    base_choice = antex.choose(base_type, base_radome, f"Base {station.id}")
    log += [rover_choice.note, base_choice.note]
    if rover_choice.model is None and rover.receiver_model:
        warnings.append("PCO de la antena del rover sin medir: se procesa sin modelo de centro de fase.")
    base_xyz = np.array([station.position.x, station.position.y, station.position.z])
    antex_path = antex.ensure_antex(workdir)
    approx = approx_position(rover, nav, workdir, cancel=cancel, log=log)
    length = float(np.linalg.norm(approx - base_xyz))
    strategy = strategies(length, options, "kinematic")[0]
    ar = options.ambiguity_mode if options.ambiguity_mode in ("continuous", "fix-and-hold", "instantaneous",
                                                              "off") else "fix-and-hold"
    cfg = rtklib.base_config(
        mode="kinematic", options=options, frequencies=options.frequencies, ionosphere=strategy.ionoopt,
        troposphere=strategy.tropopt, navsys=rtklib.navsys_mask(options.constellations), ambiguity=ar,
        glonass_ar="off", precise=options.ephemeris == Ephemeris.PRECISE, antex=antex_path,
        rover=rtklib.AntennaSetup(rover_choice.rtklib_name, (0.0, 0.0, 0.0)),
        base=rtklib.AntennaSetup(base_choice.rtklib_name, (0.0, 0.0, base_h)), base_xyz=tuple(base_xyz),
        interval_s=base_trim.interval_s, elevation_mask=options.elevation_mask_deg, min_ratio=options.min_ratio,
        combined=options.combined, kinematic_dynamics=False)
    conf = workdir / "trayectoria.conf"
    cfg.write(conf)
    pos = workdir / "trayectoria.pos"
    log.append(f"Trayectoria cinemática ({'combinada adelante/atrás' if options.combined else 'adelante'}): "
               f"{strategy.text}; base a {length / 1000:.1f} km del inicio.")
    rtklib.run_rnx2rtkp(conf, pos, rover.obs[0], base_trim.obs[0], nav, [Path(p) for p in options.precise_files],
                        workdir=workdir, start_gpst=utc_to_gps(start).replace(tzinfo=None),
                        end_gpst=utc_to_gps(end).replace(tzinfo=None), interval_s=options.interval_s,
                        progress=lambda f, m: step(0.1 + 0.8 * f, m), cancel=cancel, log=log, label="Trayectoria: ",
                        combined=options.combined)
    epochs = rtklib.parse_pos(pos)
    if not epochs:
        raise UserError("RTKLIB no dio ninguna solución de la trayectoria: revisa que haya satélites comunes con "
                        "la base y archivo de navegación.")
    times = [gps_to_utc(e.time.replace(tzinfo=timezone.utc)) for e in epochs]
    solved = [e for e in epochs if e.q in (rtklib.Q_FIX, rtklib.Q_FLOAT)]
    fix_rate = (sum(1 for e in solved if e.q == rtklib.Q_FIX) / len(solved)) if solved else 0.0
    log.append(f"Épocas: {len(epochs)}, fijas {100 * fix_rate:.1f} % de las que tienen solución.")
    step(0.92, "Promediando los puntos…")
    pdops = dop.pdop_by_epoch(Path(str(pos) + ".stat"))      # para la tolerancia de PDOP del proyecto
    processed = [_process_point(p, epochs, times, base_xyz, station, rover, pdops) for p in points]
    for p in processed:
        if p.message:
            warnings.append(f"{p.point.name}: {p.message}")
    stride = max(1, len(epochs) // MAX_TRAJECTORY_POINTS)
    trajectory = [(times[i], Ecef(*(float(v) for v in epochs[i].xyz), station.position.frame,
                                  station.position.epoch), Q_TO_SOLUTION.get(epochs[i].q, Solution.NONE))
                  for i in range(0, len(epochs), stride)]
    counts = {s: sum(1 for p in processed if p.solution == s) for s in Solution}
    log.append(f"Puntos: {counts[Solution.FIXED]} fijos, {counts[Solution.FLOAT]} flotantes, "
               f"{counts[Solution.SINGLE] + counts[Solution.NONE]} sin solución diferencial.")
    step(1.0, "Listo.")
    return PointsResult(base=station, rover=rover, rover_quality=rover_quality, points=processed, pos_file=pos,
                        fix_rate=fix_rate, log=log, warnings=list(dict.fromkeys(warnings)), base_quality=base_quality,
                        stat_file=Path(str(pos) + ".stat"), config_file=conf, ephemeris=options.ephemeris,
                        precise_files=[Path(p).name for p in options.precise_files]
                        if options.ephemeris == Ephemeris.PRECISE else [],
                        rtklib_version=tools.RTKLIB_VERSION, trajectory=trajectory)


def _height_for(point: FieldPoint, rover: ObservationSet) -> tuple[float | None, str]:
    if point.total_height_m is not None and math.isfinite(point.total_height_m):
        return point.total_height_m, ""
    pole = point.antenna_height_m
    if pole is not None and math.isfinite(pole):
        offset = point.antenna_offset_m if point.antenna_offset_m is not None else rover.case_offset_m
        if offset is None:
            return pole, "sin desplazamiento del receptor: se usó solo la altura del jalón."
        return pole + offset, ""
    return None, "el CSV no trae la altura de antena del punto."


def _process_point(point: FieldPoint, epochs, times, base_xyz, station: Station,
                   rover: ObservationSet, pdops: dict | None = None) -> ProcessedPoint:
    a = point.start - timedelta(seconds=TIME_TOLERANCE_S)
    b = point.end + timedelta(seconds=TIME_TOLERANCE_S)
    inside = [e for e, t in zip(epochs, times) if a <= t <= b]
    height, height_note = _height_for(point, rover)
    if not inside:
        return ProcessedPoint(point=point, solution=Solution.NONE, position=None, sigma_enu=None, epochs_fixed=0,
                              epochs_total=0, distance_to_base_m=None, delta_from_field_enu=None,
                              message="la ocupación no cae en la trayectoria posprocesada (fuera del horario de la "
                                      "grabación o sin solución).")
    fixed = [e for e in inside if e.q == rtklib.Q_FIX]
    floats = [e for e in inside if e.q == rtklib.Q_FLOAT]
    used = fixed or floats
    messages = []
    if fixed:
        solution = Solution.FIXED
        if floats:
            messages.append(f"{len(floats)} épocas flotantes de la ocupación no entran en el promedio.")
    elif floats:
        solution = Solution.FLOAT
        messages.append("sin épocas fijas: promedio de épocas FLOTANTES.")
    else:
        solution = Q_TO_SOLUTION.get(inside[0].q, Solution.SINGLE)
        used = inside
        messages.append("sin solución diferencial en la ocupación.")
    xyz = np.mean(np.array([e.xyz for e in used]), axis=0)
    lat, lon, _ = ecef_to_geodetic(*xyz)
    rot = enu_rotation(lat, lon)
    if len(used) > 1:
        scatter = np.cov((rot @ (np.array([e.xyz for e in used]) - xyz).T))
    else:
        scatter = np.zeros((3, 3))
    formal = np.mean([covariance_ecef_to_enu(xyz, e.cov) for e in used], axis=0)
    # σ del promedio: la dispersión de las épocas (no se reduce con √n porque las épocas consecutivas están
    # correlacionadas), la formal o el mínimo de SIGMA_FLOOR, lo que sea mayor
    var = np.maximum(np.diag(scatter), np.diag(formal))
    floor = SIGMA_FLOOR.get(solution)
    if floor is not None:
        length = float(np.linalg.norm(xyz - base_xyz))
        sh = floor[0][0] + floor[0][1] * length
        sv = floor[1][0] + floor[1][1] * length
        var = np.maximum(var, [sh * sh, sh * sh, sv * sv])
    sigma = Enu(*(float(math.sqrt(max(0.0, v))) for v in var))
    ground = None
    delta = None
    if height is None:
        messages.append(height_note)
    else:
        if height_note:
            messages.append(height_note)
        up = rot[2]
        drop = height + (point.antenna_pco_m or 0.0)
        g = xyz - drop * up
        ground = Ecef(float(g[0]), float(g[1]), float(g[2]), station.position.frame, station.position.epoch)
        if point.field_position is not None:
            f = point.field_position
            d = ecef_delta_to_enu((f.x, f.y, f.z), g - np.array([f.x, f.y, f.z]))
            delta = Enu(*d)
            if f.frame and f.frame.replace(" ", "").upper() != station.position.frame.replace(" ", "").upper():
                messages.append(f"lo medido en campo está en {f.frame} y la base en {station.position.frame}: la "
                                "diferencia incluye el cambio de marco.")
    expected = max(1, int(round((point.end - point.start).total_seconds() / max(rover.interval_s or 1.0, 0.1))) + 1)
    if len(inside) < 0.8 * expected and expected > 2:
        messages.append(f"la trayectoria solo tiene {len(inside)} de {expected} épocas de la ocupación.")
    # para las tolerancias del proyecto: el cociente mediano de las fijas promediadas y la PDOP máxima de las usadas
    ratios = [e.ratio for e in fixed if e.ratio > 0]
    return ProcessedPoint(point=point, solution=solution, position=ground, sigma_enu=sigma, epochs_fixed=len(fixed),
                          epochs_total=len(inside), distance_to_base_m=float(np.linalg.norm(xyz - base_xyz)),
                          delta_from_field_enu=delta, message=" ".join(messages),
                          antenna_position=Ecef(float(xyz[0]), float(xyz[1]), float(xyz[2]), station.position.frame,
                                                station.position.epoch),
                          height_used_m=height, epochs_float=len(floats),
                          ratio=statistics.median(ratios) if ratios else None,
                          pdop_max=dop.max_pdop(pdops or {}, [e.time for e in used]))
