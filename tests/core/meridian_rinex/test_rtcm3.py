"""RTCM3: tramas reales comparadas con la decodificación independiente de pyrtcm, y MSM4/5 construidas.

`fixtures/pyrtcm/` trae dos registros reales de la batería de pruebas de pyrtcm (BSD-3-Clause, ver
LICENSE): un receptor con MSM7 de las cinco constelaciones y una estación IGS (USCL00CHL, Septentrio
PolaRx5) con MSM6/MSM7, 1006, 1033 y efemérides 1019/1020/1042/1045/1046. `expected.json` son los
campos que pyrtcm decodifica de esas mismas tramas (su tests/test_stream.py).
"""
import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from builders import msm_frame  # noqa: E402
from tresvizo_ppk.core.meridian_rinex import gnsstime, rtcm3  # noqa: E402
from tresvizo_ppk.core.meridian_rinex.framing import split_frames  # noqa: E402
from tresvizo_ppk.core.meridian_rinex.signals import C_LIGHT, frequency  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "pyrtcm")
PI = rtcm3.SC2RAD
RANGE_MS = C_LIGHT * 1e-3


def load(log):
    with open(os.path.join(FIXTURES, log), "rb") as handle:
        data = handle.read()
    with open(os.path.join(FIXTURES, "expected.json")) as handle:
        expected = json.load(handle)["logs"][log]
    frames, _ = split_frames(data)
    payloads = [data[f.start + 3:f.end - 3] for f in frames if f.kind == "rtcm"]
    wanted = {m["type"] for m in expected}
    payloads = [p for p in payloads if rtcm3.message_type(p) in wanted]
    assert [rtcm3.message_type(p) for p in payloads] == [m["type"] for m in expected]
    return list(zip(payloads, [m["fields"] for m in expected]))


def sat_name(system, prn):
    if system == "J":
        return f"J{prn - 192:02d}"
    if system == "S":
        return f"S{prn - 100:02d}"
    return f"{system}{prn:02d}"


