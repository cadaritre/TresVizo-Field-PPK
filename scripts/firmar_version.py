"""Firma una versión para las actualizaciones de la app (§3.5). Lo corre el propietario en su equipo, nunca la CI.

    # Una sola vez: crear la llave de publicación (privada en un lugar seguro, fuera del repo)
    python scripts/firmar_version.py --nueva-llave ~/Llaves/tresvizo-field-ppk-publicacion.key

    # En cada versión: manifiesto con el SHA-256 de cada instalador y su firma Ed25519
    python scripts/firmar_version.py --llave ~/Llaves/tresvizo-field-ppk-publicacion.key \
        dist/release/TresVizo-Field-PPK-0.2.0-x64.msi dist/release/TresVizo-Field-PPK-0.2.0-macos-arm64.dmg ...

Deja `TresVizo-Field-PPK-<versión>-manifiesto.json` y `….json.sig` junto a los instaladores: se suben al release de
GitHub con ellos. La app comprueba la firma con la llave pública de `app/updates.py` (PUBLIC_KEY_HEX).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import updates  # noqa: E402
from tresvizo_ppk.datos import ed25519  # noqa: E402


def read_secret(path: Path) -> bytes:
    text = path.read_text(encoding='ascii').strip()
    secret = bytes.fromhex(text)
    if len(secret) != 32:
        raise SystemExit('La llave privada debe ser de 32 bytes en hexadecimal.')
    return secret


def new_key(path: Path) -> None:
    path = path.expanduser()
    if path.exists():
        raise SystemExit(f'{path} ya existe: no se sobrescribe una llave.')
    if ROOT in path.resolve().parents:
        raise SystemExit('La llave privada no puede quedar dentro del repositorio.')
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='ascii') as out:
        out.write(secret.hex()+'\n')
    public = ed25519.public_key(secret).hex()
    acceso = 'solo tu usuario puede leerla' if os.name == 'posix' else 'guárdala dentro de tu carpeta de usuario'
    print(f'Llave privada guardada en {path} ({acceso}). Respáldala fuera de este equipo.')
    print(f'Llave pública (ponla en app/updates.py, PUBLIC_KEY_HEX):\n{public}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--nueva-llave', type=Path, help='Crea la llave de publicación en esta ruta y termina.')
    parser.add_argument('--llave', type=Path, help='Llave privada (32 bytes en hexadecimal).')
    parser.add_argument('instaladores', nargs='*', type=Path)
    args = parser.parse_args()
    if args.nueva_llave:
        new_key(args.nueva_llave)
        return
    if not args.llave or not args.instaladores:
        parser.error('Indica --llave y los instaladores de la versión.')
    versions = set()
    for path in args.instaladores:
        match = re.match(rf'{updates.NAME}-(\d+\.\d+\.\d+)-', path.name)
        if not match or not path.is_file():
            parser.error(f'«{path}» no es un instalador de TresVizo Field PPK.')
        versions.add(match.group(1))
    if len(versions) != 1:
        parser.error('Todos los instaladores deben ser de la misma versión.')
    version = versions.pop()
    secret = read_secret(args.llave.expanduser())
    public = ed25519.public_key(secret).hex()
    if updates.PUBLIC_KEY_HEX and public != updates.PUBLIC_KEY_HEX:
        raise SystemExit('Esa llave no es la de publicación que trae la app (PUBLIC_KEY_HEX).')
    manifest = updates.build_manifest(version, args.instaladores, datetime.now(timezone.utc).date().isoformat())
    signature = updates.sign_manifest(manifest, secret)
    if updates.verify_manifest(manifest, signature, public)['version'] != version:
        raise SystemExit('La comprobación de la firma recién hecha falló.')
    folder = args.instaladores[0].parent
    manifest_name, signature_name = updates.manifest_names(version)
    (folder/manifest_name).write_bytes(manifest)
    (folder/signature_name).write_bytes(signature)
    print(f'Firmado {manifest_name} ({len(args.instaladores)} instaladores) con la llave {public[:16]}…')


if __name__ == '__main__':
    main()
