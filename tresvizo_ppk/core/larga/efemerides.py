"""Órbitas y relojes de los satélites para el procesador de líneas largas: efemérides difundidas (RINEX de
navegación 2, 3 y 4) y precisas (SP3, con relojes del SP3 o de un RINEX de relojes).

**Difundidas.** GPS (LNAV), Galileo (I/NAV, o F/NAV si no hay) y BeiDou (D1/D2, solo satélites no
geoestacionarios) con el algoritmo de la ICD de cada sistema (IS-GPS-200, Galileo OS SIS ICD, BDS-SIS-ICD): por
época se toma la efeméride sana más cercana a su tiempo de referencia dentro de su validez (GPS ±2 h, Galileo
±2 h, BeiDou ±1 h). El reloj incluye la corrección relativista periódica (−2·r·v/c²). Las posiciones difundidas se
refieren al centro de fase de la antena del satélite (convención de los segmentos de control): no se les suma el PCO.
Exactitud típica (Montenbruck et al. 2015, GPS Solutions 19:321): GPS ~1 m, Galileo ~0.3 m, BeiDou-3 ~0.5 m en
órbita; el efecto en una doble diferencia es la órbita por L/ρ (≈ L/20 000 km): 7 mm con 1 m de error a 140 km.

**Precisas.** SP3-c/d, interpolación de Lagrange de grado 9 (10 puntos, error < 1 mm con épocas de 5 o 15 min).
Las posiciones del SP3 son del centro de masa: el procesador les suma el PCO del satélite (ANTEX). El reloj sale
del RINEX de relojes si se entrega, si no del SP3 (interpolación lineal) y, si al SP3 le falta, de las difundidas:
en las dobles diferencias el reloj del satélite se cancela y solo sirve para el instante de emisión (basta 1 µs).

Tiempo: segundos GPS (ver `astro`). BDT = GPST − 14 s.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from ...errors import UserError
from .astro import gps_seconds
from .senales import BDS_GEO, C_LIGHT

GM = {"G": 3.9860050e14, "E": 3.986004418e14, "C": 3.986004418e14}
OMEGA_E = {"G": 7.2921151467e-5, "E": 7.2921151467e-5, "C": 7.292115e-5}
F_REL = {s: -2.0 * math.sqrt(GM[s]) / C_LIGHT ** 2 for s in GM}
WEEK = 604800.0
BDT_GPST = 14.0
MAX_AGE = {"G": 7200.0 + 60.0, "E": 7200.0 + 60.0, "C": 3600.0 + 60.0}


@dataclass
class Ephemeris:
    sat: str
    toc: float              # GPST s
    toe: float              # GPST s (absoluto)
    af0: float
    af1: float
    af2: float
    crs: float
    dn: float
    m0: float
    cuc: float
    e: float
    cus: float
    sqrt_a: float
    cic: float
    omega0: float
    cis: float
    i0: float
    crc: float
    omega: float
    omega_dot: float
    idot: float
    health: int
    kind: str = ""          # «INAV», «FNAV», «D1»…
    iode: float = 0.0


def _num(text: str) -> float:
    text = text.strip().replace("D", "E").replace("d", "e")
    return float(text) if text else 0.0


def _resolve_week(toe_sow: float, ref: float) -> float:
    week = math.floor(ref / WEEK)
    t = week * WEEK + toe_sow
    if t - ref > WEEK / 2:
        t -= WEEK
    elif ref - t > WEEK / 2:
        t += WEEK
    return t


def _make(sat: str, toc: float, v: list[float], kind: str) -> Ephemeris | None:
    system = sat[0]
    if len(v) < 28:
        return None
    toe_sow = v[11]
    if system == "C":
        toe = _resolve_week(toe_sow + BDT_GPST, toc)   # toc ya está en GPST
    else:
        toe = _resolve_week(toe_sow, toc)
    health = int(v[24])
    if system == "E":
        # Galileo: salud E1B/E5b/E5a en bits; 0 = sano. Fuente de datos (v[20]): bit 0/9 I/NAV, bit 1/8 F/NAV
        source = int(v[20])
        kind = "INAV" if source & 0b1000000101 else "FNAV" if source & 0b10 else kind or "INAV"
    return Ephemeris(sat=sat, toc=toc, toe=toe, af0=v[0], af1=v[1], af2=v[2], iode=v[3], crs=v[4], dn=v[5],
                     m0=v[6], cuc=v[7], e=v[8], cus=v[9], sqrt_a=v[10], cic=v[12], omega0=v[13], cis=v[14],
                     i0=v[15], crc=v[16], omega=v[17], omega_dot=v[18], idot=v[19], health=health, kind=kind)


def read_nav(paths: list[Path]) -> dict[str, list[Ephemeris]]:
    """Efemérides de GPS, Galileo y BeiDou de uno o varios RINEX de navegación (2.x, 3.x o 4.x)."""
    out: dict[str, list[Ephemeris]] = {}
    for path in paths:
        path = Path(path)
        try:
            text = path.read_text(encoding="latin-1")
        except OSError as error:
            raise UserError(f"No se pudo leer «{path.name}»: {error}") from error
        lines = text.splitlines()
        if not lines or "RINEX VERSION / TYPE" not in lines[0]:
            continue
        version = float(lines[0][0:9])
        ftype = lines[0][20:21].upper()
        default_sys = "G"
        if version < 3:
            if ftype not in ("N",):
                continue                              # GLONASS («G») y otros de la v2: no se usan
            default_sys = "G"
        start = next((i + 1 for i, l in enumerate(lines) if l[60:73] == "END OF HEADER"), None)
        if start is None:
            continue
        i = start
        if version >= 4:
            while i < len(lines):
                line = lines[i]
                if line.startswith("> EPH"):
                    parts = line.split()
                    sat, kind = parts[2], parts[3] if len(parts) > 3 else ""
                    body = [lines[j] for j in range(i + 1, min(i + 9, len(lines)))]
                    i += 1
                    if sat[0] in GM and kind in ("LNAV", "INAV", "FNAV", "D1", "D2") and len(body) >= 8:
                        _add(out, sat, body[:8], 23, kind)
                        i += 8
                    continue
                i += 1
            continue
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            if version >= 3:
                sat = line[0:3].replace(" ", "0")
                system = sat[0]
                n_lines = {"G": 8, "E": 8, "C": 8, "J": 8, "I": 8, "R": 4, "S": 4}.get(system, 8)
                if system in GM:
                    _add(out, sat, lines[i:i + n_lines], 23, "")
                i += n_lines
            else:
                prn = line[0:2].strip()
                if prn.isdigit():
                    sat = f"{default_sys}{int(prn):02d}"
                    block = lines[i:i + 8]
                    _add_v2(out, sat, block)
                i += 8
    for sat in out:
        out[sat].sort(key=lambda e: (e.toe, e.toc))
    return out


def _epoch_v3(line: str) -> float:
    y, mo, d, h, mi, s = (int(line[4:8]), int(line[9:11]), int(line[12:14]), int(line[15:17]), int(line[18:20]),
                          int(line[21:23]))
    return gps_seconds(datetime(y, mo, d, h, mi, s))


def _add(out, sat: str, block: list[str], width: int, kind: str) -> None:
    try:
        toc = _epoch_v3(block[0])
        if sat[0] == "C":
            toc += BDT_GPST
        vals = []
        for k in range(3):
            vals.append(_num(block[0][width + 19 * k: width + 19 * (k + 1)]))
        for line in block[1:]:
            for k in range(4):
                vals.append(_num(line[4 + 19 * k: 4 + 19 * (k + 1)]))
    except (ValueError, IndexError):
        return
    if sat[0] == "C" and int(sat[1:]) in BDS_GEO:
        return
    eph = _make(sat, toc, vals, kind or ("D2" if sat[0] == "C" and int(sat[1:]) <= 5 else ""))
    if eph:
        out.setdefault(sat, []).append(eph)


def _add_v2(out, sat: str, block: list[str]) -> None:
    try:
        line = block[0]
        yy = int(line[3:5])
        y = yy + (2000 if yy < 80 else 1900)
        mo, d, h, mi = int(line[6:8]), int(line[9:11]), int(line[12:14]), int(line[15:17])
        sec = float(line[17:22])
        toc = gps_seconds(datetime(y, mo, d, h, mi, int(sec)))
        vals = [_num(line[22 + 19 * k: 22 + 19 * (k + 1)]) for k in range(3)]
        for row in block[1:]:
            for k in range(4):
                vals.append(_num(row[3 + 19 * k: 3 + 19 * (k + 1)]))
    except (ValueError, IndexError):
        return
    eph = _make(sat, toc, vals, "LNAV")
    if eph:
        out.setdefault(sat, []).append(eph)


def _kepler(eph: Ephemeris, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Posición ECEF (n×3) y reloj (n) de una efeméride en los instantes t (GPST)."""
    system = eph.sat[0]
    mu, omge = GM[system], OMEGA_E[system]
    a = eph.sqrt_a ** 2
    tk = t - eph.toe
    n = math.sqrt(mu / a ** 3) + eph.dn
    m = eph.m0 + n * tk
    e_anom = m.copy()
    for _ in range(30):
        prev = e_anom
        e_anom = m + eph.e * np.sin(e_anom)
        if np.max(np.abs(e_anom - prev)) < 1e-14:
            break
    sin_e, cos_e = np.sin(e_anom), np.cos(e_anom)
    nu = np.arctan2(math.sqrt(1 - eph.e ** 2) * sin_e, cos_e - eph.e)
    phi = nu + eph.omega
    s2, c2 = np.sin(2 * phi), np.cos(2 * phi)
    u = phi + eph.cus * s2 + eph.cuc * c2
    r = a * (1 - eph.e * cos_e) + eph.crs * s2 + eph.crc * c2
    inc = eph.i0 + eph.idot * tk + eph.cis * s2 + eph.cic * c2
    x_orb, y_orb = r * np.cos(u), r * np.sin(u)
    toe_sow = (eph.toe - (BDT_GPST if system == "C" else 0.0)) % WEEK
    omg = eph.omega0 + (eph.omega_dot - omge) * tk - omge * toe_sow
    cos_o, sin_o, cos_i, sin_i = np.cos(omg), np.sin(omg), np.cos(inc), np.sin(inc)
    pos = np.stack([x_orb * cos_o - y_orb * cos_i * sin_o, x_orb * sin_o + y_orb * cos_i * cos_o, y_orb * sin_i],
                   axis=1)
    dt = t - eph.toc
    clk = eph.af0 + eph.af1 * dt + eph.af2 * dt ** 2 + F_REL[system] * eph.e * eph.sqrt_a * sin_e
    return pos, clk


