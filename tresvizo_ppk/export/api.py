"""Exportar los resultados (especificación §1.2.5 y §8).

- `export_points(result, choice, formato, out)`: los puntos del PPK.
- `export_static(result, choice, formato, out)`: el vértice del estático, con los mismos formatos.
- `export_trajectory(result, choice, formato, out)`: la trayectoria del PPK, el `.pos` de RTKLIB o un CSV.
- `export_rinex(observations, folder)`: el RINEX de la sesión (el convertido de la grabación del Meridian).

Formatos de puntos:

- `field_csv`: **el CSV de TresVizo Field** (`docs/csv-export.md` de las apps) con las coordenadas posprocesadas en
  sus columnas (Este, Norte, Cota, latitud, longitud, altura elipsoidal, RMS, estado de la solución, la base, el
  sistema y el geoide) y las columnas de calidad del posproceso al final. En el PPK, las demás columnas de la fila
  original salen tal cual, en su orden y con sus nombres (inglés o español, según venía); en el estático, las 94 de
  «Todas las columnas» en inglés (lo de las apps por omisión), para reimportar el vértice como base o punto de
  control. Mismas reglas de escritura que las apps: coma, punto decimal, comillas de RFC 4180 solo donde hacen
  falta, CRLF, UTF-8 con BOM, vacío es «sin dato» (nunca 0) y nunca «-0.000». Un punto del PPK que no se pudo
  posprocesar conserva sus coordenadas de campo y lo dice en «PPK solution» y «PPK message».
- `csv`: sin opciones, el CSV simple «Nombre, Este, Norte, Cota, Código» (o longitud y latitud en un sistema
  geográfico). Con `CsvOptions`, **el CSV a medida de las apps** («Opciones de CSV»): «Todas las columnas», PENZD,
  PNEZD, NXYZD o «Personalizada»; coma, punto y coma o tabulador; encabezado en inglés (Emlid) o en español, o sin
  encabezado en las tres cortas; con o sin BOM; coma decimal con punto y coma o tabulador. Solo los puntos
  posprocesados.
- `geojson`: RFC 7946, la geometría **siempre en longitud y latitud** con la altura elipsoidal (lo que manda el
  formato), y en `properties` la coordenada en el sistema elegido, su cota, su σ y su solución; los metadatos
  declaran el sistema, el marco, la época y el geoide.
- `kml`: KML 2.2 con los mismos puntos, claves y valores que el GeoJSON (contrato `docs/KML_FORMAT.md` de las apps).
- `dxf`: DXF R12 de las apps (`dxf.py`): una capa por código, el punto en 3D y su nombre y su cota como rótulos;
  junto a él, el `.prj` del sistema (WKT1 de ESRI). Solo sistemas proyectados.
- `landxml`: LandXML 1.2 de las apps (`landxml.py`) con `CgPoints`, para Civil 3D. Solo sistemas proyectados.
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from .. import calidad
from ..crs import api as crs
from ..crs import localizacion
from ..errors import UserError
from ..model import (CoordinateChoice, Ecef, Enu, NetworkFinalPoint, NetworkStaticResult, ObservationSet, PointsResult,
                     ProcessedPoint, QualityReport, QualityStatus, Solution, StaticResult, Station,
                     TransformedCoordinate)
from ..nombres import station_label
from ..version import __version__
from . import columnas, dxf, landxml
from .formato import CRLF, atomic_write, neutralized, number, quoted, write_rows  # noqa: F401 (API de las pruebas)

FORMATS = ("field_csv", "csv", "geojson", "kml", "dxf", "landxml")
EXTENSIONS = {"field_csv": ".csv", "csv": ".csv", "geojson": ".geojson", "kml": ".kml", "dxf": ".dxf",
              "landxml": ".xml"}
TRAJECTORY_FORMATS = ("pos", "csv")
APP = f"TresVizo Field PPK {__version__}"
SOLUTION_STATUS = {Solution.FIXED: "FIX", Solution.FLOAT: "FLOAT", Solution.SINGLE: "SINGLE", Solution.NONE: "NONE"}
DXF_NOT_PROJECTED = "El DXF necesita un sistema proyectado. Exporta en UTM o usa CSV o GeoJSON."
PLUMB = "en posproceso todo se asume plomeado: el punto es la antena menos la altura vertical del bastón"

# Columnas de campo que dejan de valer cuando la coordenada es la del posproceso (hablaban de la solución RTK de
# campo y de su base): se vacían en los puntos posprocesados. Las de la ocupación (inicio y fin, DOP, satélites,
# receptor, altura de antena) se quedan.
CLEARED = {"mountPoint", "caster", "correctionAge", "correctionSource", "baseID", "baseSerialNumber", "baseShift",
           "baseShiftID", "correctionsFrame", "correctionsFrameEpoch", "correctionsFrameSource", "withinTolerance",
           "violations", "horizontalSpread", "verticalSpread", "receiverHeightReference"}

LAYOUTS = ("all", "penzd", "pnezd", "nxyzd", "custom")
SHORT_LAYOUTS = {"penzd": ("name", "easting", "northing", "elevation", "description"),
                 "nxyzd": ("name", "easting", "northing", "elevation", "description"),
                 "pnezd": ("name", "northing", "easting", "elevation", "description")}
SEPARATORS = (",", ";", "\t")


@dataclass(frozen=True)
class CsvOptions:
    """«Opciones de CSV» de las apps (`PointCSV.ExportOptions`)."""
    layout: str = "all"                        # all | penzd | pnezd | nxyzd | custom
    separator: str = ","                       # «,», «;» o tabulador
    language: str = "en"                       # «en» (Emlid) o «es»
    header: bool = True                        # solo cuenta en PENZD, PNEZD y NXYZD
    bom: bool = True
    decimal_comma: bool = False                # solo con punto y coma o tabulador
    custom: tuple[tuple[str, str], ...] = ()   # «Personalizada»: (clave, encabezado propio o «»), en su orden

    def validate(self, extra_keys: set[str]) -> None:
        if self.layout not in LAYOUTS:
            raise UserError(f"Disposición de CSV desconocida: «{self.layout}».")
        if self.separator not in SEPARATORS:
            raise UserError("El separador del CSV tiene que ser coma, punto y coma o tabulador.")
        if self.language not in ("en", "es"):
            raise UserError(f"Idioma de los encabezados desconocido: «{self.language}».")
        if self.layout == "custom":
            if not self.custom:
                raise UserError("La disposición personalizada no tiene ninguna columna.")
            unknown = [k for k, _ in self.custom if k not in columnas.ENGLISH and k not in extra_keys]
            if unknown:
                raise UserError(f"Columnas desconocidas en la disposición personalizada: {', '.join(unknown)}.")

    @property
    def writes_header(self) -> bool:
        return self.header if self.layout in SHORT_LAYOUTS else True

    @property
    def uses_decimal_comma(self) -> bool:
        return self.decimal_comma and self.separator != ","


# --- Lo común a todos los formatos ------------------------------------------------------------------------------------

def _plane(c: TransformedCoordinate | None) -> tuple[str, str]:
    """(Este, Norte) con los decimales de las apps; en geográficas, longitud y latitud con 8."""
    if c is None:
        return "", ""
    if c.easting_m is not None:
        return number(c.easting_m, 3), number(c.northing_m, 3)
    return number(c.longitude_deg, 8), number(c.latitude_deg, 8)


def _elevation_value(c: TransformedCoordinate | None, choice: CoordinateChoice) -> float | None:
    """La cota: sobre el geoide si se aplicó; sin geoide, la elipsoidal; con un geoide declarado que no se pudo
    aplicar, ninguna (otra clase de altura con el mismo nombre sería peor que nada)."""
    if c is None:
        return None
    if c.localized:                 # la cota local, o ninguna (nunca otra altura en su lugar)
        return c.orthometric_height_m
    if c.orthometric_height_m is not None:
        return c.orthometric_height_m
    if choice.geoid_id:
        return None
    return c.ellipsoidal_height_m


def _elevation(c: TransformedCoordinate | None, choice: CoordinateChoice) -> str:
    return number(_elevation_value(c, choice), 3)


def _geoid_column(choice: CoordinateChoice, c: TransformedCoordinate | None) -> str:
    if not choice.geoid_id:
        return ""
    if c is not None and c.orthometric_height_m is not None:
        return choice.geoid_id.upper()
    return f"{choice.geoid_id.upper()} NO APLICADO"


def _arp(station: Station) -> Ecef:
    """La ARP de una base: su coordenada más la altura vertical de la antena (lo que dicen las columnas «Base»)."""
    if not station.antenna_height_m:
        return station.position
    p = station.position
    lat, lon, _ = crs.motor.geodetic(p.x, p.y, p.z, crs.definition("EPSG:6365").ellipsoid)
    up = crs.enu_rotation(lat, lon)[2]
    h = station.antenna_height_m
    return Ecef(p.x + up[0] * h, p.y + up[1] * h, p.z + up[2] * h, p.frame, p.epoch)


def _base_values(station: Station, choice: CoordinateChoice) -> dict[str, str]:
    c = crs.transform(_arp(station), choice)
    e, n = _plane(c)
    return {"baseEasting": e, "baseNorthing": n, "baseElevation": _elevation(c, choice),
            "baseLongitude": number(c.receiver_longitude_deg, 8), "baseLatitude": number(c.receiver_latitude_deg, 8),
            "baseEllipsoidalHeight": number(c.ellipsoidal_height_m, 3), "baseReceiver": station.receiver,
            "baseType": "Physical", "baseElevationReference": "ARP" if e else ""}


def _fixed_choice(reference: Ecef, choice: CoordinateChoice) -> CoordinateChoice:
    """Con una familia de zona automática, toda la exportación en la zona del primer punto (o de la base): una sola
    cuadrícula por archivo, como el proyecto de las apps."""
    if choice.crs_id not in crs.FAMILIES or choice.zone.strip():
        return choice
    zone = crs.transform(reference, CoordinateChoice(choice.crs_id, "", None)).zone
    return replace(choice, zone=zone)       # con la localización en uso, si la hay


def _app_time(moment: datetime) -> str:
    """Una hora como la escriben las apps en el CSV: «2026-10-15 12:44:19.8 UTC-06:00» (aquí, en UTC)."""
    m = moment.astimezone(timezone.utc)
    return f"{m:%Y-%m-%d %H:%M:%S}.{m.microsecond // 100000} UTC+00:00"


def _iso_from_app_time(text: str) -> str:
    """«2026-10-15 12:44:19.8 UTC-06:00» → «2026-10-15T12:44:19.8-06:00» (el `timeStamp` de LandXML); vacío si
    el texto no tiene esa forma."""
    parts = text.split(" ")
    if len(parts) != 3 or not parts[2].startswith("UTC") or len(parts[2]) != 9:
        return ""
    try:
        datetime.strptime(parts[0] + " " + parts[1].split(".")[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    return f"{parts[0]}T{parts[1]}{parts[2][3:]}"


def _decimal_year(moment: datetime) -> float:
    m = moment.astimezone(timezone.utc)
    start = datetime(m.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(m.year + 1, 1, 1, tzinfo=timezone.utc)
    return m.year + (m - start).total_seconds() / (end - start).total_seconds()


@dataclass
class _Row:
    """Un punto listo para cualquier formato: los valores de las columnas del CSV de Field por clave (ya escritos con
    sus reglas) y lo que piden el DXF, el LandXML y el GeoJSON."""
    name: str
    code: str
    description: str
    values: dict[str, str]
    c: TransformedCoordinate | None
    elevation: float | None = None
    timestamp: str = ""
    original: dict[str, str] = field(default_factory=dict)
    computed: _Computed | None = None          # PPK: para el GeoJSON y el KML
    sigma: Enu | None = None                   # estático: σ en el sistema elegido
    solution: Solution | None = None           # estático: la del vértice
    status: QualityStatus | None = None        # control de calidad del proyecto
    status_note: str = ""                      # «promedio de 2 ocupaciones»

    @property
    def processed(self) -> bool:
        return self.c is not None


# --- PPK: las filas ---------------------------------------------------------------------------------------------------

class _Computed:
    """La coordenada de un punto del PPK en el sistema elegido, con su σ, y la de campo para compararla."""

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

    def delta(self) -> Enu | None:
        p, f = self.pp.position, self.pp.point.field_position
        if p is None or f is None:
            return self.pp.delta_from_field_enu
        r = crs.enu_rotation(*crs.motor.geodetic(p.x, p.y, p.z, crs.definition("EPSG:6365").ellipsoid)[:2])
        d = (p.x - f.x, p.y - f.y, p.z - f.z)
        return Enu(*(sum(r[i][k] * d[k] for k in range(3)) for i in range(3)))


def _language(rows: list[dict[str, str]]) -> str:
    for row in rows:
        for name in row:
            key = columnas.BY_HEADER.get(name.casefold())
            if key and columnas.SPANISH[key] == name and columnas.ENGLISH[key] != name:
                return "es"
            if key and columnas.ENGLISH[key] == name and columnas.SPANISH[key] != name:
                return "en"
    return "en"


def _original_by_key(row: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in row.items():
        key = columnas.BY_HEADER.get(name.casefold())
        if key:
            out[key] = value
    return out


def _float(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _ppk_rows(result: PointsResult, choice: CoordinateChoice) -> list[_Row]:
    base = result.base
    base_values = _base_values(base, choice)
    rows = []
    for pp in result.points:
        comp = _Computed(pp, choice)
        original = pp.point.row
        by_key = _original_by_key(original)
        values = {key: neutralized(by_key.get(key, "")) for key, _, _ in columnas.COLUMNS}
        values["name"] = neutralized(pp.point.name)
        if pp.point.code or not original:
            values["code"] = neutralized(pp.point.code)
        field_e, field_n = _plane(comp.field) if comp.field else ("", "")
        if comp.processed:
            c = comp.c
            values["easting"], values["northing"] = _plane(c)
            values["elevation"] = _elevation(c, choice)
            values["longitude"] = number(c.receiver_longitude_deg, 8)
            values["latitude"] = number(c.receiver_latitude_deg, 8)
            values["ellipsoidalHeight"] = number(c.ellipsoidal_height_m, 3)
            if comp.sigma is not None:
                values["eastingRMS"] = number(comp.sigma.e, 3)
                values["northingRMS"] = number(comp.sigma.n, 3)
                values["elevationRMS"] = number(comp.sigma.u, 3)
                values["lateralRMS"] = number(math.hypot(comp.sigma.e, comp.sigma.n), 3)
            values["solutionStatus"] = SOLUTION_STATUS[pp.solution]
            values["correctionType"] = "PPK"
            values["samples"] = str(pp.epochs_fixed if pp.solution == Solution.FIXED else pp.epochs_total)
            values.update({k: neutralized(v) for k, v in base_values.items()})
            values["baseline"] = number(pp.distance_to_base_m, 3)
            values["csName"] = neutralized(c.label)
            values["csCode"] = c.crs_code
            values["geoid"] = _geoid_column(choice, c)
            values["geoidUndulation"] = number(c.undulation_m, 3)
            values["frame"] = pp.position.frame
            total = _float(by_key.get("totalAntennaHeight", ""))
            if total is None:
                total = pp.point.antenna_height_m
            values["antennaEllipsoidalHeight"] = number(c.ellipsoidal_height_m + total, 3)
            for key in CLEARED:
                values[key] = ""
        d = comp.delta()
        values.update({
            "ppkSolution": SOLUTION_STATUS[pp.solution] if comp.processed else "NONE",
            "ppkFixedEpochs": str(pp.epochs_fixed), "ppkEpochs": str(pp.epochs_total),
            "ppkEastingSigma": number(comp.sigma.e, 3) if comp.sigma else "",
            "ppkNorthingSigma": number(comp.sigma.n, 3) if comp.sigma else "",
            "ppkElevationSigma": number(comp.sigma.u, 3) if comp.sigma else "",
            "ppkBase": neutralized(station_label(base)),
            "ppkBaseline": number(pp.distance_to_base_m, 3),
            "fieldEasting": field_e, "fieldNorthing": field_n,
            "fieldElevation": _elevation(comp.field, choice) if comp.field else "",
            "fieldSolution": neutralized(pp.point.field_solution),
            "fieldDeltaHorizontal": number(math.hypot(d.e, d.n), 3) if d and comp.processed else "",
            "fieldDeltaVertical": number(d.u, 3) if d and comp.processed else "",
            "ppkMessage": neutralized(pp.message or ("" if comp.processed else
                                                     "sin posproceso: van las coordenadas de campo")),
            "ppkSoftware": APP,
        })
        stamp = _iso_from_app_time(by_key.get("averagingStart", ""))
        if not stamp:
            m = pp.point.start.astimezone(timezone.utc)
            stamp = f"{m:%Y-%m-%dT%H:%M:%S}.{m.microsecond // 100000}+00:00"
        row = _Row(pp.point.name, pp.point.code if (pp.point.code or not original) else by_key.get("code", ""),
                   by_key.get("description", ""), values, comp.c, _elevation_value(comp.c, choice), stamp, original)
        row.computed = comp
        rows.append(row)
    return rows


def _ppk_field_csv(result: PointsResult, rows: list[_Row]) -> bytes:
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
    out = []
    for row in rows:
        line = [row.values[key_of[name]] if key_of[name] else neutralized(row.original.get(name, ""))
                for name in header]
        out.append(line + [row.values[k] for k, _ in ppk])
    return write_rows(header + [name for _, name in ppk], out)


# --- Estático: la fila ------------------------------------------------------------------------------------------------

def _used_baselines(result: StaticResult):
    excluded = set(result.adjustment.excluded_baselines) if result.adjustment else set()
    return [b for b in result.baselines if b.base.id not in excluded]


def _static_solution(result: StaticResult) -> Solution:
    used = _used_baselines(result)
    if used and all(b.solution == Solution.FIXED for b in used):
        return Solution.FIXED
    if any(b.solution in (Solution.FIXED, Solution.FLOAT) for b in used):
        return Solution.FLOAT
    return Solution.SINGLE


def _static_adjustment_text(result: StaticResult) -> str:
    used = _used_baselines(result)
    adj = result.adjustment
    if adj is None:
        return "una sola línea base, sin ajuste"
    text = (f"mínimos cuadrados con {len(used)} líneas base, {adj.degrees_of_freedom} grados de libertad, "
            f"σ0 {adj.sigma0:.2f}, prueba χ² {'pasa' if adj.passed else 'no pasa'}")
    if adj.excluded_baselines:
        text += f"; fuera: {', '.join(adj.excluded_baselines)}"
    return text


def _static_sigma(result: StaticResult, choice: CoordinateChoice) -> Enu | None:
    covariance = result.adjustment.covariance_ecef if result.adjustment else None
    try:
        return crs.transform_sigma(result.position, result.sigma_enu, choice, covariance)
    except UserError:
        return None


def _static_row(result: StaticResult, choice: CoordinateChoice, code: str, description: str) -> _Row:
    c = crs.transform(result.position, choice)
    sigma = _static_sigma(result, choice)
    rover = result.rover
    used = _used_baselines(result)
    solution = _static_solution(result)
    values = {key: "" for key, _, _ in columnas.COLUMNS}
    e, n = _plane(c)
    satellites = sorted({s for b in used for s in b.satellites})
    total = rover.antenna_height_m
    pole = rover.pole_height_m
    start = min((b.start for b in used), default=rover.start)
    end = max((b.end for b in used), default=rover.end)
    values.update({
        "name": neutralized(result.point_name), "code": neutralized(code), "description": neutralized(description),
        "easting": e, "northing": n, "elevation": _elevation(c, choice),
        "longitude": number(c.receiver_longitude_deg, 8), "latitude": number(c.receiver_latitude_deg, 8),
        "ellipsoidalHeight": number(c.ellipsoidal_height_m, 3), "origin": "Global",
        "antennaHeight": number(pole if pole is not None else total, 3), "antennaHeightUnits": "m",
        "solutionStatus": SOLUTION_STATUS[solution], "correctionType": "Static",
        "samples": str(max((b.epochs_used for b in used), default=0)),
        "csName": neutralized(c.label), "csCode": c.crs_code, "geoid": _geoid_column(choice, c),
        "geoidUndulation": number(c.undulation_m, 3), "frame": result.position.frame,
        "deviceType": neutralized(rover.receiver_model or rover.receiver), "app": APP,
        "satellites": str(len(satellites)) if satellites else "",
        "antennaOffset": number(rover.case_offset_m, 4) if pole is not None else "",
        "totalAntennaHeight": number(total, 3),
        "antennaEllipsoidalHeight": number(c.ellipsoidal_height_m + total, 3),
    })
    for key, prefix in (("gpsSatellites", "G"), ("glonassSatellites", "R"), ("galileoSatellites", "E"),
                        ("beidouSatellites", "C"), ("qzssSatellites", "J")):
        if satellites:
            values[key] = str(sum(1 for s in satellites if s.startswith(prefix)))
    if sigma is not None:
        values.update(eastingRMS=number(sigma.e, 3), northingRMS=number(sigma.n, 3), elevationRMS=number(sigma.u, 3),
                      lateralRMS=number(math.hypot(sigma.e, sigma.n), 3))
    if start is not None and end is not None:
        values.update(averagingStart=_app_time(start), averagingEnd=_app_time(end),
                      averagingDuration=number((end - start).total_seconds(), 1),
                      observationEpoch=number(_decimal_year(start + (end - start) / 2), 4))
    if len(used) == 1:
        values.update({k: neutralized(v) for k, v in _base_values(used[0].base, choice).items()})
        values["baseline"] = number(used[0].length_m, 3)
    values.update({
        "staticSolution": SOLUTION_STATUS[solution],
        "staticBases": neutralized("; ".join(f"{station_label(b.base)} {b.length_m / 1000:.3f} km "
                                             f"{SOLUTION_STATUS[b.solution]}" for b in used)),
        "staticAdjustment": neutralized(_static_adjustment_text(result)),
        "ppkSoftware": APP,
    })
    stamp = ""
    if start is not None:
        m = start.astimezone(timezone.utc)
        stamp = f"{m:%Y-%m-%dT%H:%M:%S}.{m.microsecond // 100000}+00:00"
    row = _Row(result.point_name, code, description, values, c, _elevation_value(c, choice), stamp)
    row.sigma = sigma
    row.solution = solution
    return row


# Las columnas que añade el estático, detrás de las 94 (clave, inglés, español).
STATIC_COLUMNS: list[tuple[str, str, str]] = [
    ("staticSolution", "Static solution", "Solución del estático"),
    ("staticBases", "Static baselines", "Líneas base del estático"),
    ("staticAdjustment", "Static adjustment", "Ajuste del estático"),
    ("ppkSoftware", "Processed with", "Posprocesado con"),
]


# Las columnas que se pueden elegir en el CSV a medida (las del CSV de Field más el estado del control de calidad).
PPK_CUSTOM_COLUMNS = list(columnas.PPK_COLUMNS) + list(columnas.QUALITY_COLUMNS)
STATIC_CUSTOM_COLUMNS = list(STATIC_COLUMNS) + list(columnas.QUALITY_COLUMNS)


def _static_field_csv(row: _Row) -> bytes:
    keys = [k for k, _, _ in columnas.COLUMNS] + [k for k, _, _ in STATIC_COLUMNS]
    header = [en for _, en, _ in columnas.COLUMNS] + [en for _, en, _ in STATIC_COLUMNS]
    return write_rows(header, [[row.values[k] for k in keys]])


# --- CSV simple y CSV a medida ----------------------------------------------------------------------------------------

def _simple_csv(rows: list[_Row], choice: CoordinateChoice) -> bytes:
    out = []
    geographic = False
    for row in rows:
        if not row.processed:
            continue
        geographic = row.c.easting_m is None
        e, n = _plane(row.c)
        out.append([neutralized(row.name), e, n, _elevation(row.c, choice), neutralized(row.code),
                    row.values.get("qcStatus", ""), row.values.get("qcReason", "")])
    header = (["Nombre", "Longitud", "Latitud", "Cota", "Código"] if geographic else
              ["Nombre", "Este", "Norte", "Cota", "Código"]) + ["Estado", "Razón del estado"]
    return write_rows(header, out)


def _custom_csv(rows: list[_Row], extras: list[tuple[str, str, str]], options: CsvOptions) -> bytes:
    options.validate({k for k, _, _ in extras})
    names = {k: (en if options.language == "en" else es) for k, en, es in list(columnas.COLUMNS) + list(extras)}
    if options.layout == "all":
        entries = [(k, "") for k, _, _ in columnas.COLUMNS] + [(k, "") for k, _, _ in extras]
    elif options.layout in SHORT_LAYOUTS:
        entries = [(k, "") for k in SHORT_LAYOUTS[options.layout]]
    else:
        entries = list(options.custom)
    decimal = columnas.DECIMAL | columnas.PPK_DECIMAL
    out = []
    for row in rows:
        if not row.processed:
            continue
        line = []
        for key, _ in entries:
            value = row.values.get(key, "")
            if options.uses_decimal_comma and key in decimal:
                value = value.replace(".", ",")
            if key == "qcStatus" and options.language == "en":
                value = columnas.QUALITY_ENGLISH.get(value, value)
            line.append(value)
        out.append(line)
    header = [title.strip() or names[key] for key, title in entries] if options.writes_header else None
    return write_rows(header, out, separator=options.separator, bom=options.bom)


# --- GeoJSON y KML ----------------------------------------------------------------------------------------------------

def _round(value: float | None, decimals: int):
    if value is None or not math.isfinite(value):
        return None
    return round(value, decimals) + 0.0


def _coordinate_properties(c: TransformedCoordinate, props: dict) -> None:
    if c.easting_m is not None:
        props["este_m"] = _round(c.easting_m, 3)
        props["norte_m"] = _round(c.northing_m, 3)
    props["altura_elipsoidal_m"] = _round(c.ellipsoidal_height_m, 3)
    if c.orthometric_height_m is not None:
        props["cota_m"] = _round(c.orthometric_height_m, 3)
        props["ondulacion_m"] = _round(c.undulation_m, 3)
        props["tipo_cota"] = c.height_kind


def _sorted(d: dict) -> dict:
    return dict(sorted(d.items(), key=lambda kv: kv[0].encode("utf-8")))


def _ppk_properties(comp: _Computed) -> dict:
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
        _coordinate_properties(c, props)
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
    return _sorted(props)


def _status_properties(row: _Row, props: dict) -> dict:
    if row.status is not None:
        props["estado"] = row.values.get("qcStatus", "")
        if row.values.get("qcReason"):
            props["razon_estado"] = row.values["qcReason"]
    return _sorted(props)


def _geoid_metadata(choice: CoordinateChoice, sample: TransformedCoordinate | None) -> str:
    if sample is not None and sample.orthometric_height_m is not None:
        return sample.geoid_name
    return f"{choice.geoid_id} no aplicado" if choice.geoid_id else "ninguno"


def _ppk_metadata(result: PointsResult, choice: CoordinateChoice, sample: TransformedCoordinate | None,
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
        "geoide": _geoid_metadata(choice, sample),
        "plomeado": PLUMB,
        "puntos": len(result.points) - len(skipped),
    }
    if sample is not None and sample.attribution:
        meta["licencia_geoide"] = list(sample.attribution)
    if skipped:
        meta["puntos_sin_posproceso"] = skipped
    _localization_metadata(meta, choice, sample, pos.frame)
    return _sorted(meta)


def _localization_metadata(meta: dict, choice: CoordinateChoice, sample: TransformedCoordinate | None,
                           frame: str) -> None:
    """En locales, como las apps (`SurveyExport.metadata`): el sistema y la cota se dicen como son y la localización
    entera va con el archivo."""
    if sample is None or not sample.localized:
        return
    base = sample.base_crs_code or "transversa de Mercator local"
    d = crs.definition(sample.base_crs_code) if sample.base_crs_code else None
    meta["sistema"] = f"LOCAL: {sample.label} (cuadricula base {base}{' ' + d.name if d else ''})"
    meta["cota"] = ("local de la localizacion; altura base " + localizacion.height_base_text(choice.geoid_id)
                    + "; la geometría lleva la altura elipsoidal del punto en el suelo")
    meta["localizacion"] = localizacion.declaration_lines(choice, frame)


def _ppk_features(rows: list[_Row], result: PointsResult, choice: CoordinateChoice):
    features, skipped, sample = [], [], None
    for row in rows:
        if not row.processed:
            skipped.append(row.name)
            continue
        sample = sample or row.c
        c = row.c
        coords = [c.receiver_longitude_deg, c.receiver_latitude_deg, _round(c.ellipsoidal_height_m, 3)]
        features.append((row.name, _status_properties(row, _ppk_properties(row.computed)), coords))
    return _ppk_metadata(result, choice, sample, skipped), features


def _static_features(row: _Row, result: StaticResult, choice: CoordinateChoice):
    c = row.c
    used = _used_baselines(result)
    props: dict = {
        "nombre": result.point_name,
        "altura_en_geometria": "elipsoidal_del_suelo",
        "solucion": SOLUTION_STATUS[row.solution],
        "epocas": max((b.epochs_used for b in used), default=0),
        "altura_antena_m": _round(result.rover.antenna_height_m, 3),
        "lineas_base": [f"{station_label(b.base)}: {b.length_m / 1000:.3f} km, {SOLUTION_STATUS[b.solution]}"
                        for b in used],
    }
    if row.code:
        props["codigo"] = row.code
    if row.description:
        props["descripcion"] = row.description
    start = min((b.start for b in used), default=result.rover.start)
    end = max((b.end for b in used), default=result.rover.end)
    if start is not None and end is not None:
        props["inicio_utc"] = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        props["fin_utc"] = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _coordinate_properties(c, props)
    if row.sigma is not None:
        props["precision_h_m"] = _round(math.hypot(row.sigma.e, row.sigma.n), 3)
        props["precision_v_m"] = _round(row.sigma.u, 3)
    meta = {
        "app": APP,
        "exportado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sistema_de_las_propiedades": f"{c.crs_code} {c.label}",
        "marco": result.position.frame,
        "epoca_marco": result.position.epoch,
        "bases": [station_label(s) for s in result.bases],
        "ajuste": _static_adjustment_text(result),
        "cota": "la geometría lleva la altura elipsoidal del punto en el suelo; la cota del sistema va en cota_m",
        "geoide": _geoid_metadata(choice, c),
        "plomeado": PLUMB,
        "puntos": 1,
    }
    if c.attribution:
        meta["licencia_geoide"] = list(c.attribution)
    _localization_metadata(meta, choice, c, result.position.frame)
    coords = [c.receiver_longitude_deg, c.receiver_latitude_deg, _round(c.ellipsoidal_height_m, 3)]
    return _sorted(meta), [(result.point_name, _status_properties(row, props), coords)]


def _geojson(metadata: dict, features) -> bytes:
    doc = {
        "type": "FeatureCollection",
        "metadata": metadata,
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": coords},
                      "properties": props} for _, props, coords in features],
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


def _kml(name: str, metadata: dict, features) -> bytes:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2">', "<Document>",
             f"<name>{escape(name.strip())}</name>", "<ExtendedData>"]
    for key, value in metadata.items():
        lines.append(f'<Data name="{key}"><value>{_kml_value(value)}</value></Data>')
    lines.append("</ExtendedData>")
    for point_name, props, coords in features:
        lines += ["<Placemark>", f"<name>{escape(point_name)}</name>", "<ExtendedData>"]
        for key, value in props.items():
            lines.append(f'<Data name="{key}"><value>{_kml_value(value)}</value></Data>')
        lines.append("</ExtendedData>")
        text = f"{number(coords[0], 9)},{number(coords[1], 9)}"
        if coords[2] is not None:
            text += f",{number(coords[2], 3)}"
        lines += [f"<Point><coordinates>{text}</coordinates></Point>", "</Placemark>"]
    lines += ["</Document>", "</kml>"]
    return ("\n".join(lines) + "\n").encode("utf-8")


# --- DXF y LandXML ----------------------------------------------------------------------------------------------------

def esri_wkt(crs_code: str) -> str:
    """La WKT1 de ESRI de un sistema con código EPSG (el texto del `.prj`, `06-formatos.md` §4); vacío si no hay."""
    if not crs_code.upper().startswith("EPSG:"):
        return ""
    try:
        from pyproj import CRS
        return CRS.from_user_input(crs_code).to_wkt("WKT1_ESRI") or ""
    except Exception:  # noqa: BLE001 — un código que PROJ no conoce se queda sin .prj, como en las apps
        return ""


def _epsg_name(c: TransformedCoordinate) -> str:
    """El nombre del registro EPSG («Mexico ITRF2008 / UTM zone 13N», el de la referencia de las apps)."""
    if c.crs_code.upper().startswith("EPSG:"):
        try:
            from pyproj import CRS
            return CRS.from_user_input(c.crs_code).name
        except Exception:  # noqa: BLE001
            pass
    return c.crs_name


def _height_line(choice: CoordinateChoice, c: TransformedCoordinate) -> str:
    if c.localized:
        return "cota local de la localizacion; altura base " + localizacion.height_base_text(choice.geoid_id)
    if c.orthometric_height_m is not None:
        line = f"{c.height_kind} (H)   modelo: {c.geoid_name}"
        if c.height_kind == "normal":
            line += "   AVISO: altura NORMAL, no ortometrica: no se mezcla con cotas ortometricas sin convertir"
        return line
    if choice.geoid_id:
        return (f"vacia   geoide: NINGUNO APLICADO (se pidio {choice.geoid_id.upper()}, pero no se pudo aplicar: la "
                "cota va vacia)")
    return f"elipsoidal {c.frame_text} del punto en el suelo   geoide: ninguno"


def _declaration(c: TransformedCoordinate, choice: CoordinateChoice, origin: list[str], rows: list[_Row],
                 skipped: list[str], options: dxf.DxfOptions) -> list[str]:
    lines = ["TresVizo Field PPK: coordenadas en metros, X = Este, Y = Norte",
             f"sistema: {c.crs_code} {c.crs_name}",
             f"marco: {c.frame_text}",
             "Z: " + _height_line(choice, c)]
    lines += origin
    lines.append(f"plomeado: {PLUMB}")
    if skipped:
        lines.append(f"sin posproceso (no van en el dibujo): {', '.join(skipped)}")
    if any(r.elevation is None for r in rows):
        lines.append("capa SIN_COTA: puntos sin cota, sin Z")
    if options.point_names and options.elevation_labels and any(r.elevation is not None for r in rows):
        lines.append("capa COTA: la cota de cada punto como rotulo")
    if c.localized:
        lines.append("capa LOCALIZACION: la localizacion entera, junto al primer punto")
        lines += localizacion.declaration_lines(choice, _frame_of(rows))
    return lines


def _frame_of(rows: list[_Row]) -> str:
    """El marco de lo exportado, para comprobar el del geoide de la localización."""
    for r in rows:
        comp = getattr(r, "computed", None)
        position = getattr(getattr(comp, "pp", None), "position", None) if comp is not None else None
        if position is not None:
            return position.frame
    return localizacion.OFFICIAL_FRAME


def _require_projected(rows: list[_Row], message: str) -> None:
    if any(r.c.easting_m is None for r in rows):
        raise UserError(message)


def _dxf(rows: list[_Row], choice: CoordinateChoice, origin: list[str], skipped: list[str],
         options: dxf.DxfOptions) -> bytes:
    _require_projected(rows, DXF_NOT_PROJECTED)
    points = [dxf.DxfPoint(r.name, r.code, r.c.easting_m, r.c.northing_m, r.elevation, r.description) for r in rows]
    # En locales, la localización entera también **dentro del dibujo** (capa LOCALIZACION, como las apps): los
    # comentarios 999 se pierden al guardar el archivo en otro programa.
    local = localizacion.declaration_lines(choice, _frame_of(rows)) if rows[0].c.localized else []
    return dxf.write(points, _declaration(rows[0].c, choice, origin, rows, skipped, options), options,
                     localization=local)


def _landxml(rows: list[_Row], choice: CoordinateChoice, project: str, now: datetime | None) -> bytes:
    _require_projected(rows, landxml.NOT_PROJECTED)
    c = rows[0].c
    d = crs.definition(c.crs_code)
    vertical = (choice.geoid_id.upper() if c.orthometric_height_m is not None and choice.geoid_id else
                ("" if choice.geoid_id else f"elipsoidal {c.frame_text.split(',')[0]}"))
    if c.localized:
        vertical = "cota local"
    epsg = c.crs_code.split(":", 1)[1] if c.crs_code.upper().startswith("EPSG:") else ""
    system = landxml.CoordinateSystem(name=_epsg_name(c), epsg_code=epsg, horizontal_datum=d.datum if d else "",
                                      vertical_datum=vertical, wkt=esri_wkt(c.crs_code))
    points = [landxml.CgPoint(name=r.name, northing=r.values["northing"], easting=r.values["easting"],
                              elevation=number(r.elevation, 3), code=r.code, desc=_landxml_desc(r),
                              timestamp=r.timestamp) for r in rows]
    doc = landxml.Document(project=project, application_version=__version__,
                           exported_at=(now or datetime.now()).astimezone(), crs=system, points=points)
    return landxml.write(doc)


def _landxml_desc(r: _Row) -> str:
    """La descripción del punto y, detrás, su estado del control de calidad («mojonera · Revisar: σV 0.034 >
    0.020»): `desc` es el único atributo de texto libre de CgPoint en el esquema LandXML 1.2."""
    status = r.values.get("qcStatus", "")
    if not status:
        return r.description
    reason = r.values.get("qcReason", "")
    text = f"{status}: {reason}" if reason else status
    return f"{r.description} · {text}" if r.description else text


def _set_status(row: _Row, status: QualityStatus | None, note: str = "") -> None:
    row.status, row.status_note = status, note
    if status is None:
        return
    row.values["qcStatus"] = calidad.LEVEL_TEXT[status.level]
    reason = status.reason
    if note:
        reason = f"{reason} ({note})" if reason else note
    row.values["qcReason"] = neutralized(reason)


def points_quality(result: PointsResult, choice: CoordinateChoice) -> QualityReport:
    """La evaluación que trae el resultado (la de la interfaz, con las tolerancias, el control y las ocupaciones
    elegidas del proyecto) o, si no la trae, la de las tolerancias de omisión."""
    return result.quality or calidad.evaluate_points(result, choice=choice)


def static_quality(result: StaticResult, choice: CoordinateChoice) -> QualityReport:
    return result.quality or calidad.evaluate_static(result, choice=choice)


def _write_dxf(out: Path, data: bytes, c: TransformedCoordinate) -> Path:
    """El DXF y, junto a él, su `.prj` (`06-formatos.md` §4.3), si el sistema tiene WKT de ESRI."""
    path = atomic_write(out, data)
    wkt = esri_wkt(c.crs_code)
    if wkt:
        atomic_write(out.with_suffix(".prj"), wkt.encode("ascii"))
    return path


# --- Entradas ---------------------------------------------------------------------------------------------------------

def _target(out: str | Path, formato: str) -> Path:
    if formato not in FORMATS:
        raise UserError(f"Formato de exportación desconocido: «{formato}». Usa CSV de TresVizo Field, CSV, GeoJSON, "
                        "KML, DXF o LandXML.")
    out = Path(out)
    if out.suffix.lower() != EXTENSIONS[formato] and not (formato == "geojson" and out.suffix.lower() == ".json"):
        out = out.with_name(out.name + EXTENSIONS[formato])
    return out


def export_points(result: PointsResult, choice: CoordinateChoice, formato: str, out: str | Path, *,
                  csv_options: CsvOptions | None = None, dxf_options: dxf.DxfOptions | None = None,
                  project: str = "", now: datetime | None = None) -> Path:
    """Escribe los puntos posprocesados en `out` (se le pone la extensión del formato si no la trae)."""
    out = _target(out, formato)
    if not any(p.position is not None and p.solution != Solution.NONE for p in result.points) and formato != "field_csv":
        raise UserError("No hay puntos posprocesados que exportar.")
    reference = next((p.position for p in result.points if p.position is not None and p.solution != Solution.NONE),
                     result.base.position)
    choice = _fixed_choice(reference, choice)
    if formato == "field_csv":
        # el de las apps: todas las ocupaciones del CSV de campo, tal cual, sin el estado
        return atomic_write(out, _ppk_field_csv(result, _ppk_rows(result, choice)))
    # los demás: un resultado por nombre (la media de sus ocupaciones o la elegida) con su estado
    quality = points_quality(result, choice)
    rows = _ppk_rows(replace(result, points=[pq.point for pq in quality.points if pq.point is not None]), choice)
    for row, pq in zip(rows, [pq for pq in quality.points if pq.point is not None]):
        _set_status(row, pq.status, pq.note)
    processed = [r for r in rows if r.processed]
    skipped = [r.name for r in rows if not r.processed]
    if formato == "csv":
        data = (_custom_csv(rows, PPK_CUSTOM_COLUMNS, csv_options) if csv_options is not None else
                _simple_csv(rows, choice))
    elif formato in ("geojson", "kml"):
        metadata, features = _ppk_features(rows, result, choice)
        data = (_geojson(metadata, features) if formato == "geojson" else
                _kml(f"PPK {result.base.id} {result.rover.marker}", metadata, features))
    elif formato == "dxf":
        origin = [f"posproceso: PPK con base {station_label(result.base)}"]
        data = _dxf(processed, choice, origin, skipped, dxf_options or dxf.DxfOptions())
        return _write_dxf(out, data, processed[0].c)
    else:
        data = _landxml(processed, choice, project or f"PPK {result.base.id} {result.rover.marker}".strip(), now)
    return atomic_write(out, data)


def export_static(result: StaticResult, choice: CoordinateChoice, formato: str, out: str | Path, *,
                  code: str = "", description: str = "", csv_options: CsvOptions | None = None,
                  dxf_options: dxf.DxfOptions | None = None, project: str = "", now: datetime | None = None) -> Path:
    """Escribe el vértice del estático en `out`, con los mismos formatos que el PPK. `code` y `description` son los
    del punto en las apps (capa del DXF, `code` y `desc` del LandXML, «Code» y «Description» del CSV)."""
    out = _target(out, formato)
    choice = _fixed_choice(result.position, choice)
    row = _static_row(result, choice, code.strip(), description.strip())
    if formato != "field_csv":          # el CSV de TresVizo Field conserva el formato de las apps
        quality = static_quality(result, choice)
        _set_status(row, quality.points[0].status if quality.points else None)
    if formato == "field_csv":
        data = _static_field_csv(row)
    elif formato == "csv":
        data = (_custom_csv([row], STATIC_CUSTOM_COLUMNS, csv_options) if csv_options is not None else
                _simple_csv([row], choice))
    elif formato in ("geojson", "kml"):
        metadata, features = _static_features(row, result, choice)
        data = _geojson(metadata, features) if formato == "geojson" else _kml(result.point_name, metadata, features)
    elif formato == "dxf":
        used = _used_baselines(result)
        origin = [f"posproceso: estatico, {len(used)} linea(s) base: "
                  + "; ".join(f"{station_label(b.base)} {SOLUTION_STATUS[b.solution]}" for b in used),
                  f"ajuste: {_static_adjustment_text(result)}"]
        data = _dxf([row], choice, origin, [], dxf_options or dxf.DxfOptions())
        return _write_dxf(out, data, row.c)
    else:
        data = _landxml([row], choice, project or result.point_name, now)
    return atomic_write(out, data)


# --- Estático de varios puntos (red) ------------------------------------------------------------------------------------

NETWORK_COLUMNS: list[tuple[str, str, str]] = [
    ("staticSolution", "Static solution", "Solución del estático"),
    ("staticBases", "Static baselines", "Líneas base del estático"),
    ("staticAdjustment", "Static adjustment", "Ajuste del estático"),
    ("ppkSoftware", "Processed with", "Posprocesado con"),
]
NETWORK_CUSTOM_COLUMNS = list(NETWORK_COLUMNS) + list(columnas.QUALITY_COLUMNS)


def _network_lines(result: NetworkStaticResult, name: str):
    net = result.network
    used = {v.id for v in net.vectors if v.use and (not v.trivial or net.options.include_trivial)}
    return [ln for ln in result.lines if ln.id in used and name in (ln.from_id, ln.to_id)]


def _network_adjustment_text(result: NetworkStaticResult, point: NetworkFinalPoint) -> str:
    net = result.network
    a = net.constrained if point.adjustment == "constrained" and net.constrained else net.minimal
    kind = "restringido" if a is net.constrained else "mínimamente restringido"
    return (f"ajuste de red {kind} ({a.datum}), {a.degrees_of_freedom} grados de libertad, σ0 {a.sigma0:.2f}, "
            f"prueba χ² {'pasa' if a.passed else 'no pasa'}")


def _network_row(result: NetworkStaticResult, point: NetworkFinalPoint, choice: CoordinateChoice) -> _Row:
    c = crs.transform(point.position, choice)
    try:
        sigma = crs.transform_sigma(point.position, point.sigma_enu, choice, point.covariance_ecef)
    except UserError:
        sigma = None
    lines = _network_lines(result, point.name)
    solution = Solution.FIXED if lines and all(ln.baseline.solution == Solution.FIXED for ln in lines) else \
        Solution.FLOAT if lines else Solution.SINGLE
    occupations = [o for o in result.occupations if o.name == point.name]
    values = {key: "" for key, _, _ in columnas.COLUMNS}
    e, n = _plane(c)
    satellites = sorted({sat for ln in lines for sat in ln.baseline.satellites})
    heights = [o.antenna_height_m for o in occupations if o.antenna_height_m is not None]
    starts = [ln.baseline.start for ln in lines]
    ends = [ln.baseline.end for ln in lines]
    values.update({
        "name": neutralized(point.name), "code": neutralized(point.code),
        "description": neutralized(point.description), "easting": e, "northing": n,
        "elevation": _elevation(c, choice), "longitude": number(c.receiver_longitude_deg, 8),
        "latitude": number(c.receiver_latitude_deg, 8), "ellipsoidalHeight": number(c.ellipsoidal_height_m, 3),
        "origin": "Global", "solutionStatus": SOLUTION_STATUS[solution], "correctionType": "Static",
        "samples": str(max((ln.baseline.epochs_used for ln in lines), default=0)),
        "csName": neutralized(c.label), "csCode": c.crs_code, "geoid": _geoid_column(choice, c),
        "geoidUndulation": number(c.undulation_m, 3), "frame": point.position.frame, "app": APP,
        "satellites": str(len(satellites)) if satellites else "",
    })
    if len(set(heights)) == 1:
        values.update(totalAntennaHeight=number(heights[0], 3), antennaHeight=number(heights[0], 3),
                      antennaHeightUnits="m", antennaEllipsoidalHeight=number(c.ellipsoidal_height_m + heights[0], 3))
    for key, prefix in (("gpsSatellites", "G"), ("glonassSatellites", "R"), ("galileoSatellites", "E"),
                        ("beidouSatellites", "C"), ("qzssSatellites", "J")):
        if satellites:
            values[key] = str(sum(1 for sat in satellites if sat.startswith(prefix)))
    if sigma is not None:
        values.update(eastingRMS=number(sigma.e, 3), northingRMS=number(sigma.n, 3), elevationRMS=number(sigma.u, 3),
                      lateralRMS=number(math.hypot(sigma.e, sigma.n), 3))
    if starts:
        start, end = min(starts), max(ends)
        values.update(averagingStart=_app_time(start), averagingEnd=_app_time(end),
                      averagingDuration=number((end - start).total_seconds(), 1),
                      observationEpoch=number(_decimal_year(start + (end - start) / 2), 4))
    values.update({
        "staticSolution": SOLUTION_STATUS[solution],
        "staticBases": neutralized("; ".join(f"{ln.id} {ln.baseline.length_m / 1000:.3f} km "
                                             f"{SOLUTION_STATUS[ln.baseline.solution]}" for ln in lines)),
        "staticAdjustment": neutralized(_network_adjustment_text(result, point)),
        "ppkSoftware": APP,
    })
    stamp = ""
    if starts:
        m = min(starts).astimezone(timezone.utc)
        stamp = f"{m:%Y-%m-%dT%H:%M:%S}.{m.microsecond // 100000}+00:00"
    row = _Row(point.name, point.code, point.description, values, c, _elevation_value(c, choice), stamp)
    row.sigma = sigma
    row.solution = solution
    return row


def network_quality(result: NetworkStaticResult, choice: CoordinateChoice) -> QualityReport:
    return result.quality or calidad.evaluate_network(result, choice=choice)


def _network_features(rows: list[_Row], result: NetworkStaticResult, choice: CoordinateChoice):
    features = []
    for row, point in zip(rows, result.points):
        c = row.c
        props: dict = {"nombre": point.name, "altura_en_geometria": "elipsoidal_del_suelo",
                       "solucion": SOLUTION_STATUS[row.solution], "ocupaciones": point.occupations,
                       "lineas_base": [f"{ln.id}: {ln.baseline.length_m / 1000:.3f} km, "
                                       f"{SOLUTION_STATUS[ln.baseline.solution]}"
                                       for ln in _network_lines(result, point.name)],
                       "ajuste": _network_adjustment_text(result, point),
                       "elipse_95_m": [_round(point.ellipse95[0], 4), _round(point.ellipse95[1], 4),
                                       _round(point.ellipse95[2], 1)]}
        if row.code:
            props["codigo"] = row.code
        if row.description:
            props["descripcion"] = row.description
        _coordinate_properties(c, props)
        if row.sigma is not None:
            props["precision_h_m"] = _round(math.hypot(row.sigma.e, row.sigma.n), 3)
            props["precision_v_m"] = _round(row.sigma.u, 3)
        coords = [c.receiver_longitude_deg, c.receiver_latitude_deg, _round(c.ellipsoidal_height_m, 3)]
        features.append((point.name, _status_properties(row, props), coords))
    sample = rows[0].c
    net = result.network
    fixed = [f.point_id for f in result.fix if f.mode != "check"] or [s.id for s in result.bases]
    meta = {"app": APP, "exportado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sistema_de_las_propiedades": f"{sample.crs_code} {sample.label}",
            "marco": result.points[0].position.frame, "epoca_marco": result.points[0].position.epoch,
            "estaciones": [station_label(s) for s in result.bases], "fijas": fixed,
            "ajuste": (f"red de {len(result.points)} puntos, {len(net.loops)} bucles, "
                       f"{'restringido' if net.constrained else 'mínimamente restringido'}"),
            "cota": "la geometría lleva la altura elipsoidal del punto en el suelo; la cota del sistema va en cota_m",
            "geoide": _geoid_metadata(choice, sample), "plomeado": PLUMB, "puntos": len(rows)}
    if sample.attribution:
        meta["licencia_geoide"] = list(sample.attribution)
    _localization_metadata(meta, choice, sample, result.points[0].position.frame)
    return _sorted(meta), features


def export_network(result: NetworkStaticResult, choice: CoordinateChoice, formato: str, out: str | Path, *,
                   csv_options: CsvOptions | None = None, dxf_options: dxf.DxfOptions | None = None,
                   project: str = "", now: datetime | None = None) -> Path:
    """Los puntos nuevos ajustados de la red en `out`, con los mismos formatos que el estático y el PPK (el CSV de
    TresVizo Field, CSV simple o a medida, GeoJSON, KML, DXF y LandXML) y su estado del control de calidad."""
    out = _target(out, formato)
    if not result.points:
        raise UserError("La red no tiene puntos ajustados que exportar.")
    choice = _fixed_choice(result.points[0].position, choice)
    rows = [_network_row(result, p, choice) for p in result.points]
    if formato != "field_csv":
        statuses = {q.name: q for q in network_quality(result, choice).points}
        for row in rows:
            q = statuses.get(row.name)
            _set_status(row, q.status if q else None, q.note if q else "")
    if formato == "field_csv":
        keys = [k for k, _, _ in columnas.COLUMNS] + [k for k, _, _ in NETWORK_COLUMNS]
        header = [en for _, en, _ in columnas.COLUMNS] + [en for _, en, _ in NETWORK_COLUMNS]
        data = write_rows(header, [[row.values[k] for k in keys] for row in rows])
    elif formato == "csv":
        data = (_custom_csv(rows, NETWORK_CUSTOM_COLUMNS, csv_options) if csv_options is not None else
                _simple_csv(rows, choice))
    elif formato in ("geojson", "kml"):
        metadata, features = _network_features(rows, result, choice)
        data = (_geojson(metadata, features) if formato == "geojson" else
                _kml(project or f"Red de {len(rows)} puntos", metadata, features))
    elif formato == "dxf":
        net = result.network
        origin = [f"posproceso: estatico de varios puntos, {len(result.lines)} lineas base, "
                  f"{len(net.loops)} bucles",
                  f"ajuste: {_network_adjustment_text(result, result.points[0])}"]
        data = _dxf(rows, choice, origin, [], dxf_options or dxf.DxfOptions())
        return _write_dxf(out, data, rows[0].c)
    else:
        data = _landxml(rows, choice, project or f"Red de {len(rows)} puntos", now)
    return atomic_write(out, data)


# --- Trayectoria y RINEX ----------------------------------------------------------------------------------------------

TRAJECTORY_HEADER = ["Época UTC", "Solución", "Satélites", "Razón", "Latitud", "Longitud",
                     "Altura elipsoidal de la antena", "Este", "Norte", "Cota de la antena", "σ este local",
                     "σ norte local", "σ vertical"]
Q_STATUS = {1: "FIX", 2: "FLOAT", 3: "SBAS", 4: "DGPS", 5: "SINGLE", 6: "PPP"}


def export_trajectory(result: PointsResult, choice: CoordinateChoice, formato: str, out: str | Path, *,
                      progress=None, cancel=None) -> Path:
    """La trayectoria de la antena (ARP) del PPK: `pos`, el `.pos` de RTKLIB tal cual (ECEF, hora GPS), o `csv`, una
    fila por época en UTC con la solución, los satélites, la razón, la posición en el marco de la base y en el
    sistema elegido, y la σ de RTKLIB en los ejes locales (este, norte, arriba)."""
    if formato not in TRAJECTORY_FORMATS:
        raise UserError(f"Formato de trayectoria desconocido: «{formato}». Usa .pos o CSV.")
    pos = result.pos_file
    if pos is None or not Path(pos).is_file():
        raise UserError("La trayectoria ya no está en la carpeta de trabajo del proyecto: vuelve a procesar.")
    out = Path(out)
    suffix = ".pos" if formato == "pos" else ".csv"
    if out.suffix.lower() != suffix:
        out = out.with_name(out.name + suffix)
    if formato == "pos":
        return atomic_write(out, Path(pos).read_bytes())
    from ..core.geodesy import gps_to_utc
    from ..core.rtklib import parse_pos
    epochs = parse_pos(Path(pos))
    if not epochs:
        raise UserError("La trayectoria no tiene ninguna época con solución.")
    if not 6.0e6 < math.sqrt(sum(float(v) ** 2 for v in epochs[0].xyz)) < 7.0e6:
        raise UserError("El .pos de la trayectoria no está en coordenadas ECEF (x, y, z): vuelve a procesar.")
    frame, epoch = result.base.position.frame, result.base.position.epoch
    choice = _fixed_choice(Ecef(*(float(v) for v in epochs[0].xyz), frame, epoch), choice)
    rows = []
    total = len(epochs)
    for i, e in enumerate(epochs):
        if cancel is not None and cancel.is_set():
            raise UserError("Exportación cancelada.")
        if progress is not None and i % 500 == 0:
            progress(i / total, f"Época {i + 1} de {total}…")
        position = Ecef(*(float(v) for v in e.xyz), frame, epoch)
        c = crs.transform(position, choice)
        r = crs.enu_rotation(c.receiver_latitude_deg, c.receiver_longitude_deg)
        cov = [[sum(r[a][k] * e.cov[k][m] * r[b][m] for k in range(3) for m in range(3)) for b in range(3)]
               for a in range(3)]
        sigma = [math.sqrt(max(cov[k][k], 0.0)) for k in range(3)]
        t = gps_to_utc(e.time.replace(tzinfo=timezone.utc))
        east, north = (number(c.easting_m, 3), number(c.northing_m, 3)) if c.easting_m is not None else ("", "")
        rows.append([f"{t:%Y-%m-%dT%H:%M:%S}.{t.microsecond // 1000:03d}Z", Q_STATUS.get(e.q, str(e.q)), str(e.ns),
                     number(e.ratio, 1), number(c.receiver_latitude_deg, 9), number(c.receiver_longitude_deg, 9),
                     number(c.ellipsoidal_height_m, 3), east, north, _elevation(c, choice),
                     number(sigma[0], 3), number(sigma[1], 3), number(sigma[2], 3)])
    if progress is not None:
        progress(1.0, "Listo.")
    return atomic_write(out, write_rows(TRAJECTORY_HEADER, rows))


def export_rinex(observations: ObservationSet, folder: str | Path) -> list[Path]:
    """Copia el RINEX de la sesión (observación y navegación, el convertido de la grabación del Meridian o el unido y
    recortado) a `folder`, con sus nombres. Devuelve los archivos escritos."""
    folder = Path(folder)
    sources = [Path(p) for p in list(observations.obs) + list(observations.nav)]
    missing = [p.name for p in sources if not p.is_file()]
    if not sources:
        raise UserError("La sesión no tiene archivos RINEX que exportar.")
    if missing:
        raise UserError(f"El RINEX de la sesión ya no está en la carpeta de trabajo ({', '.join(missing)}): vuelve "
                        "a procesar.")
    names = [p.name.casefold() for p in sources]
    if len(set(names)) != len(names):
        raise UserError("Hay dos archivos RINEX con el mismo nombre en la sesión; no se pueden copiar a una carpeta.")
    written = []
    for source in sources:
        target = folder / source.name
        if target.exists() and target.resolve() == source.resolve():
            written.append(target)
            continue
        tmp = folder / f".{source.name}.tmp"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, tmp)
            tmp.replace(target)
        except PermissionError:
            tmp.unlink(missing_ok=True)
            raise UserError(f"No se pudo guardar «{source.name}»: el archivo está abierto en otro programa o la "
                            "carpeta no permite escribir.") from None
        except OSError as error:
            tmp.unlink(missing_ok=True)
            raise UserError(f"No se puede escribir en «{folder}»: {error.strerror or error}.") from None
        written.append(target)
    return written
