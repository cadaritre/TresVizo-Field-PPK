"""Lo que piden en el informe las normas técnicas del INEGI publicadas en el DOF el 23 de diciembre de 2010.

- **NT SGN**, Norma Técnica para el Sistema Geodésico Nacional: art. 10 (ITRF08 época 2010.0 con GRS80, marco oficial),
  art. 12, fr. I y II (levantamientos vinculados a la RGNA en ITRF08 época 2010.0) y art. 15, fr. III (la altura
  geodésica se pasa a ortométrica con «el modelo geoidal vigente, disponible en el Instituto»).
- **NT EEP**, Norma Técnica de Estándares de Exactitud Posicional: art. 6 (CEP95 = 1.2238 (σφ + σλ)), art. 7
  (EPV = 1.9600 σ), art. 8 con su tabla 1 (órdenes de exactitud horizontal) y art. 12 (nivelación GNSS:
  EPV = 1.9600 (σGNSS² + σN²)^½, con la σN del modelo geoidal publicado por el INEGI).

La declaración de conformidad solo se imprime si se cumple de verdad: todas las bases de la RGNA con su coordenada
oficial, el resultado en ITRF2008 época 2010.0 y, si hay cota, el modelo geoidal vigente del INEGI. Con estaciones de
otra red va una declaración neutra (la red, su marco y su época); con una base propia o aproximada, ninguna, y el
informe dice por qué.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from ..crs import api as crs
from ..crs.definicion import epoch_text
from ..model import OFFICIAL_EPOCH, OFFICIAL_FRAME, Ecef, Enu, Project, Station, TransformedCoordinate, UserProfile
from . import calculos, estilo
from .documento import CONTENT_WIDTH, Report, text
from .estilo import num

RGNA_NETWORK = "rgna-inegi"
NT_SGN = "Norma Técnica para el Sistema Geodésico Nacional"
NT_EEP = "Norma Técnica de Estándares de Exactitud Posicional"
DOF = "DOF 23-12-2010"

MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre")

# Una línea que explica las dos cifras horizontales al 95 % que da el informe.
HORIZONTAL_NOTE = ("Horizontal al 95 % (círculo): 2.4477·√((σE² + σN²)/2), el radio que encierra el 95 % de una normal "
                   "bidimensional. CEP95 (NT EEP art. 6): 1.2238·(σφ + σλ), con σφ = σ Norte y σλ = σ Este en metros, "
                   "la cifra de la norma y la que da el orden de exactitud (art. 8, tabla 1); coinciden si σE = σN.")


# --- Exactitud posicional (NT EEP) -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Accuracy:
    """Las cifras de la NT EEP para un punto."""
    cep95_m: float                       # art. 6
    order: str | None                    # art. 8, tabla 1 (None: fuera de la tabla)
    epv_ellipsoidal_m: float             # art. 7, de la altura geodésica
    epv_orthometric_m: float | None      # art. 12 (o la misma fórmula con otro geoide), si hay cota
    sigma_geoid_m: float | None          # σN usada
    geoid: str = ""                      # «Geoide Gravimétrico Mexicano 2025 (INEGI)»
    geoid_source: str = ""               # de dónde sale σN
    inegi_geoid: bool = False            # el geoide es el modelo vigente del INEGI (la fórmula es la del art. 12)


def accuracy(sigma: Enu | None, c: TransformedCoordinate | None) -> Accuracy | None:
    if sigma is None:
        return None
    cep = calculos.cep95(sigma.n, sigma.e)
    epv_h = calculos.epv(sigma.u)
    epv_o = sigma_n = None
    geoid = source = ""
    inegi = False
    if c is not None and c.orthometric_height_m is not None and c.geoid_uncertainty_m is not None:
        sigma_n = c.geoid_uncertainty_m
        epv_o = calculos.epv_gnss_leveling(sigma.u, sigma_n)
        meta = geoid_metadata(c.geoid_id)
        geoid = geoid_title(meta) if meta else (c.geoid_name or str(c.geoid_id))
        source = geoid_sigma_source(meta) if meta else "precisión declarada en la ficha del modelo"
        inegi = is_current_inegi_geoid(c.geoid_id)
    return Accuracy(cep, calculos.accuracy_order(cep), epv_h, epv_o, sigma_n, geoid, source, inegi)


def order_text(a: Accuracy | None) -> str:
    if a is None:
        return "—"
    return a.order or "fuera de la tabla 1 (más de 500 m)"


def accuracy_fields(a: Accuracy, height_label: str = "de la cota") -> list[tuple[str, str]]:
    """Los pares de la NT EEP para la tabla de precisión del informe."""
    rows = [("CEP95 (NT EEP art. 6)", num(a.cep95_m, 3, "m")),
            ("Orden de exactitud horizontal (NT EEP art. 8, tabla 1)", order_text(a)),
            ("EPV de la altura elipsoidal (NT EEP art. 7)", num(a.epv_ellipsoidal_m, 3, "m"))]
    if a.epv_orthometric_m is not None:
        label = f"EPV {height_label} (NT EEP art. 12)" if a.inegi_geoid else f"EPV {height_label} (fórmula del art. 12)"
        rows.append((label, f"{num(a.epv_orthometric_m, 3, 'm')}, con σN = {num(a.sigma_geoid_m, 3)} m"))
    return rows


def height_name(c: TransformedCoordinate | None) -> str:
    return "de la cota normal" if c is not None and c.height_kind == "normal" else "de la cota ortométrica"


def accuracy_note(a: Accuracy | None) -> str:
    """De dónde sale la σN del geoide y lo que el art. 12 pide además; vacío si no hay cota."""
    if a is None or a.epv_orthometric_m is None:
        return ""
    head = (f"σN = {num(a.sigma_geoid_m, 3)} m: {a.geoid_source} ({a.geoid})." if a.geoid_source else
            f"σN = {num(a.sigma_geoid_m, 3)} m ({a.geoid}).")
    if not a.inegi_geoid:
        head += (" El art. 12 se refiere al modelo de alturas geoidales que publica el INEGI; con este geoide la "
                 "cifra usa la misma fórmula, pero no es la de la norma.")
    return head + (" Para la nivelación GNSS el art. 12 pide además circuitos cerrados con ajuste por mínimos "
                   "cuadrados; este informe da la EPV de cada punto y no comprueba circuitos.")


# --- Geoides del INEGI -----------------------------------------------------------------------------------------------

def geoid_metadata(geoid_id: str | None) -> dict | None:
    if not geoid_id:
        return None
    try:
        grid = crs.geoid(geoid_id)
    except Exception:     # sin el archivo del geoide no hay ficha
        return None
    return dict(grid.metadata) if grid is not None else None


def geoid_title(meta: dict) -> str:
    name = f"{meta.get('name', '')} {meta.get('version', '')}".strip()
    short = f" ({str(meta['id']).upper()})" if meta.get("id") else ""
    return f"{name}{short}, {meta.get('authority', '')}".strip(", ")


def geoid_sigma_source(meta: dict) -> str:
    url = meta.get("sourceURL")
    return "precisión declarada en la ficha del modelo" + (f", {url}" if url else "")


def is_current_inegi_geoid(geoid_id: str | None) -> bool:
    """El modelo geoidal vigente del INEGI (NT SGN art. 15, fr. III): del INEGI y no marcado como histórico en su
    ficha (GGM10 lo está desde que el INEGI publicó GGM25)."""
    meta = geoid_metadata(geoid_id)
    return bool(meta) and meta.get("authority") == "INEGI" and not meta.get("isHistoric", False) \
        and meta.get("verification", "verified") == "verified"


def current_inegi_geoids() -> list[str]:
    out = []
    for gid in crs.BUNDLED_GEOIDS:
        meta = geoid_metadata(gid)
        if meta and is_current_inegi_geoid(gid):
            out.append(geoid_title(meta))
    return out


# --- Declaración -----------------------------------------------------------------------------------------------------

@dataclass
class Declaration:
    kind: str                                  # «inegi», «neutral» o «none»
    text: str = ""
    reasons: list[str] = field(default_factory=list)   # por qué no va la declaración de las normas del INEGI


def _is_official(s: Station) -> bool:
    return s.official and not s.approximate and s.coordinate_origin == "official" and bool(s.network)


def _same_frame(p: Ecef, frame: str, epoch: float) -> bool:
    return p.frame.replace(" ", "").upper() == frame.upper() and math.isclose(p.epoch, epoch, abs_tol=1e-6)


def declaration(stations: list[Station], position: Ecef | None, c: TransformedCoordinate | None,
                solved: bool = True) -> Declaration:
    """La declaración que va sobre la firma, o por qué no va ninguna."""
    if not solved or position is None:
        return Declaration("none", reasons=["no hay solución que declarar"])
    if not stations:
        return Declaration("none", reasons=["no hay base"])
    not_official = [s for s in stations if not _is_official(s)]
    if not_official:
        reasons = []
        for s in not_official:
            if s.approximate:
                reasons.append(f"la coordenada de la base {s.id} es aproximada")
            elif not s.network or s.coordinate_origin != "official":
                reasons.append(f"{s.id} es una base propia, sin la coordenada oficial de una red")
            else:
                reasons.append(f"la coordenada de {s.id} se corrigió a mano: no es la oficial de su red")
        return Declaration("none", reasons=reasons)
    ids = ", ".join(s.id for s in stations)
    frame = f"{position.frame}, época {epoch_text(position.epoch)}"
    if any(s.network != RGNA_NETWORK for s in stations):
        networks = list(dict.fromkeys(s.network_name or s.network for s in stations))
        return Declaration("neutral", text=(
            f"Levantamiento ligado a {' y '.join(networks)} (estaciones {ids}), con las coordenadas oficiales de su "
            f"red en {frame}. No es la RGNA del INEGI: las normas técnicas del INEGI no se declaran."))
    reasons = []
    if not _same_frame(position, OFFICIAL_FRAME, OFFICIAL_EPOCH) or not all(
            _same_frame(s.position, OFFICIAL_FRAME, OFFICIAL_EPOCH) for s in stations):
        reasons.append(f"el resultado queda en {frame} y la NT SGN (art. 10) pide ITRF2008, época 2010.0")
    has_height = c is not None and c.orthometric_height_m is not None
    if has_height and not is_current_inegi_geoid(c.geoid_id):
        meta = geoid_metadata(c.geoid_id)
        used = geoid_title(meta) if meta else (c.geoid_name or str(c.geoid_id))
        current = current_inegi_geoids()
        reasons.append(f"la cota usa {used}, que no es el modelo geoidal vigente del INEGI (NT SGN art. 15, fr. III)"
                       + (f"; el vigente es {' o '.join(current)}" if current else ""))
    if reasons:
        return Declaration("none", reasons=reasons)
    heights = ("Cotas ortométricas con el modelo geoidal vigente del INEGI, "
               f"{geoid_title(geoid_metadata(c.geoid_id) or {})} (NT SGN art. 15, fr. III)." if has_height else
               "Alturas elipsoidales sobre GRS80: el informe no da cota ortométrica.")
    return Declaration("inegi", text=(
        f"Levantamiento ligado a la Red Geodésica Nacional Activa del INEGI (estaciones {ids}), en ITRF2008 época "
        f"2010.0 con el elipsoide GRS80, conforme a la {NT_SGN} y a la {NT_EEP} ({DOF}). {heights} Exactitud "
        "posicional al 95 % (CEP95 y EPV) y orden de exactitud de cada punto según los arts. 6, 7, 8 y 12 de la "
        "NT EEP."))


APPLIED = [
    ("NT SGN art. 10", "ITRF08 época 2010.0 con el elipsoide GRS80, marco de referencia oficial"),
    ("NT SGN art. 12, fr. I y II", "coordenadas generadas con GNSS vinculadas a la RGNA en ITRF08 época 2010.0"),
    ("NT SGN art. 15, fr. III", "altura ortométrica con el modelo geoidal vigente del INEGI"),
    ("NT EEP art. 6", "CEP95 = 1.2238 (σφ + σλ)"),
    ("NT EEP art. 7", "EPV = 1.9600 σ"),
    ("NT EEP art. 8, tabla 1", "órdenes de exactitud posicional horizontal"),
    ("NT EEP art. 12", "nivelación GNSS: EPV = 1.9600 (σGNSS² + σN²)^½"),
]


# --- Bloque de firma -------------------------------------------------------------------------------------------------

def date_text(when: datetime) -> str:
    local = when.astimezone()
    return f"{local.day} de {MONTHS[local.month - 1]} de {local.year}"


def signature_section(r: Report, decl: Declaration, project: Project, user: UserProfile) -> None:
    """La última página: la declaración (o por qué no va) y el bloque de firma y sello."""
    r.page_break()
    r.h1("Declaración y firma")
    if decl.kind == "inegi":
        r.callout("success", "Declaración", [decl.text])
        r.table(["Disposición", "Qué se aplica"], [list(x) for x in APPLIED], widths=[130, CONTENT_WIDTH - 130],
                repeat=False)
        r.space(6)
    elif decl.kind == "neutral":
        r.callout("info", "Declaración", [decl.text])
    else:
        r.para(text("No se imprime la declaración de conformidad con las normas técnicas del INEGI: "
                    + "; ".join(decl.reasons) + "."), "small")
    blank = "_" * 34
    place = project.place.strip()
    r.fields([("Lugar y fecha", f"{place}, {date_text(r.generated)}" if place else
               f"{blank}, {date_text(r.generated)}"),
              ("Nombre", user.surveyor or blank), ("Cédula profesional", user.license_id or blank),
              ("Empresa", user.company or blank)], columns=1, label_width=110)
    r.space(40)
    st = ParagraphStyle("Firma", parent=r.st["cell_head"], alignment=TA_CENTER)
    box = Table([["", "", ""], [Paragraph("Firma", st), "", Paragraph("Sello", st)]],
                colWidths=[CONTENT_WIDTH * 0.45, CONTENT_WIDTH * 0.1, CONTENT_WIDTH * 0.45],
                rowHeights=[70, None], hAlign="LEFT")
    box.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.8, colors.HexColor(estilo.MUTED)),
                             ("BOX", (2, 0), (2, 0), 0.6, colors.HexColor(estilo.BORDER)),
                             ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    r.story += [box, Spacer(1, 6)]
