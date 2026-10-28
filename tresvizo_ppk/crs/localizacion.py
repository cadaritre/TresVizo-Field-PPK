"""Localización de proyecto (calibración local): **la misma de las apps TresVizo Field**.

Lo que Trimble llama *site calibration* y Leica, Carlson y Emlid *localization*: unir lo que se midió (en el marco de
la base) con las coordenadas locales de una obra, una planta, una mina o un catastro. El contrato es el de las apps,
`docs/localizacion.md` de TresVizo Field (iOS y Android lo implementan igual y reproducen `localization_vectors.json`,
que genera su árbitro `tools/localizacion/localization_reference.py`); aquí se sigue **operación por operación**, en
el mismo orden, como lo hace `LocalizationSolver.swift`:

1. **Cuadrícula base.** Lo medido (φ, λ) se proyecta con el sistema elegido; si es geográfico, con una transversa de
   Mercator local (meridiano central en la longitud media de los pares, k0 = 1, latitud de origen 0, sin falso
   origen, elipsoide del datum).
2. **Altura base.** v = h, o v = H = h − N con el geoide del sistema elegido: el plano solo corrige lo que el geoide
   no explica.
3. **Planta:** semejanza de Helmert de cuatro parámetros reducida a los centroides, sin pesos (o rígida, o solo
   traslación). `E = Ē + a·(e − ē) − b·(n − n̄)`, `N = N̄ + b·(e − ē) + a·(n − n̄)`.
4. **Cota:** plano inclinado sobre la altura base, `Z = v + c0 + cN·(n − n0) + cE·(e − e0)` (o solo desplazamiento).

Residuos de **todos** los pares (los que no entran son comprobaciones), el residuo sin el par (validación cruzada),
σ0, precisión de escala y giro, y los avisos con los mismos umbrales y textos de las apps. Los parámetros no se
guardan: se recalculan de los pares y opciones (`model.Localization`), con el geoide de ahora.

También: el registro en el JSON del contrato (§5, el que viaja en el `.mvz` y en el respaldo ZIP), leer las
localizaciones de un proyecto de TresVizo Field (`.mvz` o respaldo `.zip`), leer el control de un CSV (PNEZD,
NXYZD o PENZD) y aplicar la localización a una coordenada (`apply`, que llama `api.transform`).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import uuid
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from ..errors import UserError
from ..model import (Localization, LocalizationControl, LocalizationMeasured, LocalizationPair, OFFICIAL_FRAME,
                     CoordinateChoice, TransformedCoordinate)

# --- Umbrales: los mismos valores y motivos que el árbitro y las dos apps (son contrato) ---------------------------

# Por debajo de esta dispersión cuadrática media alrededor del centroide no hay giro ni escala que calcular: un
# centímetro de error giraría el sistema más de medio grado.
MINIMUM_HORIZONTAL_SPREAD_METERS = 1.0
# Precisión a priori de un par en planta (FIJO RTK más el error del control): solo para prever qué tan bien determina
# la geometría el giro y la escala, nunca para ponderar.
A_PRIORI_HORIZONTAL_SIGMA_METERS = 0.010
# Precisión prevista de la escala peor que esto: giro y escala mal determinados (dos pares a menos de unos 70 m).
WEAK_SCALE_PPM = 200.0
# Entre cuadrícula y terreno la escala no se aparta de 1 más de unas 1000 ppm; pasadas 1500 algo está mal.
UNUSUAL_SCALE_PPM = 1500.0
# GEO2-3 de las apps: con la escala esperada a nivel del terreno, el aviso se mide contra ella (3 cm en 100 m).
UNEXPECTED_SCALE_PPM = 300.0
# Por debajo de esta dispersión según el eje más débil los pares de cota están casi en línea: se usa la constante.
MINIMUM_VERTICAL_SPREAD_METERS = 10.0
# Un geoide rara vez se inclina más que esto: una pendiente mayor suele ser una cota de control mal.
LARGE_SLOPE_PPM = 50.0
# Tolerancias por omisión: el doble de la precisión de un FIJO RTK (1 cm en planta, 2 cm en cota).
DEFAULT_HORIZONTAL_TOLERANCE_METERS = 0.020
DEFAULT_VERTICAL_TOLERANCE_METERS = 0.040
# Con menos pares que estos en un ajuste, el mayor residuo sin el par no señala a uno con fiabilidad (§9.1).
MINIMUM_PAIRS_TO_FLAG_SUSPECT = 4

ARCSEC_PER_RADIAN = 180.0 / math.pi * 3600.0
PPM = 1e6

HORIZONTAL_METHODS = ("similarity", "rigid", "translation")
VERTICAL_METHODS = ("automatic", "constant", "none")
LOCAL_CODE = "LOCAL"                    # el «CS code» de las apps en coordenadas locales


# --- Errores ---------------------------------------------------------------------------------------------------------

class LocalizationError(UserError):
    """Lo que impide calcular (los avisos no lo impiden). `code` es la clave del contrato."""

    def __init__(self, code: str, pair_name: str = "", spread_m: float | None = None, message: str = ""):
        self.code, self.pair_name, self.spread_m = code, pair_name, spread_m
        super().__init__(message or error_message(code, pair_name, spread_m))


def error_message(code: str, pair_name: str = "", spread_m: float | None = None) -> str:
    """`LocalizationText.message(_ problem:)` de las apps."""
    if code == "noBaseGrid":
        return ("No se puede calcular la cuadrícula base: el proyecto está en geográficas y los pares no dan una "
                "longitud media.")
    if code == "noUsablePairs":
        return "No hay ningún par en uso. Marca al menos uno en planta (H) o en cota (V)."
    if code == "horizontalPairWithoutLocalPlanar":
        return f"“{pair_name}” está marcado para planta pero no tiene norte y este locales. Ingrésalos o desmarca H."
    if code == "horizontalPointsCoincident":
        return (f"Los pares de planta están casi en el mismo sitio ({length(spread_m or 0.0, 2)} de dispersión): así "
                "no hay giro ni escala que calcular. Usa pares más separados o “Solo traslación”.")
    return code


NOT_COMPUTABLE_IN_USE = ("La localización en uso no se puede calcular. Las coordenadas locales no se muestran hasta "
                         "corregirla.")


# --- Lo que entra al ajuste y lo que sale ----------------------------------------------------------------------------

@dataclass
class Observation:
    """Un par tal como entra al ajuste: lo medido ya en la cuadrícula base, con su altura base, y el control."""
    name: str
    e: float
    n: float
    v: float | None
    E: float | None
    N: float | None
    Z: float | None
    use_h: bool = True
    use_v: bool = True


@dataclass(frozen=True)
class Options:
    horizontal_method: str = "similarity"
    vertical_method: str = "automatic"
    horizontal_tolerance_m: float = DEFAULT_HORIZONTAL_TOLERANCE_METERS
    vertical_tolerance_m: float = DEFAULT_VERTICAL_TOLERANCE_METERS


@dataclass(frozen=True)
class Warning:
    """Un aviso. **Ninguno impide aplicar**: decide el topógrafo."""
    code: str
    value_ppm: float | None = None
    value_m: float | None = None
    expected_ppm: float | None = None
    pair_index: int | None = None
    pair_name: str = ""
    is_check: bool = False


@dataclass(frozen=True)
class Transform:
    a: float = 1.0
    b: float = 0.0
    ec: float = 0.0                 # centroide de la cuadrícula base (este, norte)
    nc: float = 0.0
    Ec: float = 0.0                 # centroide local
    Nc: float = 0.0
    vertical_method: str = "none"
    e0: float = 0.0                 # origen del plano
    n0: float = 0.0
    c0: float = 0.0
    cN: float = 0.0
    cE: float = 0.0

    def forward(self, e: float, n: float) -> tuple[float, float]:
        de, dn = e - self.ec, n - self.nc
        return self.Ec + self.a * de - self.b * dn, self.Nc + self.b * de + self.a * dn

    def inverse(self, E: float, N: float) -> tuple[float, float]:
        dE, dN = E - self.Ec, N - self.Nc
        s2 = self.a * self.a + self.b * self.b
        return self.ec + (self.a * dE + self.b * dN) / s2, self.nc + (-self.b * dE + self.a * dN) / s2

    def vertical_correction(self, e: float, n: float) -> float:
        if self.vertical_method == "none":
            return 0.0
        return self.c0 + self.cN * (n - self.n0) + self.cE * (e - self.e0)

    def elevation(self, v: float | None, e: float, n: float) -> float | None:
        """La cota local de una altura base. Sin altura, sin cota: None, nunca cero."""
        return None if v is None else v + self.vertical_correction(e, n)

    def base_height(self, Z: float | None, e: float, n: float) -> float | None:
        return None if Z is None else Z - self.vertical_correction(e, n)


@dataclass
class Residual:
    """Control menos calculado. None sin ajuste o sin el dato."""
    index: int
    name: str
    dE: float | None = None
    dN: float | None = None
    dH: float | None = None
    dZ: float | None = None
    used_h: bool = False
    used_v: bool = False
    loo_dH: float | None = None     # residuo si el ajuste se hiciera sin este par (mismo método efectivo)
    loo_dZ: float | None = None


@dataclass
class Horizontal:
    requested_method: str
    method: str                      # «similarity», «rigid», «translation» o «none»
    a: float
    b: float
    ec: float
    nc: float
    Ec: float
    Nc: float
    pair_count: int
    redundancy: int
    sigma0_m: float | None
    scale_sigma_ppm: float | None
    rotation_sigma_arcsec: float | None

    @property
    def scale(self) -> float:
        return math.hypot(self.a, self.b)

    @property
    def scale_ppm(self) -> float:
        return (self.scale - 1.0) * PPM

    @property
    def rotation_rad(self) -> float:
        return math.atan2(self.b, self.a)

    @property
    def azimuth_rotation_arcsec(self) -> float:
        """El giro como se lee en un acimut: acimut local = acimut de cuadrícula + giro (es −θ)."""
        return -self.rotation_rad * ARCSEC_PER_RADIAN

    @property
    def translation_e(self) -> float:
        return self.Ec - self.a * self.ec + self.b * self.nc

    @property
    def translation_n(self) -> float:
        return self.Nc - self.b * self.ec - self.a * self.nc


@dataclass
class Vertical:
    requested_method: str
    method: str                      # «plane», «constant» o «none»
    e0: float
    n0: float
    c0: float
    cN: float
    cE: float
    pair_count: int
    redundancy: int
    sigma0_m: float | None

    @property
    def slope_north_ppm(self) -> float:
        return self.cN * PPM

    @property
    def slope_east_ppm(self) -> float:
        return self.cE * PPM


@dataclass
class Solution:
    horizontal: Horizontal
    vertical: Vertical
    residuals: list[Residual]
    warnings: list[Warning]
    transform: Transform

    @property
    def horizontal_suspect_index(self) -> int | None:
        cands = [r for r in self.residuals if r.loo_dH is not None]
        return max(cands, key=lambda r: r.loo_dH).index if cands else None

    @property
    def vertical_suspect_index(self) -> int | None:
        cands = [r for r in self.residuals if r.loo_dZ is not None]
        return max(cands, key=lambda r: abs(r.loo_dZ)).index if cands else None

    def flags_horizontal_suspect(self, index: int, tolerance_m: float) -> bool:
        """El par **se señala** en planta: el de mayor residuo sin él, con 4 pares o más, y fuera de tolerancia."""
        if self.horizontal_suspect_index != index or self.horizontal.pair_count < MINIMUM_PAIRS_TO_FLAG_SUSPECT:
            return False
        loo = next((r.loo_dH for r in self.residuals if r.index == index), None)
        return loo is not None and loo > tolerance_m

    def flags_vertical_suspect(self, index: int, tolerance_m: float) -> bool:
        if self.vertical_suspect_index != index or self.vertical.pair_count < MINIMUM_PAIRS_TO_FLAG_SUSPECT:
            return False
        loo = next((r.loo_dZ for r in self.residuals if r.index == index), None)
        return loo is not None and abs(loo) > tolerance_m


# --- El ajuste (LocalizationSolver.solve de las apps) ----------------------------------------------------------------

def solve(pairs: list[Observation], options: Options = Options(), leave_one_out: bool = True) -> Solution:
    """La solución, o `LocalizationError` si no se puede calcular."""
    for p in pairs:
        if p.use_h and (p.E is None or p.N is None):
            raise LocalizationError("horizontalPairWithoutLocalPlanar", pair_name=p.name)
    h_idx = [i for i, p in enumerate(pairs) if p.use_h]
    v_idx = [i for i, p in enumerate(pairs) if p.use_v and p.Z is not None and p.v is not None]
    v_set = set(v_idx)
    if not h_idx and not v_idx:
        raise LocalizationError("noUsablePairs")
    warnings: list[Warning] = []

    # ----- Planta
    nh = len(h_idx)
    ec = nc = Ec = Nc = 0.0
    a, b = 1.0, 0.0
    redundancy_h = 0
    D = 0.0
    if nh > 0:
        m = float(nh)
        ec = sum(pairs[i].e for i in h_idx) / m
        nc = sum(pairs[i].n for i in h_idx) / m
        Ec = sum(pairs[i].E for i in h_idx) / m
        Nc = sum(pairs[i].N for i in h_idx) / m
        for i in h_idx:
            de, dn = pairs[i].e - ec, pairs[i].n - nc
            D += de * de + dn * dn
        if nh == 1 or options.horizontal_method == "translation":
            # Con un solo par cualquier método queda en traslación, como en Trimble.
            method = "translation"
            redundancy_h = 2 * nh - 2
        else:
            spread = math.sqrt(D / m)
            if spread < MINIMUM_HORIZONTAL_SPREAD_METERS:
                raise LocalizationError("horizontalPointsCoincident", spread_m=spread)
            # Y la del control (H11 de las apps): el mismo punto de control en dos pares da escala cero.
            d_control = 0.0
            for i in h_idx:
                dE, dN = pairs[i].E - Ec, pairs[i].N - Nc
                d_control += dE * dE + dN * dN
            control_spread = math.sqrt(d_control / m)
            if not control_spread >= MINIMUM_HORIZONTAL_SPREAD_METERS:
                raise LocalizationError("horizontalPointsCoincident", spread_m=control_spread)
            P = Q = 0.0
            for i in h_idx:
                de, dn = pairs[i].e - ec, pairs[i].n - nc
                dE, dN = pairs[i].E - Ec, pairs[i].N - Nc
                P += de * dE + dn * dN
                Q += de * dN - dn * dE
            if options.horizontal_method == "rigid":
                method = "rigid"
                theta = math.atan2(Q, P)
                a, b = math.cos(theta), math.sin(theta)
                redundancy_h = 2 * nh - 3
            else:
                method = "similarity"
                a, b = P / D, Q / D
                redundancy_h = 2 * nh - 4
            predicted = A_PRIORI_HORIZONTAL_SIGMA_METERS / math.sqrt(D) * PPM
            if predicted > WEAK_SCALE_PPM:
                warnings.append(Warning("horizontalGeometryWeak", value_ppm=predicted))
    else:
        method = "none"
    s = math.hypot(a, b)

    def fwd_h(e: float, n: float) -> tuple[float, float]:
        de, dn = e - ec, n - nc
        return Ec + a * de - b * dn, Nc + b * de + a * dn

    sum_sq_h = 0.0
    for i in h_idx:
        cE_, cN_ = fwd_h(pairs[i].e, pairs[i].n)
        dE, dN = pairs[i].E - cE_, pairs[i].N - cN_
        sum_sq_h += dE * dE + dN * dN
    sigma0_h = math.sqrt(sum_sq_h / redundancy_h) if redundancy_h > 0 else None
    scale_sigma = rotation_sigma = None
    if sigma0_h is not None and method in ("similarity", "rigid"):
        if method == "similarity":
            scale_sigma = sigma0_h / math.sqrt(D) * PPM
        rotation_sigma = sigma0_h / (s * math.sqrt(D)) * ARCSEC_PER_RADIAN
    if method != "none" and redundancy_h == 0:
        warnings.append(Warning("noHorizontalRedundancy"))
    scale_ppm = (s - 1.0) * PPM
    if method == "similarity" and abs(scale_ppm) > UNUSUAL_SCALE_PPM:
        warnings.append(Warning("scaleUnusual", value_ppm=scale_ppm))

    # ----- Cota
    nv = len(v_idx)
    e0 = n0 = c0 = cN = cE = 0.0
    redundancy_v = 0
    if options.vertical_method == "none" or nv == 0:
        vmethod = "none"
    else:
        k = float(nv)
        e0 = sum(pairs[i].e for i in v_idx) / k
        n0 = sum(pairs[i].n for i in v_idx) / k
        d = [pairs[i].Z - pairs[i].v for i in v_idx]
        d_mean = sum(d) / k
        use_plane = options.vertical_method == "automatic" and nv >= 3
        See = Snn = Sen = 0.0
        if use_plane:
            for i in v_idx:
                de, dn = pairs[i].e - e0, pairs[i].n - n0
                See += de * de
                Snn += dn * dn
                Sen += de * dn
            # Autovalor menor de la covarianza por punto de (e, n): la dispersión según el eje más débil.
            mee, mnn, men = See / k, Snn / k, Sen / k
            half_trace = (mee + mnn) / 2
            half = (mee - mnn) / 2
            lam_min = half_trace - math.sqrt(half * half + men * men)
            spread = math.sqrt(max(lam_min, 0.0))
            if spread < MINIMUM_VERTICAL_SPREAD_METERS:
                use_plane = False
                warnings.append(Warning("verticalCollinearFallback", value_m=spread))
        if use_plane:
            Sed = Snd = 0.0
            for j, i in enumerate(v_idx):
                de, dn = pairs[i].e - e0, pairs[i].n - n0
                Sed += de * (d[j] - d_mean)
                Snd += dn * (d[j] - d_mean)
            det = Snn * See - Sen * Sen
            cN = (See * Snd - Sen * Sed) / det
            cE = (Snn * Sed - Sen * Snd) / det
            c0 = d_mean
            vmethod = "plane"
            redundancy_v = nv - 3
            slope_ppm = math.hypot(cN, cE) * PPM
            if slope_ppm > LARGE_SLOPE_PPM:
                warnings.append(Warning("slopeLarge", value_ppm=slope_ppm))
        else:
            c0 = d_mean
            vmethod = "constant"
            redundancy_v = nv - 1
        if redundancy_v == 0:
            warnings.append(Warning("noVerticalRedundancy"))

    def plane(e: float, n: float) -> float:
        return 0.0 if vmethod == "none" else c0 + cN * (n - n0) + cE * (e - e0)

    sum_sq_v = 0.0
    if vmethod != "none":
        for i in v_idx:
            dZ = pairs[i].Z - (pairs[i].v + plane(pairs[i].e, pairs[i].n))
            sum_sq_v += dZ * dZ
    sigma0_v = math.sqrt(sum_sq_v / redundancy_v) if vmethod != "none" and redundancy_v > 0 else None

    # ----- Residuos de todos los pares: los que no entran son comprobaciones
    residuals: list[Residual] = []
    for i, p in enumerate(pairs):
        r = Residual(i, p.name, used_h=p.use_h, used_v=(i in v_set and vmethod != "none"))
        if method != "none" and p.E is not None and p.N is not None:
            cE_, cN_ = fwd_h(p.e, p.n)
            r.dE, r.dN = p.E - cE_, p.N - cN_
            r.dH = math.hypot(p.E - cE_, p.N - cN_)
        if p.Z is not None and p.v is not None and vmethod != "none":
            r.dZ = p.Z - (p.v + plane(p.e, p.n))
        residuals.append(r)
        if r.dH is not None and r.dH > options.horizontal_tolerance_m:
            warnings.append(Warning("horizontalResidualAboveTolerance", value_m=r.dH, pair_index=i, pair_name=p.name,
                                    is_check=not r.used_h))
        if r.dZ is not None and abs(r.dZ) > options.vertical_tolerance_m:
            warnings.append(Warning("verticalResidualAboveTolerance", value_m=r.dZ, pair_index=i, pair_name=p.name,
                                    is_check=not r.used_v))

    transform = Transform(a, b, ec, nc, Ec, Nc, vmethod, e0, n0, c0, cN, cE)

    # ----- Residuo sin el par (validación cruzada)
    if leave_one_out:
        for i, p in enumerate(pairs):
            if not (p.use_h or i in v_set):
                continue
            rest = pairs[:i] + pairs[i + 1:]
            try:
                without = solve(rest, options, leave_one_out=False)
            except LocalizationError:
                continue
            # Solo con el mismo método efectivo: con dos pares, quitar uno deja una traslación, y «el otro par
            # predice este» mediría el giro del sistema local, no un error.
            other = without.transform
            if p.use_h and without.horizontal.method == method and p.E is not None and p.N is not None:
                E2, N2 = other.forward(p.e, p.n)
                residuals[i].loo_dH = math.hypot(p.E - E2, p.N - N2)
            if i in v_set and vmethod != "none" and without.vertical.method == vmethod and p.Z is not None:
                Z2 = other.elevation(p.v, p.e, p.n)
                if Z2 is not None:
                    residuals[i].loo_dZ = p.Z - Z2

    return Solution(
        horizontal=Horizontal(options.horizontal_method, method, a, b, ec, nc, Ec, Nc, nh, redundancy_h, sigma0_h,
                              scale_sigma, rotation_sigma),
        vertical=Vertical(options.vertical_method, vmethod, e0, n0, c0, cN, cE, 0 if vmethod == "none" else nv,
                          redundancy_v, sigma0_v),
        residuals=residuals, warnings=warnings, transform=transform)


# --- Transversa de Mercator local (Krüger hasta n⁶, la del árbitro y las apps) ---------------------------------------

ELLIPSOIDS = {"WGS 84": (6378137.0, 298.257223563), "GRS 1980": (6378137.0, 298.257222101)}


def ellipsoid_named(name: str) -> tuple[str, float, float]:
    """(nombre, a, 1/f). Uno que no se conoce se lee como WGS 84, como en las apps."""
    key = (name or "").strip()
    if key in ELLIPSOIDS:
        return (key, *ELLIPSOIDS[key])
    for k, v in ELLIPSOIDS.items():
        if k.replace(" ", "").upper() == key.replace(" ", "").upper():
            return (k, *v)
    return ("WGS 84", *ELLIPSOIDS["WGS 84"])


@dataclass(frozen=True)
class TransverseMercator:
    lon0: float
    a: float = ELLIPSOIDS["WGS 84"][0]
    rf: float = ELLIPSOIDS["WGS 84"][1]
    k0: float = 1.0
    fe: float = 0.0
    fn: float = 0.0

    def _setup(self):
        f = 1.0 / self.rf
        n = f / (2 - f)
        A = self.a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64 + n ** 6 / 256)
        alpha = [
            n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180 - 127 * n ** 5 / 288 + 7891 * n ** 6 / 37800,
            13 * n ** 2 / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440 + 281 * n ** 5 / 630 - 1983433 * n ** 6 / 1935360,
            61 * n ** 3 / 240 - 103 * n ** 4 / 140 + 15061 * n ** 5 / 26880 + 167603 * n ** 6 / 181440,
            49561 * n ** 4 / 161280 - 179 * n ** 5 / 168 + 6601661 * n ** 6 / 7257600,
            34729 * n ** 5 / 80640 - 3418889 * n ** 6 / 1995840,
            212378941 * n ** 6 / 319334400,
        ]
        beta = [
            n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96 - n ** 4 / 360 - 81 * n ** 5 / 512 + 96199 * n ** 6 / 604800,
            n ** 2 / 48 + n ** 3 / 15 - 437 * n ** 4 / 1440 + 46 * n ** 5 / 105 - 1118711 * n ** 6 / 3870720,
            17 * n ** 3 / 480 - 37 * n ** 4 / 840 - 209 * n ** 5 / 4480 + 5569 * n ** 6 / 90720,
            4397 * n ** 4 / 161280 - 11 * n ** 5 / 504 - 830251 * n ** 6 / 7257600,
            4583 * n ** 5 / 161280 - 108847 * n ** 6 / 3991680,
            20648693 * n ** 6 / 638668800,
        ]
        e = math.sqrt(f * (2 - f))
        return n, A, alpha, beta, e

    def forward(self, lat: float, lon: float) -> tuple[float, float, float]:
        """(este, norte, factor de escala k)."""
        n, A, alpha, _, e = self._setup()
        phi = math.radians(lat)
        lam = math.radians(lon - self.lon0)
        t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
        xi_p = math.atan2(t, math.cos(lam))
        eta_p = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
        xi = xi_p + sum(alpha[j] * math.sin(2 * (j + 1) * xi_p) * math.cosh(2 * (j + 1) * eta_p) for j in range(6))
        eta = eta_p + sum(alpha[j] * math.cos(2 * (j + 1) * xi_p) * math.sinh(2 * (j + 1) * eta_p) for j in range(6))
        p = 1 + sum(2 * (j + 1) * alpha[j] * math.cos(2 * (j + 1) * xi_p) * math.cosh(2 * (j + 1) * eta_p)
                    for j in range(6))
        q = sum(2 * (j + 1) * alpha[j] * math.sin(2 * (j + 1) * xi_p) * math.sinh(2 * (j + 1) * eta_p)
                for j in range(6))
        k = (self.k0 * A / self.a * math.sqrt(1 + ((1 - n) / (1 + n) * math.tan(phi)) ** 2)
             * math.sqrt((p * p + q * q) / (t * t + math.cos(lam) ** 2)))
        return self.fe + self.k0 * A * eta, self.fn + self.k0 * A * xi, k

    def inverse(self, east: float, north: float) -> tuple[float, float]:
        """(latitud, longitud)."""
        _, A, _, beta, e = self._setup()
        xi = (north - self.fn) / (self.k0 * A)
        eta = (east - self.fe) / (self.k0 * A)
        xi_p = xi - sum(beta[j] * math.sin(2 * (j + 1) * xi) * math.cosh(2 * (j + 1) * eta) for j in range(6))
        eta_p = eta - sum(beta[j] * math.cos(2 * (j + 1) * xi) * math.sinh(2 * (j + 1) * eta) for j in range(6))
        tau_p = math.sin(xi_p) / math.sqrt(math.sinh(eta_p) ** 2 + math.cos(xi_p) ** 2)
        lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
        e2 = e * e
        tau = tau_p
        for _ in range(8):
            sig = math.sinh(e * math.atanh(e * tau / math.sqrt(1 + tau * tau)))
            tau_i = tau * math.sqrt(1 + sig * sig) - sig * math.sqrt(1 + tau * tau)
            d = ((tau_p - tau_i) / math.sqrt(1 + tau_i * tau_i)
                 * (1 + (1 - e2) * tau * tau) / ((1 - e2) * math.sqrt(1 + tau * tau)))
            tau += d
            if abs(d) < 1e-14:
                break
        return math.degrees(math.atan(tau)), normalized_longitude(self.lon0 + math.degrees(lam))


def normalized_longitude(lon: float) -> float:
    out = math.fmod(lon + 180.0, 360.0)
    if out < 0:
        out += 360.0
    return out - 180.0


def central_meridian(longitudes: list[float]) -> float | None:
    """La longitud media **por el lado corto del círculo** (H78 de las apps): cada una a menos de 180° de la primera."""
    if not longitudes:
        return None
    ref = longitudes[0]
    unwrapped = []
    for lon in longitudes:
        diff = lon - ref
        unwrapped.append(lon - 360 * _round_half_away(diff / 360) if abs(diff) > 180 else lon)
    return normalized_longitude(sum(unwrapped) / len(unwrapped))


def _round_half_away(x: float) -> float:
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def gaussian_mean_radius(lat: float, a: float, rf: float) -> float:
    """√(M·N): el radio medio de Gauss en una latitud."""
    phi = math.radians(lat)
    f = 1.0 / rf
    e2 = f * (2 - f)
    w = 1 - e2 * math.sin(phi) ** 2
    M = a * (1 - e2) / w ** 1.5
    N = a / math.sqrt(w)
    return math.sqrt(M * N)


# --- La cuadrícula base y la altura base (LocalizationBaseFrame) ----------------------------------------------------

def _api():
    from . import api
    return api


def _frame_family(frame: str) -> str:
    f = (frame or "").upper()
    if "NAD83" in f or "NAD 83" in f:
        return "NAD83"
    if "ITRF" in f or "WGS" in f or "SIRGAS" in f:
        return "ITRF/WGS84"
    return f


@dataclass
class BaseFrame:
    """De lo medido (latitud, longitud y altura elipsoidal en el marco de la base) a lo que entra al ajuste."""
    kind: str                                   # «projectCRS» o «localTransverseMercator»
    crs_code: str = ""
    definition: object | None = None            # `definicion.Definition` de la cuadrícula base
    tm: TransverseMercator | None = None
    ellipsoid_name: str = ""
    geoid_id: str | None = None                 # el geoide del sistema elegido **ahora** (manda sobre `height_base`)
    frame: str = OFFICIAL_FRAME                 # marco de lo medido, para comprobar el del geoide

    @property
    def name(self) -> str:
        """El nombre corto de la cuadrícula base para el rótulo («UTM zona 13N»; sin «WGS 84 / » delante)."""
        if self.kind == "localTransverseMercator":
            return "transversa local"
        name = getattr(self.definition, "name", self.crs_code)
        return name[len("WGS 84 / "):] if name.startswith("WGS 84 / ") else name

    def grid(self, lat: float, lon: float) -> tuple[float, float]:
        if self.tm is not None:
            e, n, _ = self.tm.forward(lat, lon)
            return e, n
        api = _api()
        try:
            dlat, dlon, _, _ = api.motor.datum_point(self.definition, lat, lon)
            e, n, _, _ = api.motor.project(self.definition, dlat, dlon)
        except api.motor.GridUnavailable as error:
            raise UserError(f"La transformación {error.transformation} de {self.definition.name} usa la rejilla "
                            f"{error.grid}, que no está en este equipo: descárgala en Sistemas y geoides.") from None
        except api.motor.OutsideGrid as error:
            raise UserError(f"El punto está fuera de la rejilla {error.grid} de {self.definition.name}.") from None
        except ValueError:
            raise UserError(f"El punto queda fuera del dominio de la proyección de {self.definition.name}.") from None
        return e, n

    def geographic(self, e: float, n: float) -> tuple[float, float]:
        """De la cuadrícula base a lo medido (marco de la base)."""
        if self.tm is not None:
            return self.tm.inverse(e, n)
        api = _api()
        dlat, dlon = api.motor.unproject(self.definition, e, n)
        lat, lon, _, _ = api.motor.receiver_point(self.definition, dlat, dlon)
        return lat, lon

    def scale_factor(self, lat: float, lon: float) -> float | None:
        if self.tm is not None:
            return self.tm.forward(lat, lon)[2]
        api = _api()
        try:
            dlat, dlon, _, _ = api.motor.datum_point(self.definition, lat, lon)
            return api.motor.project(self.definition, dlat, dlon)[3]
        except Exception:  # noqa: BLE001 — sin factor de escala no hay escala esperada; no es un error
            return None

    def _ellipsoid(self) -> tuple[float, float]:
        if self.tm is not None:
            return self.tm.a, self.tm.rf
        return self.definition.ellipsoid.a, self.definition.ellipsoid.rf

    def undulation(self, lat: float, lon: float) -> float | None:
        """La N del geoide del sistema en el punto, con las mismas comprobaciones que `api.transform`; None si no se
        puede aplicar (sin el archivo, sin ficha oficial, otro marco o fuera de cobertura)."""
        if not self.geoid_id:
            return None
        grid = _api().geoid(self.geoid_id)
        if grid is None:
            return None
        m = grid.metadata
        if m.get("verification", "verified") != "verified":
            return None
        expected = m.get("expectedFrame")
        if expected and _frame_family(expected) != _frame_family(self.frame):
            return None
        return grid.undulation(lat, lon)

    def base_height(self, h: float | None, lat: float, lon: float) -> float | None:
        """La altura base: la elipsoidal, o `H = h − N` con geoide. Sin geoide en ese sitio cuando el sistema lo usa,
        None: **nunca se cae a la elipsoidal**."""
        if h is None:
            return None
        if not self.geoid_id:
            return h
        N = self.undulation(lat, lon)
        return None if N is None else h - N

    def expected_ground_scale(self, ec: float, nc: float, mean_h: float) -> float | None:
        """La escala de un sistema local **a nivel del terreno**: `1 / (k · R/(R + h̄))` (§3 de las apps)."""
        try:
            lat, lon = self.geographic(ec, nc)
        except Exception:  # noqa: BLE001
            return None
        k = self.scale_factor(lat, lon)
        if k is None or not math.isfinite(k):
            return None
        a, rf = self._ellipsoid()
        R = gaussian_mean_radius(lat, a, rf)
        return 1 / (k * R / (R + mean_h))

    def observations(self, pairs: list[LocalizationPair]) -> list[Observation]:
        out = []
        for p in pairs:
            m = p.measured
            e, n = self.grid(m.latitude_deg, m.longitude_deg)
            out.append(Observation(p.name, e, n, self.base_height(m.ellipsoidal_height_m, m.latitude_deg,
                                                                  m.longitude_deg),
                                   p.control.easting_m, p.control.northing_m, p.control.elevation_m,
                                   p.uses_horizontal, p.uses_vertical))
        return out


def frame_for(record: Localization, geoid_id: str | None, frame: str = OFFICIAL_FRAME) -> BaseFrame:
    """La cuadrícula base **tal como se guardó** (§9.3 de las apps) y las alturas del sistema de ahora."""
    if record.base_grid_kind == "localTransverseMercator":
        lon0 = record.base_grid_central_meridian_deg
        if lon0 is None or not math.isfinite(lon0):
            raise LocalizationError("noBaseGrid")
        name, a, rf = ellipsoid_named(record.base_grid_ellipsoid)
        return BaseFrame("localTransverseMercator", tm=TransverseMercator(lon0, a, rf), ellipsoid_name=name,
                         geoid_id=geoid_id or None, frame=frame)
    if record.base_grid_kind != "projectCRS":
        raise LocalizationError("noBaseGrid")
    d = _api().definition(canonical_code(record.base_grid_crs_code))
    if d is None or not d.is_projected:
        raise LocalizationError("noBaseGrid", message=(
            f"La cuadrícula base de la localización ({record.base_grid_crs_code or 'sin código'}) no está en este "
            "equipo o no es un sistema proyectado: descarga su paquete en Sistemas y geoides. Sin ella no hay "
            "coordenadas locales (nunca se usa otra cuadrícula en su lugar)."))
    return BaseFrame("projectCRS", crs_code=d.code, definition=d, ellipsoid_name=d.ellipsoid.name,
                     geoid_id=geoid_id or None, frame=frame)


def canonical_code(code: str) -> str:
    text = (code or "").strip()
    m = re.fullmatch(r"(?i)epsg\s*:\s*(\d+)", text)
    if m:
        return f"EPSG:{int(m.group(1))}"
    if text.isdigit():
        return f"EPSG:{int(text)}"
    return text


def base_grid_for(choice: CoordinateChoice, pairs: list[LocalizationPair]) -> dict:
    """La cuadrícula base de un borrador nuevo: la del sistema elegido (con la zona por la posición de los pares si es
    una familia de zona automática) o, si es geográfico, la transversa local con la longitud media de los pares."""
    api = _api()
    lats = [p.measured.latitude_deg for p in pairs]
    lons = [p.measured.longitude_deg for p in pairs]
    lon0 = central_meridian(lons)
    lat0 = sum(lats) / len(lats) if lats else None
    d, _ = api.resolve(choice, lat0, lon0)
    if d.is_projected:
        return {"base_grid_kind": "projectCRS", "base_grid_crs_code": d.code, "base_grid_central_meridian_deg": None,
                "base_grid_ellipsoid": ""}
    if lon0 is None:
        raise LocalizationError("noBaseGrid")
    return {"base_grid_kind": "localTransverseMercator", "base_grid_crs_code": "",
            "base_grid_central_meridian_deg": lon0, "base_grid_ellipsoid": ellipsoid_named(d.ellipsoid.name)[0]}


# --- Una localización calculada (ComputedLocalization) --------------------------------------------------------------

@dataclass
class Computed:
    record: Localization
    frame: BaseFrame
    observations: list[Observation]
    solution: Solution
    expected_ground_scale: float | None

    @property
    def options(self) -> Options:
        return options_of(self.record)

    @property
    def scale_match(self) -> str | None:
        """«grid» o «ground»: con qué coordenadas coincide la escala de una semejanza (GEO3-3 de las apps)."""
        if self.expected_ground_scale is None or self.solution.horizontal.method != "similarity":
            return None
        ppm = self.solution.horizontal.scale_ppm
        grid = abs(ppm) <= UNEXPECTED_SCALE_PPM
        ground = abs(ppm - (self.expected_ground_scale - 1) * PPM) <= UNEXPECTED_SCALE_PPM
        if grid and not ground:
            return "grid"
        if ground and not grid:
            return "ground"
        return None

    @property
    def warnings(self) -> list[Warning]:
        """Los avisos que se enseñan: los del ajuste, con la escala juzgada contra la esperada a nivel del terreno
        **y** contra la de la cuadrícula cuando se conoce (GEO2-3, GEO3-3); sin ella, los del ajuste tal cual."""
        if self.expected_ground_scale is None:
            return list(self.solution.warnings)
        h = self.solution.horizontal
        expected_ppm = (self.expected_ground_scale - 1) * PPM
        out = [w for w in self.solution.warnings if w.code != "scaleUnusual"]
        new = None
        if h.method == "similarity":
            if abs(h.scale_ppm - expected_ppm) > UNEXPECTED_SCALE_PPM and abs(h.scale_ppm) > UNEXPECTED_SCALE_PPM:
                new = Warning("scaleUnexpected", value_ppm=h.scale_ppm, expected_ppm=expected_ppm)
        elif h.method in ("rigid", "translation"):
            if abs(expected_ppm) > UNEXPECTED_SCALE_PPM:
                new = Warning("scaleFixedAwayFromGround", expected_ppm=expected_ppm)
        if new is not None:
            ahead = 0
            for w in out:
                if w.code in ("horizontalGeometryWeak", "noHorizontalRedundancy"):
                    ahead += 1
                else:
                    break
            out.insert(ahead, new)
        return out


def options_of(record: Localization) -> Options:
    return Options(record.horizontal_method, record.vertical_method, record.horizontal_tolerance_m,
                   record.vertical_tolerance_m)


def compute_with_frame(record: Localization, frame: BaseFrame) -> Computed:
    if record.horizontal_method not in HORIZONTAL_METHODS:
        raise UserError(f"Método de planta desconocido en la localización: «{record.horizontal_method}».")
    if record.vertical_method not in VERTICAL_METHODS:
        raise UserError(f"Método de cota desconocido en la localización: «{record.vertical_method}».")
    observations = frame.observations(record.pairs)
    for o in observations:
        if not (math.isfinite(o.e) and math.isfinite(o.n)):
            raise LocalizationError("noBaseGrid", message=f"“{o.name}” no se puede pasar a la cuadrícula base.")
    solution = solve(observations, options_of(record))
    expected = None
    if solution.horizontal.method != "none":
        heights = [p.measured.ellipsoidal_height_m for p in record.pairs
                   if p.uses_horizontal and p.measured.ellipsoidal_height_m is not None]
        if heights:
            expected = frame.expected_ground_scale(solution.horizontal.ec, solution.horizontal.nc,
                                                   sum(heights) / len(heights))
    return Computed(record, frame, observations, solution, expected)


def compute(record: Localization, geoid_id: str | None, frame: str = OFFICIAL_FRAME) -> Computed:
    """Calcula una localización de sus pares y opciones, que es **lo único que se guarda**, con la cuadrícula base que
    se guardó y el geoide del sistema de ahora. Lanza `LocalizationError` (un `UserError`) si no se puede."""
    return _compute_cached(_cache_key(record), geoid_id or "", frame)


def _cache_key(record: Localization) -> str:
    return json.dumps(record_to_json(record), sort_keys=True, ensure_ascii=False)


@lru_cache(maxsize=64)
def _compute_cached(key: str, geoid_id: str, frame: str) -> Computed:
    record = record_from_json(json.loads(key))
    return compute_with_frame(record, frame_for(record, geoid_id or None, frame))


def clear_cache() -> None:
    """Tras instalar o borrar sistemas y geoides (cambia lo que se puede calcular)."""
    _compute_cached.cache_clear()


def draft(pairs: list[LocalizationPair], choice: CoordinateChoice, *, horizontal_method: str = "similarity",
          vertical_method: str = "automatic", horizontal_tolerance_m: float = DEFAULT_HORIZONTAL_TOLERANCE_METERS,
          vertical_tolerance_m: float = DEFAULT_VERTICAL_TOLERANCE_METERS, keep: Localization | None = None,
          origin: str = "calculada en la oficina") -> Localization:
    """Un borrador de localización con la cuadrícula del sistema elegido. Con `keep` (la que se edita), conserva su
    cuadrícula base mientras sus medidos sean los mismos (§9.3: cambiar solo las opciones no mueve la cuadrícula)."""
    def same_measured(a: list[LocalizationPair], b: list[LocalizationPair]) -> bool:
        key = lambda ps: sorted((p.measured.latitude_deg, p.measured.longitude_deg) for p in ps)
        return key(a) == key(b)

    if keep is not None and same_measured(keep.pairs, pairs):
        grid = {"base_grid_kind": keep.base_grid_kind, "base_grid_crs_code": keep.base_grid_crs_code,
                "base_grid_central_meridian_deg": keep.base_grid_central_meridian_deg,
                "base_grid_ellipsoid": keep.base_grid_ellipsoid}
    else:
        grid = base_grid_for(choice, pairs)
    return Localization(
        pairs=[replace(p, id=p.id or new_uuid()) for p in pairs], horizontal_method=horizontal_method,
        vertical_method=vertical_method, horizontal_tolerance_m=horizontal_tolerance_m,
        vertical_tolerance_m=vertical_tolerance_m,
        height_base_kind="orthometric" if choice.geoid_id else "ellipsoidal",
        height_base_geoid_id=choice.geoid_id or "", id=keep.id if keep is not None else "",
        project_id=(keep.project_id if keep is not None and keep.project_id else ""), origin=origin, **grid)


def applied(record: Localization, at: datetime | None = None) -> Localization:
    """La localización lista para usar: identificadores y la hora de aplicarla, al segundo (como las apps)."""
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    return replace(record, id=new_uuid(), project_id=record.project_id or new_uuid(), applied_at=moment,
                   removed_at=None)


def removed(record: Localization, at: datetime | None = None) -> Localization:
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    return replace(record, removed_at=moment)


def new_uuid() -> str:
    return str(uuid.uuid4()).upper()


# --- Aplicarla a una coordenada (lo que llama `api.transform`) -------------------------------------------------------

def system_label(base_name: str, saved_system_name: str = "") -> str:
    """«Local · sobre UTM zona 13N» o, desde un sistema de «Mis sistemas», «Obra Norte · local sobre UTM zona 13N»."""
    name = (saved_system_name or "").strip()
    return f"{name} · local sobre {base_name}" if name else f"Local · sobre {base_name}"


def apply(c: TransformedCoordinate, choice: CoordinateChoice, frame: str = OFFICIAL_FRAME) -> TransformedCoordinate:
    """La coordenada en locales: norte, este y cota locales, el rótulo de las apps y el código «LOCAL»; lo de la
    cuadrícula base queda en `grid_*` y `base_height_m`. Si la localización no se puede calcular, `UserError`: las
    coordenadas locales no se saben, y nunca se da la cuadrícula en su lugar."""
    record = choice.localization
    try:
        computed = compute(record, choice.geoid_id, frame)
    except UserError as error:
        raise UserError(f"{NOT_COMPUTABLE_IN_USE} {error}") from None
    fr = computed.frame
    lat, lon = c.receiver_latitude_deg, c.receiver_longitude_deg
    e, n = fr.grid(lat, lon)
    v = fr.base_height(c.ellipsoidal_height_m, lat, lon)
    t = computed.solution.transform
    E, N = t.forward(e, n)
    Z = t.elevation(v, e, n)
    label = system_label(fr.name, record.saved_system_name)
    return replace(c, northing_m=N, easting_m=E, orthometric_height_m=Z, height_kind="local" if Z is not None else "",
                   convergence_deg=None, scale_factor=None, axis_order="E,N", crs_code=LOCAL_CODE, crs_name=label,
                   label=label, localized=True, base_crs_code=fr.crs_code, grid_easting_m=e, grid_northing_m=n,
                   geoid_uncertainty_m=None,
                   base_height_m=v)


def local_sigma(record: Localization, geoid_id: str | None, grid_sigma_e: float, grid_sigma_n: float,
                frame: str = OFFICIAL_FRAME) -> tuple[float, float]:
    """σ Este y σ Norte locales de las de la cuadrícula base (independientes): la semejanza las gira y las escala."""
    t = compute(record, geoid_id, frame).solution.transform
    a, b = t.a, t.b
    se2, sn2 = grid_sigma_e ** 2, grid_sigma_n ** 2
    return math.sqrt(a * a * se2 + b * b * sn2), math.sqrt(b * b * se2 + a * a * sn2)


# --- Textos (LocalizationText de las apps, exactos) ------------------------------------------------------------------

HORIZONTAL_TITLES = {"similarity": "Semejanza (giro, escala y traslación)",
                     "rigid": "Rígida (giro y traslación, escala 1)", "translation": "Solo traslación"}
VERTICAL_TITLES = {"automatic": "Automático (plano con 3 o más)", "constant": "Solo desplazamiento",
                   "none": "Sin ajuste vertical"}
HORIZONTAL_SHORT = {"similarity": "Semejanza", "rigid": "Rígida", "translation": "Solo traslación",
                    "none": "Sin ajuste en planta"}
VERTICAL_SHORT = {"plane": "Plano inclinado", "constant": "Solo desplazamiento", "none": "Sin ajuste vertical"}
FIELD_HELP = [
    "Al menos 3 pares en planta y 4 en cota para tener comprobación; con 5 o más se ven los errores.",
    "Repártelos alrededor de la obra, en el perímetro o más allá.",
    "Deja un punto de comprobación fuera del ajuste: quítale la H y la V.",
    "Mide los pares en FIJO, con promedio y bastón nivelado.",
    "La localización depende de la base: usa la misma coordenada de base o, al volver otro día, el desplazamiento "
    "de base sobre un par.",
]
MINUS = "−"


def number(x: float, decimals: int = 3) -> str:
    return f"{x:.{decimals}f}"


def length(x: float, decimals: int = 3) -> str:
    return f"{number(x, decimals)} m"


def signed_number(x: float, decimals: int = 3) -> str:
    if not math.isfinite(x):
        return "—"
    magnitude = f"{abs(x):.{decimals}f}"
    if abs(x) < 0.5 * 10 ** (-decimals):
        return magnitude
    return ("+" if x > 0 else MINUS) + magnitude


def signed_length(x: float, decimals: int = 3) -> str:
    return f"{signed_number(x, decimals)} m"


def ppm_text(ppm: float) -> str:
    return f"{ppm:.2f} ppm"


def ascii_signed_ppm(ppm: float) -> str:
    return f"{ppm:+.2f} ppm"


def signed_ppm_text(ppm: float) -> str:
    return ascii_signed_ppm(ppm).replace("-", MINUS)


def scale_text(scale: float) -> str:
    return f"{scale:.9f}"


def dms(degrees: float, second_decimals: int = 2) -> str:
    """`UnitFormatter.dms` de las apps: «1° 22' 26.37"», con «−» delante si es negativo (no en un cero)."""
    if not math.isfinite(degrees):
        return "—"
    absolute = abs(degrees)
    whole = int(absolute)
    minutes_total = (absolute - whole) * 60
    minutes = int(minutes_total)
    scale = 10 ** second_decimals
    seconds = _round_half_away((minutes_total - minutes) * 60 * scale) / scale
    if seconds >= 60:
        seconds = 0.0
        minutes += 1
    if minutes >= 60:
        minutes -= 60
        whole += 1
    sec_text = f"{seconds:.{second_decimals}f}"
    padded = "0" + sec_text if seconds < 10 else sec_text
    zero = whole == 0 and minutes == 0 and seconds == 0
    sign = MINUS if degrees < 0 and not zero else ""
    return f"{sign}{whole}° {minutes:02d}' {padded}\""


