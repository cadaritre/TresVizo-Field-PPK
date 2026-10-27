"""Tramos de una grabación larga (GNS2-01): encontrarlos siguiendo `previous_session_id`, ordenarlos y
unirlos sin perder la trama partida en la costura."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from builders import nmea, obs_record, obsvm_payload, track_status, unicore_frame  # noqa: E402
from tresvizo_ppk.core.meridian_rinex.framing import split_frames  # noqa: E402
from tresvizo_ppk.core.meridian_rinex.segments import chain_from_files, find_chain, join, read_segment, strip_metadata  # noqa: E402

IDS = ["0123456789abcdef01234567", "89abcdef0123456789abcdef", "fedcba9876543210fedcba98"]


def meta(session_id: str, segment: int = 1, previous: str | None = None) -> bytes:
    """El registro que escribe el firmware (include/session_metadata.h), con su suma."""
    prev = f'"{previous}"' if previous else "null"
    body = ('{"model":"MeridianV","serial_number":"MV-441BF674E828","unit_name":"MeridianV-74E828",'
            '"firmware_version":"1.0.0","hardware_id":"tresvizo-meridianv-v03-s3-16m-v1","gnss_receiver":"UM980",'
            '"antenna_model":"HARXON HA-901A","antenna_radome":"NONE","case_offset_m":0.1160,"antenna_pco_m":0.0000,'
            '"pole_height_m":1.8000,"raw_profile":"obsvmb","raw_interval_s":1,"ephemeris_interval_s":30,'
            f'"session_id":"{session_id}","start_uptime_ms":1000,"start_utc_time_of_day_ms":10050000,'
            f'"start_utc":"2026-09-20T02:47:30Z","session_name":null,"segment":{segment},'
            f'"previous_session_id":{prev},"frame":null,"frame_epoch":null}}')
    line = "PTVZ,META,1," + body
    checksum = 0
    for byte in line.encode("ascii"):
        checksum ^= byte
    return f"${line}*{checksum:02X}\r\n".encode("ascii")


def body(epochs: int = 12) -> bytes:
    stream = bytearray()
    for k in range(epochs):
        records = [obs_record(19, 22246058.910 - 493.5 * k, -(116903882.532 - 2593.4 * k), 2593.4, 37.8, 10.0 + k,
                              track_status(0, 0)),
                   obs_record(42, 25674197.831 + 415 * k, -(133692269.367 + 2161 * k), -2161.0, 42.9, 1730.7 + k,
                              track_status(4, 0))]
        stream += unicore_frame(12, obsvm_payload(records), 2437, 10050000 + 1000 * k)
        stream += nmea("GNGGA,0247%05.2f,4000.00000,N,10500.00000,W,1,13,1.2,1551.835,M,0.000,M,," % (30 + k))
    return bytes(stream)


class SegmentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.stream = body()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def split(self, cuts: list[int], names: list[str]) -> list[Path]:
        """Tramos como los de la rotación: el flujo partido en `cuts`, cada tramo con su registro."""
        edges = [0] + cuts + [len(self.stream)]
        paths = []
        for i, name in enumerate(names):
            data = meta(IDS[i], i + 1, IDS[i - 1] if i else None) + self.stream[edges[i]:edges[i + 1]]
            paths.append(self.write(name, data))
        return paths

    def test_reads_segment_from_metadata(self):
        first, second = self.split([500], ["a.bin", "b.bin"])
        s = read_segment(second)
        self.assertEqual((s.session_id, s.number, s.previous), (IDS[1], 2, IDS[0]))
        self.assertIsNone(read_segment(first).previous)
        # Sin registro (firmware anterior a 1.0.0) no es un tramo reconocible.
        self.assertIsNone(read_segment(self.write("old.bin", self.stream)))

    def test_byte_cut_mid_frame_is_recovered_when_joined(self):
        # Corte por bytes a media OBSVM, como los firmwares anteriores: convertir el tramo 2 solo pierde
        # esa trama; unidos, el flujo sale entero.
        frames, _ = split_frames(self.stream)
        cut = frames[2].start + 7
        paths = self.split([cut, cut + 1300], ["MeridianV_20260920_0247UTC.bin", "zz.bin", "mm.bin"])
        for start in paths:     # cualquiera de los tres lleva a la cadena entera, en orden
            chain = find_chain(start)
            self.assertEqual([s.number for s in chain.segments], [1, 2, 3])
            self.assertEqual(chain.warnings, [])
            self.assertEqual(join(chain), meta(IDS[0]) + self.stream)
        alone, _ = split_frames(strip_metadata(paths[1].read_bytes()))
        joined, _ = split_frames(join(find_chain(paths[1])))
        self.assertLess(len([f for f in alone if f.kind == "unicore"]), 12)
        self.assertEqual(len([f for f in joined if f.kind == "unicore"]), 12)

    def test_card_copied_as_folders(self):
        # La tarjeta copiada tal cual: sessions/<id>/stream.bin.
        frames, _ = split_frames(self.stream)
        names = [f"sessions/{IDS[0]}/stream.bin", f"sessions/{IDS[1]}/stream.bin", f"sessions/{IDS[2]}/stream.part"]
        paths = self.split([frames[4].end, frames[8].end], names)
        chain = find_chain(paths[1])
        self.assertEqual([s.path for s in chain.segments], paths)
        self.assertTrue(any("incompleto" in w for w in chain.warnings) is False)   # el .part es el último
        self.assertEqual(join(chain), meta(IDS[0]) + self.stream)

    def test_missing_segment_is_reported(self):
        paths = self.split([400, 900], ["a.bin", "b.bin", "c.bin"])
        paths[0].unlink()
        chain = find_chain(paths[2])
        self.assertEqual([s.number for s in chain.segments], [2, 3])
        self.assertTrue(any("Falta el tramo 1" in w for w in chain.warnings))

    def test_unrelated_recordings_are_not_mixed(self):
        paths = self.split([400], ["a.bin", "b.bin"])
        self.write("other.bin", meta("aaaaaaaaaaaaaaaaaaaaaaaa") + self.stream)
        self.write("notes.txt", b"hola")
        chain = find_chain(paths[0])
        self.assertEqual([s.path for s in chain.segments], paths)

    def test_part_and_bin_of_the_same_session(self):
        paths = self.split([400], ["a.bin", "b.part"])
        self.write("b-copia.bin", paths[1].read_bytes())
        chain = find_chain(paths[0])
        self.assertEqual(chain.segments[1].path.name, "b-copia.bin")

    def test_files_given_in_any_order(self):
        paths = self.split([300, 800], ["a.bin", "b.bin", "c.bin"])
        chain = chain_from_files([paths[2], paths[0], paths[1]])
        self.assertEqual([s.path for s in chain.segments], paths)
        self.assertEqual(join(chain), meta(IDS[0]) + self.stream)


class ConvertJoinedTest(unittest.TestCase):
    """La conversión entera: un RINEX con todas las épocas aunque la rotación cortara a media OBSVM."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_joined_conversion_keeps_every_epoch(self):
        from tresvizo_ppk.core.meridian_rinex.converter import Options, convert
        stream = body(12)
        frames, _ = split_frames(stream)
        observations = [f for f in frames if f.kind == "unicore"]
        cut = observations[6].start + 9                 # a media OBSVM, como los firmwares anteriores
        first = self.dir / "a.bin"
        second = self.dir / "b.bin"
        first.write_bytes(meta(IDS[0]) + stream[:cut])
        second.write_bytes(meta(IDS[1], 2, IDS[0]) + stream[cut:])
        whole = convert(second, Options(antenna_height_m=1.5, out_dir=self.dir / "unida"))
        self.assertEqual(whole.epochs, 12)
        self.assertEqual(whole.segments, [first, second])
        self.assertTrue(any("2 tramos" in w for w in whole.warnings))
        alone = convert(second, Options(antenna_height_m=1.5, out_dir=self.dir / "sola", join_segments=False))
        self.assertEqual(alone.epochs, 5)               # la 7.ª, partida, se pierde convirtiendo por separado
        self.assertEqual(alone.segments, [])
        # Los archivos indicados a mano, en cualquier orden, dan lo mismo.
        given = convert(second, Options(antenna_height_m=1.5, out_dir=self.dir / "dada", segments=[second, first]))
        self.assertEqual(given.epochs, 12)
        self.assertEqual(given.obs_path.read_text().split("END OF HEADER")[1],
                         whole.obs_path.read_text().split("END OF HEADER")[1])


