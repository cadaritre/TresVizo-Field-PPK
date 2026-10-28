# Instalación, versiones e instaladores

## Instalar

La app **no está firmada con un certificado comercial** (decisión del propietario, 27-10-2026): la primera vez macOS o
Windows piden confirmar que quieres abrirla. Los pasos van dentro del DMG («Cómo abrirla.txt»), junto al MSI
(`TresVizo-Field-PPK-<versión>-Windows-como-instalarla.txt`) y en las notas de cada versión.

**Windows 10/11 (64 bits).** Ejecutar `TresVizo-Field-PPK-<versión>-x64.msi`. Se instala para la cuenta actual, sin
permisos de administrador, en `%LocalAppData%\Programs\TresVizo-Field-PPK`, con accesos en Inicio y Escritorio, y
asocia los proyectos `.tvppk` (doble clic los abre). Para actualizar, cerrar el programa y ejecutar el MSI nuevo: no
hace falta desinstalar. Un MSI de una versión inferior se rechaza. Si el programa está abierto, el instalador se detiene
sin cambiar nada. La primera vez:

1. Si el navegador avisa que el archivo no se descarga con frecuencia, elegir «Conservar» (en Edge: «…» → «Conservar» →
   «Mostrar más» → «Conservar de todos modos»).
2. Al abrir el MSI, si aparece **«Windows protegió tu PC»** (SmartScreen): **«Más información»** → **«Ejecutar de todas
   formas»**.

**macOS 14 o posterior en Mac con Apple (M1 o posterior), macOS 15 o posterior en Mac con Intel.** Es lo que piden
las ruedas de pyproj 3.8.0, la versión fijada para que los sistemas coincidan con los vectores de las apps; ningún
binario de la app puede pedir más que lo que declara (lo comprueba `scripts/distribuir.py` con
`scripts/macos_minimo.py`, y RTKLIB se compila para ese mínimo). Abrir
`TresVizo-Field-PPK-<versión>-macos-<arm64|x86_64>.dmg` (arm64 para Apple, x86_64 para Intel) y arrastrar la app a
Aplicaciones. La primera vez:

1. Abrir la app desde Aplicaciones; macOS avisa que no pudo comprobarla: «Listo».
2. Ajustes del Sistema → Privacidad y seguridad → en «Seguridad», «Se bloqueó "TresVizo Field PPK"…» →
   **«Abrir de todos modos»**, otra vez «Abrir de todos modos» y la contraseña o Touch ID. En **macOS 15 (Sequoia) o
   posterior** es la única forma; en **macOS 14 (Sonoma)** también sirve clic derecho (o Control + clic) → Abrir →
   Abrir.

Con una versión nueva, macOS puede volver a pedirlo una vez.

**Comprobar la SHA-256.** Cada instalador se publica con su `.sha256` y la tabla de las notas de la versión. La cifra
que se calcula debe ser exactamente la publicada:

- macOS, en Terminal: `shasum -a 256 ~/Downloads/TresVizo-Field-PPK-<versión>-macos-arm64.dmg`
- Windows, en la carpeta Descargas (escribir `cmd` en la barra de direcciones y Entrar):
  `certutil -hashfile TresVizo-Field-PPK-<versión>-x64.msi SHA256`

**Datos del usuario.** Los proyectos van donde el usuario los guarde (por omisión `Documentos/TresVizo Field PPK/
Proyectos`); las descargas en la carpeta de datos de Ajustes; las preferencias en `%APPDATA%\TresVizo Field PPK`
(Windows) o `~/Library/Application Support/TresVizo Field PPK` (macOS). Actualizar o desinstalar el programa no los
toca.

## Armar una versión

1. Subir `tresvizo_ppk/version.py` (tres números, Windows Installer admite hasta 255.255.65535) y anotar los cambios en
   `CHANGELOG.md`. Nunca reutilizar una versión ya entregada: el script se niega a sobrescribir un instalador.
