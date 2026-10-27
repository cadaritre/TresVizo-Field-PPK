"""Decodificación de RTCM 10403.3: MSM4 a MSM7, 1005/1006, 1033, 1230 y efemérides.

El LG290P del MeridianVI no tiene un binario de observaciones propio: su «dato crudo» son las MSM
de RTCM 10403.3 (Quectel, «LG290P(03)&LGx80P(03) GNSS Protocol Specification» V1.1, cap. 4 y tabla
5 de PQTMCFGMSGRATE) y las efemérides 1019/1020/1041/1042/1044/1046. El UM980 del MeridianV emite
las mismas MSM cuando trabaja de base.

La norma RTCM no es de libre acceso: la disposición de los campos se tomó de la implementación
abierta de RTKLIB demo5 (src/rtcm3.c, decode_msm4-7, decode_type10xx) y se comprobó contra los
valores que da otra implementación independiente (pyrtcm) para tramas reales (ver tests).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import gnsstime
from .model import Epoch, GlonassEphemeris, KeplerEphemeris, SignalObs
from .signals import C_LIGHT, MSM_SIGNALS, frequency

RANGE_MS = C_LIGHT * 0.001
SC2RAD = 3.1415926535898          # semicírculo → radianes, con el π de las ICD
URA_NOMINAL_M = (2.0, 2.8, 4.0, 5.7, 8.0, 11.3, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0, 1024.0, 2048.0,
                 4096.0, 6144.0)

MSM_SYSTEMS = {107: "G", 108: "R", 109: "E", 110: "S", 111: "J", 112: "C", 113: "I"}


class Bits:
    """Lectura de campos de bits MSB primero sobre el contenido de una trama RTCM."""

    def __init__(self, payload: bytes):
        self.total = len(payload) * 8
        self.value = int.from_bytes(payload, "big")
        self.pos = 0

    def u(self, n: int) -> int:
        if self.pos + n > self.total:
            raise ValueError("trama RTCM corta")
        shift = self.total - self.pos - n
        self.pos += n
        return (self.value >> shift) & ((1 << n) - 1)

    def s(self, n: int) -> int:
        """Entero con signo en complemento a dos."""
        raw = self.u(n)
        return raw - (1 << n) if raw & (1 << (n - 1)) else raw

    def g(self, n: int) -> int:
        """Entero en signo y magnitud (efemérides GLONASS)."""
        raw = self.u(n)
        magnitude = raw & ((1 << (n - 1)) - 1)
        return -magnitude if raw >> (n - 1) else magnitude

    def skip(self, n: int) -> None:
        self.pos += n


def message_type(payload: bytes) -> int:
    return (payload[0] << 4) | (payload[1] >> 4)


def lock_min_df402(indicator: int) -> float:
    """MSM4/5, DF402: tiempo mínimo de enganche (s) que garantiza el indicador."""
    return 0.0 if indicator == 0 else 2.0 ** (indicator + 4) / 1000.0


def lock_min_df407(indicator: int) -> float:
    """MSM6/7, DF407 (10 bits): de 0 a 63 ms en pasos de 1 ms; luego cada 32 valores duplican el paso."""
    if indicator < 64:
        return indicator / 1000.0
    step = indicator // 32 - 1
    if indicator > 704:
        indicator = 704
        step = 21
    return (2 ** step) * (indicator - 32 * step) / 1000.0


@dataclass
class MsmMessage:
    system: str
    msm: int
    station: int
    time_ms: int
    multiple: bool
    epoch: Epoch
    glonass_fcn: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)


def _epoch_time_ms(system: str, raw: int, reference_s: float, leap: int) -> int:
    """Época completa (ms GPS) a partir del tiempo de la MSM, en el día o la semana más cercanos a la
    referencia. GLONASS: hora del día de Moscú (el día de la semana, DF416, no hace falta)."""
    if system == "R":
        utc_tod_ms = (raw - gnsstime.MOSCOW_MINUS_UTC_S * 1000) % 86400000
        reference_utc_ms = round((reference_s - leap) * 1000)
        day_ms = reference_utc_ms - reference_utc_ms % 86400000
        utc_ms = int(gnsstime.nearest_modulo(day_ms + utc_tod_ms, 86400000, reference_utc_ms))
        return utc_ms + leap * 1000
    tow_ms = raw
    if system == "C":
        tow_ms = (raw + 14000) % gnsstime.MS_PER_WEEK
    reference_ms = round(reference_s * 1000)
    week_ms = reference_ms - reference_ms % gnsstime.MS_PER_WEEK
    return int(gnsstime.nearest_modulo(week_ms + tow_ms, gnsstime.MS_PER_WEEK, reference_ms))


def decode_msm(payload: bytes, reference_s: float, leap: int, glonass_fcn: dict[str, int]) -> MsmMessage | None:
    kind = message_type(payload)
    system = MSM_SYSTEMS.get(kind // 10)
    msm = kind % 10
    if system is None or msm not in (4, 5, 6, 7):
        return None
    bits = Bits(payload)
    bits.skip(12)
    station = bits.u(12)
    if system == "R":
        bits.skip(3)                     # DF416, día de la semana
        raw_time = bits.u(27)
    else:
        raw_time = bits.u(30)
    multiple = bool(bits.u(1))
    bits.skip(3 + 7 + 2 + 2 + 1 + 3)        # IODS, tiempo de sesión, reloj, suavizado
    sat_mask = bits.u(64)
    sig_mask = bits.u(32)
    sats = [i + 1 for i in range(64) if sat_mask >> (63 - i) & 1]
    sigs = [i + 1 for i in range(32) if sig_mask >> (31 - i) & 1]
    if len(sats) * len(sigs) > 64:
        return None
    cells = [bits.u(1) for _ in range(len(sats) * len(sigs))]
    ncell = sum(cells)
    nsat = len(sats)
    extended = msm in (5, 7)
    high = msm in (6, 7)

    rough_ms = [bits.u(8) for _ in range(nsat)]
    ext_info = [bits.u(4) for _ in range(nsat)] if extended else [15] * nsat
    rough_mod = [bits.u(10) for _ in range(nsat)]
    rough_rate = [bits.s(14) for _ in range(nsat)] if extended else [-8192] * nsat
    if high:
        fine_pr = [bits.s(20) for _ in range(ncell)]
        fine_cp = [bits.s(24) for _ in range(ncell)]
        lock = [bits.u(10) for _ in range(ncell)]
        half = [bits.u(1) for _ in range(ncell)]
        cnr = [bits.u(10) * 0.0625 for _ in range(ncell)]
    else:
        fine_pr = [bits.s(15) for _ in range(ncell)]
        fine_cp = [bits.s(22) for _ in range(ncell)]
        lock = [bits.u(4) for _ in range(ncell)]
        half = [bits.u(1) for _ in range(ncell)]
        cnr = [float(bits.u(6)) for _ in range(ncell)]
    fine_rate = [bits.s(15) for _ in range(ncell)] if extended else [-16384] * ncell

    pr_invalid, pr_scale = (-524288, 2.0 ** -29) if high else (-16384, 2.0 ** -24)
    cp_invalid, cp_scale = (-8388608, 2.0 ** -31) if high else (-2097152, 2.0 ** -29)
    lock_min = lock_min_df407 if high else lock_min_df402

    time_ms = _epoch_time_ms(system, raw_time, reference_s, leap)
    message = MsmMessage(system, msm, station, time_ms, multiple, Epoch(time_ms))
    table = MSM_SIGNALS[system]
    cell = 0
    for i, sat_number in enumerate(sats):
        if system == "J":
            sat = f"J{sat_number:02d}"
        elif system == "S":
            sat = f"S{sat_number + 19:02d}"
        else:
            sat = f"{system}{sat_number:02d}"
        fcn = None
        if system == "R":
            if extended and ext_info[i] <= 13:
                fcn = ext_info[i] - 7
                message.glonass_fcn[sat] = fcn
            else:
                fcn = glonass_fcn.get(sat)
        rough = None if rough_ms[i] == 255 else (rough_ms[i] + rough_mod[i] / 1024.0) * RANGE_MS
        for k, sig in enumerate(sigs):
            if not cells[i * len(sigs) + k]:
                continue
            j = cell
            cell += 1
            code = table.get(sig)
            if code is None:
                key = f"{system}:msm{sig}"
                message.skipped[key] = message.skipped.get(key, 0) + 1
                continue
            if rough is None:
                continue
            signal = SignalObs(code)
            if fine_pr[j] != pr_invalid:
                signal.pseudorange = rough + fine_pr[j] * pr_scale * RANGE_MS
            f = frequency(system, code, fcn)
            if fine_cp[j] != cp_invalid:
                if f:
                    signal.phase = (rough + fine_cp[j] * cp_scale * RANGE_MS) * f / C_LIGHT
                    signal.lock_indicator = lock[j]
                    signal.lock_min_s = lock_min(lock[j])
                    signal.half_cycle = bool(half[j])
                else:
                    message.skipped["glonass_sin_canal"] = message.skipped.get("glonass_sin_canal", 0) + 1
            if extended and f and rough_rate[i] != -8192 and fine_rate[j] != -16384:
                signal.doppler = -(rough_rate[i] + fine_rate[j] * 0.0001) * f / C_LIGHT
            if cnr[j] > 0:
                signal.cn0 = cnr[j]
            if signal.pseudorange is None and signal.phase is None:
                continue
            message.epoch.add(sat, signal)
    return message


# --- Mensajes de estación ----------------------------------------------------------------------------
@dataclass
class StationPosition:
    station: int
    ecef: tuple[float, float, float]
    antenna_height: float | None


def decode_1005_1006(payload: bytes) -> StationPosition | None:
    kind = message_type(payload)
    bits = Bits(payload)
    bits.skip(12)
    station = bits.u(12)
    bits.skip(6 + 1 + 1 + 1 + 1)       # ITRF, indicadores GPS/GLO/GAL, estación de referencia
    x = bits.s(38) * 0.0001
    bits.skip(2)                       # oscilador único, reservado
    y = bits.s(38) * 0.0001
    bits.skip(2)                       # indicador de cuarto de ciclo
    z = bits.s(38) * 0.0001
    height = bits.u(16) * 0.0001 if kind == 1006 else None
    return StationPosition(station, (x, y, z), height)


def decode_1033(payload: bytes) -> dict:
    bits = Bits(payload)
    bits.skip(12)
    station = bits.u(12)

    def text() -> str:
        n = bits.u(8)
        return bytes(bits.u(8) for _ in range(n)).decode("ascii", "replace")

    antenna = text()
    setup = bits.u(8)
    antenna_serial = text()
    receiver = text()
    firmware = text()
    receiver_serial = text()
    return {"station": station, "antenna": antenna, "antenna_setup": setup, "antenna_serial": antenna_serial,
            "receiver": receiver, "firmware": firmware, "receiver_serial": receiver_serial}


# --- Efemérides ----------------------------------------------------------------------------------------
def _ura(index: int) -> float:
    return URA_NOMINAL_M[index] if 0 <= index < len(URA_NOMINAL_M) else 6144.0


def _gps_fit_hours(flag: int, iodc: int) -> float:
    """IS-GPS-200, tabla 20-XII: intervalo de ajuste según la bandera y el IODC."""
    if not flag:
        return 4.0
    if 240 <= iodc <= 247:
        return 8.0
    if 248 <= iodc <= 255 or iodc == 496:
        return 14.0
    if 497 <= iodc <= 503 or 1021 <= iodc <= 1023:
        return 26.0
    if 504 <= iodc <= 510:
        return 50.0
    if iodc == 511 or 752 <= iodc <= 756:
        return 74.0
    if iodc == 757:
        return 98.0
    return 6.0


def _week_near(week_mod: int, modulo: int, reference_s: float) -> int:
    reference_week = int(reference_s // gnsstime.SECONDS_PER_WEEK)
    return int(gnsstime.nearest_modulo(week_mod, modulo, reference_week))


def _toe_near(week: int, toe_sow: float, reference_s: float) -> float:
    toe = gnsstime.gps_seconds(week, toe_sow)
    return gnsstime.nearest_week(toe_sow, reference_s) if abs(toe - reference_s) > gnsstime.SECONDS_PER_WEEK / 2 else toe


def decode_1019(payload: bytes, reference_s: float, ttr: float | None) -> KeplerEphemeris | None:
    b = Bits(payload)
    b.skip(12)
    prn = b.u(6)
    week = b.u(10)
    sva = b.u(4)
    codes = b.u(2)
    idot = b.s(14) * 2.0 ** -43 * SC2RAD
    iode = b.u(8)
    toc = b.u(16) * 16.0
    af2 = b.s(8) * 2.0 ** -55
    af1 = b.s(16) * 2.0 ** -43
    af0 = b.s(22) * 2.0 ** -31
    iodc = b.u(10)
    crs = b.s(16) * 2.0 ** -5
    delta_n = b.s(16) * 2.0 ** -43 * SC2RAD
    m0 = b.s(32) * 2.0 ** -31 * SC2RAD
    cuc = b.s(16) * 2.0 ** -29
    ecc = b.u(32) * 2.0 ** -33
    cus = b.s(16) * 2.0 ** -29
    sqrt_a = b.u(32) * 2.0 ** -19
    toes = b.u(16) * 16.0
    cic = b.s(16) * 2.0 ** -29
    omega0 = b.s(32) * 2.0 ** -31 * SC2RAD
    cis = b.s(16) * 2.0 ** -29
    i0 = b.s(32) * 2.0 ** -31 * SC2RAD
    crc = b.s(16) * 2.0 ** -5
    omega = b.s(32) * 2.0 ** -31 * SC2RAD
    omega_dot = b.s(24) * 2.0 ** -43 * SC2RAD
    tgd = b.s(8) * 2.0 ** -31
    health = b.u(6)
    l2p = b.u(1)
    fit = b.u(1)
    if not 1 <= prn <= 32 or sqrt_a <= 0:
        return None
    full_week = _week_near(week, 1024, reference_s)
    toe = _toe_near(full_week, toes, reference_s)
    return KeplerEphemeris(
        sat=f"G{prn:02d}", toc=gnsstime.nearest_week(toc, toe), toe=toe, ttr=ttr, af0=af0, af1=af1, af2=af2,
        iode=float(iode), crs=crs, delta_n=delta_n, m0=m0, cuc=cuc, ecc=ecc, cus=cus, sqrt_a=sqrt_a, cic=cic,
        omega0=omega0, cis=cis, i0=i0, crc=crc, omega=omega, omega_dot=omega_dot, idot=idot,
        accuracy_m=_ura(sva), health=float(health), tgd=tgd, iodc=float(iodc), codes_l2=float(codes),
        l2p_flag=float(l2p), fit=_gps_fit_hours(fit, iodc), source="rtcm")


def decode_1044(payload: bytes, reference_s: float, ttr: float | None) -> KeplerEphemeris | None:
    b = Bits(payload)
    b.skip(12)
    prn = b.u(4)
    toc = b.u(16) * 16.0
    af2 = b.s(8) * 2.0 ** -55
    af1 = b.s(16) * 2.0 ** -43
    af0 = b.s(22) * 2.0 ** -31
    iode = b.u(8)
    crs = b.s(16) * 2.0 ** -5
    delta_n = b.s(16) * 2.0 ** -43 * SC2RAD
    m0 = b.s(32) * 2.0 ** -31 * SC2RAD
    cuc = b.s(16) * 2.0 ** -29
    ecc = b.u(32) * 2.0 ** -33
    cus = b.s(16) * 2.0 ** -29
    sqrt_a = b.u(32) * 2.0 ** -19
    toes = b.u(16) * 16.0
    cic = b.s(16) * 2.0 ** -29
    omega0 = b.s(32) * 2.0 ** -31 * SC2RAD
    cis = b.s(16) * 2.0 ** -29
    i0 = b.s(32) * 2.0 ** -31 * SC2RAD
    crc = b.s(16) * 2.0 ** -5
    omega = b.s(32) * 2.0 ** -31 * SC2RAD
    omega_dot = b.s(24) * 2.0 ** -43 * SC2RAD
    idot = b.s(14) * 2.0 ** -43 * SC2RAD
    codes = b.u(2)
    week = b.u(10)
    sva = b.u(4)
    health = b.u(6)
    tgd = b.s(8) * 2.0 ** -31
    iodc = b.u(10)
    fit = b.u(1)
    if not 1 <= prn <= 10 or sqrt_a <= 0:
        return None
    full_week = _week_near(week, 1024, reference_s)
    toe = _toe_near(full_week, toes, reference_s)
    return KeplerEphemeris(
        sat=f"J{prn:02d}", toc=gnsstime.nearest_week(toc, toe), toe=toe, ttr=ttr, af0=af0, af1=af1, af2=af2,
        iode=float(iode), crs=crs, delta_n=delta_n, m0=m0, cuc=cuc, ecc=ecc, cus=cus, sqrt_a=sqrt_a, cic=cic,
        omega0=omega0, cis=cis, i0=i0, crc=crc, omega=omega, omega_dot=omega_dot, idot=idot,
        accuracy_m=_ura(sva), health=float(health), tgd=tgd, iodc=float(iodc), codes_l2=float(codes),
        fit=float(fit), source="rtcm")


def decode_1042(payload: bytes, reference_s: float, ttr: float | None) -> KeplerEphemeris | None:
    b = Bits(payload)
    b.skip(12)
    prn = b.u(6)
    week = b.u(13)
    sva = b.u(4)
    idot = b.s(14) * 2.0 ** -43 * SC2RAD
    aode = b.u(5)
    toc = b.u(17) * 8.0
    af2 = b.s(11) * 2.0 ** -66
    af1 = b.s(22) * 2.0 ** -50
    af0 = b.s(24) * 2.0 ** -33
    aodc = b.u(5)
    crs = b.s(18) * 2.0 ** -6
    delta_n = b.s(16) * 2.0 ** -43 * SC2RAD
    m0 = b.s(32) * 2.0 ** -31 * SC2RAD
    cuc = b.s(18) * 2.0 ** -31
    ecc = b.u(32) * 2.0 ** -33
    cus = b.s(18) * 2.0 ** -31
    sqrt_a = b.u(32) * 2.0 ** -19
    toes = b.u(17) * 8.0
    cic = b.s(18) * 2.0 ** -31
    omega0 = b.s(32) * 2.0 ** -31 * SC2RAD
    cis = b.s(18) * 2.0 ** -31
    i0 = b.s(32) * 2.0 ** -31 * SC2RAD
    crc = b.s(18) * 2.0 ** -6
    omega = b.s(32) * 2.0 ** -31 * SC2RAD
    omega_dot = b.s(24) * 2.0 ** -43 * SC2RAD
    tgd1 = b.s(10) * 1e-10
    tgd2 = b.s(10) * 1e-10
    health = b.u(1)
    if not 1 <= prn <= 63 or sqrt_a <= 0:
        return None
    # Semana BDT de 13 bits: sin ambigüedad práctica (8192 semanas).
    toe = gnsstime.bdt_to_gps(week, toes)
    if abs(toe - reference_s) > gnsstime.SECONDS_PER_WEEK / 2:
        toe = gnsstime.nearest_modulo(toe, gnsstime.SECONDS_PER_WEEK, reference_s)
    toc_s = gnsstime.nearest_modulo(gnsstime.bdt_to_gps(week, toc), gnsstime.SECONDS_PER_WEEK, toe)
    return KeplerEphemeris(
        sat=f"C{prn:02d}", toc=toc_s, toe=toe, ttr=ttr, af0=af0, af1=af1, af2=af2, iode=float(aode), crs=crs,
        delta_n=delta_n, m0=m0, cuc=cuc, ecc=ecc, cus=cus, sqrt_a=sqrt_a, cic=cic, omega0=omega0, cis=cis,
        i0=i0, crc=crc, omega=omega, omega_dot=omega_dot, idot=idot, accuracy_m=_ura(sva),
        health=float(health), tgd=tgd1, tgd2=tgd2, iodc=float(aodc), source="rtcm")


def _galileo_common(b: Bits) -> dict:
    out = {}
    out["prn"] = b.u(6)
    out["week"] = b.u(12)
    out["iode"] = b.u(10)
    out["sisa"] = b.u(8)
    out["idot"] = b.s(14) * 2.0 ** -43 * SC2RAD
    out["toc"] = b.u(14) * 60.0
    out["af2"] = b.s(6) * 2.0 ** -59
    out["af1"] = b.s(21) * 2.0 ** -46
    out["af0"] = b.s(31) * 2.0 ** -34
    out["crs"] = b.s(16) * 2.0 ** -5
    out["delta_n"] = b.s(16) * 2.0 ** -43 * SC2RAD
    out["m0"] = b.s(32) * 2.0 ** -31 * SC2RAD
    out["cuc"] = b.s(16) * 2.0 ** -29
    out["ecc"] = b.u(32) * 2.0 ** -33
    out["cus"] = b.s(16) * 2.0 ** -29
    out["sqrt_a"] = b.u(32) * 2.0 ** -19
    out["toes"] = b.u(14) * 60.0
    out["cic"] = b.s(16) * 2.0 ** -29
    out["omega0"] = b.s(32) * 2.0 ** -31 * SC2RAD
    out["cis"] = b.s(16) * 2.0 ** -29
    out["i0"] = b.s(32) * 2.0 ** -31 * SC2RAD
    out["crc"] = b.s(16) * 2.0 ** -5
    out["omega"] = b.s(32) * 2.0 ** -31 * SC2RAD
    out["omega_dot"] = b.s(24) * 2.0 ** -43 * SC2RAD
    out["bgd_e5a"] = b.s(10) * 2.0 ** -32
    return out


def decode_galileo(payload: bytes, reference_s: float, ttr: float | None) -> KeplerEphemeris | None:
    """1045 (F/NAV) y 1046 (I/NAV)."""
    from .unicore import galileo_sisa_m
    kind = message_type(payload)
    b = Bits(payload)
    b.skip(12)
    v = _galileo_common(b)
    if kind == 1046:
        bgd_e5b = b.s(10) * 2.0 ** -32
        e5b_hs = b.u(2)
        e5b_dvs = b.u(1)
        e1_hs = b.u(2)
        e1_dvs = b.u(1)
        health = e1_dvs | (e1_hs << 1) | (e5b_dvs << 6) | (e5b_hs << 7)
        sources = 517.0
    else:
        bgd_e5b = 0.0
        e5a_hs = b.u(2)
        e5a_dvs = b.u(1)
        health = (e5a_dvs << 3) | (e5a_hs << 4)
        sources = 258.0
    if not 1 <= v["prn"] <= 36 or v["sqrt_a"] <= 0:
        return None
    week = v["week"] + 1024          # semana GST + 1024 = semana GPS continua
    toe = _toe_near(week, v["toes"], reference_s)
    return KeplerEphemeris(
        sat=f"E{v['prn']:02d}", toc=gnsstime.nearest_week(v["toc"], toe), toe=toe, ttr=ttr, af0=v["af0"],
        af1=v["af1"], af2=v["af2"], iode=float(v["iode"]), crs=v["crs"], delta_n=v["delta_n"], m0=v["m0"],
        cuc=v["cuc"], ecc=v["ecc"], cus=v["cus"], sqrt_a=v["sqrt_a"], cic=v["cic"], omega0=v["omega0"],
        cis=v["cis"], i0=v["i0"], crc=v["crc"], omega=v["omega"], omega_dot=v["omega_dot"], idot=v["idot"],
        accuracy_m=galileo_sisa_m(v["sisa"]), health=float(health), tgd=v["bgd_e5a"], tgd2=bgd_e5b,
        data_sources=sources, source="rtcm")


def decode_1020(payload: bytes, reference_s: float, leap: int) -> GlonassEphemeris | None:
    b = Bits(payload)
    b.skip(12)
    slot = b.u(6)
    fcn = b.u(5) - 7
    b.skip(1 + 1 + 2)                   # Cn, Cn_a, P1
    tk_h = b.u(5)
    tk_m = b.u(6)
    tk_s = b.u(1) * 30
    bn = b.u(1)
    b.skip(1)                           # P2
    tb = b.u(7)
    vel_x = b.g(24) * 2.0 ** -20
    pos_x = b.g(27) * 2.0 ** -11
    acc_x = b.g(5) * 2.0 ** -30
    vel_y = b.g(24) * 2.0 ** -20
    pos_y = b.g(27) * 2.0 ** -11
    acc_y = b.g(5) * 2.0 ** -30
    vel_z = b.g(24) * 2.0 ** -20
    pos_z = b.g(27) * 2.0 ** -11
    acc_z = b.g(5) * 2.0 ** -30
    b.skip(1)                           # P3
    gamma = b.g(11) * 2.0 ** -40
    b.skip(2 + 1)                       # P, ln
    tau_n = b.g(22) * 2.0 ** -30
    b.skip(5)                           # Δτn
    age = b.u(5)
    if not 1 <= slot <= 24:
        return None
    reference_utc = reference_s - leap
    day = reference_utc - reference_utc % 86400
    toe_utc = gnsstime.nearest_modulo(day + (tb * 900 - gnsstime.MOSCOW_MINUS_UTC_S) % 86400, 86400, reference_utc)
    tof_tod = (tk_h * 3600 + tk_m * 60 + tk_s - gnsstime.MOSCOW_MINUS_UTC_S) % 86400
    tof_utc = gnsstime.nearest_modulo(day + tof_tod, 86400, toe_utc)
    # Posición, velocidad y aceleración ya vienen en km, km/s y km/s² (DF110-DF125).
    return GlonassEphemeris(
        sat=f"R{slot:02d}", toe=toe_utc + leap, tof=tof_utc + leap, tau_n=tau_n, gamma_n=gamma,
        pos_km=(pos_x, pos_y, pos_z), vel_kms=(vel_x, vel_y, vel_z), acc_kms2=(acc_x, acc_y, acc_z),
        health=float(bn), fcn=fcn, age=float(age), leap=leap, source="rtcm")


def decode_week_hint(payload: bytes) -> tuple[str, int] | None:
    """Semana de una efeméride RTCM, para fijar la semana de las MSM sin otra referencia:
    («gps10», semana mod 1024), («gal», semana GST) o («bdt», semana BDT)."""
    kind = message_type(payload)
    b = Bits(payload)
    b.skip(12)
    try:
        if kind == 1019:
            b.skip(6)
            return "gps10", b.u(10)
        if kind in (1045, 1046):
            b.skip(6)
            return "gal", b.u(12)
        if kind == 1042:
            b.skip(6)
            return "bdt", b.u(13)
    except ValueError:
        return None
    return None
