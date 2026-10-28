"""Actualizaciones: versión publicada, manifiesto firmado (llave de prueba generada aquí), descarga verificada."""
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import updates
from tresvizo_ppk.datos import ed25519
from tresvizo_ppk.errors import UserError

ROOT = Path(__file__).resolve().parents[1]


def release_json(version='9.1.0', names=()):
    return {'tag_name': f'v{version}', 'name': f'TresVizo Field PPK {version}', 'body': '- Novedad 1\n- Novedad 2',
            'published_at': '2026-11-02T10:00:00Z', 'html_url': 'https://github.com/x/y/releases/tag/v'+version,
            'assets': [{'name': n, 'browser_download_url': 'https://descarga/'+n, 'size': 0} for n in names]}


@pytest.fixture
def signed(tmp_path):
    """Una versión publicada con su manifiesto firmado por una llave de prueba."""
    secret = secrets.token_bytes(32)
    public = ed25519.public_key(secret).hex()
    version = '9.1.0'
    name = updates.installer_name(version)
    if name is None:
        pytest.skip('Linux no instala automáticamente.')
    folder = tmp_path/'publicado'
    folder.mkdir()
    installer = folder/name
    installer.write_bytes(b'instalador de prueba '*1000)
    other = folder/f'TresVizo-Field-PPK-{version}-linux-x86_64.tar.gz'
    other.write_bytes(b'otro')
    manifest = updates.build_manifest(version, [installer, other], '2026-11-02')
    manifest_name, signature_name = updates.manifest_names(version)
    (folder/manifest_name).write_bytes(manifest)
    (folder/signature_name).write_bytes(updates.sign_manifest(manifest, secret))
    release = updates.parse_release(release_json(version, [name, other.name, manifest_name, signature_name]))
    served = {'https://descarga/'+p.name: p for p in folder.iterdir()}

    def download(url, destination, expected, progress, cancel, label):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(served[url], destination)
        return destination
    return release, public, download, folder, name


def test_versions_and_names():
    assert updates.version_tuple('v1.2.3') == (1, 2, 3)
    assert updates.is_newer('0.10.0', '0.9.9') and not updates.is_newer('0.1.0', '0.1.0')
    with pytest.raises(ValueError):
        updates.version_tuple('1.2')
    assert updates.installer_name('1.0.0', 'win32', 'AMD64') == 'TresVizo-Field-PPK-1.0.0-x64.msi'
    assert updates.installer_name('1.0.0', 'darwin', 'arm64') == 'TresVizo-Field-PPK-1.0.0-macos-arm64.dmg'
    assert updates.installer_name('1.0.0', 'linux', 'x86_64') is None


def test_latest_release_parsing_and_errors():
    release = updates.latest_release(fetch=lambda url: release_json('2.0.1', ['a.msi']))
    assert release.version == '2.0.1' and 'Novedad 1' in release.notes and 'a.msi' in release.assets
    with pytest.raises(UserError):
        updates.latest_release(fetch=lambda url: {'tag_name': 'beta'})
    with pytest.raises(UserError, match='conexión'):
        updates.latest_release(fetch=lambda url: (_ for _ in ()).throw(OSError('sin red')))


def test_verified_install_flow(signed, tmp_path):
    release, public, download, folder, name = signed
    path = updates.fetch_verified_installer(release, tmp_path/'bajadas', public_hex=public, download=download)
    assert path.read_bytes() == (folder/name).read_bytes()


