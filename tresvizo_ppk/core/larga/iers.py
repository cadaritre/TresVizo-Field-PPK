"""Modelos de las Convenciones del IERS (2010) usados por el procesador de líneas largas, traducidos a Python:

- `gmf_tvppk`: funciones de mapeo troposférico globales GMF (Boehm et al. 2006), de la rutina GMF del IERS.
- `marea_solida_tvppk`: desplazamiento de la estación por las mareas terrestres sólidas (pasos 1 y 2 de la
  §7.1.1 de las Convenciones), de la rutina DEHANTTIDEINEL y sus subrutinas ST1IDIU, ST1ISEM, ST1L1, STEP2DIU y
  STEP2LON del IERS.

**Obra derivada del software de las Convenciones del IERS** (no es software de las Convenciones, ni lo distribuye
ni lo avala el Centro de Convenciones del IERS). En qué se basa y en qué difiere: los algoritmos y los coeficientes
son los de las rutinas originales en Fortran (versiones de las Convenciones 2010, tal como vienen en
`lib/iers/src` de RTKLIB-EX 2.5.1); aquí se traducen a Python con numpy, con los nombres cambiados
(`gmf_tvppk`, `marea_solida_tvppk` y las funciones privadas de este módulo), sin la rutina auxiliar de calendario
(el tiempo llega como día juliano modificado y fracción de día UTC, y TT−UTC se calcula con la tabla de segundos
intercalares del motor) y sin cambiar ningún coeficiente. Las pruebas comparan contra los casos de prueba que
traen las rutinas originales en su cabecera.

Aviso de la licencia del software de las Convenciones del IERS (reproducido íntegro, como pide):

  Copyright (C) 2008
  IERS Conventions Center

  ==================================
  IERS Conventions Software License
  ==================================

  NOTICE TO USER:

  BY USING THIS SOFTWARE YOU ACCEPT THE FOLLOWING TERMS AND CONDITIONS
  WHICH APPLY TO ITS USE.

  1. The Software is provided by the IERS Conventions Center ("the
     Center").

  2. Permission is granted to anyone to use the Software for any
     purpose, including commercial applications, free of charge,
     subject to the conditions and restrictions listed below.

  3. You (the user) may adapt the Software and its algorithms for your
     own purposes and you may distribute the resulting "derived work"
     to others, provided that the derived work complies with the
     following requirements:

     a) Your work shall be clearly identified so that it cannot be
        mistaken for IERS Conventions software and that it has been
        neither distributed by nor endorsed by the Center.

     b) Your work (including source code) must contain descriptions of
        how the derived work is based upon and/or differs from the
        original Software.

     c) The name(s) of all modified routine(s) that you distribute
        shall be changed.

     d) The origin of the IERS Conventions components of your derived
        work must not be misrepresented; you must not claim that you
        wrote the original Software.

     e) The source code must be included for all routine(s) that you
        distribute.  This notice must be reproduced intact in any
        source distribution.

  4. In any published work produced by the user and which includes
     results achieved by using the Software, you shall acknowledge
     that the Software was used in obtaining those results.

  5. The Software is provided to the user "as is" and the Center makes
     no warranty as to its use or performance.   The Center does not
     and cannot warrant the performance or results which the user may
     obtain by using the Software.  The Center makes no warranties,
     express or implied, as to non-infringement of third party rights,
     merchantability, or fitness for any particular purpose.  In no
     event will the Center be liable to the user for any consequential,
     incidental, or special damages, including any lost profits or lost
     savings, even if a Center representative has been advised of such
     damages, or for any claim by any third party.

  Correspondence concerning IERS Conventions software should be
  addressed as follows:

                     Gerard Petit
     Internet email: gpetit[at]bipm.org
     Postal address: IERS Conventions Center
                     Time, frequency and gravimetry section, BIPM
                     Pavillon de Breteuil
                     92312 Sevres  FRANCE

     or

                     Brian Luzum
     Internet email: brian.luzum[at]usno.navy.mil
     Postal address: IERS Conventions Center
                     Earth Orientation Department
                     3450 Massachusetts Ave, NW
                     Washington, DC 20392

Referencias: Boehm, J., Niell, A., Tregoning, P. y Schuh, H. (2006), Global Mapping Functions (GMF): A new
empirical mapping function based on numerical weather model data, Geophys. Res. Lett. 33, L07304,
doi:10.1029/2005GL025545. Petit, G. y Luzum, B. (eds.), IERS Conventions (2010), IERS Technical Note No. 36,
BKG (2010). Mathews, P. M., Dehant, V. y Gipson, J. M. (1997), Tidal station displacements, J. Geophys. Res. 102,
20469–20477.
"""
from __future__ import annotations

