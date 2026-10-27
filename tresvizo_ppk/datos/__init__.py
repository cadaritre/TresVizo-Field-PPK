"""Datos descargables firmados, comunes a todo el programa (especificación §1.3, §3.0 y §3.5).

- `ed25519`: verificación Ed25519 (RFC 8032) en Python puro; la usan el índice de datos y la sirve también para
  verificar el manifiesto firmado de las actualizaciones de la app.
- `indice`: el sobre firmado del índice de TresVizo (`cadaritre/GNSS-TresVizo`) y sus reglas, como las apps.
- `biblioteca`: la carpeta de datos, el índice aceptado, la descarga con reintentos y reanudación y la instalación
  comprobada de cualquier clase registrada (`geoid`, `crs-package`, `datum-grid`, `cors-network`…).
"""