class PyrtcmMsmTest(unittest.TestCase):
    invalid_rates = 0

    def check_msm(self, payload, f, reference_s, fcn_map):
        kind = f["DF002"]
        system = rtcm3.MSM_SYSTEMS[kind // 10]
        msm = kind % 10
        message = rtcm3.decode_msm(payload, reference_s, 18, fcn_map)
        self.assertIsNotNone(message)
        nsat = f["NSat"]
        prns = [f[f"PRN_{i:02d}"] for i in range(1, nsat + 1)]
        checked = 0
        for j in range(1, f["NCell"] + 1):
            prn = f[f"CELLPRN_{j:02d}"]
            code = str(f[f"CELLSIG_{j:02d}"])
            i = prns.index(prn) + 1
            sat = sat_name(system, prn)
            fcn = None
            if system == "R":
                fcn = f[f"DF419_{i:02d}"] - 7 if f"DF419_{i:02d}" in f else fcn_map.get(sat)
            rough = f[f"DF397_{i:02d}"] + f[f"DF398_{i:02d}"]
            fine_pr = f.get(f"DF405_{j:02d}", f.get(f"DF400_{j:02d}"))
            fine_cp = f.get(f"DF406_{j:02d}", f.get(f"DF401_{j:02d}"))
            signal = message.epoch.satellites[sat][code]
            self.assertAlmostEqual(signal.pseudorange, (rough + fine_pr) * RANGE_MS, places=4)
            f_hz = frequency(system, code, fcn)
            self.assertAlmostEqual(signal.phase, (rough + fine_cp) * RANGE_MS * f_hz / C_LIGHT, places=4)
            cnr = f.get(f"DF408_{j:02d}", f.get(f"DF403_{j:02d}"))
            self.assertAlmostEqual(signal.cn0, cnr)
            self.assertEqual(signal.lock_indicator, f.get(f"DF407_{j:02d}", f.get(f"DF402_{j:02d}")))
            self.assertEqual(signal.half_cycle, bool(f[f"DF420_{j:02d}"]))
            if msm in (5, 7):
                if f[f"DF399_{i:02d}"] == -8192 or abs(f[f"DF404_{j:02d}"] + 1.6384) < 1e-9:
                    self.assertIsNone(signal.doppler)      # marcador de «no válido» (DF399/DF404)
                    self.invalid_rates += 1
                else:
                    rate = f[f"DF399_{i:02d}"] + f[f"DF404_{j:02d}"]
                    self.assertAlmostEqual(signal.doppler, -rate * f_hz / C_LIGHT, places=3)
            else:
                self.assertIsNone(signal.doppler)
            checked += 1
        self.assertEqual(checked, f["NCell"])
        return message

    def test_receiver_msm7_all_constellations(self):
        reference = gnsstime.gps_seconds(2100, 204137.001)
        times = set()
        for payload, f in load("pygpsdata-RTCM3.log"):
            if f["DF002"] in (1077, 1087, 1097, 1117, 1127):
                message = self.check_msm(payload, f, reference, {})
                if f["DF002"] != 1117:      # el 1117 de este registro es de otra época
                    times.add(message.time_ms)
        self.assertEqual(self.invalid_rates, 9)      # el 1117 real trae 9 DF404 no válidos
        # GPS 204137.001 s; GLONASS martes 11:41:59.001 Moscú; BDS 204123.001 BDT: la misma época GPS.
        self.assertEqual(times, {2100 * gnsstime.MS_PER_WEEK + 204137001})

    def test_igs_station_msm6_msm7(self):
        reference = gnsstime.gps_seconds(2305, 318945)
        pairs = load("pygpsdata-NTRIP-USCL00CHL0.log")
        fcn_map = {}
        for payload, f in pairs:
            if f["DF002"] == 1087:
                for i in range(1, f["NSat"] + 1):
                    fcn_map[f"R{f[f'PRN_{i:02d}']:02d}"] = f[f"DF419_{i:02d}"] - 7
        times = set()
        for payload, f in pairs:
            kind = f["DF002"]
            if 1070 < kind < 1140 and kind % 10 in (6, 7) and f["NSat"]:
                message = self.check_msm(payload, f, reference, fcn_map)
                times.add(message.time_ms)
        self.assertEqual(times, {2305 * gnsstime.MS_PER_WEEK + 318945000})


class PyrtcmStationAndEphemerisTest(unittest.TestCase):
    def setUp(self):
        self.pairs = {f["DF002"]: (p, f) for p, f in load("pygpsdata-NTRIP-USCL00CHL0.log")}
        self.reference = gnsstime.gps_seconds(2305, 318945)

    def test_1006_and_1033(self):
        payload, f = self.pairs[1006]
        station = rtcm3.decode_1005_1006(payload)
        for got, key in zip(station.ecef, ("DF025", "DF026", "DF027")):
            self.assertAlmostEqual(got, f[key], places=4)
        self.assertAlmostEqual(station.antenna_height, f["DF028"], places=4)
        info = rtcm3.decode_1033(self.pairs[1033][0])
        self.assertEqual(info["antenna"], "SEPCHOKE_B3E6   SPKE")
        self.assertEqual(info["receiver"], "SEPT POLARX5")
        self.assertEqual(info["firmware"], "5.5.0")

    def test_1019(self):
        payload, f = self.pairs[1019]
        eph = rtcm3.decode_1019(payload, self.reference, None)
        self.assertEqual(eph.sat, "G02")
        self.assertEqual(gnsstime.week_tow(eph.toe), (2305, float(f["DF093"])))
        expected = {"af0": f["DF084"], "af1": f["DF083"], "af2": f["DF082"], "iode": f["DF071"],
                    "iodc": f["DF085"], "crs": f["DF086"], "delta_n": f["DF087"] * PI, "m0": f["DF088"] * PI,
                    "cuc": f["DF089"], "ecc": f["DF090"], "cus": f["DF091"], "sqrt_a": f["DF092"],
                    "cic": f["DF094"], "omega0": f["DF095"] * PI, "cis": f["DF096"], "i0": f["DF097"] * PI,
                    "crc": f["DF098"], "omega": f["DF099"] * PI, "omega_dot": f["DF100"] * PI,
                    "tgd": f["DF101"], "health": f["DF102"], "idot": f["DF079"] * PI}
        for key, value in expected.items():
            self.assertAlmostEqual(getattr(eph, key), value, places=12, msg=key)
        self.assertEqual(eph.accuracy_m, 2.0)              # índice URA 0
        self.assertEqual(eph.fit, 4.0)                    # bandera 0 → 4 h

    def test_1020_sign_magnitude(self):
        payload, f = self.pairs[1020]
        eph = rtcm3.decode_1020(payload, self.reference, 18)
        self.assertEqual(eph.sat, "R09")
        self.assertEqual(eph.fcn, f["DF040"] - 7)
        self.assertAlmostEqual(eph.pos_km[0], f["DF112"])
        self.assertAlmostEqual(eph.vel_kms[0], f["DF111"])
        self.assertAlmostEqual(eph.vel_kms[2], f["DF117"])       # negativa: signo y magnitud
        self.assertAlmostEqual(eph.pos_km[2], f["DF118"])
        self.assertAlmostEqual(eph.acc_kms2[1], f["DF116"])
        self.assertAlmostEqual(eph.tau_n, f["DF124"] * 2.0 ** -30)
        self.assertAlmostEqual(eph.gamma_n, f["DF121"] * 2.0 ** -40)
        # tb 79 × 15 min = 19:45 Moscú = 16:45 UTC del miércoles (día 3) → + 18 s en tiempo GPS.
        self.assertAlmostEqual(eph.toe, gnsstime.gps_seconds(2305, 3 * 86400 + 16 * 3600 + 45 * 60 + 18))
        self.assertAlmostEqual(eph.tof, gnsstime.gps_seconds(2305, 3 * 86400 + 16 * 3600 + 30 * 60 + 18))
        radius = math.sqrt(sum(v * v for v in eph.pos_km))
        self.assertTrue(25000 < radius < 26000, radius)

    def test_1042(self):
        payload, f = self.pairs[1042]
        eph = rtcm3.decode_1042(payload, self.reference, None)
        self.assertEqual(eph.sat, "C12")
        self.assertEqual(gnsstime.gps_to_bdt(eph.toe), (f["DF489"], float(f["DF505"])))
        expected = {"af0": f["DF496"], "af1": f["DF495"], "af2": f["DF494"], "iode": f["DF492"], "iodc": f["DF497"],
                    "crs": f["DF498"], "delta_n": f["DF499"] * PI, "m0": f["DF500"] * PI, "cuc": f["DF501"],
                    "ecc": f["DF502"], "cus": f["DF503"], "sqrt_a": f["DF504"], "cic": f["DF506"],
                    "omega0": f["DF507"] * PI, "cis": f["DF508"], "i0": f["DF509"] * PI, "crc": f["DF510"],
                    "omega": f["DF511"] * PI, "omega_dot": f["DF512"] * PI, "tgd": f["DF513"] * 1e-9,
                    "tgd2": f["DF514"] * 1e-9, "health": f["DF515"], "idot": f["DF491"] * PI}
        for key, value in expected.items():
            self.assertAlmostEqual(getattr(eph, key), value, places=12, msg=key)

    def test_1045_1046(self):
        for kind, prn_key, sisa_key in ((1045, "DF252", "DF291"), (1046, "DF252", "DF286")):
            payload, f = self.pairs[kind]
            eph = rtcm3.decode_galileo(payload, self.reference, None)
            self.assertEqual(eph.sat, f"E{f[prn_key]:02d}")
            self.assertEqual(gnsstime.week_tow(eph.toe), (f["DF289"] + 1024, float(f["DF304"])))
            expected = {"af0": f["DF296"], "af1": f["DF295"], "af2": f["DF294"], "iode": f["DF290"],
                        "crs": f["DF297"], "delta_n": f["DF298"] * PI, "m0": f["DF299"] * PI, "cuc": f["DF300"],
                        "ecc": f["DF301"], "cus": f["DF302"], "sqrt_a": f["DF303"], "cic": f["DF305"],
                        "omega0": f["DF306"] * PI, "cis": f["DF307"], "i0": f["DF308"] * PI, "crc": f["DF309"],
                        "omega": f["DF310"] * PI, "omega_dot": f["DF311"] * PI, "tgd": f["DF312"],
                        "idot": f["DF292"] * PI}
            for key, value in expected.items():
                self.assertAlmostEqual(getattr(eph, key), value, places=12, msg=f"{kind} {key}")
            self.assertAlmostEqual(eph.accuracy_m, 2.0 + (f[sisa_key] - 100) * 0.16)
            self.assertEqual(eph.data_sources, 517.0 if kind == 1046 else 258.0)
            if kind == 1046:
                self.assertAlmostEqual(eph.tgd2, f["DF313"], places=15)


class SyntheticMsmTest(unittest.TestCase):
    def test_msm4_gps_and_glonass(self):
        reference = gnsstime.gps_seconds(2437, 10050)
        gps = msm_frame(1074, 10050000, [{"id": 19, "rough_ms": 74, "rough_mod": 205}], [2, 16],
                        [{"sat": 19, "sig": 2, "pr": 1234, "cp": -5678, "lock": 15, "cnr": 38},
                         {"sat": 19, "sig": 16, "pr": -16384, "cp": 1000, "lock": 3, "half": 1, "cnr": 30}],
                        multiple=True)
        # GLONASS: domingo, 02:47:30 GPS − 18 s = 02:47:12 UTC = 05:47:12 Moscú.
        tod = (5 * 3600 + 47 * 60 + 12) * 1000
        glo = msm_frame(1084, tod, [{"id": 15, "rough_ms": 68, "rough_mod": 700}], [2],
                        [{"sat": 15, "sig": 2, "pr": 10, "cp": 20, "lock": 0, "cnr": 21}], glonass_dow=0)
        frames, _ = split_frames(gps + glo)
        data = gps + glo
        m1 = rtcm3.decode_msm(data[frames[0].start + 3:frames[0].end - 3], reference, 18, {})
        m2 = rtcm3.decode_msm(data[frames[1].start + 3:frames[1].end - 3], reference, 18, {"R15": 0})
        self.assertTrue(m1.multiple)
        self.assertEqual(m1.time_ms, m2.time_ms)
        rough = (74 + 205 / 1024) * RANGE_MS
        l1 = m1.epoch.satellites["G19"]["1C"]
        self.assertAlmostEqual(l1.pseudorange, rough + 1234 * 2 ** -24 * RANGE_MS, places=6)
        self.assertAlmostEqual(l1.phase, (rough - 5678 * 2 ** -29 * RANGE_MS) / (C_LIGHT / 1575.42e6), places=6)
        self.assertAlmostEqual(l1.lock_min_s, 524.288)
        l2 = m1.epoch.satellites["G19"]["2L"]
        self.assertIsNone(l2.pseudorange)            # valor «no válido» de DF400
        self.assertTrue(l2.half_cycle)
        r15 = m2.epoch.satellites["R15"]["1C"]
        self.assertAlmostEqual(r15.phase * C_LIGHT / 1602.0e6, (68 + 700 / 1024) * RANGE_MS + 20 * 2 ** -29 * RANGE_MS,
                               places=6)
        # Sin canal GLONASS conocido no hay fase.
        m3 = rtcm3.decode_msm(data[frames[1].start + 3:frames[1].end - 3], reference, 18, {})
        self.assertIsNone(m3.epoch.satellites["R15"]["1C"].phase)

    def test_msm5_doppler_and_beidou_time(self):
        reference = gnsstime.gps_seconds(2437, 10050)
        frame = msm_frame(1125, 10050000 - 14000, [{"id": 42, "rough_ms": 85, "rough_mod": 10, "ext": 0, "rate": -415}],
                          [2], [{"sat": 42, "sig": 2, "pr": 0, "cp": 0, "lock": 9, "cnr": 43, "rate": 1234}])
        payload = frame[3:-3]
        message = rtcm3.decode_msm(payload, reference, 18, {})
        self.assertEqual(message.time_ms, 2437 * gnsstime.MS_PER_WEEK + 10050000)
        signal = message.epoch.satellites["C42"]["2I"]
        self.assertAlmostEqual(signal.doppler, -(-415 + 0.1234) * 1561.098e6 / C_LIGHT, places=6)

    def test_lock_time_tables(self):
        self.assertEqual(rtcm3.lock_min_df402(0), 0.0)
        self.assertAlmostEqual(rtcm3.lock_min_df402(1), 0.032)
        self.assertAlmostEqual(rtcm3.lock_min_df402(15), 524.288)
        previous = -1
        for i in range(0, 705):
            value = rtcm3.lock_min_df407(i)
            self.assertGreater(value, previous)
            previous = value
        self.assertAlmostEqual(rtcm3.lock_min_df407(63), 0.063)
        self.assertAlmostEqual(rtcm3.lock_min_df407(64), 0.064)
        self.assertAlmostEqual(rtcm3.lock_min_df407(96), 0.128)
        self.assertAlmostEqual(rtcm3.lock_min_df407(704), 67108.864)


if __name__ == "__main__":
    unittest.main()
