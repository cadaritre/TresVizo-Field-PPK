# Exportación para CAD y entrega de crudos (27-10-2026)

Qué escribe la oficina y en qué se aparta de las apps TresVizo Field. El código: `tresvizo_ppk/export/` (`api.py`,
`dxf.py`, `landxml.py`, `formato.py`); las pruebas: `tests/test_export.py` y `tests/test_export_cad.py`.

Los mismos formatos para el **PPK** (`export_points`) y para el **vértice del estático** (`export_static`): CSV de
TresVizo Field, CSV (simple o a medida), GeoJSON, KML, DXF y LandXML. En todos, las mismas coordenadas y la misma cota
que el CSV de Field de esa exportación (sobre el geoide si se aplicó; sin geoide, la elipsoidal; con un geoide pedido
que no se pudo aplicar, sin cota). Con una familia de zona automática, todo el archivo va en la zona del primer punto.

## 1. DXF R12

La especificación de `SurveyExport.dxf` de las dos apps (`06-formatos.md` §1 de `docs/oficina` de las apps), que no
tiene archivo de referencia; la prueba `test_dxf_como_el_de_las_apps_byte_a_byte` la fija a mano desde su código:

- `999` al principio con lo que dice el archivo (sistema, marco, Z, base o líneas base del estático, plomeado, puntos
  que no van), en ASCII y en trozos de 240 caracteres.
- `HEADER`: `$ACADVER AC1009`, `$DWGCODEPAGE ANSI_1252`, `$INSUNITS 6`. `TABLES`: una `LAYER` por capa, en orden,
  con su color ACI y `CONTINUOUS`. Sin handles. CRLF. Windows-1252; lo que no cabe, `\U+XXXX`.
- **Capas**: una por código (mayúsculas, sin acentos, `[A-Z0-9_-]`, 31 caracteres); `PUNTOS` sin código; `TEXTO`
  (nombres); `DESCRIPCION`; `SIN_COTA`; `DESPLAZAMIENTO_DE_BASE` (vacía, como en las apps). Colores: los fijos de las
  apps y, por código, la paleta `1, 2, 3, 4, 5, 6, 30, 40, 84, 140, 200, 220` por FNV-1a de 32 bits del nombre.
- **Entidades**: `POINT` 3D en la capa de su código; `TEXT` con el nombre en `TEXTO`, en el punto, con su Z y altura
  0.5 m; la descripción un renglón por debajo (1.4 alturas de texto) en `DESCRIPCION`. Sin cota: `SIN_COTA`, sin
  código 30 y con un `999` que lo dice. Tres decimales.
- Junto al DXF, `⟨nombre⟩.prj` con la WKT1 de ESRI del sistema (`06-formatos.md` §4.3), de `pyproj`
  (`to_wkt("WKT1_ESRI")`, el mismo texto que la referencia de las apps para EPSG:6368). Solo sistemas proyectados.

**Añadido de la oficina, para que las apps lo adopten:** el rótulo de la **cota** en la capa **`COTA`** (color 30),
un renglón por debajo del nombre, con el mismo texto que la cota del CSV; la descripción baja entonces un renglón
más (2.8 alturas). La capa solo se declara si hay puntos con cota. `DxfOptions(elevation_labels=False)` da el archivo
de las apps sin cambiar un byte, y `point_names=False` quita los tres rótulos, como «Nombres de los puntos» apagado.

No hay biblioteca de códigos en la oficina: la capa es el código (lo que hacen las apps cuando el código no tiene
«Capa del DXF»). Las líneas por código no salen: el CSV de Field no las trae (dependencia de las apps).

## 2. LandXML 1.2

`06-formatos.md` §3 de las apps, lo que es de puntos: UTF-8 sin BOM, `\n`, dos espacios; `Units` métrico;
`CoordinateSystem` con el nombre EPSG, `epsgCode`, `horizontalDatum` (el datum del sistema), `verticalDatum` (el geoide
aplicado o «elipsoidal ⟨marco⟩»; sin atributo si el geoide no se pudo aplicar) y `ogcWktCode` (el texto del `.prj`);
`Project`; `Application` («TresVizo Field PPK», «TresVizo», versión); un `CgPoint` por punto con `name`, `code`,
`desc` y `timeStamp` (el inicio de la ocupación: el de la fila de campo con su desfase, o el UTC con `+00:00`) y
**norte, este, cota** (sin cota, solo norte y este). Solo sistemas proyectados.

