import json
from datetime import datetime, timezone

import pytest

from tresvizo_ppk.core import antex, precise, rgna


def test_rgna_catalog_official_source():
    info = rgna.catalog_info()
    assert info["frame"] == "ITRF2008" and info["epoch"] == 2010.0
    assert info["attribution"].startswith("Fuente: INEGI")
    stations = rgna.rgna_catalog()
    assert len(stations) >= 30
    ugto = rgna.find_station("UGTO00MEX")
    assert ugto is not None and ugto.official and ugto.antenna_type == "TRM115000.00"
    assert abs(ugto.antenna_height_m - 0.071) < 1e-9
    # la coordenada del catálogo es la misma que el INEGI escribe en sus RINEX (APPROX POSITION XYZ)
    assert abs(ugto.position.x - -1164730.1759) < 2e-4 and abs(ugto.position.z - 2272414.2366) < 2e-4
    assert rgna.find_station("ZZZZ") is None
    near = rgna.nearest_stations(ugto.position, 3)
    assert near[0][0].id == "UGTO" and near[1][0].id == "UQRO" and 100_000 < near[1][1] < 101_000


def test_catalog_antennas_are_in_bundled_antex():
    for station in rgna.rgna_catalog():
        choice = antex.choose(station.antenna_type, station.radome)
        assert choice.model is not None, station.id


def test_download_guide_hours_and_days():
    start = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    end = datetime(2026, 10, 21, 1, 15, tzinfo=timezone.utc)
    guide = rgna.download_guide("ugto", start, end)
    assert [(r.date, r.first_hour, r.last_hour) for r in guide] == [("2026-10-20", 24, 24), ("2026-10-21", 1, 2)]
    assert guide[0].files_hint == ["UGTO293x"] and guide[1].files_hint == ["UGTO294a", "UGTO294b"]
    assert guide[0].first_label == "Vigésima cuarta" and guide[1].last_label == "Segunda"
    text = rgna.download_guide_text("UGTO", start, end)
    assert "www.inegi.org.mx/app/geo2/rgna" in text


def test_antex_choice_rules():
    exact = antex.choose("TRM115000.00", "NONE")
    assert exact.rtklib_name == "TRM115000.00 NONE" and abs(exact.model.frequencies["G01"][2] - 0.06597) < 1e-6
    spaced = antex.choose("TRM 55971.00", "SCIT")
    assert spaced.rtklib_name == "TRM55971.00 SCIT"
    fallback = antex.choose("LEIAR10", "LEIT")
    assert fallback.rtklib_name == "LEIAR10 NONE" and "no está" in fallback.note
    missing = antex.choose("HARXON HA-901A", "NONE", "Rover")
    assert missing.model is None and missing.rtklib_name == "" and "sin modelo" in missing.note


def test_precise_candidates_and_days():
    day = datetime(2026, 10, 20)
    assert precise.gps_week(day) == 2441
    names = [p.filename for p in precise._candidates(day)]
    assert names[0] == "ESA0OPSFIN_20262930000_01D_05M_ORB.SP3.gz"
    assert "IGS0OPSRAP_20262930000_01D_15M_ORB.SP3.gz" in names
    days = precise.session_days(datetime(2026, 10, 20, 0, 30, tzinfo=timezone.utc),
                                datetime(2026, 10, 20, 3, tzinfo=timezone.utc))
    assert [d.day for d in days] == [19, 20]
    assert [d.day for d in precise.session_days(datetime(2026, 10, 20, 10, tzinfo=timezone.utc),
                                                datetime(2026, 10, 20, 12, tzinfo=timezone.utc))] == [20]