def rotation_text(arcsec: float) -> str:
    """El giro de acimut con signo siempre: «+1° 22' 26.37"»."""
    if not math.isfinite(arcsec):
        return "—"
    degrees = arcsec / 3600
    text = dms(degrees)
    return text if degrees < 0 else "+" + text


def arcsec_text(arcsec: float) -> str:
    return f"{arcsec:.2f}\""


def ascii_rotation(arcsec: float) -> str:
    return rotation_text(arcsec).replace("°", "d").replace(MINUS, "-")


def horizontal_notes(solution: Solution) -> list[str]:
    h = solution.horizontal
    if h.requested_method in ("similarity", "rigid") and h.method == "translation":
        return ["Con un solo par en planta solo hay traslación: no hay giro ni escala que sacar de un punto."]
    if h.method == "none":
        return ["Ningún par en planta: la localización solo corrige la cota."]
    return []


def vertical_notes(solution: Solution) -> list[str]:
    v = solution.vertical
    out = []
    if v.requested_method == "automatic" and v.method == "constant" and 0 < v.pair_count < 3:
        out.append("Con menos de 3 pares de cota el plano no se define: se usa solo el desplazamiento medio.")
    if v.requested_method != "none" and v.method == "none":
        out.append("Ningún par en cota: la cota local es la altura base.")
    return out


