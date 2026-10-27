"""Estado y validación de los asistentes (sin Tk): qué falta en cada paso, qué se avisa y cómo pasa a proyecto.

Las pantallas solo pintan este estado; así la lógica se prueba sin abrir ventanas.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from pathlib import Path
import re

from tresvizo_ppk.model import CoordinateChoice, Ecef, Ephemeris, ProcessingOptions, Project, Station

MAX_BASES = 3
CONSTELLATIONS = ('GPS', 'GLONASS', 'Galileo', 'BeiDou', 'QZSS')

# --- Clasificación de archivos ----------------------------------------------------------------------------------------

_COMPRESSED = ('.z', '.gz', '.zip')
_SHORT_OBS = re.compile(r'^\.\d\d[od]$', re.I)            # RINEX 2: .24o, Hatanaka .24d
_SHORT_NAV = re.compile(r'^\.\d\d[nglpfhqcj]$', re.I)
_LONG = re.compile(r'_(?P<kind>[A-Z]{2})\.(rnx|crx)$', re.I)   # RINEX 3/4: …_01D_30S_MO.crx, …_MN.rnx


def inner_name(path: Path) -> str:
    name = Path(path).name
    while Path(name).suffix.lower() in _COMPRESSED:
        name = name[:-len(Path(name).suffix)]
    return name


def classify(path: Path) -> str:
    """«recording», «obs», «nav», «precise», «csv», «project», «zip» o «unknown»."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in ('.bin', '.part'):
        return 'recording'
    if suffix == '.tvppk':
        return 'project'
    if suffix == '.csv':
        return 'csv'
    name = inner_name(path)
    inner = Path(name).suffix.lower()
    if inner in ('.sp3', '.clk', '.eph') or name.lower().endswith(('.clk_30s', '.clk_05s')) or \
            re.search(r'_(ORB|CLK)\.(SP3|CLK)$', name, re.I):
        return 'precise'
    long = _LONG.search(name)
    if long:
        return 'obs' if long['kind'].upper().endswith('O') else 'nav'
    if inner in ('.obs', '.crx') or _SHORT_OBS.match(inner):
        return 'obs'
    if inner in ('.nav', '.rnx') or _SHORT_NAV.match(inner):
        return 'nav'
    if suffix == '.zip':
        return 'zip'   # puede traer obs y nav del INEGI; el motor lo abre
    return 'unknown'


def guess_marker(path: Path) -> str:
    """Clave de 4 caracteres del nombre RINEX («chih2990.24d.Z» → «CHIH»), o «» si el nombre no la trae."""
    name = inner_name(path)
    match = re.match(r'^([A-Za-z][A-Za-z0-9]{3})', name)
    return match.group(1).upper() if match else ''


# --- Números y ángulos ------------------------------------------------------------------------------------------------

def parse_number(text: str) -> float:
    cleaned = str(text).strip().replace(' ', '').replace(' ', '')
    if cleaned.count(',') == 1 and '.' not in cleaned:
        cleaned = cleaned.replace(',', '.')
    value = float(cleaned)
    if not math.isfinite(value):
        raise ValueError('no finito')
    return value


def parse_angle(text: str, positive: str, negative: str) -> float:
    """Grados decimales o «gg° mm' ss.sss" H» (H = hemisferio). Negativo con signo o con la letra del sur/oeste."""
    raw = str(text).strip().upper().replace(',', '.')
    sign = 1.0
    negatives = {negative, 'O'} if negative == 'W' else {negative}   # oeste: W u O
    if raw and (raw[-1] == positive or raw[-1] in negatives):
        sign = -1.0 if raw[-1] in negatives else 1.0
        raw = raw[:-1].strip()
    if raw.startswith('-'):
        sign, raw = -sign, raw[1:]
    parts = [p for p in re.split(r"[°º'′\"″\s:]+", raw) if p]
    if not 1 <= len(parts) <= 3:
        raise ValueError('ángulo')
    values = [float(p) for p in parts]
    if any(v < 0 for v in values) or (len(values) > 1 and (values[1] >= 60 or (len(values) > 2 and values[2] >= 60))):
        raise ValueError('ángulo')
    degrees = values[0]+(values[1]/60 if len(values) > 1 else 0)+(values[2]/3600 if len(values) > 2 else 0)
    return sign*degrees


