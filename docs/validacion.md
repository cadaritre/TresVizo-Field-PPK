# Validación de punta a punta del motor (§4)

**Fecha de la prueba:** 26-10-2026. **Datos:** RGNA del INEGI, 20-10-2026 (día 293), RINEX 3.04 cada 15 s tal
como los entrega la página «Archivos RINEX de la RGNA» (ZIP horarios); órbitas finales de la ESA (SP3 de los días
292–294 y relojes de 30 s del 293). **Motor:** desde 20 km, el **procesador propio de líneas largas**
(`tresvizo_ppk/core/larga`: libre de ionosfera de GPS, Galileo y BeiDou, troposfera estimada con gradientes,
ambigüedades de ancho y estrecho de carril con LAMBDA); por debajo, y como comprobación independiente, RTKLIB-EX 2.5.1
compilado por `scripts/build_rtklib.py` (macOS arm64). Estrategia «auto», máscara 15°. Reproducir:
`python scripts/validacion_rgna.py --datos … --precisas … --salida …` y `--resumen …/validacion.json` para las tablas
(ver su ayuda); los datos están fuera del repositorio (`~/Documents/TresVizo-Field-PPK-datos/`).

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

## 1. Líneas largas con el procesador propio: UGTO (rover) con UQRO 100.4 km, ISLP 129.2 km e INEG 141.3 km

Cuatro sesiones por duración (00, 06, 12 y 18 h UTC; una de 24 h), tres líneas cada una, con efemérides difundidas y
precisas, procesadas con `process_static` como en la app. Error del vector frente a la referencia PPP del día, en mm
(RMS por componente y máximos). **Fijo erróneo:** línea fija con algún componente del error mayor que 3 veces su σ
(la σ que da el motor y sale en el informe).

| Línea | km | Sesión | Efemérides | Fijas | RMS E | RMS N | RMS U | Máx. horiz. | Máx. vert. | Fijos erróneos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| UGTO–UQRO | 100.4 | 1 h | difundidas | 4/4 | 4.9 | 7.1 | 7.5 | 10.6 | 12.8 | 0 |
| UGTO–UQRO | 100.4 | 1 h | precisas | 4/4 | 4.0 | 6.4 | 9.6 | 10.3 | 15.9 | 0 |
| UGTO–UQRO | 100.4 | 2 h | difundidas | 4/4 | 8.4 | 7.5 | 12.5 | 13.3 | 23.7 | 0 |
| UGTO–UQRO | 100.4 | 2 h | precisas | 4/4 | 7.1 | 6.3 | 5.9 | 12.1 | 11.2 | 0 |
| UGTO–UQRO | 100.4 | 4 h | difundidas | 4/4 | 6.5 | 5.7 | 7.6 | 11.6 | 8.6 | 0 |
| UGTO–UQRO | 100.4 | 4 h | precisas | 4/4 | 5.1 | 4.3 | 4.7 | 9.7 | 8.5 | 0 |
| UGTO–UQRO | 100.4 | 24 h | difundidas | 1/1 | 4.4 | 3.9 | 0.0 | 5.9 | 0.0 | 0 |
| UGTO–UQRO | 100.4 | 24 h | precisas | 1/1 | 4.0 | 2.8 | 2.0 | 4.9 | 2.0 | 0 |
| UGTO–ISLP | 129.2 | 1 h | difundidas | 4/4 | 12.4 | 6.7 | 15.9 | 22.4 | 20.7 | 0 |
| UGTO–ISLP | 129.2 | 1 h | precisas | 4/4 | 5.8 | 5.5 | 6.4 | 10.6 | 12.2 | 0 |
| UGTO–ISLP | 129.2 | 2 h | difundidas | 4/4 | 9.4 | 5.8 | 10.6 | 16.6 | 18.0 | 0 |
| UGTO–ISLP | 129.2 | 2 h | precisas | 4/4 | 6.0 | 4.7 | 3.9 | 10.9 | 5.8 | 0 |
| UGTO–ISLP | 129.2 | 4 h | difundidas | 4/4 | 6.1 | 3.3 | 7.0 | 10.5 | 12.1 | 0 |
| UGTO–ISLP | 129.2 | 4 h | precisas | 4/4 | 3.6 | 3.2 | 4.9 | 7.6 | 7.4 | 0 |
| UGTO–ISLP | 129.2 | 24 h | difundidas | 1/1 | 3.5 | 2.1 | 3.0 | 4.1 | 3.0 | 0 |
| UGTO–ISLP | 129.2 | 24 h | precisas | 1/1 | 3.0 | 2.1 | 4.8 | 3.7 | 4.8 | 0 |
| UGTO–INEG | 141.3 | 1 h | difundidas | 4/4 | 8.7 | 8.3 | 21.6 | 15.7 | 34.1 | 0 |
| UGTO–INEG | 141.3 | 1 h | precisas | 4/4 | 4.7 | 9.0 | 15.3 | 16.1 | 21.2 | 0 |
| UGTO–INEG | 141.3 | 2 h | difundidas | 4/4 | 7.7 | 7.8 | 10.8 | 12.9 | 20.3 | 0 |
| UGTO–INEG | 141.3 | 2 h | precisas | 4/4 | 6.1 | 8.7 | 11.8 | 13.6 | 13.4 | 0 |
| UGTO–INEG | 141.3 | 4 h | difundidas | 4/4 | 4.8 | 6.6 | 9.0 | 9.2 | 11.9 | 0 |
| UGTO–INEG | 141.3 | 4 h | precisas | 4/4 | 2.5 | 7.9 | 11.5 | 10.6 | 14.5 | 0 |
| UGTO–INEG | 141.3 | 24 h | difundidas | 1/1 | 0.6 | 5.5 | 8.0 | 5.5 | 8.0 | 0 |
| UGTO–INEG | 141.3 | 24 h | precisas | 1/1 | 0.7 | 6.8 | 9.4 | 6.8 | 9.4 | 0 |

- **Fijó las 78 líneas de 1 h o más, sin ningún fijo erróneo.** Con 1 h fija de 13 a 23 combinaciones enteras de
  15–28 dobles diferencias (razón 3.5–25); con 4 h, de 22 a 39 de 29–50; con 24 h, de 83 a 115 de 115–167; tasa de
  éxito del bootstrapping ≥ 99.90 % en todas.
- La σ que da el motor es realista: en todas las fijas de 15 min a 4 h (128) el error dividido entre su σ tiene RMS
  0.85 / 0.91 / 0.99 (E / N / U) y máximo 2.3 / 2.0 / 2.8; en las flotantes (184), RMS 1.14 / 0.75 / 1.03 y máximo
  2.96 / 1.95 / 2.59. σ típica de una fija de 1 h a 100–141 km: 7–10 mm en horizontal y 12–16 mm en vertical (difundidas)
  o 7–9 y 12–13 mm (precisas); con 4 h, 5–7 y 8–11 mm.
