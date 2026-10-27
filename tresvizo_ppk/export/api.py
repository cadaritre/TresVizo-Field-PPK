"""Exportar los puntos posprocesados (especificación §1.2.5 y §8): `export_points(result, choice, formato, out)`.

Formatos:

- `field_csv`: **el CSV de TresVizo Field** (`docs/csv-export.md` de las apps) con las coordenadas posprocesadas en
  sus columnas (Este, Norte, Cota, latitud, longitud, altura elipsoidal, RMS, estado de la solución, la base, el
  sistema y el geoide) y las columnas de calidad del posproceso al final (solución, épocas, σ, base, la coordenada de
  campo y la diferencia con ella). Las demás columnas de la fila original salen tal cual, en su orden y con sus
  nombres (inglés o español, según venía). Mismas reglas de escritura que las apps: coma, punto decimal, comillas de
  RFC 4180 solo donde hacen falta, CRLF, UTF-8 con BOM, vacío es «sin dato» (nunca 0) y nunca «-0.000».
  Un punto que no se pudo posprocesar conserva sus coordenadas de campo y lo dice en «PPK solution» y «PPK message».
- `csv`: CSV simple «Nombre, Este, Norte, Cota, Código» (o longitud y latitud en un sistema geográfico), solo los
  puntos posprocesados.
- `geojson`: RFC 7946, la geometría **siempre en longitud y latitud** con la altura elipsoidal (lo que manda el
  formato), y en `properties` la coordenada en el sistema elegido, su cota, su σ y su solución; los metadatos
  declaran el sistema, el marco, la época y el geoide.
- `kml`: KML 2.2 con los mismos puntos, claves y valores que el GeoJSON (contrato `docs/KML_FORMAT.md` de las apps).
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from ..crs import api as crs
from ..errors import UserError
from ..model import CoordinateChoice, Ecef, Enu, PointsResult, ProcessedPoint, Solution, TransformedCoordinate
from ..nombres import station_label
from ..version import __version__
from . import columnas

FORMATS = ("field_csv", "csv", "geojson", "kml")
EXTENSIONS = {"field_csv": ".csv", "csv": ".csv", "geojson": ".geojson", "kml": ".kml"}
APP = f"TresVizo Field PPK {__version__}"
SOLUTION_STATUS = {Solution.FIXED: "FIX", Solution.FLOAT: "FLOAT", Solution.SINGLE: "SINGLE", Solution.NONE: "NONE"}
CRLF = "\r\n"

# Columnas de campo que dejan de valer cuando la coordenada es la del posproceso (hablaban de la solución RTK de
# campo y de su base): se vacían en los puntos posprocesados. Las de la ocupación (inicio y fin, DOP, satélites,
# receptor, altura de antena) se quedan.
CLEARED = {"mountPoint", "caster", "correctionAge", "correctionSource", "baseID", "baseSerialNumber", "baseShift",
           "baseShiftID", "correctionsFrame", "correctionsFrameEpoch", "correctionsFrameSource", "withinTolerance",
           "violations", "horizontalSpread", "verticalSpread", "receiverHeightReference"}


# --- Formato de números y de texto (las reglas de las apps) -----------------------------------------------------------

def number(value: float | None, decimals: int) -> str:
    """Decimales fijos con punto, redondeo al par desde el binario exacto, vacío sin dato y nunca «-0.000»."""
    if value is None or not math.isfinite(value):
        return ""
    text = f"{value:.{decimals}f}"
    if text.startswith("-") and set(text[1:]) <= set("0."):
        text = text[1:]
    return text


_SIGNED_NUMBER = re.compile(r"^[+-](\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def neutralized(text: str) -> str:
    """Un texto que una hoja de cálculo tomaría por fórmula lleva «'» delante (`SurveyExport.neutralized`)."""
    if not text:
        return text
    first = text[0]
    if first in "=@\t\r":
        return "'" + text
    if first in "+-":
        return text if _SIGNED_NUMBER.match(text) else "'" + text
    return text