GRS80_A = 6378137.0
GRS80_F = 1/298.257222101


def geodetic_to_ecef(lat_deg: float, lon_deg: float, h: float) -> tuple[float, float, float]:
    """Elipsoide GRS80 (el de ITRF2008 y de la RGNA)."""
    e2 = GRS80_F*(2-GRS80_F)
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    n = GRS80_A/math.sqrt(1-e2*math.sin(lat)**2)
    return ((n+h)*math.cos(lat)*math.cos(lon), (n+h)*math.cos(lat)*math.sin(lon), (n*(1-e2)+h)*math.sin(lat))


# --- Avisos ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Issue:
    field: str
    message: str
    level: str = 'error'      # «error» impide seguir; «warning» se muestra y deja seguir


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == 'error']


def _files_issues(field_name: str, files: list[Path], what: str, allowed: set[str]) -> list[Issue]:
    found = []
    if not files:
        return [Issue(field_name, f'Agrega {what}: arrástralos aquí o usa «Agregar archivos».')]
    for path in files:
        if not Path(path).is_file():
            found.append(Issue(field_name, f'No se encuentra «{Path(path).name}».'))
        elif classify(path) not in allowed:
            found.append(Issue(field_name, f'«{Path(path).name}» no parece {what}.'))
    return found


def _height_issues(field_name: str, text: str, what: str) -> list[Issue]:
    try:
        h = parse_number(text)
    except ValueError:
        return [Issue(field_name, f'Escribe la altura {what} en metros (por ejemplo 1.800).')]
    if h < 0 or h > 10:
        return [Issue(field_name, f'La altura {what} debe estar entre 0 y 10 m.')]
    if h > 4:
        return [Issue(field_name, f'Altura {what} de {h:.3f} m: revisa que sea la vertical y en metros.', 'warning')]
    return []


def _name_issues(field_name: str, text: str, what: str, limit: int = 40) -> list[Issue]:
    value = text.strip()
    if not value:
        return [Issue(field_name, f'Escribe el nombre {what}.')]
    if len(value) > limit or any(ord(c) < 32 for c in value) or any(c in value for c in '<>:"/\\|?*'):
        return [Issue(field_name, f'El nombre {what} admite hasta {limit} caracteres, sin < > : " / \\ | ? *.')]
    return []


def options_issues(options: ProcessingOptions, precise_download: bool) -> list[Issue]:
    found = []
    if not 0 <= options.elevation_mask_deg <= 30:
        found.append(Issue('mask', 'La máscara de elevación va de 0° a 30°.'))
    elif options.elevation_mask_deg < 10:
        found.append(Issue('mask', 'Una máscara menor de 10° deja entrar más multitrayectoria.', 'warning'))
    if not options.constellations:
        found.append(Issue('constellations', 'Elige al menos una constelación.'))
    elif 'GPS' not in options.constellations:
        found.append(Issue('constellations', 'Sin GPS quedan menos satélites comunes con las estaciones de la RGNA.',
                           'warning'))
    if options.ephemeris is Ephemeris.PRECISE and not options.precise_files and not precise_download:
        found.append(Issue('ephemeris', 'Agrega los archivos SP3/CLK o marca «Descargar efemérides precisas».'))
    for path in options.precise_files:
        if not Path(path).is_file():
            found.append(Issue('ephemeris', f'No se encuentra «{Path(path).name}».'))
    if options.min_ratio < 1.5 or options.min_ratio > 10:
        found.append(Issue('ratio', 'El cociente mínimo para fijar va de 1.5 a 10.'))
    return found


def number_issues(number: str) -> list[Issue]:
    if len(number.strip()) > 40 or any(ord(c) < 32 for c in number):
        return [Issue('number', 'El número de proyecto admite hasta 40 caracteres.')]
    return []


