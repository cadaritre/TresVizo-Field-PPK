# TresVizo Field PPK — especificación 1.0

Programa de oficina (Windows primero, macOS también) para posprocesar lo que se mide con los receptores
**MeridianV / MeridianVI** y la app **TresVizo Field**, ligado a la **Red Geodésica Nacional Activa (RGNA) del
INEGI**. Informe con calidad de Trimble Business Center (TBC). Público en GitHub, con licencia MIT.

## 1. Funciones de la 1.0

### 1.1 Georreferenciar un punto (estático) a la RGNA

1. **Rover.** El usuario arrastra la grabación del receptor (`<sesión>.bin` o `.part`, que se convierte a RINEX con
   el convertidor del MeridianV incluido en `tresvizo_ppk/core/meridian_rinex/`) o un RINEX (obs + nav). Escribe la
   **altura del bastón** (o del trípode, con el método de medida) y el **nombre del punto**. La altura total a la
   referencia de antena sale de las constantes del receptor (como hace `to_rinex.py --antenna-height`).
2. **Bases.** Una a tres estaciones de la RGNA. El usuario arrastra sus RINEX tal como los bajó del INEGI (varios
   archivos horarios o diarios; RINEX 2.11, 3.x o 4; `.Z`, `.gz`, `.zip`, Hatanaka `.d`/`.crx`): la app los
   descomprime, los **une** en un solo archivo por estación (lo que el propietario hacía a mano) y los recorta a la
   ventana del rover. Detecta la estación por el nombre del marcador y propone sus **coordenadas oficiales** del
   catálogo (§3.1); el nombre de la base se puede editar. **Descarga directa** de la RGNA si es posible (§3.2).
3. **Proceso.** RTKLIB demo5 en estático (§4): combinado adelante/atrás, enmascarado 15°, todas las constelaciones y
   frecuencias que tengan el rover y la base, libre de ionosfera para líneas largas, resolución de ambigüedades,
   efemérides precisas si el usuario las baja (§3.3) o difundidas si no. Una línea base por estación.
4. **Ajuste.** Con dos o más bases, **ajuste por mínimos cuadrados** de las líneas base con la base fija en su
   coordenada oficial: coordenada final, σ, residuos por línea base, prueba de bondad del ajuste (chi²) y detección de
   la línea base que no cuadra. Con una sola base, la coordenada sale de esa línea y el informe lo dice.
5. **Resultado** en el marco oficial (ITRF2008, época 2010.0, como la RGNA) y en el sistema, datum y geoide que elija el
   usuario (§3.4).
6. **Informe de posproceso de línea base** en PDF (§5.1).

### 1.2 PPK de varios puntos

1. **Base:** una estación de la RGNA (como en 1.1) **o la base propia** del usuario: un MeridianV que grabó como base,
   con la coordenada que salió de 1.1 (se encadena desde ese proyecto) o una coordenada escrita a mano con su marco.
2. **Rover:** la grabación cinemática del MeridianV (o su RINEX) de toda la jornada.
3. **Puntos:** el **CSV de TresVizo Field** de esa jornada (formato de las apps, `docs/csv-tresvizo-field.md`): por cada
   punto, nombre, código, inicio y fin de la ocupación en UTC del receptor, altura de antena y lo que se midió en
   campo. **En posproceso todo se asume plomeado** (decisión del propietario): el punto es la antena menos la altura
   vertical del bastón; la compensación de inclinación de campo no interviene.
4. **Proceso:** trayectoria cinemática combinada (adelante/atrás) y, por punto, las épocas de su ocupación: promedio de
   las fijas (o de las flotantes si no hay fijas, marcadas), σ H/V, número de épocas, solución (FIJA / FLOTANTE /
   SIN SOLUCIÓN), distancia a la base, y la diferencia con lo medido en campo.
5. **Exportar** los puntos posprocesados: el mismo CSV de TresVizo Field con las coordenadas nuevas y columnas de
   calidad del posproceso; además CSV simple (nombre, X, Y, Z, código), GeoJSON y KML, en el sistema elegido.
6. **Informe** de la jornada en PDF (§5.2).

### 1.3 Sistemas de coordenadas, datums y geoides

El **mismo catálogo firmado que las apps TresVizo Field** (publicado en `cadaritre/GNSS-TresVizo`, índice firmado
Ed25519 con la misma llave pública de las apps): paquetes `crs-package` por país y geoides `.mvgeoid`. Vienen
incluidos WGS 84, México ITRF2008 + UTM 11–16 N, GGM10 y GGM25; lo demás se descarga desde «Sistemas y geoides».
Mismo resultado que las apps, comprobado con sus vectores (`crs_reference.json` y las pruebas de geoides). Las
transformaciones van con PROJ (`pyproj`) usando los códigos EPSG de los paquetes y el geoide con el lector `.mvgeoid`
(interpolación idéntica a las apps).

