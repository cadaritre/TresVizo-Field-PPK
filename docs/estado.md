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
- Redes de estaciones de referencia (CORS) como paquetes `cors-network` (formato en `docs/formato-red-cors.md`, comprobación registrada en
  `tresvizo_ppk/datos/`): la RGNA incluida (31 estaciones del INEGI, ITRF2008 2010.0, con atribución), redes de otros
  países instalables del índice firmado con su marco y época, descarga por plantilla https documentada o guía.
- **Paquetes de redes de otros países, listos para el índice firmado (27-10-2026; los publica el coordinador):**
  NOAA CORS del NGS (`scripts/red_noaa_cors.py`, 1854 estaciones, NAD83(2011) 2010.0 y PA11/MA11, ARP, dominio
  público, 12,7 MB por las calibraciones), CACS de NRCan (`red_cacs_nrcan.py`, 135, NAD83(CSRS) 2010.0, OGL-Canada; sin
  las de Nueva Escocia, de licencia provincial) y ERGNSS del IGN (`red_ergnss_ign.py`, 130, ETRS89 2017.0 y REGCAN95
  en Canarias, CC BY 4.0), con descarga directa de RINEX diarios por plantilla documentada. Calibraciones de antena del
  ANTEX de la IGS y, si no está, del ngs20.atx del NGS; 32 estaciones WAAS de la NOAA (MPL_WAAS_2225N, sin calibración
  publicada) llevan el aviso «antena sin calibración». Comprobado: geodésicas contra cartesianas de cada fuente (1,2 mm
  NOAA, 3,4 mm IGN), estaciones a mano, y que la app los instala del índice, los lista y avisa
  (`tests/test_redes_paquetes.py`). **Retenidas por licencia** (sin licencia escrita que permita redistribuir, o de
  pago): Colombia (MAGNA-ECO), Chile, Perú (REGPMOC), Uruguay (REGNA-ROU), Ecuador, Bolivia, Costa Rica, Guatemala,
  Panamá, El Salvador, Brasil (RBMC) y Argentina (RAMSAC). Pendiente del motor: `find_station` elige por clave sin mirar
  la posición, y MELI existe en la NOAA y en el IGN (Melilla).
- Coordenada de la base sin pedirla a mano: la oficial de su red; base propia: encabezado RINEX o media autónoma
  de la sesión, marcadas aproximadas con su origen (`base_coordinate_candidates`).
- ANTEX igs20 (satélites y antenas de la RGNA) y órbitas SP3 a petición (ESA o IGS en el BKG, sin cuenta).
- **Procesador propio de líneas largas** (`tresvizo_ppk/core/larga`, desde 20 km en «auto»): libre de ionosfera de
  GPS, Galileo y BeiDou con la misma clase de señal en los dos receptores, órbitas difundidas o SP3, PCO/PCV de
  receptor y satélite, mareas sólidas y GMF del IERS 2010, troposfera estimada con gradientes, wind-up; saltos de ciclo
  con Melbourne-Wübbena y libre de geometría; ancho de carril por arco y carril estrecho con LAMBDA, tasa de éxito,
  prueba de razón, prueba χ² de la distancia entera y resolución parcial; duración mínima para fijar (30 min hasta
  60 km, 1 h desde ahí); RTKLIB corre como comprobación independiente. **Validado con la RGNA**
  (`docs/validacion.md`): fijó las 104 sesiones de 1 h a 24 h a 30–141 km sin ningún fijo erróneo, con 1 h a
  100–141 km un RMS del error de 0.4–1.2 cm en horizontal y 0.6–2.2 cm en vertical (antes, flotante con RTKLIB, 2.1 y
  3.2 cm) y σ realista; modelos probados contra los casos del IERS,
  de RTKLIB y de Teunissen. El informe dice la resolución de ambigüedades o por qué quedó flotante.
- Estático: estrategia por longitud, modelo de error calibrado, ajuste por mínimos cuadrados con chi², prueba w y
  exclusión automática de la línea que no cuadra con tres bases (lectura de datos de Baarda; aviso si las que quedan
  no pasan la χ² por el movimiento de las bases desde la época de su coordenada).
- RTKLIB corre en una carpeta de nombres ASCII con rutas relativas (rutas con acentos en Windows) y las órbitas
  precisas comprimidas se descomprimen antes; probado con un proyecto en una carpeta con acentos y eñes. PPK de puntos: trayectoria combinada, promedio por
  ocupación, todo plomeado, σ con mínimos realistas. Línea de órdenes `python -m tresvizo_ppk estatico|puntos`.
- **Validación con datos reales de la RGNA** (`docs/validacion.md`): ver arriba; detectó el hundimiento de INEG
  (0.74 m desde 2010.0) y la deja fuera del ajuste en todas las sesiones.
