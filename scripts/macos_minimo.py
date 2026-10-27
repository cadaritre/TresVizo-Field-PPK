"""Versión mínima de macOS del programa empaquetado: la que se declara y la que piden de verdad sus binarios.

La fija la dependencia más exigente. pyproj 3.8.0, fijada para que los sistemas coincidan con los vectores de las
apps (`requirements.txt`), solo publica ruedas para macOS 14 en Apple (arm64) y macOS 15 en Intel (x86_64). Nada
del paquete puede pedir más que lo declarado: si un binario pide un macOS más nuevo, la app se cerraría al abrirla
en un equipo que el instalador dice que sirve. `check()` lee la versión mínima de cada binario Mach-O de la app
(LC_BUILD_VERSION o LC_VERSION_MIN_MACOSX) y `scripts/distribuir.py` no arma el DMG si alguno pasa de lo declarado.
"""
from __future__ import annotations

import os
from pathlib import Path
import struct

MINIMUM = {'arm64': '14.0', 'x86_64': '15.0'}

_FAT_MAGIC, _FAT_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF
_MH_MAGIC, _MH_MAGIC_64 = 0xFEEDFACE, 0xFEEDFACF
_CPU = {'x86_64': 0x01000007, 'arm64': 0x0100000C}
_LC_VERSION_MIN_MACOSX, _LC_BUILD_VERSION = 0x24, 0x32
_PLATFORM_MACOS = 1


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split('.'))


def _decode(packed: int) -> str:
    """xxxx.yy.zz en un entero de 32 bits; el parche se omite si es cero, como lo escribe Apple."""
    major, minor, patch = packed >> 16, (packed >> 8) & 0xFF, packed & 0xFF
    return f'{major}.{minor}' + (f'.{patch}' if patch else '')


def _thin_minos(data: bytes, offset: int) -> tuple[int, str | None]:
    """(cputype, versión mínima de macOS) de un Mach-O sin capas; versión None si no la declara."""
    magic = struct.unpack_from('<I', data, offset)[0]
    if magic not in (_MH_MAGIC, _MH_MAGIC_64):
        raise ValueError('no es un Mach-O')
    cputype, _, _, ncmds, _ = struct.unpack_from('<iiIII', data, offset+4)
    position = offset + (32 if magic == _MH_MAGIC_64 else 28)
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, position)
        if size < 8:
            raise ValueError('orden de carga dañada')
        if cmd == _LC_BUILD_VERSION:
            platform, minos = struct.unpack_from('<II', data, position+8)
            if platform == _PLATFORM_MACOS:
                return cputype & 0xFFFFFFFF, _decode(minos)
        elif cmd == _LC_VERSION_MIN_MACOSX:
            return cputype & 0xFFFFFFFF, _decode(struct.unpack_from('<I', data, position+8)[0])
        position += size
    return cputype & 0xFFFFFFFF, None


def minimum_of(data: bytes, arch: str) -> str | None:
    """Versión mínima de macOS que pide un binario Mach-O para `arch`, o None si no es Mach-O, no la declara o no
    trae esa arquitectura. En un binario universal cuenta la parte de esa arquitectura."""
    if len(data) < 32:
        return None
    big = struct.unpack_from('>I', data, 0)[0]
    try:
        if big in (_FAT_MAGIC, _FAT_MAGIC_64):
            count = struct.unpack_from('>I', data, 4)[0]
            entry = 32 if big == _FAT_MAGIC_64 else 20
            for i in range(count):
                cputype = struct.unpack_from('>I', data, 8+i*entry)[0]
                offset = (struct.unpack_from('>Q', data, 8+i*entry+8)[0] if big == _FAT_MAGIC_64
                          else struct.unpack_from('>I', data, 8+i*entry+8)[0])
                if cputype == _CPU[arch]:
                    return _thin_minos(data, offset)[1]
            return None
        if struct.unpack_from('<I', data, 0)[0] in (_MH_MAGIC, _MH_MAGIC_64):
            cputype, minos = _thin_minos(data, 0)
            return minos if cputype == _CPU[arch] else None   # de otra arquitectura: no se carga en esta
    except (struct.error, ValueError):
        return None
    return None


def check(bundle: Path, arch: str, declared: str | None = None) -> list[tuple[Path, str]]:
    """Los binarios de `bundle` que piden un macOS más nuevo que `declared` (por omisión, el mínimo de `arch`)."""
    limit = version_tuple(declared or MINIMUM[arch])
    above = []
    for folder, _, files in os.walk(bundle):
        for name in files:
            path = Path(folder)/name
            if path.is_symlink() or not path.is_file():
                continue
            with path.open('rb') as handle:
                head = handle.read(4)
                if len(head) < 4 or struct.unpack('>I', head)[0] not in (_FAT_MAGIC, _FAT_MAGIC_64) and \
                        struct.unpack('<I', head)[0] not in (_MH_MAGIC, _MH_MAGIC_64):
                    continue
                data = head + handle.read()
            needed = minimum_of(data, arch)
            if needed and version_tuple(needed) > limit:
                above.append((path.relative_to(bundle), needed))
    return sorted(above, key=lambda item: (version_tuple(item[1]), str(item[0])), reverse=True)
