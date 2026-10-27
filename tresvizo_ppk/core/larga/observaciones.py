"""Lector de observaciones RINEX (2.11, 3.x y 4.x) a arreglos para el procesador de líneas largas.

Solo lee lo que el procesador usa: código y fase (con su indicador de pérdida de enganche, LLI) de las bandas de
`senales.PAIRS` de GPS, Galileo y BeiDou. Cada tipo queda en un arreglo épocas × satélites (NaN donde no hay dato).

RINEX 2.11 no tiene atributos de señal: se traducen como RTKLIB. GPS: C1 → C1C, P1 → C1W, L1 → L1C (y L1W),
P2 → C2W, C2 → C2L, L2 → L2W si el archivo trae P2 (seguimiento semicodificado) o L2L si solo trae C2, C5/L5 → 5X;
Galileo: C1/L1 → 1X, C5/L5 → 5X, C7/L7 → 7X. BeiDou no está definido en RINEX 2.11 y no se lee de esos archivos.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...errors import UserError
from .. import rinex
from . import senales
from .astro import gps_seconds

SYSTEMS = ("G", "E", "C")


@dataclass
class Observations:
    marker: str
    receiver: str
    times: np.ndarray                         # segundos GPS (T)
    sats: list[str]                           # S
    values: dict[str, np.ndarray] = field(default_factory=dict)   # «C1C» → (T, S) m o ciclos
    lli: dict[str, np.ndarray] = field(default_factory=dict)      # «L1C» → (T, S) int8
    types: dict[str, list[str]] = field(default_factory=dict)     # sistema → tipos disponibles (atributos RINEX 3)
    interval: float = 0.0
    approx_xyz: np.ndarray | None = None
    version: float = 3.0

    def column(self, sat: str) -> int:
        return self.sats.index(sat)


def _wanted(types: list[str], system: str) -> list[str]:
    bands = {b for pair in senales.PAIRS.get(system, []) for b in pair}
    return [t for t in types if len(t) == 3 and t[0] in "CL" and t[1] in bands]


def _v2_mapping(types: list[str]) -> dict[str, dict[str, str]]:
    """Tipo de 2 letras → tipo RINEX 3 por sistema (los que se usan)."""
    has_p2 = "P2" in types
    gps = {"C1": ["C1C"], "P1": ["C1W"], "L1": ["L1C", "L1W"], "P2": ["C2W"], "C2": ["C2L"],
           "L2": ["L2W"] if has_p2 else ["L2L"], "C5": ["C5X"], "L5": ["L5X"]}
    gal = {"C1": ["C1X"], "L1": ["L1X"], "C5": ["C5X"], "L5": ["L5X"], "C7": ["C7X"], "L7": ["L7X"]}
    return {"G": gps, "E": gal}


def read(path: Path, *, start: float | None = None, end: float | None = None, decimate_s: float | None = None,
         tolerance_s: float = 1e-3) -> Observations:
    """Lee un RINEX de observación. `start`/`end` en segundos GPS; `decimate_s`: solo épocas múltiplo de ese
    intervalo (p. ej. 30 s de un archivo de 15 s), con `tolerance_s` para etiquetas que no caen exactas en el
    segundo."""
    header = rinex.read_header(Path(path))
    if not header.is_observation:
        raise UserError(f"«{Path(path).name}» no es un RINEX de observación.")
    bdt_offset = 14.0 if header.time_system == "BDT" else 0.0
    v2 = header.major < 3
    # columnas a leer: sistema → [(índice del campo, tipo RINEX 3)]
    plan: dict[str, list[tuple[int, str]]] = {}
    types_by_sys: dict[str, list[str]] = {}
    if v2:
        raw_types = header.obs_types.get("*", [])
        mapping = _v2_mapping(raw_types)
        for system in ("G", "E"):
            cols = []
            for k, code in enumerate(raw_types):
                for t3 in mapping[system].get(code, []):
                    cols.append((k, t3))
            plan[system] = cols
            types_by_sys[system] = sorted({t for _, t in cols})
    else:
        for system in SYSTEMS:
            types = header.obs_types.get(system, [])
            wanted = _wanted(types, system)
            plan[system] = [(types.index(t), t) for t in wanted]
            types_by_sys[system] = wanted
    all_types = sorted({t for cols in plan.values() for _, t in cols})
    times: list[float] = []
    sat_index: dict[str, int] = {}
    rows: list[dict[tuple[int, str], tuple[float, int]]] = []
    in_window = 0
    for block in rinex.iter_epochs(header):
        if block.flag > 1 and block.flag != 6:
            continue
        t = gps_seconds(block.time) + bdt_offset
        if start is not None and t < start - tolerance_s:
            continue
        if end is not None and t > end + tolerance_s:
            break
        in_window += 1
        if decimate_s and abs(math.remainder(t, decimate_s)) > tolerance_s:
            continue
        epoch: dict[tuple[int, str], tuple[float, int]] = {}
        if v2:
            _parse_v2(header, block, plan, sat_index, epoch)
        else:
            _parse_v3(block, plan, sat_index, epoch)
        times.append(t)
        rows.append(epoch)
    if not times and in_window:
        raise UserError(f"Las épocas de «{Path(path).name}» no coinciden en el tiempo con el intervalo de proceso "
                        f"({decimate_s:g} s): sus etiquetas de tiempo no caen en múltiplos de {decimate_s:g} s.")
    if not times:
        raise UserError(f"«{Path(path).name}» no tiene épocas de observación en el horario pedido.")
    n_t, n_s = len(times), len(sat_index)
    values = {t: np.full((n_t, n_s), np.nan) for t in all_types}
    lli = {t: np.zeros((n_t, n_s), dtype=np.int8) for t in all_types if t[0] == "L"}
    for i, epoch in enumerate(rows):
        for (j, code), (value, flag) in epoch.items():
            values[code][i, j] = value
            if flag and code in lli:
                lli[code][i, j] = flag
    sats = sorted(sat_index, key=lambda s: sat_index[s])
    order = sorted(range(n_s), key=lambda j: (SYSTEMS.index(sats[j][0]), int(sats[j][1:])))
    sats = [sats[j] for j in order]
    values = {k: v[:, order] for k, v in values.items()}
    lli = {k: v[:, order] for k, v in lli.items()}
    times_arr = np.array(times)
    diffs = np.diff(times_arr)
    # a la centésima: con etiquetas a unos ms del segundo la mediana no da el intervalo exacto
    interval = round(float(np.median(diffs)), 2) if len(diffs) else (header.interval or 0.0)
    approx = np.array(header.approx_xyz) if header.approx_xyz and any(header.approx_xyz) else None
    return Observations(marker=header.marker, receiver=header.receiver_type, times=times_arr, sats=sats,
                        values=values, lli=lli, types=types_by_sys, interval=interval, approx_xyz=approx,
                        version=header.version)


def _field(row: str, k: int) -> tuple[float, int] | None:
    text = row[3 + 16 * k: 3 + 16 * k + 14]
    if not text.strip():
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value == 0.0:
        return None
    flag_text = row[3 + 16 * k + 14: 3 + 16 * k + 15]
    return value, (int(flag_text) if flag_text.isdigit() else 0)


def _parse_v3(block, plan, sat_index, epoch) -> None:
    for row in block.lines[1:]:
        system = row[0:1]
        cols = plan.get(system)
        if not cols:
            continue
        num = row[1:3].strip()
        if not num.isdigit():
            continue
        sat = f"{system}{int(num):02d}"
        if not senales.usable_satellite(sat):
            continue
        j = sat_index.setdefault(sat, len(sat_index))
        for k, code in cols:
            got = _field(row, k)
            if got:
                epoch[(j, code)] = got


def _parse_v2(header, block, plan, sat_index, epoch) -> None:
    types = header.obs_types.get("*", [])
    per_sat = max(1, math.ceil(len(types) / 5))
    count = len(block.satellites)
    data_lines = block.lines[len(block.lines) - count * per_sat:]
    for i, sat in enumerate(block.satellites):
        cols = plan.get(sat[:1]) if sat else None
        if not cols or not senales.usable_satellite(sat):
            continue
        lines = data_lines[i * per_sat:(i + 1) * per_sat]
        j = sat_index.setdefault(sat, len(sat_index))
        for k, code in cols:
            row = lines[k // 5] if k // 5 < len(lines) else ""
            col = 16 * (k % 5)
            text = row[col:col + 14]
            if not text.strip():
                continue
            try:
                value = float(text)
            except ValueError:
                continue
            if value == 0.0:
                continue
            flag_text = row[col + 14:col + 15]
            epoch[(j, code)] = (value, int(flag_text) if flag_text.isdigit() else 0)