- Parte del error que queda es de la referencia (el vector de los PPP vale ±1–2 cm, ver «Diseño»): la fija de 24 h
  queda a 3–10 mm de él y el sesgo de 5–9 mm en el norte de UGTO–INEG está también en la de 24 h. Contra la propia solución fija de 24 h, las de 1 h
  repiten a 3–12 mm en horizontal.
- A 100–141 km las órbitas precisas sí importan con sesiones cortas (1 h a 129 km: 12 → 6 mm de RMS en el este).

**Lo que ve el usuario (ajuste con las tres bases).** INEG (0.74 m más abajo que su coordenada de 2010.0) se señala y
se deja fuera en las **50** sesiones (incluidas las cortas de la §3). Coordenada final de UGTO menos la oficial, mm E,
N, U, frente a lo esperado por el movimiento de las estaciones, (−4.4, −7.2, +46.6):

| Sesión | Final − oficial (difundidas) | Final − oficial (precisas) | σ |
| --- | --- | --- | --- |
| 1 h, 00 / 06 / 12 / 18 h | (−13.8, −1.3, +29.2) (−14.1, −7.1, +45.7) (−8.3, +5.2, +35.2) (−6.2, −2.1, +35.2) | (−9.9, −3.5, +34.8) (−13.9, −5.4, +49.3) (−6.6, +4.3, +37.4) (−7.8, −2.8, +43.2) | 7–10, 7–10, 11–17 |
| 2 h | (−10.0, +2.2, +28.3) (−17.7, −3.9, +48.8) (−9.9, +2.9, +42.9) (−10.8, −1.2, +43.1) | (−8.4, −0.5, +39.3) (−17.5, −5.2, +43.2) (−8.3, +1.6, +44.4) (−10.8, −1.4, +46.7) | 7–10, 7–10, 11–17 |
| 4 h | (−9.4, −1.2, +42.0) (−14.6, −2.5, +50.9) (−8.2, −0.4, +41.1) (−9.7, −2.8, +42.1) | (−7.5, −2.8, +42.6) (−13.9, −3.0, +46.4) (−7.0, −1.6, +44.1) (−8.5, −3.5, +42.4) | 7–9, 7–9, 11–14 |
| 24 h | (−9.8, −3.1, +43.0) | (−8.9, −4.0, +43.4) | 7, 7, 11 |

Con 1–2 h el resultado cae a ≤ 1.4 cm de lo esperado en horizontal y ≤ 1.8 cm en vertical, y con 4 h a ≤ 1.1 cm y
≤ 0.6 cm (antes, con RTKLIB flotante, 1–2 h daban hasta 2.5 y 5.5 cm, y ≤ 1 / 1.1 cm pedía 4 h o más). Desde 2 h la prueba χ² del ajuste sin INEG suele fallar (χ² de 7.9 a 21 con límite 7.81):
las líneas fijas miden a milímetros y las coordenadas oficiales de UQRO e ISLP difieren entre sí 2.0 / −1.2 / 1.3 cm
por su movimiento desde 2010.0. El motor deja fuera a INEG igual (lectura de datos de Baarda: se quita la línea de
mayor w y se vuelve a ajustar), avisa de esa diferencia y escala la σ del resultado con σ0. Con la regla anterior (dejar
fuera solo si el ajuste sin ella pasa la χ²) INEG volvía a entrar con 2 h o más y el resultado quedaba 20–25 cm alto.

## 2. Línea media de control: ITLA (rover) con ICEP, 30.7 km

| Sesión | Efemérides | Fijas | RMS E | RMS N | RMS U | Máx. horiz. | Máx. vert. | Fijos erróneos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 h | difundidas | 4/4 | 3.5 | 5.1 | 9.8 | 9.2 | 14.0 | 0 |
| 1 h | precisas | 4/4 | 2.4 | 5.0 | 9.5 | 8.8 | 16.5 | 0 |
| 2 h | difundidas | 4/4 | 4.8 | 4.9 | 7.3 | 9.1 | 8.9 | 0 |
| 2 h | precisas | 4/4 | 2.9 | 4.7 | 3.4 | 7.7 | 5.3 | 0 |
| 4 h | difundidas | 4/4 | 3.5 | 4.1 | 7.3 | 6.2 | 11.9 | 0 |
| 4 h | precisas | 4/4 | 2.6 | 4.0 | 8.3 | 6.2 | 15.1 | 0 |
| 24 h | difundidas | 1/1 | 1.6 | 2.3 | 2.0 | 2.8 | 2.0 | 0 |
| 24 h | precisas | 1/1 | 1.4 | 2.5 | 2.1 | 2.9 | 2.1 | 0 |

**RTKLIB o el propio a 20–60 km (decidido con estos datos):** con RTKLIB (estrategia «media» de antes, abajo en la §5)
fijaba 2 de 4 sesiones de 1 h y 3 de 4 de 4 h, con hasta 2.4 cm de error en la vertical, y la de 18 h UTC no fijaba
nunca; el propio fija las 26 de 30 min o más (§3) con ≤ 1.1 cm en horizontal. Con 15 min, la fija de RTKLIB dio un
fijo erróneo (12.5 cm en la vertical, 3.3σ) donde el propio quedó flotante. Por eso desde 20 km decide el propio y
RTKLIB solo corre su libre de ionosfera como comprobación independiente (y para las gráficas de datos del informe).
Por debajo de 20 km sigue RTKLIB con L1/L2 y la ionosfera del mensaje: a esa distancia la libre de ionosfera triplica
el ruido y no hay pares de la RGNA para comparar (ver «Lo que esta validación no cubre»).

## 3. Sesiones cortas y duración mínima para fijar

Ocho sesiones por duración repartidas en el día (00, 03, …, 21 h UTC), efemérides difundidas. Antes de fijar las
líneas cortas, el motor exige 30 min por debajo de 60 km y 1 h desde ahí (`larga.procesador.MIN_FIX_HOURS`); con menos
la línea queda flotante y el informe dice por qué y cuánto observar.

