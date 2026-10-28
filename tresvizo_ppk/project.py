"""Proyectos `.tvppk` (§1.4): JSON con rutas relativas al proyecto y la suma SHA-256 de cada archivo de entrada.

Sin interfaz: lo usan la app y cualquier herramienta que quiera volver a procesar un proyecto. La carpeta de trabajo
(RINEX unidos, configuración de RTKLIB, `.pos`, `.stat`, informe) queda junto al proyecto: `<nombre>.trabajo/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .model import (HEIGHT_METHODS, AntennaSelection, CoordinateChoice, Ecef, Enu, Ephemeris, ProcessingOptions,
                    Project, Station)

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
    inputs, hashes = {}, {}
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
            done += 1
            if progress:
                progress(done/total, f"Calculando la huella de {Path(path).name}")
    project.inputs, project.hashes = inputs, hashes


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
            "antennas": {role: asdict(sel) for role, sel in o.antennas.items()}}


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
    options = ProcessingOptions(**{**default.__dict__, **values})
    return options


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


def to_dict(project: Project, folder: Path, app_version: str = "") -> dict:
    return {"formato": FORMAT, "version": FORMAT_VERSION, "app_version": app_version,
            "kind": project.kind, "name": project.name, "number": project.number, "place": project.place, "point_name": project.point_name,
            "point_code": project.point_code, "point_description": project.point_description,
            "pole_height_m": project.pole_height_m, "inputs": project.inputs, "hashes": project.hashes,
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
            "created": _time(project.created), "processed": _time(project.processed)}


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
        c = data.get("coordinates", {})
        return Project(version=version, kind=data["kind"], name=str(data.get("name", "")), folder=Path(folder),
                       inputs=inputs, hashes=hashes, options=_parse_options(data.get("options", {}), folder),
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
                       sigma_enu=_parse_enu(data["sigma_enu"]) if data.get("sigma_enu") else None)
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
