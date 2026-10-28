"""El control de calidad en los informes (texto extraído con pypdf): el resumen, el estado de cada punto con su razón,
las ocupaciones repetidas y la sección «Control» con los residuos."""
from __future__ import annotations

import re
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import numpy as np
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).parent))
import sinteticos as s  # noqa: E402

from tresvizo_ppk import calidad  # noqa: E402
from tresvizo_ppk.core.geodesy import enu_to_ecef_delta  # noqa: E402
from tresvizo_ppk.crs import api as crs  # noqa: E402
from tresvizo_ppk.model import ControlCoordinate, Ecef, Enu  # noqa: E402
from tresvizo_ppk.report import api  # noqa: E402


def _text(pdf: Path) -> str:
    reader = PdfReader(str(pdf))
    return re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in reader.pages))


def _moved(p: Ecef, e: float, n: float, u: float) -> Ecef:
    xyz = np.array([p.x, p.y, p.z]) + np.array(enu_to_ecef_delta((p.x, p.y, p.z), (e, n, u)))
    return Ecef(*(float(v) for v in xyz), p.frame, p.epoch)


def test_points_report_quality_repeats_and_control(tmp_path):
    result = s.points_result(tmp_path)
    p2 = result.points[1]
    # P02 se ocupó otra vez diez minutos después, 6 mm al este y 9 mm más alto
    again = replace(p2, point=replace(p2.point, start=p2.point.start + timedelta(minutes=10),
                                      end=p2.point.end + timedelta(minutes=10)),
                    position=_moved(p2.position, 0.006, 0.0, 0.009))
    result.points.insert(2, again)
    project = s.points_project(tmp_path)
    project.tolerances = replace(calidad.tolerances_for_order("5 centímetros", "points"), max_sigma_v_m=0.020)
    c = crs.transform(result.points[3].position, calidad.fixed_choice(result.points[0].position,
                                                                      project.coordinates))
    # P03 contra su control: el control 30 mm al norte de lo medido → ΔN −0.030 (dentro de 0.050)
    project.control_points = [ControlCoordinate("P03", c.northing_m + 0.030, c.easting_m, None),
                              ControlCoordinate("C-99", 1.0, 2.0, 3.0)]
    project.control_source = "control.csv"
    out = api.points_report(result, project, s.user(), tmp_path / "informe.pdf")
    t = _text(out)
    assert "Control de calidad" in t and "orden de 5 centímetros (NT EEP, tabla 1)" in t
    assert "12 (13 ocupaciones)" in t
    # el punto flotante (σV 0.11) falla y el que no se procesó también; los fijos pasan
    assert "P08: falla — σH 0.078 > 0.028; σV 0.110 > 0.020" in t
    assert "P12: falla — sin posición" in t
    assert re.search(r"pasan · \d+ a revisar · 2 fallan", t)
    assert "Ocupaciones repetidas" in t and "media ponderada" in t and "promedio de 2 ocupaciones" in t
    assert "Mayor diferencia 0.006 0.009" in t
    # la sección Control con el residuo calculado a mano y el control sin medición
    assert "Control Coordenadas de control de «control.csv»" in t
    assert "-0.030" in t.replace("−", "-") and "C-99" in t


def test_points_report_chosen_occupation(tmp_path):
    result = s.points_result(tmp_path)
    p2 = result.points[1]
    again = replace(p2, point=replace(p2.point, start=p2.point.start + timedelta(minutes=10),
                                      end=p2.point.end + timedelta(minutes=10)),
                    position=_moved(p2.position, 0.080, 0.0, 0.0))
    result.points.insert(2, again)
    project = s.points_project(tmp_path)
    project.occupation_choices = {"P02": 1}
    t = _text(api.points_report(result, project, s.user(), tmp_path / "informe.pdf"))
    assert "ocupación 1 de 2 (elegida)" in t
    # 80 mm entre las dos: más que 0.050, menos que el doble → revisar
    assert "entre ocupaciones ΔH 0.080 > 0.050" in t


def test_static_report_quality_and_control(tmp_path):
    result = s.static_result(tmp_path)
    result.baselines[1].pdop_max = 7.5
    project = s.static_project(tmp_path)
    project.tolerances = calidad.tolerances_for_order("2 centímetros", "static")
    c = crs.transform(result.position, calidad.fixed_choice(result.position, project.coordinates))
    project.control_points = [ControlCoordinate("VERT-01", c.northing_m - 0.012, c.easting_m + 0.016, None)]
    t = _text(api.static_report(result, project, s.user(), tmp_path / "informe.pdf"))
    assert "Control de calidad" in t and "Líneas base" in t
    assert "PDOP 7.5 > 6.0" in t
    # medido − control: ΔN +0.012, ΔE −0.016 → ΔH 0.020 ≤ 0.020
    assert "+0.012" in t and ("-0.016" in t.replace("−", "-")) and "0.020" in t
    assert "Estado" in t and ("Revisar" in t or "Falla" in t)
