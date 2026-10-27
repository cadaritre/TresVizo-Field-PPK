# Reglas permanentes

- **Producto terminado y profesional**, de grado industrial, al nivel de Trimble Business Center, Leica Infinity
  y Emlid Studio. Si algo no se puede hacer bien todavía, se dice en `docs/estado.md` (la solución completa, por qué
  el paso intermedio y qué falta). Nunca un atajo presentado como final ni una función declarada sin verificarla.
- Python 3.13, Tkinter y ttk como base de la interfaz, con el sistema visual de TresVizo Med
  (`app/themes.py`, iconos Lucide incluidos con su licencia). Español en toda la interfaz, los informes y la
  documentación.
- Separar interfaz (`app/`), motor y modelo (`tresvizo_ppk/`) y persistencia (proyectos `.tvppk` en JSON). El motor
  no importa nada de la interfaz y se puede usar sin ella (línea de órdenes y pruebas).
- El contrato común entre motor, sistemas de coordenadas, informe e interfaz es `tresvizo_ppk/model.py`: se amplía,
  no se rompe sin actualizar a todos sus usuarios en el mismo cambio.
- **Sin telemetría ni conexiones automáticas.** La red solo se usa cuando el usuario pide una descarga (datos de la
  RGNA, efemérides precisas, sistemas de coordenadas y geoides), y siempre se dice qué se baja y de dónde.
- No atribuir autoría, colaboración, firmas, créditos o marcas a herramientas automáticas en código, documentación,
  interfaz, instaladores, informes o metadatos. No agregar trailers de autoría automática.
- Identidad Git del propietario (`cadaritre <cadaritre@gmail.com>`); no inventar identidades ni reescribir historia.
  Commits pequeños que pasan sus pruebas; nunca `git add .`/`-A`, `commit -a`, amend, rebase ni force-push.
- Conservar las licencias y avisos de terceros (RTKLIB demo5 BSD-2, Lucide ISC, ANTEX de la IGS, datos del INEGI con
  su atribución) en `third_party/` y en el instalador.
- No subir datos de clientes, credenciales ni archivos grandes de observación: los datos de prueba son pequeños y
  públicos (RGNA, IGS), con su fuente.
- Antes de distribuir: subir `tresvizo_ppk/version.py`, actualizar `CHANGELOG.md` y no reutilizar una versión de
  instalador ya entregada.
