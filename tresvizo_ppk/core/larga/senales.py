"""Señales, frecuencias y grupos de dobles diferencias del procesador de líneas largas.

Cada satélite se procesa con una pareja de frecuencias (banda 1 y banda 2) para la libre de ionosfera, la de
Melbourne-Wübbena y la libre de geometría. Por sistema se prueban las parejas en orden y se toma, para cada
satélite, la primera que los **dos** receptores observan en fase y en código con la misma clase de señal:

- GPS: L1/L2 (L1 C/A y L2 semicodificada P(Y) «W», o L2C); si no, L1/L5.
- Galileo: E1/E5a.
- BeiDou: B1I/B3I; si no, B1I/B2I (solo BeiDou-2) o B1C/B2a (solo BeiDou-3).

**Clase de señal.** Dos atributos RINEX de la misma banda que llevan el mismo código de rango (p. ej. L2C «S», «L»
y «X», o E1 «B», «C» y «X») tienen sesgos de satélite iguales a nivel de milímetros; entre clases distintas (C1C
contra C1W, L2C contra L2 P(Y)) el sesgo de código del satélite (DCB, hasta ±1–2 ns) no se cancela en la diferencia
entre receptores. Por eso la pareja tiene que ser de la misma clase en los dos receptores para formar la doble
diferencia del ancho de carril (que usa el código); si en una banda solo coincide la banda pero no la clase, el
satélite entra con su libre de ionosfera flotante y sin resolver ambigüedades (`ar_ok` falso), y el informe lo dice.

**Grupos.** Las dobles diferencias se forman dentro de cada grupo: mismo sistema, misma pareja de frecuencias y,
en BeiDou, misma generación (entre BeiDou-2 y BeiDou-3 los receptores pueden tener sesgos distintos). GLONASS
(FDMA: cada satélite en su frecuencia, con sesgos entre canales que dependen del receptor), QZSS (pocos satélites)
y los geoestacionarios de BeiDou (órbita y multitrayectoria peores) quedan fuera de este procesador.
"""
from __future__ import annotations

from dataclasses import dataclass

C_LIGHT = 299_792_458.0

# Frecuencias por sistema y banda RINEX (Hz)
FREQ = {
    "G": {"1": 1575.42e6, "2": 1227.60e6, "5": 1176.45e6},
    "E": {"1": 1575.42e6, "5": 1176.45e6, "7": 1207.14e6, "8": 1191.795e6, "6": 1278.75e6},
    "C": {"2": 1561.098e6, "6": 1268.52e6, "7": 1207.14e6, "1": 1575.42e6, "5": 1176.45e6},
}

# Clases de señal (atributo RINEX 3 → clase) por sistema y banda. Mismo código de rango ⇒ misma clase.
_CLASSES = {
    "G": {"1": {"C": "CA", "W": "P", "P": "P", "Y": "P", "S": "L1C", "L": "L1C", "X": "L1C"},
          "2": {"W": "P", "P": "P", "Y": "P", "D": "P", "S": "L2C", "L": "L2C", "X": "L2C", "C": "CA"},
          "5": {"I": "L5", "Q": "L5", "X": "L5"}},
    "E": {"1": {"B": "E1", "C": "E1", "X": "E1"}, "5": {"I": "E5a", "Q": "E5a", "X": "E5a"},
          "7": {"I": "E5b", "Q": "E5b", "X": "E5b"}},
    "C": {"2": {"I": "B1I", "Q": "B1I", "X": "B1I"}, "6": {"I": "B3I", "Q": "B3I", "X": "B3I"},
          "7": {"I": "B2I", "Q": "B2I", "X": "B2I"}, "1": {"D": "B1C", "P": "B1C", "X": "B1C"},
          "5": {"D": "B2a", "P": "B2a", "X": "B2a"}},
}

# Atributos por preferencia dentro de cada banda (el primero que haya en el archivo)
_ATTR_PRIORITY = {
    "G": {"1": "CWPYSLX", "2": "WPYDXSLC", "5": "XQI"},
    "E": {"1": "XCB", "5": "XQI", "7": "XQI"},
    "C": {"2": "IXQ", "6": "IXQ", "7": "IXQ", "1": "XPD", "5": "XPD"},
}

