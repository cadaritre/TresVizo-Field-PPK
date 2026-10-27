# Licencias de terceros

Todo lo de terceros que se incluye o se usa, con su licencia (todas compatibles con la licencia MIT del programa).
Los avisos completos van en cada carpeta y en el instalador.

## Incluido como fuente o datos

| Componente | Versión | Licencia | Dónde | Para qué |
| --- | --- | --- | --- | --- |
| RTKLIB-EX (ex demo5), T. Takasu y rtklibexplorer | 2.5.1 | BSD 2 cláusulas | `rtklib/` | `rnx2rtkp` (proceso) y `convbin` |
| RNXCMP (crx2rnx), Y. Hatanaka / GSI | 4.2.0 | Términos de uso de la GSI (≈ CC BY 4.0), con cita | `rnxcmp/` | Hatanaka `.crx`/`.d` |
| Geoide Gravimétrico Mexicano GGM10 y GGM25, INEGI | 2010 y 2025 | Términos de Libre Uso de la Información del INEGI: se redistribuye con el crédito «Fuente: INEGI…» y el aviso de la transformación (redondeo al centímetro; recorte en GGM25), que salen en el informe y en la exportación | `tresvizo_ppk/crs/datos/ggm10.mvgeoid`, `ggm25.mvgeoid` | cota ortométrica (los mismos archivos de las apps TresVizo Field, byte a byte) |
| Definiciones de WGS 84 y Mexico ITRF2008 / UTM 11N–16N, registro EPSG de IOGP | v12.029 | Términos de uso del registro EPSG (uso libre citando la fuente) | `tresvizo_ppk/crs/datos/fabrica.json` | sistemas de fábrica (los de las apps) |
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
