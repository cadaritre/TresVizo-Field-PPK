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
    # Añadido por el editor de sesión (línea de tiempo por satélite, en UTC): los tramos con datos de cada satélite
    # (inicio, fin; un hueco de más de tres intervalos corta el tramo) y el momento de cada salto de ciclo
    tracks: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    slip_times: dict[str, list[datetime]] = field(default_factory=dict)
    interval_s: float | None = None          # intervalo de registro (el del encabezado o el mediano)


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

@dataclass(frozen=True)
class TimeWindow:
    """Ventana de tiempo que se procesa (editor de sesión, como el Session Editor de TBC), en UTC con zona. None en
    un extremo: desde el inicio o hasta el fin de los datos."""
    start: datetime | None = None
    end: datetime | None = None


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
    # Añadido por el editor de sesión: la ventana de tiempo que se procesa (None: toda la sesión común del rover y la
    # base) y lo que se deja fuera del cálculo: satélites por su PRN de RINEX («G13», «R05», «E11») o sistemas
    # completos por su letra («R», «C»). Valen para todas las líneas base y para la trayectoria del PPK.
    time_window: TimeWindow | None = None
    excluded_sats: list[str] = field(default_factory=list)


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
    # Añadido por las tolerancias: PDOP máxima de las épocas con solución (de los satélites usados, del `.stat`);
    # None si no se sabe (sin `.stat` o el procesador propio)
    pdop_max: float | None = None


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
    # Añadido por las tolerancias: la evaluación contra las del proyecto (`calidad.evaluate_static`)
    quality: QualityReport | None = None
    # Añadido por el editor de sesión: la ventana y los satélites fuera con que se calculó este resultado
    time_window: TimeWindow | None = None
    excluded_sats: list[str] = field(default_factory=list)


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
    # Añadido por las tolerancias: cociente (ratio) mediano de las épocas fijas promediadas y PDOP máxima de las
    # épocas promediadas (de los satélites usados, del `.stat`); None si no se sabe
    ratio: float | None = None
    pdop_max: float | None = None


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
    # Añadido por las tolerancias: la evaluación contra las del proyecto (`calidad.evaluate_points`). Con ella, el
    # resultado de cada nombre es el de `quality.points` (promedio de sus ocupaciones o la elegida).
    quality: QualityReport | None = None
    # Añadido por el editor de sesión: la ventana y los satélites fuera con que se calculó este resultado
    time_window: TimeWindow | None = None
    excluded_sats: list[str] = field(default_factory=list)


# --- Control de calidad: tolerancias del proyecto, puntos de control y ocupaciones repetidas -------------------------
#
# Como los criterios de Trimble Business Center y Leica Infinity: el usuario fija las tolerancias del proyecto y cada
# punto y cada línea base sale «pass» (pasa), «review» (revisar) o «fail» (falla), con la razón en una línea. Un
# criterio en None no se evalúa. Se pasa a «review» al salir de la tolerancia y a «fail» al salir más de
# `fail_factor` veces (o, en los mínimos, al quedar por debajo de la tolerancia entre `fail_factor`).

QUALITY_LEVELS = ("pass", "review", "fail")


@dataclass
class ProjectTolerances:
    """Las tolerancias del proyecto. `order` es el orden de exactitud de la NT EEP (tabla 1: «5 centímetros») del
    que salieron las cifras (`calidad.tolerances_for_order`); vacío si se escribieron a mano. σ a 1σ, como las del
    resultado. Las de omisión son las del orden de 5 cm."""
    order: str = "5 centímetros"
    max_sigma_h_m: float | None = 0.028        # σH = √(σE² + σN²)
    max_sigma_v_m: float | None = 0.051
    min_fixed_percent: float | None = 95.0     # PPK: épocas fijas de la ocupación
    min_ratio: float | None = 3.0              # cociente de la solución fija
    max_pdop: float | None = 6.0
    min_duration_s: float | None = None        # ocupación del PPK o sesión del estático
    max_repeat_h_m: float | None = 0.050       # entre ocupaciones repetidas del mismo punto
    max_repeat_v_m: float | None = 0.100
    max_control_h_m: float | None = 0.050      # contra la coordenada de control
    max_control_v_m: float | None = 0.100
    fail_factor: float = 2.0
    # Añadido por la red (`core/red.py`): tolerancia del cierre de un bucle de líneas base, en mm + ppm de la longitud
    # del bucle (la suma de sus líneas), en planta y en cota. Las de omisión, las del orden de 5 cm: L y 2L (como entre
    # ocupaciones) más 1 y 2 ppm. La NT EEP no fija cifra de cierre. None en la parte fija: no se evalúa.
    max_loop_h_m: float | None = 0.050
    max_loop_h_ppm: float = 1.0
    max_loop_v_m: float | None = 0.100
    max_loop_v_ppm: float = 2.0


