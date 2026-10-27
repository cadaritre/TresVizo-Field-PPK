"""Identidad de los informes: la misma familia visual que los documentos de TresVizo Med.

De TresVizo Med (`app/documents.py` y `app/themes.py`, MIT, del mismo propietario) se toman la página A4 con márgenes
de 48/48/36/48 pt, la letra del sistema (Segoe UI en Windows), el texto de 10/15 pt en #263746, los títulos en el azul de
documento #07356F (17 pt; secciones de 12 pt), el logo del usuario arriba a la izquierda (120 × 65 pt como mucho), el
pie «Página N» a 8 pt y la paleta de gráficas (`chart1`…`chart6`) y de estados. Se añade lo que pide un informe técnico
del ramo: tablas con filas alternas, cifras alineadas, una letra monoespaciada para la bitácora y los colores de
solución de las apps de campo (siempre con texto al lado).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

PRIMARY = "#07356F"         # document_primary de TresVizo Med
TEXT = "#263746"            # document_text
MUTED = "#626269"
BORDER = "#C9CED6"
ROW_ALT = "#F3F5F8"
HEADER_BG = "#E8EEF6"
CHART = ["#086A8C", "#197653", "#8055A1", "#9A6200", "#A24468", "#4269A6"]
SOLUTION = {"fixed": "#197653", "float": "#9A6200", "single": "#A24468", "none": "#626269"}
SEMANTIC = {"error_bg": "#FDEBEC", "error_fg": "#9E182C", "warning_bg": "#FFF2CE", "warning_fg": "#714B00",
            "success_bg": "#E1F4E8", "success_fg": "#185631", "info_bg": "#E8F1FC", "info_fg": "#0B4A8B"}

MARGINS = dict(left=48, right=48, top=36, bottom=48)


@dataclass(frozen=True)
class Fonts:
    regular: str
    bold: str
    mono: str
    regular_path: str
    bold_path: str


# Letras del sistema, en orden de preferencia (regular, negrita). Tienen que traer el griego (σ, Δ, χ) y los signos.
_CANDIDATES = {
    "win32": [("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
              ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf")],
    "darwin": [("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
               ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf")],
    "linux": [("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
              ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")],
}
_MONO = {
    "win32": ["C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf"],
    "darwin": ["/System/Library/Fonts/Supplemental/Courier New.ttf"],
    "linux": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
}
_fonts: Fonts | None = None


def _matplotlib_font(name: str) -> str:
    import matplotlib
    return str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / name)


def fonts() -> Fonts:
    """Registra en ReportLab la letra del sistema (la de TresVizo Med en Windows) y una monoespaciada. Si el sistema no
    trae ninguna de las previstas, usa DejaVu Sans, que viene con matplotlib: así el informe siempre tiene σ, Δ y χ."""
    global _fonts
    if _fonts is not None:
        return _fonts
    key = "win32" if sys.platform == "win32" else ("darwin" if sys.platform == "darwin" else "linux")
    pair = next(((r, b) for r, b in _CANDIDATES[key] if Path(r).is_file() and Path(b).is_file()), None)
    if pair is None:
        pair = (_matplotlib_font("DejaVuSans.ttf"), _matplotlib_font("DejaVuSans-Bold.ttf"))
    mono = next((m for m in _MONO[key] if Path(m).is_file()), _matplotlib_font("DejaVuSansMono.ttf"))
    for name, path in (("TVSans", pair[0]), ("TVSans-Bold", pair[1]), ("TVMono", mono)):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, path))
    from reportlab.lib.fonts import addMapping
    addMapping("TVSans", 0, 0, "TVSans")
    addMapping("TVSans", 1, 0, "TVSans-Bold")
    addMapping("TVSans", 0, 1, "TVSans")
    addMapping("TVSans", 1, 1, "TVSans-Bold")
    _fonts = Fonts("TVSans", "TVSans-Bold", "TVMono", pair[0], pair[1])
    return _fonts


def styles() -> dict[str, ParagraphStyle]:
    f = fonts()
    body = ParagraphStyle("Cuerpo", fontName=f.regular, fontSize=9.5, leading=14, textColor=colors.HexColor(TEXT),
                          spaceAfter=6, alignment=TA_LEFT)
    return {
        "body": body,
        "small": ParagraphStyle("Pequeño", parent=body, fontSize=8, leading=11, spaceAfter=3,
                                textColor=colors.HexColor(MUTED)),
        "cell": ParagraphStyle("Celda", parent=body, fontSize=8.2, leading=10.5, spaceAfter=0),
        "cell_right": ParagraphStyle("CeldaDerecha", parent=body, fontSize=8.2, leading=10.5, spaceAfter=0,
                                     alignment=TA_RIGHT),
        "cell_head": ParagraphStyle("CeldaTitulo", parent=body, fontName=f.bold, fontSize=8.2, leading=10.5,
                                    spaceAfter=0, textColor=colors.HexColor(PRIMARY)),
        "cell_head_right": ParagraphStyle("CeldaTituloDerecha", parent=body, fontName=f.bold, fontSize=8.2,
                                          leading=10.5, spaceAfter=0, textColor=colors.HexColor(PRIMARY),
                                          alignment=TA_RIGHT),
        "title": ParagraphStyle("Titulo", parent=body, fontName=f.bold, fontSize=17, leading=22,
                                textColor=colors.HexColor(PRIMARY), spaceBefore=4, spaceAfter=4),
        "h1": ParagraphStyle("Seccion", parent=body, fontName=f.bold, fontSize=12.5, leading=17,
                             textColor=colors.HexColor(PRIMARY), spaceBefore=12, spaceAfter=6),
        "h2": ParagraphStyle("Subseccion", parent=body, fontName=f.bold, fontSize=10.5, leading=14,
                             textColor=colors.HexColor(PRIMARY), spaceBefore=8, spaceAfter=4),
        "mono": ParagraphStyle("Bitacora", parent=body, fontName=f.mono, fontSize=7.5, leading=9.5, spaceAfter=0,
                               textColor=colors.HexColor("#1D1D1F")),
        "big": ParagraphStyle("Grande", parent=body, fontName=f.bold, fontSize=11, leading=15,
                              textColor=colors.HexColor(TEXT), spaceAfter=0),
    }


# --- Números y ángulos -----------------------------------------------------------------------------------------------

def num(value: float | None, decimals: int = 3, unit: str = "") -> str:
    """Un número con punto decimal y decimales fijos; «—» sin dato y nunca «-0.000»."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    text = f"{value:.{decimals}f}"
    if text.startswith("-") and set(text[1:]) <= set("0."):
        text = text[1:]
    return f"{text} {unit}".rstrip() if unit else text


