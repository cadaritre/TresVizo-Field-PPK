"""RTKLIB (`rnx2rtkp`) como proceso: configuración generada, ejecución con progreso y cancelar, y lectura de la
solución (`.pos`, en ECEF con covarianzas) y de los residuos (`.stat`).

Nombres y valores de las opciones: los de `src/options.c` de RTKLIB-EX 2.5.1.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from ..errors import UserError
from . import tools

GPS_EPOCH_NAIVE = datetime(1980, 1, 6)

# Valores de RTKLIB para el estado de la solución (Q).
Q_FIX, Q_FLOAT, Q_SBAS, Q_DGPS, Q_SINGLE, Q_PPP = 1, 2, 3, 4, 5, 6

NAVSYS_BITS = {"GPS": 1, "SBAS": 2, "GLONASS": 4, "Galileo": 8, "QZSS": 16, "BeiDou": 32, "NavIC": 64}


@dataclass
class RtkConfig:
    """Opciones de `rnx2rtkp` (nombre → valor), en el orden en que se escriben."""
    values: dict[str, str] = field(default_factory=dict)

    def set(self, key: str, value) -> None:
        if isinstance(value, bool):
            value = "on" if value else "off"
        elif isinstance(value, float):
            value = f"{value:.10g}"
        self.values[key] = str(value)

    def write(self, path: Path) -> None:
        lines = ["# Configuración de rnx2rtkp generada por TresVizo Field PPK (" + tools.RTKLIB_VERSION + ")"]
        for key, value in self.values.items():
            lines.append(f"{key:<18}={value}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def text(self) -> list[str]:
        return [f"{k}={v}" for k, v in self.values.items()]


@dataclass
class AntennaSetup:
    antenna_type: str = ""                 # «TRM115000.00 NONE» (tipo y radomo) si está en el ANTEX; vacío si no
    delta_enu: tuple[float, float, float] = (0.0, 0.0, 0.0)   # de la marca a la ARP (E, N, U)


def base_config(*, mode: str, options, frequencies: int, ionosphere: str, troposphere: str,
                navsys: int, ambiguity: str, glonass_ar: str, precise: bool, antex: Path | None,
                rover: AntennaSetup, base: AntennaSetup, base_xyz: tuple[float, float, float],
                interval_s: float | None, elevation_mask: float, min_ratio: float, combined: bool,
                kinematic_dynamics: bool = False, stat_level: int = 2) -> RtkConfig:
    """Configuración completa para un proceso relativo (estático o cinemático) de rnx2rtkp."""
    cfg = RtkConfig()
    s = cfg.set
    s("pos1-posmode", {"static": "static", "kinematic": "kinematic"}[mode])
    s("pos1-frequency", {1: "l1", 2: "l1+l2", 3: "l1+l2+l5"}.get(frequencies, "l1+l2"))
    s("pos1-soltype", "combined" if combined else "forward")
    s("pos1-elmask", elevation_mask)
    s("pos1-snrmask_r", "off")
    s("pos1-snrmask_b", "off")
    s("pos1-dynamics", kinematic_dynamics)
    s("pos1-tidecorr", "on")                                  # mareas terrestres sólidas
    s("pos1-ionoopt", ionosphere)
    s("pos1-tropopt", troposphere)
    s("pos1-sateph", "precise" if precise else "brdc")
    s("pos1-posopt1", bool(precise and antex))               # PCV de satélite (solo con órbitas precisas)
    s("pos1-posopt2", bool(antex))                           # PCV de receptor
    s("pos1-posopt3", bool(precise))                         # giro de fase (wind-up)
    s("pos1-posopt4", bool(precise))                         # satélites en eclipse
    s("pos1-posopt5", "off")
    s("pos1-posopt6", "off")
    # satélites sueltos que el editor de sesión dejó fuera («G13 R05»); los sistemas completos salen de `navsys`
    s("pos1-exclsats", _exclsats(options))
    s("pos1-navsys", navsys)
    s("pos2-armode", ambiguity)
    s("pos2-gloarmode", glonass_ar)
    s("pos2-bdsarmode", "off")                                # ver baseline.py: BeiDou flotante
    s("pos2-arfilter", "on")
    s("pos2-arthres", min_ratio)
    s("pos2-arthresmin", min(min_ratio, 3.0))
    s("pos2-arthresmax", max(min_ratio, 3.0))
    s("pos2-arthres1", 0.1)
    s("pos2-arthres2", 0.0)
    s("pos2-arthres3", 1e-9)
    s("pos2-arthres4", 1e-5)
    s("pos2-varholdamb", 0.1)
    s("pos2-gainholdamb", 0.01)
    s("pos2-arlockcnt", 0)
    s("pos2-minfixsats", 4)
    s("pos2-minholdsats", 5)
    s("pos2-mindropsats", 10)
    s("pos2-arelmask", elevation_mask)
    s("pos2-arminfix", 20 if mode == "kinematic" else 10)
    s("pos2-armaxiter", 1)
    s("pos2-elmaskhold", elevation_mask)
    s("pos2-aroutcnt", 20)
    base_interval = interval_s or 30.0
    s("pos2-maxage", max(30.0, 2.5 * base_interval))
    s("pos2-syncsol", "off")
    s("pos2-slipthres", 0.05)
    s("pos2-dopthres", 0.0)
    s("pos2-rejionno", 2.0 if ionosphere in ("est-stec", "dual-freq") else 1.0)
    s("pos2-rejcode", 30.0)
    s("pos2-niter", 1)
    s("pos2-baselen", 0.0)
    s("pos2-basesig", 0.0)
    s("out-solformat", "xyz")
    s("out-outhead", "on")
    s("out-outopt", "on")
    s("out-outvel", "off")
    s("out-timesys", "gpst")
    s("out-timeform", "hms")
    s("out-timendec", 3)
    s("out-degform", "deg")
    s("out-fieldsep", "")
    s("out-outsingle", "off")
    s("out-maxsolstd", 0)
    s("out-height", "ellipsoidal")
    s("out-geoid", "internal")
    s("out-solstatic", "all")
    s("out-outstat", {0: "off", 1: "state", 2: "residual"}[stat_level])
    s("stats-eratio1", 300.0)
    s("stats-eratio2", 300.0)
    s("stats-eratio5", 300.0)
    s("stats-errphase", 0.003)
    s("stats-errphaseel", 0.003)
    s("stats-errphasebl", 0.0)
    s("stats-errdoppler", 1.0)
    s("stats-snrmax", 52.0)
    s("stats-errsnr", 0.0)
    s("stats-errrcv", 0.0)
    s("stats-stdbias", 30.0)
    s("stats-stdiono", 0.03)
    s("stats-stdtrop", 0.3)
    s("stats-prnaccelh", 3.0 if mode == "kinematic" else 1.0)
    s("stats-prnaccelv", 1.0)
    s("stats-prnbias", 0.0001)
    s("stats-prniono", 0.001)
    s("stats-prntrop", 0.0001)
    s("stats-prnpos", 0.0)
    s("stats-clkstab", 5e-12)
    s("ant1-postype", "single")
    s("ant1-anttype", rover.antenna_type)
    s("ant1-antdele", rover.delta_enu[0])
    s("ant1-antdeln", rover.delta_enu[1])
    s("ant1-antdelu", rover.delta_enu[2])
    s("ant2-postype", "xyz")
    s("ant2-pos1", f"{base_xyz[0]:.4f}")
    s("ant2-pos2", f"{base_xyz[1]:.4f}")
    s("ant2-pos3", f"{base_xyz[2]:.4f}")
    s("ant2-anttype", base.antenna_type)
    s("ant2-antdele", base.delta_enu[0])
    s("ant2-antdeln", base.delta_enu[1])
    s("ant2-antdelu", base.delta_enu[2])
    s("ant2-maxaveep", 1)
    s("ant2-initrst", "on")
    s("misc-timeinterp", "on")
    s("misc-sbasatsel", 0)
    s("misc-rnxopt1", "")
    s("misc-rnxopt2", "")
    s("misc-pppopt", "")
    s("file-satantfile", str(antex) if antex else "")
    s("file-rcvantfile", str(antex) if antex else "")
    s("file-staposfile", "")
    s("file-geoidfile", "")
    s("file-ionofile", "")
    s("file-dcbfile", "")
    s("file-eopfile", "")
    s("file-blqfile", "")
    s("file-tempdir", "")
    s("file-geexefile", "")
    s("file-solstatfile", "")
    s("file-tracefile", "")
    return cfg


def _exclsats(options) -> str:
    if options is None:
        return ""
    from . import sesion
    return sesion.rtklib_exclsats(options)


def navsys_mask(constellations) -> int:
    mask = 0
    for name in constellations:
        mask |= NAVSYS_BITS.get(name, 0)
    return mask or NAVSYS_BITS["GPS"]


# --- Carpeta de trabajo con nombres ASCII ----------------------------------------------------------------------------

def ascii_name(name: str) -> str:
    """«Vértice 1.26O» → «Vertice_1.26O»: solo letras y números ASCII, punto, guion y guion bajo."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    plain = re.sub(r"[^A-Za-z0-9._-]", "_", plain).strip("._") or "archivo"
    return plain


