"""Posición de satélite con efemérides radiodifundidas y posicionamiento absoluto (SPP) de prueba.

Solo para las pruebas: lee los registros tal como quedaron escritos en el RINEX de navegación
(`rinex_check.check_nav`) y las pseudodistancias del RINEX de observación, de modo que un error de
escala, de signo o de tiempo en cualquier punto de la cadena (decodificación, escritura) se nota
como una posición lejos de la del propio receptor. Algoritmos de IS-GPS-200 (§20.3.3.4.3), Galileo
OS SIS ICD (§5.1.1) y BDS-SIS-ICD (§5.2.4.12, satélites GEO incluidos).
"""
from __future__ import annotations

import datetime as _dt
import math

C = 299792458.0
F_REL = -4.442807633e-10
CONSTANTS = {"G": (3.986005e14, 7.2921151467e-5), "J": (3.986005e14, 7.2921151467e-5),
             "E": (3.986004418e14, 7.2921151467e-5), "C": (3.986004418e14, 7.292115e-5)}
GPS_EPOCH = _dt.datetime(1980, 1, 6)


def calendar_to_gps(epoch: tuple, system: str) -> float:
    seconds = (_dt.datetime(*epoch) - GPS_EPOCH).total_seconds()
    return seconds + 14.0 if system == "C" else seconds      # BDT → GPST


def kepler(record: dict, t_gps: float) -> tuple[tuple[float, float, float], float]:
    """(x, y, z) en ECEF del instante de emisión y error de reloj del satélite (s, sin TGD)."""
    v = record["values"]
    system = record["sat"][0]
    mu, we = CONSTANTS[system]
    af0, af1, af2 = v[0], v[1], v[2]
    crs, dn, m0, cuc, ecc, cus, sqrt_a = v[4], v[5], v[6], v[7], v[8], v[9], v[10]
    toe, cic, omega0, cis, i0, crc, omega, omega_dot, idot = v[11], v[12], v[13], v[14], v[15], v[16], v[17], v[18], v[19]
    week = v[21]
    if system == "C":
        t_sys = t_gps - 14.0 - 1356 * 604800.0
    else:
        t_sys = t_gps
    tk = t_sys - (week * 604800.0 + toe)
    a = sqrt_a * sqrt_a
    n = math.sqrt(mu / a ** 3) + dn
    m = m0 + n * tk
    e_anom = m
    for _ in range(30):
        e_anom = m + ecc * math.sin(e_anom)
    nu = math.atan2(math.sqrt(1 - ecc * ecc) * math.sin(e_anom), math.cos(e_anom) - ecc)
    phi = nu + omega
    u = phi + cus * math.sin(2 * phi) + cuc * math.cos(2 * phi)
    r = a * (1 - ecc * math.cos(e_anom)) + crs * math.sin(2 * phi) + crc * math.cos(2 * phi)
    inc = i0 + idot * tk + cis * math.sin(2 * phi) + cic * math.cos(2 * phi)
    xp, yp = r * math.cos(u), r * math.sin(u)
    prn = int(record["sat"][1:])
    if system == "C" and (prn <= 5 or prn >= 59):
        big_omega = omega0 + omega_dot * tk - we * toe
        xg = xp * math.cos(big_omega) - yp * math.cos(inc) * math.sin(big_omega)
        yg = xp * math.sin(big_omega) + yp * math.cos(inc) * math.cos(big_omega)
        zg = yp * math.sin(inc)
        f = math.radians(-5.0)
        y1 = yg * math.cos(f) + zg * math.sin(f)
        z1 = -yg * math.sin(f) + zg * math.cos(f)
        rot = we * tk
        x = xg * math.cos(rot) + y1 * math.sin(rot)
        y = -xg * math.sin(rot) + y1 * math.cos(rot)
        z = z1
    else:
        big_omega = omega0 + (omega_dot - we) * tk - we * toe
        x = xp * math.cos(big_omega) - yp * math.cos(inc) * math.sin(big_omega)
        y = xp * math.sin(big_omega) + yp * math.cos(inc) * math.cos(big_omega)
        z = yp * math.sin(inc)
    toc = calendar_to_gps(record["epoch"], system)
    dt = t_gps - toc
    clock = af0 + af1 * dt + af2 * dt * dt + F_REL * ecc * sqrt_a * math.sin(e_anom)
    return (x, y, z), clock


