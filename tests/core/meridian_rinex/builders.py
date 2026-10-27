"""Construcción de tramas para las pruebas: binario Unicore (con su CRC-32) y RTCM3 MSM (con CRC-24Q).

Los codificadores siguen la disposición de los manuales (Unicore N4 tablas 7-50 y 7-53; RTCM MSM
según RTKLIB src/rtcm3e.c) y no reutilizan el código del decodificador.
"""
from __future__ import annotations

import struct

_TABLE = []
for _n in range(256):
    _c = _n
    for _ in range(8):
        _c = (_c >> 1) ^ 0xEDB88320 if _c & 1 else _c >> 1
    _TABLE.append(_c)


def manual_crc32(data: bytes) -> int:
    """CalculateCRC32 del apéndice 1 del manual N4, transcrito tal cual (tabla reflejada, inicio 0)."""
    crc = 0
    for byte in data:
        crc = _TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc


def manual_crc24q(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def unicore_frame(message_id: int, payload: bytes, week: int, tow_ms: int, leap: int = 18, time_ref: int = 0,
                  status: int = 160) -> bytes:
    header = struct.pack("<3sBHHBBHIIBBH", b"\xaa\x44\xb5", 90, message_id, len(payload), time_ref, status,
                         week, tow_ms, 0, 0, leap, 0)
    body = header + payload
    return body + struct.pack("<I", manual_crc32(body))


def track_status(system: int, signal_type: int, phase_valid: bool = True, code_valid: bool = True,
                 l2c: bool = False) -> int:
    value = 0x03 | (1 << 5)
    if phase_valid:
        value |= (1 << 10) | (1 << 11)
    if code_valid:
        value |= 1 << 12
    return value | (system << 16) | (signal_type << 21) | (int(l2c) << 26) | (3 << 19)


def obs_record(prn: int, psr: float, adr: float, dopp: float, cn0: float, lock: float, status: int,
               freq: int = 0) -> bytes:
    return struct.pack("<HHddHHfHHfI", freq, prn, psr, adr, 120, 50, dopp, int(round(cn0 * 100)), 0, lock, status)


def obsvm_payload(records: list[bytes]) -> bytes:
    return struct.pack("<I", len(records)) + b"".join(records)


class BitWriter:
    def __init__(self) -> None:
        self.value = 0
        self.bits = 0

    def u(self, n: int, v: int) -> "BitWriter":
        assert 0 <= v < (1 << n), (n, v)
        self.value = (self.value << n) | v
        self.bits += n
        return self

    def s(self, n: int, v: int) -> "BitWriter":
        assert -(1 << (n - 1)) <= v < (1 << (n - 1)), (n, v)
        return self.u(n, v & ((1 << n) - 1))

    def g(self, n: int, v: int) -> "BitWriter":
        """Signo y magnitud."""
        return self.u(n, ((1 << (n - 1)) if v < 0 else 0) | abs(v))

    def payload(self) -> bytes:
        pad = (-self.bits) % 8
        value = self.value << pad
        return value.to_bytes((self.bits + pad) // 8, "big")


def rtcm_frame(payload: bytes) -> bytes:
    head = bytes([0xD3, (len(payload) >> 8) & 0x03, len(payload) & 0xFF]) + payload
    return head + manual_crc24q(head).to_bytes(3, "big")


def msm_frame(message_type: int, epoch_raw: int, sats: list[dict], sigs: list[int], cells: list[dict],
              multiple: bool = False, station: int = 0, glonass_dow: int = 0) -> bytes:
    """`sats`: [{id, rough_ms, rough_mod, ext, rate}]; `cells`: por satélite y señal en orden, [{sat, sig,
    pr, cp, lock, half, cnr, rate}] con los enteros tal como van en la trama."""
    msm = message_type % 10
    w = BitWriter().u(12, message_type).u(12, station)
    if message_type // 10 == 108:
        w.u(3, glonass_dow).u(27, epoch_raw)
    else:
        w.u(30, epoch_raw)
    w.u(1, int(multiple)).u(3, 0).u(7, 0).u(2, 0).u(2, 0).u(1, 0).u(3, 0)
    sat_mask = 0
    for sat in sats:
        sat_mask |= 1 << (64 - sat["id"])
    sig_mask = 0
    for sig in sigs:
        sig_mask |= 1 << (32 - sig)
    w.u(64, sat_mask).u(32, sig_mask)
    sat_order = sorted(sats, key=lambda s: s["id"])
    present = {(c["sat"], c["sig"]) for c in cells}
    for sat in sat_order:
        for sig in sorted(sigs):
            w.u(1, int((sat["id"], sig) in present))
    ordered = sorted(cells, key=lambda c: (c["sat"], c["sig"]))
    for sat in sat_order:
        w.u(8, sat["rough_ms"])
    if msm in (5, 7):
        for sat in sat_order:
            w.u(4, sat.get("ext", 15))
    for sat in sat_order:
        w.u(10, sat["rough_mod"])
    if msm in (5, 7):
        for sat in sat_order:
            w.s(14, sat.get("rate", -8192))
    high = msm in (6, 7)
    for c in ordered:
        w.s(20 if high else 15, c["pr"])
    for c in ordered:
        w.s(24 if high else 22, c["cp"])
    for c in ordered:
        w.u(10 if high else 4, c["lock"])
    for c in ordered:
        w.u(1, c.get("half", 0))
    for c in ordered:
        w.u(10 if high else 6, c["cnr"])
    if msm in (5, 7):
        for c in ordered:
            w.s(15, c.get("rate", -16384))
    return rtcm_frame(w.payload())


def nmea(sentence: str) -> bytes:
    checksum = 0
    for ch in sentence:
        checksum ^= ord(ch)
    return f"${sentence}*{checksum:02X}\r\n".encode("ascii")
