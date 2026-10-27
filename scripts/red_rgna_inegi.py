"""Arma el paquete de red CORS de la RGNA del INEGI (`tresvizo_ppk/core/data/redes/rgna-inegi.json`, formato en
docs/formato-red-cors.md) desde la fuente oficial: «Coordenadas Geodésicas de las Estaciones de la RGNA» (ITRF2008,
época 2010.0), https://www.inegi.org.mx/app/geo2/coordenadasGeoRGNA/.

Lo corre quien mantiene los datos (nunca la app): baja la misma tabla que enseña esa página (latitud, longitud y
altura geodésica de la placa, altura vertical de la antena de la placa a su base, receptor, antena del fabricante y
su nombre del NGS/IGS) y la fecha de corte que publica el INEGI, calcula las cartesianas en GRS80, añade las
calibraciones ANTEX de sus antenas (de un igs20_WWWW.atx) y escribe el paquete con su fuente, fecha, licencia y la
atribución que piden los Términos de Libre Uso del INEGI. Es el mismo archivo que se publica en el índice firmado.

Uso:  python scripts/red_rgna_inegi.py --antex RUTA/igs20_WWWW.atx [--from-json respuesta.json --cutoff TEXTO]
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
OUT = ROOT / "tresvizo_ppk" / "core" / "data" / "redes" / "rgna-inegi.json"


def _post(action: str):
    data = urllib.parse.urlencode({"accion": action}).encode()
    request = urllib.request.Request(URL + "InfoC.do", data=data,
                                     headers={"User-Agent": "TresVizo-Field-PPK/update_rgna"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def antex_name(text: str) -> str:
    """«TRM 55971.00» → «TRM55971.00»: el nombre IGS no lleva espacios internos."""
    return re.sub(r"\s+", "", text or "").upper()


HOUR_LABELS = ["Primera", "Segunda", "Tercera", "Cuarta", "Quinta", "Sexta", "Séptima", "Octava", "Novena", "Décima",
               "Décima primera", "Décima segunda", "Décima tercera", "Décima cuarta", "Décima quinta",
               "Décima sexta", "Décima séptima", "Décima octava", "Décima novena", "Vigésima", "Vigésima primera",
               "Vigésima segunda", "Vigésima tercera", "Vigésima cuarta"]

# Avisos por estación: del propio INEGI (página de RINEX) y de la validación del motor (docs/validacion.md).
STATION_NOTES = {
    "COL2": ["El INEGI avisa un desplazamiento de COL2 por el sismo del 19-09-2022 («Efecto sísmico en COL2», página "
             "de RINEX de la RGNA): su coordenada de 2010.0 puede no representar su posición actual."],
    "INEG": ["Aguascalientes tiene hundimiento del terreno por extracción de agua: en la validación del motor "
             "(20-10-2026), INEG estaba 0.73 m más abajo que su coordenada oficial de 2010.0 respecto de UGTO, "
             "UQRO e ISLP. Úsala solo con otras bases y revisa el ajuste."],
}


def antex_blocks(atx: Path, types: set[str]) -> tuple[str, str]:
    text = atx.read_text(encoding="latin-1")
    body = text.split("END OF HEADER", 1)[1]
    body = body[body.index("\n") + 1:]
    blocks = re.findall(r"[^\n]*START OF ANTENNA *\n.*?END OF ANTENNA *\n", body, re.S)
    kept = []
    for block in blocks:
        for line in block.splitlines():
            if line[60:].strip() == "TYPE / SERIAL NO":
                if not line[20:40].strip() and line[0:16].strip().upper() in types:
                    kept.append(block)
                break
    m = re.search(r"igs20_\d{4}", atx.name)
    return "".join(kept), (m.group(0) if m else atx.name)


def build(rows: list[dict], cutoff: str, atx: Path | None) -> dict:
    stations = []
    for row in rows:
        lat = dms_to_degrees(row["latitud_norte"])
        lon = -dms_to_degrees(row["longitud_oeste"])
        h = float(row["altura_geodesica"])
        x, y, z = geodetic_to_ecef(lat, lon, h)
        sid = row["nombre_est"].strip().upper()
        station = {
            "id": sid,
            "name": row["descripcion_est"].strip(),
            "latitude": round(lat, 11), "longitude": round(lon, 11), "height": h,
            "latitudeText": row["latitud_norte"].strip() + " N",
            "longitudeText": row["longitud_oeste"].strip() + " W",
            "x": round(x, 4), "y": round(y, 4), "z": round(z, 4),
            "sigma": None,
            "antennaHeight": float(row["altura_vertical_antena"]),
            "antennaType": antex_name(row.get("antena_national") or ""),
            "radome": "NONE",
            "antennaMakerName": (row.get("antena_fabricante") or "").strip(),
            "receiver": (row.get("receptor") or "").strip(),
            "status": "active",
            "log": (row.get("ftp_geodesia") or "").strip(),
        }
        if sid in STATION_NOTES:
            station["notes"] = STATION_NOTES[sid]
        stations.append(station)
    stations.sort(key=lambda s: s["id"])
    package = {
        "format": "tresvizo-cors-network",
        "formatVersion": 1,
        "id": "rgna-inegi",
        "revision": 1,
        "title": "RGNA — INEGI, México",
        "shortName": "RGNA",
        "countries": ["MX"],
        "authority": "Instituto Nacional de Estadística y Geografía (INEGI)",
        "frame": {"name": "ITRF2008", "epoch": 2010.0, "ellipsoid": "GRS80"},
        "referencePoint": "Las coordenadas están referidas a la placa (marca); la altura vertical de la antena va "
                          "de la placa a la base de la antena (ARP).",
        "source": {"title": "INEGI, Coordenadas Geodésicas de las Estaciones de la RGNA", "url": URL,
                   "cutoff": cutoff, "retrieved": date.today().isoformat()},
        "license": "Términos de Libre Uso de la Información del INEGI (https://www.inegi.org.mx/inegi/terminos.html): "
                   "uso, copia, adaptación y explotación comercial con atribución al INEGI. Transformación hecha "
                   "aquí: coordenadas cartesianas calculadas de las geodésicas publicadas, en GRS80; el nombre de "
                   "la antena sin espacios internos (como en el ANTEX de la IGS); radomo «NONE» supuesto (el INEGI "
                   "no lo publica en esta tabla; el del RINEX de la estación manda al procesar).",
        "attribution": "Fuente: INEGI, Coordenadas Geodésicas de las Estaciones de la RGNA (ITRF2008, época 2010.0).",
        "download": {
            "type": "guide",
            "url": "https://www.inegi.org.mx/app/geo2/rgna/",
            "granularity": "hourly",
            "hourLabels": HOUR_LABELS,
            "filePattern": "{ID}{doy}{h}",
            "instructions": "Elige la estación, la fecha y las horas, RINEX 3.04 (o 2.11) y «Descargar todos los "
                            "archivos»; luego arrastra el ZIP aquí tal cual. El INEGI no publica un servicio "
                            "para programas: la descarga la hace la persona en su página.",
        },
        "notes": ["No publica σ de las coordenadas."],
        "stations": stations,
    }
    if atx is not None:
        blocks, name = antex_blocks(atx, {s["antennaType"] for s in stations})
        package["antennaCalibrations"] = {"source": f"IGS, ANTEX {name}", "antex": blocks}
    return package


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-json", type=Path, help="respuesta guardada de InfoC.do?accion=MuestraTodo")
    parser.add_argument("--cutoff", default=None, help="fecha de corte del INEGI, si se lee de un archivo")
    parser.add_argument("--antex", type=Path, help="igs20_WWWW.atx de la IGS para las calibraciones de las antenas")
    args = parser.parse_args()
    if args.from_json:
        rows = json.loads(args.from_json.read_text(encoding="utf-8"))
        cutoff = args.cutoff or ""
    else:
        rows = _post("MuestraTodo")
        cutoff = str(_post("ultimaActualizacion")).strip().strip('"')
    if not rows:
        raise SystemExit("El INEGI no devolvió estaciones.")
    package = build(rows, cutoff, args.antex)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(package, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(package['stations'])} estaciones → {OUT.relative_to(ROOT)} (corte del INEGI: {cutoff})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
