"""Redes de estaciones de referencia (CORS) como paquetes de datos (especificación §3.0; formato en
docs/formato-red-cors.md).

Cada red es un paquete `cors-network` (JSON) con su clave, nombre, países, **marco y época oficial**, estaciones,
cómo se bajan sus RINEX (guía o plantilla de dirección pública), atribución y licencia. Los paquetes vienen de dos
sitios: los incluidos en el programa (`data/redes/`, la RGNA en la 1.0) y los instalados desde el índice firmado de
TresVizo (`tresvizo_ppk.datos.biblioteca`, carpeta de la clase `cors-network`). Si una red está en los dos, manda la
de revisión más alta.

Nada aquí supone un país: el marco del resultado es el de la red de la base (o el que escribió el usuario en una
base propia).
"""
from __future__ import annotations

import json
import math
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from ..errors import UserError
from ..model import Ecef, Enu, Station
from . import tools
from .geodesy import geodetic_to_ecef

FORMAT = "tresvizo-cors-network"
FORMAT_VERSION = 1
KIND = "cors-network"
BUNDLED_DIR = Path(__file__).resolve().parent / "data" / "redes"
USER_AGENT = "TresVizo-Field-PPK"


@dataclass
class Network:
    id: str
    revision: int
    title: str                              # «RGNA — INEGI, México»
    short_name: str                         # «RGNA»
    countries: list[str]
    authority: str
    frame: str                              # marco oficial de las coordenadas («ITRF2008», «SIRGAS2000»…)
    epoch: float                            # época oficial (año decimal)
    ellipsoid: str
    reference_point: str
    source: dict
    license: str
    attribution: str
    download: dict
    stations: list[dict]
    antex: str = ""                         # bloques ANTEX de las antenas de sus estaciones (opcional)
    path: Path | None = None
    bundled: bool = False
    notes: list[str] = field(default_factory=list)

    def station(self, row: dict) -> Station:
        x, y, z = _xyz(row)
        sigma = row.get("sigma")
        frame = row.get("frame") or self.frame
        epoch = float(row.get("epoch") or self.epoch)
        source = self.source.get("title") or self.title
        cutoff = self.source.get("cutoff")
        text = f"{source}" + (f", {cutoff}" if cutoff else "") + (f" (consultado el {self.source['retrieved']})"
                                                                  if self.source.get("retrieved") else "")
        return Station(
            id=row["id"], name=f"{row['id']} — {row['name']}" if row.get("name") else row["id"],
            position=Ecef(round(x, 4), round(y, 4), round(z, 4), frame, epoch),
            sigma_enu=Enu(float(sigma["e"]), float(sigma["n"]), float(sigma["u"])) if isinstance(sigma, dict) else None,
            antenna_type=row.get("antennaType", ""), radome=row.get("radome") or "NONE",
            antenna_height_m=float(row.get("antennaHeight") or 0.0), receiver=row.get("receiver", ""),
            source=text, official=True, network=self.id, network_name=self.title, attribution=self.attribution)


def _xyz(row: dict) -> tuple[float, float, float]:
    if all(isinstance(row.get(k), (int, float)) for k in ("x", "y", "z")):
        return float(row["x"]), float(row["y"]), float(row["z"])
    return geodetic_to_ecef(float(row["latitude"]), float(row["longitude"]), float(row["height"]))


class PackageError(ValueError):
    """El paquete no cumple el formato (mensaje en español)."""


