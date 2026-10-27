"""Modelos de antena (ANTEX igs20): qué calibración se usa para cada antena y el archivo que lee RTKLIB.

De dónde salen las calibraciones de receptor, en orden de preferencia:

1. **los ANTEX que importó el usuario** (una calibración individual de laboratorio, un archivo del NGS…), guardados en
   el proyecto (`ProcessingOptions.antex_files`); ganan sobre todo lo demás;
2. **el ANTEX incluido** (`data/antex/`, ver ORIGEN.md): todas las antenas de satélite y de receptor del ANTEX vigente
   de la IGS y las de receptor del NGS (ngs20.atx) que la IGS no trae, sin tocar una línea;
3. las que traiga un paquete de red CORS instalado (`antennaCalibrations`) que no estén en las anteriores.

Por antena se busca el tipo con su radomo exacto; si no está, el mismo tipo sin radomo («NONE», con aviso); si
tampoco, se procesa sin modelo (PCO/PCV 0) y se dice. La antena de los Meridian (HARXON HA-901A del MeridianV,
AS-ANT3B-OEMSUR del MeridianVI) no está en ningún ANTEX: se procesa sin modelo y el informe lo dice, como el firmware
(«Antenna PCO/PCV not calibrated»), hasta que haya calibración (se podrá importar como `.atx`).

Los ANTEX importados se activan durante un proceso con `session(...)` (lo hace `api.py`), que además dice qué antenas
de receptor van a hacer falta: el archivo para RTKLIB lleva entonces solo esas, y no las 1100 del incluido. RTKLIB
busca la antena con `strstr` (la primera cuyo nombre contiene el pedido); con una sola candidata por nombre no puede
confundir «TRM5800» con «TRM58000.00». Sin sesión el archivo lleva todas, ordenadas por longitud del tipo para que
la búsqueda de RTKLIB encuentre la exacta primero.
"""
from __future__ import annotations

import contextlib
import contextvars
import gzip
import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

DATA_DIR = Path(__file__).resolve().parent / "data" / "antex"

SATELLITE_TYPES = ("BLOCK", "GLONASS", "GALILEO", "BEIDOU", "QZSS", "IRNSS")


class AntexError(ValueError):
    """Un archivo ANTEX que no se puede usar (con el motivo en español, para el usuario)."""


def bundled_antex() -> Path:
    files = sorted(DATA_DIR.glob("igs20_*_tvppk.atx.gz"))
    if not files:
        raise FileNotFoundError("Falta el ANTEX incluido (tresvizo_ppk/core/data/antex).")
    return files[-1]


def antex_name() -> str:
    m = re.search(r"(igs20_\d{4})", bundled_antex().name)
    return m.group(1) if m else bundled_antex().name


@lru_cache(maxsize=1)
def bundled_index() -> dict:
    """El índice del ANTEX incluido (fuentes con su URL, fecha y SHA-256; antenas que vienen del NGS)."""
    path = bundled_antex().with_name(bundled_antex().name.replace(".atx.gz", ".json"))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _network_blocks() -> str:
    try:
        from . import networks
        return "".join(networks.antex_blocks())
    except Exception:
        return ""


# --- Sesión de proceso: ANTEX importados y antenas que hacen falta --------------------------------------------------

@dataclass(frozen=True)
class _Session:
    imported: tuple[Path, ...] = ()
    needed: frozenset[tuple[str, str]] | None = None     # None: todas


_SESSION: contextvars.ContextVar[_Session] = contextvars.ContextVar("antex_session", default=_Session())


@contextlib.contextmanager
def session(imported: Iterable[Path] = (), needed: Iterable[tuple[str, str]] | None = None) -> Iterator[None]:
    """Durante un proceso: los ANTEX importados por el usuario (ganan sobre el incluido) y, si se conocen, las antenas
    de receptor (tipo, radomo) que va a pedir el proceso, para que el archivo de RTKLIB lleve solo esas."""
    keys = None if needed is None else frozenset((_norm_type(t), _norm_dome(r)) for t, r in needed)
    token = _SESSION.set(_Session(tuple(Path(p) for p in imported), keys))
    try:
        yield
    finally:
        _SESSION.reset(token)


def _norm_type(text: str) -> str:
    return re.sub(r"\s+", "", text or "").upper()


