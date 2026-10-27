# Estado

0.1.0 — esqueleto: especificación, reglas y contrato común (`tresvizo_ppk/model.py`). Nada procesa todavía.

## Interfaz y distribución (§1.4, §6, §2 y §7)

**Hecho y verificado (macOS 26 arm64, Python 3.13.18, Tk 9.1, tkdnd 2.10.3):**
- Ventana con barra lateral e iconos Lucide (Inicio, Georreferenciar punto, PPK de puntos, Sistemas y geoides,
  Estaciones RGNA, Ajustes, Acerca de) con el sistema visual de TresVizo Med: tokens y contraste (4.5:1 texto, 3:1 bordes,
  foco, series y estados de solución, comprobado en las pruebas para los temas Claro y Oscuro), letra del sistema,
  casillas, opciones y barras de desplazamiento propias, transición de 150 ms que respeta «Reducir movimiento», atajos
  (Ctrl/⌘+1…5, Ctrl/⌘+O, Ctrl/⌘+S, Ctrl/⌘+⇧+S, Alt+←/→ en los asistentes), menú nativo de macOS y apertura de `.tvppk`
  por doble clic o arrastrándolo a la ventana.
- Asistentes por pasos con arrastrar y soltar y botón equivalente: validación al momento (errores tras tocar el paso,
  avisos siempre, con icono y texto), proceso en un hilo aparte con progreso por etapas, tiempo y Cancelar, resultado con
  esquema en planta y barras de precisión, «Ver informe» (vista previa con pypdfium2 y «Guardar PDF…») y «Exportar»
  (estático: copiar coordenadas y PDF; PPK: CSV de TresVizo Field, CSV simple, GeoJSON y KML).
- Proyectos `.tvppk` (`tresvizo_ppk/project.py`): rutas relativas y SHA-256, escritura atómica, recientes, aviso al abrir
  si un archivo falta o cambió, coordenada final del estático para encadenar la base propia, número de proyecto.
- Pruebas: 43 de la interfaz (`tests/test_ui_logic.py`, `tests/test_project.py` sin ventanas; `tests/test_ui_widgets.py`
  con los widgets reales ocultos y los dobles del motor de `tests/dobles.py`: flujo completo de los dos asistentes,
  guardar, procesar, cancelar, error del motor, informe, exportar y reabrir).
- Revisión visual de cada pantalla con `python scripts/preview.py --view …` (datos sintéticos, carpeta temporal).
- PyInstaller: `TresVizo Field PPK.app` armada aquí (106 MB) con RTKLIB en `bin/`; su autoprueba `--self-test` pasa
  interfaz, tkdnd, binarios de RTKLIB, sistemas y geoides, PDF, gráficas y recursos. DMG de arm64 armado y montado
  (app, acceso a Aplicaciones y «Cómo abrirla.txt»).

**Pendiente o sin verificar:**
- La autoprueba exige `tresvizo_ppk.core.api` y `tresvizo_ppk.export.api` (agentes del motor y de exportación); hasta
  que existan falla en «modulos» y `scripts/distribuir.py` no entrega el instalador. La interfaz ya llama a esas
  funciones con las firmas de la §8 (y usa los mismos valores de `ionosphere` y `ambiguity_mode` que
  `core/baseline.py`) y se probó contra dobles; falta repetir el recorrido con el motor real y datos de la RGNA.
- La descarga de RINEX de la RGNA aparece solo si el motor ofrece `download_rgna`; si no, el asistente guía al usuario
  al sitio del INEGI.
- MSI de Windows y DMG de x86_64: solo los arma la CI (`.github/workflows/ci.yml`), que no ha corrido todavía (falta el
  push). No se ha probado en Windows real la instalación, actualización, reparación y desinstalación del MSI, ni el
  escalado de DPI de Windows (125/150/200 %).
- Instaladores sin firma de código (Authenticode / Developer ID y notarización): ver `docs/distribucion.md`.
- Sin lector de pantalla certificado: Tk no expone un árbol de accesibilidad completo; el uso con teclado sí se revisó.