# --- Estático: georreferenciar un punto ------------------------------------------------------------------------------

@dataclass
class BaseEntry:
    files: list[Path] = field(default_factory=list)
    station: Station | None = None
    name: str = ''                 # nombre que sale en el informe (editable)

    @property
    def marker(self) -> str:
        for path in self.files:
            if guess_marker(path):
                return guess_marker(path)
        return ''


ROVER_KINDS = {'recording', 'obs', 'nav', 'zip'}
BASE_KINDS = {'obs', 'nav', 'zip'}


@dataclass
class StaticJob:
    rover_files: list[Path] = field(default_factory=list)
    point_name: str = ''
    pole_height: str = ''
    bases: list[BaseEntry] = field(default_factory=list)
    options: ProcessingOptions = field(default_factory=ProcessingOptions)
    coordinates: CoordinateChoice = field(default_factory=CoordinateChoice)
    precise_download: bool = False
    number: str = ''                         # número o clave del proyecto (sale en el informe)

    STEPS = ('Rover', 'Estaciones RGNA', 'Ajustes', 'Proceso', 'Resultado')

    def step_issues(self, step: int) -> list[Issue]:
        if step == 0:
            found = _files_issues('rover', self.rover_files, 'la grabación del receptor o su RINEX', ROVER_KINDS)
            kinds = {classify(p) for p in self.rover_files}
            if 'recording' in kinds and len(kinds - {'recording'}) > 0:
                found.append(Issue('rover', 'Mezclaste una grabación del receptor con archivos RINEX: usa solo uno '
                                            'de los dos.'))
            elif self.rover_files and 'recording' not in kinds and not kinds & {'obs', 'zip'}:
                found.append(Issue('rover', 'Falta el archivo de observaciones (RINEX «o», «.obs», «_MO.rnx»).'))
            elif kinds and kinds <= {'obs'}:
                found.append(Issue('rover', 'Sin navegación del rover: se usará la de las estaciones.', 'warning'))
            return found+_name_issues('point_name', self.point_name, 'del punto') + \
                _height_issues('pole_height', self.pole_height, 'del bastón o trípode')
        if step == 1:
            found = []
            if not self.bases:
                return [Issue('bases', 'Agrega al menos una estación de la RGNA (hasta tres).')]
            if len(self.bases) > MAX_BASES:
                found.append(Issue('bases', f'Usa como máximo {MAX_BASES} estaciones.'))
            seen = set()
            for index, base in enumerate(self.bases):
                key = f'base{index}'
                found += _files_issues(key, base.files, 'los RINEX de la estación', BASE_KINDS)
                if base.station is None:
                    found.append(Issue(key, 'Elige la estación del catálogo de la RGNA (no se reconoció por el nombre '
                                            'del archivo).'))
                else:
                    if base.station.id in seen:
                        found.append(Issue(key, f'La estación {base.station.id} está repetida.'))
                    seen.add(base.station.id)
                    found += _name_issues(key, base.name or base.station.name, 'de la estación', 60)
                    if not base.station.official:
                        found.append(Issue(key, f'{base.station.id}: coordenada corregida a mano; el informe lo dirá.',
                                           'warning'))
            if len(self.bases) == 1:
                found.append(Issue('bases', 'Con una sola estación no hay ajuste ni comprobación entre líneas base; '
                                            'con dos o tres el resultado se puede verificar.', 'warning'))
            return found
        if step == 2:
            return options_issues(self.options, self.precise_download)+number_issues(self.number)
        return []

    def first_invalid_step(self) -> int | None:
        for step in range(3):
            if errors(self.step_issues(step)):
                return step
        return None

    def pole_height_m(self) -> float:
        return parse_number(self.pole_height)

    def stations(self) -> list[Station]:
        """Las estaciones con el nombre editado aplicado."""
        return [replace(b.station, name=(b.name.strip() or b.station.name)) for b in self.bases if b.station]

    def files(self) -> dict[str, list[Path]]:
        files = {'rover': list(self.rover_files)}
        for base in self.bases:
            if base.station:
                files['base:'+base.station.id] = list(base.files)
        if self.options.precise_files:
            files['precise'] = list(self.options.precise_files)
        return files

    def to_project(self, project: Project | None = None) -> Project:
        project = project or Project()
        project.kind = 'static'
        project.number = self.number.strip()
        project.point_name = self.point_name.strip()
        project.pole_height_m = self.pole_height_m()
        project.options = self.options
        project.coordinates = self.coordinates
        project.bases = self.stations()
        return project

    @classmethod
    def from_project(cls, project: Project, files: dict[str, list[Path]]) -> 'StaticJob':
        stations = {s.id: s for s in project.bases}
        bases = [BaseEntry(files=paths, station=stations.get(role.split(':', 1)[1]),
                           name=stations[role.split(':', 1)[1]].name if role.split(':', 1)[1] in stations else '')
                 for role, paths in files.items() if role.startswith('base:')]
        return cls(rover_files=files.get('rover', []), point_name=project.point_name,
                   pole_height=f'{project.pole_height_m:.3f}', bases=bases, options=project.options,
                   coordinates=project.coordinates, number=project.number)


