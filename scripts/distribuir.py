"""Arma, comprueba y empaqueta una versión de TresVizo Field PPK para el sistema en que corre.

    python scripts/distribuir.py                 # pruebas, RTKLIB, PyInstaller, autoprueba e instalador
    python scripts/distribuir.py --skip-tests    # solo si esta misma revisión ya pasó las pruebas
    python scripts/distribuir.py --replace-unpublished   # rehacer artefactos locales que aún no se entregaron
    python scripts/distribuir.py --solo-este-equipo      # macOS: probar con un Python que pide un macOS más nuevo

Salida en dist/installer/:
- Windows: TresVizo-Field-PPK-<versión>-x64.msi (WiX 4.0.6, por usuario) y, junto, su «Cómo instalarla»
           (TresVizo-Field-PPK-<versión>-Windows-como-instalarla.txt: SmartScreen y la SHA-256).
- macOS:   TresVizo-Field-PPK-<versión>-macos-<arm64|x86_64>.dmg con la app y «Cómo abrirla.txt» (sin firma), para
           macOS 14 (Apple) o 15 (Intel) en adelante: ningún binario puede pedir más (scripts/macos_minimo.py).
- Linux:   TresVizo-Field-PPK-<versión>-linux-x86_64.tar.gz (carpeta lista para ejecutar).
Cada uno con su .sha256 y un .json con versiones de dependencias, huellas de las fuentes y la autoprueba.

    python scripts/distribuir.py --notas dist/release   # notas de la versión para el release de GitHub

La app no está firmada con un certificado comercial (decisión del propietario): los textos de primera apertura
(macOS 14 y 15+, SmartScreen de Windows) y la SHA-256 de cada instalador van en el DMG, junto al MSI y en las notas.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version as dependency_version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tresvizo_ppk.version import __version__ as VERSION  # noqa: E402

NAME = 'TresVizo-Field-PPK'
APP_BUNDLE = 'TresVizo Field PPK.app'
DEPENDENCIES = ('numpy', 'pyproj', 'matplotlib', 'reportlab', 'Pillow', 'tkinterdnd2', 'pypdfium2', 'pyinstaller',
                'pyinstaller-hooks-contrib')
FIRST_OPEN = ('La app no está firmada con un certificado comercial; la primera vez macOS o Windows piden confirmar que '
              'quieres abrirla.')
MAC_STEPS = """1. Abre la app desde Aplicaciones. macOS avisa que no pudo comprobarla; pulsa «Listo» (o «OK»).
2. Ve a Ajustes del Sistema → Privacidad y seguridad. Abajo, en «Seguridad», aparece
   «Se bloqueó "TresVizo Field PPK"…». Pulsa «Abrir de todos modos», vuelve a pulsar «Abrir de todos modos»
   y confirma con tu contraseña o Touch ID.
   - macOS 15 (Sequoia) o posterior: es la única forma.
   - macOS 14 (Sonoma): también sirve clic derecho (o Control + clic) sobre la app → Abrir → Abrir.
3. Desde entonces se abre normalmente. Con una versión nueva, macOS puede volver a pedirlo una vez."""
WINDOWS_STEPS = """1. Si el navegador avisa que el archivo no se descarga con frecuencia, elige «Conservar» (en Edge: «…» →
   «Conservar» → «Mostrar más» → «Conservar de todos modos»).
2. Abre el MSI. Si aparece «Windows protegió tu PC» (SmartScreen), pulsa «Más información» y después
   «Ejecutar de todas formas».
3. Sigue los pasos del instalador: se instala para tu cuenta, sin permisos de administrador. Para actualizar,
   cierra el programa y abre el MSI nuevo; no hace falta desinstalar."""
MAC_CHECK = """Comprobar que el DMG es el original: su SHA-256 está publicada junto a él (archivo .sha256 y la tabla de las
notas de la versión). En Terminal:
    shasum -a 256 ~/Downloads/{file}
La cifra debe ser exactamente la publicada."""
WINDOWS_CHECK = """Comprobar que el MSI es el original: su SHA-256 está publicada junto a él (archivo .sha256 y la tabla de las
notas de la versión). Abre la carpeta Descargas, escribe cmd en la barra de direcciones y pulsa Entrar; luego:
    certutil -hashfile {file} SHA256
