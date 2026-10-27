"""Línea de órdenes del motor: `python -m tresvizo_ppk estatico …` y `python -m tresvizo_ppk puntos …`.

El resultado sale en JSON (las mismas dataclasses de `model.py`, con fechas ISO 8601 en UTC, rutas como texto y
enumeraciones por su valor), a la salida estándar o al archivo de `--json`. El progreso va a la salida de errores.

Ejemplos:
    python -m tresvizo_ppk estatico --rover SESION.bin --altura 1.800 --punto P1 \\
        --base UQRO/*.zip --base ISLP/*.zip --salida trabajo/ --json resultado.json
    python -m tresvizo_ppk puntos --rover JORNADA.bin --base INEG/*.zip --csv puntos.csv --salida trabajo/
    python -m tresvizo_ppk puntos --rover JORNADA.bin --base BASE.bin --base-coordenada X,Y,Z \\
        --base-marco ITRF2008@2010.0 --base-altura 1.500 --csv puntos.csv --salida trabajo/
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from ..errors import Cancelled, UserError
from ..model import Ecef, Enu, Ephemeris, ProcessingOptions, Station
from . import api


def to_jsonable(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _progress(quiet: bool):
    last = {"text": ""}

    def report(fraction: float, message: str) -> None:
        if quiet:
            return
        text = f"[{100 * max(0.0, min(1.0, fraction)):3.0f} %] {message}"
        if text != last["text"]:
            print(text, file=sys.stderr, flush=True)
            last["text"] = text
    return report


def _options(args) -> ProcessingOptions:
    options = ProcessingOptions(elevation_mask_deg=args.mascara, frequencies=args.frecuencias,
                                ionosphere=args.ionosfera, combined=not args.solo_adelante,
                                ambiguity_mode=args.ambiguedades, min_ratio=args.cociente)
    if args.sistemas:
        names = {"gps": "GPS", "glonass": "GLONASS", "galileo": "Galileo", "beidou": "BeiDou", "qzss": "QZSS"}
        options.constellations = tuple(names[s.strip().lower()] for s in args.sistemas.split(","))
    if args.intervalo:
        options.interval_s = args.intervalo
    return options


def _with_precise(options: ProcessingOptions, args, start, end, workdir: Path, progress, cancel) -> None:
    files = [Path(p) for p in (args.orbitas or [])]
    if args.precisas:
        files += api.download_precise_products(start, end, workdir, progress=progress, cancel=cancel)
    if files:
        options.ephemeris = Ephemeris.PRECISE
        options.precise_files = files


def _station_for(obs, args_coord: str | None, args_id: str | None, height: float | None, name: str | None,
                 frame: str | None = None, epoch: float | None = None, work: Path | None = None) -> Station:
    if args_coord:
        try:
            x, y, z = (float(v) for v in args_coord.replace(";", ",").split(","))
        except ValueError:
            raise UserError("La coordenada de la base va como X,Y,Z en metros (ECEF).")
        return Station(id=(args_id or obs.marker or "BASE").upper()[:9], name=name or obs.marker or "Base propia",
                       position=Ecef(x, y, z, frame or "ITRF2008", epoch if epoch is not None else 2010.0)
                       if frame else Ecef(x, y, z), antenna_type=obs.antenna_type, radome=obs.radome or "NONE",
                       antenna_height_m=height if height is not None else obs.antenna_height_m,
                       receiver=obs.receiver, source="manual", official=False)
    station = api.find_station(args_id or obs.marker, near=obs.approx_position)
    if station is not None:
        return station
    # base propia sin coordenada escrita: la del encabezado RINEX o la media autónoma (aproximadas, se avisa)
    return api.base_coordinate_candidates(obs, work)[0]


def _frame(text: str | None) -> tuple[str | None, float | None]:
    if not text:
        return None, None
    name, _, epoch = text.partition("@")
    try:
        return name.strip(), float(epoch) if epoch else None
    except ValueError:
        raise UserError("El marco va como NOMBRE@ÉPOCA, p. ej. «ITRF2008@2010.0».")


def cmd_static(args) -> dict:
    cancel = threading.Event()
    progress = _progress(args.silencio)
    work = Path(args.salida)
    rover = api.prepare_observations([Path(p) for p in args.rover], work / "rover", pole_height_m=args.altura,
                                     marker=args.punto, progress=progress, cancel=cancel)
    window = (rover.start, rover.end)
    bases = []
    ids = args.estacion or []
    for k, files in enumerate(args.base):
        obs = api.prepare_observations([Path(p) for p in files], work / f"base{k + 1}", window=window,
                                       progress=progress, cancel=cancel)
        station = _station_for(obs, None, ids[k] if k < len(ids) else None, None, None,
                               work=work / f"base{k + 1}_coordenada")
        bases.append((station, obs))
    options = _options(args)
    _with_precise(options, args, rover.start, rover.end, work, progress, cancel)
    result = api.process_static(rover, bases, options, work / "proceso", progress=progress, cancel=cancel,
                                point_name=args.punto or "")
    return to_jsonable(result)


def cmd_points(args) -> dict:
    cancel = threading.Event()
    progress = _progress(args.silencio)
    work = Path(args.salida)
    rover = api.prepare_observations([Path(p) for p in args.rover], work / "rover", pole_height_m=None,
                                     progress=progress, cancel=cancel)
    base_obs = api.prepare_observations([Path(p) for p in args.base], work / "base",
                                        pole_height_m=args.base_altura, window=(rover.start, rover.end),
                                        progress=progress, cancel=cancel)
    if args.base_coordenada and not args.base_marco:
        raise UserError("Con --base-coordenada di también su marco y época con --base-marco (p. ej. "
                        "«ITRF2008@2010.0» o «SIRGAS2000@2000.4»).")
    frame, epoch = _frame(args.base_marco)
    station = _station_for(base_obs, args.base_coordenada, args.estacion, args.base_altura, args.base_nombre,
                           frame, epoch, work=work / "base_coordenada")
    points = api.read_field_csv(Path(args.csv))
    options = _options(args)
    _with_precise(options, args, rover.start, rover.end, work, progress, cancel)
    result = api.process_points(rover, (station, base_obs), points, options, work / "proceso", progress=progress,
                                cancel=cancel)
    return to_jsonable(result)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--salida", required=True, help="carpeta de trabajo (intermedios, configuración, .pos)")
    p.add_argument("--json", help="archivo del resultado (por omisión, la salida estándar)")
    p.add_argument("--mascara", type=float, default=15.0, help="máscara de elevación en grados (15)")
    p.add_argument("--frecuencias", type=int, choices=(1, 2, 3), default=2)
    p.add_argument("--sistemas", help="gps,glonass,galileo,beidou,qzss (todas)")
    p.add_argument("--ionosfera", default="auto", choices=("auto", "iono-free", "broadcast", "estimated"))
    p.add_argument("--ambiguedades", default="fix-and-hold",
                   choices=("fix-and-hold", "continuous", "instantaneous", "off"))
    p.add_argument("--cociente", type=float, default=3.0, help="cociente (ratio) mínimo para fijar")
    p.add_argument("--intervalo", type=float, help="intervalo de proceso en segundos")
    p.add_argument("--solo-adelante", action="store_true", help="sin pasada hacia atrás")
    p.add_argument("--precisas", action="store_true", help="bajar órbitas precisas (ESA/IGS) de la sesión")
    p.add_argument("--orbitas", nargs="+", help="SP3 propios")
    p.add_argument("--silencio", action="store_true", help="sin progreso en la salida de errores")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tresvizo_ppk",
                                     description="Motor de TresVizo Field PPK: estático ligado a redes CORS y PPK "
                                                 "de puntos. El resultado sale en JSON.")
    sub = parser.add_subparsers(dest="orden", required=True)
    s = sub.add_parser("estatico", help="georreferenciar un punto a una, dos o tres estaciones")
    s.add_argument("--rover", nargs="+", required=True, help="grabación .bin/.part o RINEX del punto")
    s.add_argument("--altura", type=float, help="altura vertical del bastón o trípode (marca → base del receptor)")
    s.add_argument("--punto", help="nombre del punto")
    s.add_argument("--base", nargs="+", action="append", required=True,
                   help="archivos de UNA estación (repetir --base por cada estación, hasta tres)")
    s.add_argument("--estacion", action="append", help="clave de la estación de cada --base, si el RINEX no la dice")
    _common(s)
    s.set_defaults(func=cmd_static)
    p = sub.add_parser("puntos", help="PPK de los puntos del CSV de TresVizo Field")
    p.add_argument("--rover", nargs="+", required=True, help="grabación cinemática .bin/.part o RINEX de la jornada")
    p.add_argument("--base", nargs="+", required=True, help="archivos de la base (RGNA o propia)")
    p.add_argument("--csv", required=True, help="CSV de TresVizo Field («Todas las columnas»)")
    p.add_argument("--estacion", help="clave de la estación de la RGNA, si el RINEX no la dice")
    p.add_argument("--base-coordenada", help="base propia: X,Y,Z ECEF en metros (de la marca)")
    p.add_argument("--base-altura", type=float, help="base propia: altura vertical de la marca a la base del "
                                                       "receptor")
    p.add_argument("--base-nombre", help="base propia: nombre para el informe")
    p.add_argument("--base-marco", help="base propia: marco y época de su coordenada, p. ej. ITRF2008@2010.0")
    _common(p)
    p.set_defaults(func=cmd_points)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except Cancelled:
        print("Cancelado.", file=sys.stderr)
        return 130
    except UserError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
        if not args.silencio:
            print(f"Resultado: {args.json}", file=sys.stderr)
    else:
        print(text)
    return 0