def scale_match_text(match: str) -> str:
    return ("La escala coincide con coordenadas de cuadrícula." if match == "grid" else
            "La escala coincide con coordenadas de terreno.")


def warning_message(w: Warning, options: Options) -> str:
    """El aviso en una frase, con cifras y unidades (`LocalizationText.message(_ warning:)` de las apps)."""
    c = w.code
    if c == "horizontalGeometryWeak":
        return (f"Geometría débil en planta: con 1 cm de error por par, la escala quedaría a ±{ppm_text(w.value_ppm)}. "
                "Los pares están demasiado juntos para fijar bien el giro y la escala.")
    if c == "noHorizontalRedundancy":
        return ("Sin comprobación en planta: con estos pares el ajuste encaja exacto aunque uno esté mal. "
                "Agrega otro par o uno de comprobación.")
    if c == "scaleUnusual":
        return (f"Escala fuera de lo normal: {signed_ppm_text(w.value_ppm)}. Entre cuadrícula y terreno no pasa de "
                "unas 1000 ppm; suele ser un par cambiado o el control en otra unidad, como pies.")
    if c == "scaleUnexpected":
        return (f"Escala fuera de lo esperado: {signed_ppm_text(w.value_ppm)}, que no es la de cuadrícula "
                f"({signed_ppm_text(0)}) ni la de terreno ({signed_ppm_text(w.expected_ppm)}). Suele ser un par "
                "cambiado, el control en otra unidad, como pies, o un sistema local a otra altura.")
    if c == "scaleFixedAwayFromGround":
        return (f"Escala fija en 1, pero aquí la de cuadrícula a terreno es {signed_ppm_text(w.expected_ppm)} "
                f"({length(abs(w.expected_ppm) * 1e-4)} en 100 m). Si el control está a nivel del terreno, usa "
                "“Semejanza” con dos o más pares; si está en la cuadrícula, así está bien.")
    if c == "verticalCollinearFallback":
        return (f"Los pares de cota están casi en línea ({length(w.value_m, 1)} de ancho): el plano no se define de "
                "lado y se usa solo el desplazamiento medio.")
    if c == "slopeLarge":
        return f"Plano muy inclinado: {ppm_text(w.value_ppm)}, {w.value_ppm:.0f} mm por km. Revisa las cotas de control."
    if c == "noVerticalRedundancy":
        return ("Sin comprobación en cota: el ajuste encaja exacto aunque una cota esté mal. "
                "Agrega otro par con cota.")
    if c == "horizontalResidualAboveTolerance":
        return (f"“{w.pair_name}”: residuo en planta de {length(w.value_m)}, por encima de la tolerancia de "
                f"{length(options.horizontal_tolerance_m)}."
                + (" Es un punto de comprobación: la localización no lo predice bien." if w.is_check else ""))
    if c == "verticalResidualAboveTolerance":
        return (f"“{w.pair_name}”: residuo en cota de {signed_length(w.value_m)}, por encima de la tolerancia de "
                f"{length(options.vertical_tolerance_m)}."
                + (" Es un punto de comprobación: la localización no lo predice bien." if w.is_check else ""))
    return c