| Línea | Sesión | Fijas | RMS E | RMS N | RMS U | Máx. horiz. | Máx. vert. | Fijos erróneos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ITLA–ICEP 30.7 km | 15 min | 0/8 (corta) | 88.9 | 18.0 | 96.3 | 121.3 | 194.4 | — |
| ITLA–ICEP 30.7 km | 30 min | 8/8 | 4.6 | 5.0 | 15.2 | 11.3 | 30.3 | 0 |
| UGTO–UQRO 100.4 km | 30 min | 0/8 (corta) | 35.7 | 16.5 | 57.4 | 81.0 | 97.5 | — |
| UGTO–UQRO 100.4 km | 45 min | 0/8 (corta) | 20.4 | 14.4 | 29.2 | 41.1 | 42.1 | — |
| UGTO–UQRO 100.4 km | 1 h | 8/8 | 4.0 | 5.7 | 14.4 | 10.6 | 31.2 | 0 |
| UGTO–ISLP 129.2 km | 30 min | 0/8 (corta) | 34.2 | 18.5 | 48.6 | 65.8 | 109.7 | — |
| UGTO–ISLP 129.2 km | 45 min | 0/8 (corta) | 17.9 | 13.3 | 24.1 | 38.5 | 36.4 | — |
| UGTO–ISLP 129.2 km | 1 h | 8/8 | 10.0 | 5.1 | 19.8 | 22.4 | 27.0 | 0 |
| UGTO–INEG 141.3 km | 30 min | 0/8 (corta) | 43.0 | 18.1 | 31.2 | 74.4 | 71.8 | — |
| UGTO–INEG 141.3 km | 45 min | 0/8 (corta) | 25.7 | 14.8 | 35.8 | 50.3 | 58.9 | — |
| UGTO–INEG 141.3 km | 1 h | 8/8 | 7.7 | 7.1 | 18.1 | 15.7 | 34.1 | 0 |

Por qué esos mínimos: sin el mínimo, con 30 min a 100–141 km el propio «fijaba» 23 de 24 sesiones pero 2 quedaban a
más de 3σ (hasta 7 cm en la vertical: con media hora la troposfera y la altura no se separan y las flotantes traen
sesgos que la covarianza no ve); con 45 min, 1 de 23 a 3.1σ (4.9 cm en la vertical); con 15 min a 30 km, 1 de 8 a
3.4σ. Con los mínimos,
ninguna. Las flotantes de 30–45 min a 100–141 km quedan con 2–4 cm en horizontal y su σ lo dice (RMS del error/σ ≤ 1.1).

## 4. Cómo se calibró (y lo que se probó para llegar aquí)

- **Covarianza de las flotantes del carril estrecho.** La formal del ajuste (escalada por σ0² y por la correlación
  temporal de los residuos, modelo AR(1), factor 5–8 con datos cada 15 s) se queda corta: con 24 h, las partes
  fraccionarias de N1 tienen RMS de 0.05–0.07 ciclos a 30–140 km contra una σ formal de 0.02. Son sesgos por arco
  (troposfera residual a baja elevación, multitrayectoria), no ruido: con máscara de 25° bajan a la mitad; no cambian
  al dar menos peso al código (σ de 3 m) ni con nodos de troposfera cada 30 min; los gradientes los bajan 15 %. Se
  modelan como un error por arco de (1 mm + 3 mm·L/100 km)·(1/sen e media) (`BIAS_B0_M`, `BIAS_B1_M`), correlacionado
  a través del arco de referencia. Sin él la prueba de razón no pasaba ni con 24 h (≈ 1.1–1.3); con él fija desde 1 h.
- **Ancho de carril:** las partes fraccionarias de la Melbourne-Wübbena por arco tienen RMS 0.06 ciclos contra una σ
  formal (con AR(1)) de 0.03: la σ se multiplica por 2 (`WL_SIGMA_FACTOR`) y se fija con probabilidad de redondeo
  ≥ 99.9 %.
- **Resolución parcial:** con covarianza realista y 100 ambigüedades, la razón de la solución completa es baja por la
  dimensión (cada ambigüedad aporta su χ²): se fija el subconjunto decorrelacionado más grande con tasa de éxito
  ≥ 99.9 % y, si no pasa la razón (3) o la distancia entera no cuadra con su covarianza (χ² al 99.9 %), se quita la
  combinación menos precisa y se repite.
- **σ realista del resultado** (`ERROR_FLOOR`): formal más σ = a + b·(L/100 km)/√T + c·(L/100 km) con difundidas,
  ajustado con estas sesiones para RMS(error/σ) ≤ 1 y ningún componente a más de 3σ: fija (3 + 4/√T + 1) mm en
  horizontal y (4 + 6/√T + 2) mm en vertical a 100 km; flotante (6 + 5.5/√T + 1) y (5 + 7.5/√T + 2.5) mm.
- **Lo de antes (RTKLIB) sigue valiendo:** su ionosfera estimada (`est-stec`) no fijó nunca ni a 30 ni a 100 km;
  ambigüedades de GLONASS y BeiDou en RTKLIB impedían fijar a 30 km; rnx2rtkp lee como mucho 16 archivos y descarta el
  resto sin avisar (la navegación y las órbitas van con comodín). En el propio, GLONASS no entra (FDMA) y BeiDou-2 y
  BeiDou-3 van en grupos aparte, sin los geoestacionarios.
- **Velocidad:** una sesión con tres bases tarda 4–5 s con 1 h, 7–8 s con 2 h, 13–17 s con 4 h y 1 min con 24 h
  (macOS arm64), con la comprobación de RTKLIB incluida.

## 5. Antes: RTKLIB solo (referencia de la mejora)

Así quedaba con RTKLIB (libre de ionosfera flotante desde 60 km, «media» de 20 a 60 km), con los mismos datos; es la
referencia para lo que gana el procesador propio.

### Líneas largas con RTKLIB (flotante)

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

### Línea media con RTKLIB

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

## 6. Pruebas automáticas con estos datos

`tests/core/test_e2e.py` repite una versión corta (20 min de ITLA–ICEP, incluidos en `tests/core/data/`) en cada
corrida de pruebas: estático con una base (error contra la referencia del día dentro de 3σ), PPK de puntos por la
línea de órdenes y conversión de una grabación del MeridianV. `tests/core/test_rinex.py` comprueba que RTKLIB da
exactamente la misma solución con los archivos horarios sueltos que con el archivo unido por el motor.
`tests/core/larga/` prueba el procesador propio con 1 h de UGTO–UQRO cada 30 s (recortes en `tests/core/larga/datos`):
fija a 100 km dentro de 3σ de la referencia del día, queda flotante con una sesión de 40 min y lo dice, y por el
estático completo (`test_integracion.py`) la línea sale FIJA con su resolución de ambigüedades y la comprobación de
RTKLIB. Los modelos se prueban contra los casos publicados (GMF y mareas del IERS, LAMBDA de RTKLIB y de Teunissen).

## 7. Verificación fuera de muestra (otros días y otras estaciones)