def test_rejects_without_key_wrong_key_and_tampering(signed, tmp_path):
    release, public, download, folder, name = signed
    with pytest.raises(UserError, match='no trae la llave'):
        updates.fetch_verified_installer(release, tmp_path/'a', public_hex='', download=download)
    other = ed25519.public_key(secrets.token_bytes(32)).hex()
    with pytest.raises(UserError, match='firma'):
        updates.fetch_verified_installer(release, tmp_path/'b', public_hex=other, download=download)
    (folder/name).write_bytes(b'instalador alterado')
    with pytest.raises(UserError, match='SHA-256'):
        updates.fetch_verified_installer(release, tmp_path/'c', public_hex=public, download=download)
    assert not (tmp_path/'c'/release.version/name).exists()
    manifest_name, _ = updates.manifest_names(release.version)
    (folder/manifest_name).write_bytes((folder/manifest_name).read_bytes().replace(b'9.1.0', b'9.1.1'))
    with pytest.raises(UserError, match='firma'):
        updates.fetch_verified_installer(release, tmp_path/'d', public_hex=public, download=download)


def test_missing_assets_are_reported(signed, tmp_path):
    release, public, download, folder, name = signed
    del release.assets[name]
    with pytest.raises(UserError, match='no trae'):
        updates.fetch_verified_installer(release, tmp_path, public_hex=public, download=download)


def test_signing_script_with_test_key(tmp_path):
    key = tmp_path/'llaves'/'prueba.key'
    out = subprocess.run([sys.executable, str(ROOT/'scripts/firmar_version.py'), '--nueva-llave', str(key)],
                         capture_output=True, text=True, check=True)
    public = out.stdout.strip().splitlines()[-1]
    assert len(bytes.fromhex(public)) == 32
    if sys.platform != 'win32':  # en Windows la protege la ACL de la carpeta del usuario, no el modo POSIX
        assert oct(key.stat().st_mode & 0o777) == '0o600'
    folder = tmp_path/'dist'
    folder.mkdir()
    files = [folder/'TresVizo-Field-PPK-3.0.0-x64.msi', folder/'TresVizo-Field-PPK-3.0.0-macos-arm64.dmg']
    for f in files:
        f.write_bytes(f.name.encode())
    if updates.PUBLIC_KEY_HEX:
        pytest.skip('La app ya trae la llave real: el script rechaza otras llaves (comprobado aparte).')
    subprocess.run([sys.executable, str(ROOT/'scripts/firmar_version.py'), '--llave', str(key), *map(str, files)],
                   capture_output=True, text=True, check=True)
    manifest, signature = (folder/n for n in updates.manifest_names('3.0.0'))
    data = updates.verify_manifest(manifest.read_bytes(), signature.read_bytes(), public)
    assert set(data['archivos']) == {f.name for f in files} and data['version'] == '3.0.0'
    # No se sobrescribe una llave ni se crea dentro del repositorio.
    again = subprocess.run([sys.executable, str(ROOT/'scripts/firmar_version.py'), '--nueva-llave', str(key)],
                           capture_output=True, text=True)
    assert again.returncode != 0


def test_without_key_only_the_download_page():
    """Sin llave de publicación (la distribución de hoy) la app no instala nada: abre la página de la versión."""
    assert updates.PUBLIC_KEY_HEX == ''
    for system, machine in (('win32', 'AMD64'), ('darwin', 'arm64'), ('darwin', 'x86_64'), ('linux', 'x86_64')):
        assert not updates.installs_itself('9.1.0', system=system, machine=machine)
        assert updates.installs_itself('9.1.0', 'ab'*32, system, machine) == (system != 'linux')
    release = updates.parse_release({**release_json('9.1.0'),
                                     'html_url': updates.RELEASES_PAGE+'/tag/v9.1.0'})
    assert updates.download_page(release) == 'https://github.com/cadaritre/TresVizo-Field-PPK/releases/tag/v9.1.0'
    # Una página que no es de este repositorio no se abre: se usa la de la etiqueta.
    foreign = updates.parse_release({**release_json('9.1.0'), 'html_url': 'https://otro.example/descarga'})
    assert updates.download_page(foreign) == updates.RELEASES_PAGE+'/tag/v9.1.0'
    assert updates.download_page(updates.Release('9.1.0', '', '', '', '', '')) == updates.RELEASES_PAGE
