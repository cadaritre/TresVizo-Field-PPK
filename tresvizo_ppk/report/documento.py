"""El documento PDF: encabezado con el logo y los datos del usuario, pie con «Página N de M» y las piezas de cada
sección (títulos, tablas de datos, tablas, gráficas, avisos y la bitácora)."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from ..model import Project, UserProfile
from ..version import __version__
from . import estilo

APP_NAME = "TresVizo Field PPK"
def _brand() -> Path:
    """La marca del programa: junto al código o, en la app empaquetada, en la carpeta de recursos de PyInstaller."""
    import sys
    for base in (Path(__file__).resolve().parents[2], Path(getattr(sys, "_MEIPASS", "")) if getattr(
            sys, "_MEIPASS", None) else None):
        if base is not None and (base / "assets" / "marca.png").is_file():
            return base / "assets" / "marca.png"
    return Path(__file__).resolve().parents[2] / "assets" / "marca.png"


BRAND = _brand()
PAGE = A4
CONTENT_WIDTH = PAGE[0] - estilo.MARGINS["left"] - estilo.MARGINS["right"]


def text(value) -> str:
    """Texto del usuario o de los datos, escapado para el marcado de ReportLab."""
    return escape(str(value)).replace("\n", "<br/>")


class _NumberedCanvas(rl_canvas.Canvas):
    """Guarda las páginas para escribir «Página N de M» al final."""

    def __init__(self, *args, footer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []
        self._footer = footer

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            if self._footer:
                self._footer(self, self._pageNumber, total)
            super().showPage()
        super().save()


class Report:
    def __init__(self, title: str, project: Project, user: UserProfile, generated: datetime | None = None):
        self.title = title
        self.project = project
        self.user = user
        self.generated = generated or datetime.now(timezone.utc)
        self.st = estilo.styles()
        self.f = estilo.fonts()
        self.story: list = []
        self._header()

    # -- Encabezado y pie --------------------------------------------------------------------------------------------

    def _header(self) -> None:
        u, p = self.user, self.project
        left: list = []
        if u.logo and Path(u.logo).is_file():
            try:
                from PIL import Image as PILImage
                with PILImage.open(u.logo) as image:
                    w, h = image.size
                scale = min(120 / w, 65 / h)
                left.append(Image(str(u.logo), width=w * scale, height=h * scale, hAlign="LEFT"))
            except (OSError, ValueError):
                pass
        company = [Paragraph(text(u.company), self.st["big"])] if u.company else []
        who = []
        if u.surveyor:
            who.append(f"Responsable: {text(u.surveyor)}")
        if u.license_id:
            who.append(f"Cédula profesional: {text(u.license_id)}")
        if who:
            company.append(Paragraph("<br/>".join(who), self.st["small"]))
        meta = []
        if p.number:
            meta.append(f"Proyecto n.º {text(p.number)}")
        if p.name:
            meta.append(text(p.name))
        meta.append(f"Generado el {self.generated.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
        meta.append(f"{APP_NAME} {__version__}")
        right = [Paragraph("<br/>".join(meta), estilo_right(self.st["small"]))]
        head = Table([[left or "", company or "", right]],
                     colWidths=[130, CONTENT_WIDTH - 130 - 170, 170])
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                  ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(estilo.PRIMARY)),
                                  ("BOTTOMPADDING", (0, 0), (-1, 0), 8)]))
        self.story += [head, Spacer(1, 10), Paragraph(text(self.title), self.st["title"])]

    def _footer(self, canvas, page: int, total: int) -> None:
        canvas.saveState()
        canvas.setFont(self.f.regular, 7.5)
        canvas.setFillColor(colors.HexColor(estilo.MUTED))
        y = 26
        left = f"{APP_NAME} {__version__} · {self.title}"
        if self.project.number or self.project.name:
            left += f" · {self.project.number or self.project.name}"
        canvas.drawString(estilo.MARGINS["left"], y, left[:120])
        canvas.drawRightString(PAGE[0] - estilo.MARGINS["right"], y, f"Página {page} de {total}")
        canvas.setStrokeColor(colors.HexColor(estilo.BORDER))
        canvas.setLineWidth(0.5)
        canvas.line(estilo.MARGINS["left"], y + 10, PAGE[0] - estilo.MARGINS["right"], y + 10)
        canvas.restoreState()

    # -- Piezas ------------------------------------------------------------------------------------------------------

    def h1(self, title: str) -> None:
        self.story += [CondPageBreak(60 * mm / 2), Paragraph(text(title), self.st["h1"])]

    def h2(self, title: str) -> None:
        self.story += [CondPageBreak(30 * mm / 2), Paragraph(text(title), self.st["h2"])]

    def para(self, markup: str, style: str = "body") -> None:
        """Párrafo con marcado de ReportLab (el texto del usuario ya escapado con `text`)."""
        self.story.append(Paragraph(markup, self.st[style]))

    def space(self, h: float = 6) -> None:
        self.story.append(Spacer(1, h))

    def page_break(self) -> None:
        self.story.append(PageBreak())

    def _cell(self, value, style: str = "cell") -> Paragraph:
        return value if isinstance(value, (Paragraph, Image, Table)) else Paragraph(text(value), self.st[style])

    def fields(self, pairs: list[tuple[str, str]], columns: int = 2, label_width: float = 118) -> None:
        """Pares «dato: valor» en una o dos columnas."""
        if not pairs:
            return
        rows = []
        per = (len(pairs) + columns - 1) // columns
        cols = [pairs[i * per:(i + 1) * per] for i in range(columns)]
        for i in range(per):
            row = []
            for c in cols:
                if i < len(c):
                    row += [self._cell(c[i][0], "cell_head"), self._cell(c[i][1])]
                else:
                    row += ["", ""]
            rows.append(row)
        value_width = CONTENT_WIDTH / columns - label_width
        t = Table(rows, colWidths=[label_width, value_width] * columns, hAlign="LEFT")
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 3),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#E3E7EC"))]))
        self.story.append(t)

    def table(self, headers: list[str], rows: list[list], widths: list[float] | None = None,
              right: set[int] | None = None, repeat: bool = True, highlight: dict[int, str] | None = None) -> None:
        """Tabla con encabezado, filas alternas y columnas numéricas a la derecha."""
        right = right or set()
        data = [[self._cell(h, "cell_head_right" if i in right else "cell_head") for i, h in enumerate(headers)]]
        for row in rows:
            data.append([self._cell(v, "cell_right" if i in right else "cell") for i, v in enumerate(row)])
        if widths is None:
            widths = [CONTENT_WIDTH / len(headers)] * len(headers)
        scale = CONTENT_WIDTH / sum(widths) if sum(widths) > CONTENT_WIDTH else 1.0
        t = Table(data, colWidths=[w * scale for w in widths], repeatRows=1 if repeat else 0, hAlign="LEFT")
        style = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(estilo.HEADER_BG)),
                 ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(estilo.PRIMARY)),
                 ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
                 ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                 ("LINEBELOW", (0, -1), (-1, -1), 0.4, colors.HexColor(estilo.BORDER))]
        for r in range(1, len(data)):
            if r % 2 == 0:
                style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor(estilo.ROW_ALT)))
        for r, color in (highlight or {}).items():
            style.append(("BACKGROUND", (0, r + 1), (-1, r + 1), colors.HexColor(color)))
        t.setStyle(TableStyle(style))
        self.story.append(t)

    def image(self, png: bytes, width: float = CONTENT_WIDTH, caption: str = "", heading: str = "") -> None:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(png)) as im:
            w, h = im.size
        height = width * h / w
        max_h = PAGE[1] - estilo.MARGINS["top"] - estilo.MARGINS["bottom"] - 60
        if height > max_h:
            width, height = width * max_h / height, max_h
        parts = [Paragraph(text(heading), self.st["h2"])] if heading else []
        parts.append(Image(io.BytesIO(png), width=width, height=height))
        if caption:
            parts.append(Paragraph(text(caption), self.st["small"]))
        self.story.append(KeepTogether(parts + [Spacer(1, 6)]))

    def callout(self, kind: str, title: str, lines: list[str]) -> None:
        """Recuadro de estado: «success», «warning», «error» o «info», con su título en palabras."""
        bg = colors.HexColor(estilo.SEMANTIC[f"{kind}_bg"])
        fg = estilo.SEMANTIC[f"{kind}_fg"]
        body = [Paragraph(f'<font color="{fg}"><b>{text(title)}</b></font>', self.st["body"])]
        for line in lines:
            body.append(Paragraph(f'<font color="{fg}">• {text(line)}</font>', self.st["cell"]))
        t = Table([[body]], colWidths=[CONTENT_WIDTH])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
        self.story += [t, Spacer(1, 8)]

    def log(self, lines: list[str]) -> None:
        if not lines:
            self.para("La bitácora del proceso está vacía.", "small")
            return
        for line in lines:
            self.story.append(Paragraph(text(line).replace(" ", "&nbsp;") or "&nbsp;", self.st["mono"]))

    def brand(self) -> None:
        """Cierre con la marca del programa, como el «TresVizo · app» de los documentos de TresVizo Med."""
        parts = []
        if BRAND.is_file():
            parts.append(Image(str(BRAND), width=26, height=26, hAlign="LEFT"))
        parts.append(Paragraph(f"Procesado con {APP_NAME} {__version__} (RTKLIB-EX para el cálculo GNSS, PROJ para "
                               "los sistemas de coordenadas).", self.st["small"]))
        self.story.append(KeepTogether([Spacer(1, 10)] + parts))

    # -- Salida ------------------------------------------------------------------------------------------------------

    def build(self, out_pdf: str | Path, subject: str = "") -> Path:
        out = Path(out_pdf)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        doc = SimpleDocTemplate(str(tmp), pagesize=PAGE, leftMargin=estilo.MARGINS["left"],
                                rightMargin=estilo.MARGINS["right"], topMargin=estilo.MARGINS["top"],
                                bottomMargin=estilo.MARGINS["bottom"], title=self.title,
                                author=self.user.company or self.user.surveyor or "", subject=subject,
                                creator=f"{APP_NAME} {__version__}", lang="es-MX")
        footer = self._footer
        doc.build(self.story, canvasmaker=lambda *a, **k: _NumberedCanvas(*a, footer=footer, **k))
        tmp.replace(out)
        return out


def estilo_right(style):
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    return ParagraphStyle(style.name + "Der", parent=style, alignment=TA_RIGHT)