2. En el sistema de destino, con Python 3.13 y `pip install -r requirements.txt -r requirements-build.txt`:

   ```
   python scripts/distribuir.py
   ```

   Compila RTKLIB (`scripts/build_rtklib.py`), corre las pruebas, arma el programa con PyInstaller
   (`TresVizo-Field-PPK.spec`, que incluye `rnx2rtkp`, `convbin` y `crx2rnx` en `bin/`), copia los avisos de licencias a
   `licencias/`, ejecuta la **autoprueba del programa empaquetado** (`--self-test`: interfaz, arrastrar y soltar, motor,
   binarios de RTKLIB, sistemas y geoides, PDF, gráficas, recursos) y arma el instalador con su `.sha256` y un `.json`
   con versiones de dependencias, huellas de las fuentes y el resultado de la autoprueba.
3. Windows necesita además .NET SDK 8 y WiX **4.0.6**:

   ```
   dotnet tool install --global wix --version 4.0.6
   wix extension add WixToolset.UI.wixext/4.0.6
   wix extension add WixToolset.Util.wixext/4.0.6
   ```

`UpgradeCode` en `scripts/generate_msi.py` (`2e205de5-69bc-4cc1-b6bd-457e39e5239c`) es la identidad permanente de
TresVizo Field PPK, distinta de la de TresVizo Med: **no se cambia nunca**. El ProductCode sale de la versión y la
arquitectura; los componentes tienen identificadores estables por ruta.

## Actualizaciones de la app (GitHub Releases)

En Ajustes → Actualizaciones, «Buscar actualizaciones» consulta la API pública de releases de
`cadaritre/TresVizo-Field-PPK` (la casilla «Buscar al abrir el programa» viene apagada; ninguna otra conexión). Si hay
una versión más nueva muestra sus novedades y **«Abrir la página de descarga»**, que abre en el navegador la página de
esa versión en GitHub (instaladores, `.sha256` y las notas con la primera apertura). La app no baja ni abre
instaladores por su cuenta.

**Camino firmado (sin uso hoy).** La distribución va sin llave de publicación (`PUBLIC_KEY_HEX` vacía en
`app/updates.py`). El código del camino firmado se conserva y está probado: si algún día se pega ahí una llave pública
Ed25519, «Bajar e instalar» baja el manifiesto `TresVizo-Field-PPK-<v>-manifiesto.json` y su `.sig` (los hace
`scripts/firmar_version.py`), comprueba la firma, baja el instalador de su sistema, comprueba tamaño y SHA-256 contra
el manifiesto y solo entonces lo abre.

## GitHub Actions

`.github/workflows/ci.yml` corre en cada push a `main` y en cada pull request (gratis en el repositorio público):

- **Pruebas** en Ubuntu 24.04 (Tk con xvfb), Windows Server 2022 (RTKLIB con MinGW) y macOS 15 (Apple).
- **Instaladores** (si las pruebas pasan): MSI de Windows x64, DMG de macOS arm64 (`macos-15`) y DMG de macOS x86_64
  (`macos-15-intel`), como
  artefactos descargables durante 30 días, cada uno con su `.sha256` (y el MSI con su «como-instalarla»).

**Publicar una versión** (etiqueta `v<versión>`): bajar los tres artefactos de la CI a una carpeta (por ejemplo
`dist/release`), escribir las notas con

```
python scripts/distribuir.py --notas dist/release
```

(novedades de `CHANGELOG.md`, qué archivo bajar, primera apertura en macOS 14 y 15+ y en Windows con SmartScreen, y la
tabla de la SHA-256 de cada instalador, comprobada contra su `.sha256`), y subir a la release los instaladores, sus
`.sha256` y el «como-instalarla» de Windows, con el texto de `TresVizo-Field-PPK-<versión>-notas.md` como descripción.
- **Un DMG armado en un equipo propio no se entrega.** El Python de Homebrew está compilado para el macOS de ese equipo
  (macOS 26 en el del propietario), así que la app solo abriría ahí: `scripts/distribuir.py` se niega a armarlo, y con
  `--solo-este-equipo` lo arma para probar, declarando lo que de verdad pide y con «-solo-este-equipo» en el nombre.
  Los DMG que se entregan son los de la CI (Python de `actions/setup-python`).

## Pendiente

- El ciclo completo del MSI (instalar, actualizar, reparar, rechazar retroceso, desinstalar), los avisos de SmartScreen
  y el DMG de x86_64 solo se verifican en la CI o en un equipo con ese sistema; en este equipo (macOS arm64) se armó y
  comprobó la app y el DMG de arm64.
