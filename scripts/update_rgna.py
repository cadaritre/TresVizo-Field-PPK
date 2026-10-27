"""Regenera `tresvizo_ppk/core/data/rgna.json`, el catálogo de estaciones de la RGNA, desde la fuente oficial del
INEGI: «Coordenadas Geodésicas de las Estaciones de la RGNA» (ITRF2008, época 2010.0),
https://www.inegi.org.mx/app/geo2/coordenadasGeoRGNA/.

Lo corre quien mantiene el programa (nunca la app): baja la misma tabla que enseña esa página (latitud, longitud y
altura geodésica de la placa, altura vertical de la antena de la placa a su base, receptor, antena del fabricante y
su nombre del NGS/IGS) y la fecha de corte que publica el INEGI, calcula las coordenadas cartesianas en GRS80 y
escribe el catálogo con su fuente, fecha y la atribución que piden los Términos de Libre Uso del INEGI.

Uso:  python scripts/update_rgna.py [--from-json archivo.json]   (sin red, de una copia de la respuesta)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tresvizo_ppk.core.geodesy import dms_to_degrees, geodetic_to_ecef  # noqa: E402

URL = "https://www.inegi.org.mx/app/geo2/coordenadasGeoRGNA/"
OUT = ROOT / "tresvizo_ppk" / "core" / "data" / "rgna.json"


def _post(action: str):
    data = urllib.parse.urlencode({"accion": action}).encode()
    request = urllib.request.Request(URL + "InfoC.do", data=data,
                                     headers={"User-Agent": "TresVizo-Field-PPK/update_rgna"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def antex_name(text: str) -> str:
    """«TRM 55971.00» → «TRM55971.00»: el nombre IGS no lleva espacios internos."""
    return re.sub(r"\s+", "", text or "").upper()


def build(rows: list[dict], cutoff: str) -> dict:
    stations = []
    for row in rows:
        lat = dms_to_degrees(row["latitud_norte"])
        lon = -dms_to_degrees(row["longitud_oeste"])
        h = float(row["altura_geodesica"])
        x, y, z = geodetic_to_ecef(lat, lon, h)
        stations.append({
            "id": row["nombre_est"].strip().upper(),
            "location": row["descripcion_est"].strip(),
            "latitude_dms": row["latitud_norte"].strip() + " N",
            "longitude_dms": row["longitud_oeste"].strip() + " W",
            "latitude_deg": round(lat, 11),
            "longitude_deg": round(lon, 11),
            "ellipsoidal_height_m": h,
            "x": round(x, 4), "y": round(y, 4), "z": round(z, 4),
            "antenna_height_m": float(row["altura_vertical_antena"]),
            "receiver": (row.get("receptor") or "").strip(),
            "antenna_maker_name": (row.get("antena_fabricante") or "").strip(),
            "antenna_type": antex_name(row.get("antena_national") or ""),
            "radome": "NONE",
            "log_file": (row.get("ftp_geodesia") or "").strip(),
        })
    stations.sort(key=lambda s: s["id"])
    return {
        "title": "Coordenadas Geodésicas de las Estaciones de la RGNA",
        "source": "INEGI, Coordenadas Geodésicas de las Estaciones de la RGNA",
        "url": URL,
        "frame": "ITRF2008",
        "epoch": 2010.0,
        "ellipsoid": "GRS80",
        "reference_point": "Las coordenadas están referidas a la placa (marca); la altura vertical de la antena va "
                           "de la placa a la base de la antena (ARP).",
        "source_cutoff": cutoff,
        "retrieved": date.today().isoformat(),
        "attribution": "Fuente: INEGI, Coordenadas Geodésicas de las Estaciones de la RGNA (ITRF2008, época 2010.0).",
        "license": "Términos de Libre Uso de la Información del INEGI (https://www.inegi.org.mx/inegi/terminos.html): "
                   "uso, copia, adaptación y explotación comercial con atribución al INEGI. Transformación hecha "
                   "aquí: coordenadas cartesianas calculadas de las geodésicas publicadas, en GRS80; el nombre de "
                   "la antena sin espacios internos (como en el ANTEX de la IGS); radomo «NONE» supuesto (el INEGI "
                   "no lo publica en esta tabla; el del RINEX de la estación manda al procesar).",
        "notes": "No publica σ de las coordenadas. Regenerar con scripts/update_rgna.py.",
        "stations": stations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-json", type=Path, help="respuesta guardada de InfoC.do?accion=MuestraTodo")
    parser.add_argument("--cutoff", default=None, help="fecha de corte del INEGI, si se lee de un archivo")
    args = parser.parse_args()
    if args.from_json:
        rows = json.loads(args.from_json.read_text(encoding="utf-8"))
        cutoff = args.cutoff or ""
    else:
        rows = _post("MuestraTodo")
        cutoff = str(_post("ultimaActualizacion")).strip().strip('"')
    if not rows:
        raise SystemExit("El INEGI no devolvió estaciones.")
    catalog = build(rows, cutoff)
    OUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(catalog['stations'])} estaciones → {OUT.relative_to(ROOT)} (corte del INEGI: {cutoff})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
