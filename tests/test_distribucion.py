"""Versión mínima de macOS de la app empaquetada (scripts/macos_minimo.py y la comprobación de scripts/distribuir.py)."""
from __future__ import annotations

import hashlib
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


# --- Distribución sin firma: primera apertura y SHA-256 (decisión del propietario, 27-10-2026) ---------------------

def test_textos_de_primera_apertura():
    mac = distribuir.MAC_HELP.format(version='1.2.3', file='TresVizo-Field-PPK-1.2.3-macos-arm64.dmg')
    assert 'macOS 15 (Sequoia) o posterior' in mac and 'macOS 14 (Sonoma)' in mac
    assert 'Abrir de todos modos' in mac and 'Privacidad y seguridad' in mac
    assert 'shasum -a 256 ~/Downloads/TresVizo-Field-PPK-1.2.3-macos-arm64.dmg' in mac
    assert 'no está firmada con un certificado comercial' in mac
    win = distribuir.WINDOWS_HELP.format(version='1.2.3', file='TresVizo-Field-PPK-1.2.3-x64.msi')
    for words in ('Windows protegió tu PC', 'Más información', 'Ejecutar de todas formas',
                  'certutil -hashfile TresVizo-Field-PPK-1.2.3-x64.msi SHA256'):
        assert words in win
    # Sin culpa ni miedo.
    for text in (mac, win):
        assert not re.search(r'peligro|riesgo|malicios|lo sentimos|todavía no', text, re.IGNORECASE)


def test_como_instalarla_junto_al_msi(tmp_path):
    msi = tmp_path/'TresVizo-Field-PPK-1.2.3-x64.msi'
    msi.write_bytes(b'msi')
    out = distribuir.write_windows_help(msi, '1.2.3')
    assert out.name == 'TresVizo-Field-PPK-1.2.3-Windows-como-instalarla.txt' and out.name.isascii()
    data = out.read_bytes()
    assert data.startswith(b'\xef\xbb\xbf') and b'\r\n' in data and b'\n\n' not in data.replace(b'\r\n', b'')
    assert 'Ejecutar de todas formas' in data.decode('utf-8-sig')


def test_notas_de_la_version_con_la_sha256(tmp_path):
    changelog = '# Cambios\n\n## 1.2.3 — 2026-11-02\n- Opciones del DXF.\n\n## 1.2.2 — 2026-10-30\n- Otra cosa.\n'
    assert distribuir.changelog_section('1.2.3', changelog) == '- Opciones del DXF.'
    files = {}
    for name in ('TresVizo-Field-PPK-1.2.3-x64.msi', 'TresVizo-Field-PPK-1.2.3-macos-arm64.dmg',
                 'TresVizo-Field-PPK-1.2.3-macos-x86_64.dmg'):
        (tmp_path/name).write_bytes(name.encode()*1000)
        digest = hashlib.sha256((tmp_path/name).read_bytes()).hexdigest()
        (tmp_path/(name+'.sha256')).write_text(f'{digest}  {name}\n', encoding='ascii')
        files[name] = digest
    (tmp_path/'TresVizo-Field-PPK-1.2.2-x64.msi').write_bytes(b'vieja')
    (tmp_path/'TresVizo-Field-PPK-1.2.3-macos-arm64-solo-este-equipo.dmg').write_bytes(b'no se entrega')
    notes = distribuir.release_notes('1.2.3', tmp_path, changelog)
    for name, digest in files.items():
        assert f'| `{name}` |' in notes and digest in notes
    assert '1.2.2-x64' not in notes and 'solo-este-equipo' not in notes and 'Otra cosa' not in notes
    for words in ('- Opciones del DXF.', 'Windows protegió tu PC', 'Ejecutar de todas formas', 'macOS 15 (Sequoia)',
                  'shasum -a 256 ~/Downloads/TresVizo-Field-PPK-1.2.3-macos-arm64.dmg',
                  'certutil -hashfile TresVizo-Field-PPK-1.2.3-x64.msi SHA256'):
        assert words in notes
    # Un .sha256 que no cuadra con su archivo no se publica.
    (tmp_path/'TresVizo-Field-PPK-1.2.3-x64.msi').write_bytes(b'alterado')
    with pytest.raises(SystemExit, match='no cuadra'):
        distribuir.release_notes('1.2.3', tmp_path, changelog)


def test_dmg_reintenta_si_hdiutil_esta_ocupado(tmp_path, monkeypatch):
    """«hdiutil: create failed - Resource busy» (visto en la CI de GitHub) se reintenta; al quinto fallo se rinde."""
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        if args[0] == 'hdiutil' and calls.count('hdiutil') < 3:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(distribuir, 'run', fake_run)
    monkeypatch.setattr(distribuir.time, 'sleep', lambda s: None)
    distribuir.make_dmg(tmp_path/'App.app', tmp_path/'salida.dmg')
    assert calls.count('hdiutil') == 3

    calls.clear()
    monkeypatch.setattr(distribuir, 'run', lambda *a, **k: (calls.append(a[0]), (_ for _ in ()).throw(
        subprocess.CalledProcessError(1, a)) if a[0] == 'hdiutil' else None))
    with pytest.raises(subprocess.CalledProcessError):
        distribuir.make_dmg(tmp_path/'App.app', tmp_path/'salida.dmg')
    assert calls.count('hdiutil') == 5