def suspect_note(is_largest: bool, is_flagged: bool) -> str:
    if is_flagged:
        return "el mayor, y pasa de la tolerancia: el sospechoso"
    return "el mayor" if is_largest else ""


def height_base_text(geoid_id: str | None, frame: BaseFrame | None = None) -> str:
    """La altura base como se declara en la exportación (sin acentos)."""
    if not geoid_id:
        return "elipsoidal"
    grid = _api().geoid(geoid_id)
    if grid is None:
        return f"{geoid_id.upper()}, SIN ARCHIVO DEL GEOIDE: sin cota"
    m = grid.metadata
    kind = "normal" if m.get("heightKind") == "normal" else "ortometrica"
    return f"{kind} sobre {ascii_folded(str(m.get('name', geoid_id)))} {m.get('version', '')}".rstrip()


def ascii_folded(text: str) -> str:
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


ASCII_METHOD = {"similarity": "semejanza", "rigid": "rigida", "translation": "solo traslacion",
                "none": "sin ajuste en planta"}


def declaration_lines(choice: CoordinateChoice, frame: str = OFFICIAL_FRAME) -> list[str]:
    """Lo que se declara en una exportación **en locales** (`LocalizationText.exportDeclarationLines` de las apps):
    la localización entera, como el desfase de base. Corregir en silencio es peor que no corregir. Sin acentos."""
    record = choice.localization
    if record is None:
        return []
    api = _api()
    applied_text = record.applied_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if record.applied_at \
        else "sin fecha"
    code = record.base_grid_crs_code or "transversa de Mercator local"
    lines = [f"LOCALIZACION: coordenadas locales del proyecto, no de {code}. Aplicada {applied_text}, id "
             f"{record.id or 'sin id'}."]
    if record.base_grid_kind == "localTransverseMercator":
        lines.append("localizacion_cuadricula_base: transversa de Mercator local, meridiano central "
                     f"{(record.base_grid_central_meridian_deg or 0.0):.9f}, k0 1, latitud de origen 0, sin falso "
                     f"origen, elipsoide {ellipsoid_named(record.base_grid_ellipsoid)[0]}")
    else:
        d = api.definition(canonical_code(record.base_grid_crs_code))
        name = ascii_folded(d.name) if d is not None else record.base_grid_crs_code
        lines.append(f"localizacion_cuadricula_base: cuadricula {name} ({record.base_grid_crs_code})")
    lines.append(f"localizacion_altura_base: {height_base_text(choice.geoid_id)}")
    try:
        computed = compute(record, choice.geoid_id, frame)
    except UserError:
        lines.append("localizacion: NO SE PUEDE CALCULAR; no hay coordenadas locales en este archivo")
        return lines
    h, v = computed.solution.horizontal, computed.solution.vertical
    n3 = lambda x: f"{x:.3f}"
    lines.append(f"localizacion_planta: {ASCII_METHOD[h.method]}, pedido {ASCII_METHOD[h.requested_method]}; "
                 f"escala {scale_text(h.scale)} ({ascii_signed_ppm(h.scale_ppm)}); "
                 f"giro {ascii_rotation(h.azimuth_rotation_arcsec)} (azimut local = azimut de cuadricula + giro); "
                 f"centroide cuadricula N {n3(h.nc)} E {n3(h.ec)} -> local N {n3(h.Nc)} E {n3(h.Ec)}; "
                 f"E = {n3(h.translation_e)} + a*e - b*n, N = {n3(h.translation_n)} + b*e + a*n, "
                 f"a {h.a:.12f}, b {h.b:.12f}")
    if v.method == "none":
        lines.append("localizacion_cota: sin ajuste vertical; la cota local es la altura base")
    elif v.method == "constant":
        lines.append(f"localizacion_cota: solo desplazamiento {n3(v.c0)} m")
    else:
        lines.append(f"localizacion_cota: plano inclinado; {n3(v.c0)} m en N {n3(v.n0)} E {n3(v.e0)}; pendiente norte "
                     f"{ascii_signed_ppm(v.slope_north_ppm)}, este {ascii_signed_ppm(v.slope_east_ppm)}")
    emc_h = f"{n3(h.sigma0_m)} m" if h.sigma0_m is not None else "sin redundancia"
    emc_v = f"{n3(v.sigma0_m)} m" if v.sigma0_m is not None else "sin redundancia"
    lines.append(f"localizacion_pares: {len(record.pairs)} ({h.pair_count} en planta, {v.pair_count} en cota); "
                 f"RMS planta {emc_h} (redundancia {h.redundancy}); RMS cota {emc_v} (redundancia {v.redundancy})")
    return lines


