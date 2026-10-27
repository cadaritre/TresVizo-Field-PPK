# Instalación, versiones e instaladores

## Instalar

**Windows 10/11 (64 bits).** Ejecutar `TresVizo-Field-PPK-<versión>-x64.msi`. Se instala para la cuenta actual, sin
permisos de administrador, en `%LocalAppData%\Programs\TresVizo-Field-PPK`, con accesos en Inicio y Escritorio, y
asocia los proyectos `.tvppk` (doble clic los abre). Para actualizar, cerrar el programa y ejecutar el MSI nuevo: no
hace falta desinstalar. Un MSI de una versión inferior se rechaza. Si el programa está abierto, el instalador se detiene
sin cambiar nada.

**macOS 14 o posterior en Mac con Apple (M1 o posterior), macOS 15 o posterior en Mac con Intel.** Es lo que piden
las ruedas de pyproj 3.8.0, la versión fijada para que los sistemas coincidan con los vectores de las apps; ningún
binario de la app puede pedir más que lo que declara (lo comprueba `scripts/distribuir.py` con
`scripts/macos_minimo.py`, y RTKLIB se compila para ese mínimo). Abrir
`TresVizo-Field-PPK-<versión>-macos-<arm64|x86_64>.dmg` (arm64 para Apple, x86_64 para Intel) y arrastrar la app a
Aplicaciones. La app **no está firmada ni notarizada por
Apple** (requiere una cuenta de desarrollador de pago; ver «Pendiente»): la primera vez macOS la bloquea. Se abre así:
Ajustes del Sistema → Privacidad y seguridad → «Abrir de todos modos» (en macOS 14 también con clic derecho → Abrir). El DMG trae estas instrucciones en «Cómo abrirla.txt». Comprobar antes la SHA-256 publicada junto al archivo.

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

## Actualizaciones de la app (GitHub Releases, firma Ed25519)

En Ajustes → Actualizaciones, «Buscar actualizaciones» consulta la API pública de releases de
`cadaritre/TresVizo-Field-PPK` (la casilla «Buscar al abrir el programa» viene apagada; ninguna otra conexión). Si hay
una versión más nueva muestra sus novedades y, con «Bajar e instalar», baja el **manifiesto firmado** de la versión,
comprueba su firma Ed25519 con la llave pública incluida en la app (`app/updates.py`, `PUBLIC_KEY_HEX`), baja el
instalador de su sistema (MSI o DMG), comprueba tamaño y SHA-256 contra el manifiesto y solo entonces lo abre. Sin
firma válida no instala nada y lo dice. En Linux enlaza la página de versiones.

**Llave de publicación (la crea y la guarda el propietario; nunca va al repositorio ni a la CI):**

1. Una sola vez, en el equipo del propietario:
   `python scripts/firmar_version.py --nueva-llave ~/Llaves/tresvizo-field-ppk-publicacion.key`
   Crea la llave privada (32 bytes, permisos 600; se niega a escribirla dentro del repo o a sobrescribir una) e
   imprime la llave pública. Respaldar la privada fuera del equipo: si se pierde, las versiones nuevas no se pueden
   firmar para las apps instaladas.
2. Pegar la llave pública en `app/updates.py` (`PUBLIC_KEY_HEX`, hoy vacío: **marcador**) y sacar una versión con
   ella. Mientras esté vacía, la app avisa de versiones nuevas pero no instala nada.
3. En cada versión, con los instaladores que armó la CI (y que se probaron):
   `python scripts/firmar_version.py --llave ~/Llaves/…key dist/release/TresVizo-Field-PPK-<v>-*`
   Deja `TresVizo-Field-PPK-<v>-manifiesto.json` y `…json.sig` junto a ellos; se suben **todos** al release
   `v<versión>` de GitHub (etiqueta con «v»).

## GitHub Actions

`.github/workflows/ci.yml` corre en cada push a `main` y en cada pull request (gratis en el repositorio público):

- **Pruebas** en Ubuntu 24.04 (Tk con xvfb), Windows Server 2022 (RTKLIB con MinGW) y macOS 15 (Apple).
- **Instaladores** (si las pruebas pasan): MSI de Windows x64, DMG de macOS arm64 (`macos-15`) y DMG de macOS x86_64
  (`macos-15-intel`), como
  artefactos descargables durante 30 días. Para publicar una versión se suben esos archivos y sus `.sha256` a un
  release de GitHub.
- **Un DMG armado en un equipo propio no se entrega.** El Python de Homebrew está compilado para el macOS de ese equipo
  (macOS 26 en el del propietario), así que la app solo abriría ahí: `scripts/distribuir.py` se niega a armarlo, y con
  `--solo-este-equipo` lo arma para probar, declarando lo que de verdad pide y con «-solo-este-equipo» en el nombre.
  Los DMG que se entregan son los de la CI (Python de `actions/setup-python`).

## Pendiente

- **Firma de código.** Sin certificado Authenticode (Windows) ni Developer ID con notarización (macOS) los instaladores
  muestran los avisos del sistema descritos arriba. La solución profesional es firmar el `.exe` y el `.msi` con un
  certificado de editor y firmar y notarizar la app de macOS (cuenta de desarrollador de Apple, 99 USD al año); queda a
  decisión del propietario.
- El ciclo completo del MSI (instalar, actualizar, reparar, rechazar retroceso, desinstalar) y el DMG de x86_64 solo se
  verifican en la CI; en este equipo (macOS arm64) se armó y comprobó la app y el DMG de arm64.