class Stage:
    """Carpeta donde corre RTKLIB con nombres ASCII y rutas relativas.

    RTKLIB abre los archivos con `fopen` de C: en Windows eso usa la página de códigos ANSI, así que una ruta con
    acentos o eñes (la carpeta del usuario, un proyecto «Vértice…») no se abre aunque se le pase bien codificada; en
    macOS y Linux depende de la configuración regional. Aquí cada entrada se enlaza (o se copia) dentro de una
    subcarpeta de nombre ASCII de la carpeta de trabajo, RTKLIB corre con esa subcarpeta como directorio actual y solo
    ve nombres relativos ASCII; las salidas se mueven después a donde las espera el motor. Los comprimidos (.gz, .Z,
    .zip) se descomprimen ahí mismo, porque RTKLIB los abriría con un `gzip` externo que no hay en Windows.
    """

    def __init__(self, workdir: Path, name: str) -> None:
        self.dir = Path(workdir) / f"_rtklib_{ascii_name(name)}"
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True)
        self._count = 0

    def file(self, source: Path, subfolder: str = "") -> str:
        """Pone `source` en la carpeta (descomprimido si hace falta) y devuelve su nombre relativo."""
        from . import compress
        source = Path(source)
        folder = self.dir / subfolder if subfolder else self.dir
        folder.mkdir(parents=True, exist_ok=True)
        self._count += 1
        if source.suffix.lower() in (".gz", ".z", ".zip"):
            scratch = self.dir / f"_descomprimir_{self._count:03d}"
            files = compress.unpack([source], scratch)
            names = []
            for f in files:
                names.append(self._place(f, folder, move=True))
            shutil.rmtree(scratch, ignore_errors=True)
            return names[0]
        return self._place(source, folder, move=False)

    def _place(self, source: Path, folder: Path, *, move: bool) -> str:
        target = folder / f"{self._count:03d}_{ascii_name(source.name)}"
        if move:
            shutil.move(str(source), target)
        else:
            try:
                os.link(source, target)              # enlace duro: sin copiar megas por cada proceso
            except OSError:
                shutil.copy2(source, target)
        return os.path.relpath(target, self.dir)

    def folder(self, files: list[Path], name: str) -> str:
        """Pone varios archivos en una subcarpeta y devuelve el comodín relativo que RTKLIB expande («nav/*»,
        con el separador del sistema: RTKLIB busca la carpeta del comodín con él)."""
        sub = ascii_name(name)
        for f in files:
            self.file(f, sub)
        return f"{sub}{os.sep}*"

    def config(self, source: Path) -> str:
        """Copia la configuración con los archivos que nombra (ANTEX…) puestos en la carpeta y en relativo."""
        lines = []
        for line in Path(source).read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip().startswith("file-") and value.strip():
                line = f"{key}={self.file(Path(value.strip()))}"
            lines.append(line)
        target = self.dir / "config.conf"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target.name

    def collect(self, name: str, target: Path) -> bool:
        """Mueve una salida de RTKLIB a su lugar definitivo."""
        produced = self.dir / name
        if not produced.is_file():
            return False
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        shutil.move(str(produced), target)
        return True

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