# --- PPK de puntos --------------------------------------------------------------------------------------------------

@dataclass
class PointsJob:
    base_kind: str = 'rgna'                  # «rgna» o «own»
    base: BaseEntry = field(default_factory=BaseEntry)
    own_name: str = ''
    own_height: str = ''
    own_source: str = 'project'              # «project» (encadenada desde un estático) o «manual»
    own_project: Path | None = None
    own_position: Ecef | None = None         # leída del proyecto estático
    coord_format: str = 'geodetic'           # «geodetic» (lat, lon, h) o «ecef»
    coord_text: tuple[str, str, str] = ('', '', '')
    rover_files: list[Path] = field(default_factory=list)
    csv_file: Path | None = None
    options: ProcessingOptions = field(default_factory=ProcessingOptions)
    coordinates: CoordinateChoice = field(default_factory=CoordinateChoice)
    precise_download: bool = False
    number: str = ''

    STEPS = ('Base', 'Rover y puntos', 'Ajustes', 'Proceso', 'Resultado')

    def manual_position(self) -> Ecef:
        a, b, c = self.coord_text
        if self.coord_format == 'ecef':
            x, y, z = parse_number(a), parse_number(b), parse_number(c)
            radius = math.sqrt(x*x+y*y+z*z)
            if not 6_300_000 < radius < 6_400_000:
                raise ValueError('La coordenada no está sobre la superficie de la Tierra (revisa X, Y, Z).')
            return Ecef(x, y, z)
        lat, lon, h = parse_angle(a, 'N', 'S'), parse_angle(b, 'E', 'W'), parse_number(c)
        if not -90 <= lat <= 90 or not -180 <= lon <= 180 or not -500 <= h <= 9000:
            raise ValueError('Latitud, longitud o altura fuera de rango.')
        return Ecef(*geodetic_to_ecef(lat, lon, h))

    def step_issues(self, step: int) -> list[Issue]:
        if step == 0:
            if self.base_kind == 'rgna':
                found = _files_issues('base', self.base.files, 'los RINEX de la estación', BASE_KINDS)
                if self.base.station is None:
                    found.append(Issue('base', 'Elige la estación del catálogo de la RGNA.'))
                return found
            found = _files_issues('base', self.base.files, 'la grabación de la base o su RINEX', ROVER_KINDS)
            found += _name_issues('own_name', self.own_name, 'de la base')
            found += _height_issues('own_height', self.own_height, 'de la base')
            if self.own_source == 'project':
                if self.own_project is None:
                    found.append(Issue('own_coord', 'Elige el proyecto «Georreferenciar punto» de esta base.'))
                elif self.own_position is None:
                    found.append(Issue('own_coord', 'Ese proyecto todavía no tiene coordenada final: procésalo antes.'))
            else:
                try:
                    self.manual_position()
                except ValueError as exc:
                    message = str(exc) if 'Tierra' in str(exc) or 'rango' in str(exc) else \
                        'Escribe la coordenada completa de la base (los tres valores).'
                    found.append(Issue('own_coord', message))
                else:
                    found.append(Issue('own_coord', 'Coordenada escrita a mano: debe estar en ITRF2008 época 2010.0 '
                                                    'para ligar el resultado a la RGNA.', 'warning'))
            return found
        if step == 1:
            found = _files_issues('rover', self.rover_files, 'la grabación cinemática o su RINEX', ROVER_KINDS)
            if self.csv_file is None:
                found.append(Issue('csv', 'Agrega el CSV de puntos que exportó TresVizo Field.'))
            elif not Path(self.csv_file).is_file():
                found.append(Issue('csv', f'No se encuentra «{Path(self.csv_file).name}».'))
            elif classify(self.csv_file) != 'csv':
                found.append(Issue('csv', 'El archivo de puntos debe ser el CSV de TresVizo Field.'))
            return found
        if step == 2:
            return options_issues(self.options, self.precise_download)+number_issues(self.number)
        return []

    def first_invalid_step(self) -> int | None:
        for step in range(3):
            if errors(self.step_issues(step)):
                return step
        return None

    def station(self) -> Station:
        if self.base_kind == 'rgna':
            return replace(self.base.station, name=(self.base.name.strip() or self.base.station.name))
        position = self.own_position if self.own_source == 'project' else self.manual_position()
        source = (f'Proyecto {Path(self.own_project).name}' if self.own_source == 'project' and self.own_project
                  else 'Escrita por el usuario')
        name = self.own_name.strip()
        return Station(id=re.sub(r'[^A-Za-z0-9]', '', name)[:9].upper() or 'BASE', name=name, position=position,
                       antenna_height_m=parse_number(self.own_height), receiver='MeridianV', source=source,
                       official=False)

    def base_height_m(self) -> float | None:
        return parse_number(self.own_height) if self.base_kind == 'own' else None

    def files(self) -> dict[str, list[Path]]:
        files = {'base:'+(self.station().id if self.base_kind == 'own' or self.base.station else 'BASE'):
                 list(self.base.files), 'rover': list(self.rover_files)}
        if self.csv_file:
            files['csv'] = [self.csv_file]
        if self.options.precise_files:
            files['precise'] = list(self.options.precise_files)
        return files

    def to_project(self, project: Project | None = None, folder: Path | None = None) -> Project:
        from tresvizo_ppk.project import relative
        project = project or Project()
        project.kind = 'points'
        project.number = self.number.strip()
        project.base_kind = self.base_kind
        project.options = self.options
        project.coordinates = self.coordinates
        project.bases = [self.station()]
        project.pole_height_m = self.base_height_m() or 0.0
        project.source_project = (relative(self.own_project, folder) if folder and self.own_project and
                                  self.base_kind == 'own' and self.own_source == 'project' else '')
        return project

    @classmethod
    def from_project(cls, project: Project, files: dict[str, list[Path]]) -> 'PointsJob':
        from tresvizo_ppk.project import absolute
        station = project.bases[0] if project.bases else None
        base_files = next((paths for role, paths in files.items() if role.startswith('base:')), [])
        job = cls(base_kind=project.base_kind, rover_files=files.get('rover', []),
                  csv_file=(files.get('csv') or [None])[0], options=project.options, coordinates=project.coordinates,
                  number=project.number)
        if project.base_kind == 'rgna':
            job.base = BaseEntry(files=base_files, station=station, name=station.name if station else '')
        else:
            job.base = BaseEntry(files=base_files)
            job.own_name = station.name if station else ''
            job.own_height = f'{project.pole_height_m:.3f}'
            if project.source_project and project.folder:
                job.own_source = 'project'
                job.own_project = absolute(project.source_project, project.folder)
                job.own_position = station.position if station else None
            elif station:
                job.own_source, job.coord_format = 'manual', 'ecef'
                job.coord_text = (f'{station.position.x:.4f}', f'{station.position.y:.4f}', f'{station.position.z:.4f}')
        return job