**Fecha:** 27-10-2026. Auditoría independiente: el motor **sin cambios** (`process_static`, estrategia «auto», máscara
15°, umbrales de la §3 y la §4) con datos que no se usaron para calibrarlo. **Datos:** RGNA del INEGI de dos días
lejanos al 20-10, bajados de la página pública «Archivos RINEX de la RGNA» (ZIP horarios, RINEX 3.04 cada 15 s; IMIE
solo se entrega en RINEX 2.10): **15-07-2026 (día 196, temporada de lluvias)** y **18-02-2026 (día 049, temporada
seca)**; órbitas y relojes finales de la ESA de los días 195–197 y 048–050. Referencia: PPP de 24 h de RTKLIB como en
el «Diseño». Además de las tres líneas de la §1, 15 líneas nuevas: ITLA–ICEP 30.7, ITLA–IHID 104.7, INEG–IZAC 107.6
(540 m de desnivel), TOL2–IHID 131.5, ICEP–IHID 133.0, INEG–ISLP 134.9, VIL2–ICHS 137.8 (607 m, trópico húmedo),
ITLA–TOL2 150.1, ICEP–TOL2 155.9, MERI–ICAM 156.6 (costa de Yucatán), TOL2–UQRO 164.8 (834 m), ISLP–IZAC 178.8,
ISLP–UQRO 183.0, TAMP–ICVT 211.5 (costa), ICEP–UVER 218.7 (2147 m de desnivel, costa) e INEG–UQRO 239.6 km.
Sesiones de 15 min a 24 h repartidas en el día (8 o 12 por duración), difundidas y precisas: 2 772 líneas base con
los ataques de abajo.

Reproducir (ver la ayuda del script; `--referencia` guarda el PPP, `--limpiar` borra los intermedios de cada sesión,
`--seguir` deja en el JSON las sesiones que fallan):

    python scripts/validacion_rgna.py --datos DATOS/rgna/20260715 --precisas DATOS/precisas/dia196 \
        --fecha 2026-07-15 --referencia DATOS/validacion/oos_ref_196.json --limpiar --seguir \
        --rover INEG --bases IZAC,ISLP,UQRO --sesiones 1,2,4,24 --ventanas 8 --salida DATOS/validacion/oos196_ineg

### Referencia

- Coherente con el movimiento: INEG baja −632.8 → −650.0 → −680.6 mm respecto de su coordenada de 2010.0 el 18-02,
  el 15-07 y el 20-10 (−4 a −12 cm/año, el hundimiento conocido del valle); las demás se mueven al este −5 a −8 mm en
  ocho meses (placa).
- **Su vertical vale ±1–3 cm, no ±1 cm:** el vector UGTO−UQRO en altura sale 35, 22 y 40 mm los tres días; el PPP de
  24 h cambia 1–3 cm en la altura con la máscara (10° → 15°: IZAC −12 mm, INEG +8, UQRO +10; → 20°: TAMP −32, ICVT
  −22). Por eso, junto al error contra el PPP se da la diferencia contra la fija de 24 h de la misma línea («vs 24 h»),
  que no tiene el error de la referencia.
- Las líneas con **IZAC** quedan 3 cm altas los dos días, igual la fija de 24 h que la flotante (INEG–IZAC +28/+33 mm,
  ISLP–IZAC +40/+28 mm): es de la referencia o del sitio, no de las ambigüedades; se cuentan aparte.

### Resultado (opciones por omisión; fijos erróneos = fija con algún componente a más de 3σ)

| Día | Líneas | Sesión | Fijas | > 3σ contra el PPP (sin IZAC) | Con IZAC | > 3σ vs 24 h |
| --- | --- | --- | --- | --- | --- | --- |
| 15-07 | 30.7 km | 30 min / 45 min / 1 h / 2 h / 4 h | 8/8 · 8/8 · 16/16 · 14/16 · 12/12 | 1 · 0 · 0 · 2 · 0 | — | 1 · 0 · 0 · 0 · 0 |
| 18-02 | 30.7 km | 30 min / 45 min / 1 h / 2 h / 4 h | 7/8 · 8/8 · 16/16 · 16/16 · 12/12 | 0 · 0 · 0 · 0 · 0 | — | 0 |
| 15-07 | 100–141 km (8 líneas) | 15 / 30 / 45 min | 0/36 · 0/84 · 0/84 (cortas) | — | — | — |
| 18-02 | 100–141 km (8 líneas) | 15 / 30 / 45 min | 0/36 · 0/76 · 0/76 (cortas) | — | — | — |
| 15-07 | 100–141 km | 1 h / 2 h / 4 h / 24 h | 166/168 · 168/168 · 108/108 · 18/18 | 3 · 0 · 0 · 0 | 5 · 7 · 8 · 2 | 2 · 0 · 1 · — |
| 18-02 | 100–141 km | 1 h / 2 h / 4 h / 24 h | 150/152 · 152/152 · 96/96 · 16/16 | 2 · 1 · 0 · 0 | 11 · 16 · 12 · 2 | 2 · 2 · 0 · — |
| 15-07 | 150–183 km | 1 h / 2 h / 4 h / 24 h | 78/80 · 80/80 · 60/60 · 10/10 | 0 · 1 · 0 · 0 | 4 · 9 · 9 · 2 | 0 |
| 18-02 | 150–183 km | 1 h / 2 h / 4 h / 24 h | 94/96 · 96/96 · 72/72 · 12/12 | 1 · 2 · 0 · 0 | 0 · 2 · 3 · 1 | 2 · 2 · 0 · — |
| 15-07 | 211–240 km | 1 h / 2 h / 4 h / 24 h | 39/48 · 48/48 · 36/36 · **0/6** | 1 · 1 · 2 · 0 | — | — |
| 18-02 | 211–240 km | 1 h / 2 h / 4 h / 24 h | 28/32 · 28/32 · 17/24 · 2/4 | 0 | — | — |

- **Con 1 h a 100–141 km fijó 316 de 320 sesiones** (las 4 flotantes, por la prueba de razón: ITLA–IHID a las 21 h
  UTC del 15-07 y TOL2–IHID a las 00 h del 18-02). De las fijas, **5 (1.6 %) pasan de 3σ contra el PPP sin contar
  IZAC y 4 contra la fija de 24 h**, todas en la vertical (3.1–3.7σ, 4–7 cm), a las 00 h o 18–20 h UTC; la flotante de
  esas sesiones tiene casi el mismo error (es troposfera, no enteros: ver abajo). Con 2 h o más, 1 de 558 contra el PPP.
- Casos exactos (1 h, sin IZAC): 15-07 UGTO–INEG 18:00 precisas (U −43.2 mm, σ 14.1, 3.1σ; −45.7 contra la de 24 h);
  15-07 ITLA–IHID 00:00 difundidas (U −47.3, σ 14.2; −43.1 contra 24 h); 15-07 TOL2–IHID 00:00 precisas (U −41.6, σ
  13.4); 18-02 ITLA–IHID 00:00 difundidas y precisas (U −61.8 y −51.3, σ 16.5 y 15.0; −71.9 y −61.2 contra 24 h);
  18-02 ITLA–TOL2 (150 km) 00:00 precisas (U −65.8, σ 19.4; −79.7 contra 24 h). Horizontal: 18-02 ICEP–TOL2 00:00
  precisas, E −34.8 mm contra la de 24 h (σ 10.8). A 30 km con 30 min: 15-07 ITLA–ICEP 21:00 difundidas, N +18.9 mm
  (σ 5.6, 3.4σ; +18.5 contra 24 h).