# --- Ejecución --------------------------------------------------------------------------------------------------------

def _gpst_str(moment: datetime) -> tuple[str, str]:
    return moment.strftime("%Y/%m/%d"), moment.strftime("%H:%M:%S")


def run_rnx2rtkp(config_path: Path, out_pos: Path, rover_obs: Path, base_obs: Path, nav: list[Path],
                 precise: list[Path], *, workdir: Path, start_gpst: datetime | None = None,
                 end_gpst: datetime | None = None, interval_s: float | None = None,
                 progress: tools.Progress | None = None, cancel: threading.Event | None = None,
                 log: list[str] | None = None, label: str = "", combined: bool = True) -> Path:
    """Corre rnx2rtkp; devuelve el `.pos`. El `.stat` queda junto (`<pos>.stat`)."""
    exe = tools.find_tool("rnx2rtkp")
    stage = Stage(workdir, out_pos.stem)
    args = [str(exe), "-k", stage.config(config_path), "-o", "salida.pos"]
    if start_gpst is not None:
        d, t = _gpst_str(start_gpst)
        args += ["-ts", d, t]
    if end_gpst is not None:
        d, t = _gpst_str(end_gpst)
        args += ["-te", d, t]
    if interval_s:
        args += ["-ti", f"{interval_s:g}"]
    # rnx2rtkp lee como mucho 16 archivos de entrada (MAXFILE) y descarta los demás sin avisar: la navegación y las
    # órbitas van cada una en su carpeta y se pasan con comodín, que RTKLIB expande (readrnxt/expath) sin límite.
    args += [stage.file(rover_obs), stage.file(base_obs)]
    for kind, files in (("navegacion", nav), ("orbitas", precise)):
        if files:
            args.append(stage.folder(list(files), kind))
    out_pos.unlink(missing_ok=True)
    span = None
    if start_gpst and end_gpst:
        span = (start_gpst, end_gpst)
    state = {"last": None, "backward": False}

    def parse(line: str):
        if not line.startswith("processing :"):
            if line.startswith("reading"):
                return 0.0, f"{label}leyendo datos…"
            return None
        try:
            stamp = datetime.strptime(line[13:32], "%Y/%m/%d %H:%M:%S")
        except ValueError:
            return None
        if state["last"] is not None and stamp < state["last"] - timedelta(seconds=1):
            state["backward"] = True
        state["last"] = stamp
        if span is None:
            return None
        total = max(1.0, (span[1] - span[0]).total_seconds())
        frac = min(1.0, max(0.0, (stamp - span[0]).total_seconds() / total))
        if combined:
            frac = 0.5 + 0.5 * (1.0 - frac) if state["backward"] else 0.5 * frac
        return frac, f"{label}{'hacia atrás' if state['backward'] else 'hacia adelante'} {stamp:%H:%M:%S}"

    try:
        result = tools.run(args, cwd=stage.dir, progress=progress, cancel=cancel, progress_parser=parse, log=log)
        stat = Path(str(out_pos) + ".stat")
        stat.unlink(missing_ok=True)
        stage.collect("salida.pos", out_pos)
        stage.collect("salida.pos.stat", stat)
    finally:
        stage.close()
    if result.returncode != 0 or not out_pos.is_file():
        detail = result.stderr.strip().splitlines()
        message = detail[-1] if detail else f"código {result.returncode}"
        raise UserError(f"El cálculo de RTKLIB falló ({message}). Revisa que la base y el rover se traslapen y que "
                        "haya archivo de navegación.")
    return out_pos


