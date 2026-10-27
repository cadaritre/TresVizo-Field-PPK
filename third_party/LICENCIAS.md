# Licencias de terceros

Todo lo de terceros que se incluye o se usa, con su licencia (todas compatibles con la licencia MIT del programa).
Los avisos completos van en cada carpeta y en el instalador.

## Incluido como fuente o datos

| Componente | Versión | Licencia | Dónde | Para qué |
| --- | --- | --- | --- | --- |
| RTKLIB-EX (ex demo5), T. Takasu y rtklibexplorer | 2.5.1 | BSD 2 cláusulas | `rtklib/` | `rnx2rtkp` (proceso) y `convbin` |
| RNXCMP (crx2rnx), Y. Hatanaka / GSI | 4.2.0 | Términos de uso de la GSI (≈ CC BY 4.0), con cita | `rnxcmp/` | Hatanaka `.crx`/`.d` |
| ANTEX igs20 de la IGS (subconjunto) | igs20_2437 | Acceso libre de la IGS, con cita | `tresvizo_ppk/core/data/antex/` | PCO/PCV de satélites y de las antenas de la RGNA |
| Catálogo de estaciones de la RGNA del INEGI | corte del 15-10-2024 | Términos de Libre Uso del INEGI, con atribución | `tresvizo_ppk/core/data/rgna.json` | coordenadas oficiales ITRF2008 2010.0 |
| Convertidor de grabaciones del MeridianV | commit 48e80e6 | del propietario (mismo autor) | `tresvizo_ppk/core/meridian_rinex/` | `.bin`/`.part` → RINEX |
| Geoide Gravimétrico Mexicano GGM10 y GGM25, INEGI | 2010 y 2025 | Términos de Libre Uso de la Información del INEGI: se redistribuye con el crédito «Fuente: INEGI…» y el aviso de la transformación (redondeo al centímetro; recorte en GGM25), que salen en el informe y en la exportación | `tresvizo_ppk/crs/datos/ggm10.mvgeoid`, `ggm25.mvgeoid` | cota ortométrica (los mismos archivos de las apps TresVizo Field, byte a byte) |
| Definiciones de WGS 84 y Mexico ITRF2008 / UTM 11N–16N, registro EPSG de IOGP | v12.029 | Términos de uso del registro EPSG (uso libre citando la fuente) | `tresvizo_ppk/crs/datos/fabrica.json` | sistemas de fábrica (los de las apps) |
| Software de las Convenciones del IERS 2010 (GMF y DEHANTTIDEINEL con sus subrutinas), IERS Conventions Center | Convenciones 2010 (las rutinas de `lib/iers` de RTKLIB-EX 2.5.1) | Licencia del software de las Convenciones del IERS: uso libre, también comercial, y obras derivadas identificadas como tales, con los nombres cambiados, la descripción de los cambios y el aviso íntegro en la fuente | `tresvizo_ppk/core/larga/iers.py` (traducción a Python, con el aviso completo y en qué difiere) | funciones de mapeo troposférico GMF y mareas terrestres sólidas del procesador de líneas largas |
| LAMBDA de RTKLIB (`src/lambda.c`, T. Takasu) | RTKLIB-EX 2.5.1 | BSD 2 cláusulas (la de RTKLIB, ya incluida) | `tresvizo_ppk/core/larga/lambda_ils.py` (implementación propia que sigue su estructura y se prueba con sus casos de `t_lambda.c`) | decorrelación y búsqueda entera de las ambigüedades |
| Índice firmado de datos de TresVizo (`cadaritre/GNSS-TresVizo`) | secuencia 3, 21-10-2026 | del propietario; cada entrada dice su licencia y atribución | `tresvizo_ppk/crs/datos/datos-index.json` | la lista de sistemas y geoides descargables sin conexión |

## Dependencias de Python (requirements.txt)

| Paquete | Licencia | Para qué |
| --- | --- | --- |
| numpy | BSD 3 cláusulas | ajuste por mínimos cuadrados, estadística del motor; lector de geoides `.mvgeoid` |
| pyproj (con PROJ 9.8.1 y su `proj.db`, que lleva el registro EPSG v12.029) | MIT (PROJ: MIT; datos EPSG: términos de uso de IOGP) | proyecciones, datums heredados y rejillas NTv2 |
| matplotlib (con la letra DejaVu Sans, licencia Bitstream Vera) | Licencia de Matplotlib (tipo PSF, compatible con BSD) | gráficas del informe |
| reportlab | BSD 3 cláusulas | informes PDF |
| pypdf (solo pruebas) | BSD 3 cláusulas | leer el texto de los informes en las pruebas |
| pytest (solo pruebas) | MIT | pruebas |

## Interfaz y distribución

| Componente | Versión | Licencia | Dónde | Para qué |
| --- | --- | --- | --- | --- |
| Iconos Lucide (Lucide Icons y colaboradores) | 1.61.0 | ISC | `assets/icons/` (con `LICENSE.txt` y `FUENTE.txt`) | iconos de la navegación y las acciones |
| Sistema visual de TresVizo Med (tokens, contraste, estilos, instalador MSI) | — | MIT, del mismo propietario | `app/themes.py`, `app/ui_theme.py`, `scripts/generate_msi.py`, `scripts/installer/Product.wxs` | misma familia visual y mismo instalador |
| Pillow | 13.0.0 | MIT-CMU (HPND) | dependencia | iconos, logo y vista previa |
| tkinterdnd2 (con tkdnd 2.10) | 0.7.0 | MIT (tkdnd: BSD) | dependencia | arrastrar y soltar archivos |
| pypdfium2 (con PDFium) | 5.14.0 | Apache 2.0 o BSD 3 cláusulas (PDFium: BSD 3 cláusulas y Apache 2.0) | dependencia | vista previa del informe |
| PyInstaller (solo para armar) | 6.22.3 | GPL 2.0 con excepción del cargador: permite distribuir programas armados con él bajo cualquier licencia | `requirements-build.txt` | el ejecutable de Windows y la app de macOS |
| WiX Toolset (solo para armar, Windows) | 4.0.6 | MS-RL; se incluye su licencia y el enlace a la fuente en el MSI | `scripts/installer/WiX-LICENSE.txt` | instalador MSI |

Los avisos completos de cada dependencia incluida se copian en la carpeta `licencias/` del programa instalado
(`scripts/distribuir.py`).
