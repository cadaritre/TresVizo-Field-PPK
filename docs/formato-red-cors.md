# Formato de paquete de red de estaciones de referencia (CORS, `cors-network`), versión 1

Una **red de estaciones de referencia** (RGNA de México, RBMC de Brasil, MAGNA-ECO de Colombia, RAMSAC de Argentina,
la red de Chile, NOAA CORS…) se agrega a TresVizo Field PPK publicando un paquete, **sin sacar versión nueva del
programa** (especificación §3.0). El paquete es un archivo JSON en UTF-8; se publica como una entrada de clase
`cors-network` del mismo índice firmado (Ed25519) que los sistemas de coordenadas y los geoides
(`cadaritre/GNSS-TresVizo`), con los campos comunes del índice (`id`, `version`, `revision`, `title`, `countries`,
`authority`, `license`, `attribution`, `bounds`, `file` con `bytes` y `sha256`). El programa trae incluido el de la
RGNA (`tresvizo_ppk/core/data/redes/rgna-inegi.json`); los instalados del índice se guardan en la carpeta de la
clase `cors-network` de la biblioteca de datos. Si una red está en los dos sitios, manda la de `revision` más alta.

Lector: `tresvizo_ppk/core/networks.py` (`parse_package` dice qué falla). Ejemplo completo: el paquete de la RGNA,
que arma `scripts/red_rgna_inegi.py` desde la fuente oficial del INEGI.

## Campos de la red

| Campo | Obligatorio | Qué es |
| --- | --- | --- |
| `format` | sí | `"tresvizo-cors-network"` |
| `formatVersion` | sí | `1`. Un programa rechaza paquetes de versión más nueva («actualiza el programa»). |
| `id` | sí | clave estable, minúsculas, la misma del índice: `rgna-inegi`, `rbmc-ibge`… |
| `revision` | sí | entero que sube con cada publicación |
| `title` | sí | como sale en la interfaz y el informe: «RGNA — INEGI, México» |
| `shortName` | no | «RGNA» (por omisión, `id`) |
| `countries` | sí | códigos ISO 3166-1 alfa-2: `["MX"]` |
| `authority` | sí | institución responsable |
| `frame` | sí | **marco y época oficial de las coordenadas**: `{"name": "ITRF2008", "epoch": 2010.0, "ellipsoid": "GRS80"}`; RBMC sería `{"name": "SIRGAS2000", "epoch": 2000.4, …}`. Es el marco del resultado de quien se liga a la red. |
| `referencePoint` | no | a qué punto se refieren las coordenadas y la altura de antena (marca, ARP) |
| `source` | no | `{"title", "url", "cutoff", "retrieved"}`: fuente oficial, fecha de corte de la fuente y fecha de consulta |
| `license` | sí | términos de uso de los datos, y la transformación que se les hizo si la licencia pide decirla |
| `attribution` | sí | texto que el informe pone tal cual («Fuente: INEGI, …») |
| `download` | no | cómo se bajan los RINEX (abajo). Sin él: `{"type": "none"}` |
| `antennaCalibrations` | no | `{"source": "IGS, ANTEX igs20_2437", "antex": "<bloques ANTEX>"}`: calibraciones de antenas de sus estaciones que no estén en el ANTEX incluido; se suman al que lee RTKLIB |
| `notes` | no | avisos generales de la red |
| `stations` | sí | lista de estaciones (abajo), al menos una |

### Campos para el índice firmado

`publicar.py --redes` (repositorio de TresVizo Field, `tools/geoides/`) arma la entrada `cors-network` del índice con
la cabecera del paquete; por eso un paquete que se publica lleva además, en la raíz: `version` (texto: la fecha de
corte de la fuente), `region` (texto para la lista), `licenseURL` y `bounds` (`{"south", "north", "west", "east"}`, la
caja de sus estaciones). La app no los necesita para leer la red (`parse_package` los ignora). La entrada del índice
lleva también `corsNetwork`: `{"format", "formatVersion", "stations", "frame", "epoch"}`, para mostrar la red antes
de bajarla.

## Campos de cada estación

| Campo | Obligatorio | Qué es |
| --- | --- | --- |
| `id` | sí | clave de 4 caracteres (o la larga de 9), única en la red; se compara con MARKER NAME del RINEX |
| `name` | no | lugar («Guanajuato, Gto.») |
| `x`, `y`, `z` | uno de los dos | cartesianas geocéntricas en metros, en el marco de la red |
| `latitude`, `longitude`, `height` | uno de los dos | geodésicas (grados decimales, este positivo; altura elipsoidal en metros) en el marco y elipsoide de la red. Si están los dos, mandan `x`, `y`, `z`. |
| `frame`, `epoch` | no | solo si esta estación tiene otro marco o época que la red |
| `sigma` | no | `{"e", "n", "u"}` en metros, si la fuente publica la precisión; entra al ajuste |
| `antennaHeight` | no | vertical de la marca a la ARP, metros (0 si se omite) |
| `antennaType`, `radome` | no | nombre IGS/ANTEX sin espacios internos y radomo (`NONE`) |
| `receiver` | no | receptor |
| `status` | no | `active` u otro texto |
| `notes` | no | avisos de la estación (desplazamientos por sismo, hundimientos, antena sin calibración publicada…): salen en la ficha de la estación y en los avisos del proceso |
| `velocity`, `equipmentSince`, `country`… | no | datos de la fuente que el programa no usa hoy y se conservan (velocidad `{"n", "e", "u", "unit": "mm/a"}`, fecha desde la que vale la antena, país ISO de la estación) |

El programa comprueba que la coordenada esté en la superficie de la Tierra y que las claves no se repitan. Al
procesar, la antena y la altura del **RINEX de la estación** mandan sobre las del paquete (son las de esos datos), y
la diferencia se avisa.

## Descarga de los RINEX (`download`)

- `{"type": "none"}`: el usuario entrega los archivos.
- `{"type": "guide", "url": "<página>", "granularity": "hourly"|"daily", "hourLabels": [24 textos], "filePattern":
  "{ID}{doy}{h}", "instructions": "…"}`: la red no publica una dirección para programas; el programa dice qué pedir
  (fechas y horas UTC, con los nombres de hora del formulario si los hay, y los nombres de archivo) y en qué página.
  Es el caso de la RGNA.
- `{"type": "url-template", "granularity": "hourly"|"daily", "url": "https://…/{yyyy}/{doy}/{id}{doy}0.zip"}`: la
  red publica sus RINEX en una dirección pública **documentada** y sin cuenta; el programa los baja a petición del
  usuario, diciendo qué baja y de dónde. Solo `https`.

Variables de `url` y `filePattern`: `{ID}` y `{id}` (clave en mayúsculas o minúsculas), `{yyyy}`, `{yy}`, `{mm}`,
`{dd}`, `{doy}` (día del año, 3 cifras), `{hh}` (hora UTC, 2 cifras), `{h}`/`{H}` (letra de la hora: a = 00 h … x =
23 h), todo en UTC.

Nunca se publica un paquete que dependa de raspar una página o de una cuenta o contraseña: si la red no tiene
dirección pública documentada, va con `guide`.