def parse_package(data: dict, path: Path | None = None) -> Network:
    """Valida y lee un paquete `cors-network`."""
    def need(key):
        if key not in data:
            raise PackageError(f"al paquete de red le falta «{key}»")
        return data[key]
    if data.get("format") != FORMAT:
        raise PackageError("no es un paquete de red CORS de TresVizo")
    if not isinstance(data.get("formatVersion"), int) or data["formatVersion"] > FORMAT_VERSION:
        raise PackageError("el paquete de red es de una versión más nueva: actualiza el programa")
    frame = need("frame")
    if not isinstance(frame, dict) or not frame.get("name") or not isinstance(frame.get("epoch"), (int, float)):
        raise PackageError("el marco de la red necesita nombre y época")
    stations = need("stations")
    if not isinstance(stations, list) or not stations:
        raise PackageError("la red no tiene estaciones")
    seen = set()
    for row in stations:
        sid = row.get("id")
        if not isinstance(sid, str) or not sid or sid in seen:
            raise PackageError(f"estación sin clave o repetida: «{sid}»")
        seen.add(sid)
        has_xyz = all(isinstance(row.get(k), (int, float)) for k in ("x", "y", "z"))
        has_llh = all(isinstance(row.get(k), (int, float)) for k in ("latitude", "longitude", "height"))
        if not (has_xyz or has_llh):
            raise PackageError(f"{sid}: sin coordenada")
        x, y, z = _xyz(row)
        r = math.sqrt(x * x + y * y + z * z)
        if not 6_300_000 < r < 6_400_000:
            raise PackageError(f"{sid}: la coordenada no está en la superficie de la Tierra")
        if not isinstance(row.get("antennaHeight", 0.0), (int, float)):
            raise PackageError(f"{sid}: altura de antena no numérica")
    download = data.get("download") or {"type": "none"}
    if download.get("type") not in ("none", "guide", "url-template"):
        raise PackageError(f"tipo de descarga desconocido: «{download.get('type')}»")
    if download.get("type") == "url-template":
        url = download.get("url", "")
        if not url.startswith("https://") or download.get("granularity") not in ("daily", "hourly"):
            raise PackageError("la descarga por plantilla necesita una dirección https y granularidad daily u hourly")
    return Network(
        id=need("id"), revision=int(need("revision")), title=need("title"), short_name=data.get("shortName") or
        data["id"], countries=list(need("countries")), authority=need("authority"), frame=frame["name"],
        epoch=float(frame["epoch"]), ellipsoid=frame.get("ellipsoid", "GRS80"),
        reference_point=data.get("referencePoint", ""), source=data.get("source") or {}, license=need("license"),
        attribution=need("attribution"), download=download, stations=stations,
        antex=(data.get("antennaCalibrations") or {}).get("antex", ""), path=path,
        notes=list(data.get("notes") or []))


def _installed_dir() -> Path | None:
    try:
        from ..datos import biblioteca          # índice firmado (agente de sistemas y datos)
    except Exception:
        return None
    try:
        return Path(biblioteca.kind_dir(KIND))
    except Exception:
        return None


def _load_dir(folder: Path | None, bundled: bool) -> list[Network]:
    out = []
    if folder is None or not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith(".item.json"):          # la entrada del índice que guarda la biblioteca
            continue
        try:
            network = parse_package(json.loads(path.read_text(encoding="utf-8")), path)
        except (OSError, ValueError, KeyError, TypeError):
            continue                            # un paquete dañado no tumba el catálogo
        network.bundled = bundled
        out.append(network)
    return out


def _stamp(folder: Path | None) -> str:
    parts = []
    for d in (BUNDLED_DIR, folder):
        if d is not None and d.is_dir():
            parts += [f"{p}:{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in sorted(d.glob("*.json"))
                      if not p.name.endswith(".item.json")]
    return "|".join(parts)


@lru_cache(maxsize=4)
def _load(folder_text: str, stamp: str) -> tuple[Network, ...]:
    by_id: dict[str, Network] = {}
    for net in _load_dir(BUNDLED_DIR, True) + _load_dir(Path(folder_text) if folder_text else None, False):
        current = by_id.get(net.id)
        if current is None or net.revision > current.revision:
            by_id[net.id] = net
    return tuple(sorted(by_id.values(), key=lambda n: n.title))


def networks() -> list[Network]:
    """Las redes disponibles (incluidas e instaladas); se vuelven a leer si cambian los archivos."""
    folder = _installed_dir()
    return list(_load(str(folder) if folder else "", _stamp(folder)))


def network(network_id: str) -> Network | None:
    return next((n for n in networks() if n.id == network_id), None)


def station_catalog(network_id: str | None = None) -> list[Station]:
    out = []
    for net in networks():
        if network_id and net.id != network_id:
            continue
        out.extend(net.station(row) for row in net.stations)
    return out


# La posición del encabezado RINEX es aproximada (a veces la autónoma del receptor, metros), pero una estación de otra
# red con la misma clave queda a cientos o miles de km: MELI es Lincoln (Maine) en la NOAA y Melilla en la del IGN.
SAME_STATION_M = 5_000.0