- Pruebas: 110 en `tests/core` (38 del procesador de líneas largas) más las 45 del convertidor, con muestras reales
  pequeñas de la RGNA y de la ESA; la suite completa: 275.

**Pendiente o sin verificar:**
- **Ninguna grabación real de MeridianV/MeridianVI** ni jornada real con el CSV de TresVizo Field: falta una muestra
  del propietario (base + rover + CSV) para validar el PPK de puntos y la resolución de ambigüedades en cinemático.
  Con una estación de la RGNA como rover a 30 km y 20 min, los puntos salen flotantes (0.1–0.5 m).
- **Líneas largas: validadas con un solo día, una región y un solo tipo de receptor y antena** (Trimble Alloy con
  TRM115000.00 en todas las estaciones). Falta probarlas con un MeridianV/MeridianVI contra la RGNA (receptores
  distintos: el motor solo hace dobles diferencias con la misma clase de señal, lo demás queda flotante) y, sobre todo,
  con la calibración de la antena de los Meridian: en la libre de ionosfera la diferencia de centro de fase con la de
  la base pesa unas 3 veces más que en L1 (centímetros en la vertical). Sin carga oceánica (1–2 mm tierra adentro;
  hasta 5–10 mm en la costa). Sin pares de la RGNA de 20–30 km ni de más de 141 km para probar. GLONASS no entra en
  el propio (FDMA), ni QZSS; BeiDou geoestacionario tampoco. La duración mínima y los modelos de error se calibraron
  con ese día (`docs/validacion.md` §3 y §4): conviene repetir la validación con otro día y otra zona.
- El procesador propio deja en el motor el aviso de la licencia del software de las Convenciones del IERS (GMF y
  mareas, `tresvizo_ppk/core/larga/iers.py`): la licencia pide su fuente y el aviso íntegro en toda distribución de
  la fuente; el instalador lleva bytecode. Conviene copiar `iers.py` (o el aviso) a la carpeta `licencias/` del
  instalador (`scripts/distribuir.py`, del coordinador).
- **Descarga de la RGNA (§3.2): la hace el usuario (decisión del propietario, 26-10-2026).** La página del INEGI es un
  formulario para personas, sin servicio documentado para programas; su SFTP público (geodesia2.inegi.org.mx) usa un
  usuario y contraseña publicados en la página, y el propietario decidió no meterlos en el programa. La app guía con
  fechas, horas y nombres de archivo exactos (`station_download_guide`) y el usuario arrastra lo que bajó.
- La resolución de ambigüedades de GLONASS y BeiDou está apagada (impidió fijar en las pruebas); sus satélites
  entran flotantes. Probar de nuevo con datos Meridian–Meridian (mismo receptor).
- No hay transformación de época ni de marco (§3.4): el resultado queda en el marco de la base. Con estaciones que
  se mueven (INEG, COL2) la coordenada oficial ya no es su posición: el paquete de red lleva avisos por estación.
- El ANTEX incluido solo trae las antenas de la RGNA; otra red puede traer las suyas en su paquete.
- La comprobación completa de los paquetes `cors-network` se registra al importar `tresvizo_ppk.core.networks`
  (lo hace `core.api`): si la biblioteca instala una red antes de cargarse el motor, solo se comprueba el JSON.
- Compilación de RTKLIB en Windows (MinGW/MSVC) y Linux sin probar aquí.

## Interfaz y distribución (§1.4, §3.0, §3.5, §6, §2 y §7)

**Hecho y verificado (macOS arm64, Python 3.13.18, Tk 9.1, tkdnd 2.10.3):**
- Ventana con barra lateral e iconos Lucide (Inicio, Georreferenciar punto, PPK de puntos, Sistemas y geoides, Redes y
  estaciones, Ajustes, Acerca de) con el sistema visual de TresVizo Med; temas Claro y Oscuro con contraste comprobado
  en pruebas; tamaño de letra de accesibilidad (normal, grande, muy grande); «Reducir movimiento»; atajos de teclado;
  menú nativo de macOS; abrir `.tvppk` con doble clic o arrastrándolo.
- Todo se acomoda al tamaño: barra lateral completa, angosta o solo iconos; botones del encabezado solo con icono en
  páginas angostas; cápsulas, filtros y acciones que se envuelven; dos columnas que pasan a una; catálogo con lista y
  detalle lado a lado o uno sobre otro. Revisado con capturas de las 14 pantallas a 1024×640, 1280×820, 1600×1000 y
  1920×1080 y de 11 con letra «muy grande» (en el scratch del agente, fuera de git).
