"""Tramos de una grabación larga: encontrarlos, ordenarlos y unirlos antes de convertir (GNS2-01).

Al llegar a 64 MiB el receptor cierra la sesión y sigue en otra (rotación). Cada tramo es un archivo
con su propio registro de metadatos en el byte 0 (`$PTVZ,META,…`, include/session_metadata.h del
firmware) con `session_id`, `segment` (1, 2, 3…) y `previous_session_id` (el tramo anterior; null en el
primero). Ningún byte se pierde en la costura: el tramo siguiente sigue exactamente donde acabó el
anterior. Desde la auditoría profesional 2 (21-10-2026) el firmware corta además al terminar una época
de observación; los de antes cortaban por bytes, a media trama, y por eso convertir cada tramo por
separado perdía la trama partida.

Unir es quitar el registro de metadatos de cada tramo que no es el primero y pegar el resto: sale el
flujo original byte a byte, con el registro del primero como cabecera. Así se recupera también la trama
partida de las grabaciones de firmwares anteriores.

Dónde se buscan los tramos hermanos de un archivo:
- en su misma carpeta (lo que guardan las apps y el panel: un archivo por tramo);
- y, si el archivo es `stream.bin`/`stream.part` dentro de una carpeta con el identificador de la sesión
  (la tarjeta copiada tal cual, `sessions/<id>/stream.bin`), en las carpetas vecinas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .framing import META_PREFIX, find_session_metadata

_SESSION_ID = re.compile(r"^[0-9a-f]{24}$")
# El registro de metadatos cabe en 1 KiB (session_metadata.h, kMaxLineBytes); se lee un poco más.
_HEAD_BYTES = 4096
_SUFFIXES = {".bin", ".part"}


@dataclass(frozen=True)
class Segment:
    path: Path
    session_id: str
    number: int
    previous: str | None


@dataclass
class Chain:
    """Los tramos de una grabación, del primero al último, y lo que no cuadró."""
    segments: list[Segment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def joined(self) -> bool:
        return len(self.segments) > 1


def read_segment(path: Path) -> Segment | None:
    """El tramo que dice el registro de metadatos del archivo, o None si no lo trae (firmware anterior a
    1.0.0 o archivo que no es una grabación)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError:
        return None
    if not head.startswith(META_PREFIX):
        return None
    meta = find_session_metadata(head, _HEAD_BYTES)
    if not meta:
        return None
    session = meta.get("session_id")
    if not isinstance(session, str) or not _SESSION_ID.match(session):
        return None
    number = meta.get("segment")
    number = number if isinstance(number, int) and not isinstance(number, bool) and number >= 1 else 1
    previous = meta.get("previous_session_id")
    previous = previous if isinstance(previous, str) and _SESSION_ID.match(previous) and number > 1 else None
    return Segment(path=path, session_id=session, number=number, previous=previous)


def _candidates(path: Path) -> list[Path]:
    folder = path.parent
    found = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _SUFFIXES and p != path]
    if _SESSION_ID.match(folder.name) and path.stem == "stream":
        for sibling in folder.parent.iterdir():
            if sibling.is_dir() and sibling != folder and _SESSION_ID.match(sibling.name):
                for name in ("stream.bin", "stream.part"):
                    candidate = sibling / name
                    if candidate.is_file():
                        found.append(candidate)
    return sorted(found)


