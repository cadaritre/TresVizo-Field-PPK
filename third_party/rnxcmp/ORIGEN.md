# RNXCMP 4.2.0 (crx2rnx, compresión Hatanaka)

- **Qué es:** el programa de Yuki Hatanaka (Autoridad de Información Geoespacial de Japón, GSI) que restaura los
  RINEX comprimidos en formato Hatanaka (`.crx`, `.YYd`), el formato en que entregan sus datos el INEGI y la IGS.
- **De dónde:** <https://terras.gsi.go.jp/ja/crx2rnx.html>, `RNXCMP_4.2.0_src.tar.gz`,
  SHA-256: `9a452d2908a27c5b3152cc424e048847221c065e97f6b1f1a585c7b0c34fbace`.
- **Qué se copió (sin cambios):** `source/crx2rnx.c`, `docs/LICENSE.txt`, `docs/README.txt`, `docs/CHANGES.txt`.
- **Licencia:** `LICENSE.txt` (términos de uso del sitio de la GSI, compatibles con CC BY 4.0). Se usa sin modificar
  y se cita como pide la licencia:
  Hatanaka, Y. (2008), A Compression Format and Tools for GNSS Observation Data, Bulletin of the Geospatial
  Information Authority of Japan, 55, 21-30 (<https://www.gsi.go.jp/ENGLISH/Bulletin55.html>).
- **Compilación:** `python scripts/build_rtklib.py` (lo compila junto con RTKLIB).
