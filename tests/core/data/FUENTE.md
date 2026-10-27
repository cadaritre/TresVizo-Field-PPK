# Datos de prueba del motor

Muestras pequeñas de datos públicos reales de la **Red Geodésica Nacional Activa (RGNA) del INEGI**, 20 de octubre
de 2026 (día 293), bajadas de «Archivos RINEX de la RGNA» (https://www.inegi.org.mx/app/geo2/rgna/).
Fuente: INEGI, Archivos RINEX de la Red Geodésica Nacional Activa (uso libre con atribución, Términos de Libre Uso
de la Información del INEGI).

Transformación hecha aquí: recortes de tiempo con `tresvizo_ppk.core.rinex.merge_observations` (sin tocar los
valores) y compresión.

| Archivo | Origen | Contenido |
| --- | --- | --- |
| `ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz` | ITLA293o (RINEX 3.04) | ITLA (Tlaxcala), 14:00–14:20 GPS, 81 épocas cada 15 s |
| `ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz` | ICEP293o (RINEX 3.04) | ICEP (Puebla), mismo tramo; ITLA–ICEP: 30.7 km |
| `ITLA00MEX_R_20262931400_01H_MN.rnx.gz` | ITLA293o | navegación mixta de esa hora |
| `UGTO_a.rnx.gz`, `UGTO_b.rnx.gz` | UGTO293o (RINEX 3.04) | 14:00:00–14:03:00 y 14:02:30–14:05:00 (traslapan 3 épocas) |
| `UGTO_a.rnx.Z` | el mismo `UGTO_a` | comprimido con `compress` (LZW, 16 bits) |
| `UGTO_a.crx.gz` | el mismo `UGTO_a` | Hatanaka (rnx2crx 4.2.0) y gzip |
| `INEG2930.26o.gz`, `.26n.gz`, `.26g.gz` | INEG293o (RINEX 2.11) | INEG 14:00–14:05, navegación GPS y GLONASS |
| `INEG2930.26d.gz` | el mismo `.26o` | Hatanaka (rnx2crx 4.2.0) y gzip |

Referencia para las pruebas de punta a punta (PPP estático de 24 h del mismo día con órbitas y relojes finales de
la ESA, `scripts/validacion_rgna.py`): ver `tests/core/test_e2e.py`.