def parameter_lines(computed: Computed) -> list[tuple[str, str]]:
    """Los parámetros como se enseñan en la hoja y en el informe (§3 de las apps)."""
    h, v = computed.solution.horizontal, computed.solution.vertical
    out: list[tuple[str, str]] = [("Cuadrícula base", computed.frame.name + (f" ({computed.frame.crs_code})"
                                                                            if computed.frame.crs_code else "")),
                                  ("Planta", HORIZONTAL_SHORT[h.method] + ("" if h.method == h.requested_method or
                                                                          h.method == "none" else
                                                                          f" (pedido: {HORIZONTAL_SHORT[h.requested_method]})"))]
    if h.method != "none":
        out.append(("Escala", f"{scale_text(h.scale)} ({signed_ppm_text(h.scale_ppm)})"
                    + (f" ± {h.scale_sigma_ppm:.2f} ppm" if h.scale_sigma_ppm is not None else "")))
        out.append(("Giro (acimut local = de cuadrícula + giro)", rotation_text(h.azimuth_rotation_arcsec)
                    + (f" ± {arcsec_text(h.rotation_sigma_arcsec)}" if h.rotation_sigma_arcsec is not None else "")))
        out.append(("Centroide en cuadrícula", f"N {h.nc:.3f} · E {h.ec:.3f} m"))
        out.append(("Centroide local", f"N {h.Nc:.3f} · E {h.Ec:.3f} m"))
        out.append(("Forma clásica", f"E = {h.translation_e:.3f} + a·e − b·n,  N = {h.translation_n:.3f} + b·e + a·n;"
                                     f"  a = {h.a:.12f},  b = {h.b:.12f}"))
        out.append(("Pares en planta", f"{h.pair_count} · redundancia {h.redundancy} · σ0 "
                    + (length(h.sigma0_m) if h.sigma0_m is not None else "sin redundancia")))
    out.append(("Cota", VERTICAL_SHORT[v.method]))
    if v.method != "none":
        text = f"{signed_length(v.c0)}"
        if v.method == "plane":
            text += (f" en N {v.n0:.3f} · E {v.e0:.3f} m; pendiente norte {signed_ppm_text(v.slope_north_ppm)}, "
                     f"este {signed_ppm_text(v.slope_east_ppm)}")
        out.append(("Desplazamiento" if v.method == "constant" else "Plano", text))
        out.append(("Pares en cota", f"{v.pair_count} · redundancia {v.redundancy} · σ0 "
                    + (length(v.sigma0_m) if v.sigma0_m is not None else "sin redundancia")))
    if computed.expected_ground_scale is not None:
        out.append(("Escala esperada a nivel del terreno",
                    f"{scale_text(computed.expected_ground_scale)} "
                    f"({signed_ppm_text((computed.expected_ground_scale - 1) * PPM)})"))
    return out


