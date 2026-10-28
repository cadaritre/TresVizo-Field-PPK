"""PDOP de la solución, de la geometría de los satélites que RTKLIB usó en cada época (las líneas `$SAT` del `.stat`,
primera frecuencia y `vsat` = 1), para la tolerancia de PDOP del proyecto.

Se lee el archivo línea por línea (el `.stat` de una jornada cinemática a 1 Hz pasa de cien megabytes) y cada
satélite cuenta una sola vez por época: el proceso combinado escribe la pasada hacia adelante y la de regreso.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

GPS_EPOCH_NAIVE = datetime(1980, 1, 6)


def pdop(geometry: list[tuple[float, float]]) -> float | None:
    """PDOP de los satélites (acimut, elevación en radianes); None con menos de cuatro o geometría singular."""
    if len(geometry) < 4:
        return None
    rows = [(-math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), -math.sin(el), 1.0) for az, el in geometry]
    n = [[sum(r[i] * r[j] for r in rows) for j in range(4)] for i in range(4)]
    q = _invert4(n)
    if q is None:
        return None
    return math.sqrt(max(q[0][0] + q[1][1] + q[2][2], 0.0))


def pdop_by_epoch(stat_file: Path | None) -> dict[datetime, float]:
    """PDOP por época (GPST ingenuo, como `rtklib.PosEpoch.time`). Vacío si no hay archivo."""
    if stat_file is None or not Path(stat_file).is_file():
        return {}
    by_time: dict[tuple[int, float], dict[str, tuple[float, float]]] = {}
    with open(stat_file, "r", encoding="ascii", errors="replace") as handle:
        for line in handle:
            if not line.startswith("$SAT,"):
                continue
            p = line.split(",")
            if len(p) < 10 or p[4] != "1" or p[9].strip() != "1":
                continue
            try:
                key = (int(p[1]), round(float(p[2]), 3))
                az, el = math.radians(float(p[5])), math.radians(float(p[6]))
            except ValueError:
                continue
            by_time.setdefault(key, {})[p[3]] = (az, el)
    out = {}
    for (week, tow), sats in by_time.items():
        value = pdop(list(sats.values()))
        if value is not None:
            out[GPS_EPOCH_NAIVE + timedelta(weeks=week, seconds=tow)] = value
    return out


def max_pdop(by_epoch: dict[datetime, float], times) -> float | None:
    """La mayor PDOP de las épocas `times` que tienen dato; None si ninguna."""
    values = [by_epoch[t] for t in times if t in by_epoch]
    return max(values) if values else None


def _invert4(m: list[list[float]]) -> list[list[float]] | None:
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(4)] for i, row in enumerate(m)]
    for c in range(4):
        p = max(range(c, 4), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            return None
        a[c], a[p] = a[p], a[c]
        pivot = a[c][c]
        a[c] = [v / pivot for v in a[c]]
        for r in range(4):
            if r != c and a[r][c] != 0.0:
                factor = a[r][c]
                a[r] = [v - factor * w for v, w in zip(a[r], a[c])]
    return [row[4:] for row in a]
