"""Arma el paquete de red CORS de la NOAA CORS Network (NCN) del NGS, Estados Unidos (`noaa-cors`, formato en
docs/formato-red-cors.md) desde las fuentes oficiales del NGS en https://geodesy.noaa.gov/corsdata/:

- `coord/coord_20/nad83_2011_xyz.comp.txt` y `.htdp.txt`: cartesianas del punto de referencia de la antena (ARP) en
  **NAD 83(2011), época 2010.0**, el marco en que trabajan los sistemas de Estados Unidos (State Plane, UTM) y en el que
  amarra GEOID18. `nad83_pa11_*` (Hawái y el Pacífico) y `nad83_ma11_*` (Guam y las Marianas) dan las de las
  estaciones de esas placas, cada una con su marco. Las `.comp` llevan velocidades calculadas de la serie de tiempo;
  las `.htdp`, modeladas con HTDP (estaciones nuevas): manda la `.comp` si la estación está en las dos.
- `coord/coord_20/nad83_2011_geo.comp.txt` (y las de PA11/MA11): las mismas posiciones en geodésicas, con las
  velocidades norte, este y arriba; el script comprueba que las geodésicas que calcula de las cartesianas coincidan.
- `station_log/cumulative.station.info.cors`: el equipo vigente de cada estación (antena, radomo y receptor).

El NGS publica las coordenadas de la **ARP**, no de la marca: el paquete las lleva así, con altura de antena 0 (el
programa usa la del catálogo, que es la del NGS). Se incluyen las estaciones operativas, no operativas temporalmente y
suspendidas; las dadas de baja («Decommissioned») y las de la IGS que no son CORS, no.

Licencia: obra del Gobierno de Estados Unidos (NOAA/NGS), de dominio público (17 U.S.C. §105).

Uso:  python scripts/red_noaa_cors.py [--antex RUTA/igs20_WWWW.atx] [--sin-descargar] [--salida CARPETA]
"""
from __future__ import annotations

import argparse
import math
import re
import string
from pathlib import Path

from red_comun import (DEFAULT_ANTEX, PACKAGES, Fuente, antex_blocks, antex_name, bounds,  # noqa: F401
                       mark_uncalibrated, parse_site_log, today, write_package)
from tresvizo_ppk.core.geodesy import ecef_to_geodetic, geodetic_to_ecef

BASE = "https://geodesy.noaa.gov/corsdata/"
COORD = BASE + "coord/coord_20/"
FRAMES = {"2011": "NAD83(2011)", "pa11": "NAD83(PA11)", "ma11": "NAD83(MA11)"}
KEEP_STATUS = {"Operational", "Non-Operational", "Suspended"}
# Las claves de país de los archivos del NGS son FIPS 10-4; el índice usa ISO 3166-1. Los territorios de Estados
# Unidos (clave «US» con el estado) van con su código ISO propio, como en el catálogo de sistemas (crs-pr).
FIPS_TO_ISO = {"US": "US", "CA": "CA", "BF": "BS", "ML": "MH"}
TERRITORY_ISO = {"PR": "PR", "VI": "VI", "GU": "GU", "CQ": "MP", "AS": "AS"}
DOWNLOAD_URL = BASE + "rinex/{yyyy}/{doy}/{id}/{id}{doy}0.{yy}d.gz"
FAST_H_MM = 10.0        # mm/año horizontal en NAD 83 a partir del cual la estación lleva aviso de movimiento
FAST_U_MM = 5.0


def _rows(text: str) -> list[list[str]]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 9 and re.fullmatch(r"[A-Z0-9]{4}", parts[0]) and re.fullmatch(r"\d{4}\.\d+", parts[1]):
            out.append(parts)
    return out