import math

import numpy as np

# --- GMF (rutina GMF del IERS): coeficientes de los armónicos esféricos (grado y orden 9) ----------------------------

_AH_MEAN = (
    125.17, 0.8503, 0.06936, -6.76, 0.1771, 0.0113, 0.5963, 0.01808, 0.002801, -0.001414, -1.212, 0.093, 0.003683,
    0.001095, 4.671e-05, 0.3959, -0.03867, 0.005413, -0.0005289, 0.0003229, 2.067e-05, 0.3, 0.02031, 0.0059,
    0.0004573, -7.619e-05, 2.327e-06, 3.845e-06, 0.1182, 0.01158, 0.005445, 6.219e-05, 4.204e-06, -2.093e-06,
    1.54e-07, -4.28e-08, -0.4751, -0.0349, 0.001758, 0.0004019, -2.799e-06, -1.287e-06, 5.468e-07, 7.58e-08,
    -6.3e-09, -0.116, 0.008301, 0.0008771, 9.955e-05, -1.718e-06, -2.012e-06, 1.17e-08, 1.79e-08, -1.3e-09, 1e-10,
)
_BH_MEAN = (
    0.0, 0.0, 0.03249, 0.0, 0.03324, 0.0185, 0.0, -0.1115, 0.02519, 0.004923, 0.0, 0.02737, 0.01595, -0.0007332,
    0.0001933, 0.0, -0.04796, 0.006381, -0.0001599, -0.0003685, 1.815e-05, 0.0, 0.07033, 0.002426, -0.001111,
    -0.0001357, -7.828e-06, 2.547e-06, 0.0, 0.005779, 0.003133, -0.0005312, -2.028e-05, 2.323e-07, -9.1e-08,
    -1.65e-08, 0.0, 0.03688, -0.0008638, -8.514e-05, -2.828e-05, 5.403e-07, 4.39e-07, 1.35e-08, 1.8e-09, 0.0,
    -0.02736, -0.0002977, 8.113e-05, 2.329e-07, 8.451e-07, 4.49e-08, -8.1e-09, -1.5e-09, 2e-10,
)
_AH_AMP = (
    -0.2738, -2.837, 0.01298, -0.3588, 0.02413, 0.03427, -0.7624, 0.07272, 0.0216, -0.003385, 0.4424, 0.03722,
    0.02195, -0.001503, 0.0002426, 0.3013, 0.05762, 0.01019, -0.0004476, 6.79e-05, 3.227e-05, 0.3123, -0.03535,
    0.00484, 3.025e-06, -4.363e-05, 2.854e-07, -1.286e-06, -0.6725, -0.0373, 0.0008964, 0.0001399, -3.99e-06,
    7.431e-06, -2.796e-07, -1.601e-07, 0.04068, -0.01352, 0.0007282, 9.594e-05, 2.07e-06, -9.62e-08, -2.742e-07,
    -6.37e-08, -6.3e-09, 0.08625, -0.005971, 0.0004705, 2.335e-05, 4.226e-06, 2.475e-07, -8.85e-08, -3.6e-08,
    -2.9e-09, 0.0,
)
_BH_AMP = (
    0.0, 0.0, -0.1136, 0.0, -0.1868, -0.01399, 0.0, -0.1043, 0.01175, -0.00224, 0.0, -0.03222, 0.01333, -0.002647,
    -2.316e-05, 0.0, 0.05339, 0.01107, -0.003116, -0.0001079, -1.299e-05, 0.0, 0.004861, 0.008891, -0.0006448,
    -1.279e-05, 6.358e-06, -1.417e-07, 0.0, 0.03041, 0.00115, -0.0008743, -2.781e-05, 6.367e-07, -1.14e-08,
    -4.2e-08, 0.0, -0.02982, -0.003, 1.394e-05, -3.29e-05, -1.705e-07, 7.44e-08, 2.72e-08, -6.6e-09, 0.0, 0.01236,
    -0.0009981, -3.792e-05, -1.355e-05, 1.162e-06, -1.789e-07, 1.47e-08, -2.4e-09, -4e-10,
)
_AW_MEAN = (
    56.4, 1.555, -1.011, -3.975, 0.03171, 0.1065, 0.6175, 0.1376, 0.04229, 0.003028, 1.688, -0.1692, 0.05478,
    0.02473, 0.0006059, 2.278, 0.006614, -0.0003505, -0.006697, 0.0008402, 0.0007033, -3.236, 0.2184, -0.04611,
    -0.01613, -0.001604, 5.42e-05, 7.922e-05, -0.2711, -0.4406, -0.03376, -0.002801, -0.000409, -2.056e-05,
    6.894e-06, 2.317e-06, 1.941, -0.2562, 0.01598, 0.005449, 0.0003544, 1.148e-05, 7.503e-06, -5.667e-07,
    -3.66e-08, 0.8683, -0.05931, -0.001864, -0.0001277, 0.0002029, 1.269e-05, 1.629e-06, 9.66e-08, -1.015e-07,
    -5e-10,
)
_BW_MEAN = (
    0.0, 0.0, 0.2592, 0.0, 0.02974, -0.5471, 0.0, -0.5926, -0.103, -0.01567, 0.0, 0.171, 0.09025, 0.02689,
    0.002243, 0.0, 0.3439, 0.02402, 0.00541, 0.001601, 9.669e-05, 0.0, 0.09502, -0.03063, -0.001055, -0.0001067,
    -0.000113, 2.124e-05, 0.0, -0.3129, 0.008463, 0.0002253, 7.413e-05, -9.376e-05, -1.606e-06, 2.06e-06, 0.0,
    0.2739, 0.001167, -2.246e-05, -0.0001287, -2.438e-05, -7.561e-07, 1.158e-06, 4.95e-08, 0.0, -0.1344, 0.005342,
    0.0003775, -6.756e-05, -1.686e-06, -1.184e-06, 2.768e-07, 2.73e-08, 5.7e-09,
)
_AW_AMP = (
    0.1023, -2.695, 0.3417, -0.1405, 0.3175, 0.2116, 3.536, -0.1505, -0.0166, 0.02967, 0.3819, -0.1695, -0.07444,
    0.007409, -0.006262, -1.836, -0.01759, -0.06256, -0.002371, 0.0007947, 0.0001501, -0.8603, -0.136, -0.03629,
    -0.003706, -0.0002976, 1.857e-05, 3.021e-05, 2.248, -0.1178, 0.01255, 0.001134, -0.0002161, -5.817e-06,
    8.836e-07, -1.769e-07, 0.7313, -0.1188, 0.01145, 0.001011, 0.0001083, 2.57e-06, -2.14e-06, -5.71e-08, 2e-08,
    -1.632, -0.006948, -0.003893, 0.0008592, 7.577e-05, 4.539e-06, -3.852e-07, -2.213e-07, -1.37e-08, 5.8e-09,
)
_BW_AMP = (
    0.0, 0.0, -0.08865, 0.0, -0.4309, 0.0634, 0.0, 0.1162, 0.06176, -0.004234, 0.0, 0.253, 0.04017, -0.006204,
    0.004977, 0.0, -0.1737, -0.005638, 0.0001488, 0.0004857, -0.0001809, 0.0, -0.1514, -0.01685, 0.005333,
    -7.611e-05, 2.394e-05, 8.195e-06, 0.0, 0.09326, -0.01275, -0.0003071, 5.374e-05, -3.391e-05, -7.436e-06,
    6.747e-07, 0.0, -0.08637, -0.003807, -0.0006833, -3.861e-05, -2.268e-05, 1.454e-06, 3.86e-07, -1.068e-07, 0.0,
    -0.02658, -0.001947, 0.0007131, -3.506e-05, 1.885e-07, 5.792e-07, 3.99e-08, 2e-08, -5.7e-09,
)


