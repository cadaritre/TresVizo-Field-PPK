"""Decodificación del binario nativo Unicore (UM980).

Formato según Unicore, «N4 Products Commands and Logs Reference Book» R1.6 (oct. 2024):
- Cabecera binaria (tabla 7-50): sincronismo AA 44 B5, CPU, Message ID, longitud de los datos,
  referencia de tiempo, estado del tiempo, semana GPS, milisegundos de la semana, versión,
  reservado, segundos intercalares y retardo de salida: 24 bytes.
- OBSVM, Message ID 12 (tabla 7-53): número de observaciones y, por cada señal, 40 bytes.
- GPSEPH 106, GLOEPH 107, BDSEPH 108, GALEPH 109, QZSSEPH 110 (tablas 7-73 a 7-80).
- BD3EPH 2999 (B1C/B2a/B2b de BDS-3, CNAV) no tiene registro en RINEX 3.04: se cuenta y se omite.

Contrastado con RTKLIB demo5 (src/rcv/unicore.c): la fase se escribe como −adr (el ADR de Unicore
lleva el signo contrario a la fase RINEX), y el Doppler tal cual. Las dos cosas se comprobaron con
la captura real del UM980 (ver tests): la pseudodistancia y −adr·λ crecen juntas y el Doppler es
positivo cuando la pseudodistancia disminuye.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from . import gnsstime
from .model import Epoch, GlonassEphemeris, KeplerEphemeris, SignalObs
from .signals import UNICORE_SYSTEMS, unicore_code

ID_OBSVM = 12
ID_GPSEPH = 106
ID_GLOEPH = 107
ID_BDSEPH = 108
ID_GALEPH = 109
ID_QZSSEPH = 110
ID_BD3EPH = 2999
HEADER = struct.Struct("<3sBHHBBHIIBBH")
OBS_RECORD = struct.Struct("<HHddHHfHHfI")


@dataclass
class Header:
    message_id: int
    length: int
    time_ref: int
    time_status: int
    week: int
    tow_ms: int
    leap: int

    @property
    def gps_ms(self) -> int:
        return gnsstime.gps_ms(self.week, self.tow_ms)

    @property
    def gps_s(self) -> float:
        return gnsstime.gps_seconds(self.week, self.tow_ms / 1000.0)


def parse_header(frame: bytes) -> Header:
    (_, _cpu, message_id, length, time_ref, time_status, week, tow_ms, _version, _reserved, leap,
     _delay) = HEADER.unpack_from(frame, 0)
    return Header(message_id, length, time_ref, time_status, week, tow_ms, leap)


@dataclass
class ObsvmResult:
    epoch: Epoch | None
    glonass_fcn: dict[str, int]
    skipped_signals: dict[str, int]
    reason: str | None = None


def decode_obsvm(frame: bytes, header: Header) -> ObsvmResult:
    """OBSVM → una época. Solo con referencia de tiempo GPST (TimeRef 0) y semana distinta de 0."""
    skipped: dict[str, int] = {}
    fcn: dict[str, int] = {}
    if header.week == 0:
        return ObsvmResult(None, fcn, skipped, "sin_tiempo")
    if header.time_ref != 0:
        # El manual solo dice «GPST o BDST» sin dar los valores binarios; el firmware no configura
        # BDST y la captura real trae siempre 0 (GPS). Otra referencia no se interpreta.
        return ObsvmResult(None, fcn, skipped, "referencia_de_tiempo_desconocida")
    payload = memoryview(frame)[24:24 + header.length]
    count = struct.unpack_from("<I", payload, 0)[0]
    if 4 + 40 * count > len(payload):
        return ObsvmResult(None, fcn, skipped, "longitud")
    epoch = Epoch(header.gps_ms)
    for (freq, prn, psr, adr, _psr_std, _adr_std, dopp, cn0, _reserved, lock_time,
         status) in OBS_RECORD.iter_unpack(payload[4:4 + 40 * count]):
        system = UNICORE_SYSTEMS.get((status >> 16) & 7)
        signal_type = (status >> 21) & 0x1F
        l2c = (status >> 26) & 1
        phase_valid = (status >> 10) & 1
        code_valid = (status >> 12) & 1
        if system is None:
            skipped["sistema_desconocido"] = skipped.get("sistema_desconocido", 0) + 1
            continue
        # Numeración de la tabla 7-54: GLONASS 38-61 (ranura + 37), QZSS 193-202, SBAS 120-158.
        if system == "R":
            slot = prn - 37
            if not 1 <= slot <= 24:
                skipped["glonass_sin_ranura"] = skipped.get("glonass_sin_ranura", 0) + 1
                continue
            sat = f"R{slot:02d}"
            fcn[sat] = freq - 7
        elif system == "J":
            if not 193 <= prn <= 202:
                skipped["prn_fuera_de_rango"] = skipped.get("prn_fuera_de_rango", 0) + 1
                continue
            sat = f"J{prn - 192:02d}"
        elif system == "S":
            if 120 <= prn <= 158:
                sat = f"S{prn - 100:02d}"
            else:
                skipped["prn_fuera_de_rango"] = skipped.get("prn_fuera_de_rango", 0) + 1
                continue
        else:
            limit = {"G": 32, "E": 36, "C": 63, "I": 14}[system]
            if not 1 <= prn <= limit:
                skipped["prn_fuera_de_rango"] = skipped.get("prn_fuera_de_rango", 0) + 1
                continue
            sat = f"{system}{prn:02d}"
        code = unicore_code(system, signal_type, l2c)
        if code is None:
            key = f"{system}:tipo{signal_type}"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        signal = SignalObs(code)
        if code_valid and psr > 0:
            signal.pseudorange = psr
        if phase_valid and adr != 0.0:
            signal.phase = -adr
            signal.lock_time_s = float(lock_time)
            # Sin fase enganchada el manual no dice si el Doppler vale: se descarta, como en RTKLIB.
            signal.doppler = float(dopp)
        if cn0 > 0:
            signal.cn0 = cn0 / 100.0
        if signal.pseudorange is None and signal.phase is None:
            skipped["sin_medida_valida"] = skipped.get("sin_medida_valida", 0) + 1
            continue
        epoch.add(sat, signal)
    return ObsvmResult(epoch, fcn, skipped)


# --- Efemérides -----------------------------------------------------------------------------------
_GPS = struct.Struct("<IdIIIIId" + "d" * 15 + "Id" + "d" * 4 + "Idd")   # 224 bytes
_BDS = struct.Struct("<IdIIIIId" + "d" * 15 + "Id" + "d" * 5 + "Idd")   # 232 bytes
_GLO = struct.Struct("<HHBBHIIHBBII" + "d" * 12 + "IIIII")             # 144 bytes
_GAL = struct.Struct("<III7BBII" + "d" * 15 + "Iddd" + "Iddd" + "dd")   # 220 bytes

def galileo_sisa_m(index: int) -> float:
    """Índice SISA (Galileo OS SIS ICD, tabla 76) → metros; 255 (NAPA) → −1."""
    if index <= 49:
        return index * 0.01
    if index <= 74:
        return 0.5 + (index - 50) * 0.02
    if index <= 99:
        return 1.0 + (index - 75) * 0.04
    if index <= 125:
        return 2.0 + (index - 100) * 0.16
    return -1.0


def _variance_to_m(value: float) -> float:
    # El manual da el URA «como el cuadrado (varianza) del valor nominal», en m².
    return math.sqrt(value) if value > 0 else 0.0


def _near(seconds_of_week: float, reference: float) -> float:
    return gnsstime.nearest_week(seconds_of_week, reference)


def decode_gps_like(frame: bytes, header: Header, qzss: bool) -> KeplerEphemeris | None:
    payload = frame[24:24 + header.length]
    if len(payload) < _GPS.size:
        return None
    (prn, tow, health, iode1, _iode2, week, zweek, toe, a, delta_n, m0, ecc, omega, cuc, cus, crc, crs,
     cic, cis, i0, idot, omega0, omega_dot, iodc, toc, tgd, af0, af1, af2, _as, _n,
     ura) = _GPS.unpack_from(payload, 0)
    if qzss:
        if not 1 <= prn <= 10:
            return None
        sat = f"J{prn:02d}"
    else:
        if not 1 <= prn <= 32:
            return None
        sat = f"G{prn:02d}"
    if week == 0 or a <= 0:
        return None
    toe_s = gnsstime.gps_seconds(week, toe)
    toc_s = gnsstime.nearest_week(toc, toe_s)
    # «Tow»: hora de la subtrama (transmisión) en la semana Z; si no hay semana Z, la de toe.
    ttr = gnsstime.gps_seconds(zweek if zweek else week, tow)
    if abs(ttr - toe_s) > 3 * 86400:
        ttr = gnsstime.nearest_week(tow, toe_s)
    return KeplerEphemeris(
        sat=sat, toc=toc_s, toe=toe_s, ttr=ttr, af0=af0, af1=af1, af2=af2, iode=iode1, crs=crs,
        delta_n=delta_n, m0=m0, cuc=cuc, ecc=ecc, cus=cus, sqrt_a=math.sqrt(a), cic=cic, omega0=omega0,
        cis=cis, i0=i0, crc=crc, omega=omega, omega_dot=omega_dot, idot=idot,
        accuracy_m=_variance_to_m(ura), health=float(health & 0x3F), tgd=tgd, iodc=float(iodc),
        source="unicore")


def decode_bds(frame: bytes, header: Header) -> KeplerEphemeris | None:
    payload = frame[24:24 + header.length]
    if len(payload) < _BDS.size:
        return None
    (prn, _tow, health, aode, _aode2, week, _zweek, toe, a, delta_n, m0, ecc, omega, cuc, cus, crc, crs,
     cic, cis, i0, idot, omega0, omega_dot, aodc, toc, tgd1, tgd2, af0, af1, af2, _as, _n,
     ura) = _BDS.unpack_from(payload, 0)
    if not 1 <= prn <= 63 or week == 0 or a <= 0:
        return None
    # «Week» es la semana GPS. toe y toc vienen en segundos BDT: en la captura real son múltiplos de 8
    # y caen en horas exactas BDT (7200, 0…), como emite BeiDou; con hora GPS serían BDT+14.
    bdt_week = week - gnsstime.BDT_WEEK_OFFSET
    toe_s = gnsstime.bdt_to_gps(bdt_week, toe)
    toc_s = gnsstime.nearest_modulo(gnsstime.bdt_to_gps(bdt_week, toc), gnsstime.SECONDS_PER_WEEK, toe_s)
    # El campo «Tow» de la subtrama 1 no se usa: en la captura real repite toe. La transmisión se
    # toma, por lo alto, como la hora de salida del mensaje.
    return KeplerEphemeris(
        sat=f"C{prn:02d}", toc=toc_s, toe=toe_s, ttr=header.gps_s, af0=af0, af1=af1, af2=af2,
        iode=float(aode), crs=crs, delta_n=delta_n, m0=m0, cuc=cuc, ecc=ecc, cus=cus, sqrt_a=math.sqrt(a),
        cic=cic, omega0=omega0, cis=cis, i0=i0, crc=crc, omega=omega, omega_dot=omega_dot, idot=idot,
        accuracy_m=_variance_to_m(ura), health=float(health), tgd=tgd1, tgd2=tgd2, iodc=float(aodc),
        source="unicore")


def decode_galileo(frame: bytes, header: Header) -> list[KeplerEphemeris]:
    """Un GALEPH lleva la órbita una vez y los relojes I/NAV y F/NAV por separado: sale un registro
    RINEX por cada mensaje de navegación recibido (fuentes de datos 517 = I/NAV y 258 = F/NAV)."""
    payload = frame[24:24 + header.length]
    if len(payload) < _GAL.size:
        return []
    (sat_id, fnav, inav, hs_e1b, hs_e5a, hs_e5b, dvs_e1b, dvs_e5a, dvs_e5b, sisa, _reserved, iodnav, toe,
     root_a, delta_n, m0, ecc, omega, cuc, cus, crc, crs, cic, cis, i0, idot, omega0, omega_dot,
     toc_f, af0_f, af1_f, af2_f, toc_i, af0_i, af1_i, af2_i, bgd_e5a, bgd_e5b) = _GAL.unpack_from(payload, 0)
    if not 1 <= sat_id <= 36 or root_a <= 0:
        return []
    toe_s = gnsstime.nearest_week(float(toe), header.gps_s)
    common = dict(sat=f"E{sat_id:02d}", toe=toe_s, ttr=header.gps_s, iode=float(iodnav), crs=crs,
                  delta_n=delta_n, m0=m0, cuc=cuc, ecc=ecc, cus=cus, sqrt_a=root_a, cic=cic, omega0=omega0,
                  cis=cis, i0=i0, crc=crc, omega=omega, omega_dot=omega_dot, idot=idot,
                  accuracy_m=galileo_sisa_m(sisa), source="unicore")
    out = []
    if inav & 1:
        health = (dvs_e1b & 1) | ((hs_e1b & 3) << 1) | ((dvs_e5b & 1) << 6) | ((hs_e5b & 3) << 7)
        out.append(KeplerEphemeris(toc=gnsstime.nearest_week(float(toc_i), toe_s), af0=af0_i, af1=af1_i,
                                   af2=af2_i, health=float(health), tgd=bgd_e5a, tgd2=bgd_e5b,
                                   data_sources=517.0, **common))
    if fnav & 1:
        health = ((dvs_e5a & 1) << 3) | ((hs_e5a & 3) << 4)
        out.append(KeplerEphemeris(toc=gnsstime.nearest_week(float(toc_f), toe_s), af0=af0_f, af1=af1_f,
                                   af2=af2_f, health=float(health), tgd=bgd_e5a, tgd2=0.0,
                                   data_sources=258.0, **common))
    return out


def decode_glonass(frame: bytes, header: Header) -> GlonassEphemeris | None:
    payload = frame[24:24 + header.length]
    if len(payload) < _GLO.size:
        return None
    (slot_id, freq, _sat_type, _res0, e_week, e_time_ms, _t_offset, _nt, _res1, _res2, _issue, health,
     px, py, pz, vx, vy, vz, ax, ay, az, tau_n, _dtau_n, gamma, tk, _p, _ft, age,
     _flags) = _GLO.unpack_from(payload, 0)
    slot = slot_id - 37
    if not 1 <= slot <= 24 or e_week == 0 or not 0 <= freq <= 20:
        return None
    toe = gnsstime.gps_seconds(e_week, e_time_ms / 1000.0)
    leap = header.leap if header.leap else gnsstime.leap_seconds_gps(toe)
    # tk: comienzo de la trama en segundos del día GLONASS (hora de Moscú). Se lleva a UTC y al día
    # que deja la trama a menos de 12 h de tb.
    toe_utc = toe - leap
    frame_utc_tod = (tk - gnsstime.MOSCOW_MINUS_UTC_S) % 86400
    day_start = toe_utc - (toe_utc % 86400)
    tof_utc = gnsstime.nearest_modulo(day_start + frame_utc_tod, 86400, toe_utc)
    return GlonassEphemeris(
        sat=f"R{slot:02d}", toe=toe, tof=tof_utc + leap, tau_n=tau_n, gamma_n=gamma,
        pos_km=(px / 1e3, py / 1e3, pz / 1e3), vel_kms=(vx / 1e3, vy / 1e3, vz / 1e3),
        acc_kms2=(ax / 1e3, ay / 1e3, az / 1e3), health=0.0 if health == 0 else 1.0, fcn=freq - 7,
        age=float(age), leap=leap, source="unicore")
