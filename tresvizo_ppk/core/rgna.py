"""Catálogo de estaciones de la RGNA del INEGI y guía para bajar sus RINEX.

El catálogo (`data/rgna.json`) sale de la fuente oficial del INEGI con `scripts/update_rgna.py`: coordenadas de la
placa en ITRF2008, época 2010.0, altura vertical de la antena (placa → ARP), receptor y antena. Ver la atribución
en el propio archivo.

Descarga de RINEX (§3.2 de la especificación): el INEGI no publica un servicio documentado para programas. Su
página «Archivos RINEX de la RGNA» (https://www.inegi.org.mx/app/geo2/rgna/) es un formulario para personas, y su
servidor SFTP (geodesia2.inegi.org.mx) pide usuario y contraseña. La app no automatiza ninguna de las dos (ver
docs/estado.md): `download_guide()` dice qué estación, días y horas pedir en esa página, y el usuario arrastra lo que
baje.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from ..model import Ecef, Station
from .geodesy import geodetic_to_ecef

DATA = Path(__file__).resolve().parent / "data" / "rgna.json"
RINEX_PAGE = "https://www.inegi.org.mx/app/geo2/rgna/"
HOUR_LABELS = ["Primera", "Segunda", "Tercera", "Cuarta", "Quinta", "Sexta", "Séptima", "Octava", "Novena", "Décima",
               "Décima primera", "Décima segunda", "Décima tercera", "Décima cuarta", "Décima quinta",
               "Décima sexta", "Décima séptima", "Décima octava", "Décima novena", "Vigésima", "Vigésima primera",
               "Vigésima segunda", "Vigésima tercera", "Vigésima cuarta"]


@lru_cache(maxsize=1)
def _catalog_json() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def catalog_info() -> dict:
    """Fuente, fecha de corte, marco y atribución del catálogo (para el informe)."""
    data = _catalog_json()
    return {k: v for k, v in data.items() if k != "stations"}


def rgna_catalog() -> list[Station]:
    data = _catalog_json()
    source = f"{data['source']}, {data['source_cutoff']} (consultado el {data['retrieved']})"
    stations = []
    for row in data["stations"]:
        x, y, z = geodetic_to_ecef(row["latitude_deg"], row["longitude_deg"], row["ellipsoidal_height_m"])
        stations.append(Station(
            id=row["id"], name=f"{row['id']} — {row['location']}",
            position=Ecef(round(x, 4), round(y, 4), round(z, 4), data["frame"], float(data["epoch"])),
            sigma_enu=None, antenna_type=row["antenna_type"], radome=row.get("radome") or "NONE",
            antenna_height_m=float(row["antenna_height_m"]), receiver=row.get("receiver", ""),
            source=source, official=True))
    return stations


def find_station(marker: str) -> Station | None:
    """Estación del catálogo por el nombre del marcador del RINEX (las 4 primeras letras: «UGTO00MEX» → UGTO)."""
    key = (marker or "").strip().upper()
    if not key:
        return None
    candidates = [key, key[:4]]
    for station in rgna_catalog():
        if station.id in candidates:
            return station
    return None


def nearest_stations(position: Ecef, count: int = 3) -> list[tuple[Station, float]]:
    """Las estaciones más cercanas a una posición, con su distancia en metros."""
    out = []
    for station in rgna_catalog():
        p = station.position
        out.append((station, math.dist((p.x, p.y, p.z), (position.x, position.y, position.z))))
    out.sort(key=lambda item: item[1])
    return out[:count]


@dataclass
class DownloadRequest:
    station_id: str
    date: str                     # AAAA-MM-DD (UTC)
    first_hour: int               # 1–24, como el formulario del INEGI («Primera» = 00:00–01:00 UTC)
    last_hour: int
    first_label: str
    last_label: str
    files_hint: list[str]         # nombres de archivo horarios que entrega el INEGI («UGTO293o»…)


def download_guide(station_id: str, start: datetime, end: datetime, margin_min: float = 10.0) -> list[DownloadRequest]:
    """Qué pedir en la página de RINEX de la RGNA para cubrir [start, end] (UTC) con un margen.

    El INEGI entrega archivos horarios (horas UTC; la letra del archivo es la hora: a = 00 h … x = 23 h) y el
    formulario pide la fecha y la hora inicial y final de cada día por su ordinal («Primera» = 00–01 h).
    """
    start = start.astimezone(timezone.utc) - timedelta(minutes=margin_min)
    end = end.astimezone(timezone.utc) + timedelta(minutes=margin_min)
    requests: list[DownloadRequest] = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        day_end = day + timedelta(days=1)
        a = max(start, day)
        b = min(end, day_end - timedelta(microseconds=1))
        if a <= b:
            h0, h1 = a.hour, b.hour
            doy = a.timetuple().tm_yday
            files = [f"{station_id.upper()}{doy:03d}{chr(ord('a') + h)}" for h in range(h0, h1 + 1)]
            requests.append(DownloadRequest(station_id.upper(), day.strftime("%Y-%m-%d"), h0 + 1, h1 + 1,
                                            HOUR_LABELS[h0], HOUR_LABELS[h1], files))
        day = day_end
    return requests


def download_guide_text(station_id: str, start: datetime, end: datetime) -> str:
    """La guía en palabras, para el asistente."""
    lines = [f"Baja los RINEX de {station_id.upper()} en {RINEX_PAGE}:"]
    for r in download_guide(station_id, start, end):
        lines.append(f"• Fecha {r.date}: hora inicial «{r.first_label}» y hora final «{r.last_label}» "
                     f"(UTC {r.first_hour - 1:02d}:00 a {r.last_hour:02d}:00; archivos {r.files_hint[0]}…"
                     f"{r.files_hint[-1]}).")
    lines.append("Elige RINEX 3.04 (o 2.11) y «Descargar todos los archivos»; luego arrastra el ZIP aquí tal cual.")
    return "\n".join(lines)
