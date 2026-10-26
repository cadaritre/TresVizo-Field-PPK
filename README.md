# TresVizo Field PPK

Posproceso de oficina para los receptores **MeridianV** y **MeridianVI** y la app **TresVizo Field**, ligado a la
**Red Geodésica Nacional Activa (RGNA) del INEGI**:

- **Georreferenciar un punto** (estático) contra una a tres estaciones de la RGNA, con ajuste por mínimos cuadrados e
  informe de línea base.
- **PPK de varios puntos**: la jornada cinemática del receptor y el CSV de TresVizo Field, con base de la RGNA o propia.
- **Sistemas de coordenadas, datums y geoides** del mismo catálogo que las apps de campo.

Python 3.13, Tkinter/ttk y RTKLIB demo5. En desarrollo: ver [especificación](docs/especificacion.md) y
[estado](docs/estado.md).

Licencia MIT. Avisos de terceros en `third_party/`.
