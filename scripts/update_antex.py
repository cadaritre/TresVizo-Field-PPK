"""Arma el ANTEX que va con el programa: `tresvizo_ppk/core/data/antex/igs20_<semana>_tvppk.atx.gz` y su índice
`igs20_<semana>_tvppk.json`.

Fuentes oficiales, públicas y sin cuenta:

- el ANTEX vigente de la IGS, `igs20.atx` (https://files.igs.org/pub/station/general/igs20.atx; la semana GPS de la
  versión sale de su cabecera o del nombre `igs20_WWWW.atx`);
- el ANTEX del NGS, `ngs20.atx` (https://geodesy.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx), calibraciones absolutas
  en el mismo marco igs20, para las antenas que la IGS no trae.

Se conservan, sin tocar una sola línea de ningún bloque: la cabecera de la IGS (con unas líneas COMMENT que dicen de
dónde sale cada parte), **todas las antenas de satélite** de la IGS (las usan RTKLIB y el procesador propio con
órbitas precisas), **todas las antenas de receptor** de la IGS y las del NGS que no están en la IGS (por tipo y
radomo; si está en las dos, gana la IGS). Las de receptor van ordenadas por la longitud del tipo y luego por nombre:
RTKLIB busca la antena con `strstr`, y así un tipo que es parte del nombre de otro («TRM5800» en «TRM58000.00») se
encuentra antes que el más largo.

El índice JSON anota la URL, la fecha y el SHA-256 de cada fuente y qué antenas vienen del NGS; `antex.py` lo usa
para decir en el informe de dónde salió cada calibración. `ORIGEN.md`, junto al archivo, lo dice para las personas.

Uso:
    python scripts/update_antex.py                      # baja las dos fuentes a la carpeta de datos y arma
    python scripts/update_antex.py --igs RUTA/igs20.atx --ngs RUTA/ngs20.atx   # con archivos ya bajados

Lo corre quien mantiene los datos, nunca la app.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tresvizo_ppk" / "core" / "data" / "antex"
SOURCES = Path.home() / "Documents" / "TresVizo-Field-PPK-datos" / "fuentes" / "antex"
IGS_URL = "https://files.igs.org/pub/station/general/igs20.atx"
NGS_URL = "https://geodesy.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx"
USER_AGENT = "TresVizo-Field-PPK/antex"
SATELLITE_TYPES = ("BLOCK", "GLONASS", "GALILEO", "BEIDOU", "QZSS", "IRNSS")


def split(text: str) -> tuple[str, list[str]]:
    head, sep, body = text.partition("END OF HEADER")
    if not sep:
        raise ValueError("no es un ANTEX: falta END OF HEADER")
    head_end = body.index("\n") + 1
    header = head + sep + body[:head_end]
    blocks = re.findall(r"[^\n]*START OF ANTENNA *\n.*?END OF ANTENNA *\n", body[head_end:], re.S)
    return header, blocks


def antenna_type(block: str) -> tuple[str, str]:
    """(los 20 caracteres de tipo y radomo, los 20 del número de serie) de un bloque."""
    for line in block.splitlines():
        if line[60:].strip() == "TYPE / SERIAL NO":
            return line[0:20], line[20:40]
    return "", ""


def is_satellite(block: str) -> bool:
    ant, serial = antenna_type(block)
    return bool(re.match(r"[GRECJIS]\d\d", serial.strip())) or ant.startswith(SATELLITE_TYPES)


def key(block: str) -> tuple[str, str]:
    ant, _ = antenna_type(block)
    return ant[0:16].strip().upper(), ant[16:20].strip().upper() or "NONE"


def receiver_order(k: tuple[str, str]) -> tuple[int, str, str]:
    return len(k[0]), k[0], k[1]


def week_of(path: Path, header: str) -> str:
    m = re.search(r"igs20_(\d{4})", path.name)
    if m:
        return m.group(1)
    m = re.search(r"igs20_(\d{4})", header)
    if m:
        return m.group(1)
    raise SystemExit(f"No sé la semana GPS de {path.name}: renómbralo igs20_WWWW.atx (la de su anuncio en la IGS).")


def fetch(url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        raw = response.read()
    target.write_bytes(raw)
    return target


def describe(path: Path, url: str) -> dict:
    raw = path.read_bytes()
    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {"archivo": path.name, "url": url, "fecha": stamp, "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--igs", type=Path, help="igs20.atx o igs20_WWWW.atx ya bajado (si no, se baja)")
    parser.add_argument("--ngs", type=Path, help="ngs20.atx ya bajado (si no, se baja)")
    parser.add_argument("--semana", help="semana GPS del igs20.atx si su nombre no la trae")
    args = parser.parse_args()

    igs_path = args.igs or fetch(IGS_URL, SOURCES / "igs20.atx")
    ngs_path = args.ngs or fetch(NGS_URL, SOURCES / "ngs20.atx")
    igs_text = igs_path.read_bytes().decode("latin-1")
    ngs_text = ngs_path.read_bytes().decode("latin-1")
    header, igs_blocks = split(igs_text)
    _, ngs_blocks = split(ngs_text)
    week = args.semana or week_of(igs_path, header)

    satellites = [b for b in igs_blocks if is_satellite(b)]
    receivers: dict[tuple[str, str], str] = {}
    for block in igs_blocks:
        if not is_satellite(block):
            receivers.setdefault(key(block), block)
    from_ngs: list[tuple[str, str]] = []
    for block in ngs_blocks:
        if is_satellite(block):
            continue
        k = key(block)
        if k not in receivers:
            receivers[k] = block
            from_ngs.append(k)
    ordered = sorted(receivers, key=receiver_order)

    igs_info = describe(igs_path, IGS_URL)
    ngs_info = describe(ngs_path, NGS_URL)
    note = (f"TresVizo Field PPK: de igs20_{week} (IGS, SHA-256 {igs_info['sha256']}) la cabecera, "
            f"{len(satellites)} antenas de satelite y {len(receivers) - len(from_ngs)} de receptor; de ngs20.atx "
            f"(NGS, SHA-256 {ngs_info['sha256']}) {len(from_ngs)} de receptor que la IGS no trae. Bloques sin "
            "cambios; receptores ordenados por longitud del tipo (scripts/update_antex.py).")
    comments = "".join(f"{note[k:k + 60]:<60}COMMENT\n" for k in range(0, len(note), 60))
    header_lines = header.splitlines(keepends=True)
    final = "".join(header_lines[:-1]) + comments + header_lines[-1] + "".join(satellites) + \
        "".join(receivers[k] for k in ordered)

    DATA.mkdir(parents=True, exist_ok=True)
    for old in list(DATA.glob("igs20_*_tvppk.atx.gz")) + list(DATA.glob("igs20_*_tvppk.json")):
        old.unlink()
    out = DATA / f"igs20_{week}_tvppk.atx.gz"
    with gzip.GzipFile(out, "wb", compresslevel=9, mtime=0) as handle:
        handle.write(final.encode("latin-1"))
    index = {"formato": "tvppk-antex-indice", "version": 1, "igs": {**igs_info, "nombre": f"igs20_{week}"},
             "ngs": {**ngs_info, "nombre": "ngs20.atx"}, "satelites": len(satellites),
             "receptores": len(receivers), "receptores_ngs": [list(k) for k in sorted(from_ngs)]}
    (DATA / f"igs20_{week}_tvppk.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n",
                                                   encoding="utf-8")
    origin = [
        "# Origen del ANTEX incluido", "",
        f"`{out.name}` lo arma `scripts/update_antex.py` a partir de dos fuentes oficiales, públicas y sin cuenta, "
        "sin cambiar una sola línea de sus bloques:", "",
        "| Fuente | Dirección | Fecha de bajada | SHA-256 | Qué se toma |", "| --- | --- | --- | --- | --- |",
        f"| IGS, ANTEX igs20_{week} | {IGS_URL} | {igs_info['fecha']} | `{igs_info['sha256']}` | cabecera, "
        f"{len(satellites)} antenas de satélite y {len(receivers) - len(from_ngs)} de receptor (todas) |",
        f"| NGS (NOAA), ngs20.atx | {NGS_URL} | {ngs_info['fecha']} | `{ngs_info['sha256']}` | "
        f"{len(from_ngs)} antenas de receptor que la IGS no trae |", "",
        "Licencia y cita: ver `third_party/LICENCIAS.md` (IGS: acceso libre con cita; NGS: dominio público del "
        "gobierno de los EE. UU.).", "",
        f"`{(DATA / f'igs20_{week}_tvppk.json').name}` dice qué antenas vienen del NGS; el programa lo usa para "
        "escribir en el informe la fuente de cada calibración.", ""]
    (DATA / "ORIGEN.md").write_text("\n".join(origin), encoding="utf-8")
    print(f"{len(satellites)} antenas de satélite y {len(receivers)} de receptor ({len(from_ngs)} del NGS) → "
          f"{out.relative_to(ROOT)}, {out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