def quoted(field: str, separator: str = ",") -> str:
    """Comillas de RFC 4180 solo si hacen falta, o si empieza por «#» (`SurveyExport.quoted`)."""
    if separator in field or '"' in field or "\n" in field or "\r" in field or field.startswith("#"):
        return '"' + field.replace('"', '""') + '"'
    return field


def write_rows(header: list[str], rows: list[list[str]], *, separator: str = ",", bom: bool = True) -> bytes:
    """El archivo: encabezado y filas ya formateadas, comillas donde hacen falta, CRLF y BOM."""
    lines = [separator.join(quoted(h, separator) for h in header)]
    lines += [separator.join(quoted(v, separator) for v in row) for row in rows]
    data = (CRLF.join(lines) + CRLF).encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def _atomic(out: Path, data: bytes) -> Path:
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=out.parent)
    except OSError as error:
        raise UserError(f"No se puede escribir en «{out.parent}»: {error.strerror or error}.") from None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except PermissionError:
        Path(tmp).unlink(missing_ok=True)
        raise UserError(f"No se pudo guardar «{out.name}»: el archivo está abierto en otro programa o la carpeta no "
                        "permite escribir.") from None
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return out


# --- Lo común a todos los formatos ------------------------------------------------------------------------------------

def _plane(c: TransformedCoordinate | None) -> tuple[str, str]:
    """(Este, Norte) con los decimales de las apps; en geográficas, longitud y latitud con 8."""
    if c is None:
        return "", ""
    if c.easting_m is not None:
        return number(c.easting_m, 3), number(c.northing_m, 3)
    return number(c.longitude_deg, 8), number(c.latitude_deg, 8)


def _elevation(c: TransformedCoordinate | None, choice: CoordinateChoice) -> str:
    """La cota: sobre el geoide si se aplicó; sin geoide, la elipsoidal; con un geoide declarado que no se pudo
    aplicar, vacía (otra clase de altura con el mismo nombre sería peor que nada)."""
    if c is None:
        return ""
    if c.orthometric_height_m is not None:
        return number(c.orthometric_height_m, 3)
    if choice.geoid_id:
        return ""
    return number(c.ellipsoidal_height_m, 3)


class _Computed:
    """La coordenada de un punto en el sistema elegido, con su σ, y la de campo para compararla."""

    def __init__(self, pp: ProcessedPoint, choice: CoordinateChoice):
        self.pp = pp
        self.c: TransformedCoordinate | None = None
        self.sigma: Enu | None = None
        self.field: TransformedCoordinate | None = None
        if pp.position is not None and pp.solution != Solution.NONE:
            self.c = crs.transform(pp.position, choice)
            if pp.sigma_enu is not None:
                self.sigma = crs.transform_sigma(pp.position, pp.sigma_enu, choice)
        if pp.point.field_position is not None:
            try:
                self.field = crs.transform(pp.point.field_position, choice)
            except UserError:
                self.field = None

    @property
    def processed(self) -> bool:
        return self.c is not None

    def plane(self, c: TransformedCoordinate | None = None) -> tuple[str, str]:
        return _plane(c or self.c)

    def elevation(self, choice: CoordinateChoice, c: TransformedCoordinate | None = None) -> str:
        return _elevation(c or self.c, choice)

    def delta(self) -> Enu | None:
        p, f = self.pp.position, self.pp.point.field_position
        if p is None or f is None:
            return self.pp.delta_from_field_enu
        r = crs.enu_rotation(*crs.motor.geodetic(p.x, p.y, p.z, crs.definition("EPSG:6365").ellipsoid)[:2])
        d = (p.x - f.x, p.y - f.y, p.z - f.z)
        return Enu(*(sum(r[i][k] * d[k] for k in range(3)) for i in range(3)))


