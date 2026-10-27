import gzip
import zipfile

import pytest

from tresvizo_ppk.core import compress
from tresvizo_ppk.errors import UserError

from motor_util import DATA, needs_tools


def _reference(unzip):
    return unzip("UGTO_a.rnx.gz", "ref.rnx").read_bytes()


def test_lzw_matches_unix_compress(unzip):
    ref = _reference(unzip)
    assert compress.lzw_decompress((DATA / "UGTO_a.rnx.Z").read_bytes()) == ref


def test_lzw_roundtrip_small_and_rejects_garbage():
    with pytest.raises(ValueError):
        compress.lzw_decompress(b"no es lzw")


def test_unpack_nested_zip_gz_z(tmp_path, unzip):
    ref = _reference(unzip)
    inner = tmp_path / "UGTO_a.rnx.Z"
    inner.write_bytes((DATA / "UGTO_a.rnx.Z").read_bytes())
    bundle = tmp_path / "descarga.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.write(inner, "carpeta/UGTO_a.rnx.Z")
        z.write(DATA / "UGTO_b.rnx.gz", "UGTO_b.rnx.gz")
    out = compress.unpack([bundle], tmp_path / "salida")
    names = sorted(p.name for p in out)
    assert names == ["UGTO_a.rnx", "UGTO_b.rnx"]
    assert (tmp_path / "salida" / "UGTO_a.rnx").read_bytes() == ref
    with gzip.open(DATA / "UGTO_b.rnx.gz") as handle:
        assert (tmp_path / "salida" / "UGTO_b.rnx").read_bytes() == handle.read()


def _values(path):
    from tresvizo_ppk.core.rinex import epoch_values, iter_epochs, read_header
    header = read_header(path)
    return header.obs_types, [(b.time, epoch_values(header, b)) for b in iter_epochs(header)]


@needs_tools
def test_hatanaka_v2_and_v3(tmp_path, unzip):
    # Hatanaka no conserva los espacios del final de línea ni el formato del reloj: se comparan los valores
    ref_v3 = unzip("UGTO_a.rnx.gz", "ref.rnx")
    ref_v2 = unzip("INEG2930.26o.gz", "ref.26o")
    out = compress.unpack([DATA / "UGTO_a.crx.gz", DATA / "INEG2930.26d.gz"], tmp_path / "salida")
    by_name = {p.name: p for p in out}
    assert _values(by_name["UGTO_a.rnx"]) == _values(ref_v3)
    assert _values(by_name["INEG2930.26o"]) == _values(ref_v2)
    assert len(_values(ref_v3)[1]) == 13 and len(_values(ref_v2)[1]) == 21


def test_empty_file_is_clear_error(tmp_path):
    empty = tmp_path / "INEG293q_304.zip"
    empty.write_bytes(b"")
    with pytest.raises(UserError, match="vacío"):
        compress.unpack([empty], tmp_path / "salida")


def test_damaged_zip_is_user_error(tmp_path):
    bad = tmp_path / "malo.zip"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 50)
    with pytest.raises(UserError):
        compress.unpack([bad], tmp_path / "salida")
