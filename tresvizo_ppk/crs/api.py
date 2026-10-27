"""Sistemas de coordenadas, datums y geoides: lo que llama la interfaz (especificación §1.3 y §8).

- `catalog()`: lo instalado (incluido de fábrica y descargado) y lo descargable del índice firmado.
- `download_index(progress, cancel)`: baja y verifica el índice publicado en `cadaritre/GNSS-TresVizo`.
- `install(item_id, progress, cancel)` / `uninstall(item_id)`: un paquete de sistemas, un geoide o una rejilla.
- `systems()`: los sistemas que se pueden elegir ahora (con las familias de zona automática).
- `transform(position, choice)`: una posición ECEF del marco oficial en el sistema, datum y geoide elegidos.
- `transform_sigma(position, sigma_enu, choice, covariance_ecef=None)`: la σ en los ejes de ese sistema.

El resultado es el mismo de las apps TresVizo Field: mismas definiciones, mismos geoides byte a byte y la misma
interpolación, comprobado con sus vectores (`crs_reference.json` y las pruebas de geoides) en `tests/test_crs.py`.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import Cancelled, UserError
from ..model import CoordinateChoice, Ecef, Enu, Matrix3, TransformedCoordinate
from ..datos import biblioteca, indice
from . import motor, mvgeoid
from .definicion import Definition, DefinitionError, epoch_text, merge, read_package

# Familias de sistemas con zona automática (por la longitud del punto) o elegida en `CoordinateChoice.zone`.
FAMILIES = {
    "mexico-itrf2008-utm": ("Mexico ITRF2008 / UTM", "Mexico ITRF2008 / UTM, zona según la longitud (11N a 16N)"),
    "wgs84-utm": ("WGS 84 / UTM", "WGS 84 / UTM, zona según la longitud"),
}
ALIASES = {"mexico-itrf2008": "EPSG:6365", "wgs84": "EPSG:4326", "wgs-84": "EPSG:4326"}

PACKAGE_DATA = Path(__file__).resolve().parent / "datos"
FACTORY_FILE = PACKAGE_DATA / "fabrica.json"
BUNDLED_GEOIDS = ("ggm10", "ggm25")

_lock = threading.RLock()
_registry: dict[str, Definition] | None = None
_registry_origin: dict[str, str] = {}
_geoids: dict[str, mvgeoid.Grid] = {}


# --- Lo que ve la interfaz -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogItem:
    id: str
    kind: str                       # «crs-package», «geoid», «datum-grid» o «factory» (lo de fábrica)
    title: str
    region: str = ""
    countries: tuple[str, ...] = ()
    authority: str = ""
    license: str = ""
    license_url: str = ""
    attribution: str = ""
    transformation_notice: str = ""
    version: str = ""
    revision: int = 0
    bytes: int = 0
    size_text: str = ""
    bundled: bool = False           # viene con el programa
    installed: bool = False
    update_available: bool = False  # el índice tiene una revisión más nueva que la instalada
    downloadable: bool = False      # está en el índice aceptado
    requires: tuple[str, ...] = ()
    systems: int = 0                # sistemas que trae un paquete


@dataclass(frozen=True)
class SystemInfo:
    code: str                       # «EPSG:6369» o el id de una familia («mexico-itrf2008-utm»)
    name: str
    kind: str                       # «projected», «geographic2D» o «family»
    datum: str = ""
    countries: tuple[str, ...] = ()
    frame: str = ""
    legacy: bool = False
    accuracy_text: str = ""         # «datum heredado · ±5 m»
    missing_grids: tuple[str, ...] = ()
    geoid_model_id: str | None = None
    origin: str = ""                # «fábrica» o el id del paquete
    area: str = ""


# --- Registro --------------------------------------------------------------------------------------------------------

def _load_registry() -> dict[str, Definition]:
    global _registry
    with _lock:
        if _registry is not None:
            return _registry
        reg: dict[str, Definition] = {}
        origin: dict[str, str] = {}
        sources = [("fábrica", FACTORY_FILE)]
        for item_id in sorted(biblioteca.installed_items()):
            path = biblioteca.installed_file("crs-package", item_id)
            if path.is_file():
                sources.append((item_id, path))
        for name, path in sources:
            try:
                package = read_package(path.read_bytes())
            except (OSError, DefinitionError):
                continue    # un paquete dañado no tumba los demás; `catalog()` lo enseña como no instalado
            for d in package.systems:
                if d.code in reg:
                    reg[d.code] = merge(reg[d.code], d)
                else:
                    reg[d.code] = d
                    origin[d.code] = name
        motor.add_grid_directory(biblioteca.kind_dir("datum-grid"))
        _registry = reg
        _registry_origin.clear()
        _registry_origin.update(origin)
        return reg


def reload() -> None:
    """Vuelve a leer lo instalado (tras instalar, borrar o cambiar la carpeta de datos)."""
    global _registry
    with _lock:
        _registry = None
        _geoids.clear()


def definition(code: str) -> Definition | None:
    return _load_registry().get(code)


def _grid_available(name: str | None) -> bool:
    if not name:
        return False
    base = biblioteca.kind_dir("datum-grid")
    return (base / f"{name}.gsb").is_file() or (base / name).is_file() or motor._find_grid(name)


def missing_grids(d: Definition) -> list[str]:
    out = []
    for t in d.transformations:
        if t.method == "ntv2" and not (_grid_available(t.grid_id) or _grid_available(t.grid_file)):
            out.append(t.grid_file or t.grid_id or "")
    return out


def systems() -> list[SystemInfo]:
    """Los sistemas elegibles ahora: las familias de zona automática primero y luego cada sistema instalado."""
    reg = _load_registry()
    out = [SystemInfo(code=k, name=v[1], kind="family", datum=("Mexico ITRF2008" if k.startswith("mexico") else
                                                                "World Geodetic System 1984"),
                      countries=("MX",) if k.startswith("mexico") else (), origin="fábrica")
           for k, v in FAMILIES.items()]
    for d in reg.values():
        if d.deprecated:
            continue
        acc = ""
        if d.is_legacy:
            best = min((t.accuracy_m for t in d.transformations if t.accuracy_m is not None), default=None)
            acc = "datum heredado" + (f" · ±{best:g} m" if best is not None else "")
        miss = tuple(missing_grids(d))
        if miss:
            acc += " · falta la rejilla"
        out.append(SystemInfo(code=d.code, name=d.name, kind=d.kind, datum=d.datum, countries=d.countries,
                              frame=d.frame_declaration, legacy=d.is_legacy, accuracy_text=acc, missing_grids=miss,
                              geoid_model_id=d.geoid_model_id, origin=_registry_origin.get(d.code, ""),
                              area=d.area.description))
    return out


# --- Geoides ---------------------------------------------------------------------------------------------------------

def _geoid_path(geoid_id: str) -> Path | None:
    if geoid_id in BUNDLED_GEOIDS:
        return PACKAGE_DATA / f"{geoid_id}.mvgeoid"
    path = biblioteca.installed_file("geoid", geoid_id)
    return path if path.is_file() else None


def geoid(geoid_id: str) -> mvgeoid.Grid | None:
    """El geoide abierto, o None si no está en este equipo. Un archivo dañado es un error para el usuario."""
    with _lock:
        if geoid_id in _geoids:
            return _geoids[geoid_id]
        path = _geoid_path(geoid_id)
        if path is None:
            return None
        try:
            grid = mvgeoid.read(path)
        except (OSError, mvgeoid.FormatError) as error:
            raise UserError(f"El geoide «{geoid_id}» no se pudo abrir: {error} Bórralo y vuelve a descargarlo en "
                            "Sistemas y geoides.") from None
        if grid.model_id != geoid_id:
            raise UserError(f"El archivo del geoide «{geoid_id}» trae otro modelo («{grid.model_id}»).")
        _geoids[geoid_id] = grid
        return grid


def _geoid_citation(m: dict) -> str:
    """`GeoidModel.citation` de las apps."""
    parts = [f"{m.get('name', '')} {m.get('version', '')}".strip(), str(m.get("authority", "")), str(m.get("frame", ""))]
    if m.get("frameEpoch") is not None:
        parts.append(f"época {float(m['frameEpoch']):.1f}")
    parts.append(f"resolución {float(m.get('gridStepArcMinutes', 0)):.1f}′")
    kind = "normal" if m.get("heightKind") == "normal" else "ortométrica"
    parts.append(f"altura {kind}")
    if m.get("verticalDatum"):
        parts.append(str(m["verticalDatum"]))
    total = _geoid_uncertainty(m)
    if total is not None:
        parts.append(f"±{total:.2f} m")
    return " · ".join(parts)


def _geoid_uncertainty(m: dict) -> float | None:
    declared = m.get("declaredAccuracyMeters")
    if declared is None:
        return None
    resampling = m.get("resamplingErrorMeters") or 0.0
    return math.sqrt(float(declared) ** 2 + float(resampling) ** 2)


def _frame_family(frame: str) -> str:
    f = frame.upper()
    if "NAD83" in f or "NAD 83" in f:
        return "NAD83"
    if "ITRF" in f or "WGS" in f or "SIRGAS" in f:
        return "ITRF/WGS84"
    return f


def _geoid_name(geoid_id: str) -> str:
    payload = biblioteca.accepted_index()
    for item in (payload or {}).get("items", []):
        if item["id"] == geoid_id:
            return item.get("title", geoid_id)
    return geoid_id.upper()


def _geoid_download_hint(geoid_id: str) -> str:
    payload = biblioteca.accepted_index()
    for item in (payload or {}).get("items", []):
        if item["id"] == geoid_id and item["kind"] == "geoid":
            size = biblioteca.size_text(item["file"]["bytes"])
            return f"Descarga el geoide {item.get('title', geoid_id)} ({size}) en Sistemas y geoides para ver cotas."
    return (f"El geoide «{geoid_id}» no está en el catálogo de este programa: busca actualizaciones del catálogo en "
            "Sistemas y geoides. Sin él no hay cota; la altura elipsoidal sí.")


# --- Elegir el sistema -----------------------------------------------------------------------------------------------

_ZONE = re.compile(r"^\s*(\d{1,2})\s*([NnSs]?)\s*$")


def _parse_zone(zone: str) -> tuple[int, str] | None:
    m = _ZONE.match(zone or "")
    if not m:
        return None
    return int(m.group(1)), (m.group(2) or "N").upper()


def resolve(choice: CoordinateChoice, lat: float | None = None, lon: float | None = None) -> tuple[Definition, str]:
    """El sistema concreto de una elección (con su zona, si la familia la elige por la longitud)."""
    reg = _load_registry()
    crs_id = ALIASES.get(choice.crs_id, choice.crs_id)
    if crs_id in FAMILIES:
        z = _parse_zone(choice.zone)
        if z is None:
            if choice.zone.strip():
                raise UserError(f"La zona «{choice.zone}» no es válida: escribe el número y el hemisferio, como «14N».")
            if lat is None or lon is None:
                raise UserError("Para elegir la zona UTM automáticamente hace falta la posición.")
            number = min(int((lon + 180.0) // 6.0) + 1, 60)
            z = (number, "N" if lat >= 0 else "S")
        number, hemi = z
        if crs_id == "mexico-itrf2008-utm":
            if hemi != "N" or not 11 <= number <= 16:
                raise UserError(f"El punto cae en la zona UTM {number}{hemi}, fuera de las de México (11N a 16N): "
                                "elige otro sistema.")
            code = f"EPSG:{6366 + number - 11}"
        else:
            if not 1 <= number <= 60:
                raise UserError(f"La zona UTM {number} no existe (van de 1 a 60).")
            code = f"EPSG:{(32600 if hemi == 'N' else 32700) + number}"
        d = reg.get(code)
        if d is None:
            raise UserError(f"La zona UTM {number}{hemi} de WGS 84 no viene de fábrica: descarga el paquete «Mundo» "
                            "en Sistemas y geoides.")
        return d, f"{number}{hemi}"
    d = reg.get(crs_id)
    if d is None:
        raise UserError(f"El sistema «{choice.crs_id}» no está en este equipo: descarga su paquete en Sistemas y "
                        "geoides.")
    zone = ""
    m = re.search(r"(?:zona|zone)\s+(\d{1,2}[NS])\b", d.name)
    if m:
        zone = m.group(1)
    return d, zone


def _target_epoch(code: str) -> float | None:
    d = _load_registry().get(code)
    return d.frame_epoch if d else None


def _frame_matches(position: Ecef, frame_text: str, epoch: float | None) -> bool:
    norm = lambda s: re.sub(r"[\s_-]", "", s.upper())
    if norm(position.frame) not in norm(frame_text):
        return False
    return epoch is None or abs(epoch - position.epoch) < 1e-6


# --- Transformar -----------------------------------------------------------------------------------------------------

def transform(position: Ecef, choice: CoordinateChoice) -> TransformedCoordinate:
    """La posición en el sistema, datum y geoide elegidos, con todo lo que el informe y la exportación dicen de ella."""
    warnings: list[str] = []
    # El elipsoide en que llegan las coordenadas: el del sistema, o el del marco de llegada si es un datum heredado.
    reg = _load_registry()
    probe = reg.get(ALIASES.get(choice.crs_id, choice.crs_id))
    ellipsoid = probe.ellipsoid if probe is not None else _load_registry()["EPSG:6365"].ellipsoid
    lat, lon, h = motor.geodetic(position.x, position.y, position.z, ellipsoid)
    d, zone = resolve(choice, lat, lon)
    if d.is_legacy:
        cands = d.candidates(lat, lon)
        target = cands[0][0].target_ellipsoid
        if (target.a, target.rf) != (ellipsoid.a, ellipsoid.rf):
            lat, lon, h = motor.geodetic(position.x, position.y, position.z, target)
    elif (d.ellipsoid.a, d.ellipsoid.rf) != (ellipsoid.a, ellipsoid.rf):
        lat, lon, h = motor.geodetic(position.x, position.y, position.z, d.ellipsoid)

    try:
        dlat, dlon, t, inside = motor.datum_point(d, lat, lon)
    except motor.GridUnavailable as error:
        raise UserError(f"La transformación {error.transformation} de {d.name} usa la rejilla {error.grid}, que no "
                        "está en este equipo: descárgala en Sistemas y geoides.") from None
    except motor.OutsideGrid as error:
        raise UserError(f"El punto está fuera de la rejilla {error.grid} de {d.name}.") from None

    transformation_text, accuracy = "", None
    if t is not None:
        accuracy = t.accuracy_m
        transformation_text = f"{t.name} ({t.code}), {t.accuracy_text}"
        if not inside:
            warnings.append(f"El punto está fuera del área de todas las regiones de la transformación de {d.name}: "
                            f"se usó la más cercana ({t.name}).")
        frame_ok = _frame_matches(position, t.target_frame, _target_epoch(t.target_code))
        receiver_frame = t.target_frame
    else:
        frame_ok = _frame_matches(position, f"{d.frame} {d.itrf_realization or ''}", d.frame_epoch)
        receiver_frame = d.frame_declaration
    if not frame_ok:
        warnings.append(f"Las coordenadas están en {position.frame}, época {epoch_text(position.epoch)} (el marco de "
                        f"la base) y {d.name} es de {receiver_frame}: el programa no transforma entre marcos ni "
                        "épocas, así que se toman como si fueran del mismo marco (entre ellos puede haber "
                        "decímetros).")
    if not d.area.contains(lat, lon):
        warnings.append(f"El punto está fuera del área de uso de {d.name}"
                        + (f" ({d.area.description})." if d.area.description else "."))

    northing = easting = conv = scale = None
    if d.is_projected:
        try:
            easting, northing, conv, scale = motor.project(d, dlat, dlon)
        except ValueError:
            raise UserError(f"El punto queda fuera del dominio de la proyección de {d.name}.") from None

    # La cota: H = h − N con el geoide elegido, comprobando antes que puede aplicarse.
    ortho = und = unc = None
    geoid_name = height_kind = ""
    attribution: tuple[str, ...] = ()
    gid = choice.geoid_id or None
    applied = False
    if gid:
        grid = geoid(gid)
        if grid is None:
            warnings.append(_geoid_download_hint(gid))
        else:
            m = grid.metadata
            geoid_name = _geoid_citation(m)
            height_kind = "normal" if m.get("heightKind") == "normal" else "ortométrica"
            unc = _geoid_uncertainty(m)
            attribution = tuple(x for x in (m.get("attribution"), m.get("transformationNotice")) if x)
            expected = m.get("expectedFrame")
            if m.get("verification", "verified") != "verified":
                warnings.append(f"No disponible: falta la ficha oficial de {m.get('authority', '')}.")
            elif expected and _frame_family(expected) != _frame_family(position.frame):
                warnings.append(f"{m.get('name')} {m.get('version')} requiere alturas en {expected} y el proyecto "
                                f"usa {position.frame}: la cota tendría un error de hasta 1.5 m. El proyecto y la base "
                                f"deben estar en {expected}.")
            else:
                und = grid.undulation(lat, lon)
                if und is None:
                    warnings.append(f"El punto está fuera de la cobertura de {m.get('name')} {m.get('version')}: no "
                                    f"hay cota {height_kind}.")
                else:
                    ortho = h - und
                    applied = True
    label = d.name + (f" + {gid.upper()} height" if applied else "")

    return TransformedCoordinate(
        latitude_deg=dlat, longitude_deg=dlon, ellipsoidal_height_m=h,
        northing_m=northing, easting_m=easting, orthometric_height_m=ortho, undulation_m=und, zone=zone,
        convergence_deg=conv, scale_factor=scale, receiver_latitude_deg=lat, receiver_longitude_deg=lon,
        crs_code=d.code, crs_name=d.name, frame_text=d.frame_declaration, axis_order=d.axis_order,
        geoid_id=gid, geoid_name=geoid_name,
        height_kind=height_kind if applied else "", geoid_uncertainty_m=unc if applied else None,
        transformation=transformation_text, transformation_accuracy_m=accuracy, inside_transformation_area=inside,
        label=label, attribution=attribution, warnings=tuple(warnings),
    )


def enu_rotation(lat_deg: float, lon_deg: float) -> list[list[float]]:
    """Filas e, n, u de la rotación ECEF → ENU local en una latitud y longitud geodésicas."""
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    return [[-so, co, 0.0], [-sl * co, -sl * so, cl], [cl * co, cl * so, sl]]


def ecef_to_enu_covariance(cov: Matrix3, lat_deg: float, lon_deg: float) -> list[list[float]]:
    r = enu_rotation(lat_deg, lon_deg)
    rc = [[sum(r[i][k] * cov[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return [[sum(rc[i][k] * r[j][k] for k in range(3)) for j in range(3)] for i in range(3)]


def transform_sigma(position: Ecef, sigma_enu: Enu, choice: CoordinateChoice,
                    covariance_ecef: Matrix3 | None = None) -> Enu:
    """La σ de una posición en los ejes del sistema elegido: σ Este y σ Norte de cuadrícula y σ vertical.

    En un sistema proyectado se propaga con el jacobiano de la conversión entera (datum y proyección), calculado por
    diferencias en el propio punto, así que incluye la convergencia y el factor de escala. Con la matriz de covarianza
    ECEF se propaga completa (con la correlación entre ejes); sin ella, se toman independientes la σ E y la σ N. La σ
    vertical no cambia: las alturas no se transforman (la incertidumbre del geoide se suma aparte en la cota).
    """
    lat, lon, _ = motor.geodetic(position.x, position.y, position.z, _load_registry()["EPSG:6365"].ellipsoid)
    if covariance_ecef is not None:
        c = ecef_to_enu_covariance(covariance_ecef, lat, lon)
        c2 = [[c[0][0], c[0][1]], [c[1][0], c[1][1]]]
        sigma_u = math.sqrt(max(c[2][2], 0.0))
    else:
        c2 = [[sigma_enu.e ** 2, 0.0], [0.0, sigma_enu.n ** 2]]
        sigma_u = sigma_enu.u
    d, _ = resolve(choice, lat, lon)
    if not d.is_projected:
        return Enu(math.sqrt(max(c2[0][0], 0.0)), math.sqrt(max(c2[1][1], 0.0)), sigma_u)
    base = transform(position, choice)
    r = enu_rotation(lat, lon)
    step = 1.0
    jac = [[0.0, 0.0], [0.0, 0.0]]
    for col in range(2):        # 0: un metro al este; 1: un metro al norte
        dx, dy, dz = (r[col][0] * step, r[col][1] * step, r[col][2] * step)
        moved = transform(Ecef(position.x + dx, position.y + dy, position.z + dz, position.frame, position.epoch),
                          CoordinateChoice(base.crs_code, "", None))
        jac[0][col] = (moved.easting_m - base.easting_m) / step
        jac[1][col] = (moved.northing_m - base.northing_m) / step
    jc = [[sum(jac[i][k] * c2[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    g = [[sum(jc[i][k] * jac[j][k] for k in range(2)) for j in range(2)] for i in range(2)]
    return Enu(math.sqrt(max(g[0][0], 0.0)), math.sqrt(max(g[1][1], 0.0)), sigma_u)


def height_sigma(sigma_u: float | None, coordinate: TransformedCoordinate) -> float | None:
    """σ de la cota: la del GNSS y la del geoide en cuadratura (domina la del geoide con una solución fija)."""
    if sigma_u is None or coordinate.orthometric_height_m is None:
        return None
    if coordinate.geoid_uncertainty_m is None:
        return None
    return math.sqrt(sigma_u ** 2 + coordinate.geoid_uncertainty_m ** 2)


# --- Catálogo, descarga e instalación --------------------------------------------------------------------------------

def _item_from_index(item: dict, *, installed: dict | None, bundled: bool = False) -> CatalogItem:
    revision = int(item.get("revision", 0))
    inst_rev = int(installed.get("revision", 0)) if installed else 0
    package = item.get("crsPackage") or {}
    return CatalogItem(
        id=item["id"], kind=item["kind"], title=item.get("title", item["id"]), region=item.get("region", ""),
        countries=tuple(item.get("countries", [])), authority=item.get("authority", ""),
        license=item.get("license", ""), license_url=item.get("licenseURL") or "",
        attribution=item.get("attribution", ""), transformation_notice=item.get("transformationNotice") or "",
        version=str(item.get("version", "")), revision=revision, bytes=int(item["file"]["bytes"]),
        size_text=biblioteca.size_text(int(item["file"]["bytes"])), bundled=bundled,
        installed=bundled or installed is not None, update_available=installed is not None and inst_rev < revision,
        downloadable=True, requires=tuple(item.get("requires", []) or []),
        systems=int(package.get("systems", 0)) if isinstance(package.get("systems", 0), int) else 0)


def catalog() -> list[CatalogItem]:
    """Lo de fábrica, lo instalado y lo descargable del índice aceptado (el bajado o el incluido)."""
    out: list[CatalogItem] = []
    factory = read_package(FACTORY_FILE.read_bytes())
    out.append(CatalogItem(id="fabrica", kind="factory", title="WGS 84 y Mexico ITRF2008 (de fábrica)",
                           region="México y mundial", countries=("MX",), authority="Registro EPSG (IOGP)",
                           license="Registro EPSG: uso libre con cita de la fuente",
                           license_url="https://epsg.org/terms-of-use.html",
                           attribution=f"Parámetros del registro EPSG de IOGP, {factory.epsg_database}",
                           version=factory.version, bundled=True, installed=True, systems=len(factory.systems)))
    for gid in BUNDLED_GEOIDS:
        g = geoid(gid)
        m = g.metadata if g else {}
        out.append(CatalogItem(id=gid, kind="geoid", title=f"{gid.upper()} · {m.get('name', '')} {m.get('version', '')}",
                               region="México", countries=tuple(m.get("countries", [])),
                               authority=m.get("authority", ""), license="Términos de Libre Uso de la Información "
                               "del INEGI", license_url="https://www.inegi.org.mx/inegi/terminos.html",
                               attribution=m.get("attribution", ""),
                               transformation_notice=m.get("transformationNotice", ""), version=str(m.get("version", "")),
                               bytes=(PACKAGE_DATA / f"{gid}.mvgeoid").stat().st_size,
                               size_text=biblioteca.size_text((PACKAGE_DATA / f"{gid}.mvgeoid").stat().st_size),
                               bundled=True, installed=True))
    installed = biblioteca.installed_items()
    payload = biblioteca.accepted_index()
    seen = set()
    for item in (payload or {}).get("items", []):
        if item["id"] in BUNDLED_GEOIDS:
            continue
        seen.add(item["id"])
        out.append(_item_from_index(item, installed=installed.get(item["id"])))
    for item_id, item in installed.items():   # instalado y retirado del índice: sigue sirviendo
        if item_id not in seen:
            out.append(_item_from_index(item, installed=item))
    return out


def download_index(progress=None, cancel=None) -> list[CatalogItem]:
    """Baja el índice firmado publicado, lo verifica (firma Ed25519, reglas y secuencia) y devuelve el catálogo."""
    if progress:
        progress(0.0, "Bajando el catálogo de datos de github.com/cadaritre/GNSS-TresVizo…")
    biblioteca.fetch_index(progress=progress, cancel=cancel)
    if progress:
        progress(1.0, "Catálogo de datos al día.")
    return catalog()


def install(item_id: str, progress=None, cancel=None) -> CatalogItem:
    """Baja e instala una entrada del índice (y lo que pide), comprobándola antes de usarla (`datos.biblioteca`)."""
    biblioteca.install(item_id, progress=progress, cancel=cancel)
    reload()
    return next(c for c in catalog() if c.id == item_id)


def uninstall(item_id: str) -> None:
    """Borra algo descargado (lo de fábrica no se borra). Los proyectos que lo usan dirán que falta."""
    if item_id in BUNDLED_GEOIDS or item_id == "fabrica":
        raise UserError("Lo que viene de fábrica no se puede borrar.")
    biblioteca.uninstall(item_id)
    reload()


# --- Las comprobaciones de cada clase al instalarse ---------------------------------------------------------------

def _validate_geoid(item: dict, data: bytes) -> None:
    title = item.get("title", item["id"])
    try:
        grid = mvgeoid.decode(data)
    except mvgeoid.FormatError as error:
        raise UserError(f"{title}: {error}") from None
    if grid.model_id != item["id"] or grid.checksum != (item.get("geoid") or {}).get("dataChecksum"):
        raise UserError(f"{title}: los datos del geoide no son los del catálogo firmado; no se instala.")


def _validate_package(item: dict, data: bytes) -> None:
    title = item.get("title", item["id"])
    try:
        package = read_package(data)
    except DefinitionError as error:
        raise UserError(f"{title}: {error}") from None
    if package.id and package.id != item["id"]:
        raise UserError(f"{title}: el paquete trae otro id («{package.id}»); no se instala.")


def _validate_grid(item: dict, data: bytes) -> None:
    if b"NUM_OREC" not in data[:16]:
        raise UserError(f"{item.get('title', item['id'])}: el archivo no es una rejilla NTv2; no se instala.")


biblioteca.register_kind("crs-package", "sistemas", ".json", _validate_package, "Sistemas de coordenadas")
biblioteca.register_kind("geoid", "geoides", ".mvgeoid", _validate_geoid, "Geoide")
biblioteca.register_kind("datum-grid", "rejillas", ".gsb", _validate_grid, "Transformación de datum")
biblioteca.on_change(lambda: reload())


def set_data_dir(path) -> None:
    """La carpeta de datos de Ajustes."""
    biblioteca.set_data_dir(path)
    reload()
