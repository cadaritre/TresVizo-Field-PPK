"""Procesador propio de líneas base largas (estático, ≥ 20 km): libre de ionosfera con resolución de ambigüedades
ancho/estrecho de carril y LAMBDA, como los programas científicos y comerciales (Bernese, GAMIT, TBC).

Módulos:
- `senales`: frecuencias, clases de señal y grupos de dobles diferencias.
- `observaciones`: lector de RINEX 2.11/3.x/4.x a arreglos.
- `efemerides`: órbitas y relojes difundidos (GPS, Galileo, BeiDou) y precisos (SP3 y RINEX de relojes).
- `antena`: PCO y PCV del ANTEX (receptores y satélites).
- `astro`, `iers`, `modelos`: tiempo, Sol y Luna, GMF y mareas sólidas (IERS 2010), troposfera, orientación de los
  satélites y wind-up.
- `geometria`: distancia calculada y correcciones por receptor y satélite.
- `preproceso`: combinaciones, saltos de ciclo y arcos.
- `ajuste`: mínimos cuadrados por lotes con los relojes eliminados por época (equivalente a dobles diferencias).
- `lambda_ils`: LAMBDA, tasa de éxito, prueba de razón y resolución parcial.
- `procesador`: el proceso completo y su resultado (`process`, `LongResult`).
"""
