"""Rutas del programa: recursos incluidos, configuración del usuario y carpeta de datos por omisión."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = 'TresVizo Field PPK'
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
ASSETS = ROOT/'assets'


def config_dir() -> Path:
    """Preferencias del usuario (no son datos de trabajo): la carpeta estándar de cada sistema."""
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA') or Path.home()/'AppData'/'Roaming')
        return base/APP_NAME
    if sys.platform == 'darwin':
        return Path.home()/'Library'/'Application Support'/APP_NAME
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'tresvizo-field-ppk'


def documents_dir() -> Path:
    """Documentos del usuario (en Windows, la carpeta conocida aunque esté redirigida)."""
    if sys.platform == 'win32':
        try:
            import ctypes
            from ctypes import wintypes
            buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:
                return Path(buffer.value)
        except (AttributeError, OSError):
            pass
    return Path.home()/'Documents'


def default_data_dir() -> Path:
    """Proyectos, descargas de la RGNA y efemérides: a la vista del usuario, no escondidos."""
    return documents_dir()/APP_NAME
