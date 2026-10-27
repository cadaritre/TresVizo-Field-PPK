# Estado

0.1.0 — en desarrollo. Cada sección dice lo verificado y lo pendiente.

## Motor (§1.1, §1.2, §2, §3.0–3.3, §4, §8): `tresvizo_ppk/core/`

**Hecho y verificado (macOS 26 arm64, Python 3.13.18, RTKLIB-EX 2.5.1 compilado aquí):**
- RTKLIB-EX 2.5.1 (sucesor de demo5 b34L, BSD-2) y crx2rnx (RNXCMP 4.2.0) como fuente fija en `third_party/` con su
  origen y SHA-256; `scripts/build_rtklib.py` compila `rnx2rtkp`, `convbin` y `crx2rnx` con cc/gcc/clang/MinGW/MSVC
  sin más dependencias (probado aquí con clang; Windows y Linux solo por la CI, que no ha corrido).
- RINEX 2.11/3.x/4: cabecera, épocas, unión de archivos horarios o diarios (sin repetir épocas, sin perder valores,
  unión de tipos si difieren, canales GLONASS unidos) y recorte; **RTKLIB da exactamente la misma solución con los
  archivos sueltos que con el unido** (prueba). Descompresión .zip/.gz/.Z (LZW propio, igual byte a byte a
  `compress`) y Hatanaka v2/v3.
- Grabaciones `.bin`/`.part` del Meridian con el convertidor del repo MeridianV copiado (commit 48e80e6, sus 43
  pruebas pasan aquí); altura del bastón y nombre del punto de la sesión por omisión; todo lo del encabezado (o del
  catálogo de la red, que gana y avisa) llega en el `ObservationSet` con su origen (`field_sources`).
- Control de calidad tipo TEQC (épocas, huecos, saltos de ciclo, multitrayectoria MP, señales, traslape).
- Redes CORS como paquetes `cors-network` (formato en `docs/formato-red-cors.md`, comprobación registrada en
  `tresvizo_ppk/datos/`): la RGNA incluida (31 estaciones del INEGI, ITRF2008 2010.0, con atribución), redes de otros
  países instalables del índice firmado con su marco y época, descarga por plantilla https documentada o guía.
- Coordenada de la base sin pedirla a mano: la oficial de su red; base propia: encabezado RINEX o media autónoma
  de la sesión, marcadas aproximadas con su origen (`base_coordinate_candidates`).
- ANTEX igs20 (satélites y antenas de la RGNA) y órbitas SP3 a petición (ESA o IGS en el BKG, sin cuenta).
- Estático: estrategia por longitud, modelo de error calibrado, ajuste por mínimos cuadrados con chi², prueba w y
  exclusión automática de una línea que no cuadra con tres bases. PPK de puntos: trayectoria combinada, promedio por
  ocupación, todo plomeado, σ con mínimos realistas. Línea de órdenes `python -m tresvizo_ppk estatico|puntos`.
- **Validación con datos reales de la RGNA** (`docs/validacion.md`): líneas de 100–141 km, error del vector frente a
  una referencia PPP del día de 2–3 cm (1 h), 1.5 cm (2 h), ≤ 1 cm (4 h) y ≤ 6 mm (24 h); 30 km fija dentro de 2 cm.
  Detectó el hundimiento de INEG (0.74 m desde 2010.0).
- Pruebas: 110 en `tests/core` (más las 43 del convertidor), con muestras reales pequeñas de la RGNA.

**Pendiente o sin verificar:**
- **Ninguna grabación real de MeridianV/MeridianVI** ni jornada real con el CSV de TresVizo Field: falta una muestra
  del propietario (base + rover + CSV) para validar el PPK de puntos y la resolución de ambigüedades en cinemático.
  Con una estación de la RGNA como rover a 30 km y 20 min, los puntos salen flotantes (0.1–0.5 m).
- **Líneas largas solo flotantes:** RTKLIB no resuelve ambigüedades a 100+ km (su ionosfera estimada no fijó nunca).
  La solución profesional es una resolución propia ancha/estrecha sobre la libre de ionosfera, como TBC; hoy da
  1–3 cm con 1–2 h y ≤ 1 cm con 4 h o más (validado), pero sin el «fijo» que muestran los programas comerciales.
- **Descarga directa de la RGNA (§3.2): no se automatiza.** La página del INEGI es un formulario para personas sin
  servicio documentado para programas, y su SFTP público (geodesia2.inegi.org.mx) usa un usuario y contraseña
  publicados en la página: meterlos en el programa es decisión del propietario (técnicamente es sencillo con un
  cliente SFTP; habría que elegir uno con licencia compatible). Hoy la app guía con fechas, horas y nombres de
  archivo exactos (`station_download_guide`).