def _norm_dome(text: str) -> str:
    return (text or "NONE").strip().upper() or "NONE"


# --- Lectura de ANTEX -------------------------------------------------------------------------------------------------

@dataclass
class AntennaModel:
    type: str                                  # 16 caracteres del ANTEX, sin espacios al final
    radome: str
    frequencies: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # «G01» → PCO (N, E, U) m
    source: str = ""                           # «IGS (igs20_2437)», «NGS (ngs20.atx)», «importado (lab.atx)», «red»
    serial: str = ""                           # número de serie de una calibración individual
    kind: str = ""                             # «bundled», «ngs», «imported», «network»

    @property
    def rtklib_name(self) -> str:
        return f"{self.type} {self.radome}"

    @property
    def key(self) -> tuple[str, str]:
        return self.type.upper(), self.radome.upper()


def iter_blocks(lines: Iterable[str]) -> Iterator[tuple[tuple[str, str] | None, str]]:
    """Los bloques de antena de un ANTEX (después de la cabecera): ((tipo, radomo), texto) de cada antena de
    receptor y (None, texto) de cada antena de satélite. El texto va tal cual, sin tocar una línea."""
    block: list[str] | None = None
    key: tuple[str, str] | None = None
    satellite = False
    for line in lines:
        label = line[60:].strip()
        if label == "START OF ANTENNA":
            block, key, satellite = [line], None, False
            continue
        if block is None:
            continue
        block.append(line)
        if label == "TYPE / SERIAL NO":
            serial = line[20:40].strip()
            satellite = bool(re.match(r"[GRECJIS]\d\d", serial)) or line.startswith(SATELLITE_TYPES)
            if not satellite:
                key = (line[0:16].strip().upper(), line[16:20].strip().upper() or "NONE")
        elif label == "END OF ANTENNA":
            text = "".join(block if block[-1].endswith("\n") else [*block[:-1], block[-1] + "\n"])
            if satellite:
                yield None, text
            elif key is not None:
                yield key, text
            block = None


def _parse_models(lines, source: str = "", kind: str = "") -> dict[tuple[str, str], AntennaModel]:
    models: dict[tuple[str, str], AntennaModel] = {}
    current: AntennaModel | None = None
    freq = ""
    for line in lines:
        label = line[60:].strip()
        if label == "START OF ANTENNA":
            current, freq = None, ""
        elif label == "TYPE / SERIAL NO":
            serial = line[20:40].strip()
            satellite = bool(re.match(r"[GRECJIS]\d\d", serial)) or line.startswith(SATELLITE_TYPES)
            if not satellite:
                current = AntennaModel(type=line[0:16].strip(), radome=line[16:20].strip() or "NONE",
                                       source=source, serial=serial, kind=kind)
        elif label == "START OF FREQUENCY" and current is not None:
            freq = line[3:6].strip()
        elif label == "END OF FREQUENCY":
            freq = ""
        elif label == "NORTH / EAST / UP" and current is not None and freq:
            try:
                n, e, u = (float(line[i:i + 10]) / 1000.0 for i in (0, 10, 20))
            except ValueError:
                continue
            current.frequencies[freq] = (n, e, u)
        elif label == "END OF ANTENNA" and current is not None:
            models.setdefault(current.key, current)
            current = None
    return models


def _source_bundled() -> str:
    return f"IGS ({antex_name()})"


@lru_cache(maxsize=1)
def _bundled_models() -> dict[tuple[str, str], AntennaModel]:
    ngs = {tuple(k) for k in bundled_index().get("receptores_ngs", [])}
    ngs_label = "NGS (ngs20.atx)"
    with gzip.open(bundled_antex(), "rt", encoding="latin-1") as handle:
        models = _parse_models(handle, _source_bundled(), "bundled")
    for key in ngs & set(models):
        models[key].source, models[key].kind = ngs_label, "ngs"
    return models


