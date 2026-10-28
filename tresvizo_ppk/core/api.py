"""Funciones del motor que usa la interfaz (especificación §8). Nada de aquí importa la interfaz.

Progreso: `progress(fraccion, mensaje)`; cancelar: un `threading.Event`. Los errores para el usuario son
`tresvizo_ppk.errors.UserError` (frase en español); cualquier otra excepción es un error de programa.

Redes CORS (§3.0): las estaciones salen de los paquetes de red (`networks.py`; la RGNA viene incluida). Descarga de
RINEX (§3.2): `download_station_rinex` solo para redes cuyo paquete declara una dirección pública documentada
(`can_download_station`); para las demás —la RGNA entre ellas: el INEGI no publica un servicio para programas—
`station_download_guide` dice qué pedir y dónde.
"""
from __future__ import annotations

import math
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ..errors import UserError
from ..model import (DataQuality, Ecef, FieldPoint, NetworkFixChoice, NetworkOccupation, NetworkOptions,
                     NetworkStaticResult, ObservationSet, PointsResult, ProcessingOptions, ProjectTolerances,
                     StaticResult, Station)
from . import (antenas, antex, basecoord, field_csv, networks, observations, points, precise, quality, static,
               static_network, tools)

Progress = tools.Progress

# Una línea base más corta que esto no es una línea base: la «estación» es el propio rover (la misma antena o los
# mismos archivos). Se rechaza a la entrada con un error claro en vez de dar una solución sin sentido.
MIN_BASELINE_M = 1.0


def _same_recording(a: ObservationSet, b: ObservationSet) -> bool:
    def files(obs):
        out = set()
        for path in obs.source_files or obs.obs:
            try:
                out.add(Path(path).resolve())
            except OSError:
                out.add(Path(path))
        return out
    first, second = files(a), files(b)
    return bool(first) and first == second


def _distance(a: Ecef, b: Ecef) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


def _check_not_rover(rover: ObservationSet, station: Station, obs: ObservationSet, *, use_position: bool) -> None:
    """UserError si la base es el propio rover: los mismos archivos o, en estático, a menos de MIN_BASELINE_M."""
    if _same_recording(rover, obs):
        raise UserError(f"La estación {station.id} usa los mismos archivos que el rover: una línea base necesita dos "
                        "receptores distintos. Quita esa estación o elige sus propios archivos.")
    if use_position and rover.approx_position is not None:
        distance = _distance(rover.approx_position, station.position)
        if distance < MIN_BASELINE_M:
            raise UserError(f"El rover está a {distance:.2f} m de la estación {station.id}: es el mismo punto, no una "
                            "línea base. Quita esa estación y elige otra.")


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


def find_station(marker: str, network_id: str | None = None, near: Ecef | None = None) -> Station | None:
    return networks.find_station(marker, network_id, near)


def nearest_stations(position, count: int = 3, network_id: str | None = None):
    return networks.nearest_stations(position, count, network_id)


def base_coordinate_candidates(obs: ObservationSet, workdir: Path | None = None, *, autonomous: bool | None = None,
                               cancel: threading.Event | None = None) -> list[Station]:
    """Coordenadas posibles de una base, listas para procesar (la primera, por omisión): la oficial si la estación
    está en una red CORS; si no, la del encabezado RINEX y la media autónoma de la sesión, marcadas aproximadas con su
    origen (`Station.coordinate_origin`, `approximate`, `source`, `sigma_enu`, marco y época en `position`)."""
    return basecoord.base_coordinate_candidates(obs, Path(workdir) if workdir else None, autonomous=autonomous,
                                                cancel=cancel)


def manual_base_station(obs: ObservationSet, position, *, name: str = "", origin: str = "manual", source: str = "",
                        sigma=None) -> Station:
    """Base propia con coordenada escrita a mano (con su marco y época en `position`) o encadenada de un proyecto
    (`origin="project"`)."""
    return basecoord.manual_station(obs, position, name=name, origin=origin, source=source, sigma=sigma)


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
    for station, obs in bases:
        _check_not_rover(rover, station, obs, use_position=True)
    rover, rover_height = _apply_rover(rover, options)
    chosen, extra_warnings = _apply_bases(bases, options)
    bases = [(station, obs) for station, obs, _ in chosen]
    from .baseline import base_antenna
    needed = antenas.needed_keys([(rover.antenna_type, rover.radome)] +
                                 [base_antenna(station, obs, [])[:2] for station, obs in bases])
    with antex.session(options.antex_files, needed):
        result = static.process_static(rover, bases, options, Path(workdir), progress=progress, cancel=cancel,
                                       point_name=point_name)
        for baseline in result.baselines:
            if baseline.length_m < MIN_BASELINE_M:
                raise UserError(f"La línea base con {baseline.base.id} mide {baseline.length_m:.2f} m: la estación es "
                                "el propio rover, no una base. Quítala y vuelve a procesar.")
        result.antennas = antenas.static_infos(rover, rover_height, chosen, result.baselines)
        extra_warnings += antenas.own_processor_gaps(result.antennas, result.baselines)
    result.warnings = _antenna_warnings(result.antennas, extra_warnings) + list(getattr(result, "warnings", []))
    return result