- La resolución de ambigüedades de GLONASS y BeiDou está apagada (impidió fijar en las pruebas); sus satélites
  entran flotantes. Probar de nuevo con datos Meridian–Meridian (mismo receptor).
- No hay transformación de época ni de marco (§3.4): el resultado queda en el marco de la base. Con estaciones que
  se mueven (INEG, COL2) la coordenada oficial ya no es su posición: el paquete de red lleva avisos por estación.
- El ANTEX incluido solo trae las antenas de la RGNA; otra red puede traer las suyas en su paquete.
- La comprobación completa de los paquetes `cors-network` se registra al importar `tresvizo_ppk.core.networks`
  (lo hace `core.api`): si la biblioteca instala una red antes de cargarse el motor, solo se comprueba el JSON.
- Compilación de RTKLIB en Windows (MinGW/MSVC) y Linux sin probar aquí.

## Interfaz y distribución (§1.4, §6, §2 y §7)

**Hecho y verificado (macOS 26 arm64, Python 3.13.18, Tk 9.1, tkdnd 2.10.3):**
- Ventana con barra lateral e iconos Lucide (Inicio, Georreferenciar punto, PPK de puntos, Sistemas y geoides,
  Estaciones RGNA, Ajustes, Acerca de) con el sistema visual de TresVizo Med: tokens y contraste (4.5:1 texto, 3:1 bordes,
  foco, series y estados de solución, comprobado en las pruebas para los temas Claro y Oscuro), letra del sistema,
  casillas, opciones y barras de desplazamiento propias, transición de 150 ms que respeta «Reducir movimiento», atajos
  (Ctrl/⌘+1…5, Ctrl/⌘+O, Ctrl/⌘+S, Ctrl/⌘+⇧+S, Alt+←/→ en los asistentes), menú nativo de macOS y apertura de `.tvppk`
  por doble clic o arrastrándolo a la ventana.
- Asistentes por pasos con arrastrar y soltar y botón equivalente: validación al momento (errores tras tocar el paso,
  avisos siempre, con icono y texto), proceso en un hilo aparte con progreso por etapas, tiempo y Cancelar, resultado con
  esquema en planta y barras de precisión, «Ver informe» (vista previa con pypdfium2 y «Guardar PDF…») y «Exportar»
  (estático: copiar coordenadas y PDF; PPK: CSV de TresVizo Field, CSV simple, GeoJSON y KML).
- Proyectos `.tvppk` (`tresvizo_ppk/project.py`): rutas relativas y SHA-256, escritura atómica, recientes, aviso al abrir
  si un archivo falta o cambió, coordenada final del estático para encadenar la base propia, número de proyecto.
- Pruebas: 43 de la interfaz (`tests/test_ui_logic.py`, `tests/test_project.py` sin ventanas; `tests/test_ui_widgets.py`
  con los widgets reales ocultos y los dobles del motor de `tests/dobles.py`: flujo completo de los dos asistentes,
  guardar, procesar, cancelar, error del motor, informe, exportar y reabrir).
- Revisión visual de cada pantalla con `python scripts/preview.py --view …` (datos sintéticos, carpeta temporal).
- PyInstaller: `TresVizo Field PPK.app` armada aquí (106 MB) con RTKLIB en `bin/`; su autoprueba `--self-test` pasa
  interfaz, tkdnd, binarios de RTKLIB, sistemas y geoides, PDF, gráficas y recursos. DMG de arm64 armado y montado
  (app, acceso a Aplicaciones y «Cómo abrirla.txt»).

**Pendiente o sin verificar:**
- La autoprueba exige `tresvizo_ppk.core.api` y `tresvizo_ppk.export.api` (agentes del motor y de exportación); hasta
  que existan falla en «modulos» y `scripts/distribuir.py` no entrega el instalador. La interfaz ya llama a esas
  funciones con las firmas de la §8 (y usa los mismos valores de `ionosphere` y `ambiguity_mode` que
  `core/baseline.py`) y se probó contra dobles; falta repetir el recorrido con el motor real y datos de la RGNA.
- La descarga de RINEX de la RGNA aparece solo si el motor ofrece `download_rgna`; si no, el asistente guía al usuario
  al sitio del INEGI.
- MSI de Windows y DMG de x86_64: solo los arma la CI (`.github/workflows/ci.yml`), que no ha corrido todavía (falta el
  push). No se ha probado en Windows real la instalación, actualización, reparación y desinstalación del MSI, ni el
  escalado de DPI de Windows (125/150/200 %).
