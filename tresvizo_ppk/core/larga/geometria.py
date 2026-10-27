"""Distancia calculada y correcciones de cada receptor a cada satélite (no diferenciadas), en metros.

Para cada época t (tiempo del receptor) y satélite:
1. instante de emisión τ = t − P/c − dts(τ) (con el código de la banda 1; un metro de error en P son 3 ns);
2. posición del satélite en τ (centro de fase: la difundida tal cual; la del SP3 más el PCO del satélite en la
   libre de ionosfera, con la orientación nominal) y rotación de la Tierra durante el viaje de la señal
   (efecto Sagnac);
3. posición del receptor: la marca más la marea terrestre sólida (IERS 2010) y la altura de la antena a la ARP;
4. distancia geométrica, elevación y acimut; PCO y PCV del receptor y PCV del satélite (nadir) en la libre de
   ionosfera; troposfera a priori con la GMF; wind-up de fase.

El reloj del satélite se resta en el modelo (−c·dts), aunque se cancela en la diferencia entre receptores salvo por
la diferencia de instantes de emisión (µs).

Omitidos, con su orden de magnitud en una línea de 50 a 200 km (lo que queda en la doble diferencia):
- **Carga oceánica:** en el centro de México, a 300 km o más de la costa, la diferencia entre los dos extremos es de
  1–2 mm (amplitud de M2 de 3–6 mm en la vertical que cambia poco en 150 km tierra adentro); en la costa, la
  diferencia puede llegar a 5–10 mm en la vertical y se promedia en sesiones de varias horas (períodos de 12 y 24 h).
  Se avisa en `docs/estado.md`.
- **Ionosfera de segundo orden:** 1–2 mm en la libre de ionosfera, casi igual en los dos extremos.
- **Carga atmosférica y marea polar:** < 1 mm de diferencia a estas distancias.
- **Gradientes troposféricos:** 1–3 mm en la horizontal arriba de 15° de elevación; la máscara por omisión es 15°.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..geodesy import ecef_to_geodetic, enu_rotation
from . import astro, iers, modelos
from .antena import Antenna, satellite_antenna
from .senales import C_LIGHT, Pair

OMEGA_E = 7.2921151467e-5


@dataclass
class Station:
    """Un extremo de la línea: marca (ECEF), altura vertical marca → ARP, antena (patrón completo o None)."""
    marker_xyz: np.ndarray
    height_m: float
    antenna: Antenna | None
    name: str = ""


@dataclass
class Environment:
    """Lo que no depende de la estación: Sol y Luna por época (interpolados) y órbitas."""
    times: np.ndarray
    sun: np.ndarray                 # (T, 3)
    moon: np.ndarray
    orbits: object                  # efemerides.Broadcast o efemerides.Precise
    precise: bool
    notes: list[str] = field(default_factory=list)


def environment(times: np.ndarray, orbits, precise: bool) -> Environment:
    step = 600.0
    nodes = np.arange(times[0] - step, times[-1] + 2 * step, step)
    sun_n = np.array([astro.sun_moon_ecef(t)[0] for t in nodes])
    moon_n = np.array([astro.sun_moon_ecef(t)[1] for t in nodes])
    sun = np.stack([np.interp(times, nodes, sun_n[:, k]) for k in range(3)], axis=1)
    moon = np.stack([np.interp(times, nodes, moon_n[:, k]) for k in range(3)], axis=1)
    return Environment(times=times, sun=sun, moon=moon, orbits=orbits, precise=precise)


def tide_series(xyz: np.ndarray, env: Environment) -> np.ndarray:
    """Marea sólida (T × 3) en la estación, calculada cada 10 min e interpolada (error < 0.1 mm)."""
    step = 600.0
    times = env.times
    nodes = np.arange(times[0] - step, times[-1] + 2 * step, step)
    vals = []
    for t in nodes:
        sun, moon = astro.sun_moon_ecef(t)
        mjd, hour = astro.mjd_and_hour_utc(t)
        tai = astro.leap_at_gps(t) + 19.0
        vals.append(iers.marea_solida_tvppk(xyz, sun, moon, mjd, hour, tai))
    vals = np.array(vals)
    return np.stack([np.interp(times, nodes, vals[:, k]) for k in range(3)], axis=1)


@dataclass
class SiteFrame:
    lat: float
    lon: float
    height: float
    rot: np.ndarray                 # ECEF → ENU
    gmf: iers.GmfSitio
    zhd: float
    zwd: float


def site_frame(xyz: np.ndarray, mjd: float) -> SiteFrame:
    lat, lon, h = ecef_to_geodetic(*xyz)
    rot = enu_rotation(lat, lon)
    gmf = iers.GmfSitio(mjd, math.radians(lat), math.radians(lon), h)
    zhd, zwd = modelos.zenith_delays(math.radians(lat), h)
    return SiteFrame(lat, lon, h, rot, gmf, zhd, zwd)


@dataclass
class SatGeometry:
    rho: np.ndarray                 # distancia geométrica (T)
    los: np.ndarray                 # unitario receptor → satélite, ECEF (T, 3)
    elev: np.ndarray                # rad
    azim: np.ndarray                # rad
    model_code: np.ndarray          # ρ − c·dts + troposfera a priori + antenas (m)
    model_phase: np.ndarray         # lo mismo + wind-up (m)
    map_wet: np.ndarray             # función de mapeo húmeda
    ok: np.ndarray                  # bool


@dataclass
class SatTrack:
    """Estado del satélite en la rejilla de épocas (emisión nominal t − 72 ms): posición del centro de fase,
    velocidad, reloj y su deriva; cada receptor lo lleva a su instante de emisión con la velocidad (|Δτ| < 30 ms,
    error < 0.1 mm)."""
    tau0: np.ndarray
    pos: np.ndarray
    vel: np.ndarray
    clk: np.ndarray
    clk_rate: np.ndarray
    ok: np.ndarray
    ex: np.ndarray
    ey: np.ndarray
    ez: np.ndarray
    antenna: Antenna | None


def satellite_track(sat: str, pair: Pair, env: Environment, *, when, sat_ant_cache: dict) -> SatTrack:
    orbits = env.orbits
    tau0 = env.times - 0.072
    dt = 0.5
    p0, c0, ok0 = orbits.state(sat, tau0)
    pm, cm, okm = orbits.state(sat, tau0 - dt)
    pp, cp, okp = orbits.state(sat, tau0 + dt)
    ok = ok0 & okm & okp
    vel = (pp - pm) / (2 * dt)
    rate = (cp - cm) / (2 * dt)
    p0 = np.where(ok[:, None], p0, 1.0e7)
    ex, ey, ez = modelos.attitude(p0, env.sun)
    sat_ant = None
    if env.precise:
        if sat not in sat_ant_cache:
            sat_ant_cache[sat] = satellite_antenna(sat, when)
            if sat_ant_cache[sat] is None:
                env.notes.append(f"{sat}: sin antena en el ANTEX (sin PCO del satélite)")
        sat_ant = sat_ant_cache[sat]
        pco_sat, _, _ = modelos.if_offset(sat_ant, sat[0], pair.band1, pair.band2, pair.alpha, pair.beta)
        if np.any(pco_sat):
            p0 = p0 + ex * pco_sat[0] + ey * pco_sat[1] + ez * pco_sat[2]
    return SatTrack(tau0=tau0, pos=p0, vel=np.where(ok[:, None], vel, 0.0), clk=np.where(ok, c0, 0.0),
                    clk_rate=np.where(ok, rate, 0.0), ok=ok, ex=ex, ey=ey, ez=ez, antenna=sat_ant)


def satellite_geometry(sat: str, pair: Pair, code1: np.ndarray, station: Station, rec_xyz: np.ndarray,
                       frame: SiteFrame, env: Environment, track: SatTrack, *, t_rx: np.ndarray | None = None
                       ) -> SatGeometry:
    """Geometría y modelo de un satélite visto desde un receptor en todas las épocas. `rec_xyz`: (T, 3) marca +
    marea + ARP. `t_rx`: la etiqueta de tiempo real del receptor en cada época (si no cae exacta en la época nominal
    de `env.times`, p. ej. un receptor sin reloj gobernado a unos ms del segundo); la emisión se calcula desde ella,
    así que el desfase queda como error de reloj del receptor, común a todos los satélites, y se elimina."""
    t = env.times if t_rx is None else np.asarray(t_rx, dtype=float)
    valid = np.isfinite(code1) & track.ok
    tau = t - np.where(valid, code1, 2.2e7) / C_LIGHT
    dts = track.clk + track.clk_rate * (tau - track.tau0)
    tau = tau - dts
    d_tau = tau - track.tau0
    pos = track.pos + track.vel * d_tau[:, None]
    dts = track.clk + track.clk_rate * d_tau
    ok = valid & (np.abs(d_tau) < 0.2)
    # rotación de la Tierra durante el viaje de la señal
    rho = np.linalg.norm(pos - rec_xyz, axis=1)
    for _ in range(2):
        theta = OMEGA_E * rho / C_LIGHT
        c, s = np.cos(theta), np.sin(theta)
        rot = np.stack([c * pos[:, 0] + s * pos[:, 1], -s * pos[:, 0] + c * pos[:, 1], pos[:, 2]], axis=1)
        rho = np.linalg.norm(rot - rec_xyz, axis=1)
    los = (rot - rec_xyz) / np.where(rho > 0, rho, 1.0)[:, None]
    enu = los @ frame.rot.T
    elev = np.arcsin(np.clip(enu[:, 2], -1, 1))
    azim = np.arctan2(enu[:, 0], enu[:, 1])
    zen_deg = 90.0 - np.degrees(elev)
    az_deg = np.mod(np.degrees(azim), 360.0)
    # antena del receptor
    pco_rx, pats_rx, _ = modelos.if_offset(station.antenna, sat[0], pair.band1, pair.band2, pair.alpha, pair.beta)
    pco_enu = np.array([pco_rx[1], pco_rx[0], pco_rx[2]])
    dant = -(enu @ pco_enu) + modelos.if_pcv(pats_rx, pair.alpha, pair.beta, zen_deg, az_deg)
    # PCV del satélite (nadir)
    if env.precise and track.antenna is not None:
        _, pats_sat, _ = modelos.if_offset(track.antenna, sat[0], pair.band1, pair.band2, pair.alpha, pair.beta)
        cos_nadir = np.sum(-los * track.ez, axis=1)
        nadir = np.degrees(np.arccos(np.clip(cos_nadir, -1, 1)))
        dant = dant + modelos.if_pcv(pats_sat, pair.alpha, pair.beta, nadir)
    # troposfera a priori
    mh, mw = frame.gmf(np.maximum(elev, math.radians(3.0)))
    trop = frame.zhd * mh + frame.zwd * mw
    # wind-up
    wu = modelos.wind_up(-los, track.ex, track.ey, frame.rot[1], frame.rot[0])
    wu = np.where(np.isfinite(wu), wu, 0.0)
    model_code = rho - C_LIGHT * dts + trop + dant
    model_phase = model_code + pair.lam_nl * wu
    return SatGeometry(rho=rho, los=los, elev=elev, azim=azim, model_code=model_code, model_phase=model_phase,
                       map_wet=mw, ok=ok & np.isfinite(rho))


def receiver_positions(station: Station, xyz: np.ndarray, env: Environment, frame: SiteFrame) -> np.ndarray:
    """(T, 3): marca + marea + altura a la ARP (en la vertical local)."""
    up = frame.rot[2]
    return xyz[None, :] + tide_series(xyz, env) + up[None, :] * station.height_m
