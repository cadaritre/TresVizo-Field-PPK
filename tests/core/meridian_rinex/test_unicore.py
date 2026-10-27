"""Decodificación Unicore con tramas construidas a partir de los ejemplos del propio manual N4 R1.6."""
import math
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from builders import obs_record, obsvm_payload, track_status, unicore_frame  # noqa: E402
from tresvizo_ppk.core.meridian_rinex import gnsstime, unicore  # noqa: E402


def decode(frame):
    return unicore.parse_header(frame), frame


class ObsvmTest(unittest.TestCase):
    def test_signals_signs_and_numbering(self):
        records = [
            # GPS 26, L1 C/A (primer registro del ejemplo #OBSVMA del manual).
            obs_record(26, 21720097.812, -114139892.254585, -2263.222, 42.70, 6262.010, track_status(0, 0)),
            # GPS L2 tipo 9 con la bandera L2C → 2S; sin ella → 2W.
            obs_record(26, 21720100.1, -88939000.5, -1763.5, 30.0, 100.0, track_status(0, 9, l2c=True)),
            obs_record(4, 21162081.928, -111207490.841520, -225.810, 20.10, 0.0, track_status(0, 9)),
            # GLONASS: PRN 52 = ranura 15; frecuencia 12 → canal +5.
            obs_record(52, 22454359.660, -120157814.237355, -2435.304, 22.60, 0.0,
                       track_status(1, 0, phase_valid=False), freq=12),
            # Galileo E5b piloto, BeiDou B3I, QZSS 193 L1C/A, SBAS 131.
            obs_record(7, 26403588.843, -106316308.733, -2269.504, 27.17, 50.0, track_status(3, 17)),
            obs_record(42, 25674205.656, -108635960.714, -1755.022, 31.10, 50.0, track_status(4, 21)),
            obs_record(193, 38000000.0, -199000000.0, 10.0, 35.0, 9.0, track_status(5, 0)),
            obs_record(131, 37000000.0, -194000000.0, 1.0, 38.0, 9.0, track_status(2, 0)),
            # Tipo sin código RINEX 3.04 (QZSS L1C/B) y señal sin medida válida.
            obs_record(194, 38000000.0, -199000000.0, 10.0, 35.0, 9.0, track_status(5, 1)),
            obs_record(9, 0.0, 0.0, 0.0, 0.0, 0.0, track_status(0, 0, phase_valid=False, code_valid=False)),
        ]
        header, frame = decode(unicore_frame(12, obsvm_payload(records), 2190, 117395000))
        result = unicore.decode_obsvm(frame, header)
        epoch = result.epoch
        self.assertEqual(epoch.time_ms, 2190 * gnsstime.MS_PER_WEEK + 117395000)
        g26 = epoch.satellites["G26"]
        self.assertAlmostEqual(g26["1C"].pseudorange, 21720097.812, places=6)
        self.assertAlmostEqual(g26["1C"].phase, 114139892.254585, places=6)     # fase = −adr
        self.assertAlmostEqual(g26["1C"].doppler, -2263.222, places=3)
        self.assertAlmostEqual(g26["1C"].cn0, 42.70)
        self.assertAlmostEqual(g26["1C"].lock_time_s, 6262.010, places=3)
        self.assertIn("2S", g26)
        self.assertIn("2W", epoch.satellites["G04"])
        r15 = epoch.satellites["R15"]["1C"]
        self.assertIsNone(r15.phase)          # fase no válida: ni fase ni Doppler
        self.assertIsNone(r15.doppler)
        self.assertAlmostEqual(r15.pseudorange, 22454359.660, places=6)
        self.assertEqual(result.glonass_fcn, {"R15": 5})
        self.assertIn("7Q", epoch.satellites["E07"])
        self.assertIn("6I", epoch.satellites["C42"])
        self.assertIn("1C", epoch.satellites["J01"])
        self.assertIn("1C", epoch.satellites["S31"])
        self.assertNotIn("J02", epoch.satellites)
        self.assertNotIn("G09", epoch.satellites)
        self.assertEqual(result.skipped_signals.get("J:tipo1"), 1)
        self.assertEqual(result.skipped_signals.get("sin_medida_valida"), 1)

    def test_unknown_time_reference_and_week_zero(self):
        payload = obsvm_payload([obs_record(5, 2e7, -1e8, 1.0, 40.0, 1.0, track_status(0, 0))])
        header, frame = decode(unicore_frame(12, payload, 0, 1000))
        self.assertIsNone(unicore.decode_obsvm(frame, header).epoch)
        header, frame = decode(unicore_frame(12, payload, 2190, 1000, time_ref=1))
        self.assertEqual(unicore.decode_obsvm(frame, header).reason, "referencia_de_tiempo_desconocida")


