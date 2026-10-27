"""Preferencias del usuario: datos para el informe, unidades, carpeta de datos, apariencia y proyectos recientes.

Sin Tk. Un JSON en la carpeta de configuración del sistema, escrito de forma atómica; si está dañado se conserva
aparte y se empieza con los valores por omisión (nunca se pierde en silencio).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import tempfile
import uuid

from tresvizo_ppk.model import CoordinateChoice, UserProfile

from app.paths import config_dir, default_data_dir
from app.themes import BUILTINS

SCHEMA = 1
MAX_RECENTS = 12
# Unidades de longitud que se pueden mostrar. El cálculo y los archivos siempre van en metros.
UNITS = {'m': ('metros', 'm', 1.0),
         'ftUS': ('pies topográficos de EE. UU.', 'ftUS', 1200/3937),
         'ft': ('pies internacionales', 'ft', 0.3048)}


class SettingsError(Exception):
    pass


@dataclass
class Preferences:
    company: str = ''
    surveyor: str = ''
    license_id: str = ''
    logo: str = ''                         # copia normalizada en la carpeta de configuración
    units: str = 'm'
    decimals: int = 3
    data_dir: str = ''
    theme: str = 'Claro'
    reduce_motion: bool = False
    crs_id: str = CoordinateChoice.crs_id
    zone: str = ''
    geoid_id: str | None = CoordinateChoice.geoid_id
    recents: list[str] = field(default_factory=list)
    last_folder: str = ''
    check_updates: bool = False             # buscar actualizaciones al abrir (solo si el usuario lo activa)


def validate(p: Preferences) -> Preferences:
    for name, limit in (('company', 120), ('surveyor', 120), ('license_id', 40)):
        value = getattr(p, name)
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
            raise SettingsError(f'Revisa el campo «{name}»: texto de hasta {limit} caracteres.')
    if p.units not in UNITS:
        raise SettingsError('Unidad de longitud desconocida.')
    if not isinstance(p.decimals, int) or not 0 <= p.decimals <= 4:
        raise SettingsError('Elige de 0 a 4 decimales.')
    if p.theme not in BUILTINS:
        raise SettingsError('Apariencia desconocida.')
    if not isinstance(p.recents, list) or not all(isinstance(r, str) for r in p.recents):
        raise SettingsError('Lista de recientes inválida.')
    return p


class Settings:
    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder) if folder else config_dir()
        self.path = self.folder/'preferencias.json'
        self.recovered_from: Path | None = None
        self.values = self._read()

    def _read(self) -> Preferences:
        if not self.path.exists():
            return Preferences()
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('schema') != SCHEMA:
                raise SettingsError('Versión de preferencias desconocida.')
            known = Preferences.__dataclass_fields__
            return validate(Preferences(**{k: v for k, v in data.get('values', {}).items() if k in known}))
        except (OSError, ValueError, TypeError, SettingsError):
            # Se aparta el archivo dañado para poder revisarlo y se sigue con los valores por omisión.
            damaged = self.path.with_name(f'preferencias.dañado-{uuid.uuid4().hex[:8]}.json')
            try:
                self.path.replace(damaged)
                self.recovered_from = damaged
            except OSError:
                pass
            return Preferences()

    def save(self, **changes) -> Preferences:
        candidate = Preferences(**{**asdict(self.values), **changes})
        validate(candidate)
        self.folder.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix='.preferencias-', dir=self.folder)
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as out:
                json.dump({'schema': SCHEMA, 'values': asdict(candidate)}, out, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        self.values = candidate
        return candidate

    # --- Derivados ----------------------------------------------------------------------------------------------------

    def data_dir(self) -> Path:
        return Path(self.values.data_dir) if self.values.data_dir else default_data_dir()

    def projects_dir(self) -> Path:
        return self.data_dir()/'Proyectos'

    def downloads_dir(self) -> Path:
        return self.data_dir()/'Descargas'

    def profile(self) -> UserProfile:
        v = self.values
        logo = Path(v.logo) if v.logo and Path(v.logo).is_file() else None
        return UserProfile(company=v.company, surveyor=v.surveyor, license_id=v.license_id, logo=logo)

    def coordinates(self) -> CoordinateChoice:
        v = self.values
        return CoordinateChoice(crs_id=v.crs_id, zone=v.zone, geoid_id=v.geoid_id)

    def add_recent(self, path: Path) -> None:
        text = str(Path(path).resolve())
        recents = [text]+[r for r in self.values.recents if os.path.normcase(r) != os.path.normcase(text)]
        self.save(recents=recents[:MAX_RECENTS], last_folder=str(Path(text).parent))

    def remove_recent(self, path: Path | str) -> None:
        text = os.path.normcase(str(path))
        self.save(recents=[r for r in self.values.recents if os.path.normcase(r) != text])

    def set_logo(self, source: Path) -> Path:
        """Copia normalizada (PNG) del logo del usuario; nunca se referencia la imagen original."""
        from PIL import Image
        source = Path(source)
        if source.stat().st_size > 10_000_000:
            raise SettingsError('El logo debe pesar menos de 10 MB.')
        try:
            with Image.open(source) as image:
                if image.format not in ('PNG', 'JPEG') or image.width*image.height > 20_000_000:
                    raise SettingsError('Elige un logo PNG o JPEG de hasta 20 megapíxeles.')
                normalized = image.convert('RGBA')
        except OSError as exc:
            raise SettingsError('No se pudo leer la imagen del logo.') from exc
        normalized.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        self.folder.mkdir(parents=True, exist_ok=True)
        destination = self.folder/f'logo-{uuid.uuid4().hex[:12]}.png'
        normalized.save(destination)
        old = Path(self.values.logo) if self.values.logo else None
        self.save(logo=str(destination))
        if old and old.parent == self.folder and old != destination:
            old.unlink(missing_ok=True)
        return destination

    def clear_logo(self) -> None:
        old = Path(self.values.logo) if self.values.logo else None
        self.save(logo='')
        if old and old.parent == self.folder:
            old.unlink(missing_ok=True)


def format_length(meters: float | None, settings: Preferences, decimals: int | None = None) -> str:
    if meters is None:
        return '—'
    _, symbol, factor = UNITS[settings.units]
    places = settings.decimals if decimals is None else decimals
    return f'{meters/factor:,.{places}f} {symbol}'.replace(',', ' ')
