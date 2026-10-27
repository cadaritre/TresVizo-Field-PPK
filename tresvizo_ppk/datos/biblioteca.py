"""La biblioteca de datos descargables en este equipo: el índice firmado, lo instalado y su descarga comprobada.

Es común a todo el programa (sistemas de coordenadas, geoides, rejillas de datum y redes CORS, especificación §1.3 y
§3.0), como la `DataLibrary` de las apps TresVizo Field: **una sola** verificación de la firma Ed25519 del índice
(`indice.py`), una sola descarga con reintentos y reanudación, y una sola comprobación de cada archivo (tamaño y
SHA-256 del índice firmado) antes de instalarlo. Cada clase de datos (`kind`) se registra con su carpeta, su extensión
y su comprobación propia (`register_kind`): los sistemas y geoides los registra `crs.api`; las redes CORS
(`cors-network`) vienen registradas con una comprobación mínima (JSON legible con el mismo id) y el motor puede
registrar la suya. Lo que no tiene clase registrada se lista pero no se instala.

    <datos>/indice/datos-index.json        el último índice aceptado (firmado)
    <datos>/<carpeta de la clase>/<id><ext> lo instalado      + <id>.item.json (su entrada del índice)

Lo instalado guarda su entrada del índice: sigue sirviendo, con su licencia y su atribución, aunque un índice posterior
lo retire. **La red solo se usa cuando el usuario lo pide** (`fetch_index`, `install`).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..errors import Cancelled, UserError
from ..version import __version__
from . import indice

BUNDLED_INDEX = Path(__file__).resolve().parent / "incluido" / "datos-index.json"


@dataclass(frozen=True)
class Kind:
    kind: str
    folder: str
    extension: str
    validate: Callable[[dict, bytes], None]     # (entrada del índice, bytes) → UserError si no sirve
    label: str = ""


def _validate_json(item: dict, data: bytes) -> None:
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UserError(f"{item.get('title', item['id'])}: el archivo no es JSON legible; no se instala.") from None
    if isinstance(doc, dict) and doc.get("id") not in (None, item["id"]):
        raise UserError(f"{item.get('title', item['id'])}: el archivo trae otro id («{doc.get('id')}»); no se instala.")


KINDS: dict[str, Kind] = {
    "cors-network": Kind("cors-network", "redes", ".json", _validate_json, "Red de estaciones de referencia"),
}
_on_change: list[Callable[[], None]] = []


def register_kind(kind: str, folder: str, extension: str, validate: Callable[[dict, bytes], None],
                  label: str = "") -> None:
    """Registra (o reemplaza) cómo se instala y se comprueba una clase de datos del índice."""
    KINDS[kind] = Kind(kind, folder, extension, validate, label)


def on_change(callback: Callable[[], None]) -> None:
    """Para que un módulo vuelva a leer lo instalado tras instalar o borrar (los sistemas y geoides lo usan)."""
    if callback not in _on_change:
        _on_change.append(callback)


def _changed() -> None:
    for callback in list(_on_change):
        callback()


CHUNK = 64 * 1024
TIMEOUT_S = 30
RETRIES = 3

Progress = Callable[[float, str], None] | None

_lock = threading.RLock()
_data_dir: Path | None = None


def default_data_dir() -> Path:
    """La carpeta de datos del usuario según el sistema operativo (se cambia en Ajustes o con TRESVIZO_PPK_DATOS)."""
    env = os.environ.get("TRESVIZO_PPK_DATOS")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "TresVizo Field PPK" / "datos"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TresVizo Field PPK" / "datos"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "tresvizo-field-ppk" / "datos"


def data_dir() -> Path:
    with _lock:
        return _data_dir or default_data_dir()


def set_data_dir(path: str | Path | None) -> None:
    global _data_dir
    with _lock:
        _data_dir = Path(path) if path is not None else None


def kind_dir(kind: str) -> Path:
    return data_dir() / KINDS[kind].folder


def installed_file(kind: str, item_id: str) -> Path:
    k = KINDS[kind]
    return data_dir() / k.folder / f"{item_id}{k.extension}"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- Índice ----------------------------------------------------------------------------------------------------------

def _read_envelope(path: Path) -> dict | None:
    try:
        return indice.verify(path.read_bytes())
    except (OSError, indice.IndexError_):
        return None


def accepted_index() -> dict | None:
    """El índice más nuevo de los aceptados: el descargado o el incluido, el de mayor secuencia."""
    stored = _read_envelope(data_dir() / "indice" / "datos-index.json")
    bundled = _read_envelope(BUNDLED_INDEX)
    candidates = [p for p in (stored, bundled) if p]
    return max(candidates, key=lambda p: p["sequence"]) if candidates else None


def installed_items() -> dict[str, dict]:
    """id → entrada del índice con que se instaló (lo descargado)."""
    out: dict[str, dict] = {}
    for kind, k in KINDS.items():
        folder, ext = k.folder, k.extension
        base = data_dir() / folder
        if not base.is_dir():
            continue
        for meta in sorted(base.glob("*.item.json")):
            item_id = meta.name[: -len(".item.json")]
            if not (base / f"{item_id}{ext}").is_file():
                continue
            try:
                item = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if item.get("id") == item_id and item.get("kind") == kind:
                out[item_id] = item
    return out


# --- Red -------------------------------------------------------------------------------------------------------------

def _request(url: str, headers: dict[str, str]):
    """Abre una dirección (se cambia en las pruebas). Sigue las redirecciones de GitHub a su almacén."""
    req = urllib.request.Request(url, headers={"User-Agent": f"TresVizo-Field-PPK/{__version__}", **headers})
    return urllib.request.urlopen(req, timeout=TIMEOUT_S)


opener: Callable = _request


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def download(url: str, destination: Path, *, expected_bytes: int | None = None, label: str = "",
             progress: Progress = None, cancel=None) -> Path:
    """Baja `url` a `destination` con reintentos y reanudación (archivo `.part` y `Range`)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        _check_cancel(cancel)
        have = part.stat().st_size if part.exists() else 0
        if expected_bytes is not None and have > expected_bytes:
            part.unlink()
            have = 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with opener(url, headers) as response:
                status = getattr(response, "status", 200)
                if have and status != 206:      # el servidor no reanuda: se empieza de cero
                    have = 0
                total = expected_bytes
                if total is None:
                    length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
                    total = (int(length) + have) if length else None
                with open(part, "ab" if have else "wb") as handle:
                    done = have
                    while True:
                        _check_cancel(cancel)
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        if expected_bytes is not None and done > expected_bytes:
                            raise UserError(f"{label or url}: el servidor mandó más bytes de los que dice el "
                                            "catálogo; no se instala.")
                        if progress and total:
                            progress(min(done / total, 1.0), f"Bajando {label}: {_size_text(done)} de "
                                                             f"{_size_text(total)}")
            if expected_bytes is not None and part.stat().st_size != expected_bytes:
                raise ConnectionError("descarga incompleta")
            os.replace(part, destination)
            return destination
        except (Cancelled, UserError):
            raise
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code == 416:      # el rango pedido ya no vale: se empieza de cero
                part.unlink(missing_ok=True)
            elif 400 <= error.code < 500:
                break
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
            last_error = error
        if attempt + 1 < RETRIES:
            time.sleep(min(2 ** attempt, 4))
    raise UserError(_network_message(label or url, last_error))


