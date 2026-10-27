"""Resultados sintéticos realistas para probar los informes y la exportación mientras el motor no está.

Todo es **de prueba**: las estaciones se llaman PRB1, PRB2 y PRB3 y su fuente dice «sintética»; nada de esto es una
coordenada oficial de la RGNA. Los archivos `.pos` y `.stat` imitan los que escribe `rnx2rtkp` de RTKLIB-EX 2.5.1
(mismas columnas y formato), con una geometría de satélites verosímil (pasos de 4 a 6 h, máscara de 15°), residuos
de pocos milímetros que crecen a baja elevación, algunos saltos de ciclo y un tramo flotante al inicio.
"""
from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tresvizo_ppk.crs import motor
from tresvizo_ppk.crs.definicion import Ellipsoid
from tresvizo_ppk.model import (AdjustmentResult, BaselineSolution, CoordinateChoice, DataQuality, Ecef, Enu,
                                Ephemeris, FieldPoint, ObservationSet, PointsResult, ProcessedPoint, ProcessingOptions,
                                Project, Solution, StaticResult, Station, UserProfile)
from tresvizo_ppk.report import calculos

GRS80 = Ellipsoid("GRS 1980", 6378137.0, 298.257222101)
LEAP = 18
START = datetime(2026, 10, 20, 15, 0, 0, tzinfo=timezone.utc)


def ecef(lat, lon, h) -> Ecef:
    return Ecef(*motor.geocentric(lat, lon, h, GRS80))


def _enu_offset(p: Ecef, e: float, n: float, u: float) -> Ecef:
    lat, lon, _ = calculos.geodetic(p)
    r = calculos.rotation(lat, lon)
    return Ecef(p.x + r[0][0] * e + r[1][0] * n + r[2][0] * u, p.y + r[0][1] * e + r[1][1] * n + r[2][1] * u,
                p.z + r[0][2] * e + r[1][2] * n + r[2][2] * u)


# --- Cielo -----------------------------------------------------------------------------------------------------------

def _sky(rng: random.Random):
    """Satélites con un paso cada uno: (id, hora de culminación en h desde START, duración h, elevación máx, acimut)."""
    sats = []
    for prefix, numbers in (("G", [2, 5, 7, 9, 13, 15, 18, 20, 25, 29, 30]), ("E", [3, 5, 8, 13, 15, 24, 27, 31]),
                            ("R", [3, 4, 13, 14, 19, 20]), ("C", [19, 20, 26, 29, 35, 46])):
        for n in numbers:
            sats.append((f"{prefix}{n:02d}", rng.uniform(-2.5, 4.5), rng.uniform(4.5, 6.5), rng.uniform(35, 87),
                         rng.uniform(0, 360)))
    return sats


def _azel(sat, hours: float) -> tuple[float, float]:
    _, peak, dur, elmax, az0 = sat
    x = (hours - peak) / (dur / 2)            # −1 … 1 durante el paso
    if abs(x) >= 1:
        return az0, -10.0
    el = elmax * math.cos(x * math.pi / 2)
    az = (az0 + 70 * x) % 360
    return az, el


def _gpst(t: datetime) -> tuple[int, float]:
    g = t + timedelta(seconds=LEAP) - datetime(1980, 1, 6, tzinfo=timezone.utc)
    week = g.days // 7
    return week, (g - timedelta(weeks=week)).total_seconds()


