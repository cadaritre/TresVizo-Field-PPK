"""Contrato común entre el motor (core), los sistemas de coordenadas (crs), el informe (report), la exportación
(export) y la interfaz (app). Solo datos: nada de cálculo ni de interfaz aquí.

Se amplía, no se rompe: un campo nuevo lleva valor por omisión; cambiar o quitar uno obliga a actualizar a todos sus
usuarios en el mismo commit (AGENTS.md).

Unidades: metros, segundos, grados decimales. Tiempos en UTC con zona (`datetime` con tzinfo=UTC).
Coordenadas cartesianas geocéntricas (ECEF) en el marco y época que digan sus campos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


# --- Coordenadas y marco ---------------------------------------------------------------------------------------------

OFFICIAL_FRAME = "ITRF2008"     # marco de la RGNA del INEGI
OFFICIAL_EPOCH = 2010.0         # época de las coordenadas oficiales de la RGNA


@dataclass(frozen=True)
class Ecef:
    x: float
    y: float
    z: float
    frame: str = OFFICIAL_FRAME
    epoch: float = OFFICIAL_EPOCH


@dataclass(frozen=True)
class Enu:
    e: float
    n: float
    u: float


Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


# --- Estaciones y observaciones --------------------------------------------------------------------------------------

@dataclass
class Station:
    """Estación de referencia: de la RGNA (catálogo oficial) o una base propia con su coordenada."""
    id: str                                  # clave (p. ej. «INEG», «CHIH»)
    name: str                                # nombre editable que sale en el informe
    position: Ecef                           # coordenada fija de la base
    sigma_enu: Enu | None = None             # σ de la coordenada oficial, si la publica la fuente
    antenna_type: str = ""                   # tipo IGS (ANTEX), p. ej. «TRM59800.00»
    radome: str = "NONE"
    antenna_height_m: float = 0.0            # altura vertical a la ARP
    receiver: str = ""
    source: str = ""                         # de dónde salió la coordenada (fuente y fecha) o «manual»
    official: bool = True                    # False si el usuario la corrigió o es una base propia


@dataclass
class ObservationSet:
    """Archivos RINEX listos para procesar (ya descomprimidos, unidos y recortados)."""
    obs: list[Path]
    nav: list[Path] = field(default_factory=list)
    marker: str = ""
    receiver: str = ""
    antenna_type: str = ""
    antenna_height_m: float = 0.0            # vertical a la ARP, la que va al RINEX
    start: datetime | None = None
    end: datetime | None = None
    interval_s: float | None = None
    rinex_version: str = ""
    source_files: list[Path] = field(default_factory=list)   # lo que entregó el usuario, tal cual


@dataclass
class DataQuality:
    """Control de calidad de un juego de observaciones (tipo TEQC/Anubis)."""
    epochs: int = 0
    expected_epochs: int = 0
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    satellites: list[str] = field(default_factory=list)           # «G05», «E11», …
    signals: dict[str, list[str]] = field(default_factory=dict)   # sistema → señales («GPS» → [«L1C», «L2W»])
    cycle_slips: dict[str, int] = field(default_factory=dict)     # satélite → saltos
    multipath_m: dict[str, float] = field(default_factory=dict)   # señal → MP RMS
    warnings: list[str] = field(default_factory=list)


# --- Opciones de proceso ---------------------------------------------------------------------------------------------

class Ephemeris(str, Enum):
    BROADCAST = "broadcast"
    PRECISE = "precise"


@dataclass
class ProcessingOptions:
    elevation_mask_deg: float = 15.0
    constellations: tuple[str, ...] = ("GPS", "GLONASS", "Galileo", "BeiDou", "QZSS")
    frequencies: int = 2                       # 1, 2 o 3
    ionosphere: str = "auto"                   # «auto» elige libre de ionosfera para líneas largas
    ephemeris: Ephemeris = Ephemeris.BROADCAST
    precise_files: list[Path] = field(default_factory=list)   # SP3/CLK si ephemeris es PRECISE
    ambiguity_mode: str = "fix-and-hold"
    combined: bool = True                      # adelante y atrás
    min_ratio: float = 3.0
    interval_s: float | None = None            # None: el de los datos


# --- Resultados: estático --------------------------------------------------------------------------------------------

class Solution(str, Enum):
    FIXED = "fixed"
    FLOAT = "float"
    SINGLE = "single"
    NONE = "none"


@dataclass
class BaselineSolution:
    base: Station
    rover_marker: str
    start: datetime
    end: datetime
    epochs_used: int
    satellites: list[str]
    solution: Solution
    ratio: float | None
    rms_m: float | None
    vector_ecef: Ecef                          # rover − base, en el marco de la base
    vector_enu: Enu                            # en la base
    length_m: float
    azimuth_deg: float
    zenith_deg: float
    covariance_ecef: Matrix3                   # m², del vector
    rover_position: Ecef
    ephemeris: Ephemeris
    pos_file: Path | None = None
    stat_file: Path | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class AdjustmentResult:
    position: Ecef
    covariance_ecef: Matrix3
    sigma_enu: Enu
    residuals_enu: dict[str, Enu]              # id de la base → residuo de su línea base
    sigma0: float
    chi2: float
    degrees_of_freedom: int
    passed: bool                               # prueba chi² al 95 %
    flagged_baselines: list[str] = field(default_factory=list)


@dataclass
class StaticResult:
    point_name: str
    rover: ObservationSet
    rover_quality: DataQuality
    bases: list[Station]
    baselines: list[BaselineSolution]
    adjustment: AdjustmentResult | None        # None con una sola base
    position: Ecef                             # la final, en el marco oficial
    sigma_enu: Enu
    log: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- Resultados: PPK de puntos ---------------------------------------------------------------------------------------

@dataclass
class FieldPoint:
    """Un punto del CSV de TresVizo Field."""
    name: str
    code: str
    start: datetime                            # inicio de la ocupación (UTC del receptor)
    end: datetime
    antenna_height_m: float
    field_position: Ecef | None = None         # lo que se midió en campo
    field_solution: str = ""                   # «FIJO», «FLOTANTE», «AUTÓNOMO»…
    row: dict[str, str] = field(default_factory=dict)   # la fila original, para reexportarla


@dataclass
class ProcessedPoint:
    point: FieldPoint
    solution: Solution
    position: Ecef | None                      # el punto en el suelo (antena − altura vertical: todo plomeado)
    sigma_enu: Enu | None
    epochs_fixed: int
    epochs_total: int
    distance_to_base_m: float | None
    delta_from_field_enu: Enu | None
    message: str = ""                          # por qué no se posprocesó o qué avisar


@dataclass
class PointsResult:
    base: Station
    rover: ObservationSet
    rover_quality: DataQuality
    points: list[ProcessedPoint]
    pos_file: Path | None = None
    fix_rate: float = 0.0                      # épocas fijas / épocas con solución
    log: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- Sistemas de coordenadas, usuario y proyecto ---------------------------------------------------------------------

@dataclass
class CoordinateChoice:
    crs_id: str = "mexico-itrf2008-utm"        # id del paquete o sistema del catálogo de TresVizo Field
    zone: str = ""                             # zona o variante del paquete, si aplica
    geoid_id: str | None = "ggm10"
    label: str = ""                            # cómo se llama en el informe


@dataclass(frozen=True)
class TransformedCoordinate:
    """Una posición del marco oficial expresada en el sistema, datum y geoide elegidos (`crs.api.transform`).

    Latitud y longitud son las del **datum del sistema** (el heredado, si lo es); `receiver_*` son las del marco de la
    base, que es en el que están de verdad las alturas (las alturas no se transforman entre datums). Lo que no aplica
    va en None: norte y este en un sistema geográfico, la cota sin geoide o fuera de su cobertura.
    """
    latitude_deg: float
    longitude_deg: float
    ellipsoidal_height_m: float                # sobre el elipsoide del marco de la base
    northing_m: float | None = None
    easting_m: float | None = None
    orthometric_height_m: float | None = None  # H = h − N (ortométrica o normal, según `height_kind`)
    undulation_m: float | None = None          # N del geoide (o anomalía de altura en un cuasigeoide)
    zone: str = ""                             # «14N», si el sistema es una zona
    convergence_deg: float | None = None       # convergencia de meridianos (cuadrícula − geodésico)
    scale_factor: float | None = None          # factor de escala de la proyección en el punto
    receiver_latitude_deg: float = 0.0         # en el marco de la base
    receiver_longitude_deg: float = 0.0
    crs_code: str = ""                         # «EPSG:6369»
    crs_name: str = ""                         # «Mexico ITRF2008 / UTM zona 14N»
    frame_text: str = ""                       # «ITRF2008, época 2010.0»
    axis_order: str = "E,N"                    # «E,N» en proyectadas, «N,E» en geográficas
    geoid_id: str | None = None
    geoid_name: str = ""                       # cita del modelo («GGM10 2010 · INEGI · …»)
    height_kind: str = ""                      # «ortométrica» o «normal»; vacío sin cota
    geoid_uncertainty_m: float | None = None   # la del modelo, no la del GNSS
    transformation: str = ""                   # transformación de datum usada (nombre y código), si hubo
    transformation_accuracy_m: float | None = None
    inside_transformation_area: bool = True
    label: str = ""                            # «Mexico ITRF2008 / UTM zona 14N + GGM10 height» (CS name del CSV)
    attribution: tuple[str, ...] = ()          # créditos y avisos que exige la licencia del geoide
    warnings: tuple[str, ...] = ()


@dataclass
class UserProfile:
    company: str = ""
    surveyor: str = ""
    license_id: str = ""                       # cédula profesional
    logo: Path | None = None


@dataclass
class Project:
    version: int = 1
    kind: str = "static"                       # «static» o «points»
    name: str = ""
    folder: Path | None = None
    inputs: dict[str, list[str]] = field(default_factory=dict)   # rutas relativas por papel («rover», «base:CHIH»…)
    hashes: dict[str, str] = field(default_factory=dict)         # ruta relativa → SHA-256
    options: ProcessingOptions = field(default_factory=ProcessingOptions)
    coordinates: CoordinateChoice = field(default_factory=CoordinateChoice)
    pole_height_m: float = 0.0
    point_name: str = ""
    created: datetime | None = None
    processed: datetime | None = None