# --- Lectura de resultados --------------------------------------------------------------------------------------------

@dataclass
class PosEpoch:
    time: datetime                          # GPST, ingenuo
    xyz: np.ndarray                         # m
    q: int
    ns: int
    cov: np.ndarray                         # 3×3, m²
    age: float
    ratio: float


def parse_pos(path: Path) -> list[PosEpoch]:
    """Lee un `.pos` en formato xyz (out-solformat=xyz, out-timeform=hms)."""
    epochs: list[PosEpoch] = []
    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for line in handle:
            if not line.strip() or line.startswith("%"):
                continue
            parts = line.split()
            if len(parts) < 15:
                continue
            try:
                stamp = datetime.strptime(parts[0] + " " + parts[1], "%Y/%m/%d %H:%M:%S.%f")
                x, y, z = float(parts[2]), float(parts[3]), float(parts[4])
                q, ns = int(parts[5]), int(parts[6])
                sdx, sdy, sdz, sdxy, sdyz, sdzx = (float(v) for v in parts[7:13])
                age, ratio = float(parts[13]), float(parts[14])
            except ValueError:
                continue
            cov = np.array([[_sq(sdx), _sq(sdxy), _sq(sdzx)],
                            [_sq(sdxy), _sq(sdy), _sq(sdyz)],
                            [_sq(sdzx), _sq(sdyz), _sq(sdz)]])
            epochs.append(PosEpoch(stamp, np.array([x, y, z]), q, ns, cov, age, ratio))
    return epochs