class Broadcast:
    def __init__(self, ephemerides: dict[str, list[Ephemeris]]) -> None:
        self.eph = ephemerides

    @property
    def satellites(self) -> set[str]:
        return set(self.eph)

    def _select(self, sat: str, t: np.ndarray) -> np.ndarray:
        """Índice de la efeméride elegida para cada instante (−1 si ninguna sirve)."""
        cands = [e for e in self.eph.get(sat, []) if e.health == 0]
        if sat[0] == "E":
            inav = [e for e in cands if e.kind == "INAV"]
            cands = inav or cands
        self._cands = cands
        if not cands:
            return np.full(len(t), -1)
        toes = np.array([e.toe for e in cands])
        idx = np.argmin(np.abs(t[:, None] - toes[None, :]), axis=1)
        age = np.abs(t - toes[idx])
        idx[age > MAX_AGE[sat[0]]] = -1
        return idx

    def state(self, sat: str, t) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        t = np.atleast_1d(np.asarray(t, dtype=float))
        pos = np.full((len(t), 3), np.nan)
        clk = np.full(len(t), np.nan)
        idx = self._select(sat, t)
        cands = self._cands
        for k in set(idx.tolist()) - {-1}:
            mask = idx == k
            p, c = _kepler(cands[k], t[mask])
            pos[mask] = p
            clk[mask] = c
        return pos, clk, idx >= 0

    def clock(self, sat: str, t) -> np.ndarray:
        return self.state(sat, t)[1]


