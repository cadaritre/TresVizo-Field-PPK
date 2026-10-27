"""Redes de estaciones de referencia (CORS, §3.0) vistas desde la interfaz, sin suponer ningún país.

El motor publica las redes como paquetes del índice firmado. Este adaptador lee `core.networks()` cuando existe y,
mientras tanto, arma la red que trae la 1.0 (la RGNA del INEGI) con `rgna_catalog()` y `rgna_catalog_info()`. Así las
pantallas trabajan siempre con «red + estaciones» y no con la RGNA fija.
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
    if callable(getattr(core, 'networks', None)):
        out = []
        for item in core.networks():
            stations = _get(item, 'stations', default=None)
            if stations is None and callable(getattr(core, 'network_stations', None)):
                stations = core.network_stations(_get(item, 'id'))
            out.append(Network(id=str(_get(item, 'id')), name=str(_get(item, 'name', 'title', default='')),
                               country=str(_get(item, 'country', default='') or ''),
                               frame=str(_get(item, 'frame', default='') or ''),
                               epoch=_get(item, 'epoch', default=None), stations=list(stations or []),
                               attribution=str(_get(item, 'attribution', default='') or ''),
                               license=str(_get(item, 'license', default='') or ''),
                               url=str(_get(item, 'url', 'download_url', default='') or ''),
                               source=str(_get(item, 'source', default='') or ''),
                               origin=str(_get(item, 'origin', default='') or '')))
        return out
    if not callable(getattr(core, 'rgna_catalog', None)):
        return []
    info = core.rgna_catalog_info() if callable(getattr(core, 'rgna_catalog_info', None)) else {}
    return [Network(id='rgna', name='RGNA — INEGI, México', country='México', frame=info.get('frame', 'ITRF2008'),
                    epoch=info.get('epoch', 2010.0), stations=list(core.rgna_catalog()),
                    attribution=info.get('attribution', ''), license=info.get('license', ''),
                    url='https://www.inegi.org.mx/app/geo2/rgna/', source=info.get('source', ''), origin='incluida')]


def guide_text(core, network: Network, station_id: str, start, end) -> str | None:
    """Qué pedir en la página de la red para cubrir la sesión, si el motor lo sabe calcular para esa red."""
    if callable(getattr(core, 'download_guide', None)):
        return core.download_guide(network.id, station_id, start, end)
    if network.id == 'rgna' and callable(getattr(core, 'rgna_download_guide', None)):
        return core.rgna_download_guide(station_id, start, end)
    return None


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
