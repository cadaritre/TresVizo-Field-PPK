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

## Lo que esta validación no cubre

- **Un solo día y una sola marca de receptor y antena:** todas las estaciones son Trimble Alloy con TRM115000.00 NONE.
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
