"""Proyectos `.tvppk` (§1.4): JSON con rutas relativas al proyecto y la suma SHA-256 de cada archivo de entrada.

Sin interfaz: lo usan la app y cualquier herramienta que quiera volver a procesar un proyecto. La carpeta de trabajo
(RINEX unidos, configuración de RTKLIB, `.pos`, `.stat`, informe) queda junto al proyecto: `<nombre>.trabajo/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .model import (HEIGHT_METHODS, AntennaSelection, ControlCoordinate, CoordinateChoice, Ecef, Enu, Ephemeris,
                    ProcessingOptions, Project, ProjectTolerances, Station, TimeWindow)

SUFFIX = ".tvppk"
FORMAT = "tresvizo-field-ppk/proyecto"
FORMAT_VERSION = 1
KINDS = ("static", "points")


class ProjectError(Exception):
    """Un problema con el archivo de proyecto que se le explica al usuario (en español)."""


@dataclass(frozen=True)
class InputIssue:
    role: str
    path: Path
    kind: str           # «missing» (no está) o «changed» (su contenido ya no es el que se procesó)

    @property
    def message(self) -> str:
        if self.kind == "missing":
            return f"No se encuentra «{self.path.name}» ({self.path.parent})."
        return f"«{self.path.name}» cambió desde que se guardó el proyecto (su SHA-256 ya no coincide)."


# --- Utilidades -------------------------------------------------------------------------------------------------------

def sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path, folder: Path) -> str:
    """Ruta relativa al proyecto con «/», o absoluta si no hay camino relativo (otra unidad en Windows)."""
    path, folder = Path(path).resolve(), Path(folder).resolve()
    try:
        return PurePosixPath(Path(os.path.relpath(path, folder))).as_posix()
    except ValueError:
        return str(path)


def absolute(stored: str, folder: Path) -> Path:
    candidate = Path(stored)
    if candidate.is_absolute():
        return candidate
    return (Path(folder)/Path(*PurePosixPath(stored).parts)).resolve()


def workdir(project: Project, path: Path | None = None) -> Path:
    """Carpeta de intermedios junto al proyecto (no se crea aquí)."""
    folder = Path(path).parent if path else project.folder
    if folder is None:
        raise ProjectError("Guarda el proyecto antes de procesarlo: la carpeta de trabajo va junto a él.")
    stem = Path(path).stem if path else (project.name or "proyecto")
    return Path(folder)/(stem+".trabajo")


def set_inputs(project: Project, files: dict[str, list[Path]], folder: Path, progress=None, cancel=None) -> None:
    """Rutas relativas y SHA-256 de cada archivo por papel («rover», «base:CHIH», «csv», «precise»…)."""
    folder = Path(folder)
    total = sum(len(v) for v in files.values()) or 1
    done = 0
    inputs, hashes, sizes = {}, {}, {}
    for role, paths in files.items():
        inputs[role] = []
        for path in paths:
            if cancel is not None and cancel.is_set():
                raise ProjectError("Guardado cancelado.")
            key = relative(path, folder)
            inputs[role].append(key)
            if key not in hashes:
                if not Path(path).is_file():
                    raise ProjectError(f"No se encuentra «{Path(path).name}». Revisa los archivos antes de guardar.")
                hashes[key] = sha256(Path(path))
                sizes[key] = Path(path).stat().st_size
            done += 1
            if progress:
                progress(done/total, f"Calculando la huella de {Path(path).name}")
    project.inputs, project.hashes, project.sizes = inputs, hashes, sizes


def resolve_inputs(project: Project) -> dict[str, list[Path]]:
    if project.folder is None:
        raise ProjectError("El proyecto no tiene carpeta.")
    return {role: [absolute(p, project.folder) for p in paths] for role, paths in project.inputs.items()}


def check_inputs(project: Project, progress=None, cancel=None) -> list[InputIssue]:
    """Qué archivos faltan o cambiaron desde que se guardó (compara la SHA-256)."""
    issues, seen = [], set()
    items = [(role, p) for role, paths in project.inputs.items() for p in paths]
    for index, (role, stored) in enumerate(items, 1):
        if cancel is not None and cancel.is_set():
            break
        if stored in seen:
            continue
        seen.add(stored)
        path = absolute(stored, project.folder)
        if progress:
            progress(index/max(1, len(items)), f"Comprobando {path.name}")
        if not path.is_file():
            issues.append(InputIssue(role, path, "missing"))
        elif stored in project.hashes and sha256(path) != project.hashes[stored]:
            issues.append(InputIssue(role, path, "changed"))
    return issues


# --- Serialización ----------------------------------------------------------------------------------------------------

def _time(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ecef(p: Ecef) -> dict:
    return {"x": p.x, "y": p.y, "z": p.z, "frame": p.frame, "epoch": p.epoch}


def _enu(v: Enu) -> dict:
    return {"e": v.e, "n": v.n, "u": v.u}


def _parse_ecef(p: dict) -> Ecef:
    return Ecef(float(p["x"]), float(p["y"]), float(p["z"]), str(p.get("frame", "ITRF2008")),
                float(p.get("epoch", 2010.0)))


def _parse_enu(v: dict) -> Enu:
    return Enu(float(v["e"]), float(v["n"]), float(v["u"]))


def _station(s: Station) -> dict:
    """Todos los campos de la estación: la coordenada lleva su marco y su época (los de la red, o los que eligió el
    usuario para una base escrita a mano), y su origen («official», «manual», «project»…) decide cómo se reabre."""
    return {"id": s.id, "name": s.name, "position": _ecef(s.position),
            "sigma_enu": None if s.sigma_enu is None else _enu(s.sigma_enu),
            "antenna_type": s.antenna_type, "radome": s.radome, "antenna_height_m": s.antenna_height_m,
            "receiver": s.receiver, "source": s.source, "official": s.official, "network": s.network,
            "network_name": s.network_name, "attribution": s.attribution,
            "coordinate_origin": s.coordinate_origin, "approximate": s.approximate}


def _parse_station(d: dict) -> Station:
    sigma = d.get("sigma_enu")
    return Station(id=str(d["id"]), name=str(d.get("name", d["id"])), position=_parse_ecef(d["position"]),
                   sigma_enu=None if sigma is None else _parse_enu(sigma),
                   antenna_type=str(d.get("antenna_type", "")), radome=str(d.get("radome", "NONE")),
                   antenna_height_m=float(d.get("antenna_height_m", 0.0)), receiver=str(d.get("receiver", "")),
                   source=str(d.get("source", "")), official=bool(d.get("official", True)),
                   network=str(d.get("network", "")), network_name=str(d.get("network_name", "")),
                   attribution=str(d.get("attribution", "")),
                   coordinate_origin=str(d.get("coordinate_origin", "official")),
                   approximate=bool(d.get("approximate", False)))


def _options(o: ProcessingOptions, folder: Path) -> dict:
    return {"elevation_mask_deg": o.elevation_mask_deg, "constellations": list(o.constellations),
            "frequencies": o.frequencies, "ionosphere": o.ionosphere, "ephemeris": o.ephemeris.value,
            "precise_files": [relative(p, folder) for p in o.precise_files], "ambiguity_mode": o.ambiguity_mode,
            "combined": o.combined, "min_ratio": o.min_ratio, "interval_s": o.interval_s,
            "antex_files": [relative(p, folder) for p in o.antex_files],
            "antennas": {role: asdict(sel) for role, sel in o.antennas.items()},
            "time_window": None if o.time_window is None else {"start": _time(o.time_window.start),
                                                               "end": _time(o.time_window.end)},
            "excluded_sats": list(o.excluded_sats)}


def _parse_antenna(d) -> AntennaSelection | None:
    if not isinstance(d, dict):
        return None
    known = {f.name for f in fields(AntennaSelection)}
    values = {k: v for k, v in d.items() if k in known}
    try:
        sel = AntennaSelection(**values)
        for name in ("measured_m", "mount_offset_m", "slant_radius_m", "slant_offset_m"):
            value = getattr(sel, name)
            setattr(sel, name, None if value is None else float(value))
        sel.antenna_type, sel.radome = str(sel.antenna_type), str(sel.radome)
        sel.height_method = sel.height_method if sel.height_method in HEIGHT_METHODS else "arp"
        sel.dimensions_source = str(sel.dimensions_source)
    except (TypeError, ValueError):
        return None
    return sel


def _parse_options(d: dict, folder: Path) -> ProcessingOptions:
    default = ProcessingOptions()
    known = {f.name for f in fields(ProcessingOptions)}
    values = {k: v for k, v in d.items() if k in known}
    if "constellations" in values:
        values["constellations"] = tuple(values["constellations"])
    if "ephemeris" in values:
        values["ephemeris"] = Ephemeris(values["ephemeris"])
    if "precise_files" in values:
        values["precise_files"] = [absolute(p, folder) for p in values["precise_files"]]
    if "antex_files" in values:
        values["antex_files"] = [absolute(p, folder) for p in values["antex_files"] if isinstance(p, str)]
    if "antennas" in values:
        raw = values["antennas"] if isinstance(values["antennas"], dict) else {}
        values["antennas"] = {str(k): sel for k, v in raw.items() if (sel := _parse_antenna(v)) is not None}
    if "time_window" in values:
        values["time_window"] = _parse_window(values["time_window"])
    if "excluded_sats" in values:
        values["excluded_sats"] = _parse_excluded(values["excluded_sats"])
    options = ProcessingOptions(**{**default.__dict__, **values})
    return options


def _parse_window(d) -> TimeWindow | None:
    """La ventana del editor de sesión; una ilegible o vacía queda como «toda la sesión»."""
    if not isinstance(d, dict):
        return None
    try:
        start, end = _parse_time(d.get("start")), _parse_time(d.get("end"))
    except (TypeError, ValueError):
        return None
    if start is None and end is None:
        return None
    if start is not None and end is not None and end <= start:
        return None
    return TimeWindow(start, end)


def _parse_excluded(items) -> list[str]:
    from .core import sesion
    from .errors import UserError
    if not isinstance(items, list):
        return []
    try:
        return sesion.normalize_excluded([str(i) for i in items])
    except UserError:
        # un proyecto editado a mano con algo ilegible: se queda lo que sí se entiende
        kept = []
        for item in items:
            try:
                kept += sesion.normalize_excluded([str(item)])
            except UserError:
                continue
        return sesion.normalize_excluded(kept)


def _localization(record) -> dict | None:
    """La localización en el JSON del contrato de las apps (`crs.localizacion.record_to_json`), más su origen."""
    if record is None:
        return None
    from .crs import localizacion
    return {**localizacion.record_to_json(record), "origin": record.origin}


def _parse_localization(d) -> object | None:
    if d is None:
        return None
    from .crs import localizacion
    return localizacion.record_from_json(d, origin=str(d.get("origin", "")) if isinstance(d, dict) else "")


def _tolerances(t: ProjectTolerances | None) -> dict | None:
    return None if t is None else asdict(t)


def _parse_tolerances(d) -> ProjectTolerances | None:
    """Las tolerancias guardadas; una cifra que no se entiende queda en la de omisión (y None apaga el criterio)."""
    if not isinstance(d, dict):
        return None
    t = ProjectTolerances()
    for f in fields(ProjectTolerances):
        if f.name not in d:
            continue
        value = d[f.name]
        if f.name == "order":
            t.order = str(value or "")
        elif f.name == "fail_factor":
            try:
                t.fail_factor = float(value)
            except (TypeError, ValueError):
                pass
        elif value is None:
            setattr(t, f.name, None)
        else:
            try:
                setattr(t, f.name, float(value))
            except (TypeError, ValueError):
                pass
    return t


def _parse_control(items) -> list[ControlCoordinate]:
    out = []
    for d in items if isinstance(items, list) else []:
        if not isinstance(d, dict) or not str(d.get("name", "")).strip():
            continue
        try:
            def number(key):
                return None if d.get(key) is None else float(d[key])
            out.append(ControlCoordinate(str(d["name"]), number("northing_m"), number("easting_m"),
                                         number("elevation_m"), str(d.get("description", ""))))
        except (TypeError, ValueError):
            continue
    return out


def _parse_choices(d) -> dict[str, int]:
    out = {}
    for k, v in (d.items() if isinstance(d, dict) else []):
        try:
            if int(v) >= 0:
                out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def to_dict(project: Project, folder: Path, app_version: str = "") -> dict:
    return {"formato": FORMAT, "version": FORMAT_VERSION, "app_version": app_version,
            "kind": project.kind, "name": project.name, "number": project.number, "place": project.place, "point_name": project.point_name,
            "point_code": project.point_code, "point_description": project.point_description,
            "pole_height_m": project.pole_height_m, "inputs": project.inputs, "hashes": project.hashes,
            "sizes": project.sizes,
            "options": _options(project.options, folder),
            "coordinates": {"crs_id": project.coordinates.crs_id, "zone": project.coordinates.zone,
                            "geoid_id": project.coordinates.geoid_id, "label": project.coordinates.label,
                            "localization": _localization(project.coordinates.localization),
                            "localization_history": [_localization(r)
                                                     for r in project.coordinates.localization_history]},
            "bases": [_station(s) for s in project.bases], "base_kind": project.base_kind,
            "source_project": project.source_project,
            "position": None if project.position is None else _ecef(project.position),
            "sigma_enu": None if project.sigma_enu is None else _enu(project.sigma_enu),
            "created": _time(project.created), "processed": _time(project.processed),
            "tolerances": _tolerances(project.tolerances),
            "control_points": [asdict(c) for c in project.control_points],
            "control_source": project.control_source, "occupation_choices": dict(project.occupation_choices)}


def from_dict(data: dict, folder: Path) -> Project:
    if not isinstance(data, dict) or data.get("formato") != FORMAT:
        raise ProjectError("El archivo no es un proyecto de TresVizo Field PPK.")
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise ProjectError("El proyecto no tiene una versión de formato válida.")
    if version > FORMAT_VERSION:
        raise ProjectError("El proyecto se guardó con una versión más nueva de TresVizo Field PPK. "
                           "Actualiza el programa para abrirlo.")
    if data.get("kind") not in KINDS:
        raise ProjectError("Tipo de proyecto desconocido.")
    try:
        inputs = {str(k): [str(p) for p in v] for k, v in data.get("inputs", {}).items()}
        hashes = {str(k): str(v) for k, v in data.get("hashes", {}).items()}
        sizes = {str(k): int(v) for k, v in (data.get("sizes") or {}).items()
                 if isinstance(v, int) and not isinstance(v, bool) and v >= 0}
        c = data.get("coordinates", {})
        return Project(version=version, kind=data["kind"], name=str(data.get("name", "")), folder=Path(folder),
                       inputs=inputs, hashes=hashes, sizes=sizes, options=_parse_options(data.get("options", {}), folder),
                       coordinates=CoordinateChoice(crs_id=str(c.get("crs_id", CoordinateChoice.crs_id)),
                                                    zone=str(c.get("zone", "")), geoid_id=c.get("geoid_id"),
                                                    label=str(c.get("label", "")),
                                                    localization=_parse_localization(c.get("localization")),
                                                    localization_history=[
                                                        _parse_localization(r)
                                                        for r in c.get("localization_history", []) if r is not None]),
                       pole_height_m=float(data.get("pole_height_m", 0.0)),
                       point_name=str(data.get("point_name", "")), number=str(data.get("number", "")),
                       point_code=str(data.get("point_code", "")),
                       point_description=str(data.get("point_description", "")),
                       place=str(data.get("place", "")),
                       created=_parse_time(data.get("created")), processed=_parse_time(data.get("processed")),
                       bases=[_parse_station(s) for s in data.get("bases", [])],
                       base_kind=str(data.get("base_kind", "rgna")),
                       source_project=str(data.get("source_project", "")),
                       position=_parse_ecef(data["position"]) if data.get("position") else None,
                       sigma_enu=_parse_enu(data["sigma_enu"]) if data.get("sigma_enu") else None,
                       tolerances=_parse_tolerances(data.get("tolerances")),
                       control_points=_parse_control(data.get("control_points")),
                       control_source=str(data.get("control_source", "")),
                       occupation_choices=_parse_choices(data.get("occupation_choices")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectError("El proyecto está dañado o incompleto; no se pudo leer.") from exc


def save(project: Project, path: Path, app_version: str = "") -> Path:
    """Escritura atómica: o queda el proyecto nuevo completo o el anterior intacto."""
    path = Path(path)
    if path.suffix.lower() != SUFFIX:
        path = path.with_suffix(SUFFIX)
    folder = path.parent.resolve()
    project.folder = folder
    project.name = project.name or path.stem
    if project.created is None:
        project.created = datetime.now(timezone.utc)
    text = json.dumps(to_dict(project, folder, app_version), ensure_ascii=False, indent=2)
    folder.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".tvppk-", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text+"\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def load(path: Path) -> Project:
    path = Path(path)
    try:
        if path.stat().st_size > 20_000_000:
            raise ProjectError("El archivo es demasiado grande para ser un proyecto.")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectError(f"No se encuentra el proyecto «{path.name}».") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"No se pudo leer «{path.name}»: no es un proyecto válido.") from exc
    return from_dict(data, path.parent.resolve())


# --- Carpeta de trabajo: tamaño y limpieza ---------------------------------------------------------------------------

# Lo que «Limpiar intermedios» deja en `<nombre>.trabajo/`: los informes (PDF), la bitácora del proceso y las efemérides
# bajadas de internet (se bajaron a petición del usuario; una órbita ultrarrápida se reemplaza en el servidor por la
# final, así que volver a bajarla podría no dar la misma coordenada). Todo lo demás lo rehace «Procesar».
KEEP_DIRS = ("efemerides",)
KEEP_SUFFIXES = (".pdf", ".txt")


def _walk_files(folder: Path):
    for base, dirs, names in os.walk(folder):
        for name in names:
            yield Path(base)/name


def _disk_bytes(paths) -> int:
    """Bytes en disco, contando una sola vez cada archivo con varios enlaces duros."""
    seen, total = set(), 0
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        key = (st.st_dev, st.st_ino)
        if key not in seen:
            seen.add(key)
            total += st.st_size
    return total


def intermediate_files(work: Path) -> list[Path]:
    """Los intermedios de una carpeta de trabajo: lo que se puede borrar y se rehace al volver a procesar."""
    work = Path(work)
    if not work.is_dir():
        return []
    out = []
    for path in _walk_files(work):
        rel = path.relative_to(work)
        if len(rel.parts) == 1:
            if path.suffix.lower() in KEEP_SUFFIXES or path.name.startswith("."):
                continue
        elif rel.parts[0] in KEEP_DIRS:
            continue
        out.append(path)
    return out


@dataclass(frozen=True)
class ProjectSize:
    total: int              # el .tvppk y su carpeta de trabajo, en bytes
    intermediates: int      # de eso, lo que «Limpiar intermedios» puede borrar


def project_size(path: Path) -> ProjectSize:
    path = Path(path)
    work = path.with_name(path.stem+".trabajo")
    files = [path] if path.is_file() else []
    if work.is_dir():
        files += list(_walk_files(work))
    return ProjectSize(_disk_bytes(files), _disk_bytes(intermediate_files(work)))


def clean_intermediates(path: Path, cancel=None) -> int:
    """Borra los intermedios de la carpeta de trabajo del proyecto y devuelve los bytes liberados. Quedan el
    proyecto (con su resultado), los informes, la bitácora y las efemérides bajadas; volver a procesar da la misma
    coordenada."""
    path = Path(path)
    work = path.with_name(path.stem+".trabajo")
    files = intermediate_files(work)
    freed = _disk_bytes(files)
    for f in files:
        if cancel is not None and cancel.is_set():
            break
        try:
            f.unlink()
        except FileNotFoundError:
            pass
    # las carpetas que quedaron vacías, de la más honda a la más alta (las que guardan algo no se pueden borrar)
    for base, _dirs, _names in sorted(os.walk(work), key=lambda item: len(Path(item[0]).parts), reverse=True):
        if Path(base) != work:
            try:
                Path(base).rmdir()
            except OSError:
                pass
    return freed


# --- Buscar los archivos que faltan ----------------------------------------------------------------------------------

MAX_SEARCH_FILES = 300_000


@dataclass
class RelinkResult:
    found: dict[str, Path]          # ruta guardada → dónde se encontró (misma SHA-256)
    missing: list[str]              # rutas guardadas que siguen sin aparecer
    searched: int = 0               # archivos revisados en la carpeta


def missing_inputs(project: Project) -> list[str]:
    """Las rutas guardadas cuyos archivos ya no están (sin calcular huellas: rápido)."""
    out = []
    for paths in project.inputs.values():
        for stored in paths:
            if stored not in out and not absolute(stored, project.folder).is_file():
                out.append(stored)
    return out


def relink(project: Project, folder: Path, progress=None, cancel=None) -> RelinkResult:
    """Busca en `folder` (y sus subcarpetas) los archivos de entrada que faltan, por nombre y, si se renombraron,
    por tamaño; solo acepta el que tiene la misma SHA-256 guardada. Actualiza el proyecto en memoria (rutas,
    huellas, tamaños y las órbitas y ANTEX de las opciones); guardarlo queda para quien llama."""
    if project.folder is None:
        raise ProjectError("El proyecto no tiene carpeta.")
    wanted = missing_inputs(project)
    if not wanted:
        return RelinkResult({}, [])
    folder = Path(folder)
    if not folder.is_dir():
        raise ProjectError(f"No se encuentra la carpeta «{folder}».")
    by_name: dict[str, list[Path]] = {}
    by_size: dict[int, list[Path]] = {}
    sizes_wanted = {project.sizes[k] for k in wanted if k in project.sizes}
    count = 0
    for base, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith(".trabajo")]
        for name in names:
            if cancel is not None and cancel.is_set():
                raise ProjectError("Búsqueda cancelada.")
            count += 1
            if count > MAX_SEARCH_FILES:
                raise ProjectError(f"La carpeta «{folder.name}» tiene demasiados archivos (más de "
                                   f"{MAX_SEARCH_FILES:,}): elige una más cercana a los datos.".replace(",", " "))
            path = Path(base)/name
            by_name.setdefault(name.casefold(), []).append(path)
            if sizes_wanted:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size in sizes_wanted:
                    by_size.setdefault(size, []).append(path)
        if progress and count % 500 == 0:
            progress(0.0, f"Revisando {count} archivos…")
    found: dict[str, Path] = {}
    taken: set[Path] = set()
    for index, stored in enumerate(wanted, 1):
        name = PurePosixPath(stored).name.casefold()
        expected = project.hashes.get(stored)
        size = project.sizes.get(stored)
        candidates = list(by_name.get(name, []))
        if size is not None:
            # primero los del mismo nombre; luego, si se renombró, los del mismo tamaño
            candidates = [c for c in candidates if _size(c) == size]
            candidates += [c for c in by_size.get(size, []) if c not in candidates]
        if progress:
            progress(index/len(wanted), f"Comprobando la huella de {PurePosixPath(stored).name}")
        for candidate in candidates:
            if cancel is not None and cancel.is_set():
                raise ProjectError("Búsqueda cancelada.")
            resolved = candidate.resolve()
            if resolved in taken:
                continue
            if expected is None:
                # un proyecto sin huella de ese archivo: solo por nombre, y solo si no hay dudas
                if len(by_name.get(name, [])) == 1 and candidate in by_name.get(name, []):
                    found[stored] = resolved
                    taken.add(resolved)
                break
            try:
                same = sha256(candidate) == expected
            except OSError:
                continue
            if same:
                found[stored] = resolved
                taken.add(resolved)
                break
    _apply_relink(project, found)
    return RelinkResult(found, [k for k in wanted if k not in found], count)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def _apply_relink(project: Project, found: dict[str, Path]) -> None:
    if not found:
        return
    moved = {absolute(old, project.folder): new for old, new in found.items()}
    keys = {old: relative(new, project.folder) for old, new in found.items()}
    project.inputs = {role: [keys.get(p, p) for p in paths] for role, paths in project.inputs.items()}
    project.hashes = {keys.get(k, k): v for k, v in project.hashes.items()}
    project.sizes = {keys.get(k, k): v for k, v in project.sizes.items()}
    for new in found.values():
        key = relative(new, project.folder)
        project.sizes.setdefault(key, new.stat().st_size)
    o = project.options
    o.precise_files = [moved.get(Path(p).resolve(), Path(p)) for p in o.precise_files]
    o.antex_files = [moved.get(Path(p).resolve(), Path(p)) for p in o.antex_files]


# --- Empaquetar y abrir un paquete -----------------------------------------------------------------------------------

PACKAGE_FORMAT = "tresvizo-field-ppk/paquete"
PACKAGE_VERSION = 1
PACKAGE_SUFFIX = ".zip"
MANIFEST = "paquete.json"
MAX_PACKAGE_BYTES = 50 * 1024**3
_STORED = (".zip", ".gz", ".z", ".pdf", ".png", ".jpg", ".7z", ".bz2", ".xz")
_ROLE_FOLDERS = {"rover": "rover", "csv": "csv-de-campo", "precise": "efemerides", "antex": "antex"}


def _role_folder(role: str) -> str:
    if role in _ROLE_FOLDERS:
        return _ROLE_FOLDERS[role]
    if role.startswith("base:"):
        return "base-"+role.split(":", 1)[1]
    return role.replace(":", "-")


def _safe_part(name: str) -> str:
    bad = '<>:"/\\|?*'
    text = "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip(" .")
    return text or "archivo"


def pack(path: Path, out: Path, app_version: str = "", progress=None, cancel=None) -> Path:
    """ZIP para archivar o entregar el proyecto: el `.tvppk` con rutas relativas a sus entradas, que van dentro
    (RINEX, efemerides, ANTEX importados y CSV de campo; el control y la localización viajan en el propio proyecto),
    más los informes, la bitácora y las efemérides bajadas. Sin intermedios. Cada archivo con su SHA-256 en
    `paquete.json`. Escritura atómica."""
    import zipfile

    path, out = Path(path), Path(out)
    if out.suffix.lower() != PACKAGE_SUFFIX:
        out = out.with_name(out.name+PACKAGE_SUFFIX)
    project = load(path)
    stem = _safe_part(path.stem)
    work = path.with_name(path.stem+".trabajo")
    problems = check_inputs(project, progress=(lambda f, m: progress(0.3*f, m)) if progress else None,
                            cancel=cancel)
    if problems:
        raise ProjectError("No se puede empaquetar: "+" ".join(i.message for i in problems)
                           + " Usa «Buscar los archivos en…» o revisa las entradas.")
    # entradas: datos/<papel>/<nombre>
    plan: list[tuple[Path, str]] = []
    new_key: dict[str, str] = {}
    used: set[str] = set()
    for role, stored_list in project.inputs.items():
        for stored in stored_list:
            if stored in new_key:
                continue
            source = absolute(stored, project.folder)
            name = _safe_part(source.name)
            target = f"datos/{_safe_part(_role_folder(role))}/{name}"
            k = 2
            while target.casefold() in used:
                target = f"datos/{_safe_part(_role_folder(role))}/{Path(name).stem} ({k}){Path(name).suffix}"
                k += 1
            used.add(target.casefold())
            new_key[stored] = target
            plan.append((source, target))
    # lo que se queda de la carpeta de trabajo: informes, bitácora, efemérides bajadas
    if work.is_dir():
        keep = set(intermediate_files(work))
        for f in sorted(_walk_files(work)):
            if f not in keep and not f.name.startswith("."):
                plan.append((f, f"{stem}.trabajo/"+f.relative_to(work).as_posix()))
    total = sum(src.stat().st_size for src, _ in plan) or 1
    if total > MAX_PACKAGE_BYTES:
        raise ProjectError("El proyecto es demasiado grande para empaquetarlo en un ZIP.")
    free = shutil.disk_usage(out.parent if out.parent.exists() else Path.home()).free
    if free < total*1.05+50_000_000:
        raise ProjectError("No hay espacio suficiente en el disco para el paquete.")
    # el proyecto con sus rutas dentro del paquete
    moved = {absolute(old, project.folder): new for old, new in new_key.items()}
    data = to_dict(project, project.folder, app_version)
    data["inputs"] = {role: [new_key[p] for p in paths] for role, paths in project.inputs.items()}
    data["hashes"] = {new_key.get(k, k): v for k, v in project.hashes.items() if k in new_key}
    data["sizes"] = {new_key[k]: absolute(k, project.folder).stat().st_size for k in new_key}
    for key in ("precise_files", "antex_files"):
        values = []
        for p in getattr(project.options, key):
            target = moved.get(Path(p).resolve())
            if target is None:
                raise ProjectError(f"«{Path(p).name}» de las opciones no está entre las entradas del proyecto.")
            values.append(target)
        data["options"][key] = values
    project_text = json.dumps(data, ensure_ascii=False, indent=2)+"\n"
    manifest = {"formato": PACKAGE_FORMAT, "version": PACKAGE_VERSION, "app_version": app_version,
                "proyecto": f"{stem}/{stem}{SUFFIX}", "creado": _time(datetime.now(timezone.utc)), "archivos": []}
    out.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".paquete-", suffix=".zip", dir=out.parent)
    os.close(handle)
    done = 0
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            archive.writestr(f"{stem}/{stem}{SUFFIX}", project_text, zipfile.ZIP_DEFLATED)
            manifest["archivos"].append({"ruta": f"{stem}/{stem}{SUFFIX}",
                                         "sha256": hashlib.sha256(project_text.encode()).hexdigest(),
                                         "bytes": len(project_text.encode())})
            for source, target in plan:
                if cancel is not None and cancel.is_set():
                    raise ProjectError("Empaquetado cancelado.")
                if progress:
                    progress(0.3+0.7*done/total, f"Empaquetando {source.name}")
                method = zipfile.ZIP_STORED if source.suffix.lower() in _STORED else zipfile.ZIP_DEFLATED
                digest = hashlib.sha256()
                info = zipfile.ZipInfo.from_file(source, f"{stem}/{target}", strict_timestamps=False)
                info.compress_type = method
                with open(source, "rb") as src, archive.open(info, "w", force_zip64=True) as dst:
                    while block := src.read(1 << 20):
                        digest.update(block)
                        dst.write(block)
                manifest["archivos"].append({"ruta": f"{stem}/{target}", "sha256": digest.hexdigest(),
                                             "bytes": source.stat().st_size})
                done += source.stat().st_size
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2)+"\n",
                             zipfile.ZIP_DEFLATED)
        os.replace(temporary, out)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    if progress:
        progress(1.0, "Paquete listo.")
    return out


def _member_path(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    if not name or name.startswith(("/", "\\")) or p.is_absolute() or ".." in p.parts or ":" in name \
            or "\\" in name:
        raise ProjectError("El paquete tiene rutas no válidas; no se abre.")
    return p


def unpack(package: Path, folder: Path, progress=None, cancel=None) -> Path:
    """Abre un paquete en `folder`: comprueba su formato, sus rutas y la SHA-256 de cada archivo, y lo deja en una
    carpeta nueva con el nombre del proyecto (sin pisar nada). Devuelve la ruta del `.tvppk`."""
    import zipfile

    package, folder = Path(package), Path(folder)
    try:
        archive = zipfile.ZipFile(package)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ProjectError(f"«{package.name}» no es un paquete de proyecto (ZIP) válido.") from exc
    with archive:
        try:
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
        except (KeyError, ValueError, UnicodeDecodeError) as exc:
            raise ProjectError(f"«{package.name}» no es un paquete de TresVizo Field PPK.") from exc
        if not isinstance(manifest, dict) or manifest.get("formato") != PACKAGE_FORMAT:
            raise ProjectError(f"«{package.name}» no es un paquete de TresVizo Field PPK.")
        if not isinstance(manifest.get("version"), int) or manifest["version"] > PACKAGE_VERSION:
            raise ProjectError("El paquete se hizo con una versión más nueva de TresVizo Field PPK. Actualiza el "
                               "programa para abrirlo.")
        entries = manifest.get("archivos")
        project_member = manifest.get("proyecto")
        if not isinstance(entries, list) or not isinstance(project_member, str):
            raise ProjectError("El paquete está incompleto.")
        expected = {}
        for e in entries:
            if not isinstance(e, dict) or not isinstance(e.get("ruta"), str) or not isinstance(e.get("sha256"), str):
                raise ProjectError("El paquete está incompleto.")
            expected[e["ruta"]] = e["sha256"]
        members = [m for m in archive.infolist() if not m.is_dir() and m.filename != MANIFEST]
        names = {m.filename for m in members}
        if set(expected) != names or project_member not in names:
            raise ProjectError("El paquete está incompleto o tiene archivos de más; no se abre.")
        tops = {_member_path(n).parts[0] for n in names}
        if len(tops) != 1:
            raise ProjectError("El paquete tiene rutas no válidas; no se abre.")
        top = tops.pop()
        total = sum(m.file_size for m in members) or 1
        if total > MAX_PACKAGE_BYTES:
            raise ProjectError("El paquete es demasiado grande.")
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(folder).free < total*1.05+50_000_000:
            raise ProjectError("No hay espacio suficiente en el disco para abrir el paquete.")
        target = folder/top
        k = 2
        while target.exists():
            target = folder/f"{top} ({k})"
            k += 1
        scratch = Path(tempfile.mkdtemp(prefix=".paquete-", dir=folder))
        try:
            done = 0
            for m in members:
                if cancel is not None and cancel.is_set():
                    raise ProjectError("Apertura del paquete cancelada.")
                rel = _member_path(m.filename)
                dest = scratch.joinpath(*rel.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(m) as src, open(dest, "wb") as dst:
                    while block := src.read(1 << 20):
                        digest.update(block)
                        dst.write(block)
                if digest.hexdigest() != expected[m.filename]:
                    raise ProjectError(f"«{rel.name}» del paquete está dañado (su SHA-256 no coincide); no se abre.")
                done += m.file_size
                if progress:
                    progress(done/total, f"Abriendo {rel.name}")
            os.replace(scratch/top, target)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    rel = _member_path(project_member)
    result = target.joinpath(*rel.parts[1:])
    load(result)
    return result
