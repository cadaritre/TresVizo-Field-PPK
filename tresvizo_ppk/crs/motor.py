"""La matemática de los sistemas: PROJ (pyproj) con las definiciones del catálogo firmado.

- **Proyección**: con el código EPSG del paquete (`Transformer.from_crs` del sistema geográfico del datum al sistema),
  comprobado al registrarse contra los parámetros de la definición; si un código no está en el registro EPSG de esta
  versión de PROJ, o PROJ da otra cosa, manda la definición (la de las apps) con la misma fórmula de PROJ.
  Convergencia y factor de escala, de `Proj.get_factors`.
- **Datum heredado**: la directa oficial (datum heredado con altura cero → geocéntricas → Helmert / Molodensky-Badekas
  → marco de llegada, o la rejilla NTv2) como una tubería de PROJ con los parámetros del paquete, y la inversa
  **exacta por iteración**, como las apps (`DatumTransformation.inverse`): así la directa es la oficial y la vuelta
  cierra al nanómetro.

Los transformadores de PROJ no se comparten entre hilos sin cuidado: todo pasa por un candado.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path

from pyproj import CRS, Proj, Transformer
from pyproj.enums import TransformDirection
from pyproj.exceptions import CRSError, ProjError

from .definicion import Definition, Ellipsoid, Transformation

INVERSE_ITERATIONS = 8
INVERSE_TOLERANCE_DEG = 1e-13          # una centésima de micra
EPSG_CHECK_TOLERANCE_M = 1e-6


class OutsideGrid(Exception):
    def __init__(self, grid: str):
        super().__init__(grid)
        self.grid = grid


class GridUnavailable(Exception):
    def __init__(self, grid: str, transformation: str):
        super().__init__(grid)
        self.grid = grid
        self.transformation = transformation


_lock = threading.RLock()
_geodetic: dict[tuple, Transformer] = {}
_projections: dict[str, tuple[Transformer, Proj, bool]] = {}
_datum: dict[tuple, Transformer] = {}
_grid_dirs: list[str] = []


def add_grid_directory(path: Path) -> None:
    """Donde están las rejillas NTv2 descargadas (`<id>.gsb`): se añade a la ruta de búsqueda de PROJ."""
    import pyproj.datadir
    text = str(Path(path).resolve())
    with _lock:
        if text not in _grid_dirs:
            pyproj.datadir.append_data_dir(text)
            _grid_dirs.append(text)
            _datum.clear()


def _find_grid(name: str) -> bool:
    return any((Path(d) / name).is_file() for d in _grid_dirs)


# --- Geocéntricas ----------------------------------------------------------------------------------------------------

def geodetic(x: float, y: float, z: float, ellipsoid: Ellipsoid) -> tuple[float, float, float]:
    """ECEF → latitud, longitud (grados) y altura elipsoidal sobre el elipsoide dado."""
    key = (ellipsoid.a, ellipsoid.rf)
    with _lock:
        t = _geodetic.get(key)
        if t is None:
            t = Transformer.from_pipeline(
                f"+proj=pipeline +step +inv +proj=cart {ellipsoid.proj} "
                "+step +proj=unitconvert +xy_in=rad +xy_out=deg")
            _geodetic[key] = t
        lon, lat, h = t.transform(x, y, z)
    return lat, lon, h


def geocentric(lat: float, lon: float, h: float, ellipsoid: Ellipsoid) -> tuple[float, float, float]:
    key = (ellipsoid.a, ellipsoid.rf)
    with _lock:
        t = _geodetic.get(key)
        if t is None:
            geodetic(0.0, 0.0, 6_400_000.0, ellipsoid)
            t = _geodetic[key]
        x, y, z = t.transform(lon, lat, h, direction=TransformDirection.INVERSE)
    return x, y, z


# --- Proyección ------------------------------------------------------------------------------------------------------

def _projection_string(d: Definition) -> str:
    p = d.projection or {}
    m = p.get("method")
    common = f"+x_0={p['falseEastingMeters']!r} +y_0={p['falseNorthingMeters']!r} {d.ellipsoid.proj} +units=m +no_defs"
    if m == "transverseMercator":
        return (f"+proj=tmerc +lat_0={p['latitudeOfOriginDegrees']!r} +lon_0={p['centralMeridianDegrees']!r} "
                f"+k_0={p['scaleFactor']!r} {common}")
    if m == "lambertConicConformal1SP":
        lat0 = p["latitudeOfOriginDegrees"]
        return (f"+proj=lcc +lat_1={lat0!r} +lat_0={lat0!r} +lon_0={p['centralMeridianDegrees']!r} "
                f"+k_0={p['scaleFactor']!r} {common}")
    if m == "lambertConicConformal2SP":
        return (f"+proj=lcc +lat_0={p['latitudeOfFalseOriginDegrees']!r} "
                f"+lon_0={p['longitudeOfFalseOriginDegrees']!r} +lat_1={p['firstStandardParallelDegrees']!r} "
                f"+lat_2={p['secondStandardParallelDegrees']!r} {common}")
    raise ValueError(f"proyección {m}")


def _check_points(d: Definition) -> list[tuple[float, float]]:
    a = d.area
    lat_c, lon_c = a.center
    pts = [(lat_c, lon_c)]
    for f in (0.2, 0.8):
        for g in (0.2, 0.8):
            pts.append((a.south + f * (a.north - a.south), a.west + g * (a.east - a.west)))
    return pts


def _projection(d: Definition) -> tuple[Transformer, Proj, bool]:
    """(transformador geográficas del datum → plano, Proj para los factores, si es el del registro EPSG)."""
    with _lock:
        cached = _projections.get(d.code)
        if cached is not None:
            return cached
        own_crs = CRS.from_proj4(_projection_string(d))
        own = Transformer.from_crs(own_crs.geodetic_crs, own_crs, always_xy=True)
        chosen: tuple[Transformer, Proj, bool] = (own, Proj(own_crs), False)
        if d.code.upper().startswith("EPSG:"):
            try:
                epsg_crs = CRS.from_user_input(d.code)
                epsg = Transformer.from_crs(epsg_crs.geodetic_crs, epsg_crs, always_xy=True)
                same = epsg_crs.is_projected
                for lat, lon in _check_points(d) if same else []:
                    e1, n1 = own.transform(lon, lat)
                    e2, n2 = epsg.transform(lon, lat)
                    if not (math.isfinite(e2) and math.isfinite(n2)
                            and abs(e1 - e2) <= EPSG_CHECK_TOLERANCE_M and abs(n1 - n2) <= EPSG_CHECK_TOLERANCE_M):
                        same = False
                        break
                if same:
                    chosen = (epsg, Proj(epsg_crs), True)
            except (CRSError, ProjError):
                pass
        _projections[d.code] = chosen
        return chosen


def uses_epsg_registry(d: Definition) -> bool:
    """Si la proyección sale del registro EPSG de PROJ (True) o de los parámetros de la definición (False)."""
    return d.is_projected and _projection(d)[2]


def project(d: Definition, lat: float, lon: float) -> tuple[float, float, float, float]:
    """Geográficas del datum del sistema → (este, norte, convergencia en grados, factor de escala)."""
    transformer, proj, _ = _projection(d)
    with _lock:
        e, n = transformer.transform(lon, lat)
        f = proj.get_factors(lon, lat)
    if not (math.isfinite(e) and math.isfinite(n)):
        raise ValueError("fuera del dominio de la proyección")
    return e, n, f.meridian_convergence, f.meridional_scale


def unproject(d: Definition, e: float, n: float) -> tuple[float, float]:
    transformer, _, _ = _projection(d)
    with _lock:
        lon, lat = transformer.transform(e, n, direction=TransformDirection.INVERSE)
    return lat, lon


# --- Datum heredado --------------------------------------------------------------------------------------------------

def _grid_name(t: Transformation) -> str:
    for name in ([f"{t.grid_id}.gsb", t.grid_id] if t.grid_id else []) + ([t.grid_file] if t.grid_file else []):
        if name and _find_grid(name):
            return name
    raise GridUnavailable(t.grid_file or t.grid_id or "(sin nombre)", t.code)


def _datum_pipeline(t: Transformation, source: Ellipsoid) -> Transformer:
    grid = _grid_name(t) if t.method == "ntv2" else ""
    key = (t.key, source.a, source.rf, grid)
    with _lock:
        cached = _datum.get(key)
        if cached is not None:
            return cached
        if t.method == "ntv2":
            core = f"+step +proj=hgridshift +grids={grid}"
        else:
            src = t.cartesian_source_ellipsoid or source
            x, y, z = t.translation_m
            if t.method == "geocentricTranslation":
                op = f"+proj=helmert +x={x!r} +y={y!r} +z={z!r}"
            else:
                rx, ry, rz = t.rotation_arcsec
                rot = f"+x={x!r} +y={y!r} +z={z!r} +rx={rx!r} +ry={ry!r} +rz={rz!r} +s={t.scale_ppm!r}"
                if t.method == "molodenskyBadekas":
                    px, py, pz = t.evaluation_point_m or (0.0, 0.0, 0.0)
                    op = f"+proj=molobadekas {rot} +px={px!r} +py={py!r} +pz={pz!r} +convention=coordinate_frame"
                else:
                    conv = "position_vector" if t.method == "positionVector" else "coordinate_frame"
                    op = f"+proj=helmert {rot} +convention={conv}"
            core = f"+step +proj=cart {src.proj} +step {op} +step +inv +proj=cart {t.target_ellipsoid.proj}"
        pipeline = (f"+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad {core} "
                    "+step +proj=unitconvert +xy_in=rad +xy_out=deg")
        transformer = Transformer.from_pipeline(pipeline)
        _datum[key] = transformer
        return transformer


def datum_forward(t: Transformation, lat: float, lon: float, source: Ellipsoid) -> tuple[float, float]:
    """Del datum heredado (altura cero) al marco de llegada: la directa oficial."""
    transformer = _datum_pipeline(t, source)
    with _lock:
        out = transformer.transform(lon, lat, 0.0)
    lon2, lat2 = out[0], out[1]
    if not (math.isfinite(lat2) and math.isfinite(lon2)):
        raise OutsideGrid(t.grid_file or t.grid_id or t.code)
    return lat2, lon2


def datum_inverse(t: Transformation, lat: float, lon: float, source: Ellipsoid) -> tuple[float, float]:
    """Del marco de llegada al datum heredado: la inversa exacta de `datum_forward`, por iteración."""
    transformer = _datum_pipeline(t, source)
    with _lock:
        out = transformer.transform(lon, lat, 0.0, direction=TransformDirection.INVERSE)
    est_lon, est_lat = out[0], out[1]
    if not (math.isfinite(est_lat) and math.isfinite(est_lon)):
        raise OutsideGrid(t.grid_file or t.grid_id or t.code)
    if t.method == "ntv2":   # la inversa de hgridshift ya es iterativa en PROJ
        return est_lat, est_lon
    for _ in range(INVERSE_ITERATIONS):
        f_lat, f_lon = datum_forward(t, est_lat, est_lon, source)
        d_lat = lat - f_lat
        d_lon = math.remainder(lon - f_lon, 360.0)
        est_lat += d_lat
        est_lon += d_lon
        if abs(d_lat) < INVERSE_TOLERANCE_DEG and abs(d_lon) < INVERSE_TOLERANCE_DEG:
            break
    return est_lat, est_lon


def datum_point(d: Definition, lat: float, lon: float) -> tuple[float, float, Transformation | None, bool]:
    """Del punto en el marco de la base al datum del sistema (`datumPoint(ofReceiver:)` de las apps).

    Prueba las regiones en orden; una rejilla que no cubre el punto cede a la siguiente. Una rejilla que **falta** no:
    se dice (`GridUnavailable`), nunca se cae a otra transformación según lo que haya en este equipo.
    """
    candidates = d.candidates(lat, lon)
    if not candidates:
        return lat, lon, None, True
    last: Exception = OutsideGrid("")
    for t, inside in candidates:
        try:
            dlat, dlon = datum_inverse(t, lat, lon, d.ellipsoid)
            return dlat, dlon, t, inside
        except OutsideGrid as error:
            last = error
    raise last


def receiver_point(d: Definition, lat: float, lon: float) -> tuple[float, float, Transformation | None, bool]:
    """Del datum del sistema al marco de la base (`receiverPoint(ofDatum:)`): la región se elige con el punto del
    datum y se comprueba con el de llegada, para que ida y vuelta usen la misma cerca de un borde."""
    candidates = d.candidates(lat, lon)
    if not candidates:
        return lat, lon, None, True

    def first(cands):
        last: Exception = OutsideGrid("")
        for t, inside in cands:
            try:
                return (*datum_forward(t, lat, lon, d.ellipsoid), t, inside)
            except OutsideGrid as error:
                last = error
        raise last

    r_lat, r_lon, t1, inside1 = first(candidates)
    second = d.candidates(r_lat, r_lon)
    if not second or second[0][0] == t1:
        return r_lat, r_lon, t1, (second[0][1] if second else False)
    return first(second)
