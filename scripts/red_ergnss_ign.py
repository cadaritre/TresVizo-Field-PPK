"""Arma el paquete de red CORS de la Red de Estaciones Permanentes GNSS del IGN de España (ERGNSS, `ergnss-ign`,
formato en docs/formato-red-cors.md) desde la fuente oficial del Instituto Geográfico Nacional: la reseña de cada
estación (https://datos-geodesia.ign.es/ERGNSS/fichas/<CLAVE>.pdf), que da su coordenada oficial **ETRS89**
(realización ERGNSS de 2018: ETRF2000 en la época 2017.0, EPSG:1442) o, en Canarias, **REGCAN95** (ITRF93 en la
época 1994.9), en geodésicas y cartesianas, la antena con su radomo, su altura y el punto al que se mide, y el
receptor.

Se toman las cartesianas publicadas y se comprueba que las geodésicas publicadas coincidan con ellas. Quedan fuera,
con el motivo escrito en el paquete:
- UJAE: su reseña es de 2017, anterior al recálculo de las coordenadas ERGNSS de marzo de 2018 (~2 cm);
- BJCI (base antártica Juan Carlos I) y RAEG/RAE1 (Santa María, Azores, Portugal): no son estaciones del territorio
  español y sus archivos se llaman …ATA/…PRT;
- las reseñas con coordenada provisional (segundos y altura redondeados) o en las que geodésicas y cartesianas no
  coinciden.

Licencia: CC BY 4.0, la de la información geográfica del IGN (Orden FOM/2807/2015).

Uso:  python scripts/red_ergnss_ign.py [--antex RUTA/igs20_WWWW.atx] [--sin-descargar]
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from red_comun import (DEFAULT_ANTEX, PACKAGES, SOURCES, Fuente, antex_blocks, antex_label, antex_name, bounds,
                       mark_uncalibrated, today, write_package)
from tresvizo_ppk.core.geodesy import ecef_to_geodetic, geodetic_to_ecef

BASE = "https://datos-geodesia.ign.es/ERGNSS/"
FICHAS = BASE + "fichas/"
DOWNLOAD_URL = BASE + "diario_30s/{yyyy}/{yyyy}{mm}{dd}/{ID}00ESP_R_{yyyy}{doy}0000_01D_30S_MO.crx.gz"
EXCLUDED = {
    "UJAE": "su reseña es del 28-10-2017, anterior al recálculo de las coordenadas ERGNSS de marzo de 2018 (~2 cm)",
    "BJCI": "base antártica Juan Carlos I: fuera del territorio y sin coordenada en su reseña",
    "RAEG": "Santa María (Azores, Portugal): fuera del territorio español; sus archivos se llaman …00PRT",
    "RAE1": "Santa María (Azores, Portugal): fuera del territorio español; sus archivos se llaman …00PRT",
}
# Punto al que se mide la altura de antena en la reseña: los que equivalen a la ARP.
ARP_CODES = {"BPA", "BAM", "ARP", "BOTTOM OF ANTENNA MOUNT"}
MONTHS = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "oct": 10,
          "nov": 11, "dic": 12}


def _dms(text: str) -> float:
    m = re.search(r"(-?)\s*(\d+)\s*°\s*(\d+)\s*'\s*([\d,]+)", text)
    if not m:
        raise ValueError(text)
    value = int(m.group(2)) + int(m.group(3)) / 60 + float(m.group(4).replace(",", ".")) / 3600
    return -value if m.group(1) else value


def parse_ficha(text: str) -> dict:
    """Lo que el paquete toma de una reseña (texto de `pdftotext -layout`)."""
    out: dict = {}
    m = re.search(r"ERGNSS\s+(\d{1,2})-([a-z]{3})-(\d{4})", text)
    if m:
        out["date"] = date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat()
    m = re.search(r"Código\.*:\s*(\S+)", text)
    out["id"] = m.group(1).upper() if m else ""
    lines = text.splitlines()
    name_parts, muni_parts, prov = [], [], ""
    for i, line in enumerate(lines):
        if line.startswith("Nombre"):
            left, _, right = line.partition(":")[2].partition("  ")
            name_parts.append(left.strip())
            for nxt in lines[i + 1:i + 3]:          # el nombre sigue en la columna izquierda de las líneas siguientes
                if nxt.startswith(" ") and not re.match(r"\s*(Código|Instalación|Provincia)", nxt.strip()):
                    head = re.split(r"\s{2,}", nxt.strip())[0] if not nxt.startswith(" " * 30) else ""
                    if head and not head.startswith(("Provincia", "Código")):
                        name_parts.append(head)
                else:
                    break
        m = re.search(r"Municipio:\s*(.*)$", line)
        if m:
            muni_parts.append(m.group(1).strip())
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            segs = [g for g in re.split(r"\s{2,}", nxt.strip()) if g]
            # el municipio sigue en la columna derecha de la línea siguiente (a la izquierda va el nombre)
            if nxt.startswith(" " * 30) and segs:
                tail = segs[-1]
            elif len(segs) > 1 and nxt.startswith("Nombre"):
                tail = segs[-1]
            else:
                tail = ""
            if tail and not tail.startswith(("Provincia", "Código", "Nombre")):
                muni_parts.append(tail)
        m = re.search(r"Provincia:\s*(.*)$", line)
        if m:
            prov = m.group(1).strip()
    out["name"] = " ".join(p for p in name_parts if p)
    out["municipio"] = " ".join(p for p in muni_parts if p)
    out["provincia"] = prov
    m = re.search(r"Coordenadas\s+(ETRS89|REGCAN95)\s*:", text)
    out["frame"] = m.group(1) if m else ""
    lon = re.search(r"Longitud\.*:\s*(-?\s*\d+\s*°[^X\n]*?'')", text)
    lat = re.search(r"Latitud\.*:\s*(-?\s*\d+\s*°[^X\n]*?'')", text)
    h = re.search(r"Altitud elipsoidal:\s*([-\d.]+)\s*m", text)
    xyz = [re.search(rf"{axis}\.+:\s*([-\d.]+)\s*m", text) for axis in "XYZ"]
    if lon and lat and h:
        out.update(lonText=lon.group(1).strip(), latText=lat.group(1).strip(), longitude=_dms(lon.group(1)),
                   latitude=_dms(lat.group(1)), height=float(h.group(1)))
    if all(xyz):
        out.update(x=float(xyz[0].group(1)), y=float(xyz[1].group(1)), z=float(xyz[2].group(1)))
    m = re.search(r"Receptor:\s*(.*?)\s*\n", text)
    out["receiver"] = m.group(1).strip() if m else ""
    m = re.search(r"Antena:\s*(\S+)\s+(\S+)?\s*Altura:\s*([\d.]+)\s*m\.?\s*\(([^)]*)\)", text)
    if m:
        out.update(antenna=antex_name(m.group(1)), radome=(m.group(2) or "NONE").upper(), antHt=float(m.group(3)),
                   htCode=m.group(4).strip().upper())
    return out


def _provisional(f: dict) -> bool:
    """Coordenada redondeada (segundos con ,00000 y altura con .000): provisional, no la del cálculo."""
    return f["lonText"].endswith(",00000''") and f["latText"].endswith(",00000''") and \
        abs(f["height"] - round(f["height"])) < 1e-9


def build(fichas: list[dict], present: set[str], long_names: dict[str, str], atx, retrieved: str,
          check_day: str) -> tuple[dict, dict]:
    stations, used = [], set()
    report: dict = {"left_out": dict(EXCLUDED), "max_geo_m": 0.0, "frames": {}, "sign_fixed": []}
    for f in sorted(fichas, key=lambda r: r["id"]):
        sid = f["id"]
        if sid in EXCLUDED:
            continue
        if f.get("frame") not in ("ETRS89", "REGCAN95") or "x" not in f:
            report["left_out"][sid] = "su reseña no trae coordenada ETRS89 ni REGCAN95"
            continue
        if "antenna" not in f:
            report["left_out"][sid] = "su reseña no trae antena ni altura de antena"
            continue
        if f["htCode"] not in ARP_CODES:
            report["left_out"][sid] = f"altura de antena medida a «{f['htCode']}», no a la ARP"
            continue
        if "latitude" in f and _provisional(f):
            report["left_out"][sid] = "coordenada provisional en su reseña (segundos y altura redondeados)"
            continue
        lat, lon, h = ecef_to_geodetic(f["x"], f["y"], f["z"])
        if "latitude" in f:
            # algunas reseñas omiten el signo «−» de la longitud oeste: manda la cartesiana y se anota
            if abs(f["longitude"] + lon) < 1e-6 and abs(lon) > 1e-6:
                report["sign_fixed"].append(sid)
                f["longitude"] = -f["longitude"]
            d = math.dist(geodetic_to_ecef(f["latitude"], f["longitude"], f["height"]), (f["x"], f["y"], f["z"]))
            if d > 0.005:
                report["left_out"][sid] = f"geodésicas y cartesianas de su reseña no coinciden ({d:.3f} m)"
                continue
            report["max_geo_m"] = max(report["max_geo_m"], d)
        report["frames"][f["frame"]] = report["frames"].get(f["frame"], 0) + 1
        place = f["municipio"] or f["name"]
        station = {
            "id": sid,
            "name": f"{f['name']} ({place}, {f['provincia']})" if f["provincia"] and place != f["name"]
            else (f"{f['name']} ({f['provincia']})" if f["provincia"] else f["name"] or sid),
            "latitude": round(lat, 11), "longitude": round(lon, 11), "height": round(h, 4),
            "x": f["x"], "y": f["y"], "z": f["z"],
            "antennaHeight": f["antHt"], "antennaType": f["antenna"], "radome": f["radome"],
            "receiver": f["receiver"], "status": "active", "sheetDate": f.get("date", ""),
        }
        if f["frame"] == "REGCAN95":
            station.update(frame="REGCAN95", epoch=1994.9)
        used.add((f["antenna"], f["radome"]))
        notes = []
        long_name = long_names.get(sid)
        if long_name and not long_name.startswith(f"{sid}00ESP"):
            notes.append(f"En el archivo del IGN sus RINEX se llaman {long_name}…: la descarga directa no los "
                         "encuentra; bájalos de https://datos-geodesia.ign.es/ERGNSS/diario_30s/.")
        elif sid not in present:
            notes.append(f"No estaba en el archivo diario del IGN el {check_day}: puede no tener datos recientes; si "
                         "la descarga falla, elige otra estación.")
        if notes:
            station["notes"] = notes
        stations.append(station)
    package = {
        "format": "tresvizo-cors-network",
        "formatVersion": 1,
        "id": "ergnss-ign",
        "revision": 1,
        "version": retrieved,
        "title": "ERGNSS — IGN, España",
        "shortName": "ERGNSS",
        "countries": ["ES"],
        "region": "España (Península, Baleares, Canarias, Ceuta y Melilla)",
        "authority": "Instituto Geográfico Nacional (IGN), España",
        "frame": {"name": "ETRS89", "epoch": 2017.0, "ellipsoid": "GRS80"},
        "referencePoint": "Las coordenadas son de la marca de cada estación; la altura de antena va de la marca al "
                          "punto de referencia de la antena (ARP: «BPA» o «BAM» en la reseña). En la Península, "
                          "Baleares, Ceuta y Melilla, ETRS89 en la realización ERGNSS de 2018 (ETRF2000, época 2017.0, "
                          "EPSG:1442); en Canarias, REGCAN95 (ITRF93, época 1994.9).",
        "source": {"title": "IGN, reseñas de las estaciones permanentes ERGNSS", "url": FICHAS,
                   "cutoff": f"reseñas consultadas el {retrieved}", "retrieved": retrieved},
        "license": "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), la licencia de la información "
                   "geográfica del IGN (Orden FOM/2807/2015): uso, copia, modificación y redistribución, también "
                   "comercial, citando la fuente. Obra derivada: geodésicas recalculadas en GRS80 de las cartesianas "
                   "de cada reseña (coinciden con las publicadas), nombre de la antena sin espacios y avisos por "
                   "estación.",
        "licenseURL": "https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf",
        "attribution": f"Obra derivada de Reseñas de estaciones permanentes ERGNSS {retrieved[:4]} CC-BY 4.0 "
                       "Instituto Geográfico Nacional (ETRS89 y REGCAN95).",
        "download": {
            "type": "url-template",
            "granularity": "daily",
            "url": DOWNLOAD_URL,
            "instructions": "Archivos diarios de 30 s del IGN (RINEX 3, compresión Hatanaka), "
                            "https://datos-geodesia.ign.es/ERGNSS/diario_30s/. Los horarios de 1 s y 30 s están en "
                            "horario_1s/ y horario_30s/ de la misma dirección.",
        },
        "notes": [
            "ETRS89 en la Península y Baleares; REGCAN95 en Canarias (cada estación de Canarias lo dice). No se "
            "mezclan bases de los dos marcos en un mismo ajuste.",
            "No publica σ de las coordenadas en las reseñas.",
        ],
        "stations": stations,
    }
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


def _pdftotext(pdf: Path) -> str:
    exe = shutil.which("pdftotext") or "/opt/homebrew/bin/pdftotext"
    return subprocess.run([exe, "-layout", str(pdf), "-"], capture_output=True, check=True, text=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--antex", type=Path, default=DEFAULT_ANTEX)
    parser.add_argument("--ngs-antex", type=Path, default=SOURCES / "noaa-cors" / "ngs20.atx")
    parser.add_argument("--sin-descargar", action="store_true")
    parser.add_argument("--salida", type=Path, default=PACKAGES)
    args = parser.parse_args()
    src = Fuente("ergnss-ign", offline=args.sin_descargar)
    listing = src.get(FICHAS, "fichas.html").decode("utf-8", "replace")
    names = sorted(set(re.findall(r'href="([A-Z0-9]{4})\.pdf"', listing)))
    fichas = []
    for sid in names:
        src.get(FICHAS + f"{sid}.pdf", f"fichas/{sid}.pdf")
        fichas.append(parse_ficha(_pdftotext(src.folder / "fichas" / f"{sid}.pdf")))
    check = date.today() - timedelta(days=7)
    day_url = BASE + f"diario_30s/{check:%Y}/{check:%Y%m%d}/"
    day = src.get(day_url, f"diario_{check:%Y%m%d}.html").decode("utf-8", "replace")
    long_names = {m[:4]: m for m in re.findall(r'href="([A-Z0-9]{4}\d\d[A-Z]{3})_R_\d+_01D_30S_MO\.crx\.gz"', day)}
    present = {k for k, v in long_names.items() if v.endswith("00ESP")}
    atx = [(args.antex, f"IGS, ANTEX {antex_label(args.antex)}")]
    if args.ngs_antex and args.ngs_antex.is_file():
        atx.append((args.ngs_antex, "NGS, ANTEX ngs20.atx"))
    package, report = build(fichas, present, long_names, atx, today(), check.isoformat())
    out = write_package(package, args.salida)
    src.write_record("ERGNSS del IGN — fuente del paquete `ergnss-ign`", [
        f"Consultado el {today()}.",
        "",
        "- Marco: ETRS89 (realización ERGNSS 2018, ETRF2000 época 2017.0, EPSG:1442) y REGCAN95 en Canarias; GRS80; "
        "coordenadas de la marca y altura de antena a la ARP.",
        "- Licencia: CC BY 4.0, información geográfica del IGN (Orden FOM/2807/2015).",
        f"- Estaciones en el paquete: {len(package['stations'])} ({report['frames']}).",
        f"- Geodésicas publicadas contra cartesianas publicadas: diferencia máxima {report['max_geo_m'] * 1000:.1f} mm; "
        f"reseñas sin el signo de la longitud oeste (se corrigió con la cartesiana): "
        f"{', '.join(report['sign_fixed']) or 'ninguna'}.",
        f"- Fuera: {'; '.join(f'{k}: {v}' for k, v in sorted(report['left_out'].items()))}.",
        f"- Calibraciones de antena: {report.get('antex_source', '')}; sin calibración: "
        f"{', '.join(' '.join(a) for a in report.get('antex_missing', [])) or 'ninguna'}.",
        f"- Archivo diario comprobado el {check.isoformat()}: {len(present)} estaciones …00ESP.",
    ])
    print(f"{len(package['stations'])} estaciones → {out} ({report['frames']}); fuera {len(report['left_out'])}; "
          f"geo ±{report['max_geo_m'] * 1000:.1f} mm; signo corregido {report['sign_fixed']}; "
          f"sin ANTEX {report.get('antex_missing')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
