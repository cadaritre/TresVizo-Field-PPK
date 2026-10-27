"""Comprobación estricta de la sintaxis RINEX 3.04, independiente del escritor.

Se escribió a partir del texto de la norma (RINEX 3.04, IGS/RTCM, 2018-11-23: tablas A2, A3 y A4
a A13 y tablas 4 a 10 de códigos), no del código del escritor, para que un error del escritor no
pase por bueno. Devuelve la lista de errores (vacía si el archivo cumple) y lo leído.
"""
from __future__ import annotations

import re

VALID_CODES = {
    "G": {"1C", "1S", "1L", "1X", "1P", "1W", "1Y", "1M", "1N", "2C", "2D", "2S", "2L", "2X", "2P", "2W",
          "2Y", "2M", "2N", "5I", "5Q", "5X"},
    "R": {"1C", "1P", "4A", "4B", "4X", "2C", "2P", "6A", "6B", "6X", "3I", "3Q", "3X"},
    "E": {"1A", "1B", "1C", "1X", "1Z", "5I", "5Q", "5X", "7I", "7Q", "7X", "8I", "8Q", "8X", "6A", "6B",
          "6C", "6X", "6Z"},
    "S": {"1C", "5I", "5Q", "5X"},
    "J": {"1C", "1S", "1L", "1X", "1Z", "2S", "2L", "2X", "5I", "5Q", "5X", "5D", "5P", "5Z", "6S", "6L",
          "6X", "6E", "6Z"},
    "C": {"2I", "2Q", "2X", "1D", "1P", "1X", "1A", "1N", "5D", "5P", "5X", "7I", "7Q", "7X", "7D", "7P",
          "7Z", "8D", "8P", "8X", "6I", "6Q", "6X", "6A"},
    "I": {"5A", "5B", "5C", "5X", "9A", "9B", "9C", "9X"},
}
OBS_REQUIRED = ["RINEX VERSION / TYPE", "PGM / RUN BY / DATE", "MARKER NAME", "OBSERVER / AGENCY",
                "REC # / TYPE / VERS", "ANT # / TYPE", "APPROX POSITION XYZ", "ANTENNA: DELTA H/E/N",
                "SYS / # / OBS TYPES", "TIME OF FIRST OBS", "SYS / PHASE SHIFT", "GLONASS SLOT / FRQ #",
                "GLONASS COD/PHS/BIS", "END OF HEADER"]
OBS_KNOWN = set(OBS_REQUIRED) | {"COMMENT", "MARKER NUMBER", "MARKER TYPE", "SIGNAL STRENGTH UNIT", "INTERVAL",
                                 "TIME OF LAST OBS", "LEAP SECONDS", "RCV CLOCK OFFS APPL", "# OF SATELLITES",
                                 "PRN / # OF OBS", "ANTENNA: DELTA X/Y/Z", "SYS / SCALE FACTOR"}
F14_3 = re.compile(r"^ *-?\d+\.\d{3}$")
D19_12 = re.compile(r"^[ -]\d\.\d{12}[ED][+-]\d\d$")
EPOCH = re.compile(r"^> (\d{4}) (\d\d) (\d\d) (\d\d) (\d\d)( [ \d]\d\.\d{7})  ([0-6])([ \d]{2}\d)(.*)$")
NAV_LINES = {"G": 7, "E": 7, "C": 7, "J": 7, "I": 7, "R": 3, "S": 3}


def _float(text: str, pattern: re.Pattern, errors: list[str], where: str) -> float | None:
    if not pattern.match(text):
        errors.append(f"{where}: campo mal formado {text!r}")
        return None
    return float(text.replace("D", "E"))


