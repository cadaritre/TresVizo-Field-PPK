"""Arma el ANTEX que va con el programa: `tresvizo_ppk/core/data/antex/igs20_<semana>_tvppk.atx.gz`.

Del ANTEX oficial de la IGS (igs20_WWWW.atx, https://files.igs.org/pub/station/general/) se conservan, sin tocar
una sola línea: la cabecera, **todas las antenas de satélite** (necesarias con órbitas precisas) y las antenas de
receptor de las estaciones de las redes incluidas (todas las variantes de radomo de cada tipo de antena de los
paquetes de `data/redes/`; hoy, la RGNA). El
archivo completo pesa 60 MB; este, menos de 1 MB comprimido.

Uso:  python scripts/update_antex.py RUTA/igs20_WWWW.atx [--extra "TIPO" ...]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tresvizo_ppk" / "core" / "data"


def split(text: str) -> tuple[str, list[str]]:
    head, sep, body = text.partition("END OF HEADER")
    head_end = body.index("\n") + 1
    header = head + sep + body[:head_end]
    blocks = re.findall(r"[^\n]*START OF ANTENNA *\n.*?END OF ANTENNA *\n", body[head_end:], re.S)
    return header, blocks


def antenna_type(block: str) -> tuple[str, str]:
    for line in block.splitlines():
        if line[60:].strip() == "TYPE / SERIAL NO":
            return line[0:20], line[20:40]
    return "", ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("antex", type=Path)
    parser.add_argument("--extra", nargs="*", default=[], help="otros tipos de antena de receptor que conservar")
    args = parser.parse_args()
    raw = args.antex.read_bytes()
    text = raw.decode("latin-1")
    header, blocks = split(text)
    keep_types = {e.upper() for e in args.extra}
    for package in sorted((DATA / "redes").glob("*.json")):
        data = json.loads(package.read_text(encoding="utf-8"))
        keep_types |= {s.get("antennaType", "").upper() for s in data["stations"] if s.get("antennaType")}
    kept = []
    for block in blocks:
        ant, serial = antenna_type(block)
        model = ant[0:16].strip().upper()
        is_satellite = bool(re.match(r"[GRECJIS]\d\d", serial.strip())) or ant.startswith(("BLOCK", "GLONASS",
                                                                                            "GALILEO", "BEIDOU",
                                                                                            "QZSS", "IRNSS"))
        if is_satellite or model in keep_types:
            kept.append(block)
    m = re.search(r"igs20_(\d{4})", args.antex.name)
    week = m.group(1) if m else "XXXX"
    note = (f"Subconjunto de {args.antex.name} (SHA-256 {hashlib.sha256(raw).hexdigest()}) hecho por "
            f"scripts/update_antex.py: satélites y antenas de las redes incluidas.")
    comments = "".join(f"{note[k:k + 60]:<60}COMMENT\n" for k in range(0, len(note), 60))
    out_dir = DATA / "antex"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("igs20_*_tvppk.atx.gz"):
        old.unlink()
    out = out_dir / f"igs20_{week}_tvppk.atx.gz"
    header_lines = header.splitlines(keepends=True)
    final = "".join(header_lines[:-1]) + comments + header_lines[-1] + "".join(kept)
    with gzip.GzipFile(out, "wb", mtime=0) as handle:
        handle.write(final.encode("latin-1"))
    receivers = sorted({antenna_type(b)[0].strip() + " " + antenna_type(b)[1].strip()
                        for b in kept if not re.match(r"[GRECJIS]\d\d", antenna_type(b)[1].strip())})
    print(f"{len(kept)} antenas ({len(receivers)} de receptor: {', '.join(receivers)}) → {out.relative_to(ROOT)}, "
          f"{out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
