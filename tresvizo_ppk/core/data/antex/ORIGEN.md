# Origen del ANTEX incluido

`igs20_2437_tvppk.atx.gz` lo arma `scripts/update_antex.py` a partir de dos fuentes oficiales, públicas y sin cuenta, sin cambiar una sola línea de sus bloques:

| Fuente | Dirección | Fecha de bajada | SHA-256 | Qué se toma |
| --- | --- | --- | --- | --- |
| IGS, ANTEX igs20_2437 | https://files.igs.org/pub/station/general/igs20.atx | 2026-10-27 00:09 UTC | `0cd58d0d9ee678d5586de6c01f576e5aceb86f4a0735a168f733dfd091c0a614` | cabecera, 414 antenas de satélite y 512 de receptor (todas) |
| NGS (NOAA), ngs20.atx | https://geodesy.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx | 2026-10-27 13:27 UTC | `363a313f45c0f4245e0bd85c1848604c255f4a7b9925e17c1c8beb41884ed780` | 655 antenas de receptor que la IGS no trae |

Licencia y cita: ver `third_party/LICENCIAS.md` (IGS: acceso libre con cita; NGS: dominio público del gobierno de los EE. UU.).

`igs20_2437_tvppk.json` dice qué antenas vienen del NGS; el programa lo usa para escribir en el informe la fuente de cada calibración.
