import os
import random
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from builders import manual_crc24q, manual_crc32, msm_frame, nmea, obs_record, obsvm_payload, track_status, unicore_frame  # noqa: E402
from tresvizo_ppk.core.meridian_rinex.framing import (crc24q, find_session_metadata, parse_session_metadata, split_frames,  # noqa: E402
                                    unicore_crc32)


def meta_line(payload: str) -> bytes:
    body = "PTVZ,META,1," + payload
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}\r\n".encode()


class CrcTest(unittest.TestCase):
    def test_unicore_crc_matches_manual_appendix(self):
        rng = random.Random(7)
        for size in (0, 1, 24, 100, 4000):
            data = bytes(rng.randrange(256) for _ in range(size))
            self.assertEqual(unicore_crc32(data), manual_crc32(data))

    def test_crc24q_check_value(self):
        # Valor de comprobación de CRC-24Q (polinomio 0x1864CFB, inicio 0): «123456789» → 0xCDE703.
        self.assertEqual(crc24q(b"123456789"), 0xCDE703)
        self.assertEqual(manual_crc24q(b"123456789"), 0xCDE703)


class SplitTest(unittest.TestCase):
    def test_mixed_stream(self):
        obs = unicore_frame(12, obsvm_payload([obs_record(5, 2.1e7, -1.1e8, 100.0, 40.0, 10.0,
                                                          track_status(0, 0))]), 2437, 10050000)
        # Un '$' y un 0xD3 dentro del binario no deben partir la trama.
        tricky = unicore_frame(106, b"$GPGGA" + bytes([0xD3, 0x00, 0x13]) + bytes(215), 2437, 10050000)
        msm = msm_frame(1074, 10050000, [{"id": 5, "rough_ms": 70, "rough_mod": 100}], [2],
                        [{"sat": 5, "sig": 2, "pr": 10, "cp": 20, "lock": 9, "cnr": 40}])
        gga = nmea("GNGGA,024730.00,4000.00000000,N,10500.00000000,W,1,13,1.2,1500.0000,M,-20.0000,M,,")
        stream = (meta_line('{"model":"MeridianV"}') + b"garbage\x00\xff" + obs + gga + tricky + msm +
                  b"$command,OBSVMB COM2 1,response: OK*45\r\n" + obs[:30])
        frames, stats = split_frames(stream)
        kinds = [f.kind for f in frames]
        self.assertEqual(kinds, ["ascii", "unicore", "ascii", "unicore", "rtcm", "ascii"])
        self.assertEqual(stats.unicore_ids, {12: 1, 106: 1})
        self.assertEqual(stats.rtcm_types, {1074: 1})
        self.assertTrue(stats.truncated_tail)
        self.assertEqual(stream[frames[3].start:frames[3].end], tricky)

    def test_corrupted_unicore_frame_is_skipped(self):
        good = unicore_frame(12, obsvm_payload([]), 2437, 1000)
        bad = bytearray(good)
        bad[30] ^= 0xFF
        frames, stats = split_frames(bytes(bad) + good)
        self.assertEqual([f.kind for f in frames], ["unicore"])
        self.assertEqual(stats.unicore_crc_errors, 1)

    def test_session_metadata(self):
        line = meta_line('{"model":"MeridianVI","case_offset_m":0.152,"pole_height_m":1.8}').rstrip(b"\r\n")
        record = parse_session_metadata(line)
        self.assertEqual(record["model"], "MeridianVI")
        self.assertEqual(record["_record_version"], 1)
        self.assertIsNone(parse_session_metadata(line[:-2] + b"00"))       # suma mal
        self.assertIsNone(parse_session_metadata(b"$PTVZ,META,1,{no json}*00"))
        stream = b"\xaa\x44" + meta_line('{"model":"MeridianV"}') + bytes(50)
        self.assertEqual(find_session_metadata(stream)["model"], "MeridianV")
        self.assertIsNone(find_session_metadata(bytes(100)))


@unittest.skipUnless(os.path.exists(os.path.join(os.path.dirname(__file__), "..", "..", "..", "captures", "local",
                                                 "sessions", "4d481416d0ae48aabb98f758fd82d11e", "stream.bin")),
                     "sin la captura real del UM980 (captures/local, fuera de Git)")
class RealCaptureFramingTest(unittest.TestCase):
    def test_every_frame_has_valid_crc(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "captures", "local", "sessions",
                            "4d481416d0ae48aabb98f758fd82d11e", "stream.bin")
        with open(path, "rb") as handle:
            data = handle.read()
        frames, stats = split_frames(data)
        # Lo que contó el banco el 20-09-2026: 105 OBSVM y efemérides, cero errores de CRC.
        self.assertEqual(stats.unicore_ids.get(12), 105)
        self.assertEqual(stats.unicore_crc_errors, 0)
        for frame in frames:
            if frame.kind == "unicore":
                body = data[frame.start:frame.end]
                self.assertEqual(manual_crc32(body[:-4]), struct.unpack("<I", body[-4:])[0])


if __name__ == "__main__":
    unittest.main()
