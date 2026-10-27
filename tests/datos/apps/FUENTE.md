# Vectores de las apps TresVizo Field

Copias **tal cual** (comprimidas con `gzip -9n` las que lo dicen) de los archivos con que las apps de iOS y Android
comprueban sus sistemas y geoides. Salen del repositorio de iOS (`TresVizoField`), commit `532eeb5` del 21-10-2026:

| Archivo | Origen | Qué es |
| --- | --- | --- |
| `crs_reference.json.gz` | `tools/crs/salida/crs_reference.json` | 262 sistemas (14 de fábrica y 248 de los paquetes) y sus vectores, calculados con PROJ 9.8.1, pyproj 3.8.0 y el registro EPSG v12.029 por `tools/crs/generar_paquetes.py`; los vectores NTv2 de la rejilla sintética |
| `paquetes/crs-*.json.gz` | `tools/crs/salida/paquetes/` | los nueve paquetes publicados (revisión 1 de `datos-2026-10-21`); las pruebas comprueban que su SHA-256 es el del índice firmado |
| `prueba_ntv2.gsb` | `tools/crs/salida/prueba_ntv2.gsb` | rejilla NTv2 sintética (madre e hija) de las pruebas de las apps |
| `fixtures.json` | `tools/geoides/pruebas/fixtures.json` | rejilla `.mvgeoid` pequeña, ondulaciones de GGM10 y GGM25 calculadas por la referencia y sobres firmados con una llave **de prueba** (pública a propósito; nunca firmó nada real) |

Si las apps regeneran sus vectores, se vuelven a copiar de ahí (los mismos bytes) y las pruebas tienen que seguir
pasando.
