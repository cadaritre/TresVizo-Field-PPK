"""Actualizaciones desde GitHub Releases (§3.5), solo cuando el usuario lo pide o lo autorizó en Ajustes.

1. Lee la última versión publicada en la API pública de releases de cadaritre/TresVizo-Field-PPK.
2. Si es más nueva, muestra sus novedades y, a petición, baja el **manifiesto firmado** de esa versión
   (`TresVizo-Field-PPK-<versión>-manifiesto.json` y su `.sig`): SHA-256 y tamaño de cada instalador, firmado con
   Ed25519 por la llave de publicación de la app (`scripts/firmar_version.py`).
3. Verifica la firma con la llave pública incluida aquí; baja el instalador de este sistema, comprueba tamaño y
   SHA-256 contra el manifiesto y solo entonces lo abre. Sin firma válida no instala nada y lo dice.

Sin Tk: la pantalla de Ajustes lo usa en un hilo aparte con progreso y cancelar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import base64
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import urllib.error
import urllib.request

from tresvizo_ppk.datos import ed25519
from tresvizo_ppk.errors import Cancelled, UserError
from tresvizo_ppk.version import __version__

REPOSITORY = 'cadaritre/TresVizo-Field-PPK'
LATEST_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASES_PAGE = f'https://github.com/{REPOSITORY}/releases'
NAME = 'TresVizo-Field-PPK'
MANIFEST_FORMAT = 'tresvizo-field-ppk/version'
TIMEOUT_S = 20
CHUNK = 64*1024

# Llave pública de publicación (Ed25519, 32 bytes en hexadecimal). MARCADOR: la pone el propietario con la salida de
# `python scripts/firmar_version.py --nueva-llave RUTA_PRIVADA` (procedimiento en docs/distribucion.md). Mientras esté
# vacía, la app puede avisar de versiones nuevas pero se niega a instalar nada.
PUBLIC_KEY_HEX = ''


@dataclass
class Release:
    version: str
    tag: str
    name: str
    notes: str
    published: str
    page: str
    assets: dict[str, dict] = field(default_factory=dict)    # nombre → {'url', 'bytes'}


def version_tuple(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', text.strip())
    if not match:
        raise ValueError(f'Versión no válida: {text!r}')
    return tuple(int(x) for x in match.groups())


def is_newer(candidate: str, installed: str = __version__) -> bool:
    return version_tuple(candidate) > version_tuple(installed)


def installer_name(version: str, system: str = sys.platform, machine: str | None = None) -> str | None:
    """Nombre del instalador que corresponde a este sistema (None en Linux: se baja a mano)."""
    machine = (machine or platform.machine()).lower()
    if system == 'win32':
        arch = {'amd64': 'x64', 'x86_64': 'x64', 'arm64': 'arm64'}.get(machine, machine)
        return f'{NAME}-{version}-{arch}.msi'
    if system == 'darwin':
        arch = {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)
        return f'{NAME}-{version}-macos-{arch}.dmg'
    return None


def manifest_names(version: str) -> tuple[str, str]:
    base = f'{NAME}-{version}-manifiesto.json'
    return base, base+'.sig'


def parse_release(data: dict) -> Release:
    try:
        tag = str(data['tag_name'])
        version = '.'.join(str(n) for n in version_tuple(tag))
        assets = {a['name']: {'url': a['browser_download_url'], 'bytes': int(a.get('size', 0))}
                  for a in data.get('assets', [])}
        return Release(version=version, tag=tag, name=str(data.get('name') or tag), notes=str(data.get('body') or ''),
                       published=str(data.get('published_at') or ''), page=str(data.get('html_url') or RELEASES_PAGE),
                       assets=assets)
    except (KeyError, TypeError, ValueError) as exc:
        raise UserError('La respuesta de GitHub no trae una versión válida; inténtalo más tarde.') from exc


def _open(url: str):
    request = urllib.request.Request(url, headers={'User-Agent': f'{NAME}/{__version__}',
                                                   'Accept': 'application/vnd.github+json'})
    return urllib.request.urlopen(request, timeout=TIMEOUT_S)  # noqa: S310 - solo https de GitHub


def _network_error(exc: Exception) -> UserError:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        return UserError('Todavía no hay versiones publicadas en GitHub.')
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 403:
        return UserError('GitHub limitó las consultas desde esta red; inténtalo en una hora.')
    return UserError('No se pudo consultar GitHub. Revisa la conexión a internet e inténtalo de nuevo.')


def latest_release(fetch=None) -> Release:
    """Última versión publicada (no borradores ni preliminares, que la API no devuelve como «latest»)."""
    try:
        if fetch is not None:
            data = fetch(LATEST_URL)
        else:
            with _open(LATEST_URL) as response:
                data = json.loads(response.read(2_000_000).decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise _network_error(exc) from None
    return parse_release(data)


def verify_manifest(manifest: bytes, signature: bytes, public_hex: str = PUBLIC_KEY_HEX) -> dict:
    """Comprueba la firma Ed25519 del manifiesto y devuelve su contenido."""
    if not public_hex:
        raise UserError('Esta compilación no trae la llave de publicación, así que no puede comprobar la firma de '
                        'la actualización. Baja el instalador desde la página de versiones de GitHub y compara su '
                        'SHA-256.')
    try:
        public = bytes.fromhex(public_hex)
        sig = base64.b64decode(signature.strip(), validate=True)
    except ValueError as exc:
        raise UserError('La firma de la actualización está dañada; no se instala nada.') from exc
    if len(public) != 32 or len(sig) != 64 or not ed25519.verify(public, manifest, sig):
        raise UserError('La firma de la actualización no es válida; no se instala nada.')
    try:
        data = json.loads(manifest.decode('utf-8'))
    except ValueError as exc:
        raise UserError('El manifiesto de la actualización está dañado; no se instala nada.') from exc
    if data.get('formato') != MANIFEST_FORMAT or not isinstance(data.get('archivos'), dict):
        raise UserError('El manifiesto de la actualización no es de TresVizo Field PPK; no se instala nada.')
    return data


def _download(url: str, destination: Path, expected: int, progress, cancel, label: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name+'.parcial')
    try:
        with _open(url) as response, open(partial, 'wb') as out:
            done = 0
            while block := response.read(CHUNK):
                if cancel is not None and cancel.is_set():
                    raise Cancelled('Descarga cancelada.')
                done += len(block)
                if expected and done > expected:
                    raise UserError(f'«{label}» es más grande de lo que dice el manifiesto; no se instala.')
                out.write(block)
                if progress and expected:
                    progress(done/expected, f'Bajando {label}: {done/1e6:.1f} de {expected/1e6:.1f} MB')
        partial.replace(destination)
        return destination
    except (urllib.error.URLError, OSError) as exc:
        raise _network_error(exc) from None
    finally:
        partial.unlink(missing_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def fetch_verified_installer(release: Release, folder: Path, progress=None, cancel=None,
                             public_hex: str | None = None, download=None) -> Path:
    """Baja y verifica (firma del manifiesto, tamaño y SHA-256) el instalador de este sistema."""
    public_hex = PUBLIC_KEY_HEX if public_hex is None else public_hex
    download = download or _download
    name = installer_name(release.version)
    if name is None:
        raise UserError('En Linux la actualización se baja a mano desde la página de versiones de GitHub.')
    manifest_name, signature_name = manifest_names(release.version)
    for needed in (name, manifest_name, signature_name):
        if needed not in release.assets:
            raise UserError(f'La versión {release.version} no trae «{needed}»; no se puede instalar de forma '
                            'verificada.')
    folder = Path(folder)/release.version
    manifest = download(release.assets[manifest_name]['url'], folder/manifest_name, 1_000_000, None, cancel,
                        'el manifiesto')
    signature = download(release.assets[signature_name]['url'], folder/signature_name, 10_000, None, cancel,
                         'la firma')
    data = verify_manifest(Path(manifest).read_bytes(), Path(signature).read_bytes(), public_hex)
    if data.get('version') != release.version:
        raise UserError('El manifiesto firmado es de otra versión; no se instala nada.')
    entry = data['archivos'].get(name)
    if not entry:
        raise UserError(f'El manifiesto firmado no incluye «{name}»; no se instala nada.')
    target = folder/name
    if not (target.is_file() and target.stat().st_size == entry['bytes'] and sha256(target) == entry['sha256']):
        target.unlink(missing_ok=True)
        download(release.assets[name]['url'], target, int(entry['bytes']), progress, cancel, name)
    if target.stat().st_size != entry['bytes'] or sha256(target) != entry['sha256']:
        target.unlink(missing_ok=True)
        raise UserError('El instalador bajado no coincide con el manifiesto firmado (SHA-256); se borró y no se '
                        'instala nada.')
    return target


def build_manifest(version: str, files: list[Path], date: str) -> bytes:
    """Manifiesto de una versión (lo usa scripts/firmar_version.py): bytes exactos que se firman."""
    archivos = {Path(p).name: {'sha256': sha256(Path(p)), 'bytes': Path(p).stat().st_size} for p in sorted(files)}
    data = {'formato': MANIFEST_FORMAT, 'version': version, 'fecha': date, 'archivos': archivos}
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)+'\n').encode('utf-8')


def sign_manifest(manifest: bytes, secret: bytes) -> bytes:
    return base64.b64encode(ed25519.sign(secret, manifest))+b'\n'