# --- Estático de varios puntos (red) ----------------------------------------------------------------------------------

def network_occupations(receiver: str, csv_path: Path, rover: ObservationSet | None = None):
    """Las ocupaciones estáticas (de al menos 10 min) del CSV de TresVizo Field de un receptor y los avisos de las
    filas que no sirven (`static_network.occupations_from_field`)."""
    parsed = field_csv.read_field_csv(Path(csv_path))
    occupations, notes = static_network.occupations_from_field(parsed, receiver, rover)
    if not occupations:
        raise UserError("El CSV no tiene ocupaciones estáticas (de al menos 10 minutos): para puntos cortos usa «PPK "
                        "de puntos».")
    return occupations, notes


def process_network(rovers: dict[str, ObservationSet], occupations: list[NetworkOccupation],
                    bases: list[tuple[Station, ObservationSet]], options: ProcessingOptions, workdir: Path,
                    progress: Progress | None = None, cancel: threading.Event | None = None, *,
                    fix: list[NetworkFixChoice] | None = None, network_options: NetworkOptions | None = None,
                    line_choices: dict[str, str] | None = None,
                    tolerances: ProjectTolerances | None = None) -> NetworkStaticResult:
    """Estático de varios puntos: cada ocupación contra cada estación y contra las ocupaciones simultáneas de otros
    receptores, y el ajuste de red. La antena (y la altura medida) de cada receptor es «rover:<clave>» (o «rover»)
    de `options.antennas`; la altura de una ocupación del CSV gana sobre la de la grabación."""
    for key, obs in rovers.items():
        for station, base_obs in bases:
            if _same_recording(obs, base_obs):
                raise UserError(f"La estación {station.id} usa los mismos archivos que el receptor {key}: una línea "
                                "base necesita dos receptores distintos.")
    applied = {}
    for key, obs in rovers.items():
        selection = options.antennas.get(f"rover:{key}") or options.antennas.get("rover")
        local = replace(options, antennas={**options.antennas, "rover": selection} if selection else
                        {k: v for k, v in options.antennas.items() if k != "rover"})
        applied[key], _ = _apply_rover(obs, local)
    chosen, extra_warnings = _apply_bases(bases, options)
    bases = [(station, obs) for station, obs, _ in chosen]
    from .baseline import base_antenna
    needed = antenas.needed_keys([(o.antenna_type, o.radome) for o in applied.values()] +
                                 [base_antenna(station, obs, [])[:2] for station, obs in bases])
    with antex.session(options.antex_files, needed):
        result = static_network.process_network(applied, occupations, bases, options, Path(workdir),
                                                fix=fix, network_options=network_options, line_choices=line_choices,
                                                tolerances=tolerances, progress=progress, cancel=cancel)
        for line in result.lines:
            if line.baseline.length_m < MIN_BASELINE_M:
                raise UserError(f"La línea {line.id} mide {line.baseline.length_m:.2f} m: los dos extremos son el "
                                "mismo punto. Quita esa estación o cambia el nombre del punto.")
        longest = max((ln.baseline.length_m for ln in result.lines), default=None)
        infos = [antenas.info(f"Receptor {key}", o.antenna_type, o.radome, height_m=None,
                              height_text_="la de cada ocupación", length_m=longest,
                              meridian=bool(o.receiver_model)) for key, o in applied.items()]
        for station, obs, text in chosen:
            kind, dome, height = base_antenna(station, obs, [])
            lengths = [ln.baseline.length_m for ln in result.lines if ln.from_id == station.id]
            infos.append(antenas.info(f"Base {station.id}", kind, dome, height_m=height, height_text_=text,
                                      length_m=max(lengths) if lengths else None,
                                      meridian=bool(obs.receiver_model)))
        result.antennas = infos
    result.warnings = _antenna_warnings(result.antennas, extra_warnings) + list(result.warnings)
    return result


def readjust_network(result: NetworkStaticResult, fix: list[NetworkFixChoice] | None = None,
                     network_options: NetworkOptions | None = None, line_choices: dict[str, str] | None = None,
                     tolerances: ProjectTolerances | None = None) -> NetworkStaticResult:
    """Otra elección de fijas, de líneas o de opciones del ajuste, sin volver a procesar."""
    return static_network.readjust(result, fix, network_options, line_choices, tolerances)


