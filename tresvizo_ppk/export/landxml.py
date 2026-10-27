"""LandXML 1.2 de los puntos (`CgPoints`), con la especificación de las apps TresVizo Field.

Es la de `docs/oficina/06-formatos.md` §3 de las apps (referencia `referencia/Prueba_oficina.xml`), en lo que la
oficina escribe: UTF-8 sin BOM, `\\n`, sangría de dos espacios y salto de línea al final; `Units` en métrico;
`CoordinateSystem` con el nombre, el código EPSG, el datum horizontal, el vertical (el geoide aplicado, o «elipsoidal
⟨marco⟩») y la WKT1 de ESRI del sistema (el texto del `.prj`); `Project`; `Application`; y un `CgPoint` por punto
con `name`, `code`, `desc` y `timeStamp` si los hay y las coordenadas en el **orden de LandXML: norte, este, cota**
(sin cota, solo norte y este; nunca 0), con los decimales del CSV.

Lo que la referencia de las apps lleva y aquí no sale, porque **no pasa la validación contra `LandXML-1.2.xsd`**
(comprobado el 27-10-2026 con el esquema oficial): un `Feature` dentro de `CgPoint` (su contenido es de tipo simple)
y el atributo `code` de `PlanFeature`. La oficina no tiene atributos de código ni figuras, así que su archivo es
válido; las apps tienen que corregir esas dos piezas (`docs/estado.md`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

NAMESPACE = "http://www.landxml.org/schema/LandXML-1.2"
NOT_PROJECTED = "El LandXML necesita un sistema proyectado. Exporta en UTM o usa CSV o GeoJSON."


def attribute(text: str) -> str:
    """Un valor de atributo con `&amp;`, `&lt;`, `&gt;`, `&quot;` y `&apos;` (`xa` de la referencia)."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&apos;"))


@dataclass(frozen=True)
class CoordinateSystem:
    name: str
    epsg_code: str = ""               # «6368»; vacío si el sistema no tiene código EPSG
    horizontal_datum: str = ""
    vertical_datum: str = ""          # «GGM10», o «elipsoidal ITRF2008» sin geoide
    wkt: str = ""                     # WKT1 de ESRI (el texto del `.prj`); vacío si no lo hay


@dataclass(frozen=True)
class CgPoint:
    name: str
    northing: str                     # ya con sus decimales (los del CSV)
    easting: str
    elevation: str = ""               # vacío: sin cota
    code: str = ""
    desc: str = ""
    timestamp: str = ""               # «2026-10-15T12:44:19.8-06:00»; vacío en los ingresados


@dataclass
class Document:
    project: str
    application_version: str
    exported_at: datetime             # hora local con su zona: `date` y `time` de la cabecera
    crs: CoordinateSystem
    points: list[CgPoint] = field(default_factory=list)
    application: str = "TresVizo Field PPK"
    manufacturer: str = "TresVizo"


def coordinates(p: CgPoint) -> str:
    text = f"{p.northing} {p.easting}"
    return text + (f" {p.elevation}" if p.elevation else "")


def write(doc: Document) -> bytes:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<LandXML xmlns="{NAMESPACE}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xsi:schemaLocation="{NAMESPACE} {NAMESPACE}/LandXML-1.2.xsd" version="1.2" '
        f'date="{doc.exported_at:%Y-%m-%d}" time="{doc.exported_at:%H:%M:%S}" readOnly="false" language="Spanish">',
        "  <Units>",
        '    <Metric areaUnit="squareMeter" linearUnit="meter" volumeUnit="cubicMeter" temperatureUnit="celsius" '
        'pressureUnit="milliBars" angularUnit="decimal degrees" directionUnit="decimal degrees"/>',
        "  </Units>",
    ]
    c = doc.crs
    cs = f'  <CoordinateSystem name="{attribute(c.name)}"'
    if c.epsg_code:
        cs += f' epsgCode="{attribute(c.epsg_code)}"'
    if c.horizontal_datum:
        cs += f' horizontalDatum="{attribute(c.horizontal_datum)}"'
    if c.vertical_datum:
        cs += f' verticalDatum="{attribute(c.vertical_datum)}"'
    if c.wkt:
        cs += f' ogcWktCode="{attribute(c.wkt)}"'
    lines.append(cs + "/>")
    lines.append(f'  <Project name="{attribute(doc.project)}"/>')
    lines.append(f'  <Application name="{attribute(doc.application)}" manufacturer="{attribute(doc.manufacturer)}" '
                 f'version="{attribute(doc.application_version)}"/>')
    lines.append("  <CgPoints>")
    for p in doc.points:
        a = f'name="{attribute(p.name)}"'
        if p.code:
            a += f' code="{attribute(p.code)}"'
        if p.desc:
            a += f' desc="{attribute(p.desc)}"'
        if p.timestamp:
            a += f' timeStamp="{p.timestamp}"'
        lines.append(f"    <CgPoint {a}>{coordinates(p)}</CgPoint>")
    lines.append("  </CgPoints>")
    lines.append("</LandXML>")
    return ("\n".join(lines) + "\n").encode("utf-8")
