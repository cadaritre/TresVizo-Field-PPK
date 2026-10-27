# Vectores de las apps TresVizo Field

Copias **tal cual** (comprimidas con `gzip -9n` las que lo dicen) de los archivos con que las apps de iOS y Android
comprueban sus sistemas y geoides. Salen del repositorio de iOS (`TresVizoField`): los vectores, la rejilla y los paquetes del commit `73376c0` del
26-10-2026 (índice publicado en `48ae9ad`, secuencia 4); el CSV, del commit que dice su fila:

| Archivo | Origen | Qué es |
| --- | --- | --- |
| `crs_reference.json.gz` | `tools/crs/salida/crs_reference.json` | 1255 sistemas (14 de fábrica y 1241 de los paquetes) y sus vectores (10 904 puntos con plano, 628 de ellos por un datum heredado y 8 solo de proyección, de los sistemas con rejilla NTv2 real), calculados con PROJ 9.8.1, pyproj 3.8.0 y el registro EPSG v12.029 por `tools/crs/generar_paquetes.py`; los vectores NTv2 de la rejilla sintética |
| `paquetes/crs-*.json.gz` | `tools/crs/salida/paquetes/` | los 34 paquetes publicados (`datos-2026-10-26`; los nueve primeros en su revisión 2); las pruebas comprueban que su SHA-256 es el del índice firmado |
| `prueba_ntv2.gsb` | `tools/crs/salida/prueba_ntv2.gsb` | rejilla NTv2 sintética (madre e hija) de las pruebas de las apps |
| `csv_referencia_todas.csv` | `TresVizo Field/Packages/MeridianVCore/Tests/MeridianVCoreTests/Resources/` (commit `f90b05f`) | el CSV «Todas las columnas» que las dos apps escriben byte a byte; la exportación tiene que reescribirlo igual |
| `fixtures.json` | `tools/geoides/pruebas/fixtures.json` | rejilla `.mvgeoid` pequeña, ondulaciones de GGM10 y GGM25 calculadas por la referencia y sobres firmados con una llave **de prueba** (pública a propósito; nunca firmó nada real) |

Si las apps regeneran sus vectores, se vuelven a copiar de ahí (los mismos bytes) y las pruebas tienen que seguir
pasando.