@lru_cache(maxsize=16)
def _read_imported(path: str, size: int, mtime: float) -> tuple[dict[tuple[str, str], AntennaModel],
                                                                  dict[tuple[str, str], str]]:
    raw = Path(path).read_bytes()
    text = raw.decode("latin-1")
    head, sep, body = text.partition("END OF HEADER")
    if not sep or "ANTEX VERSION / SYST" not in head:
        raise AntexError(f"«{Path(path).name}» no es un archivo ANTEX (falta la cabecera «ANTEX VERSION / SYST» o "
                         "«END OF HEADER»).")
    for line in head.splitlines():
        if line[60:].strip() == "PCV TYPE / REFANT" and line[0:1].upper() == "R":
            raise AntexError(f"«{Path(path).name}» trae calibraciones relativas (PCV TYPE «R»): con el ANTEX igs20, "
                             "que es absoluto, se necesita una calibración absoluta («A»).")
    lines = body.splitlines(keepends=True)[1:]
    name = Path(path).name
    models = _parse_models(lines, f"importado ({name})", "imported")
    blocks = {key: block for key, block in iter_blocks(lines) if key is not None}
    if not models:
        raise AntexError(f"«{name}» no trae ninguna antena de receptor.")
    empty = sorted(" ".join(k) for k, m in models.items() if not m.frequencies)
    if empty:
        raise AntexError(f"«{name}»: la antena {empty[0]} no trae ningún desfase (NORTH / EAST / UP).")
    return models, blocks


