"""Escritura de RINEX 3.04: observación (mixto) y navegación (mixto).

Formatos según RINEX 3.04 (IGS/RTCM, 2018-11-23): tablas A2 y A3 (observación) y A4 a A13
(navegación). Etiquetas en las columnas 61-80; registros de datos en ancho fijo.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import IO, Iterable

from . import gnsstime
from .model import GlonassEphemeris, KeplerEphemeris
from .signals import PHASE_REFERENCE, SYSTEM_ORDER

OBS_KINDS = "CLDS"


def header_line(content: str, label: str) -> str:
    if len(content) > 60:
        raise ValueError(f"contenido de más de 60 columnas en {label}: {content!r}")
    return f"{content:<60}{label:<20}\n"


def ascii_text(value: str, width: int) -> str:
    """Solo ASCII imprimible: RINEX no admite otra cosa."""
    cleaned = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in (value or ""))
    return cleaned[:width]


def run_date() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d %H%M%S") + " UTC"


@dataclass
class ObsHeader:
    program: str
    run_by: str
    marker_name: str
    observer: str
    agency: str
    receiver_number: str
    receiver_type: str
    receiver_version: str
    antenna_number: str
    antenna_type: str
    antenna_radome: str
    approx_xyz: tuple[float, float, float]
    delta_hen: tuple[float, float, float]
    obs_types: dict[str, list[str]]          # sistema → ["C1C", "L1C", …]
    first_ms: int
    last_ms: int
    interval_s: float | None
    phase_shift: dict[str, list[tuple[str, float | None]] | None]   # None: alineación desconocida
    glonass_slots: dict[int, int]
    comments: list[str] = field(default_factory=list)

    def write(self, out: IO[str]) -> None:
        systems = [s for s in SYSTEM_ORDER if s in self.obs_types]
        kind = systems[0] if len(systems) == 1 else "M"
        system_text = {"G": "G: GPS", "R": "R: GLONASS", "E": "E: Galileo", "J": "J: QZSS", "C": "C: BDS",
                       "I": "I: IRNSS", "S": "S: SBAS payload", "M": "M: Mixed"}[kind]
        out.write(header_line(f"{3.04:9.2f}{'':11}{'OBSERVATION DATA':<20}{system_text:<20}", "RINEX VERSION / TYPE"))
        out.write(header_line(f"{ascii_text(self.program, 20):<20}{ascii_text(self.run_by, 20):<20}{run_date():<20}",
                              "PGM / RUN BY / DATE"))
        for comment in self.comments:
            out.write(header_line(ascii_text(comment, 60), "COMMENT"))
        out.write(header_line(ascii_text(self.marker_name, 60), "MARKER NAME"))
        out.write(header_line(f"{ascii_text(self.observer, 20):<20}{ascii_text(self.agency, 40):<40}",
                              "OBSERVER / AGENCY"))
        out.write(header_line(f"{ascii_text(self.receiver_number, 20):<20}{ascii_text(self.receiver_type, 20):<20}"
                              f"{ascii_text(self.receiver_version, 20):<20}", "REC # / TYPE / VERS"))
        antenna = f"{ascii_text(self.antenna_type, 15):<16}{ascii_text(self.antenna_radome, 4):>4}"
        out.write(header_line(f"{ascii_text(self.antenna_number, 20):<20}{antenna:<20}", "ANT # / TYPE"))
        x, y, z = self.approx_xyz
        out.write(header_line(f"{x:14.4f}{y:14.4f}{z:14.4f}", "APPROX POSITION XYZ"))
        h, e, n = self.delta_hen
        out.write(header_line(f"{h:14.4f}{e:14.4f}{n:14.4f}", "ANTENNA: DELTA H/E/N"))
        for system in systems:
            types = self.obs_types[system]
            for start in range(0, len(types), 13):
                chunk = "".join(f" {t}" for t in types[start:start + 13])
                prefix = f"{system}  {len(types):3d}" if start == 0 else " " * 6
                out.write(header_line(prefix + chunk, "SYS / # / OBS TYPES"))
        out.write(header_line(f"{'DBHZ':<20}", "SIGNAL STRENGTH UNIT"))
        if self.interval_s:
            out.write(header_line(f"{self.interval_s:10.3f}", "INTERVAL"))
        for label, ms in (("TIME OF FIRST OBS", self.first_ms), ("TIME OF LAST OBS", self.last_ms)):
            year, month, day, hour, minute, second = gnsstime.calendar_ms(ms)
            out.write(header_line(f"{year:6d}{month:6d}{day:6d}{hour:6d}{minute:6d}{second:13.7f}{'':5}GPS", label))
        for system in systems:
            shifts = self.phase_shift.get(system)
            if shifts is None:
                out.write(header_line(system, "SYS / PHASE SHIFT"))
                continue
            for code, correction in shifts:
                text = f"{system} {code:3s}" if correction is None else f"{system} {code:3s} {correction:8.5f}"
                out.write(header_line(text, "SYS / PHASE SHIFT"))
        slots = sorted(self.glonass_slots.items())
        if not slots:
            out.write(header_line(f"{0:3d}", "GLONASS SLOT / FRQ #"))
        for start in range(0, len(slots), 8):
            items = "".join(f"R{slot:02d} {fcn:2d} " for slot, fcn in slots[start:start + 8])
            prefix = f"{len(slots):3d} " if start == 0 else " " * 4
            out.write(header_line(prefix + items, "GLONASS SLOT / FRQ #"))
        # Sesgo código-fase GLONASS desconocido: registro en blanco (RINEX 3.04, sección 9.9).
        out.write(header_line("", "GLONASS COD/PHS/BIS"))
        out.write(header_line("", "END OF HEADER"))


def epoch_line(time_ms: int, satellites: int, flag: int = 0) -> str:
    year, month, day, hour, minute, second = gnsstime.calendar_ms(time_ms)
    return f"> {year:4d} {month:02d} {day:02d} {hour:02d} {minute:02d}{second:11.7f}  {flag:1d}{satellites:3d}\n"


def observation_line(sat: str, values: Iterable[tuple[float | None, int]]) -> str:
    parts = [sat]
    for value, lli in values:
        if value is None:
            parts.append(" " * 16)
            continue
        parts.append(f"{value:14.3f}")
        parts.append(str(lli) if lli else " ")
        parts.append(" ")                 # SSI: no se usa (las S van como observación propia)
    return "".join(parts).rstrip() + "\n"


# --- Navegación -----------------------------------------------------------------------------------------
def d19(value: float) -> str:
    if value == 0.0 or abs(value) < 1e-99:
        value = 0.0
    return f"{value:19.12E}"


def _orbit(*values: float) -> str:
    return "    " + "".join(d19(v) for v in values) + "\n"


def _sv_epoch(sat: str, seconds: float, a: float, b: float, c: float) -> str:
    year, month, day, hour, minute, second = gnsstime.calendar(seconds)
    return f"{sat} {year:4d} {month:02d} {day:02d} {hour:02d} {minute:02d} {int(round(second)):02d}" + \
        d19(a) + d19(b) + d19(c) + "\n"


def kepler_record(eph: KeplerEphemeris) -> str:
    system = eph.sat[0]
    if system == "C":
        # Fecha de calendario en BDT: la del instante GPS menos 14 s.
        toc_calendar = eph.toc + gnsstime.BDT_MINUS_GPS_S
        week, toe_sow = gnsstime.gps_to_bdt(eph.toe)
        ttr_week, ttr_sow = gnsstime.gps_to_bdt(eph.ttr if eph.ttr is not None else eph.toe)
    else:
        toc_calendar = eph.toc
        week, toe_sow = gnsstime.week_tow(eph.toe)
        ttr_week, ttr_sow = gnsstime.week_tow(eph.ttr if eph.ttr is not None else eph.toe)
    toe_sow = round(toe_sow, 3)
    ttr = ttr_sow + (ttr_week - week) * gnsstime.SECONDS_PER_WEEK
    lines = [_sv_epoch(eph.sat, toc_calendar, eph.af0, eph.af1, eph.af2),
             _orbit(eph.iode, eph.crs, eph.delta_n, eph.m0),
             _orbit(eph.cuc, eph.ecc, eph.cus, eph.sqrt_a),
             _orbit(toe_sow, eph.cic, eph.omega0, eph.cis),
             _orbit(eph.i0, eph.crc, eph.omega, eph.omega_dot)]
    if system in "GJ":
        lines.append(_orbit(eph.idot, eph.codes_l2, float(week), eph.l2p_flag))
        lines.append(_orbit(eph.accuracy_m, eph.health, eph.tgd, eph.iodc))
        lines.append(_orbit(ttr, eph.fit))
    elif system == "E":
        lines.append(_orbit(eph.idot, eph.data_sources, float(week), 0.0))
        lines.append(_orbit(eph.accuracy_m, eph.health, eph.tgd, eph.tgd2))
        lines.append(_orbit(ttr))
    else:                                   # BDS
        lines.append(_orbit(eph.idot, 0.0, float(week), 0.0))
        lines.append(_orbit(eph.accuracy_m, eph.health, eph.tgd, eph.tgd2))
        lines.append(_orbit(ttr, eph.iodc))
    return "".join(lines)


def glonass_record(eph: GlonassEphemeris) -> str:
    leap = eph.leap if eph.leap is not None else gnsstime.leap_seconds_gps(eph.toe)
    toc_utc = eph.toe - leap
    tof_utc = eph.tof - leap
    _, tof_sow = gnsstime.week_tow(tof_utc)       # segundos de la semana UTC
    return "".join([
        _sv_epoch(eph.sat, toc_utc, -eph.tau_n, eph.gamma_n, tof_sow),
        _orbit(eph.pos_km[0], eph.vel_kms[0], eph.acc_kms2[0], eph.health),
        _orbit(eph.pos_km[1], eph.vel_kms[1], eph.acc_kms2[1], float(eph.fcn)),
        _orbit(eph.pos_km[2], eph.vel_kms[2], eph.acc_kms2[2], eph.age),
    ])


def write_nav(out: IO[str], program: str, run_by: str, comments: list[str], leap: int | None,
              records: list[KeplerEphemeris | GlonassEphemeris]) -> None:
    systems = sorted({r.sat[0] for r in records}, key=SYSTEM_ORDER.find)
    kind = systems[0] if len(systems) == 1 else "M"
    system_text = {"G": "G: GPS", "R": "R: GLONASS", "E": "E: Galileo", "J": "J: QZSS", "C": "C: BDS",
                   "M": "M: Mixed"}.get(kind, "M: Mixed")
    out.write(header_line(f"{3.04:9.2f}{'':11}{'N: GNSS NAV DATA':<20}{system_text:<20}", "RINEX VERSION / TYPE"))
    out.write(header_line(f"{ascii_text(program, 20):<20}{ascii_text(run_by, 20):<20}{run_date():<20}",
                          "PGM / RUN BY / DATE"))
    for comment in comments:
        out.write(header_line(ascii_text(comment, 60), "COMMENT"))
    if leap:
        out.write(header_line(f"{leap:6d}", "LEAP SECONDS"))
    out.write(header_line("", "END OF HEADER"))

    def order(record):
        return (SYSTEM_ORDER.find(record.sat[0]), record.sat, record.toe,
                getattr(record, "data_sources", 0.0))

    for record in sorted(records, key=order):
        out.write(glonass_record(record) if isinstance(record, GlonassEphemeris) else kepler_record(record))


def phase_shift_for(system: str, l_codes: list[str], aligned: bool) -> list[tuple[str, float | None]] | None:
    """Registros SYS / PHASE SHIFT de un sistema. Sin alineación conocida, solo el sistema."""
    if not aligned:
        return None
    reference = PHASE_REFERENCE.get(system, set())
    return [("L" + code, None if code in reference else 0.0) for code in l_codes]
