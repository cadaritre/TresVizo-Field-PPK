"""Separación del flujo grabado en tramas: binario Unicore, RTCM3 y líneas ASCII (NMEA, respuestas).

La grabación del receptor es la copia byte a byte de la UART del módulo GNSS: NMEA, respuestas a
órdenes, binario nativo Unicore y RTCM3 entremezclados, y en un `.part` puede faltar el final. Cada
trama binaria se acepta solo con su CRC (CRC-32 de Unicore, apéndice 1 del manual N4; CRC-24Q de
RTCM 10403.3) y, si no cuadra, se avanza un byte y se vuelve a buscar sincronismo.
"""
from __future__ import annotations

import binascii
import json
import re
import struct
from dataclasses import dataclass, field

UNICORE_SYNC = b"\xaa\x44\xb5"
UNICORE_HEADER_BYTES = 24
UNICORE_MAX_PAYLOAD = 16384
RTCM_PREAMBLE = 0xD3
MAX_ASCII_LINE = 4096
META_PREFIX = b"$PTVZ,META,"

_SYNC = re.compile(rb"\xaa\x44\xb5|\xd3|[$#]")
_PRINTABLE = re.compile(rb"^[\x20-\x7e]*$")


def unicore_crc32(data: bytes) -> int:
    """CRC-32 de Unicore: tabla reflejada 0xEDB88320, valor inicial 0 y sin XOR final."""
    return (binascii.crc32(data, 0xFFFFFFFF) ^ 0xFFFFFFFF) & 0xFFFFFFFF


def _crc24q_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        table.append(crc & 0xFFFFFF)
    return table


_CRC24 = _crc24q_table()


def crc24q(data: bytes) -> int:
    crc = 0
    table = _CRC24
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFF) ^ table[(crc >> 16) ^ byte]
    return crc


def nmea_checksum_ok(line: bytes) -> bool:
    """`$...*HH`: XOR de lo que va entre `$` y `*`."""
    star = line.rfind(b"*")
    if not line.startswith(b"$") or star < 0 or len(line) < star + 3:
        return False
    value = 0
    for byte in line[1:star]:
        value ^= byte
    try:
        return value == int(line[star + 1:star + 3], 16)
    except ValueError:
        return False


def parse_session_metadata(line: bytes) -> dict | None:
    """Registro `$PTVZ,META,<versión>,<json>*HH` que escribe el firmware al empezar la sesión."""
    if not line.startswith(META_PREFIX) or not nmea_checksum_ok(line):
        return None
    body = line[len(META_PREFIX):line.rfind(b"*")]
    version, _, payload = body.partition(b",")
    try:
        record = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    try:
        record["_record_version"] = int(version)
    except ValueError:
        return None
    return record


@dataclass
class FrameStats:
    unicore: int = 0
    unicore_crc_errors: int = 0
    rtcm: int = 0
    rtcm_crc_errors: int = 0
    ascii_lines: int = 0
    truncated_tail: bool = False
    unicore_ids: dict = field(default_factory=dict)
    rtcm_types: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Frame:
    kind: str        # «unicore», «rtcm» o «ascii»
    start: int
    end: int


def split_frames(data: bytes) -> tuple[list[Frame], FrameStats]:
    stats = FrameStats()
    frames: list[Frame] = []
    size = len(data)
    pos = 0
    while True:
        match = _SYNC.search(data, pos)
        if match is None:
            break
        i = match.start()
        first = data[i]
        if first == 0xAA:
            if i + 8 > size:
                stats.truncated_tail = True
                break
            length = struct.unpack_from("<H", data, i + 6)[0]
            total = UNICORE_HEADER_BYTES + length + 4
            if length > UNICORE_MAX_PAYLOAD:
                pos = i + 1
                continue
            if i + total > size:
                stats.truncated_tail = True
                pos = i + 1
                continue
            crc = struct.unpack_from("<I", data, i + total - 4)[0]
            if unicore_crc32(data[i:i + total - 4]) != crc:
                stats.unicore_crc_errors += 1
                pos = i + 1
                continue
            message_id = struct.unpack_from("<H", data, i + 4)[0]
            stats.unicore += 1
            stats.unicore_ids[message_id] = stats.unicore_ids.get(message_id, 0) + 1
            frames.append(Frame("unicore", i, i + total))
            pos = i + total
        elif first == RTCM_PREAMBLE:
            if i + 3 > size:
                break
            if data[i + 1] & 0xFC:
                pos = i + 1
                continue
            length = ((data[i + 1] & 0x03) << 8) | data[i + 2]
            total = length + 6
            if i + total > size:
                stats.truncated_tail = True
                pos = i + 1
                continue
            if length < 2 or crc24q(data[i:i + 3 + length]) != int.from_bytes(data[i + 3 + length:i + total], "big"):
                stats.rtcm_crc_errors += 1
                pos = i + 1
                continue
            message_type = (data[i + 3] << 4) | (data[i + 4] >> 4)
            stats.rtcm += 1
            stats.rtcm_types[message_type] = stats.rtcm_types.get(message_type, 0) + 1
            frames.append(Frame("rtcm", i, i + total))
            pos = i + total
        else:
            end = data.find(b"\n", i, i + MAX_ASCII_LINE)
            if end < 0:
                pos = i + 1
                continue
            line = data[i:end].rstrip(b"\r")
            if not _PRINTABLE.match(line):
                pos = i + 1
                continue
            stats.ascii_lines += 1
            frames.append(Frame("ascii", i, end))
            pos = end + 1
    return frames, stats


def find_session_metadata(data: bytes, limit: int = 1 << 20) -> dict | None:
    """El primer registro de metadatos válido en el primer MiB (el firmware lo escribe en el byte 0)."""
    window = data[:limit]
    start = window.find(META_PREFIX)
    while start >= 0:
        end = window.find(b"\n", start)
        if end < 0:
            return None
        record = parse_session_metadata(window[start:end].rstrip(b"\r"))
        if record is not None:
            return record
        start = window.find(META_PREFIX, start + 1)
    return None