### 1.4 Proyectos

Cada trabajo es un proyecto `.tvppk` (JSON con rutas relativas y sumas SHA-256 de los archivos de entrada): se guarda,
se abre y se vuelve a procesar con los mismos ajustes. La carpeta de trabajo y los intermedios (RINEX unidos,
configuración de RTKLIB, `.pos`, `.stat`) quedan junto al proyecto, para que el resultado sea reproducible.

## 2. Funciones esenciales añadidas (las de cualquier programa profesional del ramo)

- **Control de calidad de los datos** antes de procesar (tipo TEQC/Anubis): épocas, huecos, saltos de ciclo y
  multitrayectoria por satélite, satélites y señales, intervalo, y si el rover y la base se traslapan.
- **Modelos de antena (ANTEX igs20):** PCO/PCV de la antena de la estación RGNA por su tipo y radomo. La antena del
  Meridian (HA-901A) no está en el ANTEX: usa su PCO cuando esté medido (`antenna_pco_measured`); hasta entonces el
  informe dice «PCO de la antena del rover sin medir» (como el firmware).
- **Efemérides precisas** (órbitas y relojes IGS) descargables, necesarias para líneas base largas; el informe dice
  cuáles se usaron.
- **Gráficas** en pantalla y en el informe: cielo, satélites en el tiempo, residuos por satélite, cociente (ratio)
  y estado de la solución en el tiempo, DOP, trayectoria con los puntos.
- **Bitácora del proceso** (versión de RTKLIB, configuración completa, avisos) dentro del proyecto y en un anexo del
  informe.
- **Instalador** para Windows (MSI, como TresVizo Med) y para macOS (`.app` en `.dmg`), armados en GitHub Actions.

## 3. Datos externos (todos con su fuente, licencia y fecha)

### 3.1 Catálogo de estaciones de la RGNA

Nombre, clave, coordenadas oficiales (ITRF2008 época 2010.0) con sus σ, tipo de antena y radomo, altura de antena,
receptor, estado, de la fuente oficial del INEGI (hojas de datos o archivo de coordenadas de la RGNA). Se actualiza
desde el índice firmado. El usuario puede corregir una coordenada para un proyecto (queda en el informe).

### 3.2 Descarga de RINEX de la RGNA

Investigar el servicio del INEGI (geo2/rgna). Si hay una forma documentada y estable de bajar el RINEX de una estación y
fechas, la app lo hace a petición; si no, el asistente guía al usuario (qué estación, qué fechas y horas cubren su
sesión) y luego él arrastra los archivos. Nunca se automatiza raspando una página sin permiso.

### 3.3 Efemérides precisas

Productos IGS (finales, rápidos o ultra rápidos) de un servidor que no pida cuenta (investigar cuál: BKG, ESA, IGN…).
A petición del usuario. Si no hay, se procesa con difundidas y el informe lo dice.

### 3.4 Marco y época

La RGNA da coordenadas en ITRF2008 época 2010.0. Con la estación fija en esa coordenada, el resultado queda en ese
mismo marco y época (es lo que pide la norma del INEGI para ligar a la RGNA). La app no transforma épocas en la 1.0 (lo
mismo que las apps de campo): lo dice en el informe.

## 4. Motor

- **RTKLIB demo5** (rtklibexplorer, BSD-2) como fuente en `third_party/rtklib/` (instantánea fija con su versión y
  licencia) y `rnx2rtkp`/`convbin` compilados para Windows x64, macOS (arm64 y x86_64) y Linux por los scripts y la CI.
  Se llama como proceso con la configuración generada por la app; se leen `.pos` y `.stat` (residuos).
- **Hatanaka** (`crx2rnx`, licencia libre de su autor) y `.Z` (LZW) para los archivos del INEGI.
- Unión de RINEX propia (en Python), probada contra la de RTKLIB/gfzrnx.
- Ajuste por mínimos cuadrados propio (numpy), con pruebas contra casos resueltos a mano.
- **Validación de punta a punta:** procesar una estación de la RGNA como si fuera rover contra otras dos y comparar con
  su coordenada oficial; el error esperado y el obtenido quedan en `docs/validacion.md`.

## 5. Informes (PDF, ReportLab + matplotlib, letra del sistema)

Encabezado con el logo y los datos del usuario (empresa, responsable, cédula), número de proyecto, fecha y versión
de la app. Todo en español y con unidades.

### 5.1 Informe de línea base (estático)

Resumen (coordenada final en el sistema elegido y en ITRF2008 2010.0, σ H/V, solución, aprobado/con avisos);
proyecto y sistema; ajustes del proceso; por línea base: estaciones, antenas y alturas, sesión (inicio, fin, épocas,
intervalo), satélites usados, vector ΔX ΔY ΔZ y ΔE ΔN ΔU, longitud, acimut y distancia cenital, solución y cociente,
RMS, σ y matriz de covarianza, efemérides; ajuste de red (si hay): residuos, chi², σ a priori/posteriori; calidad de los
datos; gráficas; bitácora en anexo.