def check_obs(text: str) -> tuple[list[str], dict]:
    errors: list[str] = []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    info: dict = {"types": {}, "epochs": [], "comments": [], "slots": {}, "phase_shift": [], "values": {}}
    labels = []
    index = 0
    for index, line in enumerate(lines):
        if len(line) > 80:
            errors.append(f"cabecera línea {index + 1}: más de 80 columnas")
        label = line[60:80].rstrip()
        labels.append(label)
        if label not in OBS_KNOWN:
            errors.append(f"cabecera línea {index + 1}: etiqueta desconocida {label!r}")
        content = line[:60]
        if label == "RINEX VERSION / TYPE":
            if index != 0:
                errors.append("RINEX VERSION / TYPE no es la primera línea")
            if content[:9] != "     3.04" or content[20] != "O" or content[40] not in "GRECJISM":
                errors.append(f"RINEX VERSION / TYPE mal formado: {content!r}")
        elif label == "COMMENT":
            info["comments"].append(content.rstrip())
        elif label == "MARKER NAME":
            info["marker"] = content.rstrip()
        elif label == "REC # / TYPE / VERS":
            info["receiver"] = (content[0:20].rstrip(), content[20:40].rstrip(), content[40:60].rstrip())
        elif label == "ANT # / TYPE":
            info["antenna"] = (content[0:20].rstrip(), content[20:40])
            if content[36:40].strip() == "":
                errors.append("ANT # / TYPE sin radomo en las columnas 37-40")
        elif label in ("APPROX POSITION XYZ", "ANTENNA: DELTA H/E/N"):
            values = [_float(content[i:i + 14], re.compile(r"^ *-?\d+\.\d{4}$"), errors, label) for i in (0, 14, 28)]
            info[label] = values
            if content[42:].strip():
                errors.append(f"{label}: texto de más")
        elif label == "SYS / # / OBS TYPES":
            if content[0] != " ":
                system = content[0]
                if content[1:3] != "  ":
                    errors.append("SYS / # / OBS TYPES: A1,2X mal formado")
                count = int(content[3:6])
                info["types"][system] = {"count": count, "list": []}
                current = system
            else:
                if content[:6] != "      ":
                    errors.append("SYS / # / OBS TYPES: continuación mal formada")
            for k in range(13):
                field = content[6 + 4 * k:10 + 4 * k]
                if not field.strip():
                    break
                if field[0] != " " or not re.match(r"^[CLDS][0-9][A-Z]$", field[1:]):
                    errors.append(f"SYS / # / OBS TYPES: tipo mal formado {field!r}")
                info["types"][current]["list"].append(field[1:])
        elif label == "INTERVAL":
            info["interval"] = _float(content[:10], re.compile(r"^ *\d+\.\d{3}$"), errors, label)
        elif label in ("TIME OF FIRST OBS", "TIME OF LAST OBS"):
            m = re.match(r"^([ \d]{6})([ \d]{6})([ \d]{6})([ \d]{6})([ \d]{6})([ \d]{5}\.\d{7})     (GPS)$", content.rstrip())
            if not m:
                errors.append(f"{label} mal formado: {content!r}")
            else:
                info[label] = tuple(int(m.group(i)) for i in range(1, 6)) + (float(m.group(6)),)
        elif label == "SYS / PHASE SHIFT":
            stripped = content.rstrip()
            if not re.match(r"^[GRECJIS]( [CLDS][0-9][A-Z]( [ -]\d\.\d{5})?)?$", stripped):
                errors.append(f"SYS / PHASE SHIFT mal formado: {stripped!r}")
            info["phase_shift"].append(stripped)
        elif label == "GLONASS SLOT / FRQ #":
            if content[:3].strip():
                info["slot_count"] = int(content[:3])
            for k in range(8):
                field = content[4 + 7 * k:11 + 7 * k]
                if not field.strip():
                    break
                m = re.match(r"^R(\d\d) ([ -]\d) $", field)
                if not m:
                    errors.append(f"GLONASS SLOT / FRQ # mal formado: {field!r}")
                    continue
                fcn = int(m.group(2))
                if not -7 <= fcn <= 13:
                    errors.append(f"canal GLONASS fuera de rango: {fcn}")
                info["slots"][int(m.group(1))] = fcn
        elif label == "GLONASS COD/PHS/BIS":
            if content.strip() and not re.match(r"^( C1C [ \d.-]{8} C1P [ \d.-]{8} C2C [ \d.-]{8} C2P [ \d.-]{8})", content):
                errors.append("GLONASS COD/PHS/BIS mal formado")
        elif label == "END OF HEADER":
            break
    for required in OBS_REQUIRED:
        if required not in labels:
            errors.append(f"falta el registro obligatorio {required}")
    for system, entry in info["types"].items():
        if entry["count"] != len(entry["list"]):
            errors.append(f"SYS / # / OBS TYPES de {system}: {entry['count']} declarados, {len(entry['list'])} listados")
        for obs in entry["list"]:
            if obs[1:] not in VALID_CODES.get(system, set()):
                errors.append(f"código {obs} no válido en RINEX 3.04 para {system}")
    if info.get("slot_count", len(info["slots"])) != len(info["slots"]):
        errors.append("GLONASS SLOT / FRQ #: el número no coincide con la lista")
    shift_systems = {s[0] for s in info["phase_shift"]}
    for system in info["types"]:
        if system not in shift_systems:
            errors.append(f"falta SYS / PHASE SHIFT de {system}")

    # Datos.
    i = index + 1
    previous = None
    while i < len(lines):
        line = lines[i]
        m = EPOCH.match(line)
        if not m:
            errors.append(f"línea {i + 1}: se esperaba un registro de época: {line[:40]!r}")
            break
        flag = int(m.group(7))
        count = int(m.group(8))
        stamp = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)),
                 float(m.group(6)))
        if flag > 1:
            errors.append(f"línea {i + 1}: bandera de evento {flag} no esperada")
        if previous is not None and stamp <= previous:
            errors.append(f"línea {i + 1}: época no creciente")
        previous = stamp
        info["epochs"].append(stamp)
        seen = set()
        for j in range(count):
            row = lines[i + 1 + j] if i + 1 + j < len(lines) else ""
            sat = row[:3]
            if not re.match(r"^[GRECJIS]\d\d$", sat):
                errors.append(f"línea {i + 2 + j}: satélite mal formado {sat!r}")
                continue
            if sat in seen:
                errors.append(f"línea {i + 2 + j}: satélite repetido {sat}")
            seen.add(sat)
            types = info["types"].get(sat[0])
            if types is None:
                errors.append(f"línea {i + 2 + j}: sistema {sat[0]} sin tipos declarados")
                continue
            body = row[3:]
            if len(body) > 16 * len(types["list"]):
                errors.append(f"línea {i + 2 + j}: más campos que tipos declarados")
            body = body.ljust(16 * len(types["list"]))
            for k, obs in enumerate(types["list"]):
                field = body[16 * k:16 * k + 16]
                value, lli, ssi = field[:14], field[14], field[15]
                if value.strip():
                    v = _float(value, F14_3, errors, f"línea {i + 2 + j} {sat} {obs}")
                    if v is not None:
                        info["values"].setdefault((stamp, sat), {})[obs] = (v, lli)
                elif lli != " " or ssi != " ":
                    errors.append(f"línea {i + 2 + j}: LLI/SSI sin valor")
                if lli not in " 01234567":
                    errors.append(f"línea {i + 2 + j}: LLI no válido {lli!r}")
                if lli.strip() and obs[0] != "L":
                    errors.append(f"línea {i + 2 + j}: LLI en una observación que no es de fase ({obs})")
                if ssi not in " 0123456789":
                    errors.append(f"línea {i + 2 + j}: SSI no válido {ssi!r}")
        i += 1 + count
    if info["epochs"]:
        if "TIME OF FIRST OBS" in info and info["TIME OF FIRST OBS"] != info["epochs"][0]:
            errors.append("TIME OF FIRST OBS no coincide con la primera época")
        if "TIME OF LAST OBS" in info and info["TIME OF LAST OBS"] != info["epochs"][-1]:
            errors.append("TIME OF LAST OBS no coincide con la última época")
    return errors, info


