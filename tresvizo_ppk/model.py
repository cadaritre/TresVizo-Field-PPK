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
    """Estación de referencia: de una red CORS (catálogo oficial de su paquete, §3.0) o una base propia con su
    coordenada. El marco y la época de la coordenada van en `position` (los de la red, o los que escribió el usuario)."""
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
    # Añadido por el motor (redes CORS, §3.0):
    network: str = ""                        # id del paquete de su red («rgna-inegi»); vacío en una base propia
    network_name: str = ""                   # «RGNA — INEGI, México», para el informe
    attribution: str = ""                    # atribución que pide la licencia de la red («Fuente: INEGI, …»)
    # De dónde sale la coordenada (base propia sin pedirla a mano): «official» (catálogo de su red), «rinex-header»
    # (APPROX POSITION XYZ), «autonomous» (media de la solución autónoma de la sesión), «project» (encadenada de un
    # estático) o «manual». `approximate`: la coordenada es aproximada (encabezado o autónoma): los puntos quedan
    # bien entre sí pero desplazados lo mismo que la base, y el informe lo dice.
    coordinate_origin: str = "official"
    approximate: bool = False


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
    antenna_pco_measured: bool | None = None  # PCO de la antena medido (True), sin medir (False) o no se sabe (None)
    # Añadido por el motor:
    radome: str = ""                         # radomo de la antena (ANTEX), «NONE» si no lleva
    approx_position: Ecef | None = None      # APPROX POSITION XYZ del RINEX (decenas de metros)
    receiver_model: str = ""                 # «MeridianV» / «MeridianVI» si es una grabación de esos receptores
    case_offset_m: float | None = None       # base del receptor → ARP de los Meridian (0.116 / 0.152 m)
    epochs: int = 0
    warnings: list[str] = field(default_factory=list)          # de la conversión, la unión y el recorte
    # Lo que traen los archivos, para llenar la interfaz (editable) con su origen:
    point_name: str = ""                     # nombre del punto: el de la sesión del receptor o MARKER NAME
    pole_height_m: float | None = None       # altura del bastón (marca → base del receptor) de la sesión, si la hay
    field_sources: dict[str, str] = field(default_factory=dict)  # campo → «encabezado RINEX», «catálogo …», «sesión del receptor»
    approx_position_source: str = ""         # de dónde sale approx_position
    frame_declared: str = ""                 # marco que declaró el receptor (base con coordenada), si lo grabó
    frame_epoch_declared: float | None = None


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


# --- Antenas: la que eligió el usuario y la que se usó ----------------------------------------------------------------

# Método con que se midió la altura de la antena (especificación §1.1.1):
#   «arp»    vertical de la marca a la ARP (la del RINEX);
#   «mount»  vertical de la marca a la rosca o al gancho; la ARP queda `mount_offset_m` arriba (el desplazamiento del
#            case en los Meridian, 0 en las antenas cuya ARP es la base de la rosca);
#   «slant»  inclinada, de la marca al borde de la antena: h_ARP = √(s² − r²) − d, con r el radio horizontal del eje
#            al punto del borde y d lo que ese punto está arriba de la ARP.
HEIGHT_METHODS = ("arp", "mount", "slant")


@dataclass
class AntennaSelection:
    """Antena, radomo y altura que eligió el usuario para un extremo («rover» o «base:<clave>»). Lo vacío o None
    deja lo que traen los datos (encabezado RINEX, producto Meridian o catálogo de la red)."""
    antenna_type: str = ""                     # tipo IGS («CHCI80»); vacío: el de los datos
    radome: str = ""                           # «NONE», «SCIT»…; vacío: el de los datos
    height_method: str = "arp"                 # uno de HEIGHT_METHODS
    measured_m: float | None = None            # lo medido con ese método; None: la altura de los datos
    mount_offset_m: float | None = None        # «mount»: rosca o gancho → ARP
    slant_radius_m: float | None = None        # «slant»: r
    slant_offset_m: float | None = None        # «slant»: d (positivo si el borde está arriba de la ARP)
    dimensions_source: str = ""                # de dónde salen r y d (tabla con su fuente, o «escritos por el usuario»)


