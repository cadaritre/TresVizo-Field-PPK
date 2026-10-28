"""Estático de varios puntos (red): lo que el asistente guarda y valida, sin ventanas (como `wizard.StaticJob`).

Cada receptor trae su grabación (o RINEX) y, si midió varios puntos, el CSV de TresVizo Field con sus ocupaciones;
sin CSV, la grabación entera es un punto con su nombre y su altura. Las estaciones son las de las redes instaladas
(hasta `MAX_NETWORK_BASES`), cada una fija, ponderada o de comprobación en el ajuste.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from tresvizo_ppk.model import (CoordinateChoice, ControlCoordinate, Ecef, NetworkFixChoice, NetworkOptions,
                                NetworkRover, ProcessingOptions, Project, ProjectTolerances, Station)

from app.wizard import (BASE_KINDS, ROVER_KINDS, BaseEntry, Issue, _files_issues, _height_issues, _name_issues,
                        _text_issues, antenna_issues, classify, default_tolerances, errors, number_issues,
                        options_issues, parse_number, quality_from_project, quality_to_project, tolerance_issues)

MAX_NETWORK_BASES = 8
MAX_ROVERS = 12
FIX_LABELS = {'fixed': 'Fija', 'weighted': 'Ponderada', 'check': 'Comprobación'}


@dataclass
class RoverEntry:
    key: str
    files: list[Path] = field(default_factory=list)
    csv: list[Path] = field(default_factory=list)        # el CSV de TresVizo Field de sus ocupaciones (o ninguno)
    point_name: str = ''                                 # sin CSV: el punto que ocupó toda la grabación
    pole_height: str = ''                                # sin CSV: altura del bastón o trípode (o a la ARP)
    code: str = ''
    description: str = ''
    label: str = ''
    # De la grabación leída (no se guardan):
    marker: str = ''
    position: Ecef | None = None

    @property
    def uses_csv(self) -> bool:
        return bool(self.csv)

    def pole_height_m(self) -> float | None:
        return None if self.uses_csv or not self.pole_height.strip() else parse_number(self.pole_height)


@dataclass
class NetworkJob:
    rovers: list[RoverEntry] = field(default_factory=lambda: [RoverEntry('R1')])
    bases: list[BaseEntry] = field(default_factory=list)
    options: ProcessingOptions = field(default_factory=ProcessingOptions)
    coordinates: CoordinateChoice = field(default_factory=CoordinateChoice)
    precise_download: bool = False
    number: str = ''
    place: str = ''
    tolerances: ProjectTolerances = field(default_factory=lambda: default_tolerances('network'))
    control_points: list[ControlCoordinate] = field(default_factory=list)
    control_source: str = ''
    fix: list[NetworkFixChoice] = field(default_factory=list)
    network_options: NetworkOptions = field(default_factory=NetworkOptions)
    line_choices: dict[str, str] = field(default_factory=dict)

    STEPS = ('Receptores', 'Estaciones de referencia', 'Ajustes', 'Proceso', 'Red')
    QUALITY_KIND = 'network'               # tolerancias del estático más el cierre de bucle

    # --- Receptores ---------------------------------------------------------------------------------------------

    def new_rover(self) -> RoverEntry:
        used = {r.key for r in self.rovers}
        n = 1
        while f'R{n}' in used:
            n += 1
        entry = RoverEntry(f'R{n}')
        self.rovers.append(entry)
        return entry

    def point_names(self) -> list[str]:
        return [r.point_name.strip() for r in self.rovers if not r.uses_csv and r.point_name.strip()]

    # --- Fijas ----------------------------------------------------------------------------------------------------

    def fix_choices(self) -> list[NetworkFixChoice]:
        """Una por estación elegida, en su orden; las nuevas, fijas en planta y cota."""
        known = {f.point_id: f for f in self.fix}
        self.fix = [known.get(s.id) or NetworkFixChoice(s.id) for s in self.stations()]
        return self.fix

    def set_fix(self, point_id: str, mode: str | None = None, horizontal: bool | None = None,
                vertical: bool | None = None) -> None:
        for i, f in enumerate(self.fix_choices()):
            if f.point_id == point_id:
                self.fix[i] = replace(f, mode=mode if mode is not None else f.mode,
                                      horizontal=f.horizontal if horizontal is None else horizontal,
                                      vertical=f.vertical if vertical is None else vertical)

    # --- Validación -----------------------------------------------------------------------------------------------

    def step_issues(self, step: int) -> list[Issue]:
        if step == 0:
            found: list[Issue] = []
            if not self.rovers:
                return [Issue('rovers', 'Agrega al menos un receptor con su grabación.')]
            if len(self.rovers) > MAX_ROVERS:
                found.append(Issue('rovers', f'Usa como máximo {MAX_ROVERS} receptores.'))
            for r in self.rovers:
                key = f'rover:{r.key}'
                found += _files_issues(key, r.files, f'la grabación del receptor {r.key} o su RINEX', ROVER_KINDS)
                kinds = {classify(p) for p in r.files}
                if 'recording' in kinds and len(kinds - {'recording'}) > 0:
                    found.append(Issue(key, f'{r.key}: mezclaste una grabación del receptor con archivos RINEX.'))
                elif r.files and 'recording' not in kinds and not kinds & {'obs', 'zip'}:
                    found.append(Issue(key, f'{r.key}: falta el archivo de observaciones (RINEX «o»).'))
                if len(r.csv) > 1:
                    found.append(Issue(key, f'{r.key}: un solo CSV de TresVizo Field por receptor.'))
                elif r.csv and classify(r.csv[0]) != 'csv':
                    found.append(Issue(key, f'{r.key}: «{r.csv[0].name}» no es un CSV.'))
                if not r.uses_csv:
                    found += _name_issues(key, r.point_name, f'del punto del receptor {r.key}')
                    found += _height_issues(key, r.pole_height, f'del bastón o trípode del receptor {r.key}')
                found += _text_issues(key, r.code, 'El código', 40) + \
                    _text_issues(key, r.description, 'La descripción', 200) + \
                    antenna_issues(self.options, f'rover:{r.key}', key)
            return found
        if step == 1:
            found = []
            if not self.bases:
                return [Issue('bases', 'Agrega al menos una estación de referencia.')]
            if len(self.bases) > MAX_NETWORK_BASES:
                found.append(Issue('bases', f'Usa como máximo {MAX_NETWORK_BASES} estaciones.'))
            seen = set()
            points = {n.casefold() for n in self.point_names()}
            for index, base in enumerate(self.bases):
                key = f'base{index}'
                found += _files_issues(key, base.files, 'los RINEX de la estación', BASE_KINDS)
                if base.station is None:
                    found.append(Issue(key, 'Elige la estación de las redes instaladas.'))
                    continue
                if base.station.id in seen:
                    found.append(Issue(key, f'La estación {base.station.id} está repetida.'))
                seen.add(base.station.id)
                if base.station.id.casefold() in points:
                    found.append(Issue(key, f'Un punto nuevo se llama como la estación {base.station.id}: cámbiale '
                                            'el nombre al punto.'))
                found += _name_issues(key, base.name or base.station.name, 'de la estación', 60)
            fixed = [f for f in self.fix_choices() if f.mode != 'check']
            if self.bases and not fixed:
                found.append(Issue('bases', 'Ninguna estación es fija ni ponderada: la red saldrá solo del ajuste '
                                            'mínimamente restringido.', 'warning'))
            if len(self.bases) == 1:
                found.append(Issue('bases', 'Con una sola estación el ajuste no comprueba el control: con dos o más '
                                            'se ve si sus coordenadas concuerdan con las líneas.', 'warning'))
            return found
        if step == 2:
            return (options_issues(self.options, self.precise_download) + number_issues(self.number, self.place)
                    + tolerance_issues(self.tolerances))
        return []

    def first_invalid_step(self) -> int | None:
        for step in range(3):
            if errors(self.step_issues(step)):
                return step
        return None

    # --- Proyecto -------------------------------------------------------------------------------------------------

    def stations(self) -> list[Station]:
        return [replace(b.station, name=(b.name.strip() or b.station.name)) for b in self.bases if b.station]

    def files(self) -> dict[str, list[Path]]:
        files: dict[str, list[Path]] = {}
        for r in self.rovers:
            files[f'rover:{r.key}'] = list(r.files)
            if r.csv:
                files[f'csv:{r.key}'] = list(r.csv)
        for base in self.bases:
            if base.station:
                files['base:' + base.station.id] = list(base.files)
        if self.options.precise_files:
            files['precise'] = list(self.options.precise_files)
        if self.options.antex_files:
            files['antex'] = list(self.options.antex_files)
        return files

    def to_project(self, project: Project | None = None) -> Project:
        project = project or Project()
        project.kind = 'network'
        project.number, project.place = self.number.strip(), self.place.strip()
        project.options, project.coordinates = self.options, self.coordinates
        project.bases = self.stations()
        project.rovers = [NetworkRover(r.key, r.point_name.strip(), r.pole_height_m(), r.code.strip(),
                                       r.description.strip(), r.label) for r in self.rovers]
        project.network_fix = list(self.fix_choices())
        project.network_options = self.network_options
        project.line_choices = dict(self.line_choices)
        quality_to_project(self, project)
        return project

    @classmethod
    def from_project(cls, project: Project, files: dict[str, list[Path]]) -> 'NetworkJob':
        stations = {s.id: s for s in project.bases}
        bases = []
        for role, paths in files.items():
            if role.startswith('base:'):
                sid = role.split(':', 1)[1]
                station = stations.get(sid)
                bases.append(BaseEntry(files=paths, station=station, name=station.name if station else ''))
        rovers = [RoverEntry(r.key, files=files.get(f'rover:{r.key}', []), csv=files.get(f'csv:{r.key}', []),
                             point_name=r.point_name,
                             pole_height='' if r.pole_height_m is None else f'{r.pole_height_m:.3f}', code=r.code,
                             description=r.description, label=r.label) for r in project.rovers]
        job = cls(rovers=rovers or [RoverEntry('R1')], bases=bases, options=project.options,
                  coordinates=project.coordinates, number=project.number, place=project.place,
                  fix=list(project.network_fix), network_options=project.network_options,
                  line_choices=dict(project.line_choices), **quality_from_project(project, 'network'))
        job.fix_choices()
        return job