def write_rtklib(folder: Path, name: str, positions, interval: float, rng: random.Random, sky,
                 float_until_s: float = 0.0, mask: float = 15.0, extra_float=()) -> tuple[Path, Path]:
    """Escribe `<name>.pos` (geodésicas, GPST) y `<name>.pos.stat`. `positions` da la posición en cada época."""
    pos_path = folder / f"{name}.pos"
    stat_path = folder / f"{name}.pos.stat"
    pos_lines = ["% program   : RTKLIB-EX 2.5.1", "% pos mode  : static" if interval >= 5 else "% pos mode  : kinematic",
                 "% elev mask : 15.0 deg", "% (lat/lon/height=WGS84/ellipsoidal,Q=1:fix,2:float,3:sbas,4:dgps,"
                 "5:single,6:ppp,ns=# of satellites)",
                 "%  GPST                  latitude(deg) longitude(deg)  height(m)   Q  ns   sdn(m)   sde(m)   sdu(m)  "
                 "sdne(m)  sdeu(m)  sdun(m) age(s)  ratio"]
    stat_lines = []
    for i, p in enumerate(positions):
        t = START + timedelta(seconds=i * interval)
        hours = i * interval / 3600
        week, tow = _gpst(t)
        visible = []
        for sat in sky:
            az, el = _azel(sat, hours)
            if el >= mask:
                visible.append((sat[0], az, el))
        q = 2 if i * interval < float_until_s or any(a <= i * interval < b for a, b in extra_float) else 1
        ratio = (1.2 + 1.6 * (i * interval) / max(float_until_s, 1)) if q == 2 else rng.uniform(8, 60)
        sd = (0.004, 0.003, 0.009) if q == 1 else (0.06, 0.05, 0.12)
        lat, lon, h = motor.geodetic(p.x, p.y, p.z, GRS80)
        gt = t + timedelta(seconds=LEAP)
        pos_lines.append(f"{gt:%Y/%m/%d %H:%M:%S}.000 {lat:14.9f} {lon:14.9f} {h:10.4f} {q:3d} {len(visible):3d} "
                         f"{sd[0]:8.4f} {sd[1]:8.4f} {sd[2]:8.4f} {0.0:8.4f} {0.0:8.4f} {0.0:8.4f} {1.0:6.2f} "
                         f"{ratio:6.1f}")
        for sat, az, el in visible:
            for frq in (1, 2):
                res_c = rng.gauss(0, 0.35 / math.sin(math.radians(el)))
                res_p = rng.gauss(0, 0.0025 / math.sin(math.radians(el)))
                slip = 1 if rng.random() < 0.0008 else 0
                stat_lines.append(f"$SAT,{week},{tow:.3f},{sat},{frq},{az:.1f},{el:.1f},{res_c:.4f},{res_p:.4f},1,"
                                  f"{30 + el / 3:.0f},{2 if q == 1 else 1},{slip},{i},0,{slip},0,0.00,0.000000,0.00000")
    pos_path.write_text("\n".join(pos_lines) + "\n", encoding="utf-8")
    stat_path.write_text("\n".join(stat_lines) + "\n", encoding="utf-8")
    return pos_path, stat_path


# --- Comunes ---------------------------------------------------------------------------------------------------------

def user(logo: Path | None = None) -> UserProfile:
    return UserProfile(company="Topografía de Prueba S.A. de C.V.", surveyor="Ing. Ana Prueba López",
                       license_id="0000000 (prueba)", logo=logo)


def stations() -> list[Station]:
    return [
        Station("PRB1", "Estación de prueba 1", ecef(21.8562, -102.2843, 1880.512), Enu(0.002, 0.002, 0.005),
                "TRM59800.00", "SCIS", 0.0083, "TRIMBLE NETR9", "sintética para pruebas (no es la RGNA)"),
        Station("PRB2", "Estación de prueba 2", ecef(22.7709, -102.5833, 2413.205), Enu(0.002, 0.002, 0.006),
                "LEIAR20", "LEIM", 0.0, "LEICA GR50", "sintética para pruebas (no es la RGNA)"),
        Station("PRB3", "Estación de prueba 3", ecef(21.0190, -101.2574, 2011.774), Enu(0.003, 0.003, 0.007),
                "JAVRINGANT_DM", "NONE", 0.0, "JAVAD TRE_3", "sintética para pruebas (no es la RGNA)"),
    ]


def quality(epochs: int, expected: int, interval: float) -> DataQuality:
    gaps = [(START + timedelta(minutes=41), START + timedelta(minutes=41, seconds=4 * interval))]
    return DataQuality(
        epochs=epochs, expected_epochs=expected, gaps=gaps,
        satellites=[f"G{n:02d}" for n in (2, 5, 7, 9, 13, 15, 18, 20, 25, 29, 30)] +
                   [f"E{n:02d}" for n in (3, 5, 8, 13, 15, 24, 27, 31)] + ["R03", "R04", "R13", "C19", "C20"],
        signals={"GPS": ["L1C", "L2W", "L5Q"], "Galileo": ["L1C", "L5Q", "L7Q"], "GLONASS": ["L1C", "L2C"],
                 "BeiDou": ["L2I", "L7I"]},
        cycle_slips={"G13": 3, "E27": 2, "R04": 1, "C20": 1}, multipath_m={"GPS L1C": 0.31, "GPS L2W": 0.42,
                                                                         "Galileo L1C": 0.27},
        warnings=["Un hueco de 2 min a las 15:41 UTC (el receptor perdió la vista del cielo)."])


def options() -> ProcessingOptions:
    return ProcessingOptions()


