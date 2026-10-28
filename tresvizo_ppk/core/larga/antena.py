"""Centros de fase completos (PCO y PCV) del ANTEX para el procesador de líneas largas.

`antex.py` del motor decide qué calibración le toca a cada antena de receptor (exacta, sin radomo o ninguna) y deja
el archivo que lee RTKLIB; aquí se leen del mismo ANTEX (el incluido más los bloques de los paquetes de red) las
variaciones por elevación y acimut (PCV) de cada frecuencia y las antenas de los satélites (por PRN y fecha), que
el procesador propio aplica él mismo.

Convenciones del ANTEX 1.4: el PCO del receptor va en norte, este y arriba desde la ARP; el del satélite en el
marco del satélite (x, y, z) desde el centro de masa. La corrección a la distancia calculada desde la ARP es
−PCO·ê + PCV(z, α) (ê: dirección del receptor al satélite en norte/este/arriba). Las PCV están en milímetros en el
archivo y aquí en metros.

Si a una antena le falta una frecuencia (p. ej. B3I en una calibración vieja), se usa la de GPS más cercana en
frecuencia (L1 para E1/B1/B1C, L2 para B3I/E5b/B2I, L5 para E5a/B2a), como hacen RTKLIB y Bernese; eso se anota.
"""
from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

import numpy as np

from .. import antex as antex_motor

# Frecuencia del ANTEX para cada (sistema, banda RINEX) y su sustituta de GPS si falta
ANTEX_FREQ = {
    ("G", "1"): "G01", ("G", "2"): "G02", ("G", "5"): "G05",
    ("E", "1"): "E01", ("E", "5"): "E05", ("E", "7"): "E07", ("E", "8"): "E08", ("E", "6"): "E06",
    ("C", "2"): "C02", ("C", "6"): "C06", ("C", "7"): "C07", ("C", "1"): "C01", ("C", "5"): "C05",
}
FALLBACK = {"E01": "G01", "C01": "G01", "C02": "G01", "C06": "G02", "E07": "G02", "C07": "G02", "E08": "G02",
            "E06": "G02", "E05": "G05", "C05": "G05", "G05": "G02"}


@dataclass
class Pattern:
    pco: np.ndarray                          # m (N, E, U) o (x, y, z)
    zen: np.ndarray                          # grados (cenit en receptores, nadir en satélites)
    noazi: np.ndarray                        # m
    azi: np.ndarray | None = None            # grados
    grid: np.ndarray | None = None           # m, (acimut, cenit)

    def pcv(self, zenith_deg, azimuth_deg=None):
        """PCV (m) interpolada (lineal en cenit; bilineal si hay malla por acimut)."""
        z = np.clip(np.asarray(zenith_deg, dtype=float), self.zen[0], self.zen[-1])
        if self.grid is None or azimuth_deg is None:
            return np.interp(z, self.zen, self.noazi)
        a = np.mod(np.asarray(azimuth_deg, dtype=float), 360.0)
        step = self.azi[1] - self.azi[0]
        ia = np.clip(np.floor(a / step).astype(int), 0, len(self.azi) - 2)
        fa = (a - self.azi[ia]) / step
        v0 = _interp_rows(self.grid[ia], self.zen, z)
        v1 = _interp_rows(self.grid[ia + 1], self.zen, z)
        return v0 * (1 - fa) + v1 * fa


def _interp_rows(rows: np.ndarray, zen: np.ndarray, z: np.ndarray) -> np.ndarray:
    step = zen[1] - zen[0]
    iz = np.clip(np.floor((z - zen[0]) / step).astype(int), 0, len(zen) - 2)
    fz = (z - zen[iz]) / step
    rows = np.atleast_2d(rows)
    idx = np.arange(rows.shape[0])
    return rows[idx, iz] * (1 - fz) + rows[idx, iz + 1] * fz


@dataclass
class Antenna:
    type: str
    radome: str
    serial: str = ""                         # PRN («G05») en las de satélite
    svn: str = ""
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    patterns: dict[str, Pattern] = field(default_factory=dict)

    def pattern(self, freq: str) -> tuple[Pattern | None, str]:
        """El patrón de una frecuencia del ANTEX y la frecuencia usada de verdad (la sustituta si falta)."""
        if freq in self.patterns:
            return self.patterns[freq], freq
        seen = set()
        while freq in FALLBACK and freq not in seen:
            seen.add(freq)
            freq = FALLBACK[freq]
            if freq in self.patterns:
                return self.patterns[freq], freq
        return None, ""


