"""Señales: frecuencias, códigos RINEX 3.04 y tablas de traducción de cada formato de entrada.

Fuentes:
- RINEX 3.04 (IGS/RTCM, 2018-11-23): tablas 4 a 10 de códigos de observación y tabla A23 de
  señales de referencia para la alineación de fase.
- Unicore N4 R1.6, tabla 7-56 «Channel Tracking Status» (tipo de señal de OBSVM).
- RTCM 10403.3, identificadores de señal MSM, contrastados con la implementación abierta de
  RTKLIB demo5 (src/rtcm3.c, `msm_sig_*`). Solo se usan los identificadores normalizados; los que
  RTKLIB marca como provisionales (extensiones de PocketSDR) se descartan y se cuentan.
"""
from __future__ import annotations

C_LIGHT = 299792458.0

SYSTEM_ORDER = "GRECJIS"
SYSTEM_NAMES = {
    "G": "GPS", "R": "GLONASS", "E": "Galileo", "C": "BeiDou", "J": "QZSS", "I": "NavIC/IRNSS", "S": "SBAS",
}

_FREQ = {
    "G": {"1": 1575.42e6, "2": 1227.60e6, "5": 1176.45e6},
    "J": {"1": 1575.42e6, "2": 1227.60e6, "5": 1176.45e6, "6": 1278.75e6},
    "E": {"1": 1575.42e6, "5": 1176.45e6, "7": 1207.140e6, "8": 1191.795e6, "6": 1278.75e6},
    "C": {"2": 1561.098e6, "1": 1575.42e6, "5": 1176.45e6, "7": 1207.140e6, "8": 1191.795e6,
          "6": 1268.52e6},
    "I": {"5": 1176.45e6, "9": 2492.028e6},
    "S": {"1": 1575.42e6, "5": 1176.45e6},
}


def frequency(system: str, code: str, fcn: int | None = None) -> float | None:
    """Frecuencia portadora (Hz) de un código RINEX («1C», «2W»…). GLONASS FDMA necesita el canal."""
    band = code[0]
    if system == "R":
        if band == "1":
            return None if fcn is None else 1602.0e6 + fcn * 0.5625e6
        if band == "2":
            return None if fcn is None else 1246.0e6 + fcn * 0.4375e6
        return {"3": 1202.025e6, "4": 1600.995e6, "6": 1248.06e6}.get(band)
    return _FREQ.get(system, {}).get(band)


def wavelength(system: str, code: str, fcn: int | None = None) -> float | None:
    f = frequency(system, code, fcn)
    return C_LIGHT / f if f else None


# --- Unicore OBSVM: (sistema de la palabra de estado, tipo de señal, bandera L2C) → (sistema RINEX, código)
# Sistema (bits 16-18): 0 GPS, 1 GLONASS, 2 SBAS, 3 Galileo, 4 BDS, 5 QZSS, 6 IRNSS.
# Tipo (bits 21-25) según la tabla 7-56; la bandera L2C (bit 26) solo cambia el tipo 9 de GPS
# (nota 8 del manual: «L2P (Y)» con el bit 26 a 1 es en realidad L2C). Qué componente de L2C
# rastrea el UM980 en ese caso no lo dice el manual: se escribe 2S, como hace RTKLIB demo5.
UNICORE_SYSTEMS = {0: "G", 1: "R", 2: "S", 3: "E", 4: "C", 5: "J", 6: "I"}
UNICORE_SIGNALS = {
    ("G", 0): "1C", ("G", 3): "1L", ("G", 11): "1S", ("G", 6): "5I", ("G", 14): "5Q", ("G", 17): "2L",
    ("R", 0): "1C", ("R", 5): "2C", ("R", 6): "3I", ("R", 7): "3Q",
    ("S", 0): "1C", ("S", 6): "5I",
    ("E", 1): "1B", ("E", 2): "1C", ("E", 12): "5Q", ("E", 17): "7Q", ("E", 18): "6B", ("E", 22): "6C",
    ("C", 0): "2I", ("C", 4): "2Q", ("C", 8): "1P", ("C", 23): "1D", ("C", 5): "7Q", ("C", 17): "7I",
    ("C", 12): "5P", ("C", 28): "5D", ("C", 6): "6Q", ("C", 21): "6I", ("C", 13): "7D",
    ("J", 0): "1C", ("J", 3): "1L", ("J", 4): "1Z", ("J", 6): "5I", ("J", 11): "1S", ("J", 14): "5Q",
    ("J", 17): "2L", ("J", 21): "6S", ("J", 27): "6E",
    ("I", 6): "5A",
}
# Sin código RINEX 3.04: QZSS L1C/B (tipo 1, código 1E solo desde RINEX 4) y el «L5 pilot» de NavIC
# (tipo 14), que no existe como señal abierta en la tabla 10 de RINEX 3.04.


