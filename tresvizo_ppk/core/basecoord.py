"""La coordenada de la base sin pedirla a mano (regla del propietario, 26-10-2026).

- Estación de una red CORS (su marcador está en un catálogo): la **coordenada oficial** del catálogo, sola, con el
  marco y la época de su red.
- Base propia (no está en ningún catálogo): por omisión la **posición del encabezado RINEX** (APPROX POSITION XYZ,
  que en una grabación del Meridian sale de la coordenada de base que emitía, 1005/1006, o de la mediana de sus
  GGA), y la **media de la solución autónoma de la propia sesión** con su σ si el encabezado no la trae o se pide;
  las dos marcadas como aproximadas, con su origen. La interfaz ofrece además la de un proyecto de georreferenciación
  (flujo 1, encadenado) o una escrita a mano con su marco: esas las arma ella con `Station(coordinate_origin=…)`.

Una coordenada aproximada deja los puntos bien entre sí pero desplazados lo mismo que la base: el informe lo dice.
"""
from __future__ import annotations

import math
import statistics
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from ..errors import UserError
from ..model import Ecef, Enu, ObservationSet, Station
from . import networks, rtklib, tools
from .geodesy import decimal_year, ecef_to_geodetic, enu_rotation, utc_to_gps

APPROX_NOTE = ("coordenada de la base aproximada ({origen}): los puntos quedan bien entre sí, pero desplazados lo "
               "mismo que la base")
AUTONOMOUS_FRAME = "WGS 84 (solución autónoma)"
HEADER_FRAME = "WGS 84 (posición del encabezado RINEX)"


def _own_station(obs: ObservationSet, position: Ecef, sigma: Enu | None, origin: str, source: str) -> Station:
    name = obs.point_name or obs.marker or "Base propia"
    return Station(id=(obs.marker or "BASE")[:9].upper(), name=name, position=position, sigma_enu=sigma,
                   antenna_type=obs.antenna_type, radome=obs.radome or "NONE",
                   antenna_height_m=obs.antenna_height_m, receiver=obs.receiver, source=source, official=False,
                   coordinate_origin=origin, approximate=True)


def _frame_for(obs: ObservationSet, default: str = AUTONOMOUS_FRAME) -> tuple[str, float]:
    """Marco declarado por el receptor si lo grabó; si no, el del origen de la coordenada (`default`)."""
    epoch = round(decimal_year(obs.start), 4) if obs.start else 0.0
    if obs.frame_declared:
        return obs.frame_declared, obs.frame_epoch_declared or epoch
    return default, epoch


def autonomous_position(obs: ObservationSet, workdir: Path, cancel: threading.Event | None = None,
                        log: list[str] | None = None, interval_s: float = 30.0) -> tuple[Ecef, Enu, int]:
    """Media (mediana) de la solución autónoma de RTKLIB en toda la sesión, bajada a la marca, con su σ."""
    if not obs.nav:
        raise UserError("No hay archivo de navegación de la base para calcular su posición autónoma.")
    exe = tools.find_tool("rnx2rtkp")
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / f"{obs.obs[0].stem}_autonoma.pos"
    stage = rtklib.Stage(workdir, out.stem)          # nombres ASCII y rutas relativas (ver rtklib.Stage)
    try:
        args = [str(exe), "-p", "0", "-e", "-sys", "G,R,E,C,J", "-ti", f"{interval_s:g}", "-o", "salida.pos",
                stage.file(obs.obs[0]), stage.folder(list(obs.nav), "navegacion")]
        tools.run(args, cwd=stage.dir, cancel=cancel, log=log)
        out.unlink(missing_ok=True)
        stage.collect("salida.pos", out)
    finally:
        stage.close()
    rows = []
    for line in out.read_text(encoding="ascii", errors="replace").splitlines() if out.is_file() else []:
        if line.startswith("%") or not line.strip():
            continue
        parts = line.split()
        try:
            if int(parts[5]) in (5, 4, 3, 1, 2):
                rows.append((float(parts[2]), float(parts[3]), float(parts[4])))
        except (IndexError, ValueError):
            continue
    if len(rows) < 3:
        raise UserError("No se pudo calcular la posición autónoma de la base (sin solución en la sesión).")
    xyz = np.median(np.array(rows), axis=0)
    lat, lon, _ = ecef_to_geodetic(*xyz)
    rot = enu_rotation(lat, lon)
    enu = (rot @ (np.array(rows) - xyz).T).T
    # σ de la media: dispersión / √n con n de épocas casi independientes (una cada ≥ 5 min), y nunca menos de
    # lo que se sabe de la solución autónoma con efemérides difundidas (decímetros)
    independent = max(1, int(len(rows) * interval_s / 300))
    sigma = [max(float(np.std(enu[:, i])) / math.sqrt(independent), floor) for i, floor in enumerate((0.3, 0.3, 0.6))]
    mark = xyz - obs.antenna_height_m * rot[2]
    frame, epoch = _frame_for(obs)
    return Ecef(float(mark[0]), float(mark[1]), float(mark[2]), frame, epoch), Enu(*sigma), len(rows)


def base_coordinate_candidates(obs: ObservationSet, workdir: Path | None = None, *, autonomous: bool | None = None,
                               cancel: threading.Event | None = None) -> list[Station]:
    """Las coordenadas posibles de una base, la primera es la que se usa por omisión.

    `autonomous`: calcular también la media autónoma (None: solo si el encabezado no trae posición). Necesita
    `workdir` y el archivo de navegación."""
    official = networks.find_station(obs.marker)
    if official is not None:
        return [official]
    candidates: list[Station] = []
    if obs.approx_position is not None:
        p = obs.approx_position
        frame, epoch = _frame_for(obs, HEADER_FRAME)
        origin_text = "APPROX POSITION XYZ del encabezado RINEX"
        extra = (obs.approx_position_source or "").strip()
        if extra and "encabezado rinex" not in extra.lower():
            origin_text += f" ({extra})"
        candidates.append(_own_station(obs, Ecef(p.x, p.y, p.z, frame, epoch), None, "rinex-header",
                                       APPROX_NOTE.format(origen=origin_text)))
    if autonomous or (autonomous is None and not candidates):
        if workdir is None:
            raise UserError("Falta la carpeta de trabajo para calcular la posición autónoma de la base.")
        position, sigma, n = autonomous_position(obs, workdir, cancel=cancel)
        candidates.append(_own_station(obs, position, sigma, "autonomous",
                                       APPROX_NOTE.format(origen=f"media de {n} soluciones autónomas de la propia "
                                                                 f"sesión")))
    if not candidates:
        raise UserError("La base no está en ningún catálogo y su RINEX no trae posición: escribe su coordenada o "
                        "encadénala de un proyecto de georreferenciación.")
    return candidates


def manual_station(obs: ObservationSet, position: Ecef, *, name: str = "", origin: str = "manual",
                   source: str = "", sigma: Enu | None = None) -> Station:
    """Base con una coordenada escrita a mano (`origin="manual"`) o encadenada de un proyecto (`"project"`)."""
    text = source or ("escrita a mano" if origin == "manual" else "de un proyecto de georreferenciación")
    return Station(id=(obs.marker or "BASE")[:9].upper(), name=name or obs.point_name or obs.marker or "Base propia",
                   position=position, sigma_enu=sigma, antenna_type=obs.antenna_type, radome=obs.radome or "NONE",
                   antenna_height_m=obs.antenna_height_m, receiver=obs.receiver, source=text, official=False,
                   coordinate_origin=origin, approximate=False)
