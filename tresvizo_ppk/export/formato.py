"""Formato de números y de texto de las exportaciones (las reglas de las apps TresVizo Field) y la escritura atómica."""
from __future__ import annotations

import math
import os
import re
import tempfile
from pathlib import Path

from ..errors import UserError

CRLF = "\r\n"


def number(value: float | None, decimals: int) -> str:
    """Decimales fijos con punto, redondeo al par desde el binario exacto, vacío sin dato y nunca «-0.000»."""
    if value is None or not math.isfinite(value):
        return ""
    text = f"{value:.{decimals}f}"
    if text.startswith("-") and set(text[1:]) <= set("0."):
        text = text[1:]
    return text


_SIGNED_NUMBER = re.compile(r"^[+-](\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def neutralized(text: str) -> str:
    """Un texto que una hoja de cálculo tomaría por fórmula lleva «'» delante (`SurveyExport.neutralized`)."""
    if not text:
        return text
    first = text[0]
    if first in "=@\t\r":
        return "'" + text
    if first in "+-":
        return text if _SIGNED_NUMBER.match(text) else "'" + text
    return text


def quoted(field: str, separator: str = ",") -> str:
    """Comillas de RFC 4180 solo si hacen falta, o si empieza por «#» (`SurveyExport.quoted`)."""
    if separator in field or '"' in field or "\n" in field or "\r" in field or field.startswith("#"):
        return '"' + field.replace('"', '""') + '"'
    return field


def write_rows(header: list[str] | None, rows: list[list[str]], *, separator: str = ",", bom: bool = True) -> bytes:
    """El archivo: encabezado (si lo hay) y filas ya formateadas, comillas donde hacen falta, CRLF y BOM."""
    lines = [separator.join(quoted(h, separator) for h in header)] if header is not None else []
    lines += [separator.join(quoted(v, separator) for v in row) for row in rows]
    data = (CRLF.join(lines) + CRLF).encode("utf-8") if lines else b""
    return (b"\xef\xbb\xbf" + data) if bom else data


def atomic_write(out: Path, data: bytes) -> Path:
    """Escribe en un temporal de la misma carpeta y lo cambia de nombre: nunca queda un archivo a medias."""
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=out.parent)
    except OSError as error:
        raise UserError(f"No se puede escribir en «{out.parent}»: {error.strerror or error}.") from None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except PermissionError:
        Path(tmp).unlink(missing_ok=True)
        raise UserError(f"No se pudo guardar «{out.name}»: el archivo está abierto en otro programa o la carpeta no "
                        "permite escribir.") from None
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return out