@dataclass
class ControlCoordinate:
    """Una coordenada de control conocida, en el sistema del resultado (norte, este y cota, locales si hay una
    localización en uso). Norte y este None en un banco de nivel; la cota None en un vértice sin cota."""
    name: str
    northing_m: float | None = None
    easting_m: float | None = None
    elevation_m: float | None = None
    description: str = ""


@dataclass
class QualityCheck:
    """Un criterio evaluado: «sigma_h», «sigma_v», «fixed», «ratio», «pdop», «duration», «repeat_h», «repeat_v»,
    «control_h», «control_v», «solution», «adjustment» o «baseline»."""
    criterion: str
    level: str                                 # uno de QUALITY_LEVELS, o «na» si no hubo dato (no cuenta)
    value: float | None = None
    limit: float | None = None
    text: str = ""                             # «σV 0.034 > 0.020»


@dataclass
class QualityStatus:
    level: str = "pass"                        # el peor de sus criterios
    reason: str = ""                           # una línea con lo que no pasa («σV 0.034 > 0.020; PDOP 7.1 > 6.0»)
    checks: list[QualityCheck] = field(default_factory=list)


@dataclass
class ControlResidual:
    """Lo medido menos el control, en el sistema del resultado."""
    name: str
    control: ControlCoordinate
    northing_m: float | None = None            # lo medido
    easting_m: float | None = None
    elevation_m: float | None = None
    delta_e_m: float | None = None
    delta_n_m: float | None = None
    delta_h_m: float | None = None             # en la cota
    status: QualityStatus = field(default_factory=QualityStatus)
    note: str = ""                             # por qué falta una componente


@dataclass
class OccupationGroup:
    """Las ocupaciones de un mismo nombre en el CSV de campo, en su orden. `choice` 0: la media ponderada; k ≥ 1: la
    ocupación k. `deltas_enu`: cada ocupación menos la media, en ENU (None si no tiene solución)."""
    name: str
    occupations: list[ProcessedPoint] = field(default_factory=list)
    choice: int = 0
    used: list[int] = field(default_factory=list)            # índices (desde 0) que entran a la media
    deltas_enu: list[Enu | None] = field(default_factory=list)
    spread_h_m: float | None = None            # la mayor diferencia horizontal entre dos ocupaciones usadas
    spread_v_m: float | None = None
    status: QualityStatus = field(default_factory=QualityStatus)


@dataclass
class PointQuality:
    """El resultado de un nombre (el único, la media de sus ocupaciones o la elegida) con su estado."""
    name: str
    point: ProcessedPoint | None = None        # None en el estático (el vértice es el del resultado)
    status: QualityStatus = field(default_factory=QualityStatus)
    occupations: int = 1
    averaged: bool = False
    note: str = ""                             # «promedio de 2 ocupaciones», «ocupación 2 de 3 (elegida)»


@dataclass
class BaselineQuality:
    base_id: str
    status: QualityStatus = field(default_factory=QualityStatus)


@dataclass
class QualityReport:
    tolerances: ProjectTolerances
    level: str = "pass"                        # el peor de los puntos
    points: list[PointQuality] = field(default_factory=list)
    baselines: list[BaselineQuality] = field(default_factory=list)
    groups: list[OccupationGroup] = field(default_factory=list)
    control: list[ControlResidual] = field(default_factory=list)
    control_missing: list[str] = field(default_factory=list)   # control sin punto medido del mismo nombre
    control_source: str = ""
    notes: list[str] = field(default_factory=list)


