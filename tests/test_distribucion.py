"""Versión mínima de macOS de la app empaquetada (scripts/macos_minimo.py y la comprobación de scripts/distribuir.py)."""
from __future__ import annotations

import plistlib
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import distribuir, macos_minimo  # noqa: E402

CPU_ARM64, CPU_X86_64 = 0x0100000C, 0x01000007


def packed(version: str) -> int:
    parts = [int(p) for p in version.split('.')] + [0, 0]
    return (parts[0] << 16) | (parts[1] << 8) | parts[2]


def macho(cputype: int, minos: str | None = None, legacy: bool = False) -> bytes:
    """Un Mach-O de 64 bits mínimo: cabecera, una orden de carga cualquiera y la de la versión mínima."""
    commands = struct.pack('<II', 0x19, 16) + b'\0'*8                       # LC_SEGMENT_64 recortada
    if minos and legacy:
        commands += struct.pack('<IIII', 0x24, 16, packed(minos), packed(minos))   # LC_VERSION_MIN_MACOSX
    elif minos:
        commands += struct.pack('<IIIIII', 0x32, 24, 1, packed(minos), packed(minos), 0)  # LC_BUILD_VERSION
    ncmds = 1 + (1 if minos else 0)
    header = struct.pack('<IiiIIII', 0xFEEDFACF, cputype, 0, 6, ncmds, len(commands), 0) + b'\0'*4
    return header + commands


def fat(*slices: tuple[int, bytes]) -> bytes:
    head = struct.pack('>II', 0xCAFEBABE, len(slices))
    offset, body, entries = 4096, b'', b''
    for cputype, data in slices:
        entries += struct.pack('>IiIII', cputype, 0, offset + len(body), len(data), 12)
        body += data
    return (head + entries).ljust(4096, b'\0') + body


def test_minimo_de_un_binario():
    assert macos_minimo.minimum_of(macho(CPU_ARM64, '14.0'), 'arm64') == '14.0'
    assert macos_minimo.minimum_of(macho(CPU_X86_64, '10.13', legacy=True), 'x86_64') == '10.13'
    assert macos_minimo.minimum_of(macho(CPU_X86_64, '10.13.4'), 'x86_64') == '10.13.4'
    assert macos_minimo.minimum_of(macho(CPU_ARM64), 'arm64') is None
    # Uno de otra arquitectura no se carga en esta: no cuenta.
    assert macos_minimo.minimum_of(macho(CPU_X86_64, '15.0'), 'arm64') is None
    assert macos_minimo.minimum_of(b'#!/bin/sh\necho hola\n' + b'\0'*40, 'arm64') is None
    assert macos_minimo.minimum_of(macho(CPU_ARM64, '14.0')[:40], 'arm64') is None   # cortado: no revienta


def test_binario_universal_cuenta_su_arquitectura():
    data = fat((CPU_X86_64, macho(CPU_X86_64, '15.0')), (CPU_ARM64, macho(CPU_ARM64, '11.0')))
    assert macos_minimo.minimum_of(data, 'x86_64') == '15.0'
    assert macos_minimo.minimum_of(data, 'arm64') == '11.0'
    assert macos_minimo.minimum_of(fat((CPU_X86_64, macho(CPU_X86_64, '15.0'))), 'arm64') is None


def test_check_encuentra_lo_que_pasa_del_minimo(tmp_path):
    lib = tmp_path/'Contents'/'Frameworks'
    lib.mkdir(parents=True)
    (lib/'nuevo.so').write_bytes(macho(CPU_ARM64, '26.0'))
    (lib/'medio.dylib').write_bytes(macho(CPU_ARM64, '15.0'))
    (lib/'bien.so').write_bytes(macho(CPU_ARM64, '14.0'))
    (lib/'otro.dylib').write_bytes(macho(CPU_X86_64, '26.0'))
    (lib/'datos.txt').write_text('no es un binario')
    (lib/'enlace.so').symlink_to(lib/'nuevo.so')
    assert macos_minimo.check(tmp_path, 'arm64') == [(Path('Contents/Frameworks/nuevo.so'), '26.0'),
                                                     (Path('Contents/Frameworks/medio.dylib'), '15.0')]
    assert macos_minimo.check(tmp_path, 'arm64', declared='26.0') == []


def bundle(tmp_path: Path, needed: str) -> Path:
    app = tmp_path/'TresVizo Field PPK.app'
    (app/'Contents'/'MacOS').mkdir(parents=True)
    (app/'Contents'/'Info.plist').write_bytes(plistlib.dumps({'LSMinimumSystemVersion': '14.0'}))
    (app/'Contents'/'MacOS'/'TresVizo-Field-PPK').write_bytes(macho(CPU_ARM64, needed))
    return app


def test_distribuir_no_arma_un_dmg_que_no_abriria(tmp_path):
    app = bundle(tmp_path, '26.0')
    with pytest.raises(SystemExit, match='pide macOS 26.0'):
        distribuir.check_macos_minimum(app, 'arm64', local=False)
    assert distribuir.check_macos_minimum(bundle(tmp_path/'bien', '14.0'), 'arm64', local=False) == '14.0'


def test_solo_este_equipo_declara_lo_que_de_verdad_pide(tmp_path):
    app = bundle(tmp_path, '26.0')
    assert distribuir.check_macos_minimum(app, 'arm64', local=True) == '26.0'
    assert plistlib.loads((app/'Contents'/'Info.plist').read_bytes())['LSMinimumSystemVersion'] == '26.0'
    assert distribuir.output_name(local=True).name.endswith('-solo-este-equipo.dmg') or sys.platform != 'darwin'


def test_el_minimo_es_el_de_pyproj_fijada():
    """pyproj 3.8.0 solo trae ruedas para macOS 14 (arm64) y 15 (x86_64). Si cambia la versión fijada, hay que revisar
    MINIMUM con las ruedas de la nueva (y lo que dice docs/distribucion.md)."""
    pinned = re.search(r'^pyproj==(\S+)', (ROOT/'requirements.txt').read_text(), re.M).group(1)
    assert pinned == '3.8.0'
    assert macos_minimo.MINIMUM == {'arm64': '14.0', 'x86_64': '15.0'}


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('vtool'), reason='vtool es de las herramientas de Apple')
def test_lee_igual_que_vtool():
    arch = 'arm64' if 'arm64' in subprocess.check_output(['uname', '-m'], text=True) else 'x86_64'
    binary = Path(sys.executable).resolve()
    out = subprocess.run(['vtool', '-arch', arch, '-show-build', str(binary)], capture_output=True, text=True).stdout
    expected = next((line.split()[1] for line in out.splitlines()
                     if line.strip().startswith(('minos', 'version'))), None)
    assert macos_minimo.minimum_of(binary.read_bytes(), arch) == expected


def test_las_licencias_llevan_la_fuente_del_iers(tmp_path):
    """La licencia del software de las Convenciones del IERS pide la fuente de lo derivado y su aviso íntegro."""
    distribuir.copy_licenses(tmp_path)
    fuente = (tmp_path/'IERS-convenciones-fuente-iers.py').read_text(encoding='utf-8')
    assert 'IERS Conventions Software License' in fuente and 'def ' in fuente