# --- El JSON del contrato (§5 de las apps): el que viaja en el .mvz y en el respaldo ZIP ----------------------------

def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text) -> datetime | None:
    if text is None:
        return None
    if not isinstance(text, str):
        raise ValueError("fecha que no es ISO 8601")
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    moment = datetime.fromisoformat(t)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def record_to_json(record: Localization) -> dict:
    """El registro con las claves exactas del contrato: nulos escritos, fechas ISO 8601 UTC al segundo."""
    if record.base_grid_kind == "localTransverseMercator":
        base = {"kind": "localTransverseMercator", "centralMeridianDegrees": record.base_grid_central_meridian_deg,
                "ellipsoid": record.base_grid_ellipsoid or "WGS 84"}
    else:
        base = {"kind": "projectCRS", "crsCode": record.base_grid_crs_code}
    height = ({"kind": "orthometric", "geoidModelID": record.height_base_geoid_id}
              if record.height_base_kind == "orthometric" else {"kind": "ellipsoidal"})
    out = {
        "id": record.id, "projectID": record.project_id,
        "pairs": [{"id": p.id, "name": p.name,
                   "control": {"northingMeters": p.control.northing_m, "eastingMeters": p.control.easting_m,
                               "elevationMeters": p.control.elevation_m},
                   "measured": {"pointID": p.measured.point_id, "name": p.measured.name or p.name,
                                "latitudeDegrees": p.measured.latitude_deg,
                                "longitudeDegrees": p.measured.longitude_deg,
                                "ellipsoidalHeightMeters": p.measured.ellipsoidal_height_m},
                   "usesHorizontal": p.uses_horizontal, "usesVertical": p.uses_vertical} for p in record.pairs],
        "horizontalMethod": record.horizontal_method, "verticalMethod": record.vertical_method,
        "horizontalToleranceMeters": record.horizontal_tolerance_m,
        "verticalToleranceMeters": record.vertical_tolerance_m,
        "baseGrid": base, "heightBase": height,
        "appliedAt": _iso(record.applied_at), "removedAt": _iso(record.removed_at),
    }
    if record.saved_system_id:
        out["savedSystemID"] = record.saved_system_id
    if record.saved_system_name:
        out["savedSystemName"] = record.saved_system_name
    return out


