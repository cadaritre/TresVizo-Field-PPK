"""Funciones del motor que usa la interfaz (especificación §8). Nada de aquí importa la interfaz.

Progreso: `progress(fraccion, mensaje)`; cancelar: un `threading.Event`. Los errores para el usuario son
`tresvizo_ppk.errors.UserError` (frase en español); cualquier otra excepción es un error de programa.

Redes CORS (§3.0): las estaciones salen de los paquetes de red (`networks.py`; la RGNA viene incluida). Descarga de
RINEX (§3.2): `download_station_rinex` solo para redes cuyo paquete declara una dirección pública documentada
(`can_download_station`); para las demás —la RGNA entre ellas: el INEGI no publica un servicio para programas—
`station_download_guide` dice qué pedir y dónde.
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from ..model import (DataQuality, FieldPoint, ObservationSet, PointsResult, ProcessingOptions, StaticResult,
                     Station)
from . import field_csv, networks, observations, points, precise, quality, static, tools

Progress = tools.Progress


def prepare_observations(paths: list[Path], workdir: Path, *, pole_height_m: float | None = None,
                         marker: str | None = None, window: tuple[datetime, datetime] | None = None,
                         progress: Progress | None = None, cancel: threading.Event | None = None) -> ObservationSet:
    """Grabación `.bin`/`.part` del Meridian o RINEX en cualquier forma (2.11/3.x/4, `.zip`, `.gz`, `.Z`, Hatanaka):
    descomprime, convierte, une en un solo archivo y, con `window` (UTC), recorta."""
    return observations.prepare_observations(paths, Path(workdir), pole_height_m=pole_height_m, marker=marker,
                                             window=window, progress=progress, cancel=cancel)


def assess_quality(obs: ObservationSet, cancel: threading.Event | None = None) -> DataQuality:
    return quality.assess(obs, cancel=cancel)


def cors_networks() -> list[networks.Network]:
    """Las redes CORS disponibles (incluidas e instaladas del índice firmado), con su marco y época oficial."""
    return networks.networks()


def station_catalog(network_id: str | None = None) -> list[Station]:
    """Las estaciones de todas las redes (o de una)."""
    return networks.station_catalog(network_id)


def rgna_catalog() -> list[Station]:
    """Nombre de la especificación §8, anterior a las redes de varios países: hoy da las estaciones de TODAS las redes
    instaladas (igual que `station_catalog()`). Usar `station_catalog`."""
    return networks.station_catalog()


def find_station(marker: str, network_id: str | None = None) -> Station | None:
    return networks.find_station(marker, network_id)


def nearest_stations(position, count: int = 3, network_id: str | None = None):
    return networks.nearest_stations(position, count, network_id)


def can_download_station(station: Station) -> bool:
    return networks.can_download(station)


def station_download_guide(station: Station, start: datetime, end: datetime) -> str:
    """Qué bajar y de dónde para cubrir [start, end] (UTC), según el paquete de la red de la estación."""
    return networks.download_guide(station, start, end)


def download_station_rinex(station: Station, start: datetime, end: datetime, workdir: Path,
                           progress: Progress | None = None, cancel: threading.Event | None = None) -> list[Path]:
    """Baja los RINEX de la estación si su red lo permite (dirección pública documentada); a petición del usuario."""
    return networks.download_station_rinex(station, start, end, Path(workdir), progress=progress, cancel=cancel)


def download_precise_products(start: datetime, end: datetime, workdir: Path, progress: Progress | None = None,
                              cancel: threading.Event | None = None) -> list[Path]:
    """Órbitas SP3 (ESA o IGS en el BKG, sin cuenta) que cubren la sesión. Solo a petición del usuario."""
    return precise.download_precise_products(start, end, Path(workdir), progress=progress, cancel=cancel)


def process_static(rover: ObservationSet, bases: list[tuple[Station, ObservationSet]], options: ProcessingOptions,
                   workdir: Path, progress: Progress | None = None, cancel: threading.Event | None = None,
                   point_name: str = "") -> StaticResult:
    return static.process_static(rover, bases, options, Path(workdir), progress=progress, cancel=cancel,
                                 point_name=point_name)


def read_field_csv(path: Path) -> list[FieldPoint]:
    return field_csv.read_field_csv(Path(path))


def process_points(rover: ObservationSet, base: tuple[Station, ObservationSet], points_: list[FieldPoint],
                   options: ProcessingOptions, workdir: Path, progress: Progress | None = None,
                   cancel: threading.Event | None = None) -> PointsResult:
    return points.process_points(rover, base, points_, options, Path(workdir), progress=progress, cancel=cancel)


def engine_version() -> str:
    return tools.RTKLIB_VERSION
