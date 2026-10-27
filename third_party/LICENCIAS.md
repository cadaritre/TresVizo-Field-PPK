# Licencias de terceros

Todo lo de terceros que se incluye o se usa, con su licencia (todas compatibles con la licencia MIT del programa).
Los avisos completos van en cada carpeta y en el instalador.

## Incluido como fuente o datos

| Componente | Versión | Licencia | Dónde | Para qué |
| --- | --- | --- | --- | --- |
| RTKLIB-EX (ex demo5), T. Takasu y rtklibexplorer | 2.5.1 | BSD 2 cláusulas | `rtklib/` | `rnx2rtkp` (proceso) y `convbin` |
| RNXCMP (crx2rnx), Y. Hatanaka / GSI | 4.2.0 | Términos de uso de la GSI (≈ CC BY 4.0), con cita | `rnxcmp/` | Hatanaka `.crx`/`.d` |

## Dependencias de Python (requirements.txt)

| Paquete | Licencia | Para qué |
| --- | --- | --- |
| numpy | BSD 3 cláusulas | ajuste por mínimos cuadrados, estadística del motor |
| pytest (solo pruebas) | MIT | pruebas |