LOG = ["RTKLIB-EX 2.5.1 (rnx2rtkp), compilado por scripts/build_rtklib.py",
       "pos1-posmode       =static",
       "pos1-frequency     =l1+l2",
       "pos1-soltype       =combined",
       "pos1-elmask        =15",
       "pos1-navsys        =45",
       "pos2-armode        =fix-and-hold",
       "pos2-arthres       =3",
       "ant2-anttype       =TRM59800.00 SCIS",
       "Aviso: PCO de la antena del rover sin medir (HA-901A no está en igs20.atx).",
       "Fin del proceso: 0 errores."]


# --- Estático --------------------------------------------------------------------------------------------------------

def static_result(folder: Path, bases: int = 2, seed: int = 7) -> StaticResult:
    rng = random.Random(seed)
    sky = _sky(rng)
    rover_true = ecef(21.9105, -102.3127, 1893.447)
    interval = 30.0
    n = 240
    st = stations()[:bases]
    baselines = []
    for k, base in enumerate(st):
        positions = [_enu_offset(rover_true, rng.gauss(0, 0.004), rng.gauss(0, 0.004), rng.gauss(0, 0.01))
                     for _ in range(n)]
        pos, stat = write_rtklib(folder, f"linea_{base.id}", positions, interval, rng, sky, float_until_s=600)
        bias = (0.002 * (k + 1), -0.003 * k, 0.006 * (1 - 2 * k))
        rover = _enu_offset(rover_true, *bias)
        vec = (rover.x - base.position.x, rover.y - base.position.y, rover.z - base.position.z)
        lat, lon, _ = calculos.geodetic(base.position)
        e, nn, u = calculos.to_enu(vec, lat, lon)
        az, zen = calculos.azimuth_zenith(base.position, vec)
        length = math.sqrt(sum(v * v for v in vec))
        s = (0.003 + length * 0.1e-6)
        cov = ((s ** 2, 0.3 * s ** 2, -0.2 * s ** 2), (0.3 * s ** 2, (1.4 * s) ** 2, 0.25 * s ** 2),
               (-0.2 * s ** 2, 0.25 * s ** 2, (1.8 * s) ** 2))
        baselines.append(BaselineSolution(
            base=base, rover_marker="VERT-01", start=START, end=START + timedelta(seconds=(n - 1) * interval),
            epochs_used=n, satellites=sorted({x[0] for x in sky}), solution=Solution.FIXED, ratio=rng.uniform(12, 40),
            rms_m=rng.uniform(0.004, 0.009), vector_ecef=Ecef(*vec, frame="ITRF2008", epoch=2010.0),
            vector_enu=Enu(e, nn, u), length_m=length, azimuth_deg=az, zenith_deg=zen, covariance_ecef=cov,
            rover_position=rover, ephemeris=Ephemeris.BROADCAST, pos_file=pos, stat_file=stat))
    rover_obs = ObservationSet(obs=[folder / "VERT01.26O"], nav=[folder / "VERT01.26P"], marker="VERT-01",
                               receiver="MeridianV (UM980)", antenna_type="HA-901A", antenna_height_m=1.8949,
                               start=START, end=START + timedelta(seconds=(n - 1) * interval), interval_s=interval,
                               rinex_version="3.04", antenna_pco_measured=False)
    final = _enu_offset(rover_true, 0.001, -0.001, 0.002)
    adj = None
    if bases >= 2:
        cov = ((1.1e-5, 2.0e-6, -1.0e-6), (2.0e-6, 1.9e-5, 3.0e-6), (-1.0e-6, 3.0e-6, 2.6e-5))
        lat, lon, _ = calculos.geodetic(final)
        ce = calculos.cov_enu(cov, lat, lon)
        res = {b.base.id: Enu(rng.gauss(0, 0.003), rng.gauss(0, 0.003), rng.gauss(0, 0.007)) for b in baselines}
        adj = AdjustmentResult(position=final, covariance_ecef=cov,
                               sigma_enu=Enu(*(math.sqrt(ce[i][i]) for i in range(3))), residuals_enu=res,
                               sigma0=0.93, chi2=2.6, degrees_of_freedom=3 * (bases - 1), passed=True)
        sigma = adj.sigma_enu
    else:
        sigma = Enu(0.004, 0.005, 0.011)
        final = baselines[0].rover_position
    return StaticResult(point_name="VERT-01", rover=rover_obs, rover_quality=quality(n - 4, n, interval), bases=st,
                        baselines=baselines, adjustment=adj, position=final, sigma_enu=sigma, log=list(LOG))


def static_project(folder: Path, choice: CoordinateChoice | None = None) -> Project:
    return Project(kind="static", name="Vértice VERT-01, fraccionamiento de prueba", number="2026-117",
                   folder=folder, options=options(), coordinates=choice or CoordinateChoice(), pole_height_m=1.8,
                   point_name="VERT-01", created=START - timedelta(days=1), processed=START + timedelta(days=1))


