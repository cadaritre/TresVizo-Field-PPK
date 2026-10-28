# TresVizo Field PPK

Posproceso de oficina para los receptores **MeridianV** y **MeridianVI** y la app **TresVizo Field**, ligado a la
**Red Geodésica Nacional Activa (RGNA) del INEGI**:

- **Georreferenciar un punto** (estático) contra una a tres estaciones de la RGNA, con ajuste por mínimos cuadrados e
  informe de línea base.
- **PPK de varios puntos**: la jornada cinemática del receptor y el CSV de TresVizo Field, con base de la RGNA o propia.
- **Sistemas de coordenadas, datums y geoides** del mismo catálogo que las apps de campo.

## Instalar

Los instaladores están en [Releases](https://github.com/cadaritre/TresVizo-Field-PPK/releases), cada uno con su
SHA-256 al lado:

- **Windows 10 y 11 (64 bits):** `TresVizo-Field-PPK-<versión>-x64.msi`.
- **macOS 14 o posterior (Apple M1 o posterior):** `…-macos-arm64.dmg`; **macOS 15 o posterior (Intel):**
  `…-macos-x86_64.dmg`.

La app no está firmada con un certificado comercial; la primera vez macOS o Windows piden confirmar que quieres
abrirla:

- **Windows:** si aparece «Windows protegió tu PC», pulsa «Más información» y después «Ejecutar de todas formas».
- **macOS:** abre la app una vez, ve a Ajustes del Sistema → Privacidad y seguridad y pulsa «Abrir de todos modos»
  (en macOS 15 o posterior es la única forma; en macOS 14 también sirve clic derecho → Abrir).

Para comprobar que el archivo es el original, compara su SHA-256 con la publicada: en macOS
`shasum -a 256 <archivo>`; en Windows `certutil -hashfile <archivo> SHA256`. Detalles en
[distribución](docs/distribucion.md).

## Desarrollo

Python 3.13, Tkinter/ttk y RTKLIB demo5. En desarrollo: ver [especificación](docs/especificacion.md) y
[estado](docs/estado.md).

Licencia MIT. Avisos de terceros en `third_party/`.