- Byte a byte con `referencia/Prueba_oficina.xml` de las apps en todo lo que la oficina escribe (cabecera y los diez
  puntos sin atributos), con su proyecto de prueba (`tests/datos/apps/oficina/`).
- Validado contra **`LandXML-1.2.xsd`** (el oficial de landxml.org, SHA-256 `f4f73234…20fad4d77`): el del PPK y el del
  estático pasan.
- **La referencia de las apps no pasa el esquema** (lo que `06-formatos.md` §3 dejaba «sin verificar»): `Feature`
  dentro de `CgPoint` no se admite (su contenido es de tipo simple) y `PlanFeature` no tiene atributo `code`. Las apps
  tienen que llevar los atributos a un `Feature` aparte enlazado con `featureRef` y quitar `code` de `PlanFeature`
  (p. ej. a un `Feature` o al `desc`), y regenerar la referencia. La oficina no escribe ninguno de los dos.

## 3. CSV a medida

«Opciones de CSV» de las apps (`PointCSV.ExportOptions`, `csv-export.md` §2): `CsvOptions(layout, separator, language,
header, bom, decimal_comma, custom)`.

- Disposiciones: «Todas las columnas» (las 94 y, detrás, las del PPK o del estático), PENZD y NXYZD (Name, Easting,
  Northing, Elevation, Description), PNEZD (Name, Northing, Easting, Elevation, Description) y «Personalizada»
  (claves de columna en su orden, con encabezado propio o el del idioma).
- Coma, punto y coma o tabulador; encabezados en inglés (Emlid) o español; sin encabezado solo en las tres cortas;
  con o sin BOM; coma decimal solo con punto y coma o tabulador y solo en las columnas numéricas con decimales.
- Mismas reglas de escritura que el CSV de Field (RFC 4180, CRLF, neutralizar fórmulas, vacío es sin dato).
- Solo los puntos posprocesados (un PNEZD no tiene dónde decir que una fila es de campo). En geográficas, «Easting»
  es la longitud con 8 decimales, como en las apps.

## 4. El vértice del estático en el CSV de Field

Las 94 columnas de «Todas las columnas» en inglés y cuatro al final: `Static solution` (FIX si todas las líneas base
usadas fijaron), `Static baselines` (cada base con su longitud y solución), `Static adjustment` (el ajuste o «una sola
línea base») y `Processed with`. Origen `Global`, tipo de correcciones `Static`, σ del sistema elegido (con la
covarianza del ajuste) en las RMS, inicio y fin de la sesión en UTC, épocas, satélites por sistema, alturas de antena
(bastón, desplazamiento del receptor a 4 decimales y total) y, con una sola base, sus columnas de base y la línea
base. Las apps lo leen como cualquier CSV de Emlid (nombre, código, Este, Norte, cota y descripción; o latitud,
longitud y altura elipsoidal si el sistema es otro): sirve para reimportar el vértice como base o punto de control.

## 5. Trayectoria y RINEX de la sesión

- `export_trajectory(result, choice, "pos" | "csv", out)`: el `.pos` de RTKLIB tal cual, o un CSV con una fila por
  época: época UTC, solución (FIX, FLOAT, SBAS, DGPS, SINGLE, PPP), satélites, razón, latitud y longitud con 9
  decimales y altura elipsoidal de la **antena (ARP)** en el marco de la base, Este, Norte y cota de la antena en el
  sistema elegido, y la σ de RTKLIB en los ejes locales (este, norte, arriba; no de cuadrícula).
- `export_rinex(observations, folder)`: copia la observación y la navegación de la sesión (lo convertido de la
  grabación del Meridian, o lo unido y recortado de un RINEX) a una carpeta, con sus nombres.