def _fixed_choice(result: PointsResult, choice: CoordinateChoice) -> CoordinateChoice:
    """Con una familia de zona automática, toda la exportación en la zona del primer punto posprocesado (o de la
    base): una sola cuadrícula por archivo, como el proyecto de las apps."""
    if choice.crs_id not in crs.FAMILIES or choice.zone.strip():
        return choice
    ref = next((p.position for p in result.points if p.position is not None and p.solution != Solution.NONE),
               result.base.position)
    zone = crs.transform(ref, CoordinateChoice(choice.crs_id, "", None)).zone
    return CoordinateChoice(choice.crs_id, zone, choice.geoid_id, choice.label)


def _geoid_column(choice: CoordinateChoice, c: TransformedCoordinate | None) -> str:
    if not choice.geoid_id:
        return ""
    if c is not None and c.orthometric_height_m is not None:
        return choice.geoid_id.upper()
    return f"{choice.geoid_id.upper()} NO APLICADO"


def _base_arp(result: PointsResult) -> Ecef:
    """La ARP de la base: su coordenada más la altura vertical de la antena (lo que dicen las columnas «Base»)."""
    b = result.base
    if not b.antenna_height_m:
        return b.position
    lat, lon, _ = crs.motor.geodetic(b.position.x, b.position.y, b.position.z, crs.definition("EPSG:6365").ellipsoid)
    up = crs.enu_rotation(lat, lon)[2]
    h = b.antenna_height_m
    return Ecef(b.position.x + up[0] * h, b.position.y + up[1] * h, b.position.z + up[2] * h, b.position.frame,
                b.position.epoch)


# --- CSV de TresVizo Field --------------------------------------------------------------------------------------------

def _language(rows: list[dict[str, str]]) -> str:
    for row in rows:
        for name in row:
            key = columnas.BY_HEADER.get(name.casefold())
            if key and columnas.SPANISH[key] == name and columnas.ENGLISH[key] != name:
                return "es"
            if key and columnas.ENGLISH[key] == name and columnas.SPANISH[key] != name:
                return "en"
    return "en"