@dataclass
class AntennaInfo:
    """La antena que se usó de verdad en un extremo, para el informe y los avisos."""
    role: str                                  # «Rover», «Base INEG»
    antenna_type: str = ""
    radome: str = "NONE"
    model_type: str = ""                       # el modelo del ANTEX que se aplicó (vacío: sin modelo)
    model_radome: str = ""                     # su radomo («NONE» si se tomó el tipo sin radomo)
    source: str = ""                           # «IGS (igs20_2437)», «NGS (ngs20.atx)», «importado (lab.atx)», «red …»
    calibrated: bool = False                   # hubo modelo de centro de fase (PCO/PCV)
    height_m: float | None = None              # vertical a la ARP usada
    height_text: str = ""                      # cómo salió la altura («inclinada 1.523 m, r 0.120, d 0.036»)
    level: str = "ok"                          # «ok», «warning» (sin modelo, línea corta) o «error» (sin modelo, > 5 km)
    note: str = ""


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
    # Añadido por las antenas (§1.1.1): ANTEX importados (calibración individual de laboratorio, del NGS…), que
    # ganan sobre el incluido, y la antena y la altura elegidas por extremo («rover», «base:<clave>»).
    antex_files: list[Path] = field(default_factory=list)
    antennas: dict[str, AntennaSelection] = field(default_factory=dict)


# --- Resultados: estático --------------------------------------------------------------------------------------------

class Solution(str, Enum):
    FIXED = "fixed"
    FLOAT = "float"
    SINGLE = "single"
    NONE = "none"


@dataclass
class AmbiguityResolution:
    """Resolución de ambigüedades del procesador propio de líneas largas (ancho y estrecho de carril con LAMBDA)."""
    fixed: bool                                # se aceptó la solución fija
    reason: str = ""                           # por qué quedó flotante (vacío si fijó)
    double_differences: int = 0                # ambigüedades de doble diferencia (arcos sin salto, fuera de las de
                                               # referencia de cada grupo)
    widelane_fixed: int = 0                    # con el ancho de carril fijo
    narrowlane_candidates: int = 0             # las que entran a LAMBDA (ancho de carril fijo)
    narrowlane_fixed: int = 0                  # combinaciones enteras fijadas (resolución parcial)
    ratio: float | None = None                 # prueba de razón del subconjunto fijado
    min_ratio: float = 0.0
    success_rate: float | None = None          # tasa de éxito del bootstrapping del subconjunto fijado
    min_success_rate: float = 0.0
    min_session_h: float = 0.0                 # duración mínima para fijar a esta distancia (validación)
    sigma0: float = 0.0                        # σ0 a posteriori del ajuste (fijo, o flotante si no fijó)
    rms_phase_m: float = 0.0                   # RMS de los residuos de fase (libre de ionosfera, diferencia simple)
    troposphere_interval_h: float = 0.0        # tramo del retardo húmedo cenital estimado
    gradient_interval_h: float = 0.0           # tramo de los gradientes troposféricos
    signal_groups: list[str] = field(default_factory=list)    # «GPS L1/L2 (C/A, P(Y)): G01, G03…»
    excluded: list[str] = field(default_factory=list)         # sistemas o satélites fuera y por qué
    float_position: Ecef | None = None         # la flotante, para comparar
    float_sigma_enu: Enu | None = None
    observations: int = 0
    rejected: int = 0


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
    # Añadido por el motor:
    fix_rate: float = 0.0                      # épocas fijas / épocas con solución
    strategy: str = ""                         # «L1+L2, ionosfera estimada, ambigüedades fijas»…
    config_file: Path | None = None            # configuración de RTKLIB usada
    formal_sigma_enu: Enu | None = None        # σ formal de RTKLIB (antes del modelo de error)
    antenna_notes: list[str] = field(default_factory=list)   # qué calibración de antena se usó en cada extremo
    # Añadido por el motor (procesador propio de líneas largas):
    engine: str = "RTKLIB"                     # «RTKLIB» o «TresVizo» (procesador propio, líneas largas)
    ambiguity: AmbiguityResolution | None = None   # resolución de ambigüedades del procesador propio


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
    # Añadido por el motor:
    standardized_residuals: dict[str, Enu] = field(default_factory=dict)   # prueba w de Baarda por componente
    critical_value: float = 0.0                # valor crítico de la prueba w
    chi2_lower: float = 0.0                    # límites de la prueba chi² al 95 %
    chi2_upper: float = 0.0
    excluded_baselines: list[str] = field(default_factory=list)   # líneas que se dejaron fuera por no cuadrar


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
    # Añadido por el motor:
    base_quality: dict[str, DataQuality] = field(default_factory=dict)   # id de la base → calidad de sus datos
    ephemeris: Ephemeris = Ephemeris.BROADCAST
    precise_files: list[str] = field(default_factory=list)              # nombres de los SP3 usados
    rtklib_version: str = ""
    antennas: list[AntennaInfo] = field(default_factory=list)           # rover y cada base: modelo y fuente


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
    # Añadido por el motor (columnas del CSV; antenna_height_m es la «Altura de antena»: vertical del jalón):
    antenna_offset_m: float | None = None      # «Antenna offset»: base del receptor → ARP (case)
    antenna_pco_m: float = 0.0                 # «Antenna PCO»: ARP → centro de fase (0 sin calibrar)
    total_height_m: float | None = None        # «Total antenna height»: marca → ARP (jalón + case)