def find_station(marker: str, network_id: str | None = None, near: Ecef | None = None) -> Station | None:
    """Estación por el nombre del marcador del RINEX («UGTO00MEX» → UGTO; la clave larga de 9 caracteres también).

    Con `near` (la posición aproximada del RINEX) solo vale la de esa clave que quede a menos de `SAME_STATION_M`: una
    base propia que se llame como una estación lejana no toma la coordenada de esa estación. Sin posición, una clave
    que está en varias redes no se adivina (None: el usuario elige la estación)."""
    key = (marker or "").strip().upper()
    if not key:
        return None
    usable = near is not None and (near.x, near.y, near.z) != (0.0, 0.0, 0.0)
    for candidate in dict.fromkeys((key, key[:4])):
        matches = [s for s in station_catalog(network_id) if s.id.upper() == candidate]
        if not matches:
            continue
        if usable:
            close = [(math.dist((s.position.x, s.position.y, s.position.z), (near.x, near.y, near.z)), i, s)
                     for i, s in enumerate(matches)]
            close = [c for c in close if c[0] <= SAME_STATION_M]
            return min(close)[2] if close else None
        return matches[0] if len(matches) == 1 else None
    return None


def nearest_stations(position: Ecef, count: int = 3, network_id: str | None = None) -> list[tuple[Station, float]]:
    out = [(s, math.dist((s.position.x, s.position.y, s.position.z), (position.x, position.y, position.z)))
           for s in station_catalog(network_id)]
    out.sort(key=lambda item: item[1])
    return out[:count]


def station_notes(station: Station) -> list[str]:
    """Avisos que el paquete de la red da para esa estación (desplazamientos, hundimientos…)."""
    net = network(station.network) if station.network else None
    if net is None:
        return []
    row = next((r for r in net.stations if r.get("id") == station.id), None)
    return list(row.get("notes") or []) if row else []


def antex_blocks() -> list[str]:
    """Bloques ANTEX que traen los paquetes de red (para las antenas de sus estaciones)."""
    return [n.antex for n in networks() if n.antex]


# --- Descarga de RINEX de una estación -------------------------------------------------------------------------------

def _network_of(station: Station) -> Network:
    net = network(station.network) if station.network else None
    if net is None:
        raise UserError(f"La estación {station.id} no es de una red instalada: entrega sus archivos RINEX.")
    return net


def can_download(station: Station) -> bool:
    try:
        return _network_of(station).download.get("type") == "url-template"
    except UserError:
        return False


def _slots(net: Network, start: datetime, end: datetime, margin_min: float = 10.0) -> list[datetime]:
    start = start.astimezone(timezone.utc) - timedelta(minutes=margin_min)
    end = end.astimezone(timezone.utc) + timedelta(minutes=margin_min)
    hourly = net.download.get("granularity") == "hourly"
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    t = start.replace(minute=0, second=0, microsecond=0) if hourly else \
        start.replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    while t <= end:
        out.append(t)
        t += step
    return out


def _fill(template: str, station: Station, t: datetime) -> str:
    doy = t.timetuple().tm_yday
    values = {"ID": station.id.upper(), "id": station.id.lower(), "yyyy": f"{t.year:04d}", "yy": f"{t.year % 100:02d}",
              "mm": f"{t.month:02d}", "dd": f"{t.day:02d}", "doy": f"{doy:03d}", "hh": f"{t.hour:02d}",
              "h": chr(ord("a") + t.hour), "H": chr(ord("A") + t.hour)}
    return template.format(**values)