def read_antex_file(path: Path) -> list[AntennaModel]:
    """Las antenas de receptor de un ANTEX que importa el usuario; AntexError con el motivo si no sirve."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError as error:
        raise AntexError(f"No se puede leer «{path.name}»: {error.strerror or error}.") from error
    return list(_read_imported(str(path), stat.st_size, stat.st_mtime)[0].values())


def _imported(paths: Iterable[Path] | None = None):
    models: dict[tuple[str, str], AntennaModel] = {}
    blocks: dict[tuple[str, str], str] = {}
    for path in (_SESSION.get().imported if paths is None else paths):
        path = Path(path)
        try:
            stat = path.stat()
            m, b = _read_imported(str(path), stat.st_size, stat.st_mtime)
        except (OSError, AntexError):
            continue                          # la interfaz ya lo validó al importarlo; aquí no se cae el proceso
        for key in m:
            models.setdefault(key, m[key])
            blocks.setdefault(key, b[key])
    return models, blocks


def _network_models() -> dict[tuple[str, str], AntennaModel]:
    return _parse_models(_network_blocks().splitlines(keepends=True), "paquete de red", "network")


def _receiver_models(imported: Iterable[Path] | None = None) -> dict[tuple[str, str], AntennaModel]:
    models = dict(_imported(imported)[0])
    for key, model in _bundled_models().items():
        models.setdefault(key, model)
    for key, model in _network_models().items():
        models.setdefault(key, model)
    return models


def catalog(imported: Iterable[Path] | None = None) -> list[AntennaModel]:
    """Todas las antenas de receptor con modelo (importadas, incluidas y de paquetes de red), por tipo y radomo."""
    return sorted(_receiver_models(imported).values(), key=lambda m: (m.type, m.radome != "NONE", m.radome))


def receiver_block(antenna_type: str, radome: str = "NONE") -> str | None:
    """El bloque ANTEX tal cual (PCO y PCV completas) de la antena de receptor que eligió `choose`: importado, incluido
    o de un paquete de red, en ese orden. Para el procesador de líneas largas."""
    key = (_norm_type(antenna_type), _norm_dome(radome))
    imported = _imported()[1]
    if key in imported:
        return imported[key]
    found = _bundled_blocks(frozenset({key}))[1].get(key)
    if found:
        return found
    for k, block in iter_blocks(_network_blocks().splitlines(keepends=True)):
        if k == key:
            return block
    return None


# --- Elección -------------------------------------------------------------------------------------------------------

@dataclass
class AntennaChoice:
    model: AntennaModel | None
    requested: str
    note: str                                  # qué se usó, para la bitácora y el informe

    @property
    def rtklib_name(self) -> str:
        return self.model.rtklib_name if self.model else ""


def choose(antenna_type: str, radome: str = "NONE", who: str = "") -> AntennaChoice:
    """Calibración para una antena: exacta (tipo + radomo), o sin radomo («NONE») con aviso, o ninguna. Busca en los
    ANTEX importados de la sesión, luego en el incluido y luego en los paquetes de red."""
    kind = _norm_type(antenna_type)
    dome = _norm_dome(radome)
    label = f"{who}: " if who else ""
    requested = f"{kind} {dome}".strip()
    if not kind:
        return AntennaChoice(None, requested, f"{label}antena sin declarar: sin modelo de centro de fase.")
    models = _receiver_models()
    exact = models.get((kind, dome))
    if exact:
        return AntennaChoice(exact, requested, f"{label}{exact.type} {exact.radome}, calibración {exact.source}.")
    bare = models.get((kind, "NONE"))
    if bare:
        return AntennaChoice(bare, requested, f"{label}{kind} con radomo {dome} no está en ningún ANTEX: se usa "
                                              f"{kind} NONE, calibración {bare.source} (diferencia típica de 1 a 3 mm "
                                              "en la vertical).")
    return AntennaChoice(None, requested, f"{label}{kind} {dome} no está en ningún ANTEX: sin modelo de centro de "
                                          "fase (PCO/PCV 0).")


# --- Archivo para RTKLIB --------------------------------------------------------------------------------------------

def _receiver_order(key: tuple[str, str]) -> tuple[int, str, str]:
    return len(key[0]), key[0], key[1]


@lru_cache(maxsize=4)
def _bundled_blocks(needed: frozenset[tuple[str, str]] | None) -> tuple[str, dict[tuple[str, str], str]]:
    """(cabecera y antenas de satélite, bloques de receptor pedidos o todos) del ANTEX incluido."""
    head: list[str] = []
    satellites: list[str] = []
    receivers: dict[tuple[str, str], str] = {}
    with gzip.open(bundled_antex(), "rt", encoding="latin-1") as handle:
        for line in handle:
            head.append(line)
            if line[60:].strip() == "END OF HEADER":
                break
        for key, block in iter_blocks(handle):
            if key is None:
                satellites.append(block)
            elif needed is None or key in needed:
                receivers.setdefault(key, block)
    return "".join(head) + "".join(satellites), receivers


def _wanted_keys(needed: frozenset[tuple[str, str]], imported: dict, extra: dict) -> frozenset[tuple[str, str]]:
    """Para cada antena pedida, la que `choose` elegiría: exacta o sin radomo."""
    known = set(imported) | set(_bundled_models()) | set(extra)
    out = set()
    for kind, dome in needed:
        if (kind, dome) in known:
            out.add((kind, dome))
        elif (kind, "NONE") in known:
            out.add((kind, "NONE"))
    return frozenset(out)


def ensure_antex(workdir: Path) -> Path:
    """Deja en la carpeta de trabajo el ANTEX que lee RTKLIB (no lee .gz) y devuelve su ruta: la cabecera y las antenas
    de satélite del incluido y las de receptor (las importadas de la sesión ganan; luego las incluidas y las de los
    paquetes de red). Con una sesión que dice qué antenas hacen falta, solo esas."""
    state = _SESSION.get()
    imported_models, imported_blocks = _imported()
    extra = {k: b for k, b in iter_blocks(_network_blocks().splitlines(keepends=True)) if k is not None}
    needed = None if state.needed is None else _wanted_keys(state.needed, imported_blocks, extra)
    source = bundled_antex()
    stat = source.stat()
    digest = hashlib.sha256()
    digest.update(f"{source.name}|{stat.st_size}|{stat.st_mtime_ns}|".encode())
    digest.update(repr(sorted(needed) if needed is not None else None).encode())
    for key in sorted(imported_blocks):
        digest.update(repr(key).encode() + imported_blocks[key].encode("latin-1", "replace"))
    for key in sorted(extra):
        digest.update(repr(key).encode() + extra[key].encode("latin-1", "replace"))
    signature = digest.hexdigest()
    suffix = "_proyecto" if imported_blocks or needed is not None else ("_redes" if extra else "")
    target = workdir / (source.name[:-7] + suffix + ".atx")
    stamp = target.with_name(target.name + ".firma")
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        if target.is_file() and stamp.read_text(encoding="ascii").strip() == signature:
            return target
    except OSError:
        pass
    head, receivers = _bundled_blocks(None if needed is None else needed - set(imported_blocks))
    chosen: dict[tuple[str, str], str] = {}
    for pool in (imported_blocks, receivers, extra):
        for key, block in pool.items():
            if (needed is None or key in needed) and key not in chosen:
                chosen[key] = block
    text = head + "".join(chosen[k] for k in sorted(chosen, key=_receiver_order))
    target.write_text(text, encoding="latin-1")
    stamp.write_text(signature + "\n", encoding="ascii")
    return target