@dataclass
class ProcessedPoint:
    point: FieldPoint
    solution: Solution
    position: Ecef | None                      # el punto en el suelo (antena − altura vertical: todo plomeado)
    sigma_enu: Enu | None
    epochs_fixed: int
    epochs_total: int
    distance_to_base_m: float | None
    delta_from_field_enu: Enu | None           # posproceso − campo, en ENU del punto de campo (m)
    message: str = ""                          # por qué no se posprocesó o qué avisar
    # Añadido por el motor:
    antenna_position: Ecef | None = None       # la antena (ARP) promediada, antes de bajar la altura
    height_used_m: float | None = None         # altura vertical restada (marca → ARP)
    epochs_float: int = 0


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
    # Añadido por el motor:
    base_quality: DataQuality | None = None
    stat_file: Path | None = None
    config_file: Path | None = None
    ephemeris: Ephemeris = Ephemeris.BROADCAST
    precise_files: list[str] = field(default_factory=list)
    rtklib_version: str = ""
    trajectory: list[tuple[datetime, Ecef, Solution]] = field(default_factory=list)   # para la gráfica
    antennas: list[AntennaInfo] = field(default_factory=list)          # rover y base: modelo y fuente


# --- Localización de proyecto (calibración local) --------------------------------------------------------------------
#
# Lo mismo que el registro de las apps TresVizo Field (`docs/localizacion.md` §5 de las apps, que viaja en su respaldo
# ZIP y en el `.mvz`): **sus pares y sus opciones**. Los parámetros no se guardan: se recalculan siempre de aquí
# (`crs.localizacion`), con el geoide que tenga el sistema elegido ahora. Lo que no se sabe es None, nunca 0.

@dataclass
class LocalizationControl:
    """La coordenada local conocida de la marca. Norte y este None en un banco de nivel; la cota, en un vértice sin
    cota."""
    northing_m: float | None = None
    easting_m: float | None = None
    elevation_m: float | None = None


@dataclass
class LocalizationMeasured:
    """Lo medido sobre la marca, como una foto al crear el par: latitud, longitud y altura elipsoidal del suelo en el
    marco de la base (el «receptor» de las apps). `ellipsoidal_height_m` None: el par no puede usarse en cota."""
    name: str = ""
    latitude_deg: float = 0.0
    longitude_deg: float = 0.0
    ellipsoidal_height_m: float | None = None
    point_id: str | None = None                # el punto de origen en las apps (UUID), si lo hay
    source: str = ""                           # en la oficina: «PPK», «estático», «CSV de TresVizo Field»…


@dataclass
class LocalizationPair:
    name: str                                  # el nombre de la marca, el de los residuos («C1»)
    control: LocalizationControl = field(default_factory=LocalizationControl)
    measured: LocalizationMeasured = field(default_factory=LocalizationMeasured)
    uses_horizontal: bool = True               # entra al ajuste en planta; si no, es una comprobación
    uses_vertical: bool = True
    id: str = ""                               # UUID del par