def _legendre_harmonics(lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
    """Funciones de Legendre (Heiskanen y Moritz, 1967, ec. 1-62) por cos/sen(m·λ), en el orden de la rutina."""
    t = math.sin(lat)
    n_max = 9
    fact = [math.factorial(i) for i in range(2 * n_max + 2)]
    p = np.zeros((n_max + 1, n_max + 1))
    for i in range(n_max + 1):
        for j in range(i + 1):
            ir = (i - j) // 2
            total = 0.0
            for k in range(ir + 1):
                total += ((-1) ** k * fact[2 * i - 2 * k] / fact[k] / fact[i - k] / fact[i - j - 2 * k]
                          * t ** (i - j - 2 * k))
            p[i, j] = 1.0 / 2 ** i * math.sqrt((1 - t * t) ** j) * total
    ap, bp = [], []
    for n in range(n_max + 1):
        for m in range(n + 1):
            ap.append(p[n, m] * math.cos(m * lon))
            bp.append(p[n, m] * math.sin(m * lon))
    return np.array(ap), np.array(bp)


class GmfSitio:
    """Coeficientes de la GMF de un sitio (no dependen de la elevación): se calculan una vez por estación y día."""

    def __init__(self, mjd: float, lat_rad: float, lon_rad: float, height_m: float) -> None:
        doy = mjd - 44239.0 + 1 - 28                  # día de referencia: 28 de enero de 1980 (Niell 1996)
        ap, bp = _legendre_harmonics(lat_rad, lon_rad)
        season = math.cos(doy / 365.25 * 2.0 * math.pi)
        ahm = float(np.dot(_AH_MEAN, ap) + np.dot(_BH_MEAN, bp)) * 1e-5
        aha = float(np.dot(_AH_AMP, ap) + np.dot(_BH_AMP, bp)) * 1e-5
        awm = float(np.dot(_AW_MEAN, ap) + np.dot(_BW_MEAN, bp)) * 1e-5
        awa = float(np.dot(_AW_AMP, ap) + np.dot(_BW_AMP, bp)) * 1e-5
        self.ah = ahm + aha * season
        self.aw = awm + awa * season
        self.bh = 0.0029
        c0h = 0.062
        if lat_rad < 0.0:
            phh, c11h, c10h = math.pi, 0.007, 0.002
        else:
            phh, c11h, c10h = 0.0, 0.005, 0.001
        self.ch = c0h + ((math.cos(doy / 365.25 * 2.0 * math.pi + phh) + 1.0) * c11h / 2.0 + c10h) * \
            (1.0 - math.cos(lat_rad))
        self.height_km = height_m / 1000.0

    def __call__(self, elevation_rad):
        """(hidrostática, húmeda) para una o varias elevaciones (radianes)."""
        sine = np.sin(np.asarray(elevation_rad, dtype=float))
        ah, bh, ch = self.ah, self.bh, self.ch
        gmfh = (1.0 + ah / (1.0 + bh / (1.0 + ch))) / (sine + ah / (sine + bh / (sine + ch)))
        a_ht, b_ht, c_ht = 2.53e-5, 5.49e-3, 1.14e-3     # corrección por altura de Niell (1996)
        ht = 1.0 / sine - (1.0 + a_ht / (1.0 + b_ht / (1.0 + c_ht))) / (sine + a_ht / (sine + b_ht / (sine + c_ht)))
        gmfh = gmfh + ht * self.height_km
        bw, cw, aw = 0.00146, 0.04391, self.aw
        gmfw = (1.0 + aw / (1.0 + bw / (1.0 + cw))) / (sine + aw / (sine + bw / (sine + cw)))
        return gmfh, gmfw


def gmf_tvppk(mjd: float, lat_rad: float, lon_rad: float, height_m: float, zenith_rad: float) -> tuple[float, float]:
    """Funciones de mapeo GMF hidrostática y húmeda (altura sobre el nivel medio del mar, como la rutina)."""
    h, w = GmfSitio(mjd, lat_rad, lon_rad, height_m)(math.pi / 2.0 - zenith_rad)
    return float(h), float(w)


# --- Mareas terrestres sólidas (DEHANTTIDEINEL y subrutinas del IERS) -----------------------------------------------

_H20, _L20, _H3, _L3 = 0.6078, 0.0847, 0.292, 0.015
_MASS_RATIO_SUN = 332946.0482
_MASS_RATIO_MOON = 0.0123000371
_RE = 6378136.6

# STEP2DIU: argumentos (s, h, p, N', ps) y amplitudes (mm): ΔR sen, ΔR cos, ΔT sen, ΔT cos
_DIURNAL = (
    (-3, 0, 2, 0, 0, -0.01, 0, 0, 0), (-3, 2, 0, 0, 0, -0.01, 0, 0, 0), (-2, 0, 1, -1, 0, -0.02, 0, 0, 0),
    (-2, 0, 1, 0, 0, -0.08, 0, -0.01, 0.01), (-2, 2, -1, 0, 0, -0.02, 0, 0, 0), (-1, 0, 0, -1, 0, -0.10, 0, 0, 0),
    (-1, 0, 0, 0, 0, -0.51, 0, -0.02, 0.03), (-1, 2, 0, 0, 0, 0.01, 0, 0, 0), (0, -2, 1, 0, 0, 0.01, 0, 0, 0),
    (0, 0, -1, 0, 0, 0.02, 0, 0, 0), (0, 0, 1, 0, 0, 0.06, 0, 0, 0), (0, 0, 1, 1, 0, 0.01, 0, 0, 0),
    (0, 2, -1, 0, 0, 0.01, 0, 0, 0), (1, -3, 0, 0, 1, -0.06, 0, 0, 0), (1, -2, 0, -1, 0, 0.01, 0, 0, 0),
    (1, -2, 0, 0, 0, -1.23, -0.07, 0.06, 0.01), (1, -1, 0, 0, -1, 0.02, 0, 0, 0), (1, -1, 0, 0, 1, 0.04, 0, 0, 0),
    (1, 0, 0, -1, 0, -0.22, 0.01, 0.01, 0), (1, 0, 0, 0, 0, 12.00, -0.80, -0.67, -0.03),
    (1, 0, 0, 1, 0, 1.73, -0.12, -0.10, 0), (1, 0, 0, 2, 0, -0.04, 0, 0, 0), (1, 1, 0, 0, -1, -0.50, -0.01, 0.03, 0),
    (1, 1, 0, 0, 1, 0.01, 0, 0, 0), (0, 1, 0, 1, -1, -0.01, 0, 0, 0), (1, 2, -2, 0, 0, -0.01, 0, 0, 0),
    (1, 2, 0, 0, 0, -0.11, 0.01, 0.01, 0), (2, -2, 1, 0, 0, -0.01, 0, 0, 0), (2, 0, -1, 0, 0, -0.02, 0, 0, 0),
    (3, 0, 0, 0, 0, 0, 0, 0, 0), (3, 0, 0, 1, 0, 0, 0, 0, 0),
)
# STEP2LON: argumentos y amplitudes (mm): ΔR cos, ΔT cos, ΔR sen, ΔT sen
_LONG_PERIOD = (
    (0, 0, 0, 1, 0, 0.47, 0.23, 0.16, 0.07), (0, 2, 0, 0, 0, -0.20, -0.12, -0.11, -0.05),
    (1, 0, -1, 0, 0, -0.11, -0.08, -0.09, -0.04), (2, 0, 0, 0, 0, -0.13, -0.11, -0.15, -0.07),
    (2, 0, 0, 1, 0, -0.05, -0.05, -0.06, -0.03),
)


def _geocentric(xsta: np.ndarray) -> tuple[float, float, float, float, float]:
    r = float(np.linalg.norm(xsta))
    sinphi = xsta[2] / r
    cosphi = math.hypot(xsta[0], xsta[1]) / r
    sinla = xsta[1] / cosphi / r
    cosla = xsta[0] / cosphi / r
    return r, sinphi, cosphi, sinla, cosla


def _to_xyz(dr: float, dn: float, de: float, sinphi: float, cosphi: float, sinla: float, cosla: float) -> np.ndarray:
    return np.array([dr * cosla * cosphi - de * sinla - dn * sinphi * cosla,
                     dr * sinla * cosphi + de * cosla - dn * sinphi * sinla,
                     dr * sinphi + dn * cosphi])


def _out_of_phase_diurnal(xsta, xsun, xmon, fac2sun, fac2mon) -> np.ndarray:      # ST1IDIU
    dhi, dli = -0.0025, -0.0007
    _, sinphi, cosphi, sinla, cosla = _geocentric(xsta)
    cos2phi = cosphi * cosphi - sinphi * sinphi
    dr = dn = de = 0.0
    for x, fac in ((xsun, fac2sun), (xmon, fac2mon)):
        r2 = float(np.dot(x, x))
        dr += -3.0 * dhi * sinphi * cosphi * fac * x[2] * (x[0] * sinla - x[1] * cosla) / r2
        dn += -3.0 * dli * cos2phi * fac * x[2] * (x[0] * sinla - x[1] * cosla) / r2
        de += -3.0 * dli * sinphi * fac * x[2] * (x[0] * cosla + x[1] * sinla) / r2
    return _to_xyz(dr, dn, de, sinphi, cosphi, sinla, cosla)


def _out_of_phase_semidiurnal(xsta, xsun, xmon, fac2sun, fac2mon) -> np.ndarray:  # ST1ISEM
    dhi, dli = -0.0022, -0.0007
    _, sinphi, cosphi, sinla, cosla = _geocentric(xsta)
    costwola = cosla * cosla - sinla * sinla
    sintwola = 2.0 * cosla * sinla
    dr = dn = de = 0.0
    for x, fac in ((xsun, fac2sun), (xmon, fac2mon)):
        r2 = float(np.dot(x, x))
        a = (x[0] ** 2 - x[1] ** 2) * sintwola - 2.0 * x[0] * x[1] * costwola
        b = (x[0] ** 2 - x[1] ** 2) * costwola + 2.0 * x[0] * x[1] * sintwola
        dr += -3.0 / 4.0 * dhi * cosphi ** 2 * fac * a / r2
        dn += 3.0 / 2.0 * dli * sinphi * cosphi * fac * a / r2
        de += -3.0 / 2.0 * dli * cosphi * fac * b / r2
    return _to_xyz(dr, dn, de, sinphi, cosphi, sinla, cosla)


def _latitude_dependence(xsta, xsun, xmon, fac2sun, fac2mon) -> np.ndarray:      # ST1L1
    l1d, l1sd = 0.0012, 0.0024
    _, sinphi, cosphi, sinla, cosla = _geocentric(xsta)
    dn = de = 0.0
    for x, fac in ((xsun, fac2sun), (xmon, fac2mon)):
        r2 = float(np.dot(x, x))
        dn += -l1d * sinphi ** 2 * fac * x[2] * (x[0] * cosla + x[1] * sinla) / r2
        de += l1d * sinphi * (cosphi ** 2 - sinphi ** 2) * fac * x[2] * (x[0] * sinla - x[1] * cosla) / r2
    out = _to_xyz(0.0, 3.0 * dn, 3.0 * de, sinphi, cosphi, sinla, cosla)
    costwola = cosla ** 2 - sinla ** 2
    sintwola = 2.0 * cosla * sinla
    dn = de = 0.0
    for x, fac in ((xsun, fac2sun), (xmon, fac2mon)):
        r2 = float(np.dot(x, x))
        b = (x[0] ** 2 - x[1] ** 2) * costwola + 2.0 * x[0] * x[1] * sintwola
        a = (x[0] ** 2 - x[1] ** 2) * sintwola - 2.0 * x[0] * x[1] * costwola
        dn += -l1sd / 2.0 * sinphi * cosphi * fac * b / r2
        de += -l1sd / 2.0 * sinphi ** 2 * cosphi * fac * a / r2
    return out + _to_xyz(0.0, 3.0 * dn, 3.0 * de, sinphi, cosphi, sinla, cosla)


def _fundamental_arguments(t: float) -> tuple[float, float, float, float, float, float]:
    """s, τ sin la hora, h, p, N', ps (grados) de STEP2DIU/STEP2LON, con T en siglos julianos TT desde J2000."""
    s = 218.31664563 + (481267.88194 + (-0.0014663889 + 0.00000185139 * t) * t) * t
    tau0 = 280.4606184 + (36000.7700536 + (0.00038793 + (-0.0000000258) * t) * t) * t - s
    pr = (1.396971278 + (0.000308889 + (0.000000021 + 0.000000007 * t) * t) * t) * t
    s = s + pr
    h = 280.46645 + (36000.7697489 + (0.00030322222 + (0.000000020 + (-0.00000000654) * t) * t) * t) * t
    p = 83.35324312 + (4069.01363525 + (-0.01032172222 + (-0.0000124991 + 0.00000005263 * t) * t) * t) * t
    zns = 234.95544499 + (1934.13626197 + (-0.00207561111 + (-0.00000213944 + 0.00000001650 * t) * t) * t) * t
    ps = 282.93734098 + (1.71945766667 + (0.00045688889 + (-0.00000001778 + (-0.00000000334) * t) * t) * t) * t
    return s, tau0, h, p, zns, ps


def _step2_diurnal(xsta, fhr: float, t: float) -> np.ndarray:                     # STEP2DIU
    s, tau0, h, p, zns, ps = _fundamental_arguments(t)
    tau = math.fmod(fhr * 15.0 + tau0, 360.0)
    s, h, p, zns, ps = (math.fmod(v, 360.0) for v in (s, h, p, zns, ps))
    _, sinphi, cosphi, sinla, cosla = _geocentric(xsta)
    zla = math.atan2(xsta[1], xsta[0])
    out = np.zeros(3)
    for row in _DIURNAL:
        thetaf = math.radians(tau + row[0] * s + row[1] * h + row[2] * p + row[3] * zns + row[4] * ps)
        arg = thetaf + zla
        dr = row[5] * 2.0 * sinphi * cosphi * math.sin(arg) + row[6] * 2.0 * sinphi * cosphi * math.cos(arg)
        dn = row[7] * (cosphi ** 2 - sinphi ** 2) * math.sin(arg) + row[8] * (cosphi ** 2 - sinphi ** 2) * math.cos(arg)
        de = row[7] * sinphi * math.cos(arg) - row[8] * sinphi * math.sin(arg)
        out += _to_xyz(dr, dn, de, sinphi, cosphi, sinla, cosla)
    return out / 1000.0


def _step2_long_period(xsta, t: float) -> np.ndarray:                             # STEP2LON
    s, _, h, p, zns, ps = _fundamental_arguments(t)
    s, h, p, zns, ps = (math.fmod(v, 360.0) for v in (s, h, p, zns, ps))
    _, sinphi, cosphi, sinla, cosla = _geocentric(xsta)
    out = np.zeros(3)
    for row in _LONG_PERIOD:
        thetaf = math.radians(row[0] * s + row[1] * h + row[2] * p + row[3] * zns + row[4] * ps)
        dr = row[5] * (3.0 * sinphi ** 2 - 1.0) / 2.0 * math.cos(thetaf) + \
            row[7] * (3.0 * sinphi ** 2 - 1.0) / 2.0 * math.sin(thetaf)
        dn = row[6] * (cosphi * sinphi * 2.0) * math.cos(thetaf) + row[8] * (cosphi * sinphi * 2.0) * math.sin(thetaf)
        out += _to_xyz(dr, dn, 0.0, sinphi, cosphi, sinla, cosla)
    return out / 1000.0


def marea_solida_tvppk(xsta, xsun, xmon, mjd_utc: float, fhr_utc: float, tai_minus_utc: float) -> np.ndarray:
    """Desplazamiento (m, ECEF) de la estación por la marea terrestre sólida, en el sistema «libre de marea
    convencional» del ITRF (como DEHANTTIDEINEL: pasos 1 y 2, sin quitar la marea permanente).

    `mjd_utc`: día juliano modificado (entero) del día UTC; `fhr_utc`: hora UTC del día; `tai_minus_utc`: TAI−UTC
    (s). Las posiciones del Sol y la Luna, geocéntricas en el mismo marco terrestre que la estación (m)."""
    xsta = np.asarray(xsta, dtype=float)
    xsun = np.asarray(xsun, dtype=float)
    xmon = np.asarray(xmon, dtype=float)
    rsta = float(np.linalg.norm(xsta))
    rsun = float(np.linalg.norm(xsun))
    rmon = float(np.linalg.norm(xmon))
    scsun = float(np.dot(xsta, xsun)) / rsta / rsun
    scmon = float(np.dot(xsta, xmon)) / rsta / rmon
    cosphi = math.hypot(xsta[0], xsta[1]) / rsta
    h2 = _H20 - 0.0006 * (1.0 - 1.5 * cosphi ** 2)
    l2 = _L20 + 0.0002 * (1.0 - 1.5 * cosphi ** 2)
    p2sun = 3.0 * (h2 / 2.0 - l2) * scsun ** 2 - h2 / 2.0
    p2mon = 3.0 * (h2 / 2.0 - l2) * scmon ** 2 - h2 / 2.0
    p3sun = 5.0 / 2.0 * (_H3 - 3.0 * _L3) * scsun ** 3 + 3.0 / 2.0 * (_L3 - _H3) * scsun
    p3mon = 5.0 / 2.0 * (_H3 - 3.0 * _L3) * scmon ** 3 + 3.0 / 2.0 * (_L3 - _H3) * scmon
    x2sun = 3.0 * l2 * scsun
    x2mon = 3.0 * l2 * scmon
    x3sun = 3.0 * _L3 / 2.0 * (5.0 * scsun ** 2 - 1.0)
    x3mon = 3.0 * _L3 / 2.0 * (5.0 * scmon ** 2 - 1.0)
    fac2sun = _MASS_RATIO_SUN * _RE * (_RE / rsun) ** 3
    fac2mon = _MASS_RATIO_MOON * _RE * (_RE / rmon) ** 3
    fac3sun = fac2sun * (_RE / rsun)
    fac3mon = fac2mon * (_RE / rmon)
    dx = (fac2sun * (x2sun * xsun / rsun + p2sun * xsta / rsta) + fac2mon * (x2mon * xmon / rmon + p2mon * xsta / rsta)
          + fac3sun * (x3sun * xsun / rsun + p3sun * xsta / rsta) + fac3mon * (x3mon * xmon / rmon + p3mon * xsta / rsta))
    dx = dx + _out_of_phase_diurnal(xsta, xsun, xmon, fac2sun, fac2mon)
    dx = dx + _out_of_phase_semidiurnal(xsta, xsun, xmon, fac2sun, fac2mon)
    dx = dx + _latitude_dependence(xsta, xsun, xmon, fac2sun, fac2mon)
    # siglos julianos TT desde J2000 (como la rutina: día UTC + fracción + (TAI−UTC + 32.184 s))
    t = ((mjd_utc + 2400000.5 - 2451545.0) + fhr_utc / 24.0) / 36525.0
    t += (tai_minus_utc + 32.184) / (3600.0 * 24.0 * 36525.0)
    dx = dx + _step2_diurnal(xsta, fhr_utc, t)
    dx = dx + _step2_long_period(xsta, t)
    return dx