# --- PPK de puntos ---------------------------------------------------------------------------------------------------

def points_result(folder: Path, seed: int = 11) -> PointsResult:
    rng = random.Random(seed)
    sky = _sky(rng)
    base = stations()[0]
    origin = ecef(21.8705, -102.2901, 1885.0)
    # Doce puntos en una cuadrícula de 80 m, con caminata entre ellos y 60 s de ocupación en cada uno.
    targets = [(80 * (i % 4) + rng.uniform(-5, 5), 80 * (i // 4) + rng.uniform(-5, 5), rng.uniform(-1.5, 1.5))
               for i in range(12)]
    positions, occupations = [], []
    t = 0
    here = (0.0, 0.0, 0.0)
    for k, target in enumerate(targets):
        walk = int(math.hypot(target[0] - here[0], target[1] - here[1]) / 1.2) + 5
        for s in range(walk):
            f = (s + 1) / walk
            positions.append(_enu_offset(origin, here[0] + (target[0] - here[0]) * f + rng.gauss(0, 0.01),
                                         here[1] + (target[1] - here[1]) * f + rng.gauss(0, 0.01),
                                         1.8 + here[2] + (target[2] - here[2]) * f))
        start = len(positions)
        for _ in range(60):
            positions.append(_enu_offset(origin, target[0] + rng.gauss(0, 0.006), target[1] + rng.gauss(0, 0.006),
                                         1.8 + target[2] + rng.gauss(0, 0.012)))
        occupations.append((start, len(positions) - 1))
        here = target
    float_span = (occupations[7][0] - 30, occupations[7][1] + 5)    # el punto 8 queda bajo árboles: flotante
    pos, stat = write_rtklib(folder, "jornada", positions, 1.0, rng, sky, float_until_s=40,
                             extra_float=[float_span])
    points = []
    for k, ((s, e), target) in enumerate(zip(occupations, targets)):
        name = f"P{k + 1:02d}"
        ground = _enu_offset(origin, target[0], target[1], target[2])
        fp = FieldPoint(name=name, code=["LINDERO", "ESQ", "POSTE", "BN"][k % 4],
                        start=START + timedelta(seconds=s), end=START + timedelta(seconds=e), antenna_height_m=1.8,
                        field_position=_enu_offset(ground, rng.gauss(0, 0.012), rng.gauss(0, 0.012),
                                                   rng.gauss(0, 0.025)),
                        field_solution="FIJO", row={})
        if k == 11:
            fp = replace(fp, start=START + timedelta(hours=3), end=START + timedelta(hours=3, minutes=1))
            points.append(ProcessedPoint(fp, Solution.NONE, None, None, 0, 0, None, None,
                                         "su ocupación (18:00–18:01 UTC) queda fuera de la grabación del rover"))
            continue
        sol = Solution.FLOAT if k == 7 else Solution.FIXED
        sig = Enu(0.05, 0.06, 0.11) if sol == Solution.FLOAT else Enu(0.006, 0.007, 0.014)
        vec = (ground.x - base.position.x, ground.y - base.position.y, ground.z - base.position.z)
        points.append(ProcessedPoint(fp, sol, ground, sig, 0 if sol == Solution.FLOAT else 60, 60,
                                     math.sqrt(sum(v * v for v in vec)), None,
                                     "sin épocas fijas: promedio de las flotantes" if sol == Solution.FLOAT else ""))
    rover = ObservationSet(obs=[folder / "JORNADA.26O"], marker="MV-74E828", receiver="MeridianV (UM980)",
                           antenna_type="HA-901A", antenna_height_m=1.8, start=START,
                           end=START + timedelta(seconds=len(positions) - 1), interval_s=1.0, rinex_version="3.04",
                           antenna_pco_measured=False)
    fixed_epochs = sum(1 for i in range(len(positions)) if not (i < 40 or float_span[0] <= i < float_span[1]))
    return PointsResult(base=base, rover=rover, rover_quality=quality(len(positions) - 4, len(positions), 1.0),
                        points=points, pos_file=pos, fix_rate=fixed_epochs / len(positions), log=list(LOG),
                        warnings=["P12 no se posprocesó: su ocupación queda fuera de la grabación del rover."])


def points_project(folder: Path, choice: CoordinateChoice | None = None) -> Project:
    return Project(kind="points", name="Levantamiento de prueba, lote 14", number="2026-118", folder=folder,
                   options=options(), coordinates=choice or CoordinateChoice(), created=START,
                   processed=START + timedelta(hours=5), base_kind="rgna")