def _float_or_none(value, what: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{what} no es un número")
    return float(value)


def _uuid_text(value, what: str, required: bool = True) -> str:
    if value in (None, "") and not required:
        return ""
    try:
        return str(uuid.UUID(str(value))).upper()
    except (ValueError, TypeError):
        raise ValueError(f"{what} no es un UUID") from None


def record_from_json(d: dict, origin: str = "") -> Localization:
    """Lee un registro del contrato. Lo que escriba la otra app se lee: UUID en minúsculas, milisegundos, claves
    nulas omitidas. Una cuadrícula base desconocida se rechaza (no se adivina). `ValueError` si no se entiende."""
    if not isinstance(d, dict):
        raise ValueError("la localización no es un objeto")
    pairs = []
    for p in d.get("pairs") or []:
        control, measured = p.get("control") or {}, p.get("measured") or {}
        lat = _float_or_none(measured.get("latitudeDegrees"), "la latitud medida")
        lon = _float_or_none(measured.get("longitudeDegrees"), "la longitud medida")
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"el par «{p.get('name', '')}» no tiene latitud y longitud medidas válidas")
        uses_h, uses_v = p.get("usesHorizontal"), p.get("usesVertical")
        if not isinstance(uses_h, bool) or not isinstance(uses_v, bool):
            raise ValueError(f"el par «{p.get('name', '')}» no dice si entra en planta y en cota")
        pairs.append(LocalizationPair(
            name=str(p.get("name", "")),
            control=LocalizationControl(_float_or_none(control.get("northingMeters"), "el norte de control"),
                                        _float_or_none(control.get("eastingMeters"), "el este de control"),
                                        _float_or_none(control.get("elevationMeters"), "la cota de control")),
            measured=LocalizationMeasured(str(measured.get("name", p.get("name", ""))), lat, lon,
                                          _float_or_none(measured.get("ellipsoidalHeightMeters"), "la altura medida"),
                                          _uuid_text(measured.get("pointID"), "el punto medido", required=False)
                                          or None),
            uses_horizontal=uses_h, uses_vertical=uses_v, id=_uuid_text(p.get("id"), "el par", required=False)))
    base = d.get("baseGrid") or {}
    kind = base.get("kind")
    grid: dict = {"base_grid_kind": kind}
    if kind == "projectCRS":
        code = base.get("crsCode")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("la cuadrícula base no tiene código")
        grid.update(base_grid_crs_code=canonical_code(code))
    elif kind == "localTransverseMercator":
        grid.update(base_grid_central_meridian_deg=_float_or_none(base.get("centralMeridianDegrees"),
                                                                  "el meridiano central"),
                    base_grid_ellipsoid=str(base.get("ellipsoid", "WGS 84")))
        if grid["base_grid_central_meridian_deg"] is None:
            raise ValueError("la transversa local no tiene meridiano central")
    else:
        raise ValueError(f"cuadrícula base desconocida: {kind}")
    height = d.get("heightBase") or {}
    hkind = height.get("kind")
    if hkind not in ("ellipsoidal", "orthometric"):
        raise ValueError(f"altura base desconocida: {hkind}")
    hm, vm = d.get("horizontalMethod"), d.get("verticalMethod")
    if hm not in HORIZONTAL_METHODS or vm not in VERTICAL_METHODS:
        raise ValueError(f"métodos desconocidos: {hm}, {vm}")
    tol_h = _float_or_none(d.get("horizontalToleranceMeters", DEFAULT_HORIZONTAL_TOLERANCE_METERS), "la tolerancia")
    tol_v = _float_or_none(d.get("verticalToleranceMeters", DEFAULT_VERTICAL_TOLERANCE_METERS), "la tolerancia")
    return Localization(
        pairs=pairs, horizontal_method=hm, vertical_method=vm,
        horizontal_tolerance_m=DEFAULT_HORIZONTAL_TOLERANCE_METERS if tol_h is None else tol_h,
        vertical_tolerance_m=DEFAULT_VERTICAL_TOLERANCE_METERS if tol_v is None else tol_v,
        height_base_kind=hkind, height_base_geoid_id=str(height.get("geoidModelID", "")) if hkind == "orthometric"
        else "", id=_uuid_text(d.get("id"), "la localización", required=False),
        project_id=_uuid_text(d.get("projectID"), "el proyecto", required=False),
        applied_at=_parse_iso(d.get("appliedAt")), removed_at=_parse_iso(d.get("removedAt")),
        saved_system_id=_uuid_text(d.get("savedSystemID"), "el sistema guardado", required=False),
        saved_system_name=str(d.get("savedSystemName") or ""), origin=origin, **grid)


# --- Importar de un proyecto de TresVizo Field (.mvz o respaldo ZIP) -------------------------------------------------

MAX_FILE_BYTES = 256 * 1024 * 1024      # el tope de cada archivo del respaldo de las apps (BACKUP_ZIP_FORMAT §1)


@dataclass
class FieldProjectPoint:
    name: str
    latitude_deg: float
    longitude_deg: float
    ellipsoidal_height_m: float | None
    point_id: str = ""


@dataclass
class FieldProject:
    """Lo que la oficina usa de un proyecto de las apps: su sistema, su geoide, sus localizaciones y sus puntos."""
    name: str
    project_id: str
    crs_code: str
    geoid_id: str | None
    elevation_offset_m: float | None
    localizations: list[Localization]
    points: list[FieldProjectPoint] = field(default_factory=list)
    source: str = ""                         # el archivo (y la entrada del ZIP)
    written_by: str = ""                     # versión de la app que lo escribió

    @property
    def in_use(self) -> Localization | None:
        live = [loc for loc in self.localizations if loc.removed_at is None]
        return live[-1] if live else None


def _mvz_payload(data: bytes, source: str) -> tuple[dict, str]:
    """Abre el contenedor `.mvz` (`ProjectArchive` de las apps) y comprueba su suma sobre los bytes guardados."""
    try:
        container = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UserError(f"«{source}» no es un proyecto de TresVizo Field.") from None
    if not isinstance(container, dict) or container.get("format") != "mvz" or "payload" not in container:
        raise UserError(f"«{source}» no es un proyecto de TresVizo Field.")
    version = container.get("version")
    if not isinstance(version, int) or version > 2:
        raise UserError(f"«{source}» es de una versión más nueva de la app: actualiza este programa para leerlo.")
    payload = container["payload"]
    if not isinstance(payload, str):
        raise UserError(f"«{source}» no es un proyecto de TresVizo Field.")
    found = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if found != container.get("checksum"):
        raise UserError(f"«{source}» no coincide con su suma de verificación: está incompleto o dañado. No se "
                        "importa nada de él.")
    try:
        content = json.loads(payload)
    except json.JSONDecodeError:
        raise UserError(f"«{source}»: el contenido del proyecto no se puede leer.") from None
    return content, str(container.get("appVersion", ""))


def _point(p: dict) -> FieldProjectPoint | None:
    try:
        lat, lon = float(p["latitudeDegrees"]), float(p["longitudeDegrees"])
    except (KeyError, TypeError, ValueError):
        return None
    h = p.get("ellipsoidalHeightMeters")
    try:
        h = float(h) if h is not None else None
    except (TypeError, ValueError):
        h = None
    return FieldProjectPoint(str(p.get("name", "")), lat, lon, h, str(p.get("id", "")))


def _field_project(content: dict, source: str, written_by: str) -> FieldProject:
    project = content.get("project") if isinstance(content, dict) else None
    if not isinstance(project, dict):
        raise UserError(f"«{source}»: el proyecto no se puede leer.")
    name = str(project.get("name", "")) or Path(source).stem
    pid = str(project.get("id", ""))
    locs = []
    for index, raw in enumerate(content.get("localizations") or []):
        try:
            record = record_from_json(raw, origin=f"importada de «{Path(source).name}» ({name})")
        except ValueError as error:
            raise UserError(f"«{source}»: la localización {index + 1} del proyecto «{name}» no se puede leer "
                            f"({error}). No se importa nada de ella.") from None
        if pid and record.project_id and record.project_id.upper() != pid.upper():
            raise UserError(f"«{source}»: la localización {index + 1} es de otro proyecto.")
        locs.append(record)
    if sum(1 for loc in locs if loc.removed_at is None) > 1:
        raise UserError(f"«{source}»: el proyecto «{name}» trae más de una localización en uso; no se sabe cuál vale.")
    offset = project.get("elevationOffsetMeters")
    points = [pt for pt in (_point(p) for p in content.get("points") or [] if isinstance(p, dict)) if pt]
    return FieldProject(name=name, project_id=pid, crs_code=canonical_code(str(project.get("crsCode", ""))),
                        geoid_id=project.get("geoidModelID") or None,
                        elevation_offset_m=float(offset) if isinstance(offset, (int, float)) else None,
                        localizations=locs, points=points, source=source, written_by=written_by)


