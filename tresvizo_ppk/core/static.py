"""Georreferenciar un punto (estático) a una, dos o tres estaciones: líneas base y ajuste (§1.1)."""
from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..errors import Cancelled, UserError
from ..model import (Ecef, Enu, Ephemeris, ObservationSet, ProcessingOptions, StaticResult, Station)
from . import adjust, antex, networks, precise, quality, tools
from .baseline import process_baseline
from .geodesy import covariance_ecef_to_enu
from .observations import trim


def _station_label(station: Station) -> str:
    return station.id or station.name


def process_static(rover: ObservationSet, bases: list[tuple[Station, ObservationSet]], options: ProcessingOptions,
                   workdir: Path, progress: tools.Progress | None = None,
                   cancel: threading.Event | None = None, point_name: str = "") -> StaticResult:
    if not bases:
        raise UserError("Elige al menos una estación base.")
    if len(bases) > 3:
        raise UserError("Se procesan como mucho tres bases.")
    ids = [_station_label(s) for s, _ in bases]
    if len(set(ids)) != len(ids):
        raise UserError("Hay una base repetida.")
    if not (rover.start and rover.end):
        raise UserError("No se conoce el horario del rover.")
    workdir.mkdir(parents=True, exist_ok=True)
    step = progress or (lambda f, m: None)
    log: list[str] = [f"Motor: {tools.RTKLIB_VERSION}.", f"ANTEX: {antex.antex_name()} (IGS, subconjunto incluido).",
                      f"Rover: {rover.marker}, {rover.receiver}, antena {rover.antenna_type or '—'} "
                      f"{rover.radome or ''}, altura a la ARP {rover.antenna_height_m:.4f} m, "
                      f"{rover.start:%Y-%m-%d %H:%M:%S}–{rover.end:%H:%M:%S} UTC, intervalo "
                      f"{rover.interval_s or 0:g} s.",
                      f"Opciones: máscara {options.elevation_mask_deg:g}°, "
                      f"{', '.join(options.constellations)}, {options.frequencies} frecuencia(s), ionosfera "
                      f"«{options.ionosphere}», ambigüedades «{options.ambiguity_mode}», cociente mínimo "
                      f"{options.min_ratio:g}, {'combinado adelante/atrás' if options.combined else 'solo adelante'}, "
                      f"efemérides {'precisas' if options.ephemeris == Ephemeris.PRECISE else 'difundidas'}."]
    warnings: list[str] = list(rover.warnings)
    if options.ephemeris == Ephemeris.PRECISE:
        log.append("Órbitas precisas: " + precise.describe([Path(p) for p in options.precise_files]))
    step(0.0, "Control de calidad del rover…")
    rover_quality = quality.assess(rover, cancel=cancel)
    warnings.extend(f"Rover: {w}" for w in rover_quality.warnings)
    baselines = []
    used: list[tuple[Station, ObservationSet]] = []
    failed: list[tuple[Station, UserError]] = []
    base_quality = {}
    n = len(bases)
    for k, (station, base_obs) in enumerate(bases):
        tools.check(cancel)
        sid = _station_label(station)
        frac0 = 0.05 + 0.9 * k / n
        span = 0.9 / n
        step(frac0, f"Base {sid}: preparando…")
        warnings.extend(f"Base {sid}: {note}" for note in networks.station_notes(station))
        if station.approximate:
            warnings.append(f"Base {sid}: {station.source}.")
        warnings.extend(quality.overlap_warnings(rover, base_obs, sid))
        base_trim = trim(base_obs, rover.start, rover.end, workdir / sid, cancel=cancel)
        q = quality.assess(base_trim, cancel=cancel)
        base_quality[sid] = q
        warnings.extend(f"Base {sid}: {w}" for w in q.warnings)
        warnings.extend(f"Base {sid}: {w}" for w in base_obs.warnings if w not in warnings)
        try:
            solution, run = process_baseline(rover, station, base_trim, options, workdir / sid,
                                             progress=lambda f, m: step(frac0 + 0.05 * span + 0.95 * span * f, m),
                                             cancel=cancel, log=log, label=sid)
        except Cancelled:
            raise
        except UserError as error:
            # una base cuyos datos no dan solución (p. ej. pocos satélites comunes) no tumba a las demás: se deja
            # fuera con su porqué; si ninguna da solución, el error es el de la primera
            failed.append((station, error))
            log.append(f"  {sid}: sin solución ({error}).")
            continue
        used.append((station, base_obs))
        baselines.append(solution)
        log.extend(f"  {note}" for note in solution.antenna_notes)
        log.append(f"  {sid}: {solution.solution.value}, {solution.length_m:.3f} m, cociente "
                   f"{solution.ratio if solution.ratio is not None else '—'}, RMS "
                   f"{(solution.rms_m or 0) * 1000:.1f} mm, {solution.epochs_used} épocas, "
                   f"{len(solution.satellites)} satélites.")
        warnings.extend(solution.warnings)
    if not baselines:
        raise failed[0][1]
    for station, error in failed:
        warnings.append(f"Base {_station_label(station)}: se dejó fuera del cálculo. {error}")
    bases = used
    step(0.96, "Ajuste…")
    frame = bases[0][0].position.frame
    epoch = bases[0][0].position.epoch
    if any(s.position.frame != frame or s.position.epoch != epoch for s, _ in bases):
        warnings.append("Las bases tienen coordenadas en marcos o épocas distintos: el resultado los mezcla.")
    adjustment = None
    if len(baselines) == 1:
        b = baselines[0]
        position = b.rover_position
        cov = np.array(b.covariance_ecef)
        station = bases[0][0]
        if station.sigma_enu is not None:
            cov = cov + _station_cov(station)
        cov_enu = covariance_ecef_to_enu((position.x, position.y, position.z), cov)
        sigma = Enu(*(math.sqrt(max(0.0, float(cov_enu[i, i]))) for i in range(3)))
        log.append("Una sola base: la coordenada sale de esa línea base, sin ajuste ni comprobación independiente.")
        warnings.append("Con una sola base no hay comprobación independiente: usa dos o tres para un resultado "
                        "verificado.")
    else:
        lines = []
        for b, (station, _) in zip(baselines, bases):
            cov = np.array(b.covariance_ecef)
            if station.sigma_enu is not None:
                cov = cov + _station_cov(station)
            lines.append(adjust.LineObservation(_station_label(station),
                                                np.array([b.rover_position.x, b.rover_position.y,
                                                          b.rover_position.z]), cov))
        adjustment = adjust.adjust(lines, frame, epoch)
        full = adjustment
        if not adjustment.passed and len(lines) >= 3 and len(adjustment.flagged_baselines) == 1:
            # con tres o más bases y una sola línea que no cuadra (la de mayor w sobre el valor crítico): se quita y
            # se vuelve a ajustar (lectura de datos de Baarda). Si las que quedan tampoco pasan la χ², se avisa: sus
            # coordenadas oficiales difieren entre sí más de lo que miden las líneas (movimiento de las estaciones
            # desde la época de la coordenada); con dos ya no se puede saber cuál
            bad = adjustment.flagged_baselines[0]
            reduced = adjust.adjust([l for l in lines if l.base_id != bad], frame, epoch)
            log.append(f"Ajuste con todas las bases: χ² = {full.chi2:.2f} (límite {full.chi2_upper:.2f}), NO "
                       f"PASA; la prueba w señala {bad}.")
            r = full.residuals_enu[bad]
            warnings.append(f"La línea base con {bad} no cuadra con las demás (residuo E {r.e * 100:+.1f}, N "
                            f"{r.n * 100:+.1f}, U {r.u * 100:+.1f} cm): se dejó fuera del ajuste. Revisa la "
                            f"coordenada, la altura y los avisos de {bad}.")
            reduced.excluded_baselines = [bad]
            reduced.flagged_baselines = [bad]
            if not reduced.passed:
                rest = ", ".join(l.base_id for l in lines if l.base_id != bad)
                warnings.append(f"Sin {bad}, las líneas base con {rest} tampoco pasan la prueba χ² "
                                f"(χ² = {reduced.chi2:.1f} > {reduced.chi2_upper:.1f}): las coordenadas oficiales "
                                "de esas bases difieren entre sí más de lo que miden las líneas (movimiento de las "
                                "estaciones desde la época de su coordenada). La σ del resultado se escala con esa "
                                "diferencia (σ0).")
            adjustment = reduced
        position = adjustment.position
        sigma = adjustment.sigma_enu
        log.append(f"Ajuste: χ² = {adjustment.chi2:.2f} con {adjustment.degrees_of_freedom} gl (límite al 95 %: "
                   f"{adjustment.chi2_upper:.2f}), σ0 = {adjustment.sigma0:.2f}, "
                   f"{'pasa' if adjustment.passed else 'NO PASA'}.")
        for sid, r in adjustment.residuals_enu.items():
            w = adjustment.standardized_residuals[sid]
            log.append(f"  residuo {sid}: E {r.e * 1000:+.1f} N {r.n * 1000:+.1f} U {r.u * 1000:+.1f} mm "
                       f"(w {w.e:+.2f} {w.n:+.2f} {w.u:+.2f})")
        if not adjustment.passed and not adjustment.excluded_baselines:
            warnings.append(f"El ajuste no pasa la prueba chi² (χ² = {adjustment.chi2:.1f} > "
                            f"{adjustment.chi2_upper:.1f}): las líneas base no cuadran entre sí.")
        if adjustment.flagged_baselines and not adjustment.excluded_baselines:
            if len(baselines) == 2:
                warnings.append("Las dos líneas base no concuerdan y con dos no se puede saber cuál falla: agrega "
                                "una tercera base o revisa las alturas y las coordenadas de las bases.")
            else:
                warnings.append("La línea base con " + ", ".join(adjustment.flagged_baselines) + " no cuadra con las "
                                "demás (prueba w): revísala o procesa sin ella.")
        if adjustment.chi2 < adjustment.chi2_lower:
            log.append("  χ² por debajo del 5 %: el modelo de error a priori es pesimista para estos datos.")
    step(1.0, "Listo.")
    precise_names = [Path(p).name for p in options.precise_files] if options.ephemeris == Ephemeris.PRECISE else []
    return StaticResult(point_name=point_name or rover.marker, rover=rover, rover_quality=rover_quality,
                        bases=[s for s, _ in bases], baselines=baselines, adjustment=adjustment, position=position,
                        sigma_enu=sigma, log=log, warnings=_unique(warnings), base_quality=base_quality,
                        ephemeris=options.ephemeris, precise_files=precise_names,
                        rtklib_version=tools.RTKLIB_VERSION)


def _station_cov(station: Station) -> np.ndarray:
    from .geodesy import covariance_enu_to_ecef
    s = station.sigma_enu
    return covariance_enu_to_ecef((station.position.x, station.position.y, station.position.z),
                                  np.diag([s.e ** 2, s.n ** 2, s.u ** 2]))


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
