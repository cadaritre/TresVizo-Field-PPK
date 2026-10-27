# Convertidor de grabaciones del MeridianV / MeridianVI a RINEX 3.04

- **De dónde sale:** repo MeridianV del propietario, `tools/rinex/meridian_rinex/` (el mismo que usan
  `to_rinex.py` y `to_rinex_gui.py`; documentación completa en `tools/rinex/README.md` de ese repo).
- **Commit de origen:** `48e80e6a2b12188552ba19001b83e652d6029d62 2026-10-21`
- **Copiado sin cambios.** Las pruebas de origen están en `tests/core/meridian_rinex/` (solo cambia la forma de
  importar el paquete).
- **Sincronizar:** `python scripts/sync_meridian_rinex.py` (copia el paquete y las pruebas, anota aquí el commit);
  luego `python -m pytest tests/core` y commit con el commit de origen en el mensaje. Nunca se edita aquí: los
  arreglos se hacen en el repo MeridianV y se vuelven a copiar.
- **Uso en el motor:** `tresvizo_ppk.core.observations` llama a `converter.convert()` con la altura del bastón
  (marca → base del receptor); el convertidor le suma la altura del case (`case_offset_m` de los metadatos de la
  grabación, o 0.116 m MeridianV / 0.152 m MeridianVI) y escribe el total a la ARP en `ANTENNA: DELTA H`, igual
  que `to_rinex.py --antenna-height`.