- **Mismo arco, mismo entero:** en ventanas de 1 h desplazadas 10–15 min (UGTO–INEG 15–23 h UTC del 15-07, máscara 15°
  y 30°), las 1 406 parejas de sesiones que comparten un arco con su entero determinado dan el mismo entero de ancho y
  de estrecho de carril (0 distintos). La altura de esas fijas recorre −45 → +55 mm en dos horas con los mismos enteros:
  la troposfera de la tarde de lluvias, que la σ vertical no cubre del todo.
- **σ:** sin IZAC, el error/σ de las fijas tiene RMS 0.77–0.97 (E), 0.47–0.52 (N) y **1.10–1.14 (U)**, y 0.6–1.4 %
  de las verticales pasan de 3σ. **Las flotantes la subestiman en el este:** RMS 1.3–1.6 con 15–45 min (hasta 6σ) y
  2.4 en las de 1 h que no fijaron (ITLA 21 h UTC: 7–9 cm en el este con σ de 1.5–2 cm).
- Desde 200 km la de 24 h casi nunca fija (0 de 6 el 15-07, 2 de 4 el 18-02: «solo 0–1 combinaciones llegan a la tasa
  de éxito»), aunque las de 1–4 h sí; su flotante queda a ≤ 2 cm.
- Velocidad (tres bases, con la comprobación de RTKLIB): 1 h 1–5.5 s, 4 h 4–17 s, 24 h 18–74 s.

### Ataques (UGTO con UQRO, ISLP e INEG, 15-07, difundidas salvo que se diga)

| Prueba | Sesiones | Fijas | > 3σ | Lo que pasó |
| --- | --- | --- | --- | --- |
| Cruza la medianoche (ZIP de dos días unidos, órbitas de 3 días) | 1 y 2 h desde 22:30, 23:00, 23:30 | 36/36 | 0 | Bien |
| Máscara 25° | 1 h y 2 h × 6 | 36/36 | 2 | 1 h 08:00 ISLP U −77 mm (3.6σ); 1 h 16:00 INEG +53 mm (3.1σ) con la flotante a 0 mm |
| **Máscara 30°** | 1 h y 2 h × 6 | 36/36 | 2 | **1 h 16:00 INEG U +136 mm (6.8σ) con la flotante a +1.5 mm**; 1 h 16:00 UQRO −97 mm (4.2σ); con 2 h, 0 |
| Solo GPS | 1 h y 2 h × 6 | 29/36 | 1 | 2 h 08:00 ISLP U −46 mm (3.3σ, 7 de 9 combinaciones) |
| GPS y Galileo | 1 h y 2 h × 6 | 35/36 | 0 | Bien |
| Rover en RINEX 2.11, bases en 3.04 | 1 h y 2 h × 6 | 36/36 | 1 | 1 h 00:00 UQRO U +65 mm (3.7σ) |
| Todo en RINEX 2.11 (UQRO, ISLP) | 1 h y 2 h × 6 | 8/24 | 1 | 2 h 20:00 UQRO E −23 mm (3.1σ) con solo 4 combinaciones; las flotantes, hasta 20 cm |
| Saltos de ciclo sin LLI (4/3 en G14; 4/3 en E07 y E15; 1/1; cinco a la vez) | 1 h y 2 h a las 06:00 | 24/24 | 0 | Todos se ven (arco nuevo); el resultado cambia ≤ 2 mm |
| **Hueco en el rover** (10 min, 30 min o una sola época) | 1 h y 2 h × 6 | — | — | **Las 24 sesiones fallan** con `ValueError` (abajo) |
| Posición aproximada del rover movida 54 m o 470 m, o sin ella (puntual de RTKLIB) | 1 h × 4 | 12/12 | 0 | Igual a ±0.1 mm: no hay fuga de la coordenada del rover |
| Ruta con acentos («validación ñ/José Ñúñez/Vértice 1 – año 2026»), línea de órdenes | 1 h | 2/2 | 0 | Igual a ≤ 1 mm que el script |

### Fallos encontrados (para corregir en el motor)

1. **El procesador de líneas largas se cae si el rover y la base no tienen el mismo número de épocas.**
   `larga/procesador._pick_pair` compara `rov.values[…]` y `bas.values[…]` completos, sin alinearlos a las épocas
   comunes (`ir`, `ib`): `ValueError: operands could not be broadcast together with shapes (239,) (240,)`. Pasa con
   una sola época que falte en un receptor, con un hueco, con una hora que falte en la estación (MERI, 15-07, 12–13 h
   UTC, en la de 24 h) y **con intervalos distintos** (rover cada 15 s y base cada 30 s; un Meridian a 1 Hz contra la
   RGNA a 15 s sería este caso). No es `UserError`: la línea base y el proceso entero fallan. Reproducir:
   `--hueco 30,30.25` en el script, o la línea de órdenes con el rover `UGTO196g_304.zip` y la base UQRO de esa hora
   diezmada a 30 s (`awk` que deja las épocas de segundo 0 y 30).
2. **Con máscara de 25–30° hay fijos claramente erróneos con 1 h** (el de +136 mm, 6.8σ, con razón 10 y tasa de éxito
   99.999 %): con la máscara alta la altura y la troposfera no se separan en una hora y la covarianza no lo ve.
   Reproducir: `--rover UGTO --bases UQRO,ISLP,INEG --sesiones 1 --inicios 2026-07-15T16:00 --efemerides broadcast
   --mascara 30`.
3. **Pocos satélites comunes dan una «flotante» de kilómetros en vez de un error.** MEXI (Alloy, L2C) con IMIE (NetR9,
   RINEX 2.10, solo P2 en L2): el propio deja fuera los satélites con L2C (clases de señal distintas) y procesa con 2
   satélites o ninguna doble diferencia; devuelve flotantes con 14 m a 96 000 km de error (con σ del mismo orden) y el
   aviso solo dice «quedó FLOTANTE»; en otras sesiones RTKLIB no da solución y falla la línea. Hace falta un mínimo de
   satélites y dobles diferencias para entregar una solución.
4. **IMIE no se puede usar como la entrega el INEGI:** sus ZIP traen el almanaque `.26a`, que la lectura rechaza («No
   se reconocen estos archivos») y con él todo el conjunto. Un ZIP horario dañado en el servidor del INEGI
   (`MERI196m_304.zip`, 15-07) también hace fallar toda la estación; podría dejarse fuera esa hora con aviso.
5. El script de validación pasa al motor solo el SP3 con «precisas» (sin el CLK de 30 s), aquí y en la §1: las cifras
   «precisas» son con los relojes del SP3 cada 5 min.

### Recomendación de umbrales (con estos datos)

- **1 h desde 60 km se sostiene para fijar** (316/320 a 100–141 km y 172/176 a 150–183 km), pero no «sin fijos
  erróneos»: 1–2 % de las verticales pasan de 3σ. **Subir la σ vertical de la fija ×1.15** (el piso de
  (4 + 6/√T + 2) a ≈ (4.6 + 6.9/√T + 2.3) mm a 100 km) deja el RMS en 1 y cinco de los seis casos de 1 h dentro de 3σ.
