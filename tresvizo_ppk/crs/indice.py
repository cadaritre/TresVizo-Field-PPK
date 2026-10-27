"""El índice firmado de datos descargables de TresVizo (geoides, paquetes de sistemas y rejillas de cambio de datum).

Mismo formato y mismas reglas que las apps TresVizo Field (iOS `MeridianVCore/DataIndex/DataIndex.swift`, Android
`DataIndex.kt`) y que la herramienta que lo publica (`tools/geoides/indice.py` del repositorio de iOS):

    sobre    {"format":"tresvizo-signed-index","formatVersion":1,"algorithm":"Ed25519",
              "keyID":"<16 hex>","payload":"<base64>","signature":"<base64, 64 bytes>"}
    firma    Ed25519 sobre  "TresVizo datos-index v1\\n" + bytes del payload
    payload  {"format":"tresvizo-data-index","formatVersion":1,"sequence":N,
              "publishedAt":"…Z","release":"…","items":[…]}

**Nada del índice se lee antes de comprobar la firma.** Un geoide equivocado da cotas equivocadas con aspecto perfecto:
la firma es lo que impide que alguien que controle la red o el alojamiento cuele uno.
"""
from __future__ import annotations

import base64
import binascii
import json
import re

from . import ed25519

ENVELOPE_FORMAT = "tresvizo-signed-index"
PAYLOAD_FORMAT = "tresvizo-data-index"
FORMAT_VERSION = 1
SIGNING_CONTEXT = b"TresVizo datos-index v1\n"
KINDS = ("geoid", "crs-package", "datum-grid")

# La base de las direcciones, en un solo sitio (como `DataRepository.baseURL` de las apps).
BASE_URL = "https://github.com/cadaritre/GNSS-TresVizo/releases/"
INDEX_PATH = "download/datos-indice/datos-index.json"

# Las llaves públicas con que se aceptan índices: keyID → llave Ed25519 en crudo (32 bytes). La misma de las apps
# (`DataTrust.keys` en DataIndex.swift). Para rotarla se añade la nueva junto a la vieja, se publica con la nueva y,
# cuando ya nadie use versiones viejas, se retira la vieja.
TRUSTED_KEYS: dict[str, bytes] = {
    "1737938db9f9f6bc": base64.b64decode("g+oRBNcNxXFuD2cxuLxZCg01T/2/xhvn5wKIjOnaNhs="),
}

_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_HEX = set("0123456789abcdef")


class IndexError_(Exception):
    """El índice no se acepta. El mensaje es el que ve el usuario (los mismos de las apps)."""


NOT_AN_INDEX = "La respuesta del servidor no es un catálogo de datos de TresVizo."
UNSUPPORTED = "El catálogo de datos es de una versión más nueva. Actualiza la app."
UNTRUSTED = "El catálogo de datos no está firmado por TresVizo; no se usa."
OLDER = "El servidor dio un catálogo más viejo que el que ya tienes; se sigue con el tuyo."


def _invalid(detail: str) -> IndexError_:
    return IndexError_(f"El catálogo de datos no es válido: {detail}")


def verify(envelope_data: bytes, trusted: dict[str, bytes] | None = None) -> dict:
    """Comprueba la firma de un sobre y devuelve el payload ya validado (con solo las entradas de clases conocidas)."""
    trusted = TRUSTED_KEYS if trusted is None else trusted
    try:
        envelope = json.loads(envelope_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IndexError_(NOT_AN_INDEX) from None
    if not isinstance(envelope, dict) or envelope.get("format") != ENVELOPE_FORMAT:
        raise IndexError_(NOT_AN_INDEX)
    if envelope.get("formatVersion") != FORMAT_VERSION or envelope.get("algorithm") != "Ed25519":
        raise IndexError_(UNSUPPORTED)
    public = trusted.get(envelope.get("keyID"))
    if public is None:
        raise IndexError_(UNTRUSTED)
    try:
        body = base64.b64decode(envelope.get("payload", ""), validate=True)
        signature = base64.b64decode(envelope.get("signature", ""), validate=True)
    except (binascii.Error, TypeError, ValueError):
        raise IndexError_(NOT_AN_INDEX) from None
    if len(signature) != 64:
        raise IndexError_(NOT_AN_INDEX)
    # Lo mismo para llave desconocida y firma mala: no se le explica a un atacante qué falló.
    if not ed25519.verify(public, SIGNING_CONTEXT + body, signature):
        raise IndexError_(UNTRUSTED)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid(f"no se pudo leer ({error})") from None
    if not isinstance(payload, dict) or payload.get("format") != PAYLOAD_FORMAT:
        raise IndexError_(NOT_AN_INDEX)
    if payload.get("formatVersion") != FORMAT_VERSION:
        raise IndexError_(UNSUPPORTED)
    for key in ("sequence", "publishedAt", "release", "items"):
        if key not in payload:
            raise _invalid(f"falta «{key}»")
    # Una entrada de una clase desconocida (de una versión posterior) se salta sin fallar.
    payload = dict(payload, items=[i for i in payload["items"] if isinstance(i, dict) and i.get("kind") in KINDS])
    validate(payload)
    return payload


def validate(payload: dict) -> None:
    """Las mismas reglas que `indice.validate_payload` del publicador y `DataIndexVerifier.validate` de las apps."""
    sequence = payload.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise _invalid(f"secuencia {sequence}")
    seen: set[str] = set()
    for item in payload["items"]:
        for key in ("kind", "id", "version", "revision", "title", "countries", "authority", "license",
                    "attribution", "bounds", "file"):
            if key not in item:
                raise _invalid(f"{item.get('id')}: falta {key}")
        item_id = item["id"]
        if not isinstance(item_id, str) or not item_id or item_id in seen:
            raise _invalid(f"id repetido o vacío: «{item_id}»")
        seen.add(item_id)
        if not _ID.match(item_id):  # el id es también el nombre del archivo en disco
            raise _invalid(f"id no válido: «{item_id}»")
        f = item["file"]
        digest = f.get("sha256", "") if isinstance(f, dict) else ""
        size = f.get("bytes") if isinstance(f, dict) else None
        if not (isinstance(size, int) and size > 0 and isinstance(digest, str) and len(digest) == 64
                and set(digest) <= _HEX):
            raise _invalid(f"{item_id}: archivo sin tamaño o sin SHA-256")
        path = f.get("path", "")
        parts = path.split("/") if isinstance(path, str) else [""]
        if not path or path.startswith("/") or "://" in path or ".." in parts or "" in parts:
            raise _invalid(f"{item_id}: la ruta tiene que ser relativa a la base")
        b = item["bounds"]
        try:
            ok = -90 <= b["south"] < b["north"] <= 90 and -180 <= b["west"] < b["east"] <= 180
        except (KeyError, TypeError):
            ok = False
        if not ok:
            raise _invalid(f"{item_id}: la caja se sale de la Tierra")
        revision = item["revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise _invalid(f"{item_id}: revisión {revision}")
        if item["kind"] == "geoid":
            g = item.get("geoid")
            if not isinstance(g, dict) or g.get("id") != item_id or len(g.get("dataChecksum", "")) != 64:
                raise _invalid(f"{item_id}: geoide sin su modelo completo")
    ids = {i["id"] for i in payload["items"]}
    for item in payload["items"]:
        for required in item.get("requires", []) or []:
            if required not in ids:
                raise _invalid(f"{item['id']}: pide «{required}», que no está en el catálogo")


def file_url(item: dict, base_url: str = BASE_URL) -> str:
    return base_url + item["file"]["path"]


def index_url(base_url: str = BASE_URL) -> str:
    return base_url + INDEX_PATH
