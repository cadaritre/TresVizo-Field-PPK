# RTKLIB-EX 2.5.1 (sucesor de RTKLIB demo5)

- **Qué es:** la rama de RTKLIB que mantiene rtklibexplorer, antes llamada «demo5» (última etiqueta con ese nombre:
  `b34L`, mayo de 2025); desde 2.5.0 se publica como RTKLIB-EX. Es la que usan Emlid Studio y la comunidad de
  receptores de bajo costo.
- **De dónde:** <https://github.com/rtklibexplorer/RTKLIB>, etiqueta `v2.5.1` (publicada el 22-06-2026),
  `https://github.com/rtklibexplorer/RTKLIB/archive/refs/tags/v2.5.1.tar.gz`,
  SHA-256 del archivo: `ad8e56f64dd2b71e2eb8098c27925a65b027b161eae366cf5afb71402348d2e1`.
- **Qué se copió (sin cambios):** `src/*.c`, `src/rtklib.h`, `src/rcv/*.c`, `app/consapp/rnx2rtkp/rnx2rtkp.c` →
  `app/rnx2rtkp/`, `app/consapp/convbin/convbin.c` → `app/convbin/`, `license.txt` y `readme.txt`. No se copiaron
  las interfaces gráficas, los datos de prueba ni las bibliotecas de Windows.
- **Licencia:** BSD de 2 cláusulas (`license.txt`), Copyright (c) 2007-2020 T. Takasu. El aviso va en el
  instalador junto a los ejecutables.
- **Compilación:** `python scripts/build_rtklib.py` (opciones de los makefiles de `app/consapp/*/gcc/`:
  todas las constelaciones, `NFREQ=4`, `NEXOBS=3`, sin LAPACK).
- **Actualizar:** bajar la etiqueta nueva, reemplazar estos archivos, anotar aquí versión y SHA-256 y volver a
  correr las pruebas del motor y la validación (`docs/validacion.md`).