def gps_payload(prn, tow, health, iode, week, zweek, toe, a, dn, m0, ecc, omega, cuc, cus, crc, crs, cic, cis, i0,
                idot, omega0, omega_dot, iodc, toc, tgd, af0, af1, af2, anti_spoof, n, ura, extra=b""):
    return struct.pack("<IdIIIIId" + "d" * 15 + "Id" + "d" * 4 + "Idd", prn, tow, health, iode, iode, week, zweek,
                       toe, a, dn, m0, ecc, omega, cuc, cus, crc, crs, cic, cis, i0, idot, omega0, omega_dot, iodc,
                       toc, tgd, af0, af1, af2, anti_spoof, n, ura)


class EphemerisTest(unittest.TestCase):
    def test_gpseph_manual_example(self):
        # #GPSEPHA,97,GPS,FINE,2190,362528000,0,0,18,1;10,360210.0,0,30,30,2190,2190,367200.0,…
        payload = gps_payload(10, 360210.0, 0, 30, 2190, 2190, 367200.0, 2.656037435e+07, 4.374825086e-09,
                              4.615227840e-01, 7.3941934388e-03, -2.5487093877e+00, 0.0, 9.177252650e-06,
                              2.07281250e+02, -1.78125000e+00, -2.048909664e-08, 1.136213541e-07, 9.7216383679e-01,
                              4.053740283e-10, -2.969634463e-03, -7.97997526e-09, 30, 367200.0, 2.328306437e-09,
                              -2.8089155e-04, -9.3223207e-12, 0.0, 1, 1.458581356e-04, 4.0)
        header, frame = decode(unicore_frame(106, payload, 2190, 362528000))
        eph = unicore.decode_gps_like(frame, header, qzss=False)
        self.assertEqual(eph.sat, "G10")
        self.assertAlmostEqual(eph.toe, gnsstime.gps_seconds(2190, 367200.0))
        self.assertAlmostEqual(eph.ttr, gnsstime.gps_seconds(2190, 360210.0))
        self.assertAlmostEqual(eph.sqrt_a, math.sqrt(2.656037435e+07))
        self.assertAlmostEqual(eph.accuracy_m, 2.0)            # 4 m² → 2 m (índice URA 0)
        self.assertEqual(eph.iode, 30)

    def test_qzss_ura_is_a_variance(self):
        # #QZSSEPHA del manual: URA 7.84 m² → 2.8 m, el valor nominal del índice URA 1.
        payload = gps_payload(4, 368730.0, 0, 185, 2262, 2262, 370800.0, 4.216498367e+07, 2.569749898e-09,
                              1.905190738, 7.5245622196e-02, -1.5526664328, 1.514703035e-05, 3.799796104e-06,
                              -3.61250000e+01, 4.70875000e+02, 3.501772881e-07, 2.438202500e-06, 6.1294064513e-01,
                              4.364467512e-10, -2.354107999, -1.81507561e-09, 953, 370800.0, -3.725290298e-09,
                              9.4612129e-05, 2.2737368e-13, 0.0, 0, 7.292162191e-05, 7.84)
        header, frame = decode(unicore_frame(110, payload, 2262, 368756700))
        eph = unicore.decode_gps_like(frame, header, qzss=True)
        self.assertEqual(eph.sat, "J04")
        self.assertAlmostEqual(eph.accuracy_m, 2.8)

    def test_bdseph_times_are_bdt(self):
        # #BDSEPHA del manual: PRN 60, semana GPS 2190, toe 360000 (BDT), toc 360000.
        payload = struct.pack("<IdIIIIId" + "d" * 15 + "Id" + "d" * 5 + "Idd", 60, 360000.0, 0, 1, 1, 2190, 2190,
                              360000.0, 4.216441036e+07, -4.103028050e-09, 2.042808580, 3.8967351429e-05,
                              2.4660025037, -1.457566395e-05, -2.235500142e-05, 6.85031250e+02, -4.52843750e+02,
                              1.438893378e-07, -1.206062734e-07, 1.2597663760e-01, 1.132190017e-10, -1.993009969,
                              5.03270963e-09, 1, 360000.0, 4.98e-08, 4.98e-08, -1.45519e-07, 8.26006e-14, 0.0, 1,
                              7.291643104e-05, 4.0)
        header, frame = decode(unicore_frame(108, payload, 2190, 362675000))
        eph = unicore.decode_bds(frame, header)
        self.assertEqual(eph.sat, "C60")
        week, sow = gnsstime.gps_to_bdt(eph.toe)
        self.assertEqual((week, sow), (2190 - 1356, 360000.0))
        self.assertAlmostEqual(eph.toe - gnsstime.gps_seconds(2190, 360000.0), 14.0)

    def test_gloeph_manual_example(self):
        # #GLOEPHA,…,2305,116282000,…;40,12,1,0,2305,114318000,10782,71,0,0,43,0,1.890321777343750e+06,…
        payload = struct.pack("<HHBBHIIHBBII" + "d" * 12 + "IIIII", 40, 12, 1, 0, 2305, 114318000, 10782, 71, 0, 0,
                              43, 0, 1.890321777343750e+06, -1.100072509765625e+07, 2.298513378906250e+07,
                              3.121249198913574e+03, -2.802515029907227e+02, -3.969745635986328e+02,
                              -0.000000931322575, -2.793967723846436e-06, -2.793967723846436e-06,
                              -9.616464376449585e-05, -2.793967724e-09, 9.094947017729282e-13, 38040, 2, 3, 0, 12)
        header, frame = decode(unicore_frame(107, payload, 2305, 116282000))
        eph = unicore.decode_glonass(frame, header)
        self.assertEqual(eph.sat, "R03")
        self.assertEqual(eph.fcn, 5)
        # tb: 114318 s GPS − 18 s = lunes 07:45:00 UTC (10:45 en Moscú).
        self.assertAlmostEqual(eph.toe - 18, gnsstime.gps_seconds(2305, 86400 + 7 * 3600 + 45 * 60))
        # tk 38040 s = 10:34:00 Moscú = 07:34:00 UTC del mismo día.
        self.assertAlmostEqual(eph.tof - 18, gnsstime.gps_seconds(2305, 86400 + 7 * 3600 + 34 * 60))
        self.assertAlmostEqual(eph.pos_km[0], 1890.32177734375)
        radius = math.sqrt(sum(v * v for v in eph.pos_km))
        self.assertTrue(25000 < radius < 26000, radius)        # órbita GLONASS ≈ 25 510 km

    def test_galeph_manual_example_gives_inav_and_fnav(self):
        # #GALEPHA,…,2190,363656000,…;36,TRUE,TRUE,0,0,0,0,0,0,107,0,82,356400,5.44061113e+03,…
        payload = struct.pack("<III7BBII" + "d" * 15 + "Iddd" + "Iddd" + "dd", 36, 1, 1, 0, 0, 0, 0, 0, 0, 107, 0,
                              82, 356400, 5.44061113e+03, 2.4787e-09, -1.46715796, 2.844742266e-04, -1.325646591,
                              -8.5607e-06, 9.0413e-06, 1.590e+02, -1.839e+02, 9.3132e-09, -3.9116e-08, 9.965504471e-01,
                              -2.6823e-10, -1.201660091, -5.44451250e-09, 356400, -3.108567325e-04, -5.357492e-12, 0.0,
                              356400, -3.108558012e-04, -5.357492e-12, 0.0, 5.821e-09, 6.752e-09)
        header, frame = decode(unicore_frame(109, payload, 2190, 363656000))
        records = unicore.decode_galileo(frame, header)
        self.assertEqual([r.data_sources for r in records], [517.0, 258.0])
        inav, fnav = records
        self.assertAlmostEqual(inav.af0, -3.108558012e-04)
        self.assertAlmostEqual(fnav.af0, -3.108567325e-04)
        self.assertAlmostEqual(inav.accuracy_m, 3.12)          # SISA 107 → 2 + 7 × 0.16 m
        self.assertAlmostEqual(inav.tgd2, 6.752e-09)
        self.assertEqual(fnav.tgd2, 0.0)
        self.assertAlmostEqual(inav.toe, gnsstime.gps_seconds(2190, 356400))


if __name__ == "__main__":
    unittest.main()
