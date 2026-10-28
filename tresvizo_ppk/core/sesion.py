"""Editor de sesión (como el Session Editor de TBC): la ventana de tiempo que se procesa y los satélites o sistemas que
se dejan fuera (`ProcessingOptions.time_window` y `excluded_sats`).

Aquí solo se normalizan, se validan y se aplican a lo que reciben los motores: la ventana recorta la sesión común del
rover y la base (RTKLIB la recibe con `-ts`/`-te`, el procesador propio con su inicio y su fin); los sistemas fuera
salen de las constelaciones (`pos1-navsys`, `systems` del procesador propio) y los satélites sueltos van en
`pos1-exclsats` de la configuración de RTKLIB.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..errors import UserError
from ..model import ProcessingOptions, TimeWindow

SYSTEM_LETTERS = {"G": "GPS", "R": "GLONASS", "E": "Galileo", "C": "BeiDou", "J": "QZSS", "S": "SBAS", "I": "NavIC"}
LETTER_OF = {name: letter for letter, name in SYSTEM_LETTERS.items()}
ORDER = "GRECJSI"
_ITEM = re.compile(r"^([GRECJSI])(\d{1,3})?$")
MIN_WINDOW_S = 60.0          # menos de un minuto no es una sesión que se pueda procesar


def normalize_excluded(items) -> list[str]:
    """«g5», «G05», «r» → «G05», «R». Sin repetidos, en orden de sistema y PRN; un satélite cuyo sistema ya está
    fuera completo se quita (lo cubre el sistema). Un texto que no es PRN ni sistema es un error del usuario."""
    if isinstance(items, str):
        items = re.split(r"[\s,;]+", items)
    systems: set[str] = set()
    sats: set[str] = set()
    bad: list[str] = []
    for raw in items or ():
        text = str(raw).strip().upper()
        if not text:
            continue
        match = _ITEM.match(text)
        if match is None:
            bad.append(str(raw).strip())
            continue
        letter, number = match.groups()
        if number is None:
            systems.add(letter)
            continue
        prn = int(number)
        if prn <= 0 or (letter != "S" and prn > 99):
            bad.append(str(raw).strip())
            continue
        sats.add(f"{letter}{prn:02d}" if prn < 100 else f"{letter}{prn}")
    if bad:
        raise UserError("No se entiende " + ", ".join(f"«{b}»" for b in bad) + ": escribe satélites como G13, R05 o "
                        "E11, o sistemas completos con su letra (G, R, E, C, J).")
    sats = {s for s in sats if s[0] not in systems}
    return sorted(systems | sats, key=lambda s: (len(s) > 1, ORDER.find(s[0]), s))


def split(excluded) -> tuple[set[str], list[str]]:
    """(letras de los sistemas fuera completos, satélites sueltos fuera)."""
    items = normalize_excluded(excluded)
    return {s for s in items if len(s) == 1}, [s for s in items if len(s) > 1]


def constellations(options: ProcessingOptions) -> tuple[str, ...]:
    """Las constelaciones del proceso sin los sistemas que se dejaron fuera completos."""
    systems, _ = split(options.excluded_sats)
    kept = tuple(c for c in options.constellations if LETTER_OF.get(c) not in systems)
    if not kept:
        raise UserError("Se dejaron fuera todos los sistemas de satélites elegidos: deja al menos uno.")
    return kept


def excluded_satellites(options: ProcessingOptions) -> list[str]:
    """Los satélites sueltos que se dejan fuera (los de `pos1-exclsats`)."""
    return split(options.excluded_sats)[1]


def rtklib_exclsats(options: ProcessingOptions) -> str:
    """Valor de `pos1-exclsats` de RTKLIB: los PRN separados por espacios (satid2no los lee así)."""
    return " ".join(excluded_satellites(options))


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def clamp(start: datetime, end: datetime, window: TimeWindow | None, *, what: str = "") -> tuple[datetime, datetime]:
    """La sesión común [start, end] recortada a la ventana. Sin ventana, la misma. Si la ventana no deja tiempo, error
    del usuario con lo que hay (`what` dice de qué sesión se trata, p. ej. «con la base INEG»)."""
    if window is None or (window.start is None and window.end is None):
        return start, end
    w_start, w_end = _utc(window.start), _utc(window.end)
    if w_start is not None and w_end is not None and w_end <= w_start:
        raise UserError("La ventana de tiempo termina antes de empezar: revisa el inicio y el fin.")
    new_start = max(start, w_start) if w_start is not None else start
    new_end = min(end, w_end) if w_end is not None else end
    if (new_end - new_start).total_seconds() < MIN_WINDOW_S:
        where = f" {what}" if what else ""
        raise UserError(f"La ventana de tiempo ({window_text(window)}) deja menos de un minuto de la sesión{where} "
                        f"({start:%Y-%m-%d %H:%M:%S}–{end:%H:%M:%S} UTC): amplíala o quítala.")
    return new_start, new_end


def is_trimmed(window: TimeWindow | None) -> bool:
    return window is not None and (window.start is not None or window.end is not None)


def window_text(window: TimeWindow | None) -> str:
    if not is_trimmed(window):
        return "toda la sesión"
    a, b = _utc(window.start), _utc(window.end)
    if a is not None and b is not None:
        same_day = a.date() == b.date()
        return f"{a:%Y-%m-%d %H:%M:%S}–{b:%H:%M:%S} UTC" if same_day else \
            f"{a:%Y-%m-%d %H:%M:%S} – {b:%Y-%m-%d %H:%M:%S} UTC"
    if a is not None:
        return f"desde {a:%Y-%m-%d %H:%M:%S} UTC"
    return f"hasta {b:%Y-%m-%d %H:%M:%S} UTC"


def excluded_text(excluded) -> str:
    """«GLONASS completo; G13, E24» o «ninguno»."""
    systems, sats = split(excluded)
    parts = []
    if systems:
        parts.append(", ".join(SYSTEM_LETTERS[s] for s in sorted(systems, key=ORDER.find)) +
                     (" completo" if len(systems) == 1 else " completos"))
    if sats:
        parts.append(", ".join(sats))
    return "; ".join(parts) or "ninguno"


def edited(options: ProcessingOptions) -> bool:
    return is_trimmed(options.time_window) or bool(normalize_excluded(options.excluded_sats))


def describe(options: ProcessingOptions) -> list[str]:
    """Las líneas de la bitácora (vacía si no se editó la sesión)."""
    if not edited(options):
        return []
    lines = []
    if is_trimmed(options.time_window):
        lines.append(f"Editor de sesión: ventana de tiempo {window_text(options.time_window)}.")
    if normalize_excluded(options.excluded_sats):
        lines.append(f"Editor de sesión: fuera del cálculo {excluded_text(options.excluded_sats)}.")
    return lines
