"""MSI por usuario de TresVizo Field PPK (adaptado de scripts/generate_msi.py de TresVizo Med, MIT).

Identidad estable, lista explícita de archivos, asociación de los proyectos .tvppk y los datos del usuario intactos
(proyectos, descargas y preferencias viven fuera de la carpeta del programa). Requiere WiX 4.0.6 en Windows.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tresvizo_ppk.version import __version__ as VERSION  # noqa: E402

# Identidad permanente de esta línea de producto (propia, distinta de la de TresVizo Med). NO cambiar nunca.
UPGRADE_CODE = uuid.UUID('2e205de5-69bc-4cc1-b6bd-457e39e5239c')
REGISTRY_KEY = r'Software\TresVizo\FieldPPK'
EXE_NAME = 'TresVizo-Field-PPK.exe'
NS = 'http://wixtoolset.org/schemas/v4/wxs'
ET.register_namespace('', NS)


def version_tuple(version=VERSION):
    parts = version.split('.')
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError('La versión debe tener tres números: mayor.menor.corrección.')
    major, minor, patch = (int(p) for p in parts)
    if major > 255 or minor > 255 or patch > 65535:
        raise ValueError('Windows Installer admite hasta 255.255.65535.')
    return major, minor, patch


def element(parent, tag, **attributes):
    return ET.SubElement(parent, '{'+NS+'}'+tag, attributes)


def stable_id(prefix, name):
    return prefix+hashlib.sha256(name.encode()).hexdigest()[:32]


def guid(family, name):
    return str(uuid.uuid5(family, name)).upper()


def payload_xml(payload, family, arch):
    payload = Path(payload).resolve()
    files = sorted(p for p in payload.rglob('*') if p.is_file())
    if not (payload/EXE_NAME).is_file():
        raise ValueError('Falta el ejecutable compilado.')
    allowed_roots = {EXE_NAME, '_internal', 'licencias', 'build-info.json'}
    if any(p.relative_to(payload).parts[0] not in allowed_roots or p.is_symlink() for p in files):
        raise ValueError('La distribución contiene archivos ajenos al paquete permitido.')
    wix = ET.Element('{'+NS+'}Wix')
    fragment = element(wix, 'Fragment')
    base = element(fragment, 'DirectoryRef', Id='INSTALLFOLDER')
    directories = {Path('.'): (base, 'INSTALLFOLDER')}
    for directory in sorted({p.relative_to(payload).parent for p in files}, key=lambda p: (len(p.parts), p.as_posix())):
        for ancestor in [*reversed(directory.parents), directory]:
            if ancestor in directories:
                continue
            parent = directories[ancestor.parent][0]
            identifier = stable_id('D', ancestor.as_posix())
            directories[ancestor] = (element(parent, 'Directory', Id=identifier, Name=ancestor.name), identifier)
    group = element(fragment, 'ComponentGroup', Id='AppFiles')
    cleaned = set()
    for path in files:
        relative = path.relative_to(payload)
        name = relative.as_posix()
        identifier = stable_id('C', name)
        component = element(group, 'Component', Id=identifier, Directory=directories[relative.parent][1],
                            Guid=guid(family, arch+'/file/'+name), Bitness='always64' if arch == 'x64' else 'always32')
        # Componentes en el perfil del usuario: la clave es un valor de HKCU (ICE38).
        element(component, 'File', Id=stable_id('F', name), Source=str(path), KeyPath='no')
        element(component, 'RegistryValue', Root='HKCU', Key=REGISTRY_KEY+r'\Files', Name=identifier,
                Type='integer', Value='1', KeyPath='yes')
        for directory in (relative.parent, *relative.parent.parents):
            if directory == Path('.') or directory in cleaned:
                continue
            cleaned.add(directory)
            element(component, 'RemoveFolder', Id=stable_id('R', directory.as_posix()),
                    Directory=directories[directory][1], On='uninstall')
    ET.indent(wix)
    return ET.tostring(wix, encoding='unicode')


def rtf(text):
    escaped = ''.join(('\\'+c if c in '\\{}' else '\\par\n' if c == '\n' else c if ord(c) < 128 else
                       '\\u'+str(ord(c))+'?') for c in text)
    return '{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Segoe UI;}}\\f0\\fs20 '+escaped+'}'


def installer_images(work, version):
    """Paneles del asistente de Windows con la marca, sin modificar los recursos."""
    import os
    from PIL import Image, ImageDraw, ImageFont
    fonts = Path(os.environ.get('WINDIR', r'C:\Windows'))/'Fonts'

    def font(name, size):
        try:
            return ImageFont.truetype(str(fonts/name), size)
        except OSError:
            return ImageFont.load_default()
    panel = Image.new('RGB', (493, 312), 'white')
    draw = ImageDraw.Draw(panel)
    draw.rectangle((0, 0, 163, 311), fill='#EDF4FB')
    draw.rectangle((0, 0, 163, 7), fill='#1F5EAD')
    with Image.open(ROOT/'assets/marca.png') as source:
        logo = source.convert('RGBA')
    logo.thumbnail((110, 110), Image.Resampling.LANCZOS)
    panel.paste(logo, ((164-logo.width)//2, 46), logo)
    draw.text((18, 176), 'TresVizo', fill='#163F73', font=font('seguisb.ttf', 26))
    draw.text((18, 212), 'Field PPK', fill='#163F73', font=font('segoeui.ttf', 19))
    draw.text((18, 276), version, fill='#405A70', font=font('segoeui.ttf', 13))
    panel.save(work/'Dialog.bmp')
    banner = Image.new('RGB', (493, 58), 'white')
    logo.thumbnail((44, 44), Image.Resampling.LANCZOS)
    banner.paste(logo, (437, 7), logo)
    ImageDraw.Draw(banner).rectangle((0, 56, 492, 57), fill='#1F5EAD')
    banner.save(work/'Banner.bmp')


def build_msi(payload, output, arch, version=VERSION):
    version_tuple(version)
    if arch not in ('x86', 'x64', 'arm64'):
        raise ValueError('Arquitectura inválida.')
    payload, output = Path(payload).resolve(), Path(output).resolve()
    info = json.loads((payload/'build-info.json').read_text(encoding='utf-8'))
    if info['architecture'] != arch or info['version'] != version:
        raise ValueError('La versión o arquitectura del ejecutable no coincide con el MSI.')
    family = UPGRADE_CODE
    work = ROOT/'build'/'msi'/arch/version
    work.mkdir(parents=True, exist_ok=True)
    installer_images(work, version)
    output.parent.mkdir(parents=True, exist_ok=True)
    (work/'Payload.wxs').write_text(payload_xml(payload, family, arch), encoding='utf-8')
    intro = (f'TresVizo Field PPK {version}\n\nSe instala para esta cuenta de Windows, sin permisos de administrador. '
             'Los proyectos, las descargas y las preferencias quedan en tus carpetas y se conservan al actualizar o '
             'desinstalar.\n\nGuarda tu trabajo y cierra la aplicación antes de continuar.\n\n')
    (work/'License.rtf').write_text(rtf(intro+(ROOT/'LICENSE').read_text(encoding='utf-8')), encoding='ascii')
    definitions = {'ProductName': 'TresVizo Field PPK', 'InstallName': 'TresVizo-Field-PPK', 'Version': version,
                   'Architecture': arch, 'UpgradeCode': str(family), 'ProductCode': guid(family, arch+'/product/'+version),
                   'RegistryKey': REGISTRY_KEY, 'ExeName': EXE_NAME, 'SettingsGuid': guid(family, 'settings'),
                   'ShortcutsGuid': guid(family, 'shortcuts'), 'AssociationGuid': guid(family, 'association'),
                   'IconPath': str(ROOT/'assets/marca.ico'), 'LicenseRtf': str(work/'License.rtf'),
                   'DialogBmp': str(work/'Dialog.bmp'), 'BannerBmp': str(work/'Banner.bmp')}
    command = ['wix', 'build', str(ROOT/'scripts/installer/Product.wxs'), str(work/'Payload.wxs'),
               '-arch', arch, '-culture', 'es-es', '-ext', 'WixToolset.UI.wixext/4.0.6',
               '-ext', 'WixToolset.Util.wixext/4.0.6', '-cc', str(ROOT/'build/msi-cab-cache'),
               '-intermediatefolder', str(work/'obj'), '-out', str(output)]
    for key, value in definitions.items():
        command.extend(['-d', key+'='+value])
    subprocess.run(command, cwd=ROOT, check=True)
    return {'file': output.name, 'version': version, 'architecture': arch,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size,
            'upgrade_code': str(family), 'product_code': definitions['ProductCode']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--arch', choices=['x64', 'x86', 'arm64'], required=True)
    parser.add_argument('--version', default=VERSION)
    args = parser.parse_args()
    print(json.dumps(build_msi(args.payload, args.output, args.arch, args.version), indent=2))