# --- Red: estático de varios puntos y ajuste de red (`core/red.py`) ---------------------------------------------------
#
# Como TBC e Infinity: líneas base entre los puntos de la red (vértices nuevos y estaciones de control), primero un
# ajuste mínimamente restringido (prueba las mediciones sin que las fijas las deformen) y luego uno restringido a las
# fijas; cierres de bucle entre sesiones independientes y líneas triviales de cada sesión simultánea.

VECTOR_ROLES = ("auto", "independent", "trivial")
NETWORK_TESTS = ("tau", "w")                   # τ de Pope (como TBC) o w de Baarda
SCALE_MODES = ("auto", "never", "always", "value")
MINIMAL_DATUMS = ("point", "inner")


@dataclass
class NetworkVector:
    """Una línea base de la red: el vector de `from_id` a `to_id` (to − from, ECEF, en el marco de las fijas) con su
    covarianza completa. Las líneas de una misma `session` se midieron a la vez (receptores simultáneos); sin sesión,
    se deduce del horario."""
    id: str
    from_id: str
    to_id: str
    vector_ecef: Ecef
    covariance_ecef: Matrix3                   # m²
    session: str = ""
    start: datetime | None = None
    end: datetime | None = None
    solution: Solution | None = None
    engine: str = ""                           # «RTKLIB» o «TresVizo»
    use: bool = True                           # False: el usuario la deja fuera
    role: str = "auto"                         # uno de VECTOR_ROLES: «auto» lo decide el criterio de líneas triviales
    # Lo pone `core/red.py`:
    trivial: bool = False                      # dependiente de las demás de su sesión: fuera del ajuste y los bucles
    note: str = ""                             # por qué es trivial o quedó fuera


@dataclass
class NetworkConstraint:
    """Un punto de control: fijo duro (`sigma_enu` None) o ponderado con su σ, en planta, en cota o en las dos."""
    point_id: str
    position: Ecef
    sigma_enu: Enu | None = None
    horizontal: bool = True
    vertical: bool = True
    use: bool = True                           # False: no restringe el ajuste restringido (sigue como comprobación)


@dataclass
class NetworkOptions:
    test: str = "tau"                          # uno de NETWORK_TESTS
    alpha: float = 0.05                        # prueba χ² (de una cola) y τ (riesgo global)
    w_critical: float = 3.29                   # w de Baarda: α0 = 0.1 %, β = 20 %
    scale_covariances: str = "auto"            # SCALE_MODES: «auto» ×σ0² del mínimamente restringido si no pasa la χ²
    scale_value: float = 1.0                   # «value»: el factor que escribió el usuario (de la varianza)
    minimal_datum: str = "point"               # MINIMAL_DATUMS: un punto fijo o restricciones internas
    minimal_point: str = ""                    # «point»: el punto fijo (vacío: la primera fija de cada parte de la red)
    include_trivial: bool = False              # meter también las triviales (las σ salen optimistas)
    relative_pairs: str = "connected"          # «connected» (unidos por una línea) o «all»
    session_overlap: float = 0.5               # traslape (fracción de la más corta) para ser de la misma sesión


@dataclass
class NetworkPoint:
    """Un punto ajustado. `kind`: «free» (nuevo), «datum» (el fijo del mínimamente restringido), «control» (con
    coordenada conocida que en el mínimamente restringido no restringe), «fixed» (fijo duro) o «weighted»."""
    id: str
    position: Ecef
    covariance_ecef: Matrix3
    sigma_enu: Enu
    kind: str = "free"
    ellipse95: tuple[float, float, float] = (0.0, 0.0, 0.0)   # semieje mayor y menor (m), azimut del mayor (°)
    sigma_u95_m: float = 0.0
    # En un punto de control: ajustada − su coordenada (en el mínimamente restringido, cuánto la contradicen las
    # líneas), con su σ (la del ajuste más la de la coordenada, si la tiene)
    shift_enu: Enu | None = None
    shift_sigma_enu: Enu | None = None


