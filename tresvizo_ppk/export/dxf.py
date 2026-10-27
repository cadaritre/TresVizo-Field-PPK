"""DXF R12 (`AC1009`) de los puntos, con la especificación de las apps TresVizo Field.

Es la de `SurveyExport.dxf` de iOS y de Android (`docs/oficina/06-formatos.md` §1 y `docs/csv-export.md` §8 de las
apps), escrita aquí sin dependencias:

- Comentarios `999` al principio con el sistema, el marco y qué es la Z (en ASCII, trozos de 240 caracteres), porque
  un DXF que viaja solo pierde todo lo demás.
- `HEADER` con `$ACADVER AC1009`, `$DWGCODEPAGE ANSI_1252` y `$INSUNITS 6` (metros); `TABLES` con una tabla `LAYER`
  (las capas en orden, cada una con su color ACI y `CONTINUOUS`); `ENTITIES`; `EOF`. Sin handles. CRLF. Windows-1252,
  y lo que no cabe en esa página como `\\U+XXXX` (como lo escribe AutoCAD).
- **Una capa por código** (mayúsculas, sin acentos, `[A-Z0-9_-]` y 31 caracteres como mucho); `PUNTOS` sin código;
  `SIN_COTA` los que no tienen cota, en 2D y con un comentario (nunca una Z = 0 que parezca dato).
- Cada punto: `POINT` 3D en su capa; su nombre como `TEXT` en `TEXTO` en el mismo sitio y con la misma Z; su
  descripción, si la tiene, un renglón por debajo (1.4 alturas de texto) en `DESCRIPCION`.
- Colores: los fijos de las apps y, para los códigos, uno de su paleta elegido por FNV-1a de 32 bits sobre el nombre
  (el mismo color para la misma capa en los dos teléfonos y aquí).
- Números con tres decimales y punto.

**Lo que añade la oficina** (y se pide a las apps para que coincidan, `docs/formatos.md`): la **cota como rótulo**,
en su propia capa `COTA` (color 30), un renglón por debajo del nombre; la descripción baja entonces un renglón más.
Con `elevation_labels=False` el archivo es el de las apps sin cambiar un byte.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .formato import number

CRLF = "\r\n"
POINTS_LAYER = "PUNTOS"
NAME_LAYER = "TEXTO"
DESCRIPTION_LAYER = "DESCRIPCION"
NO_HEIGHT_LAYER = "SIN_COTA"
ELEVATION_LAYER = "COTA"
LOCALIZATION_LAYER = "LOCALIZACION"
# Las capas fijas que las apps declaran siempre (DESPLAZAMIENTO_DE_BASE va aunque no haya desplazamiento).
FIXED_LAYERS = (POINTS_LAYER, NAME_LAYER, DESCRIPTION_LAYER, "DESPLAZAMIENTO_DE_BASE")
# Colores de AutoCAD (ACI) que se leen sobre fondo blanco y negro (`SurveyExport.codeLayerPalette`).
CODE_LAYER_PALETTE = (1, 2, 3, 4, 5, 6, 30, 40, 84, 140, 200, 220)
FIXED_COLORS = {"PUNTOS": 7, "TEXTO": 7, "DESCRIPCION": 8, "SIN_COTA": 1, "LINEAS": 5, "POLIGONOS": 3,
                "DESPLAZAMIENTO_DE_BASE": 6, "LOCALIZACION": 4, ELEVATION_LAYER: 30}
DECLARATION_CHUNK = 240
# Windows-1252 en 0x80–0x9F (`SurveyExport.cp1252Extras`).
CP1252_EXTRAS = {
    0x20AC: 0x80, 0x201A: 0x82, 0x0192: 0x83, 0x201E: 0x84, 0x2026: 0x85, 0x2020: 0x86, 0x2021: 0x87,
    0x02C6: 0x88, 0x2030: 0x89, 0x0160: 0x8A, 0x2039: 0x8B, 0x0152: 0x8C, 0x017D: 0x8E, 0x2018: 0x91,
    0x2019: 0x92, 0x201C: 0x93, 0x201D: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97, 0x02DC: 0x98,
    0x2122: 0x99, 0x0161: 0x9A, 0x203A: 0x9B, 0x0153: 0x9C, 0x017E: 0x9E, 0x0178: 0x9F,
}


@dataclass(frozen=True)
class DxfOptions:
    """Las opciones del DXF de las apps («Altura del texto», «Nombres de los puntos») y el rótulo de la cota."""
    text_height_m: float = 0.5
    point_names: bool = True          # apagado: ni el nombre, ni la cota, ni la descripción
    elevation_labels: bool = True     # el rótulo de la cota en la capa COTA (lo que añade la oficina)


@dataclass(frozen=True)
class DxfPoint:
    name: str
    code: str
    easting_m: float
    northing_m: float
    elevation_m: float | None         # la cota del archivo (la misma que el CSV); None: sin cota
    description: str = ""


def ascii_folded(text: str) -> str:
    """Sin acentos ni diéresis (`LocalizationText.asciiFolded`: «Peña» → «Pena»)."""
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if not unicodedata.combining(c)))


def sanitized(text: str) -> str:
    """Un valor de grupo DXF no puede llevar saltos de línea (`sanitizeDXF`)."""
    return text.replace("\r", " ").replace("\n", " ")


def sanitized_layer(text: str) -> str:
    """Mayúsculas, sin acentos, `[A-Z0-9_-]` (lo demás, «_») y 31 caracteres como mucho (`sanitizedLayer`)."""
    folded = ascii_folded(text.upper())
    cleaned = "".join(c if (c.isalpha() or c.isnumeric() or c in "-_") else "_" for c in folded)
    return cleaned[:31]


def layer_name(code: str) -> str:
    """La capa de un código: el código saneado para R12; sin código, `PUNTOS`.

    En las apps la capa puede venir de la «Capa del DXF» de la biblioteca de códigos; la oficina no tiene la
    biblioteca (el CSV de Field no la trae), así que la capa es el código, que es lo que hacen las apps cuando el
    código no tiene capa propia."""
    code = code.strip()
    if not code:
        return POINTS_LAYER
    return sanitized_layer(code) or POINTS_LAYER


def layer_color(layer: str) -> int:
    """El color ACI de una capa (`SurveyExport.layerColor`)."""
    if layer in FIXED_COLORS:
        return FIXED_COLORS[layer]
    h = 2_166_136_261
    for byte in layer.encode("utf-8"):
        h = ((h ^ byte) * 16_777_619) & 0xFFFFFFFF
    return CODE_LAYER_PALETTE[h % len(CODE_LAYER_PALETTE)]


def windows1252(text: str) -> bytes:
    """El texto en Windows-1252; lo que no cabe, como `\\U+XXXX` (`SurveyExport.windows1252`)."""
    out = bytearray()
    for ch in text:
        v = ord(ch)
        if v < 0x80 or 0xA0 <= v <= 0xFF:
            out.append(v)
        elif v in CP1252_EXTRAS:
            out.append(CP1252_EXTRAS[v])
        else:
            out += f"\\U+{v:04X}".encode("ascii")
    return bytes(out)


def declaration_chunks(lines: list[str]) -> list[str]:
    """Los comentarios del principio en ASCII y en trozos de 240 caracteres (un valor de R12 llega a 255)."""
    out: list[str] = []
    for line in lines:
        text = sanitized(ascii_folded(line))
        if len(text) <= DECLARATION_CHUNK:
            out.append(text)
            continue
        out += [text[i:i + DECLARATION_CHUNK] for i in range(0, len(text), DECLARATION_CHUNK)]
    return out


def write(points: list[DxfPoint], declaration: list[str], options: DxfOptions = DxfOptions(),
          localization: list[str] = ()) -> bytes:
    """El DXF de los puntos. `declaration`: las líneas de los comentarios `999` (sistema, marco, Z…).
    `localization`: en locales, la declaración de la localización, que va también como `TEXT` en la capa
    `LOCALIZACION` por debajo del primer punto (`SurveyExport.dxf` de las apps)."""
    out: list[str] = []

    def pair(code: int, value: str) -> None:
        out.append(str(code))
        out.append(value)

    def fmt(value: float) -> str:
        return number(value, 3)

    labels = options.point_names and options.elevation_labels
    fixed = set(FIXED_LAYERS)
    if any(p.elevation_m is None for p in points):
        fixed.add(NO_HEIGHT_LAYER)
    if labels and any(p.elevation_m is not None for p in points):
        fixed.add(ELEVATION_LAYER)
    if localization and points:
        fixed.add(LOCALIZATION_LAYER)
    layers = sorted({layer_name(p.code) for p in points} | fixed)

    for line in declaration_chunks(declaration):
        pair(999, line)
    pair(0, "SECTION"); pair(2, "HEADER")
    pair(9, "$ACADVER"); pair(1, "AC1009")
    pair(9, "$DWGCODEPAGE"); pair(3, "ANSI_1252")
    pair(9, "$INSUNITS"); pair(70, "6")
    pair(0, "ENDSEC")
    pair(0, "SECTION"); pair(2, "TABLES")
    pair(0, "TABLE"); pair(2, "LAYER"); pair(70, str(len(layers)))
    for layer in layers:
        pair(0, "LAYER"); pair(2, layer); pair(70, "0"); pair(62, str(layer_color(layer))); pair(6, "CONTINUOUS")
    pair(0, "ENDTAB"); pair(0, "ENDSEC")
    pair(0, "SECTION"); pair(2, "ENTITIES")
    height = fmt(options.text_height_m)
    for p in points:
        x, y = fmt(p.easting_m), fmt(p.northing_m)
        z = fmt(p.elevation_m) if p.elevation_m is not None else None
        if z is None:
            pair(999, sanitized(f"sin cota: {p.name}"))
        pair(0, "POINT"); pair(8, NO_HEIGHT_LAYER if z is None else layer_name(p.code))
        pair(10, x); pair(20, y)
        if z is not None:
            pair(30, z)
        if not options.point_names:
            continue
        pair(0, "TEXT"); pair(8, NO_HEIGHT_LAYER if z is None else NAME_LAYER); pair(10, x); pair(20, y)
        if z is not None:
            pair(30, z)
        pair(40, height); pair(1, sanitized(p.name))
        rows = 1
        if labels and z is not None:
            pair(0, "TEXT"); pair(8, ELEVATION_LAYER); pair(10, x)
            pair(20, fmt(p.northing_m - options.text_height_m * 1.4 * rows)); pair(30, z)
            pair(40, height); pair(1, z)
            rows += 1
        if p.description:
            pair(0, "TEXT"); pair(8, DESCRIPTION_LAYER); pair(10, x)
            pair(20, fmt(p.northing_m - options.text_height_m * 1.4 * rows))
            if z is not None:
                pair(30, z)
            pair(40, height); pair(1, sanitized(p.description))
    if localization and points:
        anchor = points[0]
        for index, line in enumerate(localization):
            pair(0, "TEXT"); pair(8, LOCALIZATION_LAYER); pair(10, fmt(anchor.easting_m))
            pair(20, fmt(anchor.northing_m - options.text_height_m * (index + 2) * 1.6))
            pair(40, height); pair(1, sanitized(line))
    pair(0, "ENDSEC"); pair(0, "EOF")
    return windows1252(CRLF.join(out) + CRLF)
