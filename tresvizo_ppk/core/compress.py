"""Descomprimir lo que entregan el INEGI, la IGS y los servidores de productos: `.zip`, `.gz`, `.Z` (LZW de `compress`)
y Hatanaka (`.crx`, `.YYd`, con `crx2rnx`), anidados en cualquier orden (`.crx.gz`, `.zip` con `.d.Z` dentro…).

`unpack(paths, outdir)` deja en `outdir` los archivos de texto o binarios ya descomprimidos y devuelve sus rutas.
"""
from __future__ import annotations

import gzip
import io
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

from ..errors import UserError
from . import tools

GZIP_MAGIC = b"\x1f\x8b"
LZW_MAGIC = b"\x1f\x9d"
ZIP_MAGIC = b"PK\x03\x04"
MAX_UNPACKED_BYTES = 8 << 30          # defensa contra bombas de descompresión


def lzw_decompress(data: bytes) -> bytes:
    """Descomprime el formato de `compress` de Unix (.Z: LZW de 9 a 16 bits por código, con modo de bloques).

    Sigue a ncompress: al cambiar el ancho de código o con CLEAR, el lector salta al final del grupo de
    `n_bits` bytes en curso (los códigos se escriben en grupos de 8).
    """
    if len(data) < 3 or data[:2] != LZW_MAGIC:
        raise ValueError("no es un archivo .Z")
    flags = data[2]
    max_bits = flags & 0x1F
    block_mode = bool(flags & 0x80)
    if max_bits < 9 or max_bits > 16:
        raise ValueError(f".Z con {max_bits} bits por código")
    first_free = 257 if block_mode else 256
    max_max_code = 1 << max_bits
    out = bytearray()
    table: list[bytes] = [bytes([i]) for i in range(256)] + ([b""] if block_mode else [])
    n_bits = 9
    max_code = (1 << n_bits) - 1
    total_bits = len(data) * 8
    bitpos = 24
    group_start = bitpos
    previous: bytes | None = None
    as_int = int.from_bytes

    def align(pos: int, start: int, width: int) -> int:
        group = width * 8
        return start + -(-(pos - start) // group) * group

    while True:
        if len(table) > max_code and n_bits < max_bits:
            bitpos = align(bitpos, group_start, n_bits)
            group_start = bitpos
            n_bits += 1
            max_code = (1 << n_bits) - 1 if n_bits < max_bits else max_max_code
        if bitpos + n_bits > total_bits:
            break
        byte = bitpos >> 3
        chunk = as_int(data[byte:byte + 3], "little")
        code = (chunk >> (bitpos & 7)) & ((1 << n_bits) - 1)
        bitpos += n_bits
        if block_mode and code == 256:
            bitpos = align(bitpos, group_start, n_bits)
            group_start = bitpos
            del table[first_free:]
            n_bits = 9
            max_code = (1 << n_bits) - 1
            previous = None
            continue
        if code < len(table):
            entry = table[code]
            if previous is not None and len(table) < max_max_code:
                table.append(previous + entry[:1])
        elif code == len(table) and previous is not None:
            entry = previous + previous[:1]
            table.append(entry)
        else:
            raise ValueError("archivo .Z dañado")
        out += entry
        if len(out) > MAX_UNPACKED_BYTES:
            raise ValueError("archivo .Z demasiado grande")
        previous = entry
    return bytes(out)


def _strip_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in (".gz", ".z", ".zip"):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _hatanaka_name(name: str) -> str:
    """Nombre del RINEX restaurado: .crx → .rnx; .YYd → .YYo."""
    lower = name.lower()
    if lower.endswith(".crx"):
        return name[:-4] + ".rnx"
    if len(lower) >= 4 and lower[-4] == "." and lower[-3:-1].isdigit() and lower[-1] == "d":
        return name[:-1] + ("O" if name[-1].isupper() else "o")
    return name + ".rnx"


def is_hatanaka(head: bytes) -> bool:
    return head[:80].rstrip().endswith(b"CRINEX VERS   / TYPE") or b"CRINEX VERS" in head[:80]


def crx2rnx(source: Path, dest: Path) -> None:
    exe = tools.find_tool("crx2rnx")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    with open(source, "rb") as fin, open(dest, "wb") as fout:
        proc = subprocess.run([str(exe), "-"], stdin=fin, stdout=fout, stderr=subprocess.PIPE,
                              creationflags=creationflags)
    if proc.returncode not in (0, 2):     # 2 = avisos (el archivo se restauró)
        dest.unlink(missing_ok=True)
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise UserError(f"No se pudo restaurar el archivo Hatanaka «{source.name}»"
                        + (f": {detail[-1]}" if detail else "."))


def _unique(outdir: Path, name: str) -> Path:
    path = outdir / name
    k = 2
    while path.exists():
        stem, dot, ext = name.partition(".")
        path = outdir / f"{stem}_{k}{dot}{ext}"
        k += 1
    return path


def unpack(paths: list[Path], outdir: Path, cancel: threading.Event | None = None) -> list[Path]:
    """Descomprime todo lo que haga falta y devuelve los archivos finales (en `outdir`)."""
    outdir.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    for path in paths:
        tools.check(cancel)
        result.extend(_unpack_one(Path(path), outdir, cancel, depth=0))
    return result


def unpack_each(paths: list[Path], outdir: Path, cancel: threading.Event | None = None
                ) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Como `unpack`, pero un archivo dañado no tumba a los demás: devuelve los archivos finales y los que no se
    pudieron descomprimir, con el motivo. Lo que dejó a medias un archivo dañado se borra. Sirve para los ZIP
    horarios de una estación (el INEGI entrega a veces una hora dañada en su servidor): las demás horas se usan y el
    hueco se avisa."""
    outdir.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    failed: list[tuple[Path, str]] = []
    for path in paths:
        tools.check(cancel)
        before = set(outdir.iterdir())
        try:
            result.extend(_unpack_one(Path(path), outdir, cancel, depth=0))
        except UserError as error:
            for leftover in set(outdir.iterdir()) - before:
                if leftover.is_file():
                    leftover.unlink(missing_ok=True)
            failed.append((Path(path), str(error)))
    return result, failed


def _unpack_one(path: Path, outdir: Path, cancel, depth: int) -> list[Path]:
    if depth > 4:
        raise UserError(f"«{path.name}» tiene demasiados niveles de compresión.")
    if not path.is_file():
        raise UserError(f"No existe el archivo «{path}».")
    if path.stat().st_size == 0:
        raise UserError(f"«{path.name}» está vacío (descarga incompleta): bájalo otra vez.")
    with open(path, "rb") as handle:
        head = handle.read(256)
    try:
        if head.startswith(ZIP_MAGIC) or path.suffix.lower() == ".zip":
            files: list[Path] = []
            with zipfile.ZipFile(path) as archive:
                members = [m for m in archive.infolist() if not m.is_dir()]
                if sum(m.file_size for m in members) > MAX_UNPACKED_BYTES:
                    raise UserError(f"«{path.name}» es demasiado grande para descomprimirlo.")
                for member in members:
                    name = Path(member.filename).name
                    if not name or name.startswith("."):
                        continue
                    target = _unique(outdir, name)
                    with archive.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    files.extend(_unpack_one(target, outdir, cancel, depth + 1))
            return files
        if head.startswith(GZIP_MAGIC):
            target = _unique(outdir, _strip_suffix(path.name))
            with gzip.open(path, "rb") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            return _unpack_one(target, outdir, cancel, depth + 1)
        if head.startswith(LZW_MAGIC):
            target = _unique(outdir, _strip_suffix(path.name))
            target.write_bytes(lzw_decompress(path.read_bytes()))
            return _unpack_one(target, outdir, cancel, depth + 1)
    except (OSError, EOFError, zipfile.BadZipFile, ValueError, gzip.BadGzipFile) as error:
        raise UserError(f"No se pudo descomprimir «{path.name}»: el archivo está dañado o incompleto "
                        f"({error}).") from error
    if is_hatanaka(head):
        target = _unique(outdir, _hatanaka_name(path.name))
        crx2rnx(path, target)
        return [target]
    if path.parent.resolve() != outdir.resolve():
        target = _unique(outdir, path.name)
        shutil.copy2(path, target)
        return [target]
    return [path]


def read_text_head(path: Path, size: int = 4096) -> str:
    with open(path, "rb") as handle:
        return handle.read(size).decode("latin-1")


__all__ = ["unpack", "unpack_each", "lzw_decompress", "is_hatanaka", "crx2rnx", "read_text_head", "io"]
