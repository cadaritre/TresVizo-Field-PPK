"""Acceso de la interfaz a las funciones de la §8 (motor, sistemas, informe y exportación), y a nada más.

Los módulos se cargan al pedirlos; si una parte no se puede cargar (una compilación incompleta), la interfaz lo dice y
desactiva lo que depende de ella en lugar de fallar al arrancar. Las pruebas inyectan dobles con las mismas firmas.
"""
from __future__ import annotations

import importlib
from types import ModuleType

MODULES = {'core': 'tresvizo_ppk.core.api', 'crs': 'tresvizo_ppk.crs.api',
           'report': 'tresvizo_ppk.report.api', 'export': 'tresvizo_ppk.export.api'}
LABELS = {'core': 'el motor de proceso', 'crs': 'los sistemas de coordenadas',
          'report': 'el generador de informes', 'export': 'la exportación'}


class Backend:
    def __init__(self, **parts):
        """Backend(core=…, crs=…, report=…, export=…) para pruebas; Backend.load() en la app."""
        self.parts: dict[str, ModuleType | object | None] = {k: parts.get(k) for k in MODULES}
        self.errors: dict[str, str] = {}

    @classmethod
    def load(cls) -> 'Backend':
        backend = cls()
        for key, name in MODULES.items():
            try:
                backend.parts[key] = importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001 - se informa en la interfaz, no se oculta
                backend.parts[key] = None
                backend.errors[key] = f'{type(exc).__name__}: {exc}'
        return backend

    def __getattr__(self, key):
        parts = self.__dict__.get('parts', {})
        if key in parts:
            return parts[key]
        raise AttributeError(key)

    def available(self, key: str, function: str | None = None) -> bool:
        part = self.parts.get(key)
        return part is not None and (function is None or callable(getattr(part, function, None)))

    def missing_message(self, key: str) -> str:
        return (f'Esta instalación no incluye {LABELS[key]}. Reinstala TresVizo Field PPK; si el problema sigue, '
                'consulta el registro de errores en Ajustes.')