def _field_csv(result: PointsResult, choice: CoordinateChoice) -> bytes:
    computed = [_Computed(p, choice) for p in result.points]
    rows_in = [p.point.row for p in result.points]
    lang = _language(rows_in)
    names = columnas.ENGLISH if lang == "en" else columnas.SPANISH
    # El encabezado: el de las filas originales, en su orden; sin filas, «Todas las columnas» de las apps.
    header: list[str] = []
    for row in rows_in:
        for name in row:
            if name not in header:
                header.append(name)
    if not header:
        header = [names[k] for k, _, _ in columnas.COLUMNS]
    key_of = {name: columnas.BY_HEADER.get(name.casefold()) for name in header}
    present = {k for k in key_of.values() if k}
    # Las columnas de coordenadas tienen que estar aunque el archivo original no las trajera.
    for key in ("name", "code", "easting", "northing", "elevation", "longitude", "latitude", "ellipsoidalHeight",
                "solutionStatus", "csName", "csCode", "geoid", "frame"):
        if key not in present:
            header.append(names[key])
            key_of[names[key]] = key
    ppk = [(k, en if lang == "en" else es) for k, en, es in columnas.PPK_COLUMNS]
    header += [name for _, name in ppk]

    base = result.base
    arp = _base_arp(result)
    base_c = crs.transform(arp, choice)
    base_values = {
        "baseEasting": "", "baseNorthing": "", "baseElevation": "", "baseLongitude": "", "baseLatitude": "",
        "baseEllipsoidalHeight": "", "baseReceiver": base.receiver, "baseType": "Physical",
        "baseElevationReference": "ARP",
    }
    e, n = _plane(base_c)
    base_values.update(baseEasting=e, baseNorthing=n, baseElevation=_elevation(base_c, choice),
                       baseLongitude=number(base_c.receiver_longitude_deg, 8),
                       baseLatitude=number(base_c.receiver_latitude_deg, 8),
                       baseEllipsoidalHeight=number(base_c.ellipsoidal_height_m, 3))

    out_rows = []
    for comp in computed:
        pp = comp.pp
        original = pp.point.row
        values: dict[str, str] = {}
        for name in header[: len(header) - len(ppk)]:
            values[name] = neutralized(original.get(name, ""))
        by_key = {key_of[name]: name for name in key_of if key_of[name]}

        def put(key: str, value: str) -> None:
            if key in by_key:
                values[by_key[key]] = value

        put("name", neutralized(pp.point.name))
        if pp.point.code or not original:
            put("code", neutralized(pp.point.code))
        field_e, field_n = comp.plane(comp.field) if comp.field else ("", "")
        if comp.processed:
            c = comp.c
            e, n = comp.plane()
            put("easting", e)
            put("northing", n)
            put("elevation", comp.elevation(choice))
            put("longitude", number(c.receiver_longitude_deg, 8))
            put("latitude", number(c.receiver_latitude_deg, 8))
            put("ellipsoidalHeight", number(c.ellipsoidal_height_m, 3))
            if comp.sigma is not None:
                put("eastingRMS", number(comp.sigma.e, 3))
                put("northingRMS", number(comp.sigma.n, 3))
                put("elevationRMS", number(comp.sigma.u, 3))
                put("lateralRMS", number(math.hypot(comp.sigma.e, comp.sigma.n), 3))
            put("solutionStatus", SOLUTION_STATUS[pp.solution])
            put("correctionType", "PPK")
            put("samples", str(pp.epochs_fixed if pp.solution == Solution.FIXED else pp.epochs_total))
            for key, value in base_values.items():
                put(key, neutralized(value))
            put("baseline", number(pp.distance_to_base_m, 3))
            put("csName", neutralized(c.label))
            put("csCode", c.crs_code)
            put("geoid", _geoid_column(choice, c))
            put("geoidUndulation", number(c.undulation_m, 3))
            put("frame", pp.position.frame)
            total = _float(original.get(_header_for(by_key, "totalAntennaHeight"), ""))
            if total is None:
                total = pp.point.antenna_height_m
            put("antennaEllipsoidalHeight", number(c.ellipsoidal_height_m + total, 3))
            for key in CLEARED:
                put(key, "")
        d = comp.delta()
        ppk_values = {
            "ppkSolution": SOLUTION_STATUS[pp.solution] if comp.processed else "NONE",
            "ppkFixedEpochs": str(pp.epochs_fixed), "ppkEpochs": str(pp.epochs_total),
            "ppkEastingSigma": number(comp.sigma.e, 3) if comp.sigma else "",
            "ppkNorthingSigma": number(comp.sigma.n, 3) if comp.sigma else "",
            "ppkElevationSigma": number(comp.sigma.u, 3) if comp.sigma else "",
            "ppkBase": neutralized(station_label(base)),
            "ppkBaseline": number(pp.distance_to_base_m, 3),
            "fieldEasting": field_e, "fieldNorthing": field_n,
            "fieldElevation": comp.elevation(choice, comp.field) if comp.field else "",
            "fieldSolution": neutralized(pp.point.field_solution),
            "fieldDeltaHorizontal": number(math.hypot(d.e, d.n), 3) if d and comp.processed else "",
            "fieldDeltaVertical": number(d.u, 3) if d and comp.processed else "",
            "ppkMessage": neutralized(pp.message or ("" if comp.processed else
                                                     "sin posproceso: van las coordenadas de campo")),
            "ppkSoftware": APP,
        }
        for key, name in ppk:
            values[name] = ppk_values[key]
        out_rows.append([values[name] for name in header])
    return write_rows(header, out_rows)


def _header_for(by_key: dict[str, str], key: str) -> str:
    return by_key.get(key, "")