REAL = Path(__file__).resolve().parents[3] / "captures" / "local" / "sessions" / "4d481416d0ae48aabb98f758fd82d11e" / "stream.bin"


@unittest.skipUnless(REAL.exists(), "sin la captura real del UM980 (captures/local)")
class RealCaptureSegmentsTest(unittest.TestCase):
    """La captura real del banco partida en tres tramos por bytes, a media trama (lo peor que hacía la
    rotación): unida, el RINEX de observación es el mismo, época a época, que el de la captura entera."""

    def test_same_rinex_as_unsplit(self):
        from tresvizo_ppk.core.meridian_rinex.converter import Options, convert
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            data = REAL.read_bytes()
            head = data.split(b"\n", 1)[0] + b"\n" if data.startswith(b"$PTVZ,META,") else b""
            stream = data[len(head):]
            # A media OBSVM (Message ID 12), en el primer y el segundo tercio de la captura.
            frames, _ = split_frames(stream)
            observations = [f for f in frames if f.kind == "unicore" and stream[f.start + 4:f.start + 6] == b"\x0c\x00"]
            cuts = [observations[len(observations) // 3].start + 40, observations[2 * len(observations) // 3].start + 100]
            edges = [0] + cuts + [len(stream)]
            for i in range(3):
                (folder / f"t{i + 1}.bin").write_bytes(meta(IDS[i], i + 1, IDS[i - 1] if i else None)
                                                       + stream[edges[i]:edges[i + 1]])
            reference = folder / "ref" / "entera.bin"
            reference.parent.mkdir()
            reference.write_bytes(meta(IDS[0]) + stream)
            joined = convert(folder / "t3.bin", Options(antenna_height_m=1.5, out_dir=folder / "unida"))
            single = convert(reference, Options(antenna_height_m=1.5, out_dir=folder / "entera"))
            self.assertEqual(len(joined.segments), 3)
            self.assertEqual(joined.epochs, single.epochs)
            self.assertEqual(joined.obs_path.read_text().split("END OF HEADER")[1],
                             single.obs_path.read_text().split("END OF HEADER")[1])
            # Por separado, las costuras pierden épocas.
            parts = sum(convert(folder / f"t{i + 1}.bin", Options(antenna_height_m=1.5, out_dir=folder / f"p{i}",
                                                                  join_segments=False)).epochs for i in range(3))
            self.assertLess(parts, single.epochs)


if __name__ == "__main__":
    unittest.main()
