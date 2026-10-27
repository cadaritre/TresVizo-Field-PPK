"""Modelo (sin Tk) del catálogo de «Sistemas y geoides»: clases, regiones y países, búsqueda y filtros.

Junta lo instalado (cada sistema, con su definición) y lo que ofrece el índice firmado (paquetes, geoides, rejillas y
redes CORS) en entradas homogéneas. Pensado para cientos de sistemas: la pantalla pide solo los grupos que abre.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata

CLASSES = {  # clave → (título, icono Lucide, token de color)
    'projected': ('Sistemas proyectados', 'map', 'chart1'),
    'geographic': ('Sistemas geográficos', 'globe', 'chart2'),
    'geoid': ('Geoides', 'mountain', 'chart3'),
    'grid': ('Rejillas de transformación', 'layers', 'chart4'),
    'cors': ('Redes CORS', 'radio-tower', 'chart6'),
}
SHORT = {'projected': 'Proyectado', 'geographic': 'Geográfico', 'geoid': 'Geoide', 'grid': 'Rejilla', 'cors': 'Red CORS'}
REGIONS = ['Norteamérica', 'Centroamérica y Caribe', 'Sudamérica', 'Europa', 'Mundo']
COUNTRIES = {
    'MX': ('México', 'Norteamérica'), 'US': ('Estados Unidos', 'Norteamérica'), 'CA': ('Canadá', 'Norteamérica'),
    'GT': ('Guatemala', 'Centroamérica y Caribe'), 'BZ': ('Belice', 'Centroamérica y Caribe'),
    'SV': ('El Salvador', 'Centroamérica y Caribe'), 'HN': ('Honduras', 'Centroamérica y Caribe'),
    'NI': ('Nicaragua', 'Centroamérica y Caribe'), 'CR': ('Costa Rica', 'Centroamérica y Caribe'),
    'PA': ('Panamá', 'Centroamérica y Caribe'), 'CU': ('Cuba', 'Centroamérica y Caribe'),
    'DO': ('República Dominicana', 'Centroamérica y Caribe'), 'HT': ('Haití', 'Centroamérica y Caribe'),
    'JM': ('Jamaica', 'Centroamérica y Caribe'), 'PR': ('Puerto Rico', 'Centroamérica y Caribe'),
    'TT': ('Trinidad y Tobago', 'Centroamérica y Caribe'),
    'AR': ('Argentina', 'Sudamérica'), 'BO': ('Bolivia', 'Sudamérica'), 'BR': ('Brasil', 'Sudamérica'),
    'CL': ('Chile', 'Sudamérica'), 'CO': ('Colombia', 'Sudamérica'), 'EC': ('Ecuador', 'Sudamérica'),
    'GY': ('Guyana', 'Sudamérica'), 'PY': ('Paraguay', 'Sudamérica'), 'PE': ('Perú', 'Sudamérica'),
    'SR': ('Surinam', 'Sudamérica'), 'UY': ('Uruguay', 'Sudamérica'), 'VE': ('Venezuela', 'Sudamérica'),
    'ES': ('España', 'Europa'), 'PT': ('Portugal', 'Europa'), 'FR': ('Francia', 'Europa'),
}
WORLD = ('', 'Mundo')


def normalize(text: str) -> str:
    """Sin acentos ni mayúsculas, para buscar «mexico» y encontrar «México»."""
    decomposed = unicodedata.normalize('NFKD', str(text))
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def country_region(code: str) -> tuple[str, str]:
    if not code:
        return WORLD
    name, region = COUNTRIES.get(code.upper(), (code.upper(), 'Mundo'))
    return name, region


@dataclass
class Entry:
    cls: str                                   # clave de CLASSES
    key: str                                   # único en el catálogo
    title: str
    subtitle: str = ''
    code: str = ''                             # EPSG u otra clave visible
    countries: tuple[str, ...] = ()
    installed: bool = False
    bundled: bool = False
    update: bool = False
    downloadable: bool = False
    item_id: str = ''                          # unidad que se instala o se quita (paquete, geoide, rejilla, red)
    package: str = ''                          # nombre del paquete que lo trae
    size: str = ''
    details: list[tuple[str, str]] = field(default_factory=list)
    extra_search: str = ''

    @property
    def state(self) -> str:
        if self.bundled:
            return 'De fábrica'
        if self.installed and self.update:
            return 'Actualización disponible'
        if self.installed:
            return 'Instalado'
        return 'Disponible para bajar' if self.downloadable else '—'

    @property
    def short_state(self) -> str:
        return {'Disponible para bajar': 'Por bajar', 'Actualización disponible': 'Actualizable'}.get(self.state,
                                                                                                     self.state)

    @property
    def groups(self) -> list[tuple[str, str]]:
        """(región, país) en los que aparece (un paquete de varios países aparece en cada uno)."""
        if not self.countries:
            return [('Mundo', 'Mundo')]
        out = []
        for code in self.countries:
            name, region = country_region(code)
            out.append((region, name))
        return out

    def search_text(self) -> str:
        countries = ' '.join(f'{c} {country_region(c)[0]}' for c in self.countries)
        return normalize(' '.join((self.title, self.subtitle, self.code, countries, self.package,
                                   ' '.join(v for _, v in self.details), self.extra_search)))


def _fmt(value, places=6) -> str:
    if isinstance(value, float):
        return f'{value:.{places}f}'.rstrip('0').rstrip('.')
    return str(value)


PROJECTION_LABELS = {'latitudeOfOriginDegrees': 'Latitud de origen (°)', 'centralMeridianDegrees': 'Meridiano central (°)',
                     'scaleFactor': 'Factor de escala', 'falseEastingMeters': 'Falso este (m)',
                     'falseNorthingMeters': 'Falso norte (m)', 'standardParallel1Degrees': 'Paralelo estándar 1 (°)',
                     'standardParallel2Degrees': 'Paralelo estándar 2 (°)', 'zone': 'Zona'}


def system_details(info, definition) -> list[tuple[str, str]]:
    rows = []
    if definition is not None:
        rows.append(('Código', definition.code))
        rows.append(('Datum', definition.datum or '—'))
        e = definition.ellipsoid
        if e is not None:
            rows.append(('Elipsoide', f'{e.name} (a = {_fmt(e.a, 3)} m, 1/f = {_fmt(e.rf, 9)})'))
        if getattr(definition, 'frame_declaration', ''):
            rows.append(('Marco y época', definition.frame_declaration))
        if definition.is_projected:
            rows.append(('Proyección', definition.projection_name))
            for key, value in (definition.projection or {}).items():
                if key != 'method':
                    rows.append((PROJECTION_LABELS.get(key, key), _fmt(value)))
        rows.append(('Unidad', definition.unit or '—'))
        rows.append(('Orden de ejes', definition.axis_order or '—'))
        if definition.area is not None:
            a = definition.area
            rows.append(('Área de uso', f'{a.description} ({_fmt(a.south, 2)}° a {_fmt(a.north, 2)}° de latitud, '
                                        f'{_fmt(a.west, 2)}° a {_fmt(a.east, 2)}° de longitud)'))
        if info.accuracy_text:
            rows.append(('Exactitud', info.accuracy_text))
        if info.missing_grids:
            rows.append(('Falta', 'rejilla '+', '.join(info.missing_grids)))
        rows.append(('Fuente', definition.source or '—'))
    else:
        rows += [('Clave', info.code), ('Datum', info.datum or '—'), ('Área de uso', info.area or '—')]
    return rows


def item_details(item) -> list[tuple[str, str]]:
    rows = [(label, value) for label, value in (
        ('Fuente', item.authority), ('Licencia', item.license + (f' ({item.license_url})' if item.license_url else '')),
        ('Atribución', item.attribution), ('Aviso', item.transformation_notice), ('Versión', item.version),
        ('Tamaño', item.size_text), ('Sistemas que trae', str(item.systems) if item.systems else ''),
        ('Necesita', ', '.join(item.requires))) if value]
    return rows


def _system_countries(info, packages) -> tuple[str, ...]:
    if info.countries:
        return tuple(info.countries)
    package = packages.get(info.origin)
    if package is not None and package.countries:
        return tuple(package.countries)
    text = normalize(f'{info.name} {info.datum}')
    return ('MX',) if 'mexico' in text else ()


def build(crs, networks=()) -> list[Entry]:
    """Todas las entradas: lo instalado sistema por sistema y lo del índice por unidad de instalación."""
    items = list(crs.catalog())
    packages = {i.id: i for i in items}
    entries: list[Entry] = []
    for info in crs.systems():
        definition = crs.definition(info.code) if info.kind != 'family' and callable(getattr(crs, 'definition', None)) \
            else None
        cls = 'geographic' if info.kind.startswith('geographic') else 'projected'
        package = packages.get(info.origin)
        bundled = info.origin in ('fábrica', 'fabrica') or (package is not None and package.bundled)
        entries.append(Entry(
            cls=cls, key='sys:'+info.code, title=info.name, code=info.code if info.code.startswith('EPSG') else '',
            subtitle=('Zona automática por la longitud' if info.kind == 'family' else info.datum),
            countries=_system_countries(info, packages), installed=True, bundled=bundled,
            update=bool(package and package.update_available), downloadable=bool(package and package.downloadable),
            item_id='' if bundled else info.origin, package=package.title if package else 'De fábrica',
            details=system_details(info, definition), extra_search=info.datum))
    for item in items:
        if item.kind == 'factory':
            continue
        if item.kind == 'crs-package':
            if item.installed and not item.update_available:
                continue    # sus sistemas ya están, uno por uno
            for cls in ('projected', 'geographic'):
                entries.append(Entry(
                    cls=cls, key=f'pkg:{cls}:{item.id}', title=f'Paquete {item.title}',
                    subtitle=f'{item.systems} sistemas (proyectados y geográficos) · se instalan juntos',
                    countries=tuple(item.countries), installed=item.installed, update=item.update_available,
                    downloadable=item.downloadable, item_id=item.id, package=item.title, size=item.size_text,
                    details=item_details(item)))
            continue
        cls = {'geoid': 'geoid', 'datum-grid': 'grid', 'cors-network': 'cors'}.get(item.kind)
        if cls is None:
            continue
        entries.append(Entry(cls=cls, key=f'item:{item.id}', title=item.title, subtitle=item.region,
                             code=item.id.upper() if cls == 'geoid' else item.id, countries=tuple(item.countries),
                             installed=item.installed, bundled=item.bundled, update=item.update_available,
                             downloadable=item.downloadable, item_id='' if item.bundled else item.id,
                             package=item.title, size=item.size_text, details=item_details(item)))
    known = {e.item_id for e in entries if e.cls == 'cors'}
    for network in networks:
        if network.id in known:
            continue
        entries.append(Entry(cls='cors', key='net:'+network.id, title=network.name,
                             subtitle=f'{len(network.stations)} estaciones · {network.frame_text}',
                             countries=tuple(c.strip() for c in network.country.split(',') if c.strip()),
                             installed=True, bundled=network.origin == 'incluida', item_id='' if
                             network.origin == 'incluida' else network.id, package=network.name,
                             details=[(k, v) for k, v in (('Marco y época oficial', network.frame_text),
                                                          ('Fuente', network.source), ('Licencia', network.license),
                                                          ('Atribución', network.attribution),
                                                          ('Página', network.url)) if v]))
    return entries


def select(entries: list[Entry], cls: str | None, query: str = '', state: str = 'all') -> list[Entry]:
    """Filtra por clase, texto (sin acentos ni mayúsculas, todas las palabras) y estado («installed», «available»)."""
    words = normalize(query).split()
    out = []
    for e in entries:
        if cls and e.cls != cls:
            continue
        if state == 'installed' and not e.installed:
            continue
        if state == 'available' and (e.installed and not e.update):
            continue
        if words:
            text = e.search_text()
            if not all(w in text for w in words):
                continue
        out.append(e)
    return out


def grouped(entries: list[Entry]) -> dict[str, dict[str, list[Entry]]]:
    """región → país → entradas, en el orden de REGIONS y por nombre."""
    tree: dict[str, dict[str, list[Entry]]] = {}
    for e in entries:
        for region, country in e.groups:
            tree.setdefault(region, {}).setdefault(country, []).append(e)
    ordered = {}
    for region in REGIONS+sorted(set(tree)-set(REGIONS)):
        if region in tree:
            ordered[region] = {c: sorted(v, key=lambda e: (not e.installed, normalize(e.title)))
                               for c, v in sorted(tree[region].items(), key=lambda kv: normalize(kv[0]))}
    return ordered


def counts(entries: list[Entry], query: str = '', state: str = 'all') -> dict[str, int]:
    return {cls: len(select(entries, cls, query, state)) for cls in CLASSES}
