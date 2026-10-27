"""Lector del formato `.mvgeoid` de TresVizo Field, con la misma interpolación que las apps.

Es el formato que leen iOS (`MeridianVCore/Geoid/GeoidGrid.swift`) y Android (`GeoidGrid.kt`) y que escribe
`tools/geoides/mvgeoid.py` del repositorio de iOS (la referencia en Python). Aquí solo se lee: los datos se descomprimen
con numpy (la referencia lo hace en Python puro y tarda segundos con GGM25) y la interpolación es la de las apps,
**operación por operación** en reales de doble precisión, para dar el mismo número.

Cabecera (64 bytes, little-endian): "MVGEOID1", versión (UInt16: 1 sin comprimir, 2 comprimida), filas y columnas
(UInt32), latitud y longitud mínimas y pasos (Float64, grados), bytes de metadatos y bytes sin comprimir (UInt32),
método (UInt8: 0 ninguno, 1 deflate crudo) y 5 reservados. Luego los metadatos (JSON UTF-8, el `GeoidModel`) y los datos:
enteros de 16 bits con signo en **centímetros**, fila 0 la del sur, columna 0 la del oeste, −32768 «sin dato». En la
versión 2 cada valor va como diferencia contra el predictor plano (izquierda + abajo − diagonal) módulo 2¹⁶, en deflate
crudo. El SHA-256 de los datos descomprimidos va en `dataChecksum` de los metadatos.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MAGIC = b"MVGEOID1"
HEADER_BYTES = 64
NODATA = -32768
MAXIMUM_SAMPLES = 100_000_000   # el mismo tope que las apps (`GeoidGrid.maximumSamples`)
_RAW_DEFLATE = 1


class FormatError(Exception):
    """El archivo no es un `.mvgeoid` válido o está dañado (mensaje en español)."""


@dataclass
class Grid:
    version: int
    rows: int
    columns: int
    latitude_minimum: float
    longitude_minimum: float
    latitude_step: float
    longitude_step: float
    metadata: dict
    samples: np.ndarray          # int16, rows × columns
    checksum: str

    @property
    def latitude_maximum(self) -> float:
        return self.latitude_minimum + self.latitude_step * (self.rows - 1)

    @property
    def longitude_maximum(self) -> float:
        return self.longitude_minimum + self.longitude_step * (self.columns - 1)

    @property
    def model_id(self) -> str:
        return str(self.metadata.get("id", ""))

    def sample(self, row: int, column: int) -> float | None:
        v = int(self.samples[row, column])
        return None if v == NODATA else v / 100

    def contains(self, latitude: float, longitude: float) -> bool:
        return (self.latitude_minimum <= latitude <= self.latitude_maximum
                and self.longitude_minimum <= longitude <= self.longitude_maximum)

    def undulation(self, latitude: float, longitude: float) -> float | None:
        """`GeoidGrid.undulation(at:)` de las apps. None fuera de cobertura o si falta una esquina (no se extrapola
        ni se rellena un hueco con las vecinas)."""
        if not self.contains(latitude, longitude):
            return None
        fr = (latitude - self.latitude_minimum) / self.latitude_step
        fc = (longitude - self.longitude_minimum) / self.longitude_step
        r = min(int(math.floor(fr)), self.rows - 2)   # en el borde exacto, la última celda completa
        c = min(int(math.floor(fc)), self.columns - 2)
        ffr, ffc = fr - r, fc - c
        sw, se = self.sample(r, c), self.sample(r, c + 1)
        nw, ne = self.sample(r + 1, c), self.sample(r + 1, c + 1)
        if sw is None or se is None or nw is None or ne is None:
            return None
        south = sw + (se - sw) * ffc
        north = nw + (ne - nw) * ffc
        return south + (north - south) * ffr


def decode(data: bytes) -> Grid:
    """Lee y comprueba un `.mvgeoid` con las mismas reglas que las apps."""
    if len(data) <= HEADER_BYTES or data[:8] != MAGIC:
        raise FormatError("El archivo no es un geoide de TresVizo.")
    version, = struct.unpack_from("<H", data, 8)
    if version not in (1, 2):
        raise FormatError(f"El geoide usa la versión {version} del formato; actualiza la app.")
    rows, columns = struct.unpack_from("<II", data, 10)
    lat0, lon0, dlat, dlon = struct.unpack_from("<dddd", data, 18)
    meta_bytes, plain_bytes = struct.unpack_from("<II", data, 50)
    compression = data[58] if version >= 2 else 0
    if rows < 2 or columns < 2 or rows * columns > MAXIMUM_SAMPLES:
        raise FormatError(f"El geoide declara una malla de {rows}×{columns}: no es válida.")
    if not (math.isfinite(dlat) and math.isfinite(dlon) and dlat > 0 and dlon > 0):
        raise FormatError("El geoide declara un paso de malla no válido.")
    start = HEADER_BYTES + meta_bytes
    if len(data) < start:
        raise FormatError("El geoide está truncado (metadatos).")
    try:
        metadata = json.loads(data[HEADER_BYTES:start].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FormatError("Los metadatos del geoide no se pueden leer.") from None
    expected = rows * columns * 2
    stored = data[start:]
    if compression == 0:
        if len(stored) != expected:
            raise FormatError(f"El geoide está truncado: {len(stored)} de {expected} bytes.")
        body = bytes(stored)
    elif compression == _RAW_DEFLATE:
        if plain_bytes != expected:
            raise FormatError("El tamaño declarado de los datos del geoide no corresponde a su malla.")
        try:
            diffs = zlib.decompress(stored, -15)
        except zlib.error:
            raise FormatError("Los datos del geoide no se pudieron descomprimir: archivo dañado.") from None
        if len(diffs) != expected:
            raise FormatError("Los datos del geoide no tienen el tamaño de su malla: archivo dañado.")
        d = np.frombuffer(diffs, dtype="<u2").reshape(rows, columns)
        # Camino de vuelta del predictor plano, módulo 2¹⁶: suma por filas (e) y luego por columnas.
        e = np.cumsum(d, axis=1, dtype=np.uint16)
        v = np.cumsum(e, axis=0, dtype=np.uint16)
        body = v.astype("<u2").tobytes()
    else:
        raise FormatError(f"El geoide usa un método de compresión desconocido ({compression}).")
    checksum = hashlib.sha256(body).hexdigest()
    if checksum != metadata.get("dataChecksum"):
        raise FormatError("La suma de los datos del geoide no coincide: archivo dañado.")
    samples = np.frombuffer(body, dtype="<i2").reshape(rows, columns)
    return Grid(version, rows, columns, lat0, lon0, dlat, dlon, metadata, samples, checksum)


def read(path: str | Path) -> Grid:
    return decode(Path(path).read_bytes())