def _network_message(label: str, error: Exception | None) -> str:
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 404:
            return f"No se encontró {label} en el servidor de datos de TresVizo (error 404)."
        return f"El servidor de datos de TresVizo respondió con un error {error.code} al bajar {label}."
    if isinstance(error, urllib.error.URLError) or isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return (f"No se pudo bajar {label}: no hay conexión con github.com o se cortó. Revisa la conexión y "
                "vuelve a intentarlo; lo ya bajado se reanuda.")
    return f"No se pudo bajar {label}."


def _size_text(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.0f} KB"
    return f"{n} B"


size_text = _size_text


def fetch_index(progress: Progress = None, cancel=None, base_url: str = indice.BASE_URL) -> dict:
    """Baja el índice firmado, lo verifica y lo guarda si su secuencia no es menor que la del que ya se aceptó."""
    url = indice.index_url(base_url)
    with tempfile.TemporaryDirectory() as tmp:
        path = download(url, Path(tmp) / "datos-index.json", label="el catálogo de datos", progress=progress,
                        cancel=cancel)
        data = path.read_bytes()
    try:
        payload = indice.verify(data)
    except indice.IndexError_ as error:
        raise UserError(str(error)) from None
    current = accepted_index()
    if current and payload["sequence"] < current["sequence"]:
        raise UserError(indice.OLDER)
    with _lock:
        _atomic_write(data_dir() / "indice" / "datos-index.json", data)
    return payload


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --- Instalar y borrar ------------------------------------------------------------------------------------------------

