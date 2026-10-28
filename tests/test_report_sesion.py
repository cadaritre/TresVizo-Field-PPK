"""El informe dice qué se recortó en el editor de sesión y qué satélites se dejaron fuera (estático y PPK)."""
from __future__ import annotations

import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).parent))
import sinteticos as s  # noqa: E402

from tresvizo_ppk.model import TimeWindow  # noqa: E402
from tresvizo_ppk.report import api  # noqa: E402

WINDOW = TimeWindow(datetime(2026, 10, 24, 15, 10, tzinfo=timezone.utc),
                    datetime(2026, 10, 24, 16, 40, tzinfo=timezone.utc))


def _text(pdf: Path) -> str:
    return re.sub(r"\s+", " ", "\n".join(p.extract_text() or "" for p in PdfReader(str(pdf)).pages))


def test_static_report_says_window_and_excluded(tmp_path):
    project = s.static_project(tmp_path)
    project.options = replace(project.options, time_window=WINDOW, excluded_sats=["R", "G13", "E24"])
    t = _text(api.static_report(s.static_result(tmp_path), project, s.user(None), tmp_path / "e.pdf"))
    assert "Ventana de tiempo 2026-10-24 15:10:00–16:40:00 UTC (recortada en el editor de sesión)" in t
    assert "Satélites fuera del cálculo GLONASS completo; G13, E24" in t


def test_points_report_says_whole_session_when_not_edited(tmp_path):
    t = _text(api.points_report(s.points_result(tmp_path), s.points_project(tmp_path), s.user(None),
                                tmp_path / "p.pdf"))
    assert "Ventana de tiempo toda la sesión" in t and "Satélites fuera del cálculo ninguno" in t
