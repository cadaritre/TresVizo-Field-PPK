"""Escalas de tiempo GNSS.

Toda la herramienta trabaja en **tiempo GPS** contado desde la época GPS (1980-01-06 00:00:00):
las épocas de observación como milisegundos enteros (exactos) y los tiempos de efemérides como
segundos en coma flotante. Galileo (GST) y QZSS (QZSST) comparten la escala GPS a efectos de RINEX;
BeiDou (BDT) va 14 s por detrás de GPS y su semana 0 empieza en la semana GPS 1356; GLONASS se
expresa en UTC (y en hora de Moscú, UTC + 3 h, dentro de sus mensajes).
"""
from __future__ import annotations

import datetime as _dt

SECONDS_PER_WEEK = 604800
MS_PER_WEEK = SECONDS_PER_WEEK * 1000
GPS_EPOCH = _dt.datetime(1980, 1, 6)
BDT_WEEK_OFFSET = 1356          # semana GPS en la que empieza la semana BDT 0
BDT_MINUS_GPS_S = -14.0         # BDT = GPST − 14 s
MOSCOW_MINUS_UTC_S = 10800      # hora de Moscú (GLONASS) = UTC + 3 h

# GPS − UTC (segundos intercalares) desde la fecha UTC indicada. Basta para datos de 1999 en adelante.
_LEAP_TABLE = (
    (_dt.datetime(2017, 1, 1), 18),
    (_dt.datetime(2015, 7, 1), 17),
    (_dt.datetime(2012, 7, 1), 16),
    (_dt.datetime(2009, 1, 1), 15),
    (_dt.datetime(2006, 1, 1), 14),
    (_dt.datetime(1999, 1, 1), 13),
)


def gps_seconds(week: int, tow: float) -> float:
    return week * SECONDS_PER_WEEK + tow


def gps_ms(week: int, tow_ms: int) -> int:
    return week * MS_PER_WEEK + tow_ms


def week_tow(seconds: float) -> tuple[int, float]:
    week = int(seconds // SECONDS_PER_WEEK)
    return week, seconds - week * SECONDS_PER_WEEK


def to_datetime(seconds: float) -> _dt.datetime:
    """Fecha y hora de calendario en la misma escala (sin segundos intercalares)."""
    return GPS_EPOCH + _dt.timedelta(seconds=seconds)


def from_datetime(value: _dt.datetime) -> float:
    return (value - GPS_EPOCH).total_seconds()


def ms_to_datetime(ms: int) -> _dt.datetime:
    return GPS_EPOCH + _dt.timedelta(milliseconds=ms)


def leap_seconds_utc(utc: _dt.datetime) -> int:
    for start, leap in _LEAP_TABLE:
        if utc >= start:
            return leap
    return 13


def leap_seconds_gps(gps_s: float) -> int:
    """GPS − UTC en un instante dado en tiempo GPS."""
    approx = to_datetime(gps_s)
    leap = leap_seconds_utc(approx - _dt.timedelta(seconds=18))
    return leap_seconds_utc(approx - _dt.timedelta(seconds=leap))


def gps_to_utc(gps_s: float, leap: int | None = None) -> float:
    """Devuelve el instante UTC expresado como segundos «de calendario» desde la época GPS."""
    return gps_s - (leap if leap is not None else leap_seconds_gps(gps_s))


def utc_to_gps(utc_s: float, leap: int | None = None) -> float:
    if leap is None:
        leap = leap_seconds_utc(to_datetime(utc_s))
    return utc_s + leap


def gps_to_bdt(gps_s: float) -> tuple[int, float]:
    """Semana BDT y segundos de la semana BDT."""
    bdt = gps_s + BDT_MINUS_GPS_S - BDT_WEEK_OFFSET * SECONDS_PER_WEEK
    week = int(bdt // SECONDS_PER_WEEK)
    return week, bdt - week * SECONDS_PER_WEEK


def bdt_to_gps(bdt_week: int, bdt_sow: float) -> float:
    return (bdt_week + BDT_WEEK_OFFSET) * SECONDS_PER_WEEK + bdt_sow - BDT_MINUS_GPS_S


def nearest_week(tow: float, reference_s: float) -> float:
    """Tiempo GPS completo para un segundo de la semana, en la semana que lo deja más cerca de la
    referencia (a menos de media semana)."""
    week, ref_tow = week_tow(reference_s)
    if tow < ref_tow - SECONDS_PER_WEEK / 2:
        week += 1
    elif tow > ref_tow + SECONDS_PER_WEEK / 2:
        week -= 1
    return gps_seconds(week, tow)


def nearest_modulo(value: float, modulo: float, reference: float) -> float:
    """El valor `value + k·modulo` más cercano a `reference`."""
    k = round((reference - value) / modulo)
    return value + k * modulo


def calendar(seconds: float) -> tuple[int, int, int, int, int, float]:
    """(año, mes, día, hora, minuto, segundos) redondeado al décimo de microsegundo."""
    whole = int(seconds // 1)
    fraction = round(seconds - whole, 7)
    if fraction >= 1.0:
        whole += 1
        fraction -= 1.0
    moment = to_datetime(whole)
    return moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second + fraction


def calendar_ms(ms: int) -> tuple[int, int, int, int, int, float]:
    moment = ms_to_datetime(ms - ms % 1000)
    return moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second + (ms % 1000) / 1000.0