def items(kind: str | None = None) -> list[dict]:
    """Las entradas del índice aceptado (todas, o las de una clase)."""
    payload = accepted_index()
    return [i for i in (payload or {}).get("items", []) if kind is None or i["kind"] == kind]


def install(item_id: str, progress: Progress = None, cancel=None) -> dict:
    """Baja e instala una entrada del índice aceptado y lo que pide (`requires`), comprobando cada archivo (tamaño,
    SHA-256 del índice firmado y la comprobación de su clase) antes de dejarlo instalado. Devuelve su entrada."""
    payload = accepted_index()
    if payload is None:
        raise UserError("No hay catálogo de datos: bájalo primero en Sistemas y geoides.")
    by_id = {i["id"]: i for i in payload["items"]}
    if item_id not in by_id:
        raise UserError(f"«{item_id}» no está en el catálogo de datos.")
    order: list[str] = []

    def visit(i: str, depth: int = 0) -> None:
        if depth > 8 or i in order or i not in by_id:
            return
        for r in by_id[i].get("requires", []) or []:
            visit(r, depth + 1)
        order.append(i)

    visit(item_id)
    have = installed_items()
    pending = [i for i in order if i == item_id or i not in have]
    for n, i in enumerate(pending):
        def sub(fraction: float, message: str, n=n) -> None:
            if progress:
                progress((n + fraction) / len(pending), message)
        _install_one(by_id[i], sub, cancel)
    _changed()
    return by_id[item_id]


def _install_one(item: dict, progress, cancel) -> None:
    kind = KINDS.get(item["kind"])
    title = item.get("title", item["id"])
    if kind is None:
        raise UserError(f"{title} es de una clase de datos («{item['kind']}») que esta versión del programa no "
                        "instala. Actualiza el programa.")
    target = installed_file(item["kind"], item["id"])
    tmp = target.with_name(target.name + ".nuevo")
    download(indice.file_url(item), tmp, expected_bytes=int(item["file"]["bytes"]), label=title, progress=progress,
             cancel=cancel)
    try:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if sha256_file(tmp) != item["file"]["sha256"]:
            raise UserError(f"{title}: el archivo bajado no es el del catálogo firmado (SHA-256 distinto); no se "
                            "instala.")
        kind.validate(item, tmp.read_bytes())
        with _lock:
            os.replace(tmp, target)
            _atomic_write(target.with_name(f"{item['id']}.item.json"),
                          (json.dumps(item, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    finally:
        tmp.unlink(missing_ok=True)


def uninstall(item_id: str) -> None:
    """Borra algo descargado. Lo incluido con el programa no está aquí y no se borra."""
    found = False
    for kind in KINDS:
        path = installed_file(kind, item_id)
        for p in (path, path.with_name(f"{item_id}.item.json")):
            if p.exists():
                p.unlink()
                found = True
    if not found:
        raise UserError(f"«{item_id}» no está instalado.")
    _changed()


def read_installed(kind: str, item_id: str) -> bytes | None:
    """Los bytes de algo instalado (ya comprobado al instalarse), o None."""
    path = installed_file(kind, item_id)
    return path.read_bytes() if path.is_file() else None
