"""El promedio de cada punto y su bajada al suelo, con una trayectoria sintética (sin RTKLIB)."""
import math
from datetime import datetime, timedelta, timezone

import numpy as np

from tresvizo_ppk.core import points, rtklib
from tresvizo_ppk.core.geodesy import ecef_delta_to_enu, ecef_to_geodetic, geodetic_to_ecef
from tresvizo_ppk.model import Ecef, FieldPoint, ObservationSet, Solution, Station

ARP = np.array(geodetic_to_ecef(19.3, -98.2, 2300.0))
BASE = Station(id="BASE", name="Base", position=Ecef(*geodetic_to_ecef(19.31, -98.2, 2290.0)))
T0 = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)


def trajectory(qualities):
    epochs, times = [], []
    for k, q in enumerate(qualities):
        jitter = np.array([0.002, -0.001, 0.003]) * (1 if k % 2 else -1)
        epochs.append(rtklib.PosEpoch(time=datetime(2026, 1, 1), xyz=ARP + jitter, q=q, ns=20,
                                      cov=np.eye(3) * 1e-6, age=1.0, ratio=9.0))
        times.append(T0 + timedelta(seconds=k))
    return epochs, times


def point(start_s, end_s, total=1.916, field=None):
    return FieldPoint(name="P", code="", start=T0 + timedelta(seconds=start_s, milliseconds=100),
                      end=T0 + timedelta(seconds=end_s, milliseconds=-100), antenna_height_m=1.8,
                      field_position=field, total_height_m=total)


def test_ground_is_antenna_minus_vertical_height():
    epochs, times = trajectory([1] * 30)
    lat, lon, h = ecef_to_geodetic(*ARP)
    field = Ecef(*geodetic_to_ecef(lat, lon, h - 1.916 + 0.010))     # campo: 1 cm más alto
    p = points._process_point(point(5, 14, field=field), epochs, times, np.array([BASE.position.x, BASE.position.y,
                              BASE.position.z]), BASE, ObservationSet(obs=[], interval_s=1.0))
    assert p.solution == Solution.FIXED and p.epochs_fixed == 10 and p.epochs_total == 10
    glat, glon, gh = ecef_to_geodetic(p.position.x, p.position.y, p.position.z)
    assert abs(glat - lat) < 1e-9 and abs(glon - lon) < 1e-9 and abs(gh - (h - 1.916)) < 1e-4
    assert abs(p.delta_from_field_enu.u + 0.010) < 1e-4 and abs(p.delta_from_field_enu.e) < 1e-4
    assert p.height_used_m == 1.916 and 1100 < p.distance_to_base_m < 1120
    assert p.sigma_enu.e >= 0.008                       # σ mínima de una fija
    assert p.message == ""


def test_float_epochs_only_when_no_fixed():
    epochs, times = trajectory([2] * 10 + [1] * 5 + [2] * 15)
    p = points._process_point(point(0, 29), epochs, times, ARP, BASE, ObservationSet(obs=[], interval_s=1.0))
    assert p.solution == Solution.FIXED and p.epochs_fixed == 5 and p.epochs_float == 25
    q = points._process_point(point(16, 25), epochs, times, ARP, BASE, ObservationSet(obs=[], interval_s=1.0))
    assert q.solution == Solution.FLOAT and "FLOTANTES" in q.message and q.sigma_enu.u >= 0.20


def test_height_from_pole_and_receiver_case():
    epochs, times = trajectory([1] * 10)
    fp = point(0, 9, total=None)
    p = points._process_point(fp, epochs, times, ARP, BASE, ObservationSet(obs=[], interval_s=1.0, case_offset_m=0.116))
    assert abs(p.height_used_m - 1.916) < 1e-12
    fp.antenna_height_m = float("nan")
    p = points._process_point(fp, epochs, times, ARP, BASE, ObservationSet(obs=[], interval_s=1.0))
    assert p.position is None and p.antenna_position is not None and "altura" in p.message


def test_outside_trajectory_and_partial_coverage():
    epochs, times = trajectory([1] * 10)
    p = points._process_point(point(100, 110), epochs, times, ARP, BASE, ObservationSet(obs=[], interval_s=1.0))
    assert p.solution == Solution.NONE and p.position is None and "no cae" in p.message
    q = points._process_point(point(5, 25), epochs, times, ARP, BASE, ObservationSet(obs=[], interval_s=1.0))
    assert q.epochs_total == 5 and "solo tiene 5 de 21" in q.message


def test_ratio_and_pdop_of_the_occupation_for_the_tolerances():
    epochs, times = trajectory([2] * 3 + [1] * 10)
    for k, e in enumerate(epochs):
        e.ratio = 2.0 + k
        e.time = datetime(2026, 1, 1, 0, 0, k)
    pdops = {e.time: 1.5 + 0.1 * k for k, e in enumerate(epochs)}
    p = points._process_point(point(0, 12), epochs, times, ARP, BASE, ObservationSet(obs=[], interval_s=1.0), pdops)
    # mediana del cociente de las fijas (5 … 14) y la PDOP mayor de las épocas promediadas (solo las fijas)
    assert p.ratio == 9.5 and abs(p.pdop_max - 2.7) < 1e-12
    q = points._process_point(point(0, 12), epochs, times, ARP, BASE, ObservationSet(obs=[], interval_s=1.0))
    assert q.pdop_max is None
