# Datos que viajan con el motor

## `redes/rgna-inegi.json` — paquete de red CORS de la RGNA (formato: docs/formato-red-cors.md)

- **Fuente:** INEGI, «Coordenadas Geodésicas de las Estaciones de la RGNA»
  (https://www.inegi.org.mx/app/geo2/coordenadasGeoRGNA/): latitud, longitud y altura geodésica de la placa en
  ITRF2008, época 2010.0 (elipsoide GRS80), altura vertical de la antena (placa → base de la antena), receptor,
  antena del fabricante y su nombre del NGS. Corte del INEGI: el de `source.cutoff`.
- **Licencia:** Términos de Libre Uso de la Información del INEGI (https://www.inegi.org.mx/inegi/terminos.html):
  uso y redistribución, también comercial, con la atribución «Fuente: INEGI, Coordenadas Geodésicas de las
  Estaciones de la RGNA», que el programa pone en el informe. Transformación propia (se dice como piden los
  términos): cartesianas calculadas de las geodésicas, nombre de antena sin espacios, radomo «NONE» supuesto, y
  avisos por estación (COL2, del INEGI; INEG, de la validación del motor).
- **Regenerar:** `python scripts/red_rgna_inegi.py` (lo corre quien mantiene los datos; la app no se conecta). Es
  el mismo archivo que se publica como paquete `cors-network` en el índice firmado.

## `antex/igs20_WWWW_tvppk.atx.gz` — modelos de antena

- **Fuente:** ANTEX de la IGS `igs20_2437.atx` (https://files.igs.org/pub/station/general/), SHA-256
  `0cd58d0d9ee678d5586de6c01f576e5aceb86f4a0735a168f733dfd091c0a614`.
- **Qué lleva:** la cabecera, todas las antenas de satélite y las de receptor de la RGNA (todas sus variantes de
  radomo), sin cambiar una línea; un comentario en la cabecera dice de qué archivo sale.
- **Licencia:** los productos de la IGS son de acceso libre y se citan como «International GNSS Service (IGS),
  ANTEX igs20» (Johnston, G., Riddell, A., Hausler, G. (2017), The International GNSS Service, Springer
  Handbook of GNSS).
- **Regenerar:** bajar el `igs20_WWWW.atx` vigente y `python scripts/update_antex.py RUTA/igs20_WWWW.atx`.