# --- SP3 y relojes ---------------------------------------------------------------------------------------------------

class Precise:
    """Órbitas SP3 (centro de masa) con relojes del SP3, de un RINEX de relojes o de las difundidas."""

    def __init__(self, sp3: list[Path], clk: list[Path] | None = None, broadcast: Broadcast | None = None) -> None:
        epochs: dict[float, dict[str, tuple[np.ndarray, float]]] = {}
        for path in sp3:
            _read_sp3(Path(path), epochs)
        if not epochs:
            raise UserError("Los archivos SP3 no traen órbitas.")
        self.times = np.array(sorted(epochs))
        sats = sorted({s for e in epochs.values() for s in e if s[0] in GM})
        self.pos: dict[str, np.ndarray] = {}
        self.clk: dict[str, np.ndarray] = {}
        for s in sats:
            p = np.full((len(self.times), 3), np.nan)
            c = np.full(len(self.times), np.nan)
            for i, t in enumerate(self.times):
                got = epochs[t].get(s)
                if got:
                    p[i], c[i] = got
            self.pos[s] = p
            self.clk[s] = c
        self.rinex_clk: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for path in clk or []:
            _read_clk(Path(path), self.rinex_clk)
        self.broadcast = broadcast

    @property
    def satellites(self) -> set[str]:
        return set(self.pos)

    def state(self, sat: str, t) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        t = np.atleast_1d(np.asarray(t, dtype=float))
        n = len(t)
        if sat not in self.pos:
            return np.full((n, 3), np.nan), np.full(n, np.nan), np.zeros(n, dtype=bool)
        pos = _lagrange(self.times, self.pos[sat], t)
        clk = self._clock(sat, t)
        ok = np.all(np.isfinite(pos), axis=1) & np.isfinite(clk)
        return pos, clk, ok

    def _clock(self, sat: str, t: np.ndarray) -> np.ndarray:
        if sat in self.rinex_clk:
            tt, cc = self.rinex_clk[sat]
            out = _linear(tt, cc, t, max_gap=300.0)
            if np.all(np.isfinite(out)):
                return out
        else:
            out = np.full(len(t), np.nan)
        sp3 = _linear(self.times, self.clk[sat], t, max_gap=1800.0)
        out = np.where(np.isfinite(out), out, sp3)
        if self.broadcast is not None and not np.all(np.isfinite(out)):
            out = np.where(np.isfinite(out), out, self.broadcast.clock(sat, t))
        return out