La cifra debe ser exactamente la publicada (certutil la escribe en minúsculas, igual que el .sha256)."""
MAC_HELP = """TresVizo Field PPK {version} para macOS

Instalar: arrastra «TresVizo Field PPK» a la carpeta Aplicaciones.

Abrirla la primera vez. """ + FIRST_OPEN + """

""" + MAC_STEPS + """

""" + MAC_CHECK + """

Tus proyectos, descargas y preferencias quedan fuera de la app: actualizar o borrar la app no los toca.
"""
WINDOWS_HELP = """TresVizo Field PPK {version} para Windows 10 y 11 (64 bits)

Instalarla. """ + FIRST_OPEN + """

""" + WINDOWS_STEPS + """

""" + WINDOWS_CHECK + """

Tus proyectos, descargas y preferencias quedan en tus carpetas: actualizar o desinstalar el programa no los toca.
"""


def run(*args, **kwargs):
    print('$', ' '.join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True, **kwargs)


def arch_name() -> str:
    machine = platform.machine().lower()
    if sys.platform == 'win32':
        return {'amd64': 'x64', 'x86_64': 'x64', 'arm64': 'arm64', 'x86': 'x86', 'i386': 'x86'}.get(machine, machine)
    return {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)


def output_name(local: bool = False) -> Path:
    arch = arch_name()
    folder = ROOT/'dist'/'installer'
    if sys.platform == 'win32':
        return folder/f'{NAME}-{VERSION}-{arch}.msi'
    if sys.platform == 'darwin':
        return folder/f'{NAME}-{VERSION}-macos-{arch}{"-solo-este-equipo" if local else ""}.dmg'
    return folder/f'{NAME}-{VERSION}-linux-{arch}.tar.gz'


def write_version_resource():
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                    VarFileInfo, VarStruct, VSVersionInfo)
    from scripts.generate_msi import version_tuple
    numbers = (*version_tuple(VERSION), 0)
    info = VSVersionInfo(ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3f, flags=0, OS=0x40004,
                                           fileType=1, subtype=0, date=(0, 0)), kids=[
        StringFileInfo([StringTable('0c0a04b0', [StringStruct(k, v) for k, v in {
            'CompanyName': 'TresVizo', 'FileDescription': 'TresVizo Field PPK', 'FileVersion': VERSION,
            'InternalName': NAME, 'OriginalFilename': NAME+'.exe', 'ProductName': 'TresVizo Field PPK',
            'ProductVersion': VERSION, 'LegalCopyright': 'Licencia MIT'}.items()])]),
        VarFileInfo([VarStruct('Translation', [3082, 1200])])])
    (ROOT/'build').mkdir(exist_ok=True)
    (ROOT/'build'/'windows-version.txt').write_text(str(info), encoding='utf-8')


def copy_licenses(destination: Path):
    """Avisos legales de las dependencias de Python incluidas (y de Python mismo) junto al programa."""
    from importlib.metadata import distribution
    destination.mkdir(parents=True, exist_ok=True)
    for name in DEPENDENCIES:
        try:
            package = distribution(name)
        except PackageNotFoundError:
            continue
        target = destination/name
        target.mkdir(exist_ok=True)
        for item in package.files or []:
            if any(part.lower().startswith(('license', 'licence', 'copying', 'notice', 'authors')) for part in
                   item.parts):
                source = Path(package.locate_file(item))
                if source.is_file():
                    out = target/Path(*item.parts[-2:])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, out)
        (target/'version.txt').write_text(f'{name} {package.version}\n', encoding='utf-8')
    for candidate in (Path(sys.base_prefix)/'LICENSE.txt', Path(sys.base_prefix)/'lib'/'python3.13'/'LICENSE.txt'):
        if candidate.exists():
            shutil.copyfile(candidate, destination/'Python-LICENSE.txt')
            break
    shutil.copyfile(ROOT/'LICENSE', destination/'TresVizo-Field-PPK-LICENSE.txt')
    shutil.copyfile(ROOT/'third_party'/'LICENCIAS.md', destination/'LICENCIAS.md')
    shutil.copyfile(ROOT/'assets'/'icons'/'LICENSE.txt', destination/'Lucide-LICENSE.txt')
    # El software de las Convenciones del IERS pide distribuir el código fuente de lo que se deriva de él, con su aviso
    # íntegro (lo lleva el encabezado): la app empaquetada solo trae bytecode, así que va aquí la fuente.
    shutil.copyfile(ROOT/'tresvizo_ppk'/'core'/'larga'/'iers.py', destination/'IERS-convenciones-fuente-iers.py')
    for folder in ('rtklib', 'rnxcmp'):
        for path in (ROOT/'third_party'/folder).glob('*'):
            if path.is_file() and path.suffix.lower() in ('.txt', '.md', '') and path.stat().st_size < 200_000:
                shutil.copyfile(path, destination/f'{folder}-{path.name}')
    if sys.platform == 'win32':
        shutil.copyfile(ROOT/'scripts'/'installer'/'WiX-LICENSE.txt', destination/'WiX-LICENSE.txt')
        (destination/'WiX-source.txt').write_text(
            'WiX Toolset 4.0.6 (MS-RL). Código fuente de las acciones del instalador:\n'
            'https://github.com/wixtoolset/wix/tree/v4.0.6\n', encoding='utf-8')


def make_dmg(app: Path, output: Path):
    with tempfile.TemporaryDirectory(prefix='tvppk-dmg-') as folder:
        stage = Path(folder)/'TresVizo Field PPK'
        stage.mkdir()
        run('ditto', app, stage/APP_BUNDLE)
        os.symlink('/Applications', stage/'Aplicaciones')
        (stage/'Cómo abrirla.txt').write_text(MAC_HELP.format(version=VERSION, file=output.name), encoding='utf-8')
        run('hdiutil', 'create', '-volname', f'TresVizo Field PPK {VERSION}', '-srcfolder', stage, '-ov',
            '-format', 'UDZO', '-fs', 'HFS+', output)


def windows_help_name(version: str = VERSION) -> str:
    """El «Cómo instalarla» que va junto al MSI (en ASCII: GitHub cambia los acentos del nombre de un archivo)."""
    return f'{NAME}-{version}-Windows-como-instalarla.txt'


def write_windows_help(msi: Path, version: str = VERSION) -> Path:
    out = msi.with_name(windows_help_name(version))
    # CRLF y BOM: el Bloc de notas de Windows 10 lo abre bien.
    out.write_bytes(WINDOWS_HELP.format(version=version, file=msi.name).replace('\n', '\r\n').encode('utf-8-sig'))
    return out


def changelog_section(version: str, text: str) -> str:
    """Las novedades de la versión en CHANGELOG.md (lo que va bajo «## <versión> …» hasta la siguiente)."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith('## '):
            if inside:
                break
            inside = line[3:].split()[0:1] == [version]
            continue
        if inside:
            lines.append(line)
    return '\n'.join(lines).strip()


INSTALLER_SUFFIXES = ('.msi', '.dmg', '.tar.gz')


def release_notes(version: str, folder: Path, changelog: str) -> str:
    """Las notas de la versión para GitHub: novedades, cómo abrirla la primera vez (macOS 14 y 15+, Windows con
    SmartScreen) y la SHA-256 de cada instalador de `folder` con cómo comprobarla. Lee los .sha256 que dejó este
    script (los de la CI) y comprueba que cuadran con el archivo."""
    folder = Path(folder)
    rows = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or not path.name.startswith(f'{NAME}-{version}-') or \
                not path.name.endswith(INSTALLER_SUFFIXES) or 'solo-este-equipo' in path.name:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        published = path.with_name(path.name+'.sha256')
        if published.is_file() and published.read_text(encoding='ascii').split()[0] != digest:
            raise SystemExit(f'{published.name} no cuadra con {path.name}: no se publica.')
        rows.append((path.name, digest, path.stat().st_size))
    if not rows:
        raise SystemExit(f'No hay instaladores de la versión {version} en {folder}.')
    mac = next((n for n, _, _ in rows if n.endswith('.dmg')), f'{NAME}-{version}-macos-arm64.dmg')
    win = next((n for n, _, _ in rows if n.endswith('.msi')), f'{NAME}-{version}-x64.msi')
    news = changelog_section(version, changelog) or '- (Sin notas en CHANGELOG.md.)'
    table = '\n'.join(f'| `{name}` | {size/1e6:.1f} MB | `{digest}` |' for name, digest, size in rows)
    return f"""# TresVizo Field PPK {version}

## Novedades

{news}

## Qué archivo bajar

- **Windows 10 y 11 (64 bits):** `{NAME}-{version}-x64.msi`.
- **macOS 14 o posterior en Mac con Apple (M1 o posterior):** `{NAME}-{version}-macos-arm64.dmg`.
- **macOS 15 o posterior en Mac con Intel:** `{NAME}-{version}-macos-x86_64.dmg`.

Tus proyectos, descargas y preferencias se conservan al actualizar.

## Abrirla la primera vez

{FIRST_OPEN}

**macOS**

{MAC_STEPS}

**Windows**

{WINDOWS_STEPS}

## SHA-256 de cada archivo

| Archivo | Tamaño | SHA-256 |
|---|---|---|
{table}

Para comprobar que el archivo es el original, calcula su SHA-256 y compárala con la de la tabla (o la de su
`.sha256`); debe ser exactamente la misma:

- macOS, en Terminal: `shasum -a 256 ~/Downloads/{mac}`
- Windows, en la carpeta Descargas (escribe `cmd` en la barra de direcciones y pulsa Entrar):
  `certutil -hashfile {win} SHA256`
"""


def check_macos_minimum(app: Path, arch: str, local: bool) -> str:
    """Que ningún binario de la app pida un macOS más nuevo que el que declara su Info.plist. Con `local` (solo para
    probar en este equipo) la app declara lo que de verdad piden sus binarios, para que un macOS anterior diga que no
    la puede abrir en vez de cerrarla al arrancar."""
    import plistlib
    from scripts.macos_minimo import MINIMUM, check, version_tuple
    info = app/'Contents'/'Info.plist'
    plist = plistlib.loads(info.read_bytes())
    declared = plist['LSMinimumSystemVersion']
    if declared != MINIMUM[arch]:
        raise SystemExit(f'Info.plist declara macOS {declared} y el mínimo de {arch} es {MINIMUM[arch]}.')
    above = check(app, arch, declared)
    if not above:
        return declared
    if not local:
        lines = '\n'.join(f'  {path} pide macOS {needed}' for path, needed in above[:12])
        more = f'\n  … y {len(above) - 12} más' if len(above) > 12 else ''
        raise SystemExit(f'La app declara macOS {declared} o posterior, pero hay binarios que piden uno más nuevo:\n'
                         f'{lines}{more}\nSuele ser el Python (el de Homebrew está compilado para el macOS del '
                         f'equipo): los DMG que se entregan los arma la CI. Para probar solo en este equipo, '
                         f'--solo-este-equipo.')
    needed = max((n for _, n in above), key=version_tuple)
    plist['LSMinimumSystemVersion'] = needed
    info.write_bytes(plistlib.dumps(plist))
    print(f'Solo para este equipo: la app pide macOS {needed} o posterior (no se entrega).', flush=True)
    return needed


def sources_hash() -> dict:
    paths = [ROOT/'main.py', ROOT/f'{NAME}.spec', ROOT/'requirements.txt', *(ROOT/'app').rglob('*.py'),
             *(ROOT/'tresvizo_ppk').rglob('*.py'), *(ROOT/'scripts').rglob('*.py'), *(ROOT/'scripts'/'installer').glob('*'),
             *(ROOT/'assets').rglob('*')]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if p.is_file() and '__pycache__' not in p.parts}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--skip-tests', action='store_true', help='Solo tras pasar las pruebas en esta revisión.')
    parser.add_argument('--replace-unpublished', action='store_true',
                        help='Reemplazar artefactos locales que todavía no se han entregado.')
    parser.add_argument('--skip-rtklib', action='store_true', help='Usar los binarios ya compilados.')
    parser.add_argument('--notas', type=Path, metavar='CARPETA',
                        help='Solo escribir las notas de la versión (novedades, primera apertura y SHA-256) de los '
                             'instaladores de CARPETA, para pegarlas en el release de GitHub.')
    parser.add_argument('--solo-este-equipo', action='store_true',
                        help='macOS: armar aunque algún binario pida un macOS más nuevo que el mínimo (un Python de '
                             'Homebrew, por ejemplo). La app declara lo que de verdad pide y el DMG no se entrega.')
    args = parser.parse_args()
    if args.notas is not None:
        notes = release_notes(VERSION, args.notas, (ROOT/'CHANGELOG.md').read_text(encoding='utf-8'))
        out = args.notas/f'{NAME}-{VERSION}-notas.md'
        out.write_text(notes, encoding='utf-8')
        print(f'Notas de la versión: {out}')
        return
    if sys.version_info[:2] != (3, 13):
        parser.error('Compila con Python 3.13.')
    local = args.solo_este_equipo and sys.platform == 'darwin'
    output = output_name(local)
    if output.exists() and not args.replace_unpublished:
        parser.error(f'Ya existe {output.name}. Sube tresvizo_ppk/version.py: no se reutiliza una versión entregada.')
    if sys.platform == 'win32':
        wix = subprocess.check_output(['wix', '--version'], text=True).strip()
        if not wix.startswith('4.0.6'):
            parser.error('Usa WiX 4.0.6 (dotnet tool install --global wix --version 4.0.6).')
    if not args.skip_rtklib:
        run(sys.executable, 'scripts/build_rtklib.py')
    if not args.skip_tests:
        run(sys.executable, '-m', 'pytest', '-q')
    if sys.platform == 'win32':
        write_version_resource()
    arch = arch_name()
    dist, work = ROOT/'dist'/arch, ROOT/'build'/arch
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', dist, '--workpath', work,
        f'{NAME}.spec')
    if sys.platform == 'darwin':
        app = dist/APP_BUNDLE
        payload = app/'Contents'/'Resources'
        executable = app/'Contents'/'MacOS'/NAME
    else:
        payload = dist/NAME
        executable = payload/(NAME+('.exe' if sys.platform == 'win32' else ''))
    copy_licenses(payload/'licencias')
    dependencies = {}
    for name in DEPENDENCIES:
        try:
            dependencies[name] = dependency_version(name)
        except PackageNotFoundError:
            pass
    build_info = {'version': VERSION, 'architecture': arch, 'platform': sys.platform,
                  'python': platform.python_version(), 'built_at': datetime.now(timezone.utc).isoformat(),
                  'dependencies': dependencies, 'sources': sources_hash()}
    if sys.platform == 'win32':
        build_info['wix'] = subprocess.check_output(['wix', '--version'], text=True).strip()
    (payload/'build-info.json').write_text(json.dumps(build_info, indent=2), encoding='utf-8')
    if sys.platform == 'darwin':
        build_info['macos_minimum'] = check_macos_minimum(app, arch, local)
        (payload/'build-info.json').write_text(json.dumps(build_info, indent=2), encoding='utf-8')
        # Copiar archivos dentro de la app invalida la firma ad hoc de PyInstaller: se vuelve a firmar (ad hoc).
        run('codesign', '--force', '--deep', '--sign', '-', app)
    report = ROOT/'build'/f'autoprueba-{arch}.json'
    run(executable, '--self-test', report)
    checked = json.loads(report.read_text(encoding='utf-8'))
    if not checked['ok'] or checked['version'] != VERSION:
        raise SystemExit('La autoprueba del programa empaquetado falló: '+', '.join(checked['failed']))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    if sys.platform == 'win32':
        from scripts.generate_msi import build_msi
        manifest = build_msi(payload, output, arch)
        write_windows_help(output)
    elif sys.platform == 'darwin':
        make_dmg(app, output)
        manifest = {'file': output.name, 'version': VERSION, 'architecture': arch}
    else:
        with tarfile.open(output, 'w:gz') as archive:
            archive.add(payload, arcname=f'{NAME}-{VERSION}')
        manifest = {'file': output.name, 'version': VERSION, 'architecture': arch}
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest.update(sha256=digest, bytes=output.stat().st_size, signed=False, distributable=not local,
                    build_info=build_info, self_test=checked)
    output.with_name(output.name+'.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                                     encoding='utf-8')
    output.with_name(output.name+'.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
    print(f'Instalador listo y comprobado: {output}')


if __name__ == '__main__':
    main()