def _apply_rover(rover: ObservationSet, options: ProcessingOptions, use_height: bool = True):
    try:
        return antenas.apply_rover(rover, options, use_height=use_height)
    except ValueError as error:
        raise UserError(f"Altura de la antena del rover: {error}") from error


def _apply_bases(bases, options: ProcessingOptions):
    chosen, warnings = [], []
    for station, obs in bases:
        try:
            station, obs, text, notes = antenas.apply_base(station, obs, options)
        except ValueError as error:
            raise UserError(f"Altura de la antena de {station.id}: {error}") from error
        chosen.append((station, obs, text))
        warnings += notes
    return chosen, warnings


def _antenna_warnings(infos, extra: list[str]) -> list[str]:
    """Avisos de antena para el resultado: los rojos (sin modelo y más de 5 km), los amarillos (el del Meridian ya lo
    da el motor como «PCO … sin medir») y los cambios hechos a mano sobre el catálogo de una red."""
    out = [i.note for i in infos if i.level == "error"]
    out += [i.note for i in infos if i.level == "warning" and not (i.role == "Rover" and "Meridian" in i.note)]
    return out + extra


def read_field_csv(path: Path) -> list[FieldPoint]:
    return field_csv.read_field_csv(Path(path))


def process_points(rover: ObservationSet, base: tuple[Station, ObservationSet], points_: list[FieldPoint],
                   options: ProcessingOptions, workdir: Path, progress: Progress | None = None,
                   cancel: threading.Event | None = None) -> PointsResult:
    # En cinemático el rover puede pasar junto a la base: solo se rechaza la misma grabación.
    _check_not_rover(rover, base[0], base[1], use_position=False)
    # en PPK de puntos la altura del rover es la de cada punto (CSV); del rover solo cuenta la antena
    rover, _ = _apply_rover(rover, options, use_height=False)
    chosen, extra_warnings = _apply_bases([base], options)
    station, obs, base_height = chosen[0]
    from .baseline import base_antenna
    base_type, base_dome, base_h = base_antenna(station, obs, [])
    needed = antenas.needed_keys([(rover.antenna_type, rover.radome), (base_type, base_dome)])
    with antex.session(options.antex_files, needed):
        result = points.process_points(rover, (station, obs), points_, options, Path(workdir), progress=progress,
                                       cancel=cancel)
        distances = [p.distance_to_base_m for p in result.points if p.distance_to_base_m is not None]
        longest = max(distances) if distances else None
        result.antennas = [
            antenas.info("Rover", rover.antenna_type, rover.radome, height_m=None,
                         height_text_="la de cada punto (CSV)", length_m=longest, meridian=bool(rover.receiver_model)),
            antenas.info(f"Base {station.id}", base_type, base_dome, height_m=base_h, height_text_=base_height,
                         length_m=longest, meridian=bool(obs.receiver_model))]
    result.warnings = _antenna_warnings(result.antennas, extra_warnings) + list(getattr(result, "warnings", []))
    return result


# --- Antenas (§1.1.1) ------------------------------------------------------------------------------------------------

def antenna_catalog(antex_files: list[Path] | None = None) -> list[tuple[str, str, str]]:
    """(tipo, radomo, fuente) de todas las antenas de receptor con modelo: las de los ANTEX importados primero (ganan),
    las del incluido (IGS y NGS) y las de los paquetes de red."""
    return [(m.type, m.radome, m.source) for m in antex.catalog(antex_files or [])]


def read_antex(path: Path) -> list[tuple[str, str, str]]:
    """Valida un ANTEX que importa el usuario y da sus antenas (tipo, radomo, fuente); UserError si no sirve."""
    try:
        return [(m.type, m.radome, m.source) for m in antex.read_antex_file(Path(path))]
    except antex.AntexError as error:
        raise UserError(str(error)) from error


def antenna_status(antenna_type: str, radome: str, length_m: float | None = None,
                   antex_files: list[Path] | None = None, role: str = ""):
    """El modelo que se aplicaría a esa antena (`model.AntennaInfo`: modelo, fuente, nivel del aviso)."""
    with antex.session(antex_files or []):
        return antenas.info(role or "Antena", antenna_type, radome, height_m=None, height_text_="", length_m=length_m)


def slant_dimensions(antenna_type: str) -> dict | None:
    """Radio y desfase de la antena para la altura inclinada, con su fuente, si están en la tabla."""
    return antenas.slant_dimensions(antenna_type)


def vertical_height(selection, mount_offset_m: float | None = None) -> float:
    """Altura vertical a la ARP según el método de medida; UserError con el motivo si los datos no alcanzan."""
    try:
        return antenas.vertical_height(selection, mount_offset_m=mount_offset_m)
    except ValueError as error:
        raise UserError(str(error)) from error


def engine_version() -> str:
    return tools.RTKLIB_VERSION