- **σ de la flotante: ×1.6 en el este con 15–45 min y ×2.4 cuando una sesión de 1 h o más queda flotante;** en la
  vertical ×1.2–1.6. La de ahora promete 1–2 cm donde hay 4–9 cm.
- **Máscara alta:** con máscara de más de 20°, pedir 2 h (con 2 h, 0 de 36 fuera de 3σ) o no fijar.
- **Menos de 60 km:** con 30 min, 1 de 16 fuera de 3σ (3.4σ en el norte) y 1 de 16 flotante; con 45 min, 16/16 sin
  ninguno. Pasar el mínimo de 30 a 45 min (de 20 a 30 km sigue sin datos).
- `NL_MIN_FIXED`, la razón y la tasa de éxito: no separan los fijos erróneos (con 4–5, 6–7, 8–9, 10–11 y 12 o más
  combinaciones fijadas pasan de 3σ el 1.8, 2.0, 0, 5.9 y 1.2 %, con razón de 3.2 a 27 y tasa ≥ 99.9 %), ni el
  salto de la fija respecto de la flotante. No conviene tocarlos con estos datos; el remedio es la σ y la máscara.

### Lo que no se pudo probar

- **20–30 km:** la RGNA no tiene pares a esa distancia (la más cercana es ITLA–ICEP, 30.7 km).
- **Receptores mixtos:** el único par posible (MEXI–IMIE) no se procesa (fallos 3 y 4); ningún Meridian.
- **Satélite malo:** en los mensajes de los dos días solo G13 sale enfermo (salud 63, 15-07 desde las 12 h): con
  difundidas no entra; con precisas sí entra con el SP3 de la ESA, sin efecto visible. No se buscó en las notas NANU.
- **Costa y carga oceánica:** MERI–ICAM, TAMP–ICVT e ICEP–UVER fijan con errores como los del interior, pero la
  referencia (PPP sin carga oceánica, vertical ±1–3 cm) no alcanza para medir el efecto de 5–10 mm.

## 8. Arreglos tras la verificación fuera de muestra, y tercer día como prueba final

**Fecha:** 27-10-2026. Lo que se cambió en el motor por los fallos y las recomendaciones de la §7, y el resultado con
los tres días de antes (293, 196 y 049, ahora días de calibración) y con un **tercer día guardado** que no se miró
hasta congelar los umbrales (commit `2b12e3f`).

### Qué se cambió

1. **Épocas desalineadas** (fallo 1): cada receptor se lee diezmado al intervalo de proceso y las épocas se alinean
   en la rejilla común con 20 ms de tolerancia (`larga.procesador.align_epochs`); la geometría se calcula en la
   etiqueta real de cada receptor. Huecos, épocas sueltas, 15 s contra 30 s, 1 Hz contra 15 s y etiquetas corridas
   unos ms dan la misma solución (`tests/core/larga/test_alineacion.py`). La unión de archivos horarios con un hueco
   y RTKLIB no tenían el problema (`tests/core/test_entrega_inegi.py`). La hora 12 que falta en MERI el 15-07 ya no
   tumba la línea: las sesiones que la tocan dicen «solo hay 2 épocas comunes» y las demás se procesan.
2. **Receptores mixtos y datos que no alcanzan** (fallo 3). MEXI (Trimble Alloy) registra L2C en los satélites
   modernos y L2 P(Y) solo en los viejos; IMIE (NetR9, RINEX 2.10) solo P(Y). Antes esos satélites quedaban fuera
   y con 2 satélites o ninguna doble diferencia salían «flotantes» de 14 m a 96 000 km. Decisión, con la práctica
   profesional:
   - **La fase de clases distintas sí sirve:** el desfase de cada clase (el cuarto de ciclo de L2C respecto de
     P(Y), que RINEX 3 declara en `SYS / PHASE SHIFT`) es el mismo en todos los satélites de un receptor, así que se
     cancela si las dobles diferencias se forman dentro de un grupo con las mismas clases. Esos satélites forman un
     grupo aparte.
   - **El código no:** el sesgo de código de cada satélite entre clases (DCB C2W−C2L, del orden de ±1 ns) no se
     cancela entre receptores y sesga la Melbourne-Wübbena unas 0.15 ciclos por ns: su ancho de carril no se fija
     (ambigüedad flotante) y su código entra con ese sesgo como incertidumbre constante por arco
     (`DCB_SIGMA_M` = 0.3 m). Corregirlo con los sesgos publicados por satélite (OSB del CODE o del CAS) permitiría
     fijarlos; no se hace todavía (hay que bajar ese producto: `docs/estado.md`).
   - Con MEXI–IMIE (137 km) quedan flotantes de 5 a 33 cm de error; el RMS de error/σ era 1.7–1.8, así que la σ de
     una flotante con clases mezcladas se multiplica por 1.8 (`MIXED_FLOAT_FACTOR`, calibrado con esas 16 sesiones de
     1 h: es el único par mixto de la RGNA). El informe dice «los receptores no comparten la misma clase de señal
     (GPS L1/L2, L2C en el rover y P(Y) en la base)».
   - **Ninguna solución absurda llega al usuario** (`InsufficientData`): menos de 4 dobles diferencias por época en
     la mitad de la sesión («Solo hay 3 satélites comunes útiles… hacen falta al menos 5 a la vez»), sin dos
     frecuencias en común («en GPS el rover registra L1, L2 y la base L1»), σ de la flotante de más de 1 m o de la
     solución de código de más de 5 m, fases a más de 5 m del código, menos de 20 épocas comunes. No se pasa a RTKLIB
     (tendría los mismos datos); con varias bases, esa se deja fuera con aviso y las demás se ajustan.
3. **ZIP del INEGI** (fallo 4): el almanaque `.26a` (y cualquier archivo que no sea observación ni navegación) se
   ignora con una nota; un ZIP dañado (`MERI196m_304.zip`) se deja fuera con aviso del hueco y se usan las otras
   horas. IMIE se lee tal como lo entrega el INEGI.
4. **Umbrales e incertidumbre** (fallo 2 y recomendaciones), con fundamento antes que factores:
   - **Prueba de coherencia fija–flotante:** con enteros correctos la fija cae dentro de la región de confianza de
     la flotante, que no depende de los enteros; si algún componente se aleja más de 3σ (σ realista de la flotante),
     no se fija. El fijo de +136 mm (6.8σ, razón 10) de UGTO–INEG con máscara de 30° se aleja 135 mm de su flotante
     (3.5σ) y queda flotante con el porqué. Con la máscara de 15° no se disparó en ninguna de las 1 624 fijas de los tres días.
   - **Máscara de más de 20°: 2 h para fijar** (sin satélites bajos la altura y el retardo cenital no se separan en
     una hora). **Bajo 60 km: 45 min** en vez de 30.
   - **σ vertical de la fija ×1.15**; **σ de la flotante ×1.6 en el este y ×1.2 en la vertical**, y ×2.4 y ×1.4 si
     una sesión que debía fijar queda flotante (la flotante «fallida» es de datos peores).
   - Sin tocar `NL_MIN_FIXED`, la razón ni la tasa de éxito (no separan los fijos erróneos, §7).
   - La comprobación con RTKLIB (`CHECK_H_M`/`CHECK_V_M`) **no avisó en ninguno de los fijos a más de 3σ de la §7**:
     su tolerancia es la σ de la libre de ionosfera de RTKLIB (4–5 cm en la vertical con 1 h a 140 km), más gruesa
     que esos errores. Solo atrapa fallos gruesos (saltos de ciclo que RTKLIB no ve, MEXI–IMIE de antes); se deja así.