@dataclass
class Localization:
    """Una localización: pares y opciones. Métodos de las apps: planta «similarity», «rigid» o «translation»; cota
    «automatic», «constant» o «none». La cuadrícula base es la proyección del sistema por su código («projectCRS») o,
    en un sistema geográfico, una transversa de Mercator local («localTransverseMercator», con su meridiano central y
    su elipsoide); `height_base_*` declara con qué alturas se aplicó (manda el geoide de ahora)."""
    pairs: list[LocalizationPair] = field(default_factory=list)
    horizontal_method: str = "similarity"
    vertical_method: str = "automatic"
    horizontal_tolerance_m: float = 0.020
    vertical_tolerance_m: float = 0.040
    base_grid_kind: str = "projectCRS"         # «projectCRS» o «localTransverseMercator»
    base_grid_crs_code: str = ""               # «EPSG:6368»
    base_grid_central_meridian_deg: float | None = None
    base_grid_ellipsoid: str = ""              # «WGS 84», «GRS 1980»
    height_base_kind: str = "ellipsoidal"      # «ellipsoidal» u «orthometric»
    height_base_geoid_id: str = ""
    id: str = ""                               # UUID
    project_id: str = ""                       # UUID del proyecto de las apps (o uno propio de la oficina)
    applied_at: datetime | None = None
    removed_at: datetime | None = None         # None mientras está en uso
    saved_system_id: str = ""                  # el de «Mis sistemas» de las apps, si salió de uno
    saved_system_name: str = ""
    origin: str = ""                           # de dónde salió: «calculada en la oficina», «importada de …»


# --- Sistemas de coordenadas, usuario y proyecto ---------------------------------------------------------------------

@dataclass
class CoordinateChoice:
    crs_id: str = "mexico-itrf2008-utm"        # id del paquete o sistema del catálogo de TresVizo Field
    zone: str = ""                             # zona o variante del paquete, si aplica
    geoid_id: str | None = "ggm10"
    label: str = ""                            # cómo se llama en el informe
    # Añadido por la localización: la que está en uso. Con ella, norte, este y cota del resultado, del informe y de la
    # exportación son los locales (`crs.api.transform`). Y las que se quitaron (con `removed_at`), de la más antigua a
    # la más reciente, como el historial de las apps: van con la elección del sistema para que el proyecto las guarde
    # sin más (el sistema del proyecto y su historial son una sola cosa).
    localization: Localization | None = None
    localization_history: list[Localization] = field(default_factory=list)


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
    # Añadido por la localización. Con una en uso, `northing_m`/`easting_m` son los **locales**,
    # `orthometric_height_m` la cota local (`height_kind` «local»; None si no se sabe), `crs_code` es «LOCAL» y
    # `label` el rótulo de las apps («Local · sobre UTM zona 13N»); la convergencia y el factor de escala, que son de
    # la cuadrícula, van en None. Lo de la cuadrícula base queda aquí:
    localized: bool = False
    base_crs_code: str = ""                    # código de la cuadrícula base («EPSG:32613»); vacío en la transversa local
    grid_northing_m: float | None = None       # en la cuadrícula base
    grid_easting_m: float | None = None
    base_height_m: float | None = None         # la altura base (elipsoidal u ortométrica) a la que se suma el plano


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
    number: str = ""                           # número o clave del proyecto que da el usuario (sale en el informe)
    place: str = ""                            # lugar de la firma del informe («Aguascalientes, Ags.»); vacío: a mano
    folder: Path | None = None
    inputs: dict[str, list[str]] = field(default_factory=dict)   # rutas relativas por papel («rover», «base:CHIH»…)
    hashes: dict[str, str] = field(default_factory=dict)         # ruta relativa → SHA-256
    options: ProcessingOptions = field(default_factory=ProcessingOptions)
    coordinates: CoordinateChoice = field(default_factory=CoordinateChoice)
    pole_height_m: float = 0.0
    point_name: str = ""
    point_code: str = ""                       # estático: código del vértice (capa del DXF, «Code» del CSV)
    point_description: str = ""                # estático: descripción del vértice («Description», `desc` del LandXML)
    created: datetime | None = None
    processed: datetime | None = None
    # Añadido por la interfaz (proyectos .tvppk, §1.4):
    bases: list[Station] = field(default_factory=list)   # estaciones elegidas con su coordenada (editada o no)
    base_kind: str = "rgna"                    # PPK de puntos: «rgna» o «own» (base propia)
    source_project: str = ""                   # base propia encadenada: ruta relativa del proyecto estático
    position: Ecef | None = None               # estático: coordenada final del último proceso (para encadenar la base)
    sigma_enu: Enu | None = None               # y su σ
