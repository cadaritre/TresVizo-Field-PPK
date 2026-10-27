"""El informe de línea base dice lo que hizo el procesador de líneas largas: fija con sus pruebas (razón, tasa de
éxito, combinaciones fijadas) o flotante con el porqué, sin repetir el aviso del motor."""
from __future__ import annotations

import re
import sys
from dataclasses import replace
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).parent))
import sinteticos as s  # noqa: E402

from tresvizo_ppk.model import AmbiguityResolution, Ecef, Enu, Solution  # noqa: E402
from tresvizo_ppk.report import api  # noqa: E402


def _text(pdf: Path) -> str:
    reader = PdfReader(str(pdf))
    return re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in reader.pages))


def _own(b, fixed: bool, reason: str = ""):
    p = b.rover_position
    amb = AmbiguityResolution(
        fixed=fixed, reason=reason, double_differences=25, widelane_fixed=24, narrowlane_candidates=24,
        narrowlane_fixed=21 if fixed else 0, ratio=4.6 if fixed else None, min_ratio=3.0,
        success_rate=0.99962 if fixed else None, min_success_rate=0.999, min_session_h=1.0, sigma0=0.41,
        rms_phase_m=0.0102, troposphere_interval_h=1.0, gradient_interval_h=6.0,
        signal_groups=["GPS L1/L2: G01, G07", "Galileo E1/E5a: E07, E13"],
        excluded=["GLONASS no entra en las líneas largas."],
        float_position=Ecef(p.x + 0.01, p.y - 0.02, p.z + 0.005, p.frame, p.epoch),
        float_sigma_enu=Enu(0.014, 0.011, 0.02), observations=40765, rejected=25)
    warnings = [] if fixed else [f"La línea base {b.base.id} ({b.length_m / 1000:.1f} km) quedó FLOTANTE: {reason}."]
    return replace(b, engine="TresVizo", ambiguity=amb, solution=Solution.FIXED if fixed else Solution.FLOAT,
                   ratio=4.6 if fixed else None, pos_file=None, warnings=warnings)


def test_fixed_long_line(tmp_path):
    result = s.static_result(tmp_path)
    result.baselines[1] = _own(result.baselines[1], True)
    t = _text(api.static_report(result, s.static_project(tmp_path), s.user(), tmp_path / "fija.pdf"))
    for needle in ("Resultado: Aprobado", "Líneas largas fijas", "FIJA con el procesador de líneas largas",
                   "21 combinaciones enteras de 24 candidatas", "razón 4.6 (mínimo 3)", "tasa de éxito 99.96 %",
                   "Resolución de ambigüedades (procesador de líneas largas)", "Ancho de carril fijo 24 de 25",
                   "Fija − flotante", "FIJA (todas las líneas base)", "comprobación libre de ionosfera de RTKLIB",
                   "obra derivada del software de las Convenciones del IERS 2010"):
        assert needle in t, needle
    assert "Líneas largas con solución flotante" not in t


def test_float_long_line_says_why(tmp_path):
    result = s.static_result(tmp_path)
    why = "la sesión de 40 min es corta para fijar a 99 km: hacen falta 60 min o más"
    result.baselines[1] = _own(result.baselines[1], False, why)
    result.warnings = list(result.baselines[1].warnings)
    t = _text(api.static_report(result, s.static_project(tmp_path), s.user(), tmp_path / "flotante.pdf"))
    assert "Resultado: Con avisos" in t
    assert "FLOTANTE. El procesador de líneas largas no la fijó porque " + why in t
    assert "observa al menos 60 min" in t
    assert "quedó FLOTANTE" not in t                       # el aviso del motor no se repite
    assert "MIXTA: PRB1 FIJA, PRB2 FLOTANTE" in t
