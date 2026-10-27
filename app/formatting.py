"""Textos de resultados para la pantalla (sin Tk): ángulos, soluciones, coordenadas en el sistema elegido."""
from __future__ import annotations

import math

from tresvizo_ppk.model import Enu, Solution

from app.settings import Preferences, format_length

SOLUTION_TEXT = {Solution.FIXED: 'FIJA', Solution.FLOAT: 'FLOTANTE', Solution.SINGLE: 'AUTÓNOMA',
                 Solution.NONE: 'SIN SOLUCIÓN'}


def solution_text(solution) -> str:
    try:
        return SOLUTION_TEXT[Solution(solution)]
    except ValueError:
        return str(solution).upper()


def solution_key(solution) -> str:
    try:
        return Solution(solution).value
    except ValueError:
        return 'none'


def dms(value: float, positive: str, negative: str, places: int = 5) -> str:
    hemisphere = positive if value >= 0 else negative
    value = abs(value)
    degrees = int(value)
    minutes_full = (value-degrees)*60
    minutes = int(minutes_full)
    seconds = (minutes_full-minutes)*60
    if round(seconds, places) >= 60:
        seconds, minutes = 0.0, minutes+1
    if minutes >= 60:
        minutes, degrees = 0, degrees+1
    return f'{degrees}° {minutes:02d}′ {seconds:0{3+places}.{places}f}″ {hemisphere}'


def horizontal(sigma: Enu | None) -> float | None:
    return None if sigma is None else math.hypot(sigma.e, sigma.n)


def coordinate_rows(coordinate, prefs: Preferences, sigma: Enu | None = None) -> list[tuple[str, str]]:
    """Filas (etiqueta, valor) de una `TransformedCoordinate`, en el orden de los ejes del sistema."""
    rows = []
    if coordinate is None:
        return rows
    if coordinate.northing_m is not None and coordinate.easting_m is not None:
        north = ('Norte', format_length(coordinate.northing_m, prefs))
        east = ('Este', format_length(coordinate.easting_m, prefs))
        rows += [east, north] if coordinate.axis_order.startswith('E') else [north, east]
        if coordinate.zone:
            rows.append(('Zona', coordinate.zone))
    rows += [('Latitud', dms(coordinate.latitude_deg, 'N', 'S')), ('Longitud', dms(coordinate.longitude_deg, 'E', 'W')),
             ('Altura elipsoidal', format_length(coordinate.ellipsoidal_height_m, prefs))]
    if coordinate.orthometric_height_m is not None:
        kind = coordinate.height_kind or 'ortométrica'
        rows.append((f'Cota ({kind})', format_length(coordinate.orthometric_height_m, prefs)))
        rows.append(('Ondulación del geoide', format_length(coordinate.undulation_m, prefs)))
    if sigma is not None:
        rows.append(('σ Este / Norte', f'{format_length(sigma.e, prefs)} / {format_length(sigma.n, prefs)}'))
        rows.append(('σ vertical', format_length(sigma.u, prefs)))
    if coordinate.convergence_deg is not None:
        rows.append(('Convergencia', f'{coordinate.convergence_deg:+.6f}°'))
    if coordinate.scale_factor is not None:
        rows.append(('Factor de escala', f'{coordinate.scale_factor:.8f}'))
    return rows


def coordinate_clipboard(name: str, coordinate, ecef, sigma: Enu | None) -> str:
    """Texto plano para pegar en otra aplicación (metros, sin separadores de miles)."""
    lines = [name]
    if coordinate is not None:
        lines.append(coordinate.label or coordinate.crs_name)
        if coordinate.northing_m is not None:
            lines.append(f'Norte\t{coordinate.northing_m:.4f}\nEste\t{coordinate.easting_m:.4f}')
        lines.append(f'Latitud\t{coordinate.latitude_deg:.9f}\nLongitud\t{coordinate.longitude_deg:.9f}')
        lines.append(f'Altura elipsoidal\t{coordinate.ellipsoidal_height_m:.4f}')
        if coordinate.orthometric_height_m is not None:
            lines.append(f'Cota\t{coordinate.orthometric_height_m:.4f}\t{coordinate.geoid_name}')
    lines.append(f'{ecef.frame} época {ecef.epoch:g}\tX {ecef.x:.4f}\tY {ecef.y:.4f}\tZ {ecef.z:.4f}')
    if sigma is not None:
        lines.append(f'σ E {sigma.e:.4f}\tσ N {sigma.n:.4f}\tσ U {sigma.u:.4f}')
    return '\n'.join(lines)+'\n'
