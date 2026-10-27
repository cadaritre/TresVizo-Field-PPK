"""Lectura de lo que escribe RTKLIB (demo5 / RTKLIB-EX) para las gráficas y tablas del informe.

- `.pos`: una línea por época con la solución (`%` es cabecera). Se aceptan las dos salidas de posición que da
  `rnx2rtkp`: geodésicas (latitud, longitud, altura) y geocéntricas (X, Y, Z), con la hora en GPST o UTC según diga la
  cabecera, en el formato «aaaa/mm/dd hh:mm:ss.sss» o en semana GPS y segundos.
- `.stat` (nivel 2): las líneas `$SAT` (por satélite y frecuencia: acimut, elevación, residuo de código y de fase,
  satélite usado, SNR, estado de la ambigüedad, salto…) y `$POS`.

Los campos se leen por posición, como los escribe `rtkpos.c` (`outsolstat`): un campo de más al final (las versiones
nuevas añaden) no estorba; una línea que no se entiende se salta y se cuenta.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
LEAP_SECONDS = 18          # GPST − UTC desde el 1-1-2017 (sin cambios anunciados por el IERS)

# Estado de la solución de RTKLIB (columna Q).
Q_NAMES = {1: "FIJA", 2: "FLOTANTE", 3: "SBAS", 4: "DGPS", 5: "AUTÓNOMA", 6: "PPP"}


@dataclass
class PosEpoch:
    time: datetime              # UTC
    q: int
    ns: int
    lat: float | None = None
    lon: float | None = None
    height: float | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    sd: tuple[float, float, float] = (math.nan, math.nan, math.nan)   # σ n, e, u (o x, y, z)
    age: float | None = None
    ratio: float | None = None


@dataclass
class PosFile:
    epochs: list[PosEpoch] = field(default_factory=list)
    geocentric: bool = False
    skipped: int = 0
    header: list[str] = field(default_factory=list)


@dataclass
class SatRecord:
    time: datetime              # UTC
    sat: str                    # «G05»
    frequency: int
    azimuth: float
    elevation: float
    residual_code: float
    residual_phase: float
    used: bool
    snr: float
    fix: int                    # 1 flotante, 2 fija, 3 fija y mantenida (código de RTKLIB)
    slip: int


@dataclass
class StatFile:
    satellites: list[SatRecord] = field(default_factory=list)
    skipped: int = 0


def gpst_to_utc(week: int, tow: float) -> datetime:
    return GPS_EPOCH + timedelta(weeks=week, seconds=tow - LEAP_SECONDS)


def _parse_time(parts: list[str], gpst: bool) -> tuple[datetime, int]:
    """La hora de una línea `.pos`: «aaaa/mm/dd hh:mm:ss.sss» (2 campos) o «semana segundos» (2 campos)."""
    a, b = parts[0], parts[1]
    if "/" in a:
        date = datetime.strptime(a, "%Y/%m/%d")
        h, m, s = b.split(":")
        t = datetime(date.year, date.month, date.day, tzinfo=timezone.utc) + timedelta(
            hours=int(h), minutes=int(m), seconds=float(s))
        return (t - timedelta(seconds=LEAP_SECONDS) if gpst else t), 2
    t = gpst_to_utc(int(a), float(b))
    return (t if gpst else t + timedelta(seconds=LEAP_SECONDS)), 2


def read_pos(path: str | Path) -> PosFile:
    out = PosFile()
    gpst = True
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if line.startswith("%"):
                out.header.append(line)
                low = line.lower()
                if "ecef" in low:
                    out.geocentric = True
                if low.startswith("%  utc") or " utc " in low[:12]:
                    gpst = False
                if "gpst" in low[:12]:
                    gpst = True
                continue
            parts = line.split()
            try:
                t, i = _parse_time(parts, gpst)
                c1, c2, c3 = float(parts[i]), float(parts[i + 1]), float(parts[i + 2])
                q, ns = int(parts[i + 3]), int(parts[i + 4])
                sd = tuple(float(v) for v in parts[i + 5:i + 8])
                age = float(parts[i + 11]) if len(parts) > i + 11 else None
                ratio = float(parts[i + 12]) if len(parts) > i + 12 else None
            except (ValueError, IndexError):
                out.skipped += 1
                continue
            ep = PosEpoch(time=t, q=q, ns=ns, sd=sd, age=age, ratio=ratio)   # type: ignore[arg-type]
            if out.geocentric:
                ep.x, ep.y, ep.z = c1, c2, c3
            else:
                ep.lat, ep.lon, ep.height = c1, c2, c3
            out.epochs.append(ep)
    return out


def read_stat(path: str | Path) -> StatFile:
    out = StatFile()
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith("$SAT,"):
                continue
            f = line.strip().split(",")
            try:
                out.satellites.append(SatRecord(
                    time=gpst_to_utc(int(f[1]), float(f[2])), sat=f[3], frequency=int(f[4]),
                    azimuth=float(f[5]), elevation=float(f[6]), residual_code=float(f[7]),
                    residual_phase=float(f[8]), used=f[9].strip() == "1", snr=float(f[10]), fix=int(f[11]),
                    slip=int(f[12])))
            except (ValueError, IndexError):
                out.skipped += 1
    return out


def dop_series(stat: StatFile, mask_deg: float = 0.0) -> list[tuple[datetime, float, float, float, int]]:
    """(hora, PDOP, HDOP, VDOP, satélites) por época, de la geometría de los satélites usados (los `$SAT` con
    `vsat` = 1 en la primera frecuencia). Es la DOP de la solución, no la del cielo entero."""
    by_time: dict[datetime, list[tuple[float, float]]] = {}
    for r in stat.satellites:
        if r.frequency == 1 and r.used and r.elevation >= mask_deg:
            by_time.setdefault(r.time, []).append((math.radians(r.azimuth), math.radians(r.elevation)))
    out = []
    for t in sorted(by_time):
        geom = by_time[t]
        if len(geom) < 4:
            continue
        # G = [−cos el sin az, −cos el cos az, −sin el, 1]; Q = (GᵀG)⁻¹ en ENU.
        rows = [(-math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), -math.sin(el), 1.0) for az, el in geom]
        n = [[sum(r[i] * r[j] for r in rows) for j in range(4)] for i in range(4)]
        q = _invert4(n)
        if q is None:
            continue
        pdop = math.sqrt(max(q[0][0] + q[1][1] + q[2][2], 0.0))
        hdop = math.sqrt(max(q[0][0] + q[1][1], 0.0))
        vdop = math.sqrt(max(q[2][2], 0.0))
        out.append((t, pdop, hdop, vdop, len(geom)))
    return out


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
