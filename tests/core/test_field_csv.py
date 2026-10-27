import math
from datetime import datetime, timezone

import pytest

from tresvizo_ppk.core import field_csv
from tresvizo_ppk.core.geodesy import ecef_to_geodetic
from tresvizo_ppk.errors import UserError

EN = ("Name,Code,Code description,Easting,Northing,Elevation,Description,Longitude,Latitude,Ellipsoidal height,"
      "Origin,Tilt angle,Easting RMS,Northing RMS,Elevation RMS,Lateral RMS,Antenna height,Antenna height units,"
      "Solution status,Correction type,Averaging start,Averaging end,Samples,Device type,Antenna offset,Antenna PCO,"
      "Frame,Total antenna height")


def write(tmp_path, text, encoding="utf-8-sig", name="p.csv"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def test_english_all_columns(tmp_path):
    rows = [EN,
            "P1,ESQ,Esquina,,,,,-101.27166666,21.00270960,2060.400,Global,,,,,,1.800,m,FIX,RTK,"
            "2026-10-15 12:44:19.8 UTC-06:00,2026-10-15 12:45:19.8 UTC-06:00,60,MeridianV,0.1160,0.0000,ITRF2008,1.916",
            '"=P2",,,,,,,-101.2,21.0,2000.0,Global,,,,,,1.500,m,FLOAT,RTK,,2026-10-15 13:00:00.0 UTC-06:00,1,'
            'MeridianVI,,,,',
            "P3,,,,,,,,,,Manual,,,,,,,,,,,,,,,,,",
            ]
    result = field_csv.read_field_csv_detailed(write(tmp_path, "\r\n".join(rows) + "\r\n"))
    assert [p.name for p in result.points] == ["P1", "=P2"]
    p1 = result.points[0]
    assert p1.start == datetime(2026, 10, 15, 18, 44, 19, 800000, tzinfo=timezone.utc)
    assert p1.end == datetime(2026, 10, 15, 18, 45, 19, 800000, tzinfo=timezone.utc)
    assert p1.code == "ESQ" and p1.field_solution == "FIJO"
    assert p1.antenna_height_m == 1.8 and p1.antenna_offset_m == 0.116 and p1.total_height_m == 1.916
    lat, lon, h = ecef_to_geodetic(p1.field_position.x, p1.field_position.y, p1.field_position.z)
    assert abs(lat - 21.0027096) < 1e-9 and abs(lon + 101.27166666) < 1e-9 and abs(h - 2060.4) < 1e-6
    assert p1.field_position.frame == "ITRF2008" and p1.row["Name"] == "P1"
    p2 = result.points[1]
    # instantánea: inicio = fin; sin desplazamiento en el CSV, el del modelo (MeridianVI: 0.152)
    assert p2.start == p2.end and p2.field_solution == "FLOTANTE"
    assert abs(p2.total_height_m - (1.5 + 0.152)) < 1e-12
    assert result.skipped == ["P3: sin medición (Manual)."]


def test_spanish_semicolon_decimal_comma(tmp_path):
    header = ("Nombre;Código;Longitud;Latitud;Altura elipsoidal;Origen;Altura de antena;Estado de la solución;"
              "Inicio del promedio;Fin del promedio;Receptor;Desplazamiento de antena;Altura total de la antena")
    row = ("P9;BAN;-99,50000000;19,40000000;2240,125;Global;2,000;FIX;2026-10-15 07:00:00,0 UTC-05:00;"
           "2026-10-15 07:00:30,0 UTC-05:00;MeridianV;0,1160;2,116")
    pts = field_csv.read_field_csv(write(tmp_path, header + "\n" + row + "\n"))
    assert len(pts) == 1 and pts[0].total_height_m == 2.116 and pts[0].antenna_height_m == 2.0
    assert pts[0].start == datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    lat, lon, h = ecef_to_geodetic(pts[0].field_position.x, pts[0].field_position.y, pts[0].field_position.z)
    assert abs(h - 2240.125) < 1e-6


def test_windows_1252_and_tab(tmp_path):
    header = "Name\tCode\tAntenna height\tAveraging start\tAveraging end"
    row = "Árbol\tARB\t1.8\t2026-10-15 12:00:00.0 UTC\t2026-10-15 12:00:10.0 UTC"
    pts = field_csv.read_field_csv(write(tmp_path, header + "\n" + row + "\n", encoding="cp1252"))
    assert pts[0].name == "Árbol" and pts[0].field_position is None
    assert pts[0].total_height_m == 1.8           # sin modelo ni desplazamiento: solo el jalón


def test_not_a_field_csv(tmp_path):
    with pytest.raises(UserError, match="Todas las columnas"):
        field_csv.read_field_csv(write(tmp_path, "P,N,E,Z,D\n1,2,3,4,x\n"))
    with pytest.raises(UserError, match="no tiene puntos medidos"):
        field_csv.read_field_csv(write(tmp_path, EN + "\nP3,,,,,,,,,,Manual,,,,,,,,,,,,,,,,,\n"))


def test_parse_time_variants():
    assert field_csv.parse_time("2026-10-15 12:44:19.8 UTC+01:30") == datetime(2026, 10, 15, 11, 14, 19, 800000,
                                                                                 tzinfo=timezone.utc)
    assert field_csv.parse_time("2026-10-15T12:44:19 UTC") == datetime(2026, 10, 15, 12, 44, 19, tzinfo=timezone.utc)
    assert field_csv.parse_time("no") is None
