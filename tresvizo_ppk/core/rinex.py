"""RINEX de observación 2.11, 3.x y 4.0x: cabecera, épocas, unión de archivos y recorte a una ventana.

La unión trabaja sobre el texto de cada época (sin reescribir los valores) cuando todos los archivos declaran los
mismos tipos de observación, que es el caso de los archivos horarios o diarios de una estación; si los tipos
difieren (una estación que cambió de receptor a media sesión), se reescribe cada registro con la unión de los
tipos, sin perder ningún valor. Las épocas repetidas en dos archivos (traslape) se escriben una sola vez.

Los tiempos de las épocas son los del archivo (sistema de tiempo de la cabecera, GPS casi siempre) y se manejan
como `datetime` ingenuos: `rinex_time_to_utc()` los pasa a UTC.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, TextIO

from ..errors import UserError
from .geodesy import gps_to_utc

PROGRAM = "TresVizo Field PPK"


# --- Cabecera ---------------------------------------------------------------------------------------------------------

@dataclass
class RinexHeader:
    path: Path
    version: float
    file_type: str                         # «O» observación, «N»/«G»/«L»… navegación, «C» reloj, «M» meteorológico
    system: str                            # «G», «R», «E», «C», «J», «S», «I» o «M» (mixto)
    lines: list[str]                       # todas las líneas de la cabecera, con END OF HEADER
    marker: str = ""
    marker_number: str = ""
    receiver_number: str = ""
    receiver_type: str = ""
    receiver_version: str = ""
    antenna_number: str = ""
    antenna_type: str = ""
    radome: str = ""
    approx_xyz: tuple[float, float, float] | None = None
    delta_hen: tuple[float, float, float] = (0.0, 0.0, 0.0)
    obs_types: dict[str, list[str]] = field(default_factory=dict)   # v3/v4: por sistema; v2: {"*": [...]}
    interval: float | None = None
    first_obs: datetime | None = None
    last_obs: datetime | None = None
    time_system: str = "GPS"
    leap_seconds: int | None = None
    glonass_slots: dict[str, int] = field(default_factory=dict)     # «R09» → canal k
    data_offset: int = 0                   # byte donde empiezan los datos

    @property
    def major(self) -> int:
        return int(self.version)

    @property
    def is_observation(self) -> bool:
        return self.file_type == "O"

    @property
    def is_navigation(self) -> bool:
        return self.file_type in ("N", "G", "H", "L", "J", "E", "I")

    @property
    def is_clock(self) -> bool:
        return self.file_type in ("C", "CLOCK")


def _label(line: str) -> str:
    return line[60:80].strip()


def _float(text: str, default: float | None = None) -> float | None:
    text = text.strip().replace("D", "E").replace("d", "e")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _parse_time_of_obs(line: str) -> tuple[datetime | None, str]:
    try:
        y, mo, d, h, mi = (int(line[i:i + 6]) for i in range(0, 30, 6))
        sec = float(line[30:43])
    except ValueError:
        return None, "GPS"
    system = line[48:51].strip() or "GPS"
    return _make_time(y, mo, d, h, mi, sec), system


def _make_time(y: int, mo: int, d: int, h: int, mi: int, sec: float) -> datetime:
    whole = int(math.floor(sec))
    micro = int(round((sec - whole) * 1e6))
    if micro >= 1_000_000:
        whole += 1
        micro -= 1_000_000
    return datetime(y, mo, d, h, mi) + timedelta(seconds=whole, microseconds=micro)


def read_header(path: Path) -> RinexHeader:
    """Lee la cabecera de un RINEX (observación o navegación, 2.x a 4.x)."""
    path = Path(path)
    lines: list[str] = []
    with open(path, "rb") as handle:
        offset = 0
        for raw in handle:
            offset += len(raw)
            line = raw.decode("latin-1").rstrip("\r\n")
            lines.append(line)
            if _label(line) == "END OF HEADER":
                break
            if len(lines) > 2000:
                raise UserError(f"«{path.name}» no parece un RINEX: la cabecera no termina.")
        else:
            raise UserError(f"«{path.name}» no parece un RINEX: no tiene «END OF HEADER».")
    first = lines[0] if lines else ""
    if "RINEX VERSION / TYPE" not in first:
        raise UserError(f"«{path.name}» no es un archivo RINEX.")
    version = _float(first[0:9], 0.0) or 0.0
    file_type = first[20:21].upper()
    system = (first[40:41].strip() or "G").upper()
    if "CLOCK" in first[20:40].upper() or "CLOCK DATA" in first.upper():
        file_type = "CLOCK"
    if version < 3 and file_type in ("N",):
        system = "G"
    elif version < 3 and file_type == "G":
        system = "R"
    header = RinexHeader(path=path, version=version, file_type=file_type, system=system, lines=lines,
                         data_offset=offset)
    pending_sys = ""
    pending_count = 0
    v2_types: list[str] = []
    v2_count = 0
    for line in lines[1:]:
        label = _label(line)
        if label == "MARKER NAME":
            header.marker = line[0:60].strip()
        elif label == "MARKER NUMBER":
            header.marker_number = line[0:20].strip()
        elif label == "REC # / TYPE / VERS":
            header.receiver_number = line[0:20].strip()
            header.receiver_type = line[20:40].strip()
            header.receiver_version = line[40:60].strip()
        elif label == "ANT # / TYPE":
            header.antenna_number = line[0:20].strip()
            header.antenna_type = line[20:36].strip()
            header.radome = line[36:40].strip()
        elif label == "APPROX POSITION XYZ":
            values = [_float(line[i:i + 14]) for i in (0, 14, 28)]
            if all(v is not None for v in values):
                header.approx_xyz = (values[0], values[1], values[2])
        elif label == "ANTENNA: DELTA H/E/N":
            values = [_float(line[i:i + 14], 0.0) for i in (0, 14, 28)]
            header.delta_hen = (values[0] or 0.0, values[1] or 0.0, values[2] or 0.0)
        elif label == "SYS / # / OBS TYPES":
            if line[0:1].strip():
                pending_sys = line[0:1]
                pending_count = int(line[3:6])
                header.obs_types[pending_sys] = []
            types = line[7:58].split()
            header.obs_types.setdefault(pending_sys, []).extend(types)
        elif label == "# / TYPES OF OBSERV":
            if line[0:6].strip():
                v2_count = int(line[0:6])
            v2_types.extend(line[6:60].split())
        elif label == "INTERVAL":
            header.interval = _float(line[0:10])
        elif label == "TIME OF FIRST OBS":
            header.first_obs, header.time_system = _parse_time_of_obs(line)
        elif label == "TIME OF LAST OBS":
            header.last_obs, _ = _parse_time_of_obs(line)
        elif label == "LEAP SECONDS":
            try:
                header.leap_seconds = int(line[0:6])
            except ValueError:
                pass
        elif label == "GLONASS SLOT / FRQ #":
            body = line[4:60]
            for k in range(0, len(body), 7):
                item = body[k:k + 7]
                if item.strip():
                    sat = item[0:3].replace(" ", "0")
                    try:
                        header.glonass_slots[sat] = int(item[3:7])
                    except ValueError:
                        pass
    if v2_types:
        header.obs_types = {"*": v2_types[:v2_count] if v2_count else v2_types}
    if pending_sys and pending_count and len(header.obs_types.get(pending_sys, [])) > pending_count:
        header.obs_types[pending_sys] = header.obs_types[pending_sys][:pending_count]
    if not header.time_system:
        header.time_system = "GPS"
    return header


def classify(path: Path) -> str:
    """«obs», «nav», «sp3», «clk», «meridian» (grabación .bin/.part) o «unknown», por el contenido."""
    path = Path(path)
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return "unknown"
    text = head.decode("latin-1", "replace")
    first = text.splitlines()[0] if text else ""
    if first.startswith("#a") or first.startswith("#b") or first.startswith("#c") or first.startswith("#d"):
        if len(first) > 2 and first[2] in "PV":
            return "sp3"
    if "RINEX VERSION / TYPE" in first:
        kind = first[20:21].upper()
        if "CLOCK" in first.upper() or kind == "C" and "CLOCK" in first[20:60].upper():
            return "clk"
        if kind == "O":
            return "obs"
        if kind in ("N", "G", "H", "L", "J", "E") or "NAV" in first[20:60].upper():
            return "nav"
        if kind == "C":
            return "clk"
        if kind == "M":
            return "met"
        return "unknown"
    if b"$PTVZ,META" in head[:4096] or path.suffix.lower() in (".bin", ".part"):
        return "meridian"
    return "unknown"


# --- Épocas -----------------------------------------------------------------------------------------------------------

@dataclass
class EpochBlock:
    time: datetime
    flag: int
    lines: list[str]                       # la línea de época, las de continuación y las de los satélites
    satellites: list[str] = field(default_factory=list)


def _sat_id(text: str, default_system: str = "G") -> str:
    text = text.rstrip()
    if not text.strip():
        return ""
    system = text[0] if text[0].isalpha() else default_system
    number = text[1:3] if text[0].isalpha() else text[0:3]
    number = number.strip()
    if not number.isdigit():
        return ""
    return f"{system}{int(number):02d}"


def iter_epochs(header: RinexHeader) -> Iterator[EpochBlock]:
    """Recorre las épocas de un RINEX de observación (v2, v3 o v4) como bloques de texto."""
    with open(header.path, "rb") as handle:
        handle.seek(header.data_offset)
        reader = _LineReader(handle)
        if header.major >= 3:
            yield from _iter_v3(reader, header)
        else:
            yield from _iter_v2(reader, header)


class _LineReader:
    def __init__(self, handle) -> None:
        self.handle = handle

    def next(self) -> str | None:
        raw = self.handle.readline()
        if not raw:
            return None
        return raw.decode("latin-1").rstrip("\r\n")


def _iter_v3(reader: _LineReader, header: RinexHeader) -> Iterator[EpochBlock]:
    while True:
        line = reader.next()
        if line is None:
            return
        if not line.startswith(">"):
            continue                      # basura entre épocas: se ignora
        try:
            y, mo, d, h, mi = int(line[2:6]), int(line[7:9]), int(line[10:12]), int(line[13:15]), int(line[16:18])
            sec = float(line[18:29])
            flag = int(line[31:32] or 0)
            count = int(line[32:35])
        except ValueError:
            continue
        lines = [line]
        sats: list[str] = []
        for _ in range(count):
            row = reader.next()
            if row is None:
                break
            lines.append(row)
            if flag <= 1 or flag == 6:
                sats.append(_sat_id(row[0:3]))
        yield EpochBlock(time=_make_time(y, mo, d, h, mi, sec), flag=flag, lines=lines, satellites=sats)


def _iter_v2(reader: _LineReader, header: RinexHeader) -> Iterator[EpochBlock]:
    n_types = len(header.obs_types.get("*", []))
    lines_per_sat = max(1, math.ceil(n_types / 5))
    default_system = header.system if header.system in "GRESJ" else "G"
    while True:
        line = reader.next()
        if line is None:
            return
        if len(line) < 32 or not line[1:3].strip().isdigit():
            continue
        try:
            yy = int(line[1:3])
            mo, d, h, mi = int(line[4:6]), int(line[7:9]), int(line[10:12]), int(line[13:15])
            sec = float(line[15:26])
            flag = int(line[28:29] or 0)
            count = int(line[29:32])
        except ValueError:
            continue
        year = yy + (2000 if yy < 80 else 1900)
        lines = [line]
        if flag > 1 and flag != 6:
            for _ in range(count):
                row = reader.next()
                if row is None:
                    break
                lines.append(row)
            yield EpochBlock(time=_make_time(year, mo, d, h, mi, sec), flag=flag, lines=lines)
            continue
        sat_text = line[32:68]
        while len(sat_text) < 3 * count:
            row = reader.next()
            if row is None:
                break
            lines.append(row)
            sat_text += row[32:68]
        sats = [_sat_id(sat_text[3 * k:3 * k + 3], default_system) for k in range(count)]
        for _ in range(count * lines_per_sat):
            row = reader.next()
            if row is None:
                break
            lines.append(row)
        yield EpochBlock(time=_make_time(year, mo, d, h, mi, sec), flag=flag, lines=lines, satellites=sats)


def epoch_values(header: RinexHeader, block: EpochBlock) -> dict[str, dict[str, tuple[float, int]]]:
    """Valores de una época: satélite → tipo → (valor, LLI). Solo épocas de observación (bandera 0, 1 o 6)."""
    result: dict[str, dict[str, tuple[float, int]]] = {}
    if block.flag > 1 and block.flag != 6:
        return result
    if header.major >= 3:
        for row in block.lines[1:]:
            sat = _sat_id(row[0:3])
            types = header.obs_types.get(sat[:1], [])
            values: dict[str, tuple[float, int]] = {}
            for k, code in enumerate(types):
                field_text = row[3 + 16 * k: 3 + 16 * k + 16]
                value = _float(field_text[0:14])
                if value is None or value == 0.0:
                    continue
                lli_text = field_text[14:15]
                values[code] = (value, int(lli_text) if lli_text.isdigit() else 0)
            if sat:
                result[sat] = values
        return result
    types = header.obs_types.get("*", [])
    per_sat = max(1, math.ceil(len(types) / 5))
    count = len(block.satellites)
    data_lines = block.lines[len(block.lines) - count * per_sat:]
    for i, sat in enumerate(block.satellites):
        rows = data_lines[i * per_sat:(i + 1) * per_sat]
        values = {}
        for k, code in enumerate(types):
            row = rows[k // 5] if k // 5 < len(rows) else ""
            col = 16 * (k % 5)
            field_text = row[col:col + 16]
            value = _float(field_text[0:14])
            if value is None or value == 0.0:
                continue
            lli_text = field_text[14:15]
            values[code] = (value, int(lli_text) if lli_text.isdigit() else 0)
        if sat:
            result[sat] = values
    return result


# --- Escritura --------------------------------------------------------------------------------------------------------

def _header_line(content: str, label: str) -> str:
    return f"{content:<60.60}{label:<20}"


def _time_line(moment: datetime, system: str, label: str) -> str:
    sec = moment.second + moment.microsecond / 1e6
    return _header_line(f"{moment.year:6d}{moment.month:6d}{moment.day:6d}{moment.hour:6d}{moment.minute:6d}"
                        f"{sec:13.7f}     {system:<3}", label)


def _obs_types_lines(version: float, obs_types: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    if version >= 3:
        for system in sorted(obs_types, key=lambda s: "GRECJSI".find(s) if s in "GRECJSI" else 99):
            types = obs_types[system]
            for k in range(0, max(1, len(types)), 13):
                chunk = types[k:k + 13]
                prefix = f"{system}  {len(types):3d}" if k == 0 else "      "
                out.append(_header_line(prefix + "".join(f" {t:>3}" for t in chunk), "SYS / # / OBS TYPES"))
    else:
        types = obs_types.get("*", [])
        for k in range(0, max(1, len(types)), 9):
            chunk = types[k:k + 9]
            prefix = f"{len(types):6d}" if k == 0 else "      "
            out.append(_header_line(prefix + "".join(f"    {t:>2}" for t in chunk), "# / TYPES OF OBSERV"))
    return out


def build_header(base: RinexHeader, *, first: datetime | None, last: datetime | None,
                 obs_types: dict[str, list[str]] | None = None, comments: list[str] = (),
                 antenna_delta_h: float | None = None, marker: str | None = None,
                 glonass_slots: dict[str, int] | None = None) -> list[str]:
    """Cabecera nueva a partir de la de `base`: tiempos de la primera y la última época, tipos de observación (si
    cambian), sin los conteos por satélite (que ya no valdrían) y con comentarios del proceso."""
    drop = {"TIME OF FIRST OBS", "TIME OF LAST OBS", "# OF SATELLITES", "PRN / # OF OBS", "END OF HEADER"}
    if obs_types is not None:
        drop |= {"SYS / # / OBS TYPES", "# / TYPES OF OBSERV"}
    if glonass_slots:
        drop.add("GLONASS SLOT / FRQ #")
    slots_written = not glonass_slots
    out: list[str] = []
    types_written = obs_types is None
    pgm_written = False
    for line in base.lines:
        label = _label(line)
        if label in drop:
            if not slots_written and label == "GLONASS SLOT / FRQ #":
                out.extend(_glonass_slot_lines(glonass_slots))
                slots_written = True
            if not types_written and label in ("SYS / # / OBS TYPES", "# / TYPES OF OBSERV"):
                out.extend(_obs_types_lines(base.version, obs_types))
                types_written = True
            continue
        if label == "ANTENNA: DELTA H/E/N" and antenna_delta_h is not None:
            out.append(_header_line(f"{antenna_delta_h:14.4f}{base.delta_hen[1]:14.4f}{base.delta_hen[2]:14.4f}",
                                    label))
            continue
        if label == "MARKER NAME" and marker is not None:
            out.append(_header_line(marker, label))
            continue
        if label == "PGM / RUN BY / DATE":
            if not pgm_written:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d %H%M%S UTC" if base.major >= 3 else "%d-%b-%y %H:%M")
                out.append(_header_line(f"{PROGRAM:<20}{'':<20}{stamp:<20}", label))
                pgm_written = True
                comments = ["Original: " + " ".join(line[0:60].split()) + "."] + list(comments)
            continue
        out.append(line[0:60].ljust(60) + line[60:80].ljust(20))
    if not types_written:
        out.extend(_obs_types_lines(base.version, obs_types or {}))
    if not slots_written:
        out.extend(_glonass_slot_lines(glonass_slots))
    for text in comments:
        for chunk in _wrap(text, 60):
            out.append(_header_line(chunk, "COMMENT"))
    if first is not None:
        out.append(_time_line(first, base.time_system or "GPS", "TIME OF FIRST OBS"))
    if last is not None:
        out.append(_time_line(last, base.time_system or "GPS", "TIME OF LAST OBS"))
    out.append(_header_line("", "END OF HEADER"))
    # la de PGM / RUN BY / DATE original queda como historia: RINEX permite varias
    return out


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _glonass_slot_lines(slots: dict[str, int]) -> list[str]:
    items = sorted(slots.items())
    out = []
    for k in range(0, max(1, len(items)), 8):
        chunk = items[k:k + 8]
        prefix = f"{len(items):3d} " if k == 0 else "    "
        out.append(_header_line(prefix + "".join(f"{sat} {frq:2d} " for sat, frq in chunk), "GLONASS SLOT / FRQ #"))
    return out


def _format_value(value: float | None, lli: int = 0) -> str:
    if value is None:
        return " " * 16
    text = f"{value:14.3f}"
    if len(text) > 14:
        text = " " * 14              # fuera de rango del formato F14.3: se deja vacío, como hacen los convertidores
    return text + (str(lli) if lli else " ") + " "


def render_epoch(header: RinexHeader, block: EpochBlock, target_types: dict[str, list[str]]) -> list[str]:
    """Reescribe una época con otros tipos de observación (unión), conservando valores, LLI y fuerza de señal."""
    if block.flag > 1 and block.flag != 6:
        return block.lines
    raw = _raw_fields(header, block)
    out: list[str] = []
    if header.major >= 3:
        out.append(block.lines[0])
        for sat, fields in raw.items():
            types = target_types.get(sat[:1], [])
            out.append((sat + "".join(fields.get(t, " " * 16) for t in types)).rstrip())
        return out
    types = target_types.get("*", [])
    out.extend(block.lines[:len(block.lines) - len(raw) * max(1, math.ceil(len(header.obs_types["*"]) / 5))])
    for sat, fields in raw.items():
        cells = [fields.get(t, " " * 16) for t in types]
        for k in range(0, max(1, len(cells)), 5):
            out.append("".join(cells[k:k + 5]).rstrip())
    return out


def _raw_fields(header: RinexHeader, block: EpochBlock) -> dict[str, dict[str, str]]:
    """Campos de 16 caracteres sin interpretar: satélite → tipo → texto."""
    result: dict[str, dict[str, str]] = {}
    if header.major >= 3:
        for row in block.lines[1:]:
            sat = _sat_id(row[0:3])
            types = header.obs_types.get(sat[:1], [])
            result[sat] = {code: row[3 + 16 * k: 3 + 16 * k + 16].ljust(16)
                           for k, code in enumerate(types) if row[3 + 16 * k: 3 + 16 * k + 14].strip()}
        return result
    types = header.obs_types.get("*", [])
    per_sat = max(1, math.ceil(len(types) / 5))
    count = len(block.satellites)
    data_lines = block.lines[len(block.lines) - count * per_sat:]
    for i, sat in enumerate(block.satellites):
        rows = data_lines[i * per_sat:(i + 1) * per_sat]
        fields = {}
        for k, code in enumerate(types):
            row = rows[k // 5] if k // 5 < len(rows) else ""
            text = row[16 * (k % 5):16 * (k % 5) + 16]
            if text[0:14].strip():
                fields[code] = text.ljust(16)
        result[sat] = fields
    return result


# --- Unión y recorte --------------------------------------------------------------------------------------------------

@dataclass
class MergeReport:
    output: Path
    header: RinexHeader
    files: list[Path]
    epochs: int
    first: datetime | None
    last: datetime | None
    duplicates: int = 0
    rewritten: bool = False                # True si hubo que unir tipos de observación distintos
    warnings: list[str] = field(default_factory=list)


def _union_types(headers: list[RinexHeader]) -> dict[str, list[str]]:
    union: dict[str, list[str]] = {}
    for h in headers:
        for system, types in h.obs_types.items():
            target = union.setdefault(system, [])
            for t in types:
                if t not in target:
                    target.append(t)
    return union


def merge_observations(paths: list[Path], output: Path, *, start: datetime | None = None,
                       end: datetime | None = None, marker: str | None = None,
                       antenna_delta_h: float | None = None, cancel=None) -> MergeReport:
    """Une RINEX de observación de una misma estación en uno solo, ordenado y sin épocas repetidas, y lo recorta a
    [start, end] (tiempos del archivo, GPS) si se dan."""
    if not paths:
        raise UserError("No hay archivos de observación que unir.")
    headers = [read_header(p) for p in paths]
    for h in headers:
        if not h.is_observation:
            raise UserError(f"«{h.path.name}» no es un RINEX de observación.")
    majors = {h.major for h in headers}
    if len(majors) > 1:
        names = ", ".join(f"{h.path.name} ({h.version:g})" for h in headers)
        raise UserError("Los archivos de una misma estación tienen que ser de la misma versión de RINEX (todos 2.11 o "
                        f"todos 3.x/4.x): {names}. Baja todos en la misma versión.")
    markers = {(h.marker or "").strip().upper()[:4] for h in headers if h.marker.strip()}
    if len(markers) > 1:
        raise UserError("Los archivos no son de la misma estación (MARKER NAME: " + ", ".join(sorted(markers))
                        + "). Une solo los de una estación.")
    headers.sort(key=lambda h: (h.first_obs or datetime.max, h.path.name))
    base = headers[0]
    same_types = all(h.obs_types == base.obs_types for h in headers)
    target_types = base.obs_types if same_types else _union_types(headers)
    report = MergeReport(output=output, header=base, files=[h.path for h in headers], epochs=0, first=None,
                         last=None, rewritten=not same_types)
    if not same_types:
        report.warnings.append("Los archivos declaran tipos de observación distintos: se unieron con todos los tipos "
                               "(los que falten en un archivo quedan vacíos en sus épocas).")
    systems = {h.time_system for h in headers}
    if len(systems) > 1:
        raise UserError("Los archivos usan sistemas de tiempo distintos (" + ", ".join(sorted(systems)) + ").")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    last_time: datetime | None = None
    tolerance = timedelta(milliseconds=1)
    with open(tmp, "w", encoding="latin-1", newline="\n") as out:
        body = _BodyWriter(out)
        for h in headers:
            for block in iter_epochs(h):
                if cancel is not None and cancel.is_set():
                    out.close()
                    tmp.unlink(missing_ok=True)
                    from ..errors import Cancelled
                    raise Cancelled()
                if block.flag > 1:
                    # eventos 2, 3 y 5 (antena en movimiento, nueva estación, evento externo) se conservan si caen
                    # en la ventana; la 4 (cabecera intercalada) y la 6 (saltos de ciclo) no aplican al archivo unido
                    if block.flag in (2, 3, 5) and (start is None or block.time >= start) and \
                            (end is None or block.time <= end):
                        body.write(block.lines)
                    continue
                if start is not None and block.time < start - tolerance:
                    continue
                if end is not None and block.time > end + tolerance:
                    continue
                if last_time is not None and block.time <= last_time + tolerance:
                    report.duplicates += 1
                    continue
                lines = block.lines if same_types else render_epoch(h, block, target_types)
                body.write(lines)
                last_time = block.time
                report.epochs += 1
                if report.first is None:
                    report.first = block.time
                report.last = block.time
    if report.epochs == 0:
        tmp.unlink(missing_ok=True)
        raise UserError("No quedó ninguna época de observación"
                        + (" en la ventana pedida" if start or end else "") + ".")
    if len(headers) == 1:
        comments = [f"Archivo de origen: {headers[0].path.name}."]
    else:
        comments = [f"Unido por {PROGRAM} de {len(headers)} archivos, de {headers[0].path.name} a "
                    f"{headers[-1].path.name}."]
    slots: dict[str, int] = {}
    for h in headers:
        slots.update(h.glonass_slots)
    if start or end:
        comments.append("Recortado a la ventana de proceso.")
    new_header = build_header(base, first=report.first, last=report.last,
                              obs_types=None if same_types else target_types, comments=comments,
                              antenna_delta_h=antenna_delta_h, marker=marker,
                              glonass_slots=slots if len(headers) > 1 and slots else None)
    with open(output, "w", encoding="latin-1", newline="\n") as final, open(tmp, "r", encoding="latin-1") as src:
        final.write("\n".join(new_header) + "\n")
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            final.write(chunk)
    tmp.unlink(missing_ok=True)
    report.header = read_header(output)
    if report.duplicates:
        report.warnings.append(f"{report.duplicates} épocas repetidas entre archivos (traslape) se escribieron una "
                               "sola vez.")
    return report


class _BodyWriter:
    def __init__(self, handle: TextIO) -> None:
        self.handle = handle

    def write(self, lines: list[str]) -> None:
        self.handle.write("\n".join(lines) + "\n")


def scan_epochs(header: RinexHeader) -> tuple[int, datetime | None, datetime | None, float | None]:
    """Número de épocas de observación, primera, última e intervalo dominante (moda de las diferencias)."""
    count = 0
    first = last = None
    previous = None
    diffs: dict[float, int] = {}
    for block in iter_epochs(header):
        if block.flag > 1 and block.flag != 6:
            continue
        count += 1
        if first is None:
            first = block.time
        if previous is not None:
            step = round((block.time - previous).total_seconds(), 3)
            if step > 0:
                diffs[step] = diffs.get(step, 0) + 1
        previous = block.time
        last = block.time
    interval = max(diffs.items(), key=lambda kv: (kv[1], -kv[0]))[0] if diffs else header.interval
    return count, first, last, interval


def rinex_time_to_utc(moment: datetime | None, time_system: str = "GPS") -> datetime | None:
    """Tiempo de época del RINEX → UTC con zona."""
    if moment is None:
        return None
    aware = moment.replace(tzinfo=timezone.utc)
    if time_system in ("GPS", "GAL", "QZS", "IRN", ""):
        return gps_to_utc(aware)
    if time_system == "BDT":
        return gps_to_utc(aware + timedelta(seconds=14))
    if time_system == "GLO":
        return aware - timedelta(hours=3)
    return aware


def utc_to_rinex_time(moment: datetime, time_system: str = "GPS") -> datetime:
    from .geodesy import utc_to_gps
    utc = moment.astimezone(timezone.utc)
    if time_system in ("GPS", "GAL", "QZS", "IRN", ""):
        return utc_to_gps(utc).replace(tzinfo=None)
    if time_system == "BDT":
        return (utc_to_gps(utc) - timedelta(seconds=14)).replace(tzinfo=None)
    if time_system == "GLO":
        return (utc + timedelta(hours=3)).replace(tzinfo=None)
    return utc.replace(tzinfo=None)


_RINEX3_LONG_NAME = re.compile(r"^(?P<site>[A-Z0-9]{4})\d{2}[A-Z]{3}_[RSU]_(?P<year>\d{4})(?P<doy>\d{3})", re.I)