def _float(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


# --- CSV simple -------------------------------------------------------------------------------------------------------

def _simple_csv(result: PointsResult, choice: CoordinateChoice) -> bytes:
    rows = []
    geographic = False
    for p in result.points:
        comp = _Computed(p, choice)
        if not comp.processed:
            continue
        geographic = comp.c.easting_m is None
        e, n = comp.plane()
        rows.append([neutralized(p.point.name), e, n, comp.elevation(choice), neutralized(p.point.code)])
    header = (["Nombre", "Longitud", "Latitud", "Cota", "Código"] if geographic else
              ["Nombre", "Este", "Norte", "Cota", "Código"])
    return write_rows(header, rows)


# --- GeoJSON y KML ----------------------------------------------------------------------------------------------------

def _round(value: float | None, decimals: int):
    if value is None or not math.isfinite(value):
        return None
    return round(value, decimals) + 0.0


def _properties(comp: _Computed, choice: CoordinateChoice) -> dict:
    p = comp.pp
    c = comp.c
    props: dict = {
        "nombre": p.point.name,
        "altura_en_geometria": "elipsoidal_del_suelo",
        "solucion": SOLUTION_STATUS[p.solution],
        "epocas": p.epochs_total,
        "epocas_fijas": p.epochs_fixed,
        "altura_antena_m": _round(p.point.antenna_height_m, 3),
        "inicio_utc": p.point.start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fin_utc": p.point.end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if p.point.code:
        props["codigo"] = p.point.code
    if c is not None:
        if c.easting_m is not None:
            props["este_m"] = _round(c.easting_m, 3)
            props["norte_m"] = _round(c.northing_m, 3)
        props["altura_elipsoidal_m"] = _round(c.ellipsoidal_height_m, 3)
        if c.orthometric_height_m is not None:
            props["cota_m"] = _round(c.orthometric_height_m, 3)
            props["ondulacion_m"] = _round(c.undulation_m, 3)
            props["tipo_cota"] = c.height_kind
    if comp.sigma is not None:
        props["precision_h_m"] = _round(math.hypot(comp.sigma.e, comp.sigma.n), 3)
        props["precision_v_m"] = _round(comp.sigma.u, 3)
    if p.distance_to_base_m is not None:
        props["distancia_base_m"] = _round(p.distance_to_base_m, 3)
    d = comp.delta()
    if d is not None and c is not None:
        props["dif_campo_h_m"] = _round(math.hypot(d.e, d.n), 3)
        props["dif_campo_v_m"] = _round(d.u, 3)
    if p.point.field_solution:
        props["solucion_campo"] = p.point.field_solution
    if p.message:
        props["aviso"] = p.message
    return dict(sorted(props.items(), key=lambda kv: kv[0].encode("utf-8")))


def _metadata(result: PointsResult, choice: CoordinateChoice, sample: TransformedCoordinate | None,
              skipped: list[str]) -> dict:
    pos = result.base.position
    meta = {
        "app": APP,
        "exportado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sistema_de_las_propiedades": f"{sample.crs_code} {sample.label}" if sample else choice.crs_id,
        "marco": pos.frame,
        "epoca_marco": pos.epoch,
        "base": station_label(result.base),
        "base_fuente": result.base.source or ("oficial" if result.base.official else "manual"),
        "cota": "la geometría lleva la altura elipsoidal del punto en el suelo; la cota del sistema va en cota_m",
        "geoide": (sample.geoid_name if sample and sample.orthometric_height_m is not None else
                   (f"{choice.geoid_id} no aplicado" if choice.geoid_id else "ninguno")),
        "plomeado": "en posproceso todo se asume plomeado: el punto es la antena menos la altura vertical del bastón",
        "puntos": len(result.points) - len(skipped),
    }
    if sample is not None and sample.attribution:
        meta["licencia_geoide"] = list(sample.attribution)
    if skipped:
        meta["puntos_sin_posproceso"] = skipped
    return dict(sorted(meta.items(), key=lambda kv: kv[0].encode("utf-8")))


def _features(result: PointsResult, choice: CoordinateChoice):
    out, skipped, sample = [], [], None
    for p in result.points:
        comp = _Computed(p, choice)
        if not comp.processed:
            skipped.append(p.point.name)
            continue
        sample = sample or comp.c
        c = comp.c
        coords = [c.receiver_longitude_deg, c.receiver_latitude_deg, _round(c.ellipsoidal_height_m, 3)]
        out.append((comp, coords))
    return out, skipped, sample


def _geojson(result: PointsResult, choice: CoordinateChoice) -> bytes:
    features, skipped, sample = _features(result, choice)
    doc = {
        "type": "FeatureCollection",
        "metadata": _metadata(result, choice, sample, skipped),
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": coords},
                      "properties": _properties(comp, choice)} for comp, coords in features],
    }
    return (json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _kml_value(value) -> str:
    """Las reglas de `KML_FORMAT.md`: texto tal cual, booleanos, enteros sin decimales, reales en su forma más
    corta sin notación científica, listas con «; » y nulos vacíos."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        if value == int(value) and abs(value) < 2 ** 53:
            return str(int(value))
        return format(Decimal(repr(value)), "f")
    if isinstance(value, list):
        return "; ".join(_kml_value(v) for v in value)
    return escape(str(value), {'"': "&quot;"})


def _kml_number(value: float, decimals: int) -> str:
    return number(value, decimals)


def _kml(result: PointsResult, choice: CoordinateChoice) -> bytes:
    features, skipped, sample = _features(result, choice)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2">', "<Document>",
             f"<name>{escape(f'PPK {result.base.id} {result.rover.marker}'.strip())}</name>", "<ExtendedData>"]
    for key, value in _metadata(result, choice, sample, skipped).items():
        lines.append(f'<Data name="{key}"><value>{_kml_value(value)}</value></Data>')
    lines.append("</ExtendedData>")
    for comp, coords in features:
        lines += ["<Placemark>", f"<name>{escape(comp.pp.point.name)}</name>", "<ExtendedData>"]
        for key, value in _properties(comp, choice).items():
            lines.append(f'<Data name="{key}"><value>{_kml_value(value)}</value></Data>')
        lines.append("</ExtendedData>")
        text = f"{_kml_number(coords[0], 9)},{_kml_number(coords[1], 9)}"
        if coords[2] is not None:
            text += f",{_kml_number(coords[2], 3)}"
        lines += [f"<Point><coordinates>{text}</coordinates></Point>", "</Placemark>"]
    lines += ["</Document>", "</kml>"]
    return ("\n".join(lines) + "\n").encode("utf-8")


# --- Entrada ----------------------------------------------------------------------------------------------------------

def export_points(result: PointsResult, choice: CoordinateChoice, formato: str, out: str | Path) -> Path:
    """Escribe los puntos posprocesados en `out` (se le pone la extensión del formato si no la trae)."""
    if formato not in FORMATS:
        raise UserError(f"Formato de exportación desconocido: «{formato}». Usa CSV de TresVizo Field, CSV, GeoJSON o "
                        "KML.")
    out = Path(out)
    if out.suffix.lower() != EXTENSIONS[formato] and not (formato == "geojson" and out.suffix.lower() == ".json"):
        out = out.with_name(out.name + EXTENSIONS[formato])
    if not any(p.position is not None and p.solution != Solution.NONE for p in result.points) and formato != "field_csv":
        raise UserError("No hay puntos posprocesados que exportar.")
    choice = _fixed_choice(result, choice)
    writer = {"field_csv": _field_csv, "csv": _simple_csv, "geojson": _geojson, "kml": _kml}[formato]
    return _atomic(out, writer(result, choice))
