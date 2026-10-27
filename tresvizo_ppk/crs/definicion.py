"""La definición de un sistema de coordenadas y los paquetes `tresvizo-crs-package`, como las apps TresVizo Field.

El JSON es el de `docs/sistemas-coordenadas.md` §3 y §4 del repositorio de iOS (palabra por palabra igual en Android):
lo escriben `tools/crs/generar_paquetes.py` y el publicador, y lo leen `CRSDefinitionCoding.swift` / su par de Android.
Lo opcional se lee con su valor por omisión. Un paquete se lee entero o nada.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

PACKAGE_FORMAT = "tresvizo-crs-package"
PACKAGE_FORMAT_VERSION = 1

TRANSFORMATION_METHODS = ("geocentricTranslation", "positionVector", "coordinateFrame", "molodenskyBadekas", "ntv2")
PROJECTION_METHODS = ("transverseMercator", "lambertConicConformal1SP", "lambertConicConformal2SP")

METHOD_NAMES = {
    "geocentricTranslation": "Traslación geocéntrica (3 parámetros)",
    "positionVector": "Helmert de 7 parámetros (vector de posición)",
    "coordinateFrame": "Helmert de 7 parámetros (marco de coordenadas)",
    "molodenskyBadekas": "Molodensky-Badekas (10 parámetros)",
    "ntv2": "Rejilla NTv2",
}
PROJECTION_NAMES = {
    None: "Geográficas",
    "transverseMercator": "Transversa de Mercator",
    "lambertConicConformal1SP": "Lambert cónica conforme (1SP)",
    "lambertConicConformal2SP": "Lambert cónica conforme (2SP)",
}


class DefinitionError(Exception):
    """La definición o el paquete no se pueden leer (mensaje en español)."""


@dataclass(frozen=True)
class Ellipsoid:
    name: str
    a: float
    rf: float

    @classmethod
    def parse(cls, d: dict) -> "Ellipsoid":
        try:
            a, rf = float(d["semiMajorAxisMeters"]), float(d["inverseFlattening"])
        except (KeyError, TypeError, ValueError):
            raise DefinitionError("elipsoide sin semieje mayor o sin aplanamiento") from None
        if not (6_000_000 < a < 7_000_000 and 250 < rf < 350):
            raise DefinitionError(f"elipsoide fuera de rango: a={a}, 1/f={rf}")
        return cls(str(d.get("name", "")), a, rf)

    @property
    def proj(self) -> str:
        return f"+a={self.a!r} +rf={self.rf!r}"


@dataclass(frozen=True)
class Area:
    south: float
    north: float
    west: float
    east: float
    description: str = ""

    @classmethod
    def parse(cls, d: dict | None) -> "Area":
        if not d:
            return cls(-90.0, 90.0, -180.0, 180.0, "")
        return cls(float(d["south"]), float(d["north"]), float(d["west"]), float(d["east"]),
                   str(d.get("description", "")))

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east

    def distance_deg(self, lat: float, lon: float) -> float:
        """Distancia angular aproximada al rectángulo (cero dentro); solo sirve para elegir la región más cercana."""
        d_lat = max(self.south - lat, 0.0, lat - self.north)
        d_lon = max(self.west - lon, 0.0, lon - self.east)
        return math.hypot(d_lat, d_lon)

    @property
    def center(self) -> tuple[float, float]:
        return (self.south + self.north) / 2, (self.west + self.east) / 2


@dataclass(frozen=True)
class Transformation:
    """Una transformación oficial de un datum heredado al marco actual del país (§3.1)."""
    code: str
    name: str
    method: str
    target_code: str
    target_frame: str
    target_ellipsoid: Ellipsoid
    translation_m: tuple[float, float, float]
    rotation_arcsec: tuple[float, float, float]
    scale_ppm: float
    evaluation_point_m: tuple[float, float, float] | None
    grid_file: str | None
    grid_id: str | None
    accuracy_m: float | None
    area: Area
    source: str
    cartesian_source_ellipsoid: Ellipsoid | None

    @classmethod
    def parse(cls, d: dict) -> "Transformation":
        method = d.get("method")
        if method not in TRANSFORMATION_METHODS:
            raise DefinitionError(f"transformación {d.get('code')}: método desconocido «{method}»")
        target = d.get("target") or {}
        t = tuple(float(x) for x in d.get("translationMeters", [0, 0, 0]))
        r = tuple(float(x) for x in d.get("rotationArcseconds", [0, 0, 0]))
        if len(t) != 3 or len(r) != 3:
            raise DefinitionError(f"transformación {d.get('code')}: traslación o giro sin tres componentes")
        p = d.get("evaluationPointMeters")
        csrc = d.get("cartesianSourceEllipsoid")
        if method == "ntv2" and not (d.get("gridFile") or d.get("gridID")):
            raise DefinitionError(f"transformación {d.get('code')}: rejilla sin nombre")
        return cls(
            code=str(d.get("code", "")), name=str(d.get("name", "")), method=method,
            target_code=str(target.get("code", "")), target_frame=str(target.get("frame", "")),
            target_ellipsoid=Ellipsoid.parse(target.get("ellipsoid") or {}),
            translation_m=t, rotation_arcsec=r, scale_ppm=float(d.get("scalePPM", 0) or 0),
            evaluation_point_m=tuple(float(x) for x in p) if p else None,
            grid_file=d.get("gridFile"), grid_id=d.get("gridID"),
            accuracy_m=None if d.get("accuracyMeters") is None else float(d["accuracyMeters"]),
            area=Area.parse(d.get("areaOfUse")), source=str(d.get("source", "")),
            cartesian_source_ellipsoid=Ellipsoid.parse(csrc) if csrc else None,
        )

    @property
    def accuracy_text(self) -> str:
        if self.accuracy_m is None:
            return "exactitud no declarada por la fuente"
        v = self.accuracy_m
        return f"exactitud ±{v:.0f} m" if v == round(v) else f"exactitud ±{v:.1f} m"

    @property
    def key(self) -> tuple:
        return (self.code, self.area.south, self.area.north, self.area.west, self.area.east)


@dataclass(frozen=True)
class Definition:
    code: str
    name: str
    kind: str                         # «geographic2D» | «projected»
    datum: str
    ellipsoid: Ellipsoid
    frame: str
    frame_epoch: float | None
    itrf_realization: str | None
    projection: dict | None           # el objeto `projection` del JSON, tal cual
    unit: str
    axis_order: str
    area: Area
    countries: tuple[str, ...]
    deprecated: bool
    availability: str
    geoid_model_id: str | None
    transformations: tuple[Transformation, ...]
    source: str
    raw: dict = field(compare=False, repr=False, default_factory=dict)

    @classmethod
    def parse(cls, d: dict) -> "Definition":
        try:
            code = str(d["code"])
            name = str(d["name"])
        except KeyError:
            raise DefinitionError("sistema sin código o sin nombre") from None
        projection = d.get("projection")
        if projection is not None and projection.get("method") not in PROJECTION_METHODS:
            raise DefinitionError(f"{code}: proyección desconocida «{projection.get('method')}»")
        kind = d.get("kind") or ("projected" if projection else "geographic2D")
        if kind not in ("geographic2D", "projected") or (kind == "projected") != (projection is not None):
            raise DefinitionError(f"{code}: clase «{kind}» que no corresponde a su proyección")
        try:
            transformations = tuple(Transformation.parse(t) for t in d.get("transformations", []) or [])
        except (KeyError, TypeError, ValueError) as error:
            raise DefinitionError(f"{code}: transformación ilegible ({error})") from None
        return cls(
            code=code, name=name, kind=kind, datum=str(d.get("datum", "")),
            ellipsoid=Ellipsoid.parse(d.get("ellipsoid") or {}), frame=str(d.get("frame", "")),
            frame_epoch=None if d.get("frameEpoch") is None else float(d["frameEpoch"]),
            itrf_realization=d.get("itrfRealization"), projection=projection,
            unit=str(d.get("unit", "metro" if projection else "grado")),
            axis_order=str(d.get("axisOrder", "E,N" if projection else "N,E")),
            area=Area.parse(d.get("areaOfUse")), countries=tuple(d.get("countries", []) or []),
            deprecated=bool(d.get("deprecated", False)), availability=str(d.get("availability", "frameFromBase")),
            geoid_model_id=d.get("geoidModelID"), transformations=transformations,
            source=str(d.get("source", "")), raw=d,
        )

    # -- Lo que se dice del sistema ------------------------------------------------------------------------------

    @property
    def is_projected(self) -> bool:
        return self.projection is not None

    @property
    def is_legacy(self) -> bool:
        """Datum heredado: tiene transformación oficial a un marco actual."""
        return bool(self.transformations)

    @property
    def projection_name(self) -> str:
        return PROJECTION_NAMES[self.projection["method"] if self.projection else None]

    @property
    def receiver_frames(self) -> list[str]:
        seen: list[str] = []
        for t in self.transformations:
            if t.target_frame not in seen:
                seen.append(t.target_frame)
        return seen

    @property
    def receiver_frame(self) -> str:
        """El marco en que llegan las coordenadas de la base (`CRSDefinition.receiverFrame` de las apps)."""
        frames = self.receiver_frames
        if not frames:
            if self.itrf_realization and "ITRF" not in self.frame.upper():
                return f"{self.frame} ({self.itrf_realization})"
            return self.frame
        if len(frames) == 1:
            return frames[0]
        return ", ".join(frames[:-1]) + " o " + frames[-1] + ", según la región"

    @property
    def frame_declaration(self) -> str:
        """«SIRGAS 2000 (ITRF2000), época 2000.4», como `frameDeclaration` de las apps."""
        text = self.frame
        if self.itrf_realization and "ITRF" not in self.frame.upper():
            text += f" ({self.itrf_realization})"
        if self.frame_epoch is not None:
            text += f", época {epoch_text(self.frame_epoch)}"
        return text

    def candidates(self, lat: float, lon: float) -> list[tuple[Transformation, bool]]:
        """Las regiones que se prueban para un punto del marco de llegada: las que lo contienen, de mejor a peor
        exactitud; si ninguna, la más cercana, marcada como fuera (`CRSDefinition.candidates(near:)`)."""
        if not self.transformations:
            return []
        inside = [t for t in self.transformations if t.area.contains(lat, lon)]
        if inside:
            inside.sort(key=lambda t: math.inf if t.accuracy_m is None else t.accuracy_m)
            return [(t, True) for t in inside]
        nearest = min(self.transformations, key=lambda t: t.area.distance_deg(lat, lon))
        return [(nearest, False)]


def epoch_text(epoch: float) -> str:
    """La época con los decimales que trae: 2000.4, 2006.632, 2018.0."""
    text = f"{epoch:.3f}"
    while text.endswith("0") and not text.endswith(".0"):
        text = text[:-1]
    return text


@dataclass(frozen=True)
class Package:
    id: str
    version: str
    title: str
    country: str
    epsg_database: str
    summary: str
    systems: tuple[Definition, ...]


def read_package(data: bytes | str | dict) -> Package:
    """Lee un paquete entero o nada: otro `format`, un `formatVersion` mayor o una lista vacía lo rechazan."""
    if isinstance(data, (bytes, str)):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise DefinitionError("El archivo no es un paquete de sistemas de coordenadas de TresVizo.") from None
    if not isinstance(data, dict) or data.get("format") != PACKAGE_FORMAT:
        raise DefinitionError("El archivo no es un paquete de sistemas de coordenadas de TresVizo.")
    version = data.get("formatVersion")
    if not isinstance(version, int) or version > PACKAGE_FORMAT_VERSION:
        raise DefinitionError("El paquete de sistemas es de una versión más nueva. Actualiza la app.")
    systems = data.get("systems")
    if not isinstance(systems, list) or not systems:
        raise DefinitionError("El paquete de sistemas no trae ningún sistema.")
    parsed = tuple(Definition.parse(s) for s in systems)
    return Package(id=str(data.get("id", "")), version=str(data.get("version", "")), title=str(data.get("title", "")),
                   country=str(data.get("country", "")), epsg_database=str(data.get("epsgDatabase", "")),
                   summary=str(data.get("summary", "")), systems=parsed)


def merge(a: Definition, b: Definition) -> Definition:
    """Un código que traen dos paquetes es una sola definición, con los países y las regiones de los dos."""
    countries = tuple(dict.fromkeys(a.countries + b.countries))
    keys = {t.key for t in a.transformations}
    transformations = a.transformations + tuple(t for t in b.transformations if t.key not in keys)
    from dataclasses import replace
    return replace(a, countries=countries, transformations=transformations)