def _read_sp3(path: Path, epochs: dict) -> None:
    try:
        handle = open(path, "r", encoding="latin-1")
    except OSError as error:
        raise UserError(f"No se pudo leer «{path.name}»: {error}") from error
    current = None
    bdt = False
    with handle:
        for line in handle:
            if line.startswith("%c") and current is None and not bdt:
                bdt = line[9:12] == "BDT"
            if line.startswith("*"):
                y, mo, d, h, mi = (int(v) for v in line[3:31].split()[:5])
                sec = float(line[3:31].split()[5])
                current = gps_seconds(datetime(y, mo, d, h, mi, int(sec))) + (BDT_GPST if bdt else 0.0)
                epochs.setdefault(current, {})
            elif line.startswith("P") and current is not None:
                sat = line[1:4].replace(" ", "0")
                try:
                    x, y_, z = float(line[4:18]), float(line[18:32]), float(line[32:46])
                    c = float(line[46:60])
                except ValueError:
                    continue
                if x == 0.0 and y_ == 0.0 and z == 0.0:
                    continue
                clk = np.nan if c >= 999999.0 or c == 0.0 else c * 1e-6
                epochs[current][sat] = (np.array([x, y_, z]) * 1000.0, clk)


def _read_clk(path: Path, out: dict) -> None:
    rows: dict[str, list[tuple[float, float]]] = {}
    with open(path, "r", encoding="latin-1") as handle:
        in_body = False
        for line in handle:
            if not in_body:
                in_body = "END OF HEADER" in line
                continue
            if not line.startswith("AS "):
                continue
            sat = line[3:6].replace(" ", "0")
            if sat[0] not in GM:
                continue
            parts = line[8:].split()
            try:
                y, mo, d, h, mi = (int(v) for v in parts[:5])
                sec = float(parts[5])
                value = float(parts[7].replace("D", "E"))
            except (ValueError, IndexError):
                continue
            t = gps_seconds(datetime(y, mo, d, h, mi, int(sec))) + (sec - int(sec))
            rows.setdefault(sat, []).append((t, value))
    for sat, values in rows.items():
        values.sort()
        tt = np.array([v[0] for v in values])
        cc = np.array([v[1] for v in values])
        if sat in out:
            tt = np.concatenate([out[sat][0], tt])
            cc = np.concatenate([out[sat][1], cc])
            order = np.argsort(tt)
            tt, cc = tt[order], cc[order]
        out[sat] = (tt, cc)


