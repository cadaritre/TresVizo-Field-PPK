# Validación de punta a punta del motor (§4)

**Fecha de la prueba:** 26-10-2026. **Datos:** RGNA del INEGI, 20-10-2026 (día 293), RINEX 3.04 cada 15 s tal
como los entrega la página «Archivos RINEX de la RGNA» (ZIP horarios). **Motor:** RTKLIB-EX 2.5.1 compilado por
`scripts/build_rtklib.py` (macOS arm64), estrategia «auto», máscara 15°, GPS + GLONASS + Galileo + BeiDou + QZSS,
L1+L2, combinado adelante/atrás. Reproducir: `python scripts/validacion_rgna.py --datos … --precisas …` (ver su
ayuda); los datos están fuera del repositorio (`~/Documents/TresVizo-Field-PPK-datos/`).

## Diseño

Una estación de la RGNA hace de rover y otras de bases; cada sesión se procesa con `process_static`, igual que en
la app. Hay dos comparaciones, porque las estaciones **se han movido desde 2010** y la coordenada oficial
(ITRF2008, época 2010.0) ya no es su posición del día:

1. **Error del proceso:** el vector rover−base de la línea base contra el de una referencia independiente del mismo
   día: PPP estático de 24 h de RTKLIB en cada estación con órbitas y relojes finales de la ESA (IGS20). La
   referencia vale ±1–2 cm (los errores comunes de los PPP se cancelan en la diferencia).
2. **Lo que ve el usuario:** la coordenada final del rover contra su coordenada oficial. Incluye el movimiento
   relativo de las estaciones desde 2010, que no es error del proceso.

Movimiento de cada estación del día respecto de su coordenada oficial (PPP − oficial, mm E, N, U; la parte común
de unos −15 cm E y −9 cm N es el desplazamiento de la placa y el cambio de marco ITRF2008→IGS20):

| UGTO | UQRO | ISLP | INEG | ITLA | ICEP |
| --- | --- | --- | --- | --- | --- |
| −152.9, −96.1, +58.9 | −138.5, −94.7, +18.8 | −158.5, −83.1, +5.8 | −177.8, −65.5, **−680.7** | −118.3, −57.4, +50.8 | −82.6, −81.6, −48.7 |

**INEG (Aguascalientes) está 0.74 m más abajo** que su coordenada de 2010.0 respecto de las demás: es el hundimiento
del valle de Aguascalientes por extracción de agua (documentado hasta −15 cm/año en el valle, MDPI Remote Sensing
13(23):4800). El paquete de la RGNA lleva el aviso en INEG y el ajuste la detecta (abajo).

## 1. Líneas largas: UGTO (rover) con UQRO 100.4 km, ISLP 129.2 km e INEG 141.3 km

Estrategia «auto» para ≥ 60 km: libre de ionosfera, ambigüedades flotantes, troposfera estimada. Cuatro sesiones
por duración (00, 06, 12 y 18 h UTC; una de 24 h), tres líneas cada una. Error del vector frente a la referencia
PPP, en mm:

| Sesión | Efemérides | Líneas | RMS E | RMS N | RMS U | Máx. horizontal | Máx. vertical |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 h | difundidas | 12 | 21.2 | 7.9 | 31.5 | 34.9 | 56.5 |
| 1 h | precisas ESA | 12 | 19.8 | 8.3 | 32.9 | 36.7 | 59.1 |
| 2 h | difundidas | 12 | 14.0 | 6.2 | 14.1 | 33.6 | 32.2 |
| 2 h | precisas ESA | 12 | 13.6 | 5.9 | 16.0 | 32.0 | 34.4 |
| 4 h | difundidas | 12 | 8.5 | 3.6 | 6.7 | 14.0 | 11.0 |
| 4 h | precisas ESA | 12 | 7.7 | 3.9 | 5.4 | 13.0 | 9.8 |
| 24 h | difundidas | 3 | 5.1 | 2.2 | 5.5 | 6.3 | 6.3 |
| 24 h | precisas ESA | 3 | 4.9 | 2.4 | 5.0 | 5.8 | 7.1 |

- El modelo de error a priori de `baseline.py` (flotante: 3 mm + 0.02 ppm + 18 mm/√h en horizontal, 5 mm + 0.05 ppm
  + 30 mm/√h en vertical) se calibró con estas cifras: queda del orden del RMS observado o algo por encima.
- A 100–140 km las órbitas precisas apenas cambian el resultado (≤ 2 mm de RMS): el error lo dominan la ionosfera
  residual, la troposfera y la duración de la sesión.

**Ajuste con las tres bases (lo que ve el usuario):** en las 26 sesiones la prueba chi² falla (χ² de 118 a 999 con
límite 12.59) y la prueba w señala **INEG** cada vez; con la regla del motor (con tres bases y una sola línea
marcada, se deja fuera si sin ella el ajuste pasa) el resultado es el de UQRO + ISLP y el usuario recibe el aviso
con el residuo de INEG. Comprobado de nuevo con esa regla (13 sesiones, difundidas): INEG fuera en todas y la
coordenada final idéntica a la de la tabla de abajo. Sin la regla, quedaba unos 25 cm alta.

**Ajuste con UQRO + ISLP:** pasa en las 13 sesiones (χ² de 0.6 a 3.7, límite 7.81). Coordenada final de UGTO menos
la oficial (mm E, N, U) y σ del ajuste:

| Sesión | Final − oficial | σ |
| --- | --- | --- |
| 1 h (4 sesiones) | (−7.7, −5.8, +25.3) (−29.1, −11.5, −6.6) (−4.5, −0.7, +34.2) (+16.1, −0.4, +55.6) | 19–23, 17–18, 33–36 |
| 2 h (4 sesiones) | (+0.8, −5.9, +33.1) (−19.0, −12.1, +55.8) (−15.5, −3.5, +38.3) (+13.4, −1.6, +59.6) | 14–17, 13–15, 25–28 |
| 4 h (4 sesiones) | (−3.1, −8.3, +43.3) (−13.2, −6.4, +40.7) (−13.5, −5.5, +39.9) (−11.8, −2.0, +36.2) | 11, 10, 19–20 |
| 24 h | (−11.4, −6.0, +39.3) | 7, 7, 14 |

**Esperado** por el movimiento de las estaciones (UGTO respecto de la media de UQRO e ISLP, de la tabla de arriba):
(−4.4, −7.2, +46.6) mm. Con 4 h o más el resultado cae a ≤ 1 cm de lo esperado en horizontal y ≤ 1.1 cm en
vertical; con 1–2 h, a ≤ 2.5 cm en horizontal y ≤ 5.5 cm en vertical, siempre dentro de 1.6σ del ajuste.

## 2. Línea media: ITLA (rover) con ICEP, 30.7 km

Estrategia «auto» para 20–60 km: ionosfera difundida, troposfera estimada y ambigüedades enteras (GPS, Galileo,
QZSS), aceptada solo si fija la mitad de las épocas y coincide con la libre de ionosfera; si no, libre de
ionosfera. Error del vector frente a la referencia PPP (mm E, N, U):

| Sesión | 00 h | 06 h | 12 h | 18 h |
| --- | --- | --- | --- | --- |
| 1 h | flotante (+2.0, +11.2, −53.3) | fija (+3.2, −9.4, −15.5) | fija (+0.7, −12.9, −11.1) | flotante (−66.2, −13.1, −30.9) |
| 2 h | flotante (−1.9, +7.1, −12.8) | fija (+2.4, −7.9, −24.2) | fija (+0.1, −12.1, −21.6) | flotante (−10.7, −9.4, −6.6) |
| 4 h | fija (+11.7, −15.0, −15.7) | fija (+4.3, −7.2, −18.5) | fija 91 % (−1.8, −13.4, +3.2) | flotante (−7.7, −8.1, −4.2) |
| 24 h | fija 99 % (+3.7, −20.9, −3.7) | | | |

(Difundidas; con las precisas de la ESA las cifras cambian ≤ 5 mm salvo 2 h 00 h, que fijó: (+2.8, −18.2, +20.3).)
La sesión de 18 h UTC (mediodía local, ionosfera máxima) no fija en 1–4 h. Todas las fijas quedan dentro de 2 cm
en horizontal; en el norte hay un sesgo común de ~1 cm (también en las flotantes) que puede ser de la referencia PPP.

## 3. Lo que se probó para llegar aquí (y por qué la estrategia es esta)

- **Ionosfera estimada de RTKLIB (`est-stec`):** no fijó **ninguna** época ni a 30 km ni a 100 km, ni con 24 h
  (cociente 1.0 constante). Queda como opción «estimated», fuera de «auto».
- **Ambigüedades de GLONASS y BeiDou:** con GPS + GLONASS + Galileo + BeiDou juntas y la resolución de GLONASS
  encendida, la línea de 30 km no fijaba (cociente ≈ 1); cada sistema solo sí fijaba. Con GLONASS y BeiDou en
  flotante (siguen aportando a la posición) fija el 100 %. Por eso `gloarmode` y `bdsarmode` van apagados.
- **rnx2rtkp lee como mucho 16 archivos de entrada** y descarta el resto sin avisar: con 24 navegaciones horarias
  usaba efemérides viejas. La navegación y las órbitas se le pasan ahora con comodín (sin límite).
- Ionosfera difundida a 100 km: fija parte de las épocas, pero no se puede validar contra la libre de ionosfera con
  la precisión necesaria; por eso ≥ 60 km va siempre libre de ionosfera.

## 4. Pruebas automáticas con estos datos

`tests/core/test_e2e.py` repite una versión corta (20 min de ITLA–ICEP, incluidos en `tests/core/data/`) en cada
corrida de pruebas: estático con una base (error contra la referencia del día dentro de 3σ), PPK de puntos por la
línea de órdenes y conversión de una grabación del MeridianV. `tests/core/test_rinex.py` comprueba que RTKLIB da
exactamente la misma solución con los archivos horarios sueltos que con el archivo unido por el motor.

## Lo que esta validación no cubre

- **Ninguna grabación real de un MeridianV o MeridianVI** ha pasado por el motor (no hay muestra): la conversión se
  probó con las tramas construidas del convertidor y el PPK de puntos con una estación de la RGNA como rover.
  Falta una jornada real con el CSV de TresVizo Field y su base.
- Un solo día de datos, en el centro de México; ninguna línea de menos de 30 km (la RGNA no tiene pares más cercanos
  con datos), así que la estrategia «corta» (< 20 km) solo se probó en el sentido de que fija al 100 % a 30 km.
- La referencia PPP es de RTKLIB (flotante): una referencia de otro programa (CSRS-PPP, GIPSY) pide cuenta.