@dataclass
class NetworkResidual:
    """Residuo (observado − ajustado) de una línea, en ENU de su punto de inicio, o de una fija ponderada
    (`is_constraint`, `vector_id` es el punto)."""
    vector_id: str
    residual_enu: Enu
    standardized_enu: Enu                      # w o τ por componente
    redundancy_enu: Enu                        # números de redundancia por componente
    max_standardized: float = 0.0
    flagged: bool = False
    is_constraint: bool = False


@dataclass
class RelativeEllipse:
    from_id: str
    to_id: str
    distance_m: float
    semi_major_95_m: float
    semi_minor_95_m: float
    azimuth_deg: float                         # del semieje mayor, desde el norte
    sigma_u95_m: float
    ppm_95: float                              # semieje mayor / distancia


@dataclass
class LoopClosure:
    """Un bucle de líneas independientes: la suma de sus vectores (con su sentido) debería ser cero."""
    vectors: list[str]
    directions: list[int]                      # +1 en el sentido de la línea, −1 al revés
    points: list[str]
    sessions: list[str]
    length_m: float
    misclosure_enu: Enu
    closure_h_m: float
    closure_v_m: float
    closure_3d_m: float
    ppm_h: float
    ppm_3d: float
    tolerance_h_m: float | None = None
    tolerance_v_m: float | None = None
    chi2: float = 0.0                          # cierreᵀ Σ⁻¹ cierre con las covarianzas a priori (3 gl)
    chi2_limit: float = 0.0
    status: QualityStatus = field(default_factory=QualityStatus)


@dataclass
class NetworkAdjustment:
    kind: str                                  # «minimal» o «constrained»
    datum: str                                 # «UQRO fijo», «restricciones internas», «INEG, ISLP fijos»
    points: list[NetworkPoint] = field(default_factory=list)
    residuals: list[NetworkResidual] = field(default_factory=list)   # de mayor a menor estadístico
    relative: list[RelativeEllipse] = field(default_factory=list)
    observations: int = 0                      # componentes observadas (3 por línea, más las de las ponderadas)
    unknowns: int = 0
    constraints: int = 0                       # ecuaciones de restricción dura
    degrees_of_freedom: int = 0
    chi2: float = 0.0                          # con las covarianzas escaladas, si se escalaron
    chi2_lower: float = 0.0
    chi2_upper: float = 0.0
    passed: bool = True
    sigma0: float = 0.0
    test: str = "tau"
    critical_value: float = 0.0
    scale_factor: float = 1.0                  # de la varianza, aplicado a las covarianzas de las líneas
    chi2_unscaled: float = 0.0
    sigma0_unscaled: float = 0.0
    flagged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class NetworkResult:
    vectors: list[NetworkVector]               # todas, con `trivial` y `note` puestas
    constraints: list[NetworkConstraint]
    options: NetworkOptions
    sessions: dict[str, list[str]] = field(default_factory=dict)    # sesión → ids de sus líneas
    minimal: NetworkAdjustment | None = None
    constrained: NetworkAdjustment | None = None   # None sin fijas en uso
    loops: list[LoopClosure] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)   # líneas independientes que ningún bucle comprueba
    frame: str = OFFICIAL_FRAME
    epoch: float = OFFICIAL_EPOCH
    log: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
    sizes: dict[str, int] = field(default_factory=dict)          # ruta relativa → bytes (para volver a encontrarlo)
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
    # Añadido por las tolerancias (control de calidad): las tolerancias (None: las de omisión del tipo de proyecto,
    # `calidad.default_tolerances`), las coordenadas de control importadas y de qué archivo, y qué resultado se
    # queda cada nombre repetido del CSV de campo (nombre → 0 la media ponderada, k la ocupación k)
    tolerances: ProjectTolerances | None = None
    control_points: list[ControlCoordinate] = field(default_factory=list)
    control_source: str = ""
    occupation_choices: dict[str, int] = field(default_factory=dict)