5. **Relojes precisos (fallo 5):** la app baja solo el SP3 (`core/precise.py`) y el script le pasa solo el SP3. En
   dobles diferencias el reloj del satélite se cancela: los dos receptores lo ven con menos de 0.5 ms de diferencia y
   el error de interpolar el reloj del SP3 cada 5 min es el mismo en los dos; en la emisión, 1 ns de reloj mueve el
   satélite 4 µm. Comprobado: UGTO–INEG 1 h (04 y 16 h del 15-07) con SP3 solo y con SP3 + CLK de 30 s da lo mismo a
   0.1 mm. Se deja así.

### Resultado en los días de calibración (antes → después, mismas sesiones)

Procesador propio (desde 20 km), sin las líneas con IZAC (referencia, §7) ni MEXI–IMIE (aparte); fijos a más de
3σ = fija con algún componente del error contra el PPP a más de 3 veces su σ. RMS y máximo de error/σ por
componente (E / N / U). Tablas con `scripts/validacion_rgna.py --resumen … --por-dia --excluir IZAC`; JSON en
`DATOS/validacion/v2/`.

| Día | Líneas base | Fijas | Flotantes | Fijas > 3σ | Flotantes > 3σ | RMS error/σ fijas | Máx. fijas | RMS error/σ flotantes | Máx. flotantes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20-10 (293) antes | 192 | 136 | 56 | 0 | 0 | 0.84 / 0.91 / 0.97 | 2.3 / 2.0 / 2.8 | 1.34 / 0.90 / 1.19 | 3.0 / 2.0 / 2.6 |
| 20-10 (293) después | 192 | 128 | 64 | **0** | 0 | 0.85 / 0.90 / 0.85 | 2.3 / 2.0 / 2.2 | 1.10 / 0.87 / 1.11 | 2.7 / 1.9 / 2.4 |
| 15-07 (196) antes | 1100 | 779 | 321 | 11 | 33 | 0.83 / 0.57 / 1.17 | 2.2 / 3.4 / 3.4 | 1.71 / 0.75 / 1.25 | 6.1 / 2.5 / 5.2 |
| 15-07 (196) después | 1100 | 771 | 329 | **1** | 22 | 0.83 / 0.56 / 1.04 | 2.2 / 2.8 / 3.1 | 1.32 / 0.75 / 1.17 | 5.6 / 2.5 / 5.0 |
| 18-02 (049) antes | 1038 | 732 | 306 | 6 | 12 | 0.92 / 0.44 / 1.07 | 2.6 / 2.5 / 3.7 | 1.25 / 0.73 / 1.06 | 5.0 / 3.1 / 3.9 |
| 18-02 (049) después | 1038 | 725 | 313 | **4** | 8 | 0.92 / 0.43 / 0.96 | 2.7 / 1.8 / 3.5 | 0.99 / 0.75 / 0.97 | 4.8 / 3.1 / 3.5 |

- **El día 293 no empeora:** ninguna fija a más de 3σ antes ni después; la vertical de la fija pasa de RMS 0.97 a
  0.85 y su máximo de 2.8 a 2.2σ. Las 8 fijas de menos son las de 30 min a 30 km (ITLA–ICEP), ahora flotantes por
  el mínimo de 45 min.
- Las fijas de menos de los otros días son las de 30 min a 30 km (8 + 7). Con 1 h a 100–141 km (sin IZAC) siguen
  fijas 150 de 152 (15-07) y 134 de 136 (18-02), igual que antes.
- Los fijos que quedan a más de 3σ (todos en la vertical, 3.1–3.5σ): 18-02 ITLA–IHID 1 h 00 h difundidas y
  precisas, ITLA–TOL2 1 h 00 h y 2 h 03 h precisas; 15-07 ICEP–UVER 2 h 21 h (219 km, 2147 m de desnivel). Son la
  troposfera de la noche de la §7 (la flotante tiene casi el mismo error): ningún umbral de enteros los separa.
- Las flotantes a más de 3σ (22 y 8) son de 15–45 min a 100–240 km, casi todas en el este: la σ de la flotante corta
  sigue algo corta en el este el 15-07 (RMS 1.32); se dejó el factor recomendado (1.6) en vez de ajustarlo a ese día.
- **Ataques con máscara alta (15-07, UGTO con tres bases):** con 25° y 30°, 18 de 36 sesiones fijan (las de 2 h) y
  **ninguna fija pasa de 3σ** (antes 2 y 2, una a 6.8σ); las de 1 h quedan flotantes con el porqué, la de +136 mm a
  +1.5 mm.
- **Nuevos:** MERI–ICAM del 15-07 (antes tumbado por el ZIP dañado): 42 fijas de 58, 1 a 3.3σ (24 h precisas, U,
  costa: carga oceánica sin modelar) y 4 sesiones de la hora que falta con su error. **MEXI–IMIE:** 92 líneas base,
  ninguna absurda: 88 flotantes (error/σ máx. 3.4) y 2 fijas de 4 h el 18-02 con los 6 satélites viejos que sí
  comparten P(Y), una a 3.1σ en la vertical (+42 mm): con receptores y antenas distintos (NetR9 con TRM55971.00) y
  tan pocas combinaciones, la fija es frágil; ver «Lo que no se pudo».

### Tercer día: 15-04-2026 (día 105, fin de la temporada seca), prueba final

**Datos:** RGNA del 15-04-2026, bajada de la página pública «Archivos RINEX de la RGNA» (ZIP horarios, RINEX 3.04
cada 15 s): UGTO, UQRO, ISLP, INEG, ITLA, ICEP, IHID y TOL2; órbitas y relojes finales de la ESA de los días 104–106
(`DATOS/precisas/dia105`); referencia PPP de 24 h como en el «Diseño» (`DATOS/validacion/oos_ref_105.json`). Se bajó
antes de tocar los umbrales y se corrió una sola vez, después de congelarlos. Líneas: UGTO con UQRO, ISLP e INEG
(100–141 km), ITLA con ICEP, IHID y TOL2 (31–150 km), ICEP con IHID y TOL2 (133 y 156 km), TOL2 con UQRO e IHID
(165 y 132 km), INEG con ISLP y UQRO (135 y 240 km); sesiones de 15 min a 24 h (8 o 12 por duración), difundidas y
precisas: **756 líneas base**. Reproducir (`--salida DATOS/validacion/v2/oos105_ugto`, etc.):

    python scripts/validacion_rgna.py --datos DATOS/rgna/20260415 --precisas DATOS/precisas/dia105 \
        --fecha 2026-04-15 --referencia DATOS/validacion/oos_ref_105.json --limpiar --seguir \
        --rover UGTO --bases UQRO,ISLP,INEG --sesiones 1,2,4,24 --ventanas 12 --salida DATOS/validacion/v2/oos105_ugto

