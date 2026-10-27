"""Nombres visibles en español de sistemas, geoides y redes (una sola función para la interfaz y el informe).

Regla: si el paquete trae el nombre en español, se usa; si no, se traduce con la tabla de términos («zone» → «zona»,
«height» → «cota», «Mexico» → «México»…). El nombre original del registro EPSG se muestra solo en el detalle
(«Nombre EPSG»).
"""
from __future__ import annotations

import re

# Orden importa: los compuestos antes que las palabras sueltas.
TERMS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\s*\+\s*([^+]+?)\s+height\b', re.I), r' · cota \1'),        # «… + GGM10 height» → «… · cota GGM10»
    (re.compile(r'\s*\+\s*([^+]+?)\s+depth\b', re.I), r' · profundidad \1'),
    (re.compile(r'\bUTM zone\b'), 'UTM zona'),
    (re.compile(r'\bzone\b'), 'zona'),
    (re.compile(r'\bheight\b'), 'cota'),
    (re.compile(r'\bMexico\b'), 'México'),
    (re.compile(r'\bMexican\b'), 'mexicano'),
    (re.compile(r'\bBrazil\b'), 'Brasil'),
    (re.compile(r'\bPeru\b'), 'Perú'),
    (re.compile(r'\bPanama\b'), 'Panamá'),
    (re.compile(r'\bSpain\b'), 'España'),
    (re.compile(r'\bWorld\b'), 'Mundo'),
    (re.compile(r'\bNorth\b'), 'Norte'),
    (re.compile(r'\bSouth\b'), 'Sur'),
    (re.compile(r'\bGeographic\b'), 'Geográficas'),
    (re.compile(r'\bTransverse Mercator\b'), 'Transversa de Mercator'),
    (re.compile(r'\bLambert Conic Conformal\b'), 'Cónica conforme de Lambert'),
    (re.compile(r'\bGauss-Kruger\b|\bGauss-Krüger\b'), 'Gauss-Krüger'),
    (re.compile(r'\bfalse easting\b', re.I), 'falso este'),
]


def display_name(name: str, spanish: str | None = None) -> str:
    """Nombre visible en español: el que trae el paquete (`spanish`) o el traducido con la tabla de términos."""
    if spanish:
        return spanish
    if not name:
        return name or ''
    text = name
    for pattern, replacement in TERMS:
        text = pattern.sub(replacement, text)
    return re.sub(r'\s{2,}', ' ', text).strip()
