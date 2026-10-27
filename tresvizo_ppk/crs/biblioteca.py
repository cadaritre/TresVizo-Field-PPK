"""La biblioteca de datos de sistemas y geoides en este equipo: lo incluido, lo descargado y el índice firmado.

Como la `DataLibrary` de las apps: lo incluido de fábrica viene en `tresvizo_ppk/crs/datos/` (WGS 84, Mexico ITRF2008 y
sus UTM 11N a 16N, GGM10, GGM25 y el índice firmado de la última publicación, para que la lista se vea sin conexión);
lo descargado vive en la carpeta de datos del usuario:

    <datos>/indice/datos-index.json        el último índice aceptado (firmado)
    <datos>/sistemas/<id>.json             paquetes de sistemas      + <id>.item.json (su entrada del índice)
    <datos>/geoides/<id>.mvgeoid           geoides                   + <id>.item.json
    <datos>/rejillas/<id>.gsb              rejillas NTv2             + <id>.item.json

Lo instalado guarda su entrada del índice: sigue sirviendo, con su licencia y su atribución, aunque un índice posterior
lo retire. **La red solo se usa cuando el usuario lo pide** (`download_index`, `install`), y cada archivo se comprueba
(tamaño, SHA-256 del índice firmado y, en un geoide, la suma de sus datos) antes de instalarse.
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
from pathlib import Path
from typing import Callable

from ..errors import Cancelled, UserError
from ..version import __version__
from . import indice

PACKAGE_DATA = Path(__file__).resolve().parent / "datos"
BUNDLED_INDEX = PACKAGE_DATA / "datos-index.json"
BUNDLED_GEOIDS = ("ggm10", "ggm25")
FACTORY_FILE = PACKAGE_DATA / "fabrica.json"

KIND_DIRS = {"crs-package": ("sistemas", ".json"), "geoid": ("geoides", ".mvgeoid"), "datum-grid": ("rejillas", ".gsb")}

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
    return data_dir() / KIND_DIRS[kind][0]


def installed_file(kind: str, item_id: str) -> Path:
    folder, ext = KIND_DIRS[kind]
    return data_dir() / folder / f"{item_id}{ext}"


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
    for kind, (folder, ext) in KIND_DIRS.items():
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
