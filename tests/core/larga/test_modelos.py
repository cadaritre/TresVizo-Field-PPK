"""Modelos del procesador de líneas largas contra sus casos publicados: GMF y mareas sólidas con los casos de
prueba de las rutinas del IERS, Sol y Luna, troposfera a priori, wind-up y antenas del ANTEX incluido."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np
import pytest

from tresvizo_ppk.core.geodesy import leap_seconds
from tresvizo_ppk.core.larga import antena, astro, iers, modelos


def test_gmf_iers_test_case():
    # caso de prueba de la cabecera de GMF (IERS): NRAO, Green Bank, MJD 55055
    h, w = iers.gmf_tvppk(55055.0, 0.6708665767, -1.393397187, 844.715, 1.278564131)
    assert h == pytest.approx(3.425245519339138678, abs=1e-12)
    assert w == pytest.approx(3.449589116182419257, abs=1e-12)


TIDE_CASES = [
    ((4075578.385, 931852.890, 4801570.154), (137859926952.015, 54228127881.4350, 23509422341.6960),
     (-179996231.920342, -312468450.131567, -169288918.592160), date(2009, 4, 13), 34,
     (0.07700420357108125891, 0.06304056321824967613, 0.05516568152597246810)),
    ((1112189.660, -4842955.026, 3985352.284), (-54537460436.2357, 130244288385.279, 56463429031.5996),
     (300396716.912, 243238281.451, 120548075.939), date(2012, 7, 13), 35,
     (-0.02036831479592075833, 0.05658254776225972449, -0.07597679676871742227)),
    ((1112200.5696, -4842957.8511, 3985345.9122), (100210282451.6279, 103055630398.3160, 56855096480.4475),
     (369817604.4348, 1897917.5258, 120804980.8284), date(2015, 7, 15), 36,
     (0.00509570869172363845, 0.0828663025983528700, -0.0636634925404189617)),
]


@pytest.mark.parametrize("xsta, xsun, xmon, day, tai, expected", TIDE_CASES)
def test_solid_tide_iers_test_cases(xsta, xsun, xmon, day, tai, expected):
    mjd = (day - date(1858, 11, 17)).days
    dx = iers.marea_solida_tvppk(xsta, xsun, xmon, mjd, 0.0, tai)
    assert np.allclose(dx, expected, atol=1e-9)


def test_sun_and_moon_directions():
    # declinaciones de los vectores del caso de prueba del IERS (13-4-2009) y el Sol al mediodía de Greenwich
    moment = datetime(2009, 4, 13, tzinfo=timezone.utc)
    gps = astro.gps_seconds(moment) + leap_seconds(moment)
    sun, moon = astro.sun_moon_ecef(gps)
    dec = lambda v: math.degrees(math.asin(v[2] / np.linalg.norm(v)))
    assert dec(sun) == pytest.approx(dec(np.array([137859926952.015, 54228127881.4350, 23509422341.6960])), abs=0.02)
    assert dec(moon) == pytest.approx(dec(np.array([-179996231.920342, -312468450.131567, -169288918.592160])),
                                      abs=0.1)
    assert np.linalg.norm(sun) == pytest.approx(1.0026 * astro.AU, rel=2e-3)
    noon = astro.gps_seconds(datetime(2026, 10, 20, 12, tzinfo=timezone.utc)) + 18
    sun, _ = astro.sun_moon_ecef(noon)
    # al mediodía UTC el Sol está cerca del meridiano de Greenwich (ecuación del tiempo del 20 de octubre: +15 min)
    assert math.degrees(math.atan2(sun[1], sun[0])) == pytest.approx(-15.0 / 4.0, abs=0.3)


def test_zenith_delays():
    zhd, zwd = modelos.zenith_delays(math.radians(20.0), 0.0)
    assert zhd == pytest.approx(2.30, abs=0.02)          # al nivel del mar, 1013 hPa
    assert 0.05 < zwd < 0.25
    zhd_high, _ = modelos.zenith_delays(math.radians(20.0), 2000.0)
    assert zhd_high == pytest.approx(zhd * modelos.standard_pressure_hpa(2000) / 1013.25, rel=1e-3)


def test_wind_up_is_continuous_and_follows_yaw():
    n = 50
    los = np.tile([0.0, 0.0, -1.0], (n, 1))              # satélite en el cenit, mirando hacia abajo
    yaw = np.linspace(0.0, 3.0 * math.pi, n)              # el satélite gira 1.5 vueltas
    ex = np.stack([np.cos(yaw), np.sin(yaw), np.zeros(n)], axis=1)
    ey = np.stack([np.sin(yaw), -np.cos(yaw), np.zeros(n)], axis=1)      # y = z × x con z hacia abajo
    north, east = np.array([0.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.0])
    wu = modelos.wind_up(los, ex, ey, north, east)
    assert np.all(np.abs(np.diff(wu)) < 0.1)              # sin saltos de un ciclo
    assert abs(wu[-1] - wu[0]) == pytest.approx(1.5, abs=1e-6)


def test_antex_patterns():
    ant = antena.receiver_antenna("TRM115000.00", "NONE")
    assert ant is not None
    g01, used = ant.pattern("G01")
    assert used == "G01"
    assert np.allclose(g01.pco, [0.00017, 0.00014, 0.06597])
    assert g01.pcv(0.0) == pytest.approx(0.0)
    assert g01.pcv(10.0) == pytest.approx(-0.00011, abs=1e-8)      # NOAZI a 10° de cenit: −0.11 mm
    assert g01.pcv(10.0, 0.0) == pytest.approx(-0.00004, abs=1e-8)  # malla: acimut 0°, cenit 10°
    sat = antena.satellite_antenna("G05", datetime(2026, 10, 20))
    assert sat.type == "BLOCK IIR-M"
    assert sat.patterns["G01"].pco[2] == pytest.approx(0.74263)
    pco, _, _ = modelos.if_offset(ant, "G", "1", "2", 2.5457, 1.5457)
    assert pco[2] == pytest.approx(2.5457 * 0.06597 - 1.5457 * ant.pattern("G02")[0].pco[2])