def unicore_code(system: str, signal_type: int, l2c_flag: int) -> str | None:
    if system == "G" and signal_type == 9:
        return "2S" if l2c_flag else "2W"
    return UNICORE_SIGNALS.get((system, signal_type))


# --- RTCM MSM: identificador de señal (1-32) → código RINEX. Solo los normalizados.
MSM_SIGNALS = {
    "G": {2: "1C", 3: "1P", 4: "1W", 8: "2C", 9: "2P", 10: "2W", 15: "2S", 16: "2L", 17: "2X",
          22: "5I", 23: "5Q", 24: "5X", 30: "1S", 31: "1L", 32: "1X"},
    "R": {2: "1C", 3: "1P", 8: "2C", 9: "2P"},
    "E": {2: "1C", 3: "1A", 4: "1B", 5: "1X", 6: "1Z", 8: "6C", 9: "6A", 10: "6B", 11: "6X", 12: "6Z",
          14: "7I", 15: "7Q", 16: "7X", 18: "8I", 19: "8Q", 20: "8X", 22: "5I", 23: "5Q", 24: "5X"},
    "J": {2: "1C", 9: "6S", 10: "6L", 11: "6X", 15: "2S", 16: "2L", 17: "2X", 22: "5I", 23: "5Q",
          24: "5X", 30: "1S", 31: "1L", 32: "1X"},
    "C": {2: "2I", 3: "2Q", 4: "2X", 8: "6I", 9: "6Q", 10: "6X", 14: "7I", 15: "7Q", 16: "7X",
          22: "5D", 23: "5P", 24: "5X", 25: "7D", 30: "1D", 31: "1P", 32: "1X"},
    "I": {8: "9A", 22: "5A"},
    "S": {2: "1C", 22: "5I", 23: "5Q", 24: "5X"},
}

# --- Señal de referencia de cada banda (RINEX 3.04, tabla A23). En SYS / PHASE SHIFT la referencia
# va sin corrección y las demás con la corrección aplicada.
PHASE_REFERENCE = {
    "G": {"1C", "2P", "5I"},
    "R": {"1C", "2C", "3I", "4A", "6A"},
    "E": {"1B", "5I", "7I", "8I", "6B"},
    "J": {"1C", "2S", "5I", "5D", "6S"},
    "C": {"2I", "1D", "5D", "7I", "7D", "8D", "6I"},
    "I": {"5A", "9A"},
    "S": {"1C", "5I"},
}

_BAND_ORDER = "125346789"
_ATTRIBUTE_ORDER = "CSLXPWYMNDIQABZE"


def code_sort_key(code: str) -> tuple[int, int]:
    band = _BAND_ORDER.find(code[0])
    attribute = _ATTRIBUTE_ORDER.find(code[1])
    return (band if band >= 0 else 99, attribute if attribute >= 0 else 99)


def satellite_id(system: str, prn: int) -> str:
    return f"{system}{prn:02d}"
