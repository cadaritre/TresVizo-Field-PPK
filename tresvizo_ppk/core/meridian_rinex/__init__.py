"""Conversión de las grabaciones crudas del MeridianV y del MeridianVI a RINEX 3.04.

Ver tools/rinex/README.md. Formatos de entrada:

- Binario nativo Unicore (UM980 del MeridianV): OBSVMB y efemérides GPSEPHB, GLOEPHB,
  GALEPHB, BDSEPHB y QZSSEPHB (Unicore, «N4 Products Commands and Logs Reference Book» R1.6).
- RTCM 10403.3: MSM4 a MSM7, 1005/1006, 1033 y efemérides 1019, 1020, 1042, 1044, 1045 y 1046
  (lo que emite el LG290P del MeridianVI y el UM980 en modo base).
- NMEA (GGA y RMC) para la posición aproximada y la fecha.
- El registro de metadatos de sesión `$PTVZ,META,...` que el firmware escribe al empezar.
"""

VERSION = "1.0"
PROGRAM = "to_rinex " + VERSION