def signed(value: float | None, decimals: int = 3, unit: str = "") -> str:
    if value is None or not math.isfinite(value):
        return "—"
    text = num(value, decimals)
    if not text.startswith("-"):
        text = "+" + text
    return f"{text} {unit}".rstrip() if unit else text


def dms(value: float | None, positive: str, negative: str, decimals: int = 5) -> str:
    """21°51′21.60000″ N."""
    if value is None or not math.isfinite(value):
        return "—"
    hemi = positive if value >= 0 else negative
    v = abs(value)
    d = int(v)
    m_full = (v - d) * 60
    m = int(m_full)
    s = (m_full - m) * 60
    if round(s, decimals) >= 60:
        s = 0.0
        m += 1
    if m >= 60:
        m -= 60
        d += 1
    return f"{d}°{m:02d}′{s:0{3 + decimals}.{decimals}f}″ {hemi}"


def lat_text(value: float | None) -> str:
    return dms(value, "N", "S")


def lon_text(value: float | None) -> str:
    return dms(value, "E", "O")


def azimuth_dms(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    v = value % 360.0
    d = int(v)
    m_full = (v - d) * 60
    m = int(m_full)
    s = (m_full - m) * 60
    if round(s, 2) >= 60:
        s, m = 0.0, m + 1
    if m >= 60:
        m, d = m - 60, d + 1
    return f"{d % 360}°{m:02d}′{s:05.2f}″"


def duration_text(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min {s:02d} s"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def utc_text(t) -> str:
    return "—" if t is None else t.strftime("%Y-%m-%d %H:%M:%S UTC")
