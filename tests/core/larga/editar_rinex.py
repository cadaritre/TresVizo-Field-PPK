"""Copias editadas de un RINEX 3 de observación para las pruebas de robustez (se importa como módulo).

Huecos, épocas sueltas que faltan, otro intervalo, épocas intermedias interpoladas (un receptor «a 1 Hz» cuyas
épocas de más no coinciden con la base) y etiquetas de tiempo corridas unos ms con observaciones coherentes: un reloj
de receptor adelantado δ etiqueta la misma medida en t + δ, y el código y la fase llevan c·δ (en ciclos, f·δ)."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from tresvizo_ppk.core.larga.senales import C_LIGHT, FREQ


def _parse(path: Path):
    lines = Path(path).read_text(encoding="ascii", errors="replace").splitlines()
    end = next(i for i, line in enumerate(lines) if line[60:].strip() == "END OF HEADER")
    types: dict[str, list[str]] = {}
    cur = None
    for line in lines[:end]:
        if line[60:].strip() == "SYS / # / OBS TYPES":
            if line[0] != " ":
                cur = line[0]
                types[cur] = []
            types[cur] += line[7:60].split()
    epochs = []
    i = end + 1
    while i < len(lines):
        head = lines[i]
        n = int(head[32:35])
        when = datetime(int(head[2:6]), int(head[7:9]), int(head[10:12]), int(head[13:15]), int(head[16:18])) + \
            timedelta(seconds=float(head[18:29]))
        epochs.append((when, head, lines[i + 1:i + 1 + n]))
        i += 1 + n
    return lines[:end + 1], types, epochs


def _head(when: datetime, template: str, count: int) -> str:
    sec = when.second + when.microsecond / 1e6
    return (f"> {when.year:4d} {when.month:02d} {when.day:02d} {when.hour:02d} {when.minute:02d}{sec:11.7f}"
            f"  0{count:3d}" + template[35:])


def _write(dst: Path, header: list[str], epochs) -> Path:
    out = list(header)
    for when, head, recs in epochs:
        out.append(_head(when, head, len(recs)))
        out += recs
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out) + "\n", encoding="ascii")
    return dst


def _fields(rec: str, n: int) -> list[str]:
    rec = rec.ljust(3 + 16 * n)
    return [rec[3 + 16 * k:3 + 16 * (k + 1)] for k in range(n)]


def keep(src: Path, dst: Path, predicate) -> Path:
    """Solo las épocas con predicate(datetime) verdadero (huecos, épocas sueltas, otro intervalo)."""
    header, _, epochs = _parse(src)
    return _write(dst, header, [e for e in epochs if predicate(e[0])])


def shifted(src: Path, dst: Path, offsets_s) -> Path:
    """Etiquetas corridas `offsets_s(k)` segundos en la época k, con código y fase coherentes (reloj del receptor).
    Solo GPS, Galileo y BeiDou (GLONASS necesitaría el canal de cada satélite)."""
    header, types, epochs = _parse(src)
    out = []
    for k, (when, head, recs) in enumerate(epochs):
        d = float(offsets_s(k))
        new = []
        for rec in recs:
            system = rec[0]
            if system not in "GEC":
                continue
            fields = _fields(rec, len(types[system]))
            for j, t in enumerate(types[system]):
                text = fields[j][:14]
                if not text.strip() or t[0] not in "CL":
                    continue
                v = float(text)
                v += C_LIGHT * d if t[0] == "C" else FREQ[system][t[1]] * d
                fields[j] = f"{v:14.3f}" + fields[j][14:]
            new.append((rec[:3] + "".join(fields)).rstrip())
        out.append((when + timedelta(seconds=d), head, new))
    return _write(dst, header, out)


def densified(src: Path, dst: Path, step_s: float) -> Path:
    """Épocas intermedias cada `step_s` entre las originales, con código y fase interpolados (cúbica de 4 épocas):
    sirven para un receptor a más frecuencia que la base; esas épocas no tienen pareja y se descartan."""
    header, types, epochs = _parse(src)
    base_t = np.array([(e[0] - epochs[0][0]).total_seconds() for e in epochs])
    by_sat: dict[str, np.ndarray] = {}
    for k, (_, _, recs) in enumerate(epochs):
        for rec in recs:
            sat = rec[:3].replace(" ", "0")
            if sat[0] not in "GEC":
                continue
            fields = _fields(rec, len(types[sat[0]]))
            row = by_sat.setdefault(sat, np.full((len(epochs), len(fields)), np.nan))
            for j, f in enumerate(fields):
                if f[:14].strip():
                    row[k, j] = float(f[:14])
    out = []
    for k in range(len(epochs)):
        when, head, recs = epochs[k]
        out.append((when, head, [r for r in recs if r[0] in "GEC"]))
        if k + 1 >= len(epochs) or k < 1 or k + 2 >= len(epochs):
            continue
        idx = [k - 1, k, k + 1, k + 2]
        t = base_t[k] + step_s
        while t < base_t[k + 1] - 1e-6:
            recs_i = []
            for sat, vals in by_sat.items():
                block = vals[idx]
                if np.any(~np.isfinite(block).any(axis=1)):
                    continue
                parts = []
                for j in range(block.shape[1]):
                    col = block[:, j]
                    if not np.all(np.isfinite(col)):
                        parts.append(" " * 16)
                        continue
                    coef = np.polyfit(base_t[idx] - base_t[k], col - col[1], 3)
                    parts.append(f"{col[1] + np.polyval(coef, t - base_t[k]):14.3f}  ")
                recs_i.append((sat + "".join(parts)).rstrip())
            out.append((epochs[0][0] + timedelta(seconds=t), head, recs_i))
            t += step_s
    out.sort(key=lambda e: e[0])
    return _write(dst, header, out)
