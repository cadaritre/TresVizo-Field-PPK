import math
from datetime import datetime, timezone

import numpy as np

from tresvizo_ppk.core import geodesy as g


def test_ugto_official_matches_inegi_rinex_header():
    # UGTO del catálogo del INEGI (21 00 09.75456 N, 101 16 17.99246 W, 2062.282 m) contra el APPROX POSITION XYZ
    # que escribe el propio INEGI en sus RINEX: -1164730.1759 -5843944.6861 2272414.2366
    lat = g.dms_to_degrees("21 00 09.75456")
    lon = -g.dms_to_degrees("101 16 17.99246")
    x, y, z = g.geodetic_to_ecef(lat, lon, 2062.282)
    assert abs(x - -1164730.1759) < 2e-4 and abs(y - -5843944.6861) < 2e-4 and abs(z - 2272414.2366) < 2e-4


def test_roundtrip_ecef_geodetic():
    for lat, lon, h in [(21.0, -101.3, 2062.3), (-33.4, 151.2, 50.0), (89.9, 10.0, 0.0), (0.0, 0.0, -100.0)]:
        x, y, z = g.geodetic_to_ecef(lat, lon, h)
        la, lo, hh = g.ecef_to_geodetic(x, y, z)
        assert abs(la - lat) < 1e-10 and abs(lo - lon) < 1e-10 and abs(hh - h) < 1e-5


def test_enu_azimuth_zenith():
    origin = g.geodetic_to_ecef(20.0, -100.0, 1000.0)
    north = np.array(g.geodetic_to_ecef(20.001, -100.0, 1000.0)) - np.array(origin)
    e, n, u = g.ecef_delta_to_enu(origin, north)
    assert abs(e) < 1e-6 and 110 < n < 111 and abs(u) < 0.01
    az, zen = g.azimuth_zenith(origin, north)
    assert abs(az) < 1e-4 or abs(az - 360) < 1e-4
    assert abs(zen - 90.0) < 0.01
    east = g.enu_to_ecef_delta(origin, (100.0, 0.0, 0.0))
    az, _ = g.azimuth_zenith(origin, east)
    assert abs(az - 90.0) < 1e-6


def test_covariance_rotation_preserves_trace():
    origin = g.geodetic_to_ecef(19.3, -98.2, 2300.0)
    cov = np.diag([1e-4, 4e-4, 9e-4])
    enu = g.covariance_ecef_to_enu(origin, cov)
    assert math.isclose(np.trace(enu), np.trace(cov), rel_tol=1e-12)
    back = g.covariance_enu_to_ecef(origin, enu)
    assert np.allclose(back, cov)


def test_leap_seconds_and_gps_time():
    utc = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    assert g.leap_seconds(utc) == 18
    assert g.utc_to_gps(utc) == datetime(2026, 10, 20, 14, 0, 18, tzinfo=timezone.utc)
    assert g.gps_to_utc(g.utc_to_gps(utc)) == utc
    assert g.leap_seconds(datetime(2016, 12, 31, tzinfo=timezone.utc)) == 17
