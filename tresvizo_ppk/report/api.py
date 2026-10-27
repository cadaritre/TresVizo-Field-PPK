"""Informes PDF de posproceso (especificación §5 y §8): lo que llama la interfaz.

- `static_report(result, project, user, out_pdf)`: informe de posproceso de línea base (estático, §5.1).
- `points_report(result, project, user, out_pdf)`: informe de PPK de la jornada (§5.2).

Las coordenadas salen en el sistema, datum y geoide del proyecto (`project.coordinates`, con `crs.api.transform`) y en
el marco oficial. Las gráficas se arman con lo que escribió RTKLIB (`.pos` y `.stat` de cada solución). Un error que el
usuario puede corregir es `UserError`; el PDF se escribe entero o no se escribe (archivo temporal y renombrado).
"""
from __future__ import annotations

from pathlib import Path

from ..errors import UserError
from ..model import PointsResult, Project, StaticResult, UserProfile
from .estatico import static_report as _static
from .puntos import points_report as _points


def _check_out(out_pdf: str | Path) -> Path:
    out = Path(out_pdf)
    if out.suffix.lower() != ".pdf":
        out = out.with_suffix(".pdf")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise UserError(f"No se puede escribir en «{out.parent}»: {error.strerror or error}.") from None
    return out


def static_report(result: StaticResult, project: Project, user: UserProfile, out_pdf: str | Path) -> Path:
    out = _check_out(out_pdf)
    try:
        return _static(result, project, user, out)
    except PermissionError:
        raise UserError(f"No se pudo guardar «{out.name}»: el archivo está abierto en otro programa o la carpeta no "
                        "permite escribir.") from None


def points_report(result: PointsResult, project: Project, user: UserProfile, out_pdf: str | Path) -> Path:
    out = _check_out(out_pdf)
    try:
        return _points(result, project, user, out)
    except PermissionError:
        raise UserError(f"No se pudo guardar «{out.name}»: el archivo está abierto en otro programa o la carpeta no "
                        "permite escribir.") from None