def parse_xyz(text: str) -> dict[str, dict]:
    """SITE EPOCH x y z Vx Vy Vz Ctry State Status."""
    out = {}
    for p in _rows(text):
        sid, epoch, x, y, z = p[0], float(p[1]), float(p[2]), float(p[3]), float(p[4])
        status = p[-1]
        state, country = p[-2], p[-3]
        out[sid] = {"epoch": epoch, "x": x, "y": y, "z": z, "country": country, "state": state, "status": status}
    return out


_DMS = re.compile(r"(\d+)\s+(\d+)\s+([\d.]+)\s+([NSEW])")


def parse_geo(text: str) -> dict[str, dict]:
    """SITE EPOCH lat(D M S N) lon(D M S W) h Vn Ve Vu Ctry State Status."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z0-9]{4})\s+(\d{4}\.\d+)\s+(.*)$", line)
        if not m:
            continue
        rest = m.group(3)
        found = list(_DMS.finditer(rest))
        if len(found) < 2:
            continue
        dms = [f.groups() for f in found[:2]]
        tail = rest[found[1].end():].split()
        lat = (float(dms[0][0]) + float(dms[0][1]) / 60 + float(dms[0][2]) / 3600) * (-1 if dms[0][3] == "S" else 1)
        lon = (float(dms[1][0]) + float(dms[1][1]) / 60 + float(dms[1][2]) / 3600) * (-1 if dms[1][3] == "W" else 1)
        out[m.group(1)] = {"latitude": lat, "longitude": lon, "height": float(tail[0]),
                           "vn": float(tail[1]), "ve": float(tail[2]), "vu": float(tail[3])}
    return out


def parse_station_info(text: str) -> dict[str, dict]:
    """El último periodo de equipo de cada estación del station.info acumulado del NGS (formato de GAMIT)."""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        if not line.startswith(" ") or len(line) < 100:
            continue
        sid = line[1:5].strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{4}", sid):
            continue
        name = line[7:24].strip()
        start = line[25:43].split()
        stop = line[44:62].split()
        try:
            ant_ht = float(line[63:72])
        except ValueError:
            continue
        code = line[72:79].strip()
        m = re.search(r">>(.+?)\s{2,}", line)
        receiver = m.group(1).strip() if m else ""
        # «>>ANTENA  RADOMO  S/N= >>serie <»: la serie puede llevar espacios, así que se busca la antena por su forma
        found = re.findall(r">>(\S+)\s+(\S{4})\s+S/N=", line)
        antenna, radome = found[-1] if found else ("", "")
        row = {"name": name, "start": start, "stop": stop, "antHt": ant_ht, "htCode": code, "receiver": receiver,
               "antenna": antex_name(antenna), "radome": (radome or "NONE").upper()}
        prev = out.get(sid)
        if prev is None or tuple(map(int, start)) >= tuple(map(int, prev["start"])):
            out[sid] = row
    return out


def _since(start: list[str]) -> str:
    from datetime import date, timedelta
    year, doy = int(start[0]), int(start[1])
    return (date(year, 1, 1) + timedelta(days=doy - 1)).isoformat()


def _title_name(text: str) -> str:
    """«LOUISIANA STATE U» → «Louisiana State U»; «GlassMtn__CN2006» → «GlassMtn CN2006» (sin tocar lo mixto)."""
    text = re.sub(r"_+", " ", text).strip()
    if text.isupper() or text.islower():
        return string.capwords(text)
    return text


def build(xyz: dict[str, dict[str, dict]], geo: dict[str, dict[str, dict]], info: dict[str, dict],
          atx: Path | None, retrieved: str, cutoff: str) -> tuple[dict, dict]:
    """Paquete y un resumen de lo comprobado. `xyz`/`geo`: {marco: {estación: fila}} (marco «2011», «pa11», «ma11»)."""
    chosen: dict[str, tuple[str, dict]] = {}
    for key in ("2011", "pa11", "ma11"):          # PA11 y MA11 mandan sobre 2011 en sus placas
        for sid, row in xyz.get(key, {}).items():
            if row["status"] not in KEEP_STATUS or row["country"] == "--":
                continue
            chosen[sid] = (key, row)
    stations, used, report = [], set(), {"geo_max_m": 0.0, "no_equipment": [], "frames": {}, "countries": set()}
    for sid in sorted(chosen):
        key, row = chosen[sid]
        frame = FRAMES[key]
        lat, lon, h = ecef_to_geodetic(row["x"], row["y"], row["z"])
        g = geo.get(key, {}).get(sid)
        if g is not None:                          # las geodésicas publicadas, contra las cartesianas publicadas
            gx, gy, gz = geodetic_to_ecef(g["latitude"], g["longitude"], g["height"])
            report["geo_max_m"] = max(report["geo_max_m"], math.dist((gx, gy, gz), (row["x"], row["y"], row["z"])))
        eq = info.get(sid)
        country = TERRITORY_ISO.get(row["state"]) if row["country"] == "US" else None
        country = country or FIPS_TO_ISO.get(row["country"], row["country"])
        report["countries"].add(country)
        report["frames"][frame] = report["frames"].get(frame, 0) + 1
        station = {
            "id": sid,
            "name": f"{_title_name(eq['name']) if eq else sid} ({row['state']}, {country})",
            "latitude": round(lat, 11), "longitude": round(lon, 11), "height": round(h, 4),
            "x": row["x"], "y": row["y"], "z": row["z"],
            "antennaHeight": 0.0,
            "status": "active" if row["status"] == "Operational" else row["status"].lower(),
            "country": country,
        }
        if key != "2011":
            station["frame"] = frame
        if not (eq and eq["antenna"]):
            # Sin antena conocida la altura sale mal: la estación no entra (decisión del 27-10-2026).
            report["no_equipment"].append(sid)
            continue
        if eq and eq["antenna"]:
            station.update(antennaType=eq["antenna"], radome=eq["radome"], receiver=eq["receiver"],
                           equipmentSince=eq.get("since") or _since(eq["start"]))
            used.add((eq["antenna"], eq["radome"]))
        else:
            report["no_equipment"].append(sid)
        notes = []
        if g is not None:
            station["velocity"] = {"n": g["vn"], "e": g["ve"], "u": g["vu"], "unit": "mm/a"}
            vh = math.hypot(g["vn"], g["ve"])
            if vh >= FAST_H_MM or abs(g["vu"]) >= FAST_U_MM:
                years = max(0.0, int(retrieved[:4]) - 2010.0)
                notes.append(f"Según el NGS, {sid} se mueve {vh:.0f} mm/año en horizontal y {g['vu']:+.0f} mm/año "
                             f"en vertical respecto de {frame}: hoy está a unos {vh * years / 10:.0f} cm de su "
                             "coordenada de 2010.0. Los puntos cercanos se mueven parecido; en líneas largas o con "
                             "otras bases, revisa el ajuste.")
        if row["status"] != "Operational":
            notes.append(f"El NGS la marca como «{row['status']}»: puede no tener datos recientes.")
        if notes:
            station["notes"] = notes
        stations.append(station)
    package = {
        "format": "tresvizo-cors-network",
        "formatVersion": 1,
        "id": "noaa-cors",
        "revision": 1,
        "version": cutoff,
        "title": "NOAA CORS — NGS, Estados Unidos",
        "shortName": "NOAA CORS",
        "countries": sorted(report["countries"]),
        "region": "Estados Unidos y sus territorios, con algunas estaciones de Canadá, las Bahamas y el Pacífico",
        "authority": "National Geodetic Survey (NGS), NOAA",
        "frame": {"name": "NAD83(2011)", "epoch": 2010.0, "ellipsoid": "GRS80"},
        "referencePoint": "Las coordenadas del NGS son del punto de referencia de la antena (ARP), no de la marca: la "
                          "altura de antena del catálogo es 0. Las estaciones de Hawái y el Pacífico van en "
                          "NAD83(PA11) y las de Guam y las Marianas en NAD83(MA11), época 2010.0.",
        "source": {"title": "NGS, NOAA CORS Network: coordenadas NAD 83(2011, PA11, MA11) época 2010.0 "
                            "(coord_20) y equipo de las estaciones (cumulative.station.info.cors)",
                   "url": COORD, "cutoff": cutoff, "retrieved": retrieved},
        "license": "Dominio público: obra del Gobierno de Estados Unidos (NOAA/NGS, 17 U.S.C. §105); el NGS "
                   "redistribuye los datos de sus CORS sin costo («NGS redistributes their data to the public free "
                   "of charge», corsdata/readme.txt). Transformación hecha aquí: geodésicas calculadas en GRS80 de "
                   "las cartesianas publicadas (coinciden con las geodésicas del NGS), nombre de la antena y "
                   "radomo del equipo vigente del station.info del NGS, y avisos por estación de su velocidad.",
        "licenseURL": "https://geodesy.noaa.gov/corsdata/readme.txt",
        "attribution": "Fuente: NOAA National Geodetic Survey, NOAA CORS Network (NAD 83(2011), época 2010.0).",
        "download": {
            "type": "url-template",
            "granularity": "daily",
            "url": DOWNLOAD_URL,
            "instructions": "Archivos diarios de 30 s del NGS (RINEX 2.11 con compresión Hatanaka), documentados en "
                            "https://geodesy.noaa.gov/corsdata/readme.txt. Los archivos horarios solo se guardan "
                            "dos días; para otra cadencia, la página UFCORS del NGS (https://geodesy.noaa.gov/UFCORS).",
        },
        "notes": [
            "Coordenadas NAD 83(2011) época 2010.0 del NGS: el resultado queda en ese marco y época (el programa no "
            "transforma épocas). En el oeste del país las estaciones se mueven centímetros por año; el aviso de cada "
            "una lo dice.",
            "La antena y la coordenada son las del equipo vigente (`equipmentSince`): para datos de antes de un "
            "cambio de antena, el NGS publica otra coordenada en coord_20/Old.",
            "No publica σ de las coordenadas en estos archivos.",
        ],
        "stations": stations,
    }
    if report["no_equipment"]:
        package["notes"].append("Fuera del paquete por no tener su antena en el station.info ni en el site log del "
                                "NGS (sin antena la altura sale mal): " + ", ".join(report["no_equipment"]) + ".")
    if atx:
        blocks, source, missing = antex_blocks(atx, used)
        package["antennaCalibrations"] = {"source": source, "antex": blocks}
        report["antex_missing"] = sorted(missing)
        report["uncalibrated"] = mark_uncalibrated(stations, missing)
        report["antex_source"] = source
    package["bounds"] = bounds(stations)
    return package, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--antex", type=Path, default=DEFAULT_ANTEX, help="igs20_WWWW.atx de la IGS")
    parser.add_argument("--ngs-antex", type=Path, default=None,
                        help="ngs20.atx del NGS (si no se da, se baja de https://geodesy.noaa.gov/ANTCAL/)")
    parser.add_argument("--sin-descargar", action="store_true", help="usa los archivos ya bajados")
    parser.add_argument("--salida", type=Path, default=PACKAGES)
    args = parser.parse_args()
    src = Fuente("noaa-cors", offline=args.sin_descargar)
    xyz, geo = {}, {}
    for key in ("2011", "pa11", "ma11"):
        xyz[key] = {}
        for kind in ("htdp", "comp"):              # la .comp manda: se lee después
            name = f"nad83_{key}_xyz.{kind}.txt"
            xyz[key].update(parse_xyz(src.get(COORD + name, name).decode("latin-1")))
        name = f"nad83_{key}_geo.comp.txt"
        geo[key] = parse_geo(src.get(COORD + name, name).decode("latin-1"))
        name = f"nad83_{key}_geo.htdp.txt"
        for sid, row in parse_geo(src.get(COORD + name, name).decode("latin-1")).items():
            geo[key].setdefault(sid, row)
    head = src.get(COORD + "nad83_2011_xyz.comp.txt", "nad83_2011_xyz.comp.txt").decode("latin-1")
    cutoff = head.strip().splitlines()[0].strip()
    info = parse_station_info(src.get(BASE + "station_log/cumulative.station.info.cors",
                                      "cumulative.station.info.cors").decode("latin-1"))
    src.get(BASE + "readme.txt", "readme.txt")
    # Las que no están en el station.info acumulado: su «site log» de la IGS en station_log/.
    wanted = {sid for key in xyz for sid, row in xyz[key].items()
              if row["status"] in KEEP_STATUS and row["country"] != "--"}
    for sid in sorted(wanted - set(info)):
        name = f"logs/{sid.lower()}.log.txt"
        try:
            log = parse_site_log(src.get(BASE + f"station_log/{sid.lower()}.log.txt", name).decode("latin-1"))
        except OSError:
            continue
        if log.get("antenna"):
            info[sid] = {"name": log.get("siteName") or sid, "start": ["1900", "1"], "antenna": log["antenna"], "radome": log["radome"],
                         "receiver": log.get("receiver", ""), "since": log.get("since", "")}
    from datetime import datetime
    cutoff_iso = datetime.strptime(cutoff, "%B %d, %Y").date().isoformat()
    # Calibraciones: primero el ANTEX de la IGS; las antenas que no estén ahí, del ngs20.atx del NGS (calibraciones
    # absolutas propias del NGS, dominio público).
    ngs_atx = args.ngs_antex
    if ngs_atx is None:
        src.get("https://geodesy.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx", "ngs20.atx")
        ngs_atx = src.folder / "ngs20.atx"
    from red_comun import antex_label
    atx = [(args.antex, f"IGS, ANTEX {antex_label(args.antex)}"), (ngs_atx, "NGS, ANTEX ngs20.atx")]
    package, report = build(xyz, geo, info, atx, today(), cutoff_iso)
    out = write_package(package, args.salida)
    src.write_record("NOAA CORS Network (NGS) — fuente del paquete `noaa-cors`", [
        f"Corte de las coordenadas del NGS: {cutoff}. Consultado el {today()}.",
        "",
        "- Marco: NAD 83(2011) época 2010.0 (PA11 y MA11 en sus placas), GRS80; coordenadas de la ARP.",
        "- Licencia: dominio público (obra del Gobierno de EE. UU., 17 U.S.C. §105). Uso: «NGS redistributes their "
        "data to the public free of charge» (readme.txt).",
        "- Descarga de RINEX documentada en readme.txt: rinex/{yyyy}/{ddd}/{ssss}/{ssss}{ddd}0.{yy}d.gz.",
        f"- Estaciones en el paquete: {len(package['stations'])} ({report['frames']}); países {sorted(report['countries'])}.",
        f"- Geodésicas calculadas contra las publicadas: diferencia máxima {report['geo_max_m'] * 1000:.1f} mm.",
        f"- Sin equipo en el station.info: {', '.join(report['no_equipment']) or 'ninguna'}.",
        f"- Calibraciones de antena: {report.get('antex_source', '')}.",
        f"- Antenas sin calibración en el ANTEX de la IGS ni en el del NGS: "
        f"{', '.join(' '.join(a) for a in report.get('antex_missing', [])) or 'ninguna'}; estaciones con el aviso "
        f"«antena sin calibración»: {', '.join(report.get('uncalibrated', [])) or 'ninguna'}. El NGS y la IGS "
        "calibran una «MPL_WAAS_2225NW», pero el station.info del NGS nombra la antena «MPL_WAAS_2225N»: no se supone "
        "que sean la misma.",
    ])
    print(f"{len(package['stations'])} estaciones → {out} ({report['frames']}); geodésicas ±"
          f"{report['geo_max_m'] * 1000:.1f} mm; sin equipo {len(report['no_equipment'])}; antenas sin ANTEX "
          f"{len(report.get('antex_missing', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
