"""Estructuras comunes a los decodificadores y al escritor RINEX."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SignalObs:
    """Una señal de un satélite en una época. Las magnitudes ausentes son None."""
    code: str                      # código RINEX sin el tipo: «1C», «2W»…
    pseudorange: float | None = None     # m
    phase: float | None = None           # ciclos, ya con el signo RINEX
    doppler: float | None = None         # Hz, positivo si el satélite se acerca
    cn0: float | None = None             # dB-Hz
    # Continuidad de la fase, según el formato de origen:
    lock_time_s: float | None = None     # Unicore: tiempo de enganche continuo (s)
    lock_indicator: int | None = None    # RTCM MSM: indicador DF402/DF407 tal cual
    lock_min_s: float | None = None      # RTCM MSM: mínimo tiempo de enganche que garantiza el indicador
    half_cycle: bool = False             # RTCM MSM DF420: posible ambigüedad de medio ciclo


@dataclass
class Epoch:
    time_ms: int                                   # tiempo GPS en ms desde la época GPS
    satellites: dict[str, dict[str, SignalObs]] = field(default_factory=dict)

    def add(self, sat: str, signal: SignalObs) -> None:
        self.satellites.setdefault(sat, {})[signal.code] = signal


@dataclass
class KeplerEphemeris:
    """Efemérides keplerianas (GPS, Galileo, BeiDou, QZSS). Tiempos en segundos GPS absolutos."""
    sat: str
    toc: float
    toe: float
    ttr: float | None              # transmisión (None: se fija después con la hora del flujo)
    af0: float
    af1: float
    af2: float
    iode: float
    crs: float
    delta_n: float
    m0: float
    cuc: float
    ecc: float
    cus: float
    sqrt_a: float
    cic: float
    omega0: float
    cis: float
    i0: float
    crc: float
    omega: float
    omega_dot: float
    idot: float
    accuracy_m: float              # GPS/QZSS/BDS: URA (m); Galileo: SISA (m)
    health: float
    tgd: float = 0.0               # GPS/QZSS TGD, Galileo BGD E5a/E1, BDS TGD1
    tgd2: float = 0.0              # Galileo BGD E5b/E1, BDS TGD2
    iodc: float = 0.0              # GPS/QZSS IODC, BDS AODC
    codes_l2: float = 0.0
    l2p_flag: float = 0.0
    fit: float = 0.0               # GPS: horas (0 = desconocido); QZSS: bandera
    data_sources: float = 0.0      # Galileo
    source: str = ""               # «unicore» o «rtcm»: solo informativo


@dataclass
class GlonassEphemeris:
    sat: str
    toe: float                     # tiempo GPS absoluto de tb
    tof: float                     # tiempo GPS absoluto del comienzo de la trama (tk)
    tau_n: float
    gamma_n: float
    pos_km: tuple[float, float, float]
    vel_kms: tuple[float, float, float]
    acc_kms2: tuple[float, float, float]
    health: float
    fcn: int
    age: float
    leap: int | None = None
    source: str = ""
