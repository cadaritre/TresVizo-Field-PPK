"""Arma el paquete de red CORS del Sistema Canadiense de Control Activo (CACS) de Recursos Naturales Canadá
(`cacs-nrcan`, formato en docs/formato-red-cors.md) desde las fuentes oficiales del Levantamiento Geodésico de Canadá
(CGS, Canadian Geodetic Survey):

- la lista de estaciones que reparte su página CACS
  (https://webapp.csrs-scrs.nrcan-rncan.gc.ca/geod/data-donnees/cacs-scca.php, la consulta `process/getStations.php`
  que usa su propio mapa): clave, red y número único de cada estación;
- el informe de cada estación (`data-donnees/station/report-rapport.php?id=<número>`): coordenada oficial
  **NAD83(CSRS), época 2010.0** de la marca (latitud, longitud y altura elipsoidal, GRS80), sus velocidades, la antena
  vigente con su radomo y su altura (marca → ARP), el receptor y **la licencia que el CGS pone a esa estación**.

Licencia: cada informe dice la suya. Las del CGS van con la Open Government Licence – Canada (OGL-Canada), que
permite copiar, modificar y redistribuir, también con fines comerciales, con atribución. Las de la red de Nueva
Escocia (NSACS) llevan la «Nova Scotia License» provincial: **se dejan fuera** (decisión del 27-10-2026). Las
estaciones sin coordenada publicada, también.

Descarga de RINEX: el archivo público del CGS, https://cacs.nrcan.gc.ca/gps/data/gpsdata/<aa><ddd>/<aa>d/, que el
propio CGS anuncia para sus estaciones, con la misma licencia OGL-Canada en cada página; sin cuenta.

Uso:  python scripts/red_cacs_nrcan.py [--antex RUTA/igs20_WWWW.atx] [--ngs-antex RUTA/ngs20.atx] [--sin-descargar]
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import string
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

from red_comun import (DEFAULT_ANTEX, PACKAGES, SOURCES, Fuente, antex_blocks, antex_label, antex_name, bounds,
                       complete_coordinates, mark_uncalibrated, today, write_package)

WEB = "https://webapp.csrs-scrs.nrcan-rncan.gc.ca/geod/"
LIST_URL = WEB + "process/getStations.php"
REPORT_URL = WEB + "data-donnees/station/report-rapport.php?id={id}&locale=en"
ARCHIVE = "https://cacs.nrcan.gc.ca/gps/data/gpsdata/"
DOWNLOAD_URL = ARCHIVE + "{yy}{doy}/{yy}d/{id}{doy}0.{yy}d.Z"
OGL = "Open Government Licence - Canada"
PROVINCES = {"Ontario": "ON", "Quebec": "QC", "Québec": "QC", "British Columbia": "BC", "Alberta": "AB",
             "Nova Scotia": "NS", "Manitoba": "MB", "Saskatchewan": "SK", "New Brunswick": "NB",
             "Prince Edward Island": "PE", "Newfoundland and Labrador": "NL", "Newfoundland": "NL",
             "Northwest Territories": "NT", "Yukon": "YT", "Nunavut": "NU"}
FAST_H_MM, FAST_U_MM = 10.0, 5.0


def _angle(text: str) -> float:
    """«0 0 165440.847239» (grados, minutos y segundos; el CGS los da todos en segundos) o «W0 0 281056.906491»."""
    t = text.strip()
    sign = -1.0 if t[:1] in "WSws" else 1.0
    nums = [float(v) for v in re.findall(r"[-\d.]+", t)]
    d, m, s = (nums + [0.0, 0.0, 0.0])[:3]
    return sign * (abs(d) + m / 60.0 + s / 3600.0)


def _hidden(page: str, name: str) -> str | None:
    m = re.search(rf"<input[^>]*type='hidden'[^>]*name='{name}'[^>]*value='([^']*)'", page)
    if m is None:
        m = re.search(rf"<input[^>]*name='{name}'[^>]*type='hidden'[^>]*value='([^']*)'", page)
    return html.unescape(m.group(1)) if m else None


def _cells(fragment: str) -> list[str]:
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", fragment, re.S)]


def parse_report(page: str) -> dict:
    """Lo que el paquete toma del informe de una estación del CGS."""
    out: dict = {}
    ident = re.search(r"Site Identification.*?</tr>\s*<tr>(.*?)</tr>", page, re.S)
    if ident:
        cells = _cells(ident.group(1))
        if len(cells) >= 6:
            out.update(name=cells[0], province=cells[1], unique=cells[3], provincial=cells[4], network=cells[5])
    lat, lon, h = _hidden(page, "x"), _hidden(page, "y"), _hidden(page, "z")
    if lat and lon and h:
        out.update(latitude=_angle(lat), longitude=_angle(lon), height=float(h),
                   epoch=float(_hidden(page, "epoch") or "nan"), frame=_hidden(page, "frame") or "")
        try:
            out["velocity"] = {"n": float(_hidden(page, "vx")), "e": float(_hidden(page, "vy")),
                               "u": float(_hidden(page, "vz")), "unit": "mm/a"}
        except (TypeError, ValueError):
            pass
    rec = re.search(r"<th class='active'>Receiver</th>(.*?)</tr>", page, re.S)
    if rec:
        cells = _cells(rec.group(1))
        out["receiver"] = cells[0] if cells else ""
    ant = re.search(r"<h3 class='panel-title text-center'>Antenna</h3>.*?</tr>\s*<tr>.*?</tr>\s*<tr>(.*?)</tr>",
                    page, re.S)
    if ant:
        cells = _cells(ant.group(1))
        if len(cells) >= 3:
            parts = cells[0].split()
            out["antenna"] = antex_name(parts[0]) if parts else ""
            out["radome"] = (parts[1] if len(parts) > 1 else "NONE").upper()
            out["since"] = cells[1][:10]
            try:
                out["antHt"] = float(cells[2])
            except ValueError:
                pass
    m = re.search(r"<p>Use of ([^<]*)<a[^>]*href='([^']*)'[^>]*>([^<]*)</a>", page[page.find("Equipment on site"):])
    if m:
        out["license"], out["licenseURL"] = m.group(3).strip(), m.group(2)
    return out


def _day_listing(src: Fuente, day: date) -> set[str]:
    yy, doy = f"{day.year % 100:02d}", f"{day.timetuple().tm_yday:03d}"
    raw = src.get(f"{ARCHIVE}{yy}{doy}/{yy}d/", f"archivo_{yy}{doy}.html").decode("latin-1")
    return {m.upper() for m in re.findall(rf'href="([a-z0-9]{{4}}){doy}0\.{yy}d\.Z"', raw)}


def build(rows: list[dict], reports: dict[str, dict], present: set[str], atx, retrieved: str,
          check_day: str) -> tuple[dict, dict]:
    stations, used = [], set()
    report = {"left_out": {}, "frames": set()}
    for row in rows:
        sid = row["n"].strip().upper()
        rep = reports.get(sid, {})
        if not re.fullmatch(r"[A-Z0-9]{4}", sid):
            report["left_out"][row.get("name2") or row["id"]] = "sin clave de 4 caracteres"
            continue
        if row["net"] == "NSACS" or rep.get("license", "").startswith("Nova Scotia"):
            report["left_out"][sid] = "red de Nueva Escocia (NSACS), con licencia provincial"
            continue
        if rep.get("license") != OGL:
            report["left_out"][sid] = f"el informe no da la OGL-Canada ({rep.get('license') or 'sin licencia'})"
            continue
        if "latitude" not in rep:
            report["left_out"][sid] = "sin coordenada publicada"
            continue
        if not rep.get("antenna") or "antHt" not in rep:
            report["left_out"][sid] = "sin antena o altura de antena publicada"
            continue
        if rep.get("frame") != "NAD83CSRS" or rep.get("epoch") != 2010.0:
            report["left_out"][sid] = f"coordenada en {rep.get('frame')} {rep.get('epoch')}, no NAD83(CSRS) 2010.0"
            continue
        if sid in {s["id"] for s in stations}:
            report["left_out"][f"{sid} ({row['id']})"] = "clave repetida en la lista del CGS"
            continue
        province = rep.get("province", "")
        prov = next((v for k, v in PROVINCES.items() if k.lower() == province.lower().replace("&", "and")), province)
        place = (row.get("town") or "").strip() or \
            re.sub(r"\s+(CACS|RACS|WCDA|ACS|MTL)-.*$", "", rep.get("name") or sid).strip()
        station = complete_coordinates({
            "id": sid,
            "name": f"{string.capwords(place) if place.isupper() else place} ({prov})" if prov else place,
            "latitude": round(rep["latitude"], 11), "longitude": round(rep["longitude"], 11),
            "height": rep["height"],
            "antennaHeight": rep["antHt"], "antennaType": rep["antenna"], "radome": rep["radome"],
            "receiver": rep.get("receiver", ""), "status": "active" if row["net"] != "DISCONTINUED" else "discontinued",
            "equipmentSince": rep.get("since", ""), "cgsNetwork": row["net"], "cgsUniqueNumber": row["id"],
        })
        used.add((rep["antenna"], rep["radome"]))
        notes = []
        v = rep.get("velocity")
        if v:
            station["velocity"] = v
            vh = math.hypot(v["n"], v["e"])
            if vh >= FAST_H_MM or abs(v["u"]) >= FAST_U_MM:
                years = max(0.0, int(retrieved[:4]) - 2010.0)
                notes.append(f"Según el CGS, {sid} se mueve {vh:.0f} mm/año en horizontal y {v['u']:+.0f} mm/año en "
                             f"vertical respecto de NAD83(CSRS): hoy está a unos "
                             f"{math.hypot(vh, v['u']) * years / 10:.0f} cm de su coordenada de 2010.0.")
        if row["net"] == "DISCONTINUED":
            notes.append("El CGS la da como discontinuada: sirve para datos antiguos, no tiene datos recientes.")
        if sid not in present:
            notes.append(f"No estaba en el archivo público del CGS el {check_day}: puede que solo tenga datos en "
                         "fechas sueltas o por otros medios; si la descarga falla, elige otra estación.")
        if notes:
            station["notes"] = notes
        stations.append(station)
    stations.sort(key=lambda s: s["id"])
    package = {
        "format": "tresvizo-cors-network",
        "formatVersion": 1,
        "id": "cacs-nrcan",
        "revision": 1,
        "version": retrieved,
        "title": "CACS — NRCan, Canadá",
        "shortName": "CACS",
        "countries": ["CA"],
        "region": "Canadá",
        "authority": "Natural Resources Canada (NRCan), Canadian Geodetic Survey",
        "frame": {"name": "NAD83(CSRS)", "epoch": 2010.0, "ellipsoid": "GRS80"},
        "referencePoint": "Las coordenadas son de la marca de la estación; la altura de antena va de la marca al "
                          "punto de referencia de la antena (ARP), como en el informe del CGS.",
        "source": {"title": "Canadian Geodetic Survey, CACS: informes de estación (NAD83(CSRS) v8, época 2010.0)",
                   "url": WEB + "data-donnees/cacs-scca.php", "cutoff": f"informes consultados el {retrieved}",
                   "retrieved": retrieved},
        "license": "Open Government Licence – Canada (https://open.canada.ca/en/open-government-licence-canada): "
                   "copiar, modificar y redistribuir, también con fines comerciales, con atribución. Es la licencia "
                   "que el informe de cada estación del CGS pone a sus datos; las de Nueva Escocia (licencia "
                   "provincial) no están en el paquete. Transformación hecha aquí: cartesianas calculadas en GRS80 "
                   "de las geodésicas publicadas, nombre de la antena sin espacios y avisos por estación (movimiento, "
                   "archivo de datos).",
        "licenseURL": "https://open.canada.ca/en/open-government-licence-canada",
        "attribution": "Contiene información con licencia de la Open Government Licence – Canada. Fuente: Natural "
                       "Resources Canada, Canadian Geodetic Survey, CACS (NAD83(CSRS), época 2010.0).",
        "download": {
            "type": "url-template",
            "granularity": "daily",
            "url": DOWNLOAD_URL,
            "instructions": "Archivos diarios de 30 s del archivo público del CGS (RINEX 2.11 con compresión "
                            "Hatanaka y compress), https://cacs.nrcan.gc.ca/gps/data/gpsdata/, con licencia "
                            "OGL-Canada.",
        },
        "notes": [
            "Coordenadas NAD83(CSRS) época 2010.0 del CGS (rejilla de velocidades v8): el resultado queda en ese marco "
            "y época; el programa no transforma épocas.",
            "La antena y la coordenada son las del equipo vigente (`equipmentSince`).",
            "No publica σ de las coordenadas en un formato que el programa lea; el informe de cada estación da su "
            "covarianza.",
        ],
        "stations": stations,
    }
    if report["left_out"]:
        package["notes"].append("Fuera del paquete: " + "; ".join(f"{k}: {v}" for k, v in
                                                                  sorted(report["left_out"].items())) + ".")
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
    parser.add_argument("--antex", type=Path, default=DEFAULT_ANTEX)
    parser.add_argument("--ngs-antex", type=Path, default=SOURCES / "noaa-cors" / "ngs20.atx")
    parser.add_argument("--sin-descargar", action="store_true")
    parser.add_argument("--salida", type=Path, default=PACKAGES)
    args = parser.parse_args()
    src = Fuente("cacs-nrcan", offline=args.sin_descargar)
    body = urllib.parse.urlencode({"maxlatitude": 90, "minlatitude": -90, "minlongitude": -180,
                                   "maxlongitude": 180, "map": "cacs"}).encode()
    data = json.loads(src.get(LIST_URL, "getStations.json", data=body))
    rows = [s for net in data["networks"]["stations"] for s in net]
    reports = {}
    for row in rows:
        sid = row["n"].strip().upper()
        if not sid or row["net"] == "NSACS":
            continue
        page = src.get(REPORT_URL.format(id=row["id"]), f"informes/{row['id']}.html").decode("utf-8", "replace")
        reports.setdefault(sid, parse_report(page))
    check = date.today() - timedelta(days=10)
    present = _day_listing(src, check)
    atx = [(args.antex, f"IGS, ANTEX {antex_label(args.antex)}")]
    if args.ngs_antex and args.ngs_antex.is_file():
        atx.append((args.ngs_antex, "NGS, ANTEX ngs20.atx"))
    package, report = build(rows, reports, present, atx, today(), check.isoformat())
    out = write_package(package, args.salida)
    src.write_record("CACS de NRCan — fuente del paquete `cacs-nrcan`", [
        f"Consultado el {today()}.",
        "",
        "- Marco: NAD83(CSRS) v8, época 2010.0, GRS80; coordenadas de la marca y altura marca → ARP del informe.",
        "- Licencia: Open Government Licence – Canada, la que cada informe de estación del CGS da a sus datos "
        "(https://open.canada.ca/en/open-government-licence-canada). El archivo https://cacs.nrcan.gc.ca/ dice lo "
        "mismo en cada página.",
        f"- Estaciones en el paquete: {len(package['stations'])}.",
        f"- Fuera: {'; '.join(f'{k}: {v}' for k, v in sorted(report['left_out'].items())) or 'ninguna'}.",
        f"- Calibraciones de antena: {report.get('antex_source', '')}; sin calibración: "
        f"{', '.join(' '.join(a) for a in report.get('antex_missing', [])) or 'ninguna'} "
        f"(estaciones con aviso: {', '.join(report.get('uncalibrated', [])) or 'ninguna'}).",
        f"- Archivo de datos comprobado el {check.isoformat()}: {len(present)} estaciones con archivo diario.",
    ])
    print(f"{len(package['stations'])} estaciones → {out}; fuera {len(report['left_out'])}; sin ANTEX "
          f"{report.get('antex_missing')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