def download_guide(station: Station, start: datetime, end: datetime) -> str:
    """Qué bajar y de dónde para cubrir [start, end] (UTC), en palabras, según el paquete de la red."""
    net = _network_of(station)
    d = net.download
    lines: list[str] = []
    if d.get("type") == "url-template":
        lines.append(f"{net.title}: el programa puede bajar los RINEX de {station.id} directamente:")
        for t in _slots(net, start, end):
            lines.append("• " + _fill(d["url"], station, t))
        return "\n".join(lines)
    page = d.get("url") or net.source.get("url", "")
    lines.append(f"Baja los RINEX de {station.id} ({net.title})" + (f" en {page}:" if page else ":"))
    hours = d.get("hourLabels")
    files = d.get("filePattern")
    for day_start in _days(start, end):
        a = max(start.astimezone(timezone.utc) - timedelta(minutes=10), day_start)
        b = min(end.astimezone(timezone.utc) + timedelta(minutes=10), day_start + timedelta(days=1, microseconds=-1))
        text = f"• Fecha {day_start:%Y-%m-%d} (UTC)"
        if hours and len(hours) == 24:
            text += f": hora inicial «{hours[a.hour]}» y hora final «{hours[b.hour]}»"
        text += f", de {a:%H}:00 a {(b.hour + 1) % 24:02d}:00 UTC"
        if files:
            names = [_fill(files, station, day_start.replace(hour=h)) for h in range(a.hour, b.hour + 1)]
            text += f" (archivos {names[0]}…{names[-1]})" if len(names) > 1 else f" (archivo {names[0]})"
        lines.append(text + ".")
    if d.get("instructions"):
        lines.append(d["instructions"])
    return "\n".join(lines)


def _days(start: datetime, end: datetime) -> list[datetime]:
    a = (start.astimezone(timezone.utc) - timedelta(minutes=10)).replace(hour=0, minute=0, second=0, microsecond=0)
    b = end.astimezone(timezone.utc) + timedelta(minutes=10)
    out = []
    while a <= b:
        out.append(a)
        a += timedelta(days=1)
    return out


def download_station_rinex(station: Station, start: datetime, end: datetime, workdir: Path,
                           progress: tools.Progress | None = None,
                           cancel: threading.Event | None = None) -> list[Path]:
    """Baja los RINEX de una estación de una red con dirección pública documentada (`url-template`). Solo a
    petición del usuario; dice qué baja y de dónde. Para redes con guía, UserError con la guía."""
    net = _network_of(station)
    if net.download.get("type") != "url-template":
        raise UserError(download_guide(station, start, end))
    folder = workdir / f"descarga_{station.id}"
    folder.mkdir(parents=True, exist_ok=True)
    slots = _slots(net, start, end)
    files: list[Path] = []
    missing: list[str] = []
    for i, t in enumerate(slots):
        tools.check(cancel)
        url = _fill(net.download["url"], station, t)
        target = folder / url.rsplit("/", 1)[-1]
        if progress:
            progress(i / max(1, len(slots)), f"Bajando {url}")
        if target.is_file() and target.stat().st_size > 0:
            files.append(target)
            continue
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(request, timeout=60) as response, open(tmp, "wb") as out:
                while True:
                    tools.check(cancel)
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
        except urllib.error.HTTPError as error:
            tmp.unlink(missing_ok=True)
            if error.code in (403, 404):
                missing.append(url)
                continue
            raise UserError(f"El servidor de {net.short_name} contestó {error.code} al bajar {url}.") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            tmp.unlink(missing_ok=True)
            raise UserError(f"No se pudo bajar {url}: revisa la conexión.") from error
        tmp.replace(target)
        files.append(target)
    if not files:
        raise UserError(f"{net.short_name} no tiene publicados los RINEX de {station.id} para esas fechas.")
    if missing and progress:
        progress(1.0, f"Faltan {len(missing)} archivo(s) en el servidor de {net.short_name}.")
    return files


# --- Registro en la biblioteca de datos firmados ----------------------------------------------------------------------

def validate_download(item: dict, data: bytes) -> None:
    """Comprobación de un paquete `cors-network` bajado del índice firmado, antes de instalarlo."""
    title = item.get("title", item.get("id", "red"))
    try:
        doc = json.loads(data.decode("utf-8"))
        net = parse_package(doc)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UserError(f"{title}: el archivo no es JSON legible; no se instala.") from None
    except (PackageError, KeyError, TypeError, ValueError) as error:
        raise UserError(f"{title}: el paquete de red no es válido ({error}); no se instala.") from None
    if net.id != item.get("id"):
        raise UserError(f"{title}: el archivo trae otra red («{net.id}»); no se instala.")


def _register() -> None:
    try:
        from ..datos import biblioteca
        biblioteca.register_kind(KIND, "redes", ".json", validate_download, "Red de estaciones de referencia")
    except Exception:
        pass


_register()