def _linear(tt: np.ndarray, vv: np.ndarray, t: np.ndarray, max_gap: float) -> np.ndarray:
    ok = np.isfinite(vv)
    if ok.sum() < 2:
        return np.full(len(t), np.nan)
    tt, vv = tt[ok], vv[ok]
    i = np.clip(np.searchsorted(tt, t) - 1, 0, len(tt) - 2)
    t0, t1 = tt[i], tt[i + 1]
    f = (t - t0) / (t1 - t0)
    out = vv[i] + (vv[i + 1] - vv[i]) * f
    bad = (t1 - t0 > max_gap) | (t < tt[0] - 1.0) | (t > tt[-1] + 1.0)
    out[bad] = np.nan
    return out


def _lagrange(tt: np.ndarray, pp: np.ndarray, t: np.ndarray, order: int = 10) -> np.ndarray:
    """Interpolación de Lagrange de `order` puntos centrada en cada instante (NaN si faltan puntos)."""
    n = len(tt)
    out = np.full((len(t), 3), np.nan)
    if n < order:
        return out
    half = order // 2
    i0 = np.clip(np.searchsorted(tt, t) - half, 0, n - order)
    idx = i0[:, None] + np.arange(order)[None, :]
    ts = tt[idx]                                   # (m, order)
    ps = pp[idx]                                   # (m, order, 3)
    w = np.ones_like(ts)
    for j in range(order):
        for k in range(order):
            if k != j:
                w[:, j] *= (t - ts[:, k]) / (ts[:, j] - ts[:, k])
    out = np.einsum("mj,mjc->mc", w, ps)
    spacing = np.median(np.diff(tt))
    bad = (t < ts[:, 0] - 1.0) | (t > ts[:, -1] + 1.0) | ~np.all(np.isfinite(ps), axis=(1, 2)) | \
        (np.max(np.diff(ts, axis=1), axis=1) > 3.0 * spacing)
    out[bad] = np.nan
    return out


def sp3_and_clk(files: list[Path]) -> tuple[list[Path], list[Path]]:
    sp3 = [Path(f) for f in files if re.search(r"\.sp3$|orb", Path(f).name, re.I) and not
           re.search(r"\.clk$|_clk\.", Path(f).name, re.I)]
    clk = [Path(f) for f in files if re.search(r"\.clk$|_clk\.", Path(f).name, re.I)]
    return sp3, clk