### 5.2 Informe de PPK de la jornada

Resumen (puntos FIJOS/FLOTANTES/sin solución), base y su origen, trayectoria, tabla de puntos (nombre, coordenadas, σ,
épocas, solución, diferencia con campo), avisos (huecos, puntos fuera de la trayectoria), gráficas y anexo.

## 6. Interfaz

Ventana principal con barra lateral (estilo TresVizo Med): **Inicio** (proyectos recientes, nuevo), **Georreferenciar
punto**, **PPK de puntos**, **Sistemas y geoides**, **Estaciones RGNA**, **Ajustes** (datos del usuario y logo,
unidades, carpeta de datos). Cada flujo es un asistente por pasos con arrastrar y soltar, validación al momento,
progreso con cancelar, resultado en pantalla con gráficas y botones «Ver informe» y «Exportar». Accesible con
teclado, contraste verificado, ventanas que se acomodan a pantallas chicas.

## 7. Calidad

Pruebas unitarias del motor, de los sistemas, del ajuste, de la unión de RINEX, del CSV y del informe (que se arme y
diga lo que tiene que decir); pruebas de punta a punta con datos públicos pequeños; CI en GitHub Actions (gratis en
repo público) en Windows, macOS y Linux. `docs/estado.md` dice qué está verificado y qué no.

## 8. Funciones entre módulos (la interfaz solo llama a estas)

Progreso: `progress(fraccion: float, mensaje: str)`; cancelar: un `threading.Event`. Los errores que ve el usuario son
`tresvizo_ppk.errors.UserError(mensaje_en_español)`; lo demás es un error de programa.

**Motor, `tresvizo_ppk/core/api.py`:**
- `prepare_observations(paths, workdir, *, pole_height_m=None, marker=None, progress=None, cancel=None) -> ObservationSet`
  (grabación `.bin`/`.part` del Meridian o RINEX en cualquier forma; une, descomprime y recorta)
- `assess_quality(obs) -> DataQuality`
- `rgna_catalog() -> list[Station]`, `find_station(marker) -> Station | None`
- `download_rgna(station_id, start, end, workdir, progress=None, cancel=None) -> list[Path]` (si §3.2 lo permite)
- `download_precise_products(start, end, workdir, progress=None, cancel=None) -> list[Path]`
- `process_static(rover, bases: list[tuple[Station, ObservationSet]], options, workdir, progress=None, cancel=None) -> StaticResult`
- `read_field_csv(path) -> list[FieldPoint]`
- `process_points(rover, base: tuple[Station, ObservationSet], points, options, workdir, progress=None, cancel=None) -> PointsResult`
- Línea de órdenes: `python -m tresvizo_ppk estatico …` y `python -m tresvizo_ppk puntos …`.

**Sistemas, `tresvizo_ppk/crs/api.py`:** `catalog()` (instalados y descargables), `download_index(progress, cancel)`,
`install(item_id, progress, cancel)`, `transform(position: Ecef, choice: CoordinateChoice) -> TransformedCoordinate`
(dataclass nueva en `model.py`: latitud, longitud, altura elipsoidal, norte, este, altura ortométrica, ondulación,
zona, convergencia, factor de escala y los nombres para el informe) y `transform_sigma(...)` para pasar σ al sistema.

**Informe, `tresvizo_ppk/report/api.py`:** `static_report(result, project, user, out_pdf) -> Path` y
`points_report(result, project, user, out_pdf) -> Path`.

**Exportar, `tresvizo_ppk/export/api.py`:** `export_points(result, choice, formato, out) -> Path` con formato
`field_csv`, `csv`, `geojson` o `kml`.

Fuentes que hay que leer (del propietario, mismos repos de siempre):
- CSV de TresVizo Field: `~/Documents/TresVizoField/TresVizoField/docs/csv-export.md` (y el lector de Android).
- Sistemas y geoides: `~/Documents/TresVizoField/TresVizoField/docs/sistemas-coordenadas.md`, `tools/crs/`,
  `tools/geoides/` (formato `.mvgeoid`, índice firmado `indice.py`/`ed25519.py`) y la llave pública en
  `TresVizo Field/Packages/MeridianVCore/Sources/MeridianVCore/DataIndex/DataIndex.swift`.
- Grabación a RINEX: `~/Documents/MeridianV/tools/rinex/` (se incluye en `tresvizo_ppk/core/meridian_rinex/` con la
  nota de dónde sale y cómo se sincroniza).
- Estilo: `cadaritre/TresVizo-Med` (público, MIT): `app/themes.py`, `app/widgets.py`, `app/icons.py`, `docs/estilo.md`,
  `scripts/generate_msi.py`.