def find_chain(path: Path) -> Chain:
    """La grabación entera a la que pertenece `path`: sus tramos hermanos, en orden, siguiendo
    `previous_session_id` hacia atrás y hacia delante. Un archivo sin metadatos, o sin hermanos, es
    una cadena de un solo tramo."""
    path = Path(path)
    chain = Chain()
    own = read_segment(path)
    if own is None:
        chain.segments.append(Segment(path=path, session_id="", number=1, previous=None))
        return chain
    by_id: dict[str, Segment] = {own.session_id: own}
    by_previous: dict[str, list[Segment]] = {}
    for candidate in _candidates(path):
        segment = read_segment(candidate)
        if segment is None:
            continue
        # Dos archivos de la misma sesión (p. ej. el .part y el .bin, o una copia): vale el completo.
        known = by_id.get(segment.session_id)
        if known is not None:
            if known is not own and known.path.suffix.lower() == ".part" and segment.path.suffix.lower() == ".bin":
                by_id[segment.session_id] = segment
            continue
        by_id[segment.session_id] = segment
    for segment in by_id.values():
        if segment.previous:
            by_previous.setdefault(segment.previous, []).append(segment)
    # Hacia atrás hasta el primero (o hasta un tramo que falta).
    first = own
    seen = {own.session_id}
    while first.previous:
        before = by_id.get(first.previous)
        if before is None or before.session_id in seen:
            chain.warnings.append(f"Falta el tramo {first.number - 1} de la grabación (sesión {first.previous}): "
                                  f"se convierte desde el tramo {first.number}.")
            break
        if before.number != first.number - 1:
            chain.warnings.append(f"Los números de tramo no son seguidos ({before.number} → {first.number}).")
        seen.add(before.session_id)
        first = before
    ordered = [first]
    in_chain = {first.session_id}
    # Hacia delante: el tramo cuyo anterior es el último que se tiene.
    while True:
        following = [s for s in by_previous.get(ordered[-1].session_id, []) if s.session_id not in in_chain]
        if not following:
            break
        following.sort(key=lambda s: (s is not own, s.path.name))
        if len(following) > 1:
            chain.warnings.append(f"Hay {len(following)} tramos que dicen seguir a la sesión "
                                  f"{ordered[-1].session_id}; se usa {following[0].path.name}.")
        nxt = following[0]
        if nxt.number != ordered[-1].number + 1:
            chain.warnings.append(f"Los números de tramo no son seguidos ({ordered[-1].number} → {nxt.number}).")
        in_chain.add(nxt.session_id)
        ordered.append(nxt)
    chain.segments = ordered
    for segment in ordered[:-1]:
        if segment.path.suffix.lower() == ".part":
            chain.warnings.append(f"El tramo {segment.number} ({segment.path.name}) está incompleto (.part): "
                                  "la unión puede tener un hueco ahí.")
    return chain


def chain_from_files(paths: list[Path]) -> Chain:
    """Los archivos que indica el usuario, ordenados como tramos de una misma grabación."""
    segments = []
    chain = Chain()
    for path in paths:
        segment = read_segment(Path(path))
        if segment is None:
            chain.warnings.append(f"{Path(path).name} no trae metadatos de tramo: se une en el orden dado.")
            segment = Segment(path=Path(path), session_id="", number=len(segments) + 1, previous=None)
        segments.append(segment)
    if all(s.session_id for s in segments):
        by_previous = {s.previous: s for s in segments if s.previous}
        starts = [s for s in segments if not s.previous or s.previous not in {x.session_id for x in segments}]
        if len(starts) == 1:
            ordered = [starts[0]]
            while ordered[-1].session_id in by_previous and len(ordered) < len(segments):
                ordered.append(by_previous[ordered[-1].session_id])
            if len(ordered) == len(segments):
                segments = ordered
            else:
                chain.warnings.append("Los archivos no forman una sola cadena de tramos: se unen por número de tramo.")
                segments.sort(key=lambda s: s.number)
        else:
            chain.warnings.append("Los archivos no forman una sola cadena de tramos: se unen por número de tramo.")
            segments.sort(key=lambda s: s.number)
    chain.segments = segments
    return chain


def strip_metadata(data: bytes) -> bytes:
    """El flujo sin el registro de metadatos del byte 0 (si lo tiene)."""
    if not data.startswith(META_PREFIX):
        return data
    end = data.find(b"\n")
    return data[end + 1:] if end >= 0 else b""


def join(chain: Chain) -> bytes:
    """Los tramos unidos: el primero entero y de los demás todo menos su registro de metadatos."""
    parts = []
    for index, segment in enumerate(chain.segments):
        data = segment.path.read_bytes()
        parts.append(data if index == 0 else strip_metadata(data))
    return b"".join(parts)
