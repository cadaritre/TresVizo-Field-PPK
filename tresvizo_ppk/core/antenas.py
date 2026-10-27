"""Antena y altura de cada extremo, como las eligió el usuario (especificación §1.1.1), y la antena que se usó de
verdad, para el informe.

- **Altura** con su método de medida (`model.HEIGHT_METHODS`): vertical a la ARP; vertical a la rosca o al gancho más
  el desplazamiento hasta la ARP (el del case en los Meridian, `meridian_rinex/products.py`; 0 en las antenas cuya
  ARP es la base de la rosca, que son casi todas las del ANTEX: «BAM» en el antenna.gra de la IGS); o inclinada al
  borde de la antena: h_ARP = √(s² − r²) − d, con r y d de la tabla `data/antex/medidas_inclinada.json` (de los
  dibujos acotados de la IGS, con su fuente) o escritos por el usuario.
- **Antena y radomo**: por omisión los de los datos (encabezado RINEX, producto Meridian o catálogo de la red); el
  usuario los puede cambiar por otros del ANTEX o de un `.atx` importado.
- **Aviso** si una antena queda sin modelo de centro de fase: rojo («error») si la línea base pasa de 5 km, amarillo
  si es más corta (en una línea corta con antenas iguales el error se cancela en parte).
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from ..model import (HEIGHT_METHODS, AntennaInfo, AntennaSelection, BaselineSolution, ObservationSet,
                     ProcessingOptions, Station)
from . import antex

UNMODELED_ERROR_M = 5_000.0          # sin modelo de antena y una línea base más larga que esto: aviso rojo
DIMENSIONS_FILE = Path(__file__).resolve().parent / "data" / "antex" / "medidas_inclinada.json"
METHOD_TEXT = {"arp": "vertical a la ARP", "mount": "vertical a la rosca o al gancho", "slant": "inclinada al borde"}


@lru_cache(maxsize=1)
def _dimensions() -> dict:
    try:
        return json.loads(DIMENSIONS_FILE.read_text(encoding="utf-8")).get("antenas", {})
    except (OSError, ValueError):
        return {}


def slant_dimensions(antenna_type: str) -> dict | None:
    """{«radius_m», «offset_m», «mark», «source»} de la tabla para la altura inclinada, o None si no está."""
    return _dimensions().get(antex._norm_type(antenna_type))


def vertical_height(selection: AntennaSelection, *, mount_offset_m: float | None = None) -> float:
    """Altura vertical de la marca a la ARP según el método; ValueError con el motivo si los datos no alcanzan."""
    method = selection.height_method or "arp"
    if method not in HEIGHT_METHODS:
        raise ValueError(f"Método de altura desconocido: «{method}».")
    measured = selection.measured_m
    if measured is None or not math.isfinite(measured) or measured < 0:
        raise ValueError("Escribe la altura medida (en metros, cero o positiva).")
    if method == "arp":
        return measured
    if method == "mount":
        offset = selection.mount_offset_m if selection.mount_offset_m is not None else mount_offset_m
        if offset is None or not math.isfinite(offset) or abs(offset) > 1.0:
            raise ValueError("Escribe el desplazamiento de la rosca o el gancho a la ARP (de −1 a 1 m).")
        return measured + offset
    r, d = selection.slant_radius_m, selection.slant_offset_m
    if r is None or not math.isfinite(r) or not 0 < r < 1.0:
        raise ValueError("Escribe el radio de la antena r (del eje al borde donde se apoya la cinta, de 0 a 1 m).")
    if d is None or not math.isfinite(d) or abs(d) > 1.0:
        raise ValueError("Escribe el desfase vertical d del borde a la ARP (de −1 a 1 m).")
    if measured <= r:
        raise ValueError(f"La altura inclinada ({measured:.3f} m) tiene que ser mayor que el radio de la antena "
                         f"({r:.3f} m).")
    return math.sqrt(measured * measured - r * r) - d


def height_text(selection: AntennaSelection, height_m: float, *, mount_offset_m: float | None = None) -> str:
    """Cómo salió la altura, para el informe."""
    method = selection.height_method or "arp"
    if method == "mount":
        offset = selection.mount_offset_m if selection.mount_offset_m is not None else mount_offset_m
        return (f"vertical a la rosca o al gancho {selection.measured_m:.4f} m + {offset:.4f} m a la ARP = "
                f"{height_m:.4f} m")
    if method == "slant":
        source = f" ({selection.dimensions_source})" if selection.dimensions_source else ""
        return (f"inclinada al borde s = {selection.measured_m:.4f} m, r = {selection.slant_radius_m:.4f} m, "
                f"d = {selection.slant_offset_m:.4f} m{source}: √(s² − r²) − d = {height_m:.4f} m a la ARP")
    return f"vertical a la ARP {height_m:.4f} m"


def default_mount_offset(obs: ObservationSet | None) -> float:
    """Rosca → ARP por omisión: el del case de los Meridian; 0 en las demás (la ARP es la base de la rosca)."""
    if obs is not None and obs.case_offset_m is not None:
        return float(obs.case_offset_m)
    return 0.0


def _override(antenna_type: str, radome: str, selection: AntennaSelection | None) -> tuple[str, str, bool]:
    if selection is None or not (selection.antenna_type or selection.radome):
        return antenna_type, radome or "NONE", False
    kind = selection.antenna_type.strip() or antenna_type
    dome = selection.radome.strip().upper() or (radome or "NONE")
    changed = antex._norm_type(kind) != antex._norm_type(antenna_type) or antex._norm_dome(dome) != \
        antex._norm_dome(radome)
    return kind, dome, changed


def apply_rover(rover: ObservationSet, options: ProcessingOptions, *, use_height: bool = True
                ) -> tuple[ObservationSet, str]:
    """El rover con la antena y la altura elegidas (si las hay) y el texto de cómo salió su altura."""
    selection = options.antennas.get("rover")
    kind, dome, changed = _override(rover.antenna_type, rover.radome, selection)
    sources = dict(rover.field_sources)
    warnings = list(rover.warnings)
    if changed:
        sources["antenna_type"] = sources["radome"] = "elegida por el usuario"
    height = rover.antenna_height_m
    text = f"vertical a la ARP {height:.4f} m"
    if use_height and selection is not None and selection.measured_m is not None:
        mount = default_mount_offset(rover)
        height = vertical_height(selection, mount_offset_m=mount)
        text = height_text(selection, height, mount_offset_m=mount)
        sources["antenna_height_m"] = f"medida por el usuario ({METHOD_TEXT.get(selection.height_method, '')})"
    out = replace(rover, antenna_type=kind, radome=dome, antenna_height_m=height, field_sources=sources,
                  warnings=warnings)
    return out, text


def apply_base(station: Station, obs: ObservationSet, options: ProcessingOptions
               ) -> tuple[Station, ObservationSet, str, list[str]]:
    """La estación y sus observaciones con la antena y la altura elegidas para «base:<clave>» (si las hay)."""
    selection = options.antennas.get(f"base:{station.id}")
    warnings: list[str] = []
    official = bool(station.network and station.official)
    current_type = station.antenna_type if official and station.antenna_type else (obs.antenna_type or
                                                                                   station.antenna_type)
    current_dome = (station.radome if official and station.antenna_type else
                    (obs.radome if obs.antenna_type else station.radome)) or "NONE"
    current_h = station.antenna_height_m if official else (obs.antenna_height_m or station.antenna_height_m)
    text = f"vertical a la ARP {current_h:.4f} m"
    if selection is None:
        return station, obs, text, warnings
    kind, dome, changed = _override(current_type, current_dome, selection)
    height = current_h
    if selection.measured_m is not None:
        mount = default_mount_offset(obs)
        height = vertical_height(selection, mount_offset_m=mount)
        text = height_text(selection, height, mount_offset_m=mount)
    if changed and official:
        warnings.append(f"{station.id}: antena cambiada a mano a {kind} {dome} (el catálogo de la red dice "
                        f"{current_type or '—'} {current_dome}).")
    if official and abs(height - current_h) > 0.0005:
        warnings.append(f"{station.id}: altura de antena escrita a mano, {height:.4f} m (el catálogo de la red dice "
                        f"{current_h:.4f} m).")
    station = replace(station, antenna_type=kind, radome=dome, antenna_height_m=height)
    obs = replace(obs, antenna_type=kind, radome=dome, antenna_height_m=height)
    return station, obs, text, warnings


def needed_keys(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(t, r or "NONE") for t, r in pairs if t]


def info(role: str, antenna_type: str, radome: str, *, height_m: float | None, height_text_: str,
         length_m: float | None, meridian: bool = False) -> AntennaInfo:
    """Qué modelo se usó para una antena (dentro de la sesión de `antex`) y el nivel de su aviso."""
    choice = antex.choose(antenna_type, radome or "NONE", role)
    model = choice.model
    out = AntennaInfo(role=role, antenna_type=antex._norm_type(antenna_type), radome=antex._norm_dome(radome),
                      height_m=height_m, height_text=height_text_, note=choice.note)
    if model is not None:
        out.model_type, out.model_radome, out.source, out.calibrated = model.type, model.radome, model.source, True
        return out
    out.source = "sin modelo"
    long_ = length_m is not None and length_m > UNMODELED_ERROR_M
    out.level = "error" if long_ else "warning"
    name = f"{out.antenna_type} {out.radome}".strip() if out.antenna_type else "sin declarar"
    if meridian:
        what = "la antena del Meridian no tiene calibración publicada"
    else:
        what = f"la antena {name} no está en ningún ANTEX"
    if long_:
        out.note = (f"{role}: {what} y la línea base mide {length_m / 1000:.1f} km: sin modelo de centro de fase "
                    "(PCO/PCV 0) la vertical puede tener un error de varios centímetros. Elige la antena correcta o "
                    "importa su calibración (.atx).")
    else:
        out.note = (f"{role}: {what}: se procesa sin modelo de centro de fase (PCO/PCV 0). En una línea corta con la "
                    "misma antena en los dos extremos el error se cancela en parte; con antenas distintas son "
                    "centímetros en la vertical.")
    return out


def static_infos(rover: ObservationSet, rover_height: str, bases: list[tuple[Station, ObservationSet, str]],
                 baselines: list[BaselineSolution]) -> list[AntennaInfo]:
    lengths = {b.base.id: b.length_m for b in baselines}
    longest = max(lengths.values()) if lengths else None
    out = [info("Rover", rover.antenna_type, rover.radome, height_m=rover.antenna_height_m, height_text_=rover_height,
                length_m=longest, meridian=bool(rover.receiver_model))]
    from .baseline import base_antenna
    for station, obs, text in bases:
        kind, dome, height = base_antenna(station, obs, [])
        out.append(info(f"Base {station.id}", kind, dome, height_m=height, height_text_=text,
                        length_m=lengths.get(station.id), meridian=bool(obs.receiver_model)))
    return out


def warnings_for(infos: list[AntennaInfo]) -> list[str]:
    """Los avisos de las antenas sin modelo (el rojo primero)."""
    return [i.note for i in sorted(infos, key=lambda i: i.level != "error") if i.level in ("error", "warning")]


def own_processor_gaps(infos: list[AntennaInfo], baselines: list[BaselineSolution]) -> list[str]:
    """Avisos si el procesador propio de líneas largas no usó la misma calibración que eligió `antex.choose` (p. ej.
    un ANTEX importado que aún no le llega): se comprueba el PCO de L1 de cada extremo de sus líneas base."""
    if not any(getattr(b, "engine", "") == "TresVizo" for b in baselines):
        return []
    from .larga import antena as larga_antena
    out = []
    roles = {"Rover"} | {f"Base {b.base.id}" for b in baselines if getattr(b, "engine", "") == "TresVizo"}
    for item in infos:
        if item.role not in roles or not item.calibrated:
            continue
        model = antex.choose(item.model_type, item.model_radome).model
        theirs = larga_antena.receiver_antenna(item.model_type, item.model_radome)
        ours = model.frequencies.get("G01") if model else None
        pattern = theirs.patterns.get("G01") if theirs is not None else None
        if ours is None or (pattern is not None and abs(float(pattern.pco[2]) - ours[2]) < 1e-6):
            continue
        out.append(f"{item.role}: el procesador de líneas largas no aplicó la calibración {item.source} de "
                   f"{item.model_type} {item.model_radome} (la de RTKLIB sí): su solución puede diferir en la "
                   "vertical. Compara con la comprobación libre de ionosfera de RTKLIB.")
    return out