- Asistentes por pasos con arrastrar y soltar: al soltar los archivos se llenan solos (con `ObservationSet` y
  `field_sources`) nombre, altura del bastón o a la ARP, receptor, antena y sesión, con su origen y editables; la
  estación se propone por la clave del encabezado; la base propia no pide su coordenada (candidatas de
  `base_coordinate_candidates()`: oficial, encabezado RINEX o autónoma, más proyecto georreferenciado o punto conocido).
  Validación al momento, proceso en hilo con progreso y Cancelar, resultado con esquema y barras, «Ver informe» con
  vista previa y «Exportar».
- Recorrido real de punta a punta por la interfaz con el motor real y datos públicos de la RGNA (ICEP como rover contra
  ITLA y UQRO): proceso en 4 s, ajuste aprobado, coordenada en UTM 14N con cota GGM10 e informe PDF de 11 páginas.
- Sistemas y geoides como catálogo: clases con icono, color y conteo; búsqueda sin acentos; filtro de estado; región y
  país; lista perezosa; todo el índice visible e instalable; detalle completo. Redes y estaciones: redes de
  `cors_networks()` con su marco y época, estaciones, guía de `station_download_guide()` con el enlace a la página de
  la red (la descarga directa solo aparece si `can_download_station()` lo permite; hoy la RGNA no).
- Actualizaciones desde GitHub Releases (`app/updates.py`) con manifiesto firmado Ed25519 y `scripts/firmar_version.py`.
- Proyectos `.tvppk` con rutas relativas, SHA-256, aviso de archivos cambiados, número de proyecto y coordenada final.
- Robustez (27-10-2026, paquete 1 de la auditoría):
  - **Reabrir mientras se procesa.** Pregunta si se cancela el proceso y espera a que termine. Cada proyecto en
    pantalla tiene su generación, y un resultado viejo nunca cae en el nuevo. Cada lectura usa su propia carpeta
    temporal. Los `after` de Tk van en la ventana principal (`components.later`).
  - **Comprobación con el motor real.** El arnés de la auditoría (`reabrir2.py`) hizo 38 pasos con dos proyectos de
    la RGNA, reabriéndolos durante el proceso, en 40 s y sin ningún error.
  - **La estación del rover no es base.** Si está a menos de 30 m o tiene su clave, sale «Es el rover» y no se puede
    usar. El motor da un `UserError` si una línea base mide menos de 1 m o usa los mismos archivos que el rover.
  - **Base escrita a mano.** Lleva su marco y su época, se guardan en el proyecto y el informe los dice. Se avisa si
    no son los de la red de la zona (no se transforma), y el receptor sale de la grabación.
  - **Otros arreglos.** La tabla de líneas base muestra la clave y el nombre completo. Hay un campo «Lugar» para la
    firma. Con letra «muy grande», la ventana ya no se congela.
  - **Sin verificar.** No hay capturas de pantalla: `screencapture` no tiene permiso en el entorno de los agentes.
- Pruebas: la suite completa pasa (212, 3 omitidas), entre ellas 81 de la interfaz, proyectos y actualizaciones.
- PyInstaller: `TresVizo Field PPK.app` con RTKLIB y datos incluidos; su autoprueba pasa entera (interfaz, tkdnd,
  motor, sistemas, informe, exportación, binarios, datos); DMG de arm64 armado (51.7 MB) en este equipo, con el
  Python de Homebrew: solo abre en macOS 26, así que no se entrega (`docs/distribucion.md`).

**Pendiente o sin verificar:**
- **Llave de publicación de las actualizaciones:** `PUBLIC_KEY_HEX` en `app/updates.py` está vacía (marcador); el
  propietario la crea con `scripts/firmar_version.py --nueva-llave` (ver `docs/distribucion.md`). Hasta entonces la app
  avisa de versiones nuevas pero no instala.
- La CI ya corre: pruebas en Ubuntu, Windows y macOS y el MSI de Windows salen bien. El DMG de x86_64 espera su primera
  corrida en `macos-15-intel` (el `macos-13` de antes ya no existe en GitHub). Mínimo de macOS declarado y comprobado
  en el armado: 14 en Apple, 15 en Intel (lo fija pyproj 3.8.0). No se probó en Windows real el ciclo del MSI ni los
  DPI de Windows (125/150/200 %).
- Al soltar una grabación grande se convierte para leer sus datos y se vuelve a convertir al procesar (la copia de la
  lectura se borra); un lector solo de encabezado en el motor evitaría el doble trabajo.