def _sq(v: float) -> float:
    """RTKLIB escribe las covarianzas como raíz con signo: sd_xy = sign(c)·√|c|."""
    return math.copysign(v * v, v)


@dataclass
class SatResidual:
    time: datetime
    sat: str
    freq: int
    az: float
    el: float
    resp: float                             # residuo de código (m)
    resc: float                             # residuo de fase (m)
    valid: bool
    snr: float
    fix: int                                # 0 no, 1 flotante, 2 fija, 3 retenida
    slip: int
    lock: int


def parse_stat(path: Path) -> list[SatResidual]:
    rows: list[SatResidual] = []
    if not path.is_file():
        return rows
    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for line in handle:
            if not line.startswith("$SAT,"):
                continue
            p = line.strip().split(",")
            if len(p) < 13:
                continue
            try:
                week, tow = int(p[1]), float(p[2])
                rows.append(SatResidual(
                    time=GPS_EPOCH_NAIVE + timedelta(weeks=week, seconds=tow), sat=p[3], freq=int(p[4]),
                    az=float(p[5]), el=float(p[6]), resp=float(p[7]), resc=float(p[8]), valid=p[9] == "1",
                    snr=float(p[10]), fix=int(p[11]), slip=int(p[12]), lock=int(p[13]) if len(p) > 13 else 0))
            except ValueError:
                continue
    return rows


def residual_rms(rows: list[SatResidual], only_fixed_times: set[datetime] | None = None) -> float | None:
    values = [r.resc for r in rows if r.valid and r.resc != 0.0
              and (only_fixed_times is None or r.time in only_fixed_times)]
    if not values:
        return None
    return math.sqrt(sum(v * v for v in values) / len(values))


def satellites_used(rows: list[SatResidual]) -> list[str]:
    sats = {r.sat for r in rows if r.valid}
    return sorted(sats, key=lambda s: ("GRECJSI".find(s[0]) if s[0] in "GRECJSI" else 9, s))


def ppp_static_config(*, antex: Path, antenna: AntennaSetup, constellations=("GPS", "Galileo"),
                      elevation_mask: float = 10.0) -> RtkConfig:
    """PPP estático (RTKLIB) con órbitas y relojes precisos: se usa para la validación (coordenada actual de una
    estación en el marco de las órbitas, independiente de las líneas base), no en los flujos del usuario."""
    cfg = RtkConfig()
    s = cfg.set
    s("pos1-posmode", "ppp-static")
    s("pos1-frequency", "l1+l2")
    s("pos1-soltype", "forward")
    s("pos1-elmask", elevation_mask)
    s("pos1-dynamics", "off")
    s("pos1-tidecorr", "on")
    s("pos1-ionoopt", "dual-freq")
    s("pos1-tropopt", "est-ztdgrad")
    s("pos1-sateph", "precise")
    s("pos1-posopt1", "on")
    s("pos1-posopt2", "on")
    s("pos1-posopt3", "on")
    s("pos1-posopt4", "on")
    s("pos1-posopt5", "off")
    s("pos1-navsys", navsys_mask(constellations))
    s("pos2-armode", "off")
    s("pos2-rejionno", 30.0)
    s("pos2-niter", 1)
    s("out-solformat", "xyz")
    s("out-outhead", "on")
    s("out-timesys", "gpst")
    s("out-timeform", "hms")
    s("out-timendec", 3)
    s("out-solstatic", "all")
    s("stats-eratio1", 100.0)
    s("stats-eratio2", 100.0)
    s("stats-errphase", 0.003)
    s("stats-errphaseel", 0.003)
    s("stats-prntrop", 0.0001)
    s("ant1-postype", "single")
    s("ant1-anttype", antenna.antenna_type)
    s("ant1-antdele", antenna.delta_enu[0])
    s("ant1-antdeln", antenna.delta_enu[1])
    s("ant1-antdelu", antenna.delta_enu[2])
    s("file-satantfile", str(antex))
    s("file-rcvantfile", str(antex))
    return cfg
