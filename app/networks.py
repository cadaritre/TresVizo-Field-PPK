"""Redes de estaciones de referencia (CORS, §3.0) vistas desde la interfaz, sin suponer ningún país.

El motor publica las redes como paquetes del índice firmado (`cors_networks()` y `station_catalog()`); este adaptador
las pasa a una forma única para las pantallas, que trabajan siempre con «red + estaciones» y no con la RGNA fija.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tresvizo_ppk.model import Station


@dataclass
class Network:
    id: str
    name: str                                   # «RGNA — INEGI, México»
    country: str = ''
    frame: str = ''                             # marco oficial de sus coordenadas
    epoch: float | None = None
    stations: list[Station] = field(default_factory=list)
    attribution: str = ''
    license: str = ''
    url: str = ''                               # página de la red (para bajar sus RINEX a mano)
    source: str = ''                            # fuente y fecha de las coordenadas
    origin: str = ''                            # «incluida» o «descargada»

    @property
    def frame_text(self) -> str:
        if not self.frame:
            return '—'
        return f'{self.frame}, época {self.epoch:g}' if self.epoch is not None else self.frame


def _get(obj, *names, default=None):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def load(core) -> list[Network]:
    """Redes instaladas con sus estaciones. Corre en un hilo aparte (puede leer paquetes del disco)."""
    if core is None:
        return []
    if callable(getattr(core, 'cors_networks', None)):
        out = []
        for item in core.cors_networks():
            source = _get(item, 'source', default={}) or {}
            download = _get(item, 'download', default={}) or {}
            countries = _get(item, 'countries', default=[]) or []
            stations = core.station_catalog(item.id) if callable(getattr(core, 'station_catalog', None)) else []
            source_text = source.get('title', '') if isinstance(source, dict) else str(source)
            if isinstance(source, dict) and source.get('cutoff'):
                source_text += f", {source['cutoff']}"
            out.append(Network(id=str(item.id), name=str(_get(item, 'title', 'name', default=item.id)),
                               country=', '.join(countries) if isinstance(countries, (list, tuple)) else str(countries),
                               frame=str(_get(item, 'frame', default='') or ''), epoch=_get(item, 'epoch'),
                               stations=list(stations), attribution=str(_get(item, 'attribution', default='') or ''),
                               license=str(_get(item, 'license', default='') or ''),
                               url=str((download.get('url') if isinstance(download, dict) else '') or
                                       (source.get('url') if isinstance(source, dict) else '') or ''),
                               source=source_text, origin='incluida' if _get(item, 'bundled', default=False) else
                               'instalada'))
        return out
    return []


def guide_text(core, network: Network, station: Station, start, end) -> str | None:
    """Qué pedir en la página de la red para cubrir la sesión, si el motor lo sabe calcular para esa red."""
    if callable(getattr(core, 'station_download_guide', None)):
        return core.station_download_guide(station, start, end)
    return None


def can_download(core, station: Station) -> bool:
    return callable(getattr(core, 'can_download_station', None)) and bool(core.can_download_station(station))


def network_of(networks: list[Network], station: Station) -> Network | None:
    """La red de una estación (por su campo `network`, por identidad o, si no, por clave)."""
    key = getattr(station, 'network', '')
    if key:
        found = next((n for n in networks if n.id == key), None)
        if found is not None:
            return found
    for network in networks:
        if any(s is station for s in network.stations):
            return network
    for network in networks:
        if any(s.id == station.id for s in network.stations):
            return network
    return None


def station_notes(core, station: Station) -> list[str]:
    """Avisos que el paquete de la red da para la estación (hundimientos, desplazamientos…), si el motor los publica."""
    if core is None or station is None:
        return []
    notes = getattr(core, 'station_notes', None)
    if not callable(notes):
        notes = getattr(getattr(core, 'networks', None), 'station_notes', None)
    if not callable(notes):
        return []
    try:
        return [str(n) for n in notes(station) or []]
    except Exception:  # noqa: BLE001 - un aviso que no se puede leer no impide trabajar
        return []