| Día | Líneas base | Fijas | Flotantes | Fijas > 3σ | Flotantes > 3σ | RMS error/σ fijas | Máx. fijas | RMS error/σ flotantes | Máx. flotantes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15-04 (105) | 756 | 591 | 165 | **4** (0.7 %) | 12 | 0.90 / 0.57 / 1.03 | 2.5 / 2.3 / 4.1 | 1.41 / 0.93 / 1.21 | 5.0 / 2.8 / 3.9 |

| Sesión | 30.7 km | 100–141 km | 150–165 km |
| --- | --- | --- | --- |
| 15 / 30 min | flotantes (8) | flotantes (80) | flotantes (8) |
| 45 min | 8/8 fijas | flotantes (44) | flotantes (8) |
| 1 h | 16/16 | **134/136** | 54/64 |
| 2 h | 16/16 | 136/136 | 64/64 |
| 4 h / 24 h | 12/12 · 2/2 | 84/84 · 14/14 | 43/48 · 8/8 |

- **Ninguna sesión falló ni dio una solución absurda.** 4 fijas de 591 (0.7 %) pasan de 3σ, todas en la vertical:
  INEG–ISLP e INEG–UQRO 1 h a las 09 h UTC difundidas (3.6 y 3.9σ) y ITLA–TOL2 y TOL2–IHID 2 h a las 00 h precisas
  (−3.2 y 4.1σ). El RMS de error/σ de las fijas es 0.90 / 0.57 / 1.03: la σ de la fija está bien calibrada.
- Las flotantes a más de 3σ son sobre todo **las de TOL2 a las 00 h UTC**: en las seis líneas con TOL2 la sesión de
  1 h de las 00 h no pasó la razón (1.1–1.3) y quedó flotante con 11–15 cm en el este (3.1–3.9σ con la σ de
  «fallida»). Bien que no fijó; la σ de esa flotante se queda corta. Es de la estación esa hora (todas sus líneas a
  la vez), no del procesador.
- El este de la flotante corta sigue algo optimista (RMS 1.41): con los cuatro días, la σ de la flotante de menos de
  la duración mínima vale para decidir, no para entregar coordenadas de centímetro.

### Lo que ahora se sostiene

Con los cuatro días (20-10, 15-07, 18-02 y 15-04; tres temporadas), 16 líneas de 30 a 240 km y receptores Trimble
Alloy:
- **Con 1 h o más a 30–141 km el procesador fija el 98–99 % de las sesiones** (con 1 h a 100–141 km, 150/152,
  134/136 y 134/136 los días 15-07, 18-02 y 15-04; a 150–165 km, 54 de 64 el 15-04), y desde 2 h casi todas hasta 183 km.
- **De 2 215 fijas, 9 (0.4 %) pasan de 3σ, ninguna a más de 4.1σ, todas en la vertical (4–8 cm)**: la troposfera de
  la noche y de la tarde de lluvias, que la σ cubre casi siempre; ningún fijo erróneo como el de +136 mm. El RMS de
  error/σ de las fijas queda en 0.85–1.04 por componente: la σ que se entrega es realista (1σ ≈ 68 %).
- Las flotantes dicen por qué no fijaron; su σ puede quedarse corta en el este (RMS 1.1–1.4).
- Lo que no se puede resolver con fundamento no entrega solución: pocos satélites, sin frecuencias en común, fases
  incoherentes.
- **Ya no se sostiene «sin fijos erróneos»** tal cual: la frase correcta es «0.4 % de las fijas fuera de 3σ, en la
  vertical, y ninguna a más de 4.1σ».

### Lo que no se pudo

- **Receptores mixtos de verdad:** solo MEXI–IMIE (y con antenas distintas); allí la fija con pocas combinaciones
  (6, de los satélites viejos) dio 3.1σ en la vertical. Ningún Meridian. Los sesgos de código publicados (OSB) para
  fijar con clases mezcladas, sin hacer.
- **σ de la flotante en el este:** el factor 1.6 deja el RMS de error/σ en 1.1–1.4; las colas (TOL2 00 h, 15–45 min
  a 150–240 km) llegan a 5σ. No se ajustó más para no calibrar sobre los mismos días.
- **20–30 km**, la carga oceánica en la costa (MERI–ICAM 24 h, 3.3σ en la vertical) y la referencia PPP de RTKLIB
  (±1–3 cm en la vertical): como en la §7.
- Los días 196 y 049 pasaron a ser de calibración (los umbrales de la §8 salieron de ellos y del 293); el único día
  fuera de muestra para estos umbrales es el 15-04.

## Lo que esta validación no cubre

- **Cuatro días (§7 y §8) y una sola marca de receptor y antena en las líneas fijas:** todas las estaciones son Trimble Alloy con TRM115000.00 NONE.
  Con receptores distintos (un MeridianV o MeridianVI contra la RGNA) los sesgos de código y fase entre clases de señal
  no se cancelan: el motor solo forma dobles diferencias con la misma clase en los dos receptores (lo que no coincide
  queda flotante), pero eso no se ha probado con datos reales mixtos. La antena de los Meridian no tiene calibración:
  en la libre de ionosfera la diferencia de centro de fase con la de la base pesa unas 3 veces más que en L1 (cm en la
  vertical); hace falta su calibración de laboratorio.
- **Ninguna grabación real de un MeridianV o MeridianVI** ha pasado por el motor (no hay muestra).
- Centro de México: sin carga oceánica (a 300 km de la costa la diferencia entre los extremos es de 1–2 mm), con
  ionosfera de máximo solar. En la costa la carga oceánica puede dar 5–10 mm en la vertical en líneas de 100 km.
- Ninguna línea de menos de 30 km (la RGNA no tiene pares más cercanos con datos): de 20 a 30 km el propio se usa por
  la física (la libre de ionosfera y sus sesgos crecen con la distancia, así que cuanto más corta, mejor fija), no por
  una prueba; por debajo de 20 km sigue RTKLIB.
- Ninguna línea de más de 141 km: el motor no limita la longitud, pero de ahí en adelante la duración mínima (1 h) y
  la σ (que crece con L) no están probadas.
- La referencia PPP es de RTKLIB (flotante): una referencia de otro programa (CSRS-PPP, GIPSY) pide cuenta.
