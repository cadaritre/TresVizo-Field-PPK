"""Conversión completa con flujos construidos (Unicore y RTCM) y con el registro real de una estación IGS."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from builders import msm_frame, nmea, obs_record, obsvm_payload, track_status, unicore_frame  # noqa: E402
from tresvizo_ppk.core.meridian_rinex.converter import ConversionError, Options, convert, inspect  # noqa: E402
from rinex_check import check_nav, check_obs  # noqa: E402

# La misma línea que genera el firmware en firmware/esp32/test/session_metadata_test.cpp.
FIRMWARE_META = (
    b'$PTVZ,META,1,{"model":"MeridianV","serial_number":"MV-441BF674E828","unit_name":"MeridianV-E828",'
    b'"firmware_version":"0.10.0","hardware_id":"tresvizo-meridianv-v03-s3-16m-v1","gnss_receiver":"UM980",'
    b'"antenna_model":"HARXON HA-901A","antenna_radome":"NONE","case_offset_m":0.1160,"antenna_pco_m":0.0000,'
    b'"pole_height_m":1.8000,"raw_profile":"obsvmb","raw_interval_s":1,"ephemeris_interval_s":30,'
    b'"session_id":"0123456789abcdef01234567","start_uptime_ms":123456,"start_utc_time_of_day_ms":10050000,'
    b'"start_utc":null,"session_name":null,"segment":1,"previous_session_id":null,"frame":null,"frame_epoch":null,'
    b'"antenna_calibration":"uncalibrated"}'
    b"*57\r\n")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pyrtcm"


def unicore_session(lock_times=(10.0, 11.0, 0.4, 1.4), meta=FIRMWARE_META) -> bytes:
    stream = bytearray(meta)
    for k, lock in enumerate(lock_times):
        tow = 10050000 + 1000 * k
        records = [
            obs_record(19, 22246058.910 - 493.5 * k, -(116903882.532 - 2593.4 * k), 2593.4, 37.8, lock,
                       track_status(0, 0)),
            obs_record(19, 22246060.0 - 493.5 * k, -(91093882.0 - 2020.8 * k), 2020.8, 30.0, 50.0 + k,
                       track_status(0, 9)),
            obs_record(42, 25674197.831 + 415 * k, -(133692269.367 + 2161 * k), -2161.0, 42.9, 1730.7 + k,
                       track_status(4, 0)),
            obs_record(55, 20872681.187, -111419852.210, 3522.782, 37.68, 99.0 + k, track_status(1, 0), freq=4),
        ]
        stream += unicore_frame(12, obsvm_payload(records), 2437, tow)
        stream += nmea("GNGGA,%02d%02d%05.2f,4000.00000,N,10500.00000,W,1,13,1.2,1551.835,M,0.000,M,,"
                       % (2, 47, 12 + k))
    return bytes(stream)


class ConverterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_header_from_session_metadata(self):
        path = self.write("a.bin", unicore_session())
        self.assertEqual(inspect(path)["serial_number"], "MV-441BF674E828")
        report = convert(path, Options(antenna_height_m=1.234, out_dir=self.dir))
        errors, info = check_obs(report.obs_path.read_text())
        self.assertEqual(errors, [])
        self.assertEqual(info["marker"], "MeridianV-E828")
        self.assertEqual(info["receiver"], ("MV-441BF674E828", "MeridianV UM980", "0.10.0"))
        self.assertEqual(info["antenna"][1], "HARXON HA-901A  NONE")
        self.assertEqual(info["ANTENNA: DELTA H/E/N"], [1.35, 0.0, 0.0])      # 1.234 + 0.116
        self.assertTrue(any("1.2340 m" in c for c in info["comments"]))
        self.assertEqual(report.obs_path.name, "MeridianV-E828_20260920_024730.obs")
        self.assertEqual(info["slots"], {18: -3})
        self.assertIsNone(report.nav_path)                                   # sin efemérides
        self.assertTrue(any("efemérides" in w for w in report.warnings))
        # La fase del UM980 no documenta su alineación: SYS / PHASE SHIFT solo con el sistema.
        self.assertEqual(sorted(info["phase_shift"]), ["C", "G", "R"])
        approx = info["APPROX POSITION XYZ"]
        self.assertTrue(6.3e6 < sum(v * v for v in approx) ** 0.5 < 6.4e6)

    def test_base_frame_from_session_metadata(self):
        # Grabado como base con el marco declarado (T2): va como comentario, sin transformar nada.
        body = FIRMWARE_META[len(b"$PTVZ,META,1,"):FIRMWARE_META.index(b"*")]
        body = body.replace(b'"frame":null,"frame_epoch":null', b'"frame":"ITRF2008","frame_epoch":2010.0000')
        checksum = 0
        for byte in b"PTVZ,META,1," + body:
            checksum ^= byte
        meta = b"$PTVZ,META,1," + body + b"*%02X\r\n" % checksum
        path = self.write("f.bin", unicore_session(meta=meta))
        self.assertEqual(inspect(path)["frame"], "ITRF2008")
        report = convert(path, Options(antenna_height_m=1.0, out_dir=self.dir))
        _, info = check_obs(report.obs_path.read_text())
        self.assertIn("Base frame declared: ITRF2008 @2010.0000", info["comments"])
        # Sin marco (rover, o una grabación anterior), ningún comentario.
        plain = convert(self.write("g.bin", unicore_session()), Options(antenna_height_m=1.0, out_dir=self.dir))
        _, info = check_obs(plain.obs_path.read_text())
        self.assertFalse(any("frame" in c for c in info["comments"]))

    def test_pco_only_below_the_phase_center(self):
        # GNS2-02 / GEO2-4: el 1005 es el ARP (solo se le resta DELTA H); la GGA es el centro de fase (DELTA H +
        # PCO). Hasta el 21-10-2026 también se le restaba el PCO al ARP, sin notarse porque era 0.
        from tresvizo_ppk.core.meridian_rinex import converter, rtcm3
        from tresvizo_ppk.core.meridian_rinex.geodesy import ecef_to_geodetic, geodetic_to_ecef
        arp = geodetic_to_ecef(19.4326, -99.1332, 2240.0)
        station = converter.Survey(station=rtcm3.StationPosition(station=7, ecef=arp, antenna_height=None))
        mark, source = converter._approx_position(station, 1.916, 0.06)
        self.assertAlmostEqual(ecef_to_geodetic(*mark)[2], 2240.0 - 1.916, places=6)
        self.assertEqual(source, "RTCM 1005/1006 ARP minus DELTA H")
        gga = converter.Survey(gga=[(19.4326, -99.1332, 2240.0)])
        mark, source = converter._approx_position(gga, 1.916, 0.06)
        self.assertAlmostEqual(ecef_to_geodetic(*mark)[2], 2240.0 - 1.916 - 0.06, places=6)
        self.assertEqual(source, "median of NMEA GGA minus DELTA H and PCO")

    def test_antenna_calibration_comment(self):
        report = convert(self.write("h.bin", unicore_session()), Options(antenna_height_m=1.0, out_dir=self.dir))
        _, info = check_obs(report.obs_path.read_text())
        self.assertIn("Antenna PCO/PCV not calibrated (no ANTEX entry)", info["comments"])

    def test_lock_time_reset_sets_lli(self):
        report = convert(self.write("b.bin", unicore_session()), Options(antenna_height_m=1.0, out_dir=self.dir))
        _, info = check_obs(report.obs_path.read_text())
        llis = [info["values"][(stamp, "G19")]["L1C"][1] for stamp in info["epochs"]]
        self.assertEqual(llis, [" ", " ", "1", " "])          # el tiempo de enganche volvió a 0.4 s

    def test_pole_height_from_receiver_when_not_given(self):
        report = convert(self.write("c.bin", unicore_session()), Options(out_dir=self.dir))
        self.assertAlmostEqual(report.delta_h_m, 1.916)
        self.assertTrue(any("bastón" in w for w in report.warnings))

    def test_missing_height_without_pole(self):
        meta = FIRMWARE_META.replace(b'"pole_height_m":1.8000', b'"pole_height_m":null')
        checksum = 0
        for byte in meta[1:meta.rfind(b"*")]:
            checksum ^= byte
        meta = meta[:meta.rfind(b"*") + 1] + b"%02X\r\n" % checksum
        with self.assertRaises(ConversionError) as raised:
            convert(self.write("d.bin", unicore_session(meta=meta)), Options(out_dir=self.dir))
        self.assertIn("5/8", str(raised.exception))

    def test_without_metadata_assumes_meridianv_and_warns(self):
        report = convert(self.write("e.bin", unicore_session(meta=b"")), Options(antenna_height_m=2.0, out_dir=self.dir))
        self.assertEqual(report.model, "MeridianV")
        self.assertAlmostEqual(report.delta_h_m, 2.116)
        self.assertTrue(any("metadatos" in w for w in report.warnings))

    def test_interrupted_part_file(self):
        data = unicore_session()
        report = convert(self.write("f.part", data[:-40]), Options(antenna_height_m=1.0, out_dir=self.dir))
        self.assertTrue(report.partial)
        errors, info = check_obs(report.obs_path.read_text())
        self.assertEqual(errors, [])
        self.assertTrue(any(".part" in c for c in info["comments"]))

    def test_nmea_only_recording_is_explained(self):
        data = FIRMWARE_META + nmea("GNGGA,024730.00,4000.00000,N,10500.00000,W,1,13,1.2,1551.835,M,0.000,M,,") * 5
        with self.assertRaises(ConversionError) as raised:
            convert(self.write("g.bin", data), Options(antenna_height_m=1.0, out_dir=self.dir))
        self.assertIn("perfil de datos crudos", str(raised.exception))

    def test_msm_without_date_asks_for_it(self):
        frame = msm_frame(1074, 10050000, [{"id": 19, "rough_ms": 74, "rough_mod": 205}], [2],
                          [{"sat": 19, "sig": 2, "pr": 1, "cp": 2, "lock": 15, "cnr": 38}])
        path = self.write("h.bin", frame * 3)
        with self.assertRaises(ConversionError) as raised:
            convert(path, Options(antenna_height_m=1.0, out_dir=self.dir, model="MeridianVI"))
        self.assertIn("--date", str(raised.exception))
        report = convert(path, Options(antenna_height_m=1.0, out_dir=self.dir, model="MeridianVI",
                                       date=__import__("datetime").date(2026, 9, 20)))
        errors, info = check_obs(report.obs_path.read_text())
        self.assertEqual(errors, [])
        self.assertEqual(info["epochs"], [(2026, 9, 20, 2, 47, 30.0)])
        # MSM: fases alineadas por la norma → referencia en blanco y el resto a 0.
        self.assertEqual(info["phase_shift"], ["G L1C"])

    def test_msm_lli_and_half_cycle(self):
        frames = b""
        for k, (lock, half) in enumerate(((12, 0), (12, 0), (5, 1), (6, 0))):
            frames += msm_frame(1074, 10050000 + 1000 * k, [{"id": 19, "rough_ms": 74, "rough_mod": 205}], [2],
                                [{"sat": 19, "sig": 2, "pr": 1, "cp": 2 + k, "lock": lock, "half": half, "cnr": 38}])
        frames += nmea("GNRMC,024730.00,A,4000.00000,N,10500.00000,W,0.0,0.0,200926,,,A")
        report = convert(self.write("i.bin", frames), Options(antenna_height_m=1.0, out_dir=self.dir,
                                                               model="MeridianVI"))
        _, info = check_obs(report.obs_path.read_text())
        llis = [info["values"][(stamp, "G19")]["L1C"][1] for stamp in info["epochs"]]
        # El indicador baja (12 → 5) con medio ciclo → 1 + 2 = 3; luego 6 (≥ 1.024 s) ya no marca nada.
        self.assertEqual(llis, [" ", " ", "3", " "])


class IgsStationRtcmTest(unittest.TestCase):
    """Registro real de USCL00CHL (Septentrio PolaRx5, red IGS) con MSM6/MSM7 y efemérides."""

    def test_convert(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "uscl.bin"
            shutil.copy(FIXTURES / "pygpsdata-NTRIP-USCL00CHL0.log", source)
            report = convert(source, Options(antenna_height_m=0.0, out_dir=Path(tmp), model="MeridianVI"))
            errors, info = check_obs(report.obs_path.read_text())
            self.assertEqual(errors, [])
            # Las MSM de GPS, GLONASS, Galileo, SBAS, QZSS, BeiDou y NavIC caen en la misma época:
            # comprueba la hora de Moscú → GPS de GLONASS y los 14 s de BDT.
            self.assertEqual(len(info["epochs"]), 1)
            self.assertEqual(set(info["types"]), {"G", "R", "E", "S", "C"})     # 1117 y 1137 vienen vacías
            self.assertIn("G L1C", info["phase_shift"])
            self.assertIn("G L2W  0.00000", info["phase_shift"])
            nav_errors, records = check_nav(report.nav_path.read_text())
            self.assertEqual(nav_errors, [])
            self.assertEqual(sorted(r["sat"] for r in records), ["C12", "E03", "E05", "G02", "R09"])
            # Aproximada: la ARP de la 1006 (sin altura de antena, 0 m indicados).
            self.assertTrue(any("1005/1006" in c for c in info["comments"]))
            x, y, z = info["APPROX POSITION XYZ"]
            self.assertAlmostEqual(x, 1762489.6191, delta=0.2)


if __name__ == "__main__":
    unittest.main()