def _date(text: str) -> datetime | None:
    parts = text.split()
    if len(parts) < 6:
        return None
    y, mo, d, h, mi = (int(p) for p in parts[:5])
    sec = float(parts[5])
    return datetime(y, mo, d, h, mi, int(sec))


def parse(lines) -> tuple[dict[tuple[str, str], Antenna], dict[str, list[Antenna]]]:
    """Lee un ANTEX: (antenas de receptor por (tipo, radomo), antenas de satélite por PRN)."""
    receivers: dict[tuple[str, str], Antenna] = {}
    satellites: dict[str, list[Antenna]] = {}
    cur: Antenna | None = None
    freq = ""
    in_rms = False
    dazi = 0.0
    zen = np.zeros(0)
    pco = np.zeros(3)
    noazi: list[float] = []
    rows: list[list[float]] = []
    for raw in lines:
        line = raw.rstrip("\n")
        label = line[60:].strip()
        if label == "START OF ANTENNA":
            cur, freq, in_rms = None, "", False
        elif label == "TYPE / SERIAL NO":
            serial = line[20:40].strip()
            cur = Antenna(type=line[0:16].strip(), radome=line[16:20].strip() or "NONE",
                          serial=serial if len(serial) == 3 and serial[0].isalpha() else "",
                          svn=line[40:50].strip())
        elif cur is None:
            continue
        elif label == "DAZI":
            dazi = float(line[2:8])
        elif label == "ZEN1 / ZEN2 / DZEN":
            z1, z2, dz = float(line[2:8]), float(line[8:14]), float(line[14:20])
            zen = np.arange(z1, z2 + dz / 2, dz)
        elif label == "VALID FROM":
            cur.valid_from = _date(line[0:43])
        elif label == "VALID UNTIL":
            cur.valid_until = _date(line[0:43])
        elif label == "START OF FREQ RMS":
            in_rms = True
        elif label == "END OF FREQ RMS":
            in_rms = False
        elif in_rms:
            continue
        elif label == "START OF FREQUENCY":
            freq = line[3:6].strip()
            noazi, rows = [], []
        elif label == "NORTH / EAST / UP" and freq:
            pco = np.array([float(line[0:10]), float(line[10:20]), float(line[20:30])]) / 1000.0
        elif label == "END OF FREQUENCY" and freq:
            grid = np.array(rows) / 1000.0 if rows and dazi > 0 else None
            cur.patterns[freq] = Pattern(pco=pco.copy(), zen=zen.copy(), noazi=np.array(noazi) / 1000.0,
                                         azi=np.arange(0.0, 360.0 + dazi / 2, dazi) if grid is not None else None,
                                         grid=grid)
            freq = ""
        elif label == "END OF ANTENNA":
            if cur.serial:
                satellites.setdefault(cur.serial, []).append(cur)
            else:
                receivers.setdefault((cur.type.upper(), cur.radome.upper()), cur)
            cur = None
        elif freq and line[3:8] == "NOAZI":
            noazi = [float(v) for v in line[8:].split()]
        elif freq and dazi > 0 and line[0:8].strip():
            values = line[8:].split()
            if values:
                rows.append([float(v) for v in values])
    return receivers, satellites


@lru_cache(maxsize=1)
def _bundled() -> tuple[dict[tuple[str, str], Antenna], dict[str, list[Antenna]]]:
    with gzip.open(antex_motor.bundled_antex(), "rt", encoding="latin-1") as handle:
        return parse(handle)


def receiver_antenna(type_name: str, radome: str) -> Antenna | None:
    """Patrón completo de la antena de receptor que eligió `antex.choose` (tipo y radomo exactos del ANTEX), en el
    mismo orden que ella: el ANTEX que importó el usuario en la sesión (`antex.session`), el incluido y los paquetes de
    red. Sin el importado, una calibración de laboratorio cambiaría lo que dice el informe pero no la solución."""
    key = (type_name.strip().upper(), (radome or "NONE").strip().upper())
    block = antex_motor.receiver_block(type_name, radome or "NONE")
    if block:
        found, _ = parse(block.splitlines())
        if key in found:
            return found[key]
        if len(found) == 1:
            return next(iter(found.values()))
    receivers, _ = _bundled()
    if key in receivers:
        return receivers[key]
    extra = antex_motor._network_blocks()
    if extra:
        more, _ = parse(extra.splitlines())
        return more.get(key)
    return None


def satellite_antenna(prn: str, when: datetime) -> Antenna | None:
    _, satellites = _bundled()
    for ant in satellites.get(prn, []):
        if ant.valid_from and when < ant.valid_from:
            continue
        if ant.valid_until and when > ant.valid_until:
            continue
        return ant
    return None
