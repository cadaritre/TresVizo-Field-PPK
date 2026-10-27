"""Lo común de los scripts que arman paquetes de red CORS (`cors-network`, docs/formato-red-cors.md) de otros países:
`scripts/red_noaa_cors.py`, `scripts/red_cacs_nrcan.py`, `scripts/red_ergnss_ign.py`.

Cada script baja la fuente oficial de su institución a `~/Documents/TresVizo-Field-PPK-datos/fuentes/redes/<id>/`
(fuera del repositorio), anota la dirección, la fecha y el SHA-256 de cada archivo en `FUENTE.md`, arma el paquete,
lo valida con `networks.parse_package` y lo deja en `~/Documents/TresVizo-Field-PPK-datos/redes/paquetes/<id>.json`,
que es la carpeta que se le pasa a `publicar.py --redes` del índice firmado (repositorio de TresVizo Field).

Lo corre quien mantiene los datos, nunca la app. Las descargas son públicas, sin cuenta ni contraseña.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tresvizo_ppk.core import networks  # noqa: E402
from tresvizo_ppk.core.geodesy import ecef_to_geodetic, geodetic_to_ecef  # noqa: E402

DATA_ROOT = Path.home() / "Documents" / "TresVizo-Field-PPK-datos"
SOURCES = DATA_ROOT / "fuentes" / "redes"
PACKAGES = DATA_ROOT / "redes" / "paquetes"
DEFAULT_ANTEX = DATA_ROOT / "fuentes" / "igs20_2437.atx"
USER_AGENT = "TresVizo-Field-PPK/redes"
# Aviso de estación cuya antena no tiene calibración publicada en ningún ANTEX oficial (decisión del 27-10-2026).
UNCALIBRATED_NOTE = ("Antena sin calibración publicada: la altura puede tener un error de centímetros; si hay otra "
                     "estación cerca, úsala.")


def mark_uncalibrated(stations: list[dict], missing: set[tuple[str, str]]) -> list[str]:
    """Pone el aviso de antena sin calibración a las estaciones cuya antena está en `missing`; devuelve sus claves."""
    out = []
    for s in stations:
        if (s.get("antennaType"), s.get("radome") or "NONE") in missing:
            notes = s.setdefault("notes", [])
            if not any(n.startswith(UNCALIBRATED_NOTE) for n in notes):
                notes.insert(0, f"{UNCALIBRATED_NOTE} ({s['antennaType']} {s.get('radome') or 'NONE'})")
            out.append(s["id"])
    return out


class Fuente:
    """La carpeta de la fuente oficial de una red: baja, guarda y anota cada archivo (URL, fecha, SHA-256)."""

    def __init__(self, network_id: str, folder: Path | None = None, offline: bool = False):
        self.folder = folder or SOURCES / network_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.files: list[tuple[str, str, str, str]] = []       # (nombre, url, fecha UTC, sha256)

    def get(self, url: str, name: str, data: bytes | None = None, retries: int = 3) -> bytes:
        """Baja `url` (POST si hay `data`) a la carpeta con el nombre `name`; con `offline`, lee lo ya bajado."""
        path = self.folder / name
        if self.offline:
            raw = path.read_bytes()
        else:
            for attempt in range(retries):
                try:
                    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(request, timeout=120) as response:
                        raw = response.read()
                    break
                except OSError:
                    if attempt == retries - 1:
                        raise
                    time.sleep(2 + 3 * attempt)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.files.append((name, url, stamp, hashlib.sha256(raw).hexdigest()))
        return raw

    def write_record(self, title: str, lines: list[str]) -> Path:
        """`FUENTE.md`: qué es, licencia y cómo se transformó (en `lines`) y la tabla de archivos bajados."""
        out = [f"# {title}", "", *lines, "", "## Archivos bajados", "",
               "| Archivo | Dirección | Fecha | SHA-256 |", "| --- | --- | --- | --- |"]
        seen = set()
        for name, url, stamp, digest in self.files:
            if name in seen:
                continue
            seen.add(name)
            out.append(f"| `{name}` | {url} | {stamp} | `{digest}` |")
        path = self.folder / "FUENTE.md"
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return path


def antex_name(text: str) -> str:
    """«TRM 55971.00» → «TRM55971.00»: el nombre IGS no lleva espacios internos."""
    return re.sub(r"\s+", "", text or "").upper()


def bundled_antennas() -> set[tuple[str, str]]:
    """Las antenas de receptor que ya trae el ANTEX incluido en el programa: no hace falta repetirlas en un paquete."""
    from tresvizo_ppk.core import antex
    return set(antex._bundled_models())


def _antex_index(atx: Path) -> dict[tuple[str, str], str]:
    text = atx.read_text(encoding="latin-1")
    body = text.split("END OF HEADER", 1)[1]
    body = body[body.index("\n") + 1:]
    by_key: dict[tuple[str, str], str] = {}
    for block in re.findall(r"[^\n]*START OF ANTENNA *\n.*?END OF ANTENNA *\n", body, re.S):
        for line in block.splitlines():
            if line[60:].strip() == "TYPE / SERIAL NO":
                if not line[20:40].strip():          # las de satélite llevan el PRN en el número de serie
                    by_key.setdefault((line[0:16].strip().upper(), line[16:20].strip().upper() or "NONE"), block)
                break
    return by_key


def antex_label(atx: Path) -> str:
    m = re.search(r"(igs20_\d{4}|ngs20)", atx.name)
    return m.group(1) if m else atx.name


def antex_blocks(sources: Path | list[tuple[Path, str]], used: set[tuple[str, str]],
                 skip: set[tuple[str, str]] | None = None) -> tuple[str, str, set[tuple[str, str]]]:
    """Calibraciones de las antenas (tipo, radomo) que usan las estaciones, de los ANTEX dados en orden de
    preferencia (primero el de la IGS; luego, si hace falta, otro oficial como el ngs20.atx del NGS). Por antena: el
    radomo exacto o, si no está, el mismo tipo sin radomo («NONE»), que es el que usaría el programa. Se salta lo que
    ya trae el ANTEX incluido en el programa (`skip`). Los bloques van sin tocar una línea.

    Devuelve los bloques, el texto de la fuente («IGS, ANTEX igs20_2437; NGS, ngs20.atx (ASH111661 NONE)») y las
    antenas que no están en ninguno."""
    if isinstance(sources, Path):
        sources = [(sources, f"IGS, ANTEX {antex_label(sources)}")]
    indexes = [(_antex_index(path), label) for path, label in sources]
    skip = skip if skip is not None else bundled_antennas()
    chosen: dict[tuple[str, str], tuple[str, int]] = {}
    missing: set[tuple[str, str]] = set()
    for kind, dome in sorted(used):
        found = None
        for candidate in ((kind, dome), (kind, "NONE")):
            if candidate in skip:
                found = ("skip", -1)
                break
            for rank, (index, _) in enumerate(indexes):
                if candidate in index:
                    found = (candidate, rank)
                    break
            if found:
                break
        if found is None:
            missing.add((kind, dome))
        elif found[1] >= 0:
            key, rank = found
            chosen[key] = (indexes[rank][0][key], rank)
    parts = [indexes[0][1]]
    for rank in range(1, len(indexes)):
        extra = sorted(" ".join(k) for k, (_, r) in chosen.items() if r == rank)
        if extra:
            parts.append(f"{indexes[rank][1]} ({', '.join(extra)})")
    return "".join(chosen[k][0] for k in sorted(chosen)), "; ".join(parts), missing


def parse_site_log(text: str) -> dict:
    """Equipo vigente de un «site log» de la IGS: la última antena (§4) y el último receptor (§3) sin fecha de retiro."""
    def sections(prefix: str) -> list[dict]:
        out, cur = [], None
        for line in text.splitlines():
            m = re.match(rf"^{prefix}\.(\d+)\s+(\S.*?)\s*:\s*(.*)$", line)
            if m and not m.group(1).startswith("x"):
                cur = {"_": m.group(3).strip()}
                out.append(cur)
                continue
            m = re.match(r"^\s{3,}(\S.*?)\s*:\s*(.*)$", line)
            if cur is not None and m:
                cur[m.group(1).strip()] = m.group(2).strip()
            elif re.match(r"^\d+\.", line):
                cur = None
        return out
    antennas = [a for a in sections("4") if a["_"] and not a["_"].startswith("(")]
    receivers = [r for r in sections("3") if r["_"] and not r["_"].startswith("(")]
    out: dict = {}
    if antennas:
        a = antennas[-1]
        parts = a["_"].split()
        out["antenna"] = antex_name(parts[0]) if parts else ""
        out["radome"] = (a.get("Antenna Radome Type") or (parts[1] if len(parts) > 1 else "NONE")).split()[0].upper()
        try:
            out["antHt"] = float((a.get("Marker->ARP Up Ecc. (m)") or "nan").split()[0])
        except ValueError:
            out["antHt"] = float("nan")
        out["since"] = (a.get("Date Installed") or "")[:10]
    if receivers:
        out["receiver"] = receivers[-1]["_"]
    m = re.search(r"^\s*Site Name\s*:\s*(.+?)\s*$", text, re.M)
    if m:
        out["siteName"] = m.group(1)
    return out


def bounds(stations: list[dict], margin_deg: float = 0.5) -> dict:
    """Caja que cubre las estaciones (con margen), como la piden los índices. Si la red cruza el antimeridiano
    (Alaska), la caja va de −180 a 180 en longitud."""
    lats, lons = [], []
    for s in stations:
        if "latitude" in s:
            lat, lon = s["latitude"], s["longitude"]
        else:
            lat, lon, _ = ecef_to_geodetic(s["x"], s["y"], s["z"])
        lats.append(lat)
        lons.append(lon)
    west, east = min(lons) - margin_deg, max(lons) + margin_deg
    if west < -180 or east > 180:
        west, east = -180.0, 180.0
    return {"south": round(max(-90.0, min(lats) - margin_deg), 4), "north": round(min(90.0, max(lats) + margin_deg), 4),
            "west": round(west, 4), "east": round(east, 4)}


def complete_coordinates(station: dict) -> dict:
    """Pone las dos formas de la coordenada (geodésicas y cartesianas, GRS80) a partir de la que publica la fuente."""
    if "x" in station and "latitude" not in station:
        lat, lon, h = ecef_to_geodetic(station["x"], station["y"], station["z"])
        station["latitude"], station["longitude"], station["height"] = round(lat, 11), round(lon, 11), round(h, 4)
    elif "latitude" in station and "x" not in station:
        x, y, z = geodetic_to_ecef(station["latitude"], station["longitude"], station["height"])
        station["x"], station["y"], station["z"] = round(x, 4), round(y, 4), round(z, 4)
    return station


def write_package(package: dict, out_dir: Path = PACKAGES) -> Path:
    """Valida el paquete con el mismo lector de la app y lo escribe (UTF-8, una estación por bloque)."""
    networks.parse_package(json.loads(json.dumps(package)))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{package['id']}.json"
    path.write_text(json.dumps(package, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def today() -> str:
    return date.today().isoformat()