def read_field_project(path: str | Path) -> list[FieldProject]:
    """Los proyectos de un archivo de TresVizo Field: un `.mvz` («Compartir proyecto») o un respaldo `.zip` con la
    sección `projects` (`BACKUP_ZIP_FORMAT.md` de las apps). Comprueba la suma de cada `.mvz`; uno dañado no se lee."""
    path = Path(path)
    if path.stat().st_size > 4 * 1024 ** 3:
        raise UserError("Ese archivo pasa de 4 GB: no es un respaldo de TresVizo Field.")
    if zipfile.is_zipfile(path):
        return _read_backup(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise UserError(f"«{path.name}» pasa de 256 MB: no es un proyecto de TresVizo Field.")
    content, written_by = _mvz_payload(path.read_bytes(), path.name)
    return [_field_project(content, path.name, written_by)]


def _read_backup(path: Path) -> list[FieldProject]:
    out: list[FieldProject] = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "manifest.json" not in names:
                raise UserError(f"«{path.name}» no es un respaldo de TresVizo Field (no trae manifest.json).")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
            if manifest.get("format") != "tresvizo-field-backup":
                raise UserError("Este archivo no es un respaldo de TresVizo Field.")
            if not isinstance(manifest.get("formatVersion"), int) or manifest["formatVersion"] > 1:
                raise UserError("Este respaldo es de una versión más nueva de la app: actualiza este programa.")
            section = next((s for s in manifest.get("sections", []) if s.get("id") == "projects"), None)
            if section is None:
                raise UserError(f"«{path.name}» no incluye proyectos: expórtalo en la app con «Incluir proyectos».")
            index = json.loads(archive.read(section.get("file", "projects.json")).decode("utf-8-sig"))
            for row in index.get("projects", []):
                entry = str(row.get("file", ""))
                if not entry.startswith("projects/") or ".." in entry or entry not in names:
                    raise UserError(f"El respaldo está dañado: «{entry}» no se puede leer.")
                info = archive.getinfo(entry)
                if info.file_size > MAX_FILE_BYTES:
                    raise UserError(f"El respaldo trae «{entry}» de más de 256 MB descomprimido: no se abre.")
                content, written_by = _mvz_payload(archive.read(entry), f"{path.name} · {entry}")
                project = _field_project(content, f"{path.name} · {entry}", written_by)
                if row.get("id") and project.project_id and str(row["id"]).upper() != project.project_id.upper():
                    raise UserError(f"El respaldo está dañado: «{entry}» trae otro proyecto.")
                out.append(project)
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UserError(f"«{path.name}» no se puede leer como respaldo de TresVizo Field ({error}).") from None
    return out


def import_localization(project: FieldProject, which: Localization | None = None) -> Localization:
    """La localización de un proyecto de las apps para usarla aquí **igual**: sus pares, opciones y cuadrícula base
    tal cual (no se recalcula nada de la cuadrícula); se aplica con el geoide del sistema que se elija aquí, que debe
    ser el del proyecto (las apps declaran con qué alturas se aplicó)."""
    record = which or project.in_use
    if record is None:
        raise UserError(f"El proyecto «{project.name}» no tiene una localización en uso.")
    return replace(record, removed_at=None)


def choice_for(project: FieldProject, record: Localization) -> CoordinateChoice:
    """El sistema y el geoide con que el proyecto de las apps calcula su localización: la cuadrícula base guardada y
    su geoide de ahora (o el que declara la localización si el proyecto no lo dice)."""
    api = _api()
    code = record.base_grid_crs_code if record.base_grid_kind == "projectCRS" else project.crs_code
    geoid = project.geoid_id
    if geoid is None and record.height_base_kind == "orthometric":
        geoid = record.height_base_geoid_id or None
    d = api.definition(canonical_code(code)) if code else None
    return CoordinateChoice(crs_id=canonical_code(code) if d is not None else (code or "EPSG:4326"), zone="",
                            geoid_id=geoid, label=d.name if d is not None else code, localization=record)


# --- El control desde un CSV (PNEZD, NXYZD/PENZD) --------------------------------------------------------------------

@dataclass
class ControlPoint:
    name: str
    northing_m: float | None
    easting_m: float | None
    elevation_m: float | None
    description: str = ""


CSV_ORDERS = {"pnezd": ("name", "northing", "easting", "elevation", "description"),
              "penzd": ("name", "easting", "northing", "elevation", "description"),
              "nxyzd": ("name", "easting", "northing", "elevation", "description")}
HEADER_NAMES = {
    "name": {"name", "nombre", "punto", "point", "p", "id", "pt"},
    "northing": {"northing", "norte", "n", "y", "north"},
    "easting": {"easting", "este", "e", "x", "east"},
    "elevation": {"elevation", "cota", "z", "elev", "h", "altura", "height"},
    "description": {"description", "descripcion", "descripción", "desc", "d", "code", "codigo", "código"},
}


def _parse_number(text: str, decimal_comma: bool) -> float | None:
    t = (text or "").strip().replace(" ", "").replace(" ", "")
    if not t:
        return None
    if decimal_comma:
        t = t.replace(".", "").replace(",", ".") if t.count(",") == 1 else t
    value = float(t)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def read_control_csv(path: str | Path, order: str = "pnezd") -> tuple[list[ControlPoint], list[str]]:
    """Las coordenadas de control de un CSV. Con encabezado (Name/Nombre, Northing/Norte, Easting/Este,
    Elevation/Cota) se leen por su nombre; sin él, en el orden elegido (`pnezd`, `penzd` o `nxyzd`, que es PENZD).
    Separador coma, punto y coma o tabulador; con punto y coma o tabulador se acepta la coma decimal. Devuelve los
    puntos y los avisos por renglón; un nombre repetido o un número que no se lee es un error con su renglón."""
    raw = Path(path).read_bytes()
    if len(raw) > 20 * 1024 * 1024:
        raise UserError("El archivo pasa de 20 MB: divídelo en partes.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise UserError(f"«{Path(path).name}» no trae puntos.")
    sample = "\n".join(lines[:5])
    separator = "\t" if "\t" in sample else (";" if sample.count(";") > sample.count(",") else ",")
    decimal_comma = separator != ","
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=separator))
    columns = list(CSV_ORDERS.get(order, CSV_ORDERS["pnezd"]))
    first = [c.strip().lower() for c in rows[0]]

    def is_number(t):
        try:
            return _parse_number(t, decimal_comma) is not None
        except ValueError:
            return False

    start = 0
    if len(first) >= 3 and not any(is_number(t) for t in first[1:4]):
        mapped = []
        for cell in first:
            key = next((k for k, names in HEADER_NAMES.items() if ascii_folded(cell) in
                        {ascii_folded(n) for n in names}), None)
            mapped.append(key)
        if {"name", "northing", "easting"} <= {m for m in mapped if m}:
            columns = [m or "" for m in mapped]
        start = 1
    out: list[ControlPoint] = []
    notes: list[str] = []
    seen: set[str] = set()
    for number_, row in enumerate(rows[start:], start=start + 1):
        values = {key: (row[i].strip() if i < len(row) else "") for i, key in enumerate(columns) if key}
        name = values.get("name", "")
        if not name:
            notes.append(f"Renglón {number_}: sin nombre; no se usa.")
            continue
        try:
            n = _parse_number(values.get("northing", ""), decimal_comma)
            e = _parse_number(values.get("easting", ""), decimal_comma)
            z = _parse_number(values.get("elevation", ""), decimal_comma)
        except ValueError:
            raise UserError(f"«{Path(path).name}», renglón {number_} («{name}»): un número no se puede leer.") from None
        if (n is None) != (e is None):
            raise UserError(f"«{Path(path).name}», renglón {number_} («{name}»): tiene norte o este, pero no los dos.")
        if n is None and z is None:
            notes.append(f"Renglón {number_} («{name}»): sin coordenadas; no se usa.")
            continue
        if name in seen:
            raise UserError(f"«{Path(path).name}»: el punto «{name}» está repetido (renglón {number_}).")
        seen.add(name)
        out.append(ControlPoint(name, n, e, z, values.get("description", "")))
    return out, notes


def pairs_from(control: list[ControlPoint], measured: list[LocalizationMeasured]) -> tuple[list[LocalizationPair],
                                                                                           list[str]]:
    """Los pares por **nombre** (sin distinguir mayúsculas ni espacios de los extremos): cada control con su medido.
    Un par sin cota de control o sin altura medida no entra en cota; un banco de nivel (sin norte ni este), no en
    planta. Devuelve los pares y los nombres de control sin medido."""
    key = lambda s: s.strip().casefold()
    by_name: dict[str, LocalizationMeasured] = {}
    for m in measured:
        by_name.setdefault(key(m.name), m)
    pairs, missing = [], []
    for c in control:
        m = by_name.get(key(c.name))
        if m is None:
            missing.append(c.name)
            continue
        planar = c.northing_m is not None and c.easting_m is not None
        pairs.append(LocalizationPair(
            name=c.name, control=LocalizationControl(c.northing_m, c.easting_m, c.elevation_m), measured=m,
            uses_horizontal=planar, uses_vertical=c.elevation_m is not None and m.ellipsoidal_height_m is not None,
            id=new_uuid()))
    return pairs, missing


def measured_from_coordinate(name: str, c: TransformedCoordinate, source: str) -> LocalizationMeasured:
    """El medido de un punto del posproceso: la foto de su posición en el suelo, en el marco de la base."""
    return LocalizationMeasured(name=name, latitude_deg=c.receiver_latitude_deg, longitude_deg=c.receiver_longitude_deg,
                                ellipsoidal_height_m=c.ellipsoidal_height_m, source=source)
