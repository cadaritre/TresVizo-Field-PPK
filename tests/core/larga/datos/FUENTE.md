# Datos de prueba del procesador de líneas largas

Recortes de datos públicos reales del 20 de octubre de 2026 (día 293).

- **RGNA del INEGI** («Archivos RINEX de la RGNA», https://www.inegi.org.mx/app/geo2/rgna/). Fuente: INEGI, Archivos
  RINEX de la Red Geodésica Nacional Activa (uso libre con atribución, Términos de Libre Uso de la Información del
  INEGI).
- **Órbitas finales de la ESA/ESOC** (Navigation Support Office, https://navigation-office.esa.int/products/gnss-products/),
  productos IGS de acceso libre con cita.

Transformación hecha aquí, sin cambiar ningún valor: ventana de 00:00:00 a 00:59:30 GPS, una época cada 30 s y solo
los tipos que usa el procesador (GPS C1C L1C C2W L2W C2X L2X; Galileo C1X L1X C5X L5X; BeiDou C2I L2I C6I L6I),
con las columnas copiadas tal cual; en el SP3, de 00:00 a 02:00 y solo GPS, Galileo y BeiDou (la cabecera sigue
listando todos los satélites del original).

| Archivo | Origen | Contenido |
| --- | --- | --- |
| `UGTO00MEX_R_20262930000_01H_30S_MO.rnx.gz` | UGTO293a (RINEX 3.04) | UGTO (Guanajuato), 120 épocas |
| `UQRO00MEX_R_20262930000_01H_30S_MO.rnx.gz` | UQRO293a (RINEX 3.04) | UQRO (Querétaro), mismas épocas; UGTO–UQRO: 100.4 km |
| `UQRO00MEX_R_20262930000_01H_MN.rnx.gz` | UQRO293a | navegación mixta de esa hora, tal cual |
| `ESA0OPSFIN_20262930000_02H_05M_ORB.SP3.gz` | `ESA0OPSFIN_20262930000_01D_05M_ORB.SP3` | 26 épocas cada 5 min |

Referencia para la prueba de punta a punta: el vector UGTO−UQRO de los PPP estáticos de 24 h del día con órbitas y
relojes finales de la ESA (`docs/validacion.md`, movimiento de las estaciones respecto de su coordenada oficial).