# Parejas de bandas por sistema, en orden de preferencia
PAIRS = {"G": [("1", "2"), ("1", "5")], "E": [("1", "5")], "C": [("2", "6"), ("2", "7"), ("1", "5")]}

SYSTEM_NAMES = {"G": "GPS", "E": "Galileo", "C": "BeiDou", "R": "GLONASS", "J": "QZSS", "S": "SBAS", "I": "NavIC"}

# BeiDou: geoestacionarios (fuera) y generación
BDS_GEO = {1, 2, 3, 4, 5, 59, 60, 61, 62, 63}


def bds_generation(prn: int) -> int:
    return 2 if prn <= 18 else 3


def usable_satellite(sat: str) -> bool:
    system, prn = sat[0], int(sat[1:])
    if system not in FREQ:
        return False
    if system == "C" and prn in BDS_GEO:
        return False
    return True


def signal_class(system: str, band: str, attr: str) -> str | None:
    return _CLASSES.get(system, {}).get(band, {}).get(attr)


@dataclass(frozen=True)
class Pair:
    """Pareja de frecuencias de un satélite: los códigos RINEX elegidos en cada receptor y sus constantes."""
    system: str
    band1: str
    band2: str
    f1: float
    f2: float

    @property
    def lam1(self) -> float:
        return C_LIGHT / self.f1

    @property
    def lam2(self) -> float:
        return C_LIGHT / self.f2

    @property
    def lam_wl(self) -> float:
        return C_LIGHT / (self.f1 - self.f2)

    @property
    def lam_nl(self) -> float:
        return C_LIGHT / (self.f1 + self.f2)

    @property
    def alpha(self) -> float:
        """Coeficiente de la banda 1 en la libre de ionosfera."""
        return self.f1 ** 2 / (self.f1 ** 2 - self.f2 ** 2)

    @property
    def beta(self) -> float:
        """Coeficiente de la banda 2 en la libre de ionosfera (se resta)."""
        return self.f2 ** 2 / (self.f1 ** 2 - self.f2 ** 2)

    @property
    def kappa(self) -> float:
        """Metros de libre de ionosfera por ciclo de ancho de carril: B_IF = λ_NL·N1 + κ·N_WL."""
        return C_LIGHT * self.f2 / (self.f1 ** 2 - self.f2 ** 2)

    @property
    def key(self) -> str:
        return f"{self.system}{self.band1}{self.band2}"


def make_pair(system: str, band1: str, band2: str) -> Pair:
    return Pair(system, band1, band2, FREQ[system][band1], FREQ[system][band2])


def group_key(sat: str, pair: Pair) -> str:
    """Grupo de dobles diferencias: «G12», «E15», «C2-26», «C3-26»…"""
    if sat[0] == "C":
        return f"C{bds_generation(int(sat[1:]))}-{pair.band1}{pair.band2}"
    return pair.key


def group_label(key: str) -> str:
    system = key[0]
    name = SYSTEM_NAMES.get(system, system)
    if system == "C":
        gen, bands = key[1], key.split("-")[1]
        return f"BeiDou-{gen} {_band_text('C', bands[0])}/{_band_text('C', bands[1])}"
    return f"{name} {_band_text(system, key[1])}/{_band_text(system, key[2])}"


def _band_text(system: str, band: str) -> str:
    names = {"G": {"1": "L1", "2": "L2", "5": "L5"}, "E": {"1": "E1", "5": "E5a", "7": "E5b"},
             "C": {"2": "B1I", "6": "B3I", "7": "B2I", "1": "B1C", "5": "B2a"}}
    return names.get(system, {}).get(band, band)


def candidates(system: str, band: str, types: list[str]) -> list[tuple[str, str]]:
    """Parejas (código, fase) de una banda presentes en los tipos del archivo, por preferencia."""
    out = []
    for attr in _ATTR_PRIORITY.get(system, {}).get(band, ""):
        code, phase = f"C{band}{attr}", f"L{band}{attr}"
        if code in types and phase in types:
            out.append((code, phase))
    return out