def check_nav(text: str) -> tuple[list[str], list[dict]]:
    errors: list[str] = []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    records: list[dict] = []
    end = None
    for index, line in enumerate(lines):
        if len(line) > 80:
            errors.append(f"cabecera línea {index + 1}: más de 80 columnas")
        label = line[60:80].rstrip()
        if index == 0:
            if label != "RINEX VERSION / TYPE" or line[:9] != "     3.04" or line[20] != "N":
                errors.append("RINEX VERSION / TYPE de navegación mal formado")
        if label not in ("RINEX VERSION / TYPE", "PGM / RUN BY / DATE", "COMMENT", "LEAP SECONDS",
                         "IONOSPHERIC CORR", "TIME SYSTEM CORR", "END OF HEADER"):
            errors.append(f"cabecera línea {index + 1}: etiqueta desconocida {label!r}")
        if label == "LEAP SECONDS" and not re.match(r"^[ \d]{5}\d", line[:6]):
            errors.append("LEAP SECONDS mal formado")
        if label == "END OF HEADER":
            end = index
            break
    if end is None:
        return ["falta END OF HEADER"], records
    i = end + 1
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([GRECJIS]\d\d) (\d{4}) (\d\d) (\d\d) (\d\d) (\d\d) (\d\d)(.{57})$", line)
        if not m:
            errors.append(f"línea {i + 1}: primera línea de registro mal formada {line[:30]!r}")
            break
        sat = m.group(1)
        values = []
        for k in range(3):
            values.append(_float(m.group(8)[19 * k:19 * k + 19], D19_12, errors, f"línea {i + 1}"))
        count = NAV_LINES[sat[0]]
        for j in range(count):
            row = lines[i + 1 + j] if i + 1 + j < len(lines) else ""
            if not row.startswith("    ") or len(row) > 80 or (len(row) - 4) % 19:
                errors.append(f"línea {i + 2 + j}: línea de órbita mal formada")
                continue
            for k in range((len(row) - 4) // 19):
                values.append(_float(row[4 + 19 * k:23 + 19 * k], D19_12, errors, f"línea {i + 2 + j}"))
        records.append({"sat": sat, "epoch": tuple(int(m.group(g)) for g in range(2, 8)), "values": values})
        i += 1 + count
    return errors, records
