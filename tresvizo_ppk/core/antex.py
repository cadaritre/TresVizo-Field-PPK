"""Modelos de antena (ANTEX igs20): qué calibración se usa para cada antena y el archivo que lee RTKLIB.

El programa trae un subconjunto del ANTEX de la IGS (`data/antex/`, ver ORIGEN.md): todas las antenas de satélite y
las de receptor de la RGNA; un paquete de red CORS puede traer las calibraciones de las antenas de sus estaciones
(`antennaCalibrations`), que se suman. La antena de los Meridian (HARXON HA-901A del MeridianV, AS-ANT3B-OEMSUR del MeridianVI)
no está en ningún ANTEX: se procesa sin modelo (PCO 0) y el informe lo dice, como el firmware («Antenna PCO/PCV not
calibrated»), hasta que haya calibración de laboratorio.
"""
from __future__ import annotations

import gzip
import re
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data" / "antex"


def bundled_antex() -> Path:
    files = sorted(DATA_DIR.glob("igs20_*_tvppk.atx.gz"))
    if not files:
        raise FileNotFoundError("Falta el ANTEX incluido (tresvizo_ppk/core/data/antex).")
    return files[-1]


def antex_name() -> str:
    m = re.search(r"(igs20_\d{4})", bundled_antex().name)
    return m.group(1) if m else bundled_antex().name


def _network_blocks() -> str:
    try:
        from . import networks
        return "".join(networks.antex_blocks())
    except Exception:
        return ""


def ensure_antex(workdir: Path) -> Path:
    """Deja en la carpeta de trabajo el ANTEX que lee RTKLIB (no lee .gz): el incluido más las calibraciones que
    traigan los paquetes de red instalados (antenas de sus estaciones), y devuelve su ruta."""
    source = bundled_antex()
    extra = _network_blocks()
    target = workdir / (source.name[:-3] if not extra else source.name[:-7] + "_redes.atx")
    workdir.mkdir(parents=True, exist_ok=True)
    with gzip.open(source, "rt", encoding="latin-1") as src:
        text = src.read()
    if extra:
        known = set(_bundled_models())
        add = []
        for block in re.findall(r"[^\n]*START OF ANTENNA *\n.*?END OF ANTENNA *\n", extra, re.S):
            m = re.search(r"^(.{20})(.{20}).*TYPE / SERIAL NO", block, re.M)
            if m and (m.group(1)[0:16].strip().upper(), m.group(1)[16:20].strip().upper() or "NONE") not in known:
                add.append(block)
        text += "".join(add)
    if not target.is_file() or target.read_text(encoding="latin-1") != text:
        target.write_text(text, encoding="latin-1")
    return target


@dataclass
class AntennaModel:
    type: str                                  # 16 caracteres del ANTEX, sin espacios al final
    radome: str
    frequencies: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # «G01» → PCO (N, E, U) m

    @property
    def rtklib_name(self) -> str:
        return f"{self.type} {self.radome}"


def _receiver_models() -> dict[tuple[str, str], AntennaModel]:
    models = dict(_bundled_models())
    for key, model in _parse_models(_network_blocks().splitlines(keepends=True)).items():
        models.setdefault(key, model)
    return models


@lru_cache(maxsize=1)
def _bundled_models() -> dict[tuple[str, str], AntennaModel]:
    with gzip.open(bundled_antex(), "rt", encoding="latin-1") as handle:
        return _parse_models(handle)


def _parse_models(lines) -> dict[tuple[str, str], AntennaModel]:
    models: dict[tuple[str, str], AntennaModel] = {}
    current: AntennaModel | None = None
    freq = ""
    for line in lines:
        label = line[60:].strip()
        if label == "START OF ANTENNA":
            current, freq = None, ""
        elif label == "TYPE / SERIAL NO":
            if not line[20:40].strip():          # las de satélite llevan el PRN en el número de serie
                current = AntennaModel(type=line[0:16].strip(), radome=line[16:20].strip() or "NONE")
        elif label == "START OF FREQUENCY" and current is not None:
            freq = line[3:6].strip()
        elif label == "NORTH / EAST / UP" and current is not None and freq:
            n, e, u = (float(line[i:i + 10]) / 1000.0 for i in (0, 10, 20))
            current.frequencies[freq] = (n, e, u)
        elif label == "END OF ANTENNA" and current is not None:
            models[(current.type.upper(), current.radome.upper())] = current
            current = None
    return models


@dataclass
class AntennaChoice:
    model: AntennaModel | None
    requested: str
    note: str                                  # qué se usó, para la bitácora y el informe

    @property
    def rtklib_name(self) -> str:
        return self.model.rtklib_name if self.model else ""


def choose(antenna_type: str, radome: str = "NONE", who: str = "") -> AntennaChoice:
    """Calibración del ANTEX para una antena: exacta (tipo + radomo), o sin radomo («NONE») con aviso, o ninguna."""
    kind = re.sub(r"\s+", "", antenna_type or "").upper()
    dome = (radome or "NONE").strip().upper() or "NONE"
    label = f"{who}: " if who else ""
    requested = f"{kind} {dome}".strip()
    if not kind:
        return AntennaChoice(None, requested, f"{label}antena sin declarar: sin modelo de centro de fase.")
    models = _receiver_models()
    exact = models.get((kind, dome))
    if exact:
        return AntennaChoice(exact, requested, f"{label}{exact.type} {exact.radome}, calibración {antex_name()}.")
    bare = models.get((kind, "NONE"))
    if bare:
        return AntennaChoice(bare, requested, f"{label}{kind} con radomo {dome} no está en {antex_name()}: se usa "
                                              f"{kind} NONE (diferencia típica de 1 a 3 mm en la vertical).")
    return AntennaChoice(None, requested, f"{label}{kind} {dome} no está en el ANTEX incluido: sin modelo de centro "
                                          "de fase (PCO/PCV 0).")