def group_delay(record: dict) -> float:
    """Retardo de grupo para la señal de una frecuencia usada en el SPP: TGD (L1 C/A), TGD1 (B1I) y
    BGD E5b/E1 o E5a/E1 (E1) según la fuente de datos."""
    v = record["values"]
    system = record["sat"][0]
    if system == "E":
        return v[26] if int(v[20]) & 1 and v[26] else v[25]
    return v[25]


def solve(a_rows: list[list[float]], b: list[float]) -> list[float]:
    """Mínimos cuadrados por ecuaciones normales y eliminación de Gauss."""
    n = len(a_rows[0])
    ata = [[sum(r[i] * r[j] for r in a_rows) for j in range(n)] for i in range(n)]
    atb = [sum(r[i] * bi for r, bi in zip(a_rows, b)) for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda k: abs(ata[k][col]))
        ata[col], ata[pivot] = ata[pivot], ata[col]
        atb[col], atb[pivot] = atb[pivot], atb[col]
        for row in range(n):
            if row != col and ata[col][col]:
                factor = ata[row][col] / ata[col][col]
                for k in range(col, n):
                    ata[row][k] -= factor * ata[col][k]
                atb[row] -= factor * atb[col]
    return [atb[i] / ata[i][i] for i in range(n)]


def elevation(receiver: tuple, satellite: tuple) -> float:
    rx, ry, rz = receiver
    norm = math.sqrt(rx * rx + ry * ry + rz * rz)
    if norm < 1e6:
        return math.pi / 2
    dx, dy, dz = satellite[0] - rx, satellite[1] - ry, satellite[2] - rz
    rho = math.sqrt(dx * dx + dy * dy + dz * dz)
    return math.asin((dx * rx + dy * ry + dz * rz) / (rho * norm))


def spp(t_gps: float, ranges: dict[str, float], ephemerides: dict[str, dict]) -> tuple[tuple, float, int] | None:
    """`ranges`: satélite → pseudodistancia (m). Devuelve (x, y, z), RMS de residuos y satélites."""
    position = [0.0, 0.0, 0.0]
    systems = sorted({s[0] for s in ranges if s in ephemerides})
    clocks = {s: 0.0 for s in systems}
    residual_rms = None
    used = 0
    for iteration in range(10):
        rows, b = [], []
        for sat, pr in ranges.items():
            record = ephemerides.get(sat)
            if record is None:
                continue
            tau = pr / C
            sat_pos = clock = None
            for _ in range(3):
                sat_pos, clock = kepler(record, t_gps - tau)
                sat_pos = (sat_pos[0] * math.cos(CONSTANTS[sat[0]][1] * tau) + sat_pos[1] * math.sin(CONSTANTS[sat[0]][1] * tau),
                           -sat_pos[0] * math.sin(CONSTANTS[sat[0]][1] * tau) + sat_pos[1] * math.cos(CONSTANTS[sat[0]][1] * tau),
                           sat_pos[2])
                rho = math.dist(sat_pos, position)
                tau = rho / C
            el = elevation(position, sat_pos)
            if iteration >= 3 and el < math.radians(10):
                continue
            rho = math.dist(sat_pos, position)
            troposphere = 2.3 / math.sin(max(el, math.radians(5))) if iteration >= 3 else 0.0
            corrected_clock = clock - group_delay(record)
            predicted = rho + clocks[sat[0]] - C * corrected_clock + troposphere
            row = [(position[k] - sat_pos[k]) / rho for k in range(3)] + [1.0 if sat[0] == s else 0.0 for s in systems]
            rows.append(row)
            b.append(pr - predicted)
        if len(rows) < 3 + len(systems) + 1:
            return None
        dx = solve(rows, b)
        position = [position[k] + dx[k] for k in range(3)]
        for k, s in enumerate(systems):
            clocks[s] += dx[3 + k]
        residuals = [bi - sum(ri * di for ri, di in zip(r, dx)) for r, bi in zip(rows, b)]
        residual_rms = math.sqrt(sum(x * x for x in residuals) / len(residuals))
        used = len(rows)
    return tuple(position), residual_rms, used