- PPK de puntos de punta a punta con datos reales del MeridianV y su CSV: probado solo con los dobles.
- Instaladores sin firma de código (Authenticode / Developer ID y notarización).
- Sin lector de pantalla certificado (Tk no expone un árbol de accesibilidad completo); el teclado sí se revisó.

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
- Normas técnicas del INEGI (`report/normas.py`, 27-10-2026), leídas en su texto del DOF del 23-12-2010: CEP95 (NT EEP
  art. 6), EPV elipsoidal (art. 7) y de la cota (art. 12), orden de exactitud por punto (art. 8, tabla 1) y la última
  página con la declaración y el bloque de firma. La declaración solo sale con todas las bases de la RGNA en su
  coordenada oficial, resultado en ITRF2008 época 2010.0 (NT SGN arts. 10 y 12) y, si hay cota, el geoide vigente del
  INEGI (NT SGN art. 15, fr. III); si no, una línea dice por qué, y con otra red va una declaración neutra
  (`tests/test_report_normas.py`, valores a mano con σE ≠ σN y bordes de los 15 órdenes).
- Exportar (`tresvizo_ppk/export/`): CSV de TresVizo Field (el escritor reproduce byte a byte `csv_referencia_todas.csv`
  de las apps), CSV simple, GeoJSON y KML. 41 pruebas en `test_crs.py`, `test_report.py` y `test_export.py`.
- Exportación para CAD (27-10-2026, `docs/formatos-cad.md`, `tests/test_export_cad.py`): el **vértice del estático**
  en los mismos formatos que el PPK (su CSV de Field para reimportarlo en las apps como base o control); **DXF R12**
  con la especificación de las apps (capas por código y sus colores, Windows-1252, `999`, `SIN_COTA`) más el rótulo de
  la cota en `COTA`, y su `.prj`, leído de vuelta con ezdxf (capas, puntos 3D, rótulos, acentos, auditoría sin
  errores) y fijado a mano byte a byte desde el código de las apps; **LandXML 1.2** byte a byte con la referencia de
  las apps en lo que es de puntos y **validado contra `LandXML-1.2.xsd`**; **CSV a medida** (Todas, PENZD, PNEZD,
  NXYZD, Personalizada; separadores, idioma, sin encabezado, BOM, coma decimal); **trayectoria** del PPK (`.pos` y
  CSV por época) y **RINEX de la sesión**.
  En la interfaz (`app/export_ui.py`): el menú «Exportar» del estático y del PPK con los seis formatos, «CSV a medida»
  con «Opciones de CSV» (los textos de las apps, recordadas en las preferencias), la trayectoria (.pos y CSV) y «Exportar
  RINEX de la sesión»; el estático pide código y descripción del vértice (opcionales, guardados en el proyecto).
  Pruebas con los dobles en `tests/test_ui_export.py`. El diálogo no se revisó en una captura de pantalla (este equipo
  no da permiso de grabar la pantalla a la terminal).

**Pendiente o sin verificar:**
- Exportación para CAD: el DXF y el LandXML no se han abierto en AutoCAD ni en Civil 3D (no hay dónde); se leyeron con
  ezdxf y se validaron con el esquema oficial. El esquema (`LandXML-1.2.xsd` de landxml.org, 225 951 bytes, SHA-256
  `f4f732341970a923943f94592a3fde0838534515d564a699761e85420fad4d77`) no declara licencia y no va en el repo: se baja una
  vez de `http://www.landxml.org/schema/LandXML-1.2/LandXML-1.2.xsd` a
  `~/Documents/TresVizo-Field-PPK-datos/landxml/` (o a la ruta de `TVPPK_LANDXML_XSD`); sin él, sus dos pruebas se
  saltan (también en la CI).
- **La referencia LandXML de las apps no pasa el esquema** (`Feature` dentro de `CgPoint`, `code` en `PlanFeature`):
  lo tienen que corregir las apps (`docs/formatos-cad.md` §2). El rótulo `COTA` del DXF es de la oficina: las apps
  todavía no lo escriben.
- Líneas por código en el DXF y el LandXML: el CSV de Field no las trae; hace falta que las apps decidan cómo llegan.
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
- Normas del INEGI, sin confirmar: (1) la vigencia: la ficha del SNIEG de la NT EEP dice «Modificaciones: ninguna» y el
  comité de información geográfica básica la dio por vigente en 2024; no se revisó el DOF de 2025–2026. (2) «El modelo
  geoidal vigente» (NT SGN art. 15, fr. III) se toma como GGM25 porque la ficha de GGM10 lo marca histórico; GGM25 es
  compatible con NAPGD2022 (W₀) y el mismo art. 15 fija NAVD88 como nivel de referencia: confirmarlo con el INEGI.
  (3) La σN es la «precisión declarada» de la ficha del geoide (0.10 m GGM25, 0.20 m GGM10); no está confirmado que
  el INEGI la publique como 1σ. (4) El art. 12 de la NT EEP pide circuitos cerrados con ajuste para la nivelación
  GNSS: el informe da la EPV de cada punto y no los comprueba (lo dice).
- El lugar de la firma (`Project.place`) se guarda en el proyecto pero el asistente todavía no lo pide: el informe
  deja la raya para escribirlo a mano.