- Instaladores sin firma de código (Authenticode / Developer ID y notarización): ver `docs/distribucion.md`.
- Sin lector de pantalla certificado: Tk no expone un árbol de accesibilidad completo; el uso con teclado sí se revisó.

## Sistemas, informes y exportación (§1.3, §5, §1.2.5)

**Hecho y verificado (macOS arm64, Python 3.13.18, pyproj 3.8.0 con PROJ 9.8.1 y EPSG v12.029):**
- Datos firmados comunes (`tresvizo_ppk/datos/`): Ed25519 en Python puro (vector de la RFC 8032), el índice firmado con
  la llave de las apps (`1737938db9f9f6bc`) y sus reglas (secuencia que solo crece, rutas, SHA-256, clases
  desconocidas se saltan), descarga con reintentos y reanudación, e instalación comprobada de cualquier clase
  registrada (`geoid`, `crs-package`, `datum-grid`, `cors-network`; el motor puede registrar su comprobación de las
  redes). Probado contra el índice publicado de verdad en `cadaritre/GNSS-TresVizo` (igual byte a byte al incluido,
  secuencia 3): se bajaron e instalaron `crs-cr`, `crs-sv` y `qgeoidcol2023` con su firma y su SHA-256
  (`TRESVIZO_PPK_RED=1 pytest tests/test_crs.py`).
- Sistemas (`tresvizo_ppk/crs/`): de fábrica WGS 84, Mexico ITRF2008 y UTM 11N–16N (definiciones de las apps) y GGM10 y
  GGM25 **byte a byte** los de las apps. `transform` y `transform_sigma` con PROJ: los 1316 vectores de
  `crs_reference.json` (262 sistemas, 458 de datum heredado) pasan con ida ≤ 3 nm, vuelta ≤ 5e-14°, escala ≤ 1.1e-11 y
  convergencia ≤ 2.4e-11° (las apps piden 1 µm, 1e-10°, 1e-9 y 1e-7°); la misma región de transformación en los 458;
  las 246 proyecciones salen del registro EPSG de PROJ y coinciden con la definición del paquete; NTv2 contra la
  rejilla sintética de las apps (≤ 1e-9°); las 56 ondulaciones de GGM10/GGM25 de las apps dan **el mismo número**.
- Informes PDF (`tresvizo_ppk/report/`): línea base y PPK de la jornada con la identidad de los documentos de TresVizo
  Med, todas las secciones de §5 y las gráficas de RTKLIB (`.pos` y `.stat`); el marco y la época del resultado son
  los de la coordenada de la base (su red), no fijos. Pruebas con resultados sintéticos (estaciones PRB1–PRB3, que no
  son de la RGNA) que leen el texto con pypdf; revisión visual de las páginas.
- Exportar (`tresvizo_ppk/export/`): CSV de TresVizo Field (el escritor reproduce byte a byte `csv_referencia_todas.csv`
  de las apps), CSV simple, GeoJSON y KML. 41 pruebas en `test_crs.py`, `test_report.py` y `test_export.py`.

**Pendiente o sin verificar:**
- Informes y exportación probados solo con resultados sintéticos: falta repetirlos con el motor real y datos de la
  RGNA (los `.pos`/`.stat` que escribe de verdad `rnx2rtkp`; el lector sigue el formato de `rtkpos.c`/`solution.c`).
- La convención de `ProcessedPoint.delta_from_field_enu` no está escrita en el contrato: el informe y la exportación
  calculan «posproceso − campo» de las dos posiciones cuando están, y solo usan ese campo si falta la de campo.
- Sistemas propios (de mina, LDP) y localizaciones de las apps no entran (no hay dónde elegirlos en un proyecto
  `.tvppk`); los vectores `localDesigns` de `crs_reference.json` no se prueban.
- Las rejillas NTv2 de IBGE siguen retenidas en el índice; el código las instala y aplica (probado con la sintética).
- Letra del PDF: Segoe UI en Windows (como TresVizo Med), Arial en macOS, DejaVu Sans en Linux (la de matplotlib si el
  sistema no trae ninguna). En Windows real no se ha generado un PDF todavía.
- El paquete de PyInstaller tiene que llevar `tresvizo_ppk/crs/datos/`, `tresvizo_ppk/datos/incluido/` y
  `assets/marca.png` (el informe la busca también en `sys._MEIPASS`).
- El formato del paquete `cors-network` lo define el motor: aquí solo se comprueba que sea JSON con el mismo id.
