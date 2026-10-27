import shutil
from datetime import datetime, timezone

import pytest

from tresvizo_ppk.core import rinex, tools
from tresvizo_ppk.errors import UserError

from motor_util import DATA, gunzip, needs_tools


def t(h, m, s=0):
    return datetime(2026, 10, 20, h, m, s)


def test_header_v3(unzip):
    h = rinex.read_header(unzip("UGTO_a.rnx.gz"))
    assert h.version == 3.04 and h.is_observation and h.marker == "UGTO"
    assert h.antenna_type == "TRM115000.00" and h.radome == "NONE"
    assert h.delta_hen == (0.071, 0.0, 0.0)
    assert h.receiver_type == "TRIMBLE ALLOY"
    assert h.obs_types["G"][:3] == ["C1C", "L1C", "S1C"] and len(h.obs_types["G"]) == 15
    assert h.interval == 15.0 and h.time_system == "GPS"
    assert h.first_obs == t(14, 0) and h.last_obs == t(14, 3)
    assert h.glonass_slots["R09"] == -2


def test_header_v2_and_classify(unzip):
    obs = unzip("INEG2930.26o.gz")
    nav = unzip("INEG2930.26n.gz")
    glo = unzip("INEG2930.26g.gz")
    h = rinex.read_header(obs)
    assert h.version == 2.11 and h.system == "M" and h.obs_types["*"][:4] == ["C1", "L1", "S1", "P1"]
    assert len(h.obs_types["*"]) == 20
    assert rinex.classify(obs) == "obs" and rinex.classify(nav) == "nav" and rinex.classify(glo) == "nav"
    assert rinex.read_header(glo).system == "R"


def test_epochs_and_values_v3(unzip):
    h = rinex.read_header(unzip("UGTO_a.rnx.gz"))
    blocks = list(rinex.iter_epochs(h))
    assert len(blocks) == 13 and blocks[0].time == t(14, 0) and blocks[-1].time == t(14, 3)
    values = rinex.epoch_values(h, blocks[0])
    assert "G23" in values and abs(values["G23"]["C1C"][0] - 22023649.813) < 1e-6
    assert abs(values["G23"]["L1C"][0] - 115734962.832) < 1e-6


def test_epochs_and_values_v2(unzip):
    h = rinex.read_header(unzip("INEG2930.26o.gz"))
    blocks = list(rinex.iter_epochs(h))
    assert len(blocks) == 21
    assert all(len(b.satellites) > 12 for b in blocks)          # con líneas de continuación de satélites
    values = rinex.epoch_values(h, blocks[0])
    assert all(sat[0] in "GRESC" for sat in values)
    sat = next(s for s in values if s.startswith("G"))
    assert "L1" in values[sat] and "C1" in values[sat]


def test_merge_overlap_and_trim(tmp_path, unzip):
    a, b = unzip("UGTO_a.rnx.gz"), unzip("UGTO_b.rnx.gz")
    report = rinex.merge_observations([b, a], tmp_path / "unido.obs")          # en desorden a propósito
    assert report.epochs == 21 and report.duplicates == 3                      # 14:00:00 … 14:05:00
    assert report.first == t(14, 0) and report.last == t(14, 5)
    h = report.header
    assert h.first_obs == t(14, 0) and h.last_obs == t(14, 5) and h.marker == "UGTO"
    count, first, last, interval = rinex.scan_epochs(h)
    assert (count, first, last, interval) == (21, t(14, 0), t(14, 5), 15.0)
    trimmed = rinex.merge_observations([a, b], tmp_path / "recorte.obs", start=t(14, 1), end=t(14, 2, 30))
    assert trimmed.epochs == 7 and trimmed.first == t(14, 1) and trimmed.last == t(14, 2, 30)


def test_merge_keeps_every_value(tmp_path, unzip):
    a, b = unzip("UGTO_a.rnx.gz"), unzip("UGTO_b.rnx.gz")
    merged = rinex.merge_observations([a, b], tmp_path / "unido.obs").header
    original = {}
    for path in (a, b):
        h = rinex.read_header(path)
        for block in rinex.iter_epochs(h):
            original[block.time] = rinex.epoch_values(h, block)
    for block in rinex.iter_epochs(merged):
        assert rinex.epoch_values(merged, block) == original[block.time]


def test_merge_union_of_types(tmp_path, unzip):
    a, b = unzip("UGTO_a.rnx.gz"), unzip("UGTO_b.rnx.gz")
    # quitar S5X de GPS en el archivo b: hay que reescribir con la unión de tipos sin perder valores
    h = rinex.read_header(b)
    types = dict(h.obs_types)
    types["G"] = [x for x in types["G"] if x != "S5X"]
    lines = rinex.build_header(h, first=h.first_obs, last=h.last_obs, obs_types=types)
    body = []
    for block in rinex.iter_epochs(h):
        body += rinex.render_epoch(h, block, types)
    b2 = tmp_path / "b_sin_s5x.rnx"
    b2.write_text("\n".join(lines + body) + "\n", encoding="latin-1")
    report = rinex.merge_observations([a, b2], tmp_path / "unido.obs")
    assert report.rewritten and "S5X" in report.header.obs_types["G"]
    merged = report.header
    hb = rinex.read_header(b2)
    for block in rinex.iter_epochs(merged):
        if block.time >= t(14, 3, 15):
            values = rinex.epoch_values(merged, block)
            assert all("S5X" not in v for s, v in values.items() if s.startswith("G"))
    original_b = {blk.time: rinex.epoch_values(hb, blk) for blk in rinex.iter_epochs(hb)}
    for block in rinex.iter_epochs(merged):
        if block.time in original_b and block.time > t(14, 3):
            assert rinex.epoch_values(merged, block) == original_b[block.time]


def test_merge_v2(tmp_path, unzip):
    obs = unzip("INEG2930.26o.gz")
    first = rinex.merge_observations([obs], tmp_path / "p1.26o", end=t(14, 2))
    second = rinex.merge_observations([obs], tmp_path / "p2.26o", start=t(14, 2))
    merged = rinex.merge_observations([second.output, first.output], tmp_path / "unido.26o")
    assert merged.epochs == 21 and merged.duplicates == 1
    h0 = rinex.read_header(obs)
    ref = {b.time: rinex.epoch_values(h0, b) for b in rinex.iter_epochs(h0)}
    for block in rinex.iter_epochs(merged.header):
        assert rinex.epoch_values(merged.header, block) == ref[block.time]


def test_merge_rejects_mixed_versions_and_stations(tmp_path, unzip):
    with pytest.raises(UserError, match="misma versión"):
        rinex.merge_observations([unzip("UGTO_a.rnx.gz"), unzip("INEG2930.26o.gz")], tmp_path / "x.obs")
    with pytest.raises(UserError, match="misma estación"):
        rinex.merge_observations([unzip("UGTO_a.rnx.gz"), unzip("ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz")],
                                 tmp_path / "y.obs")


@needs_tools
def test_merge_equals_rtklib_reading_separate_files(tmp_path, unzip):
    """RTKLIB (rnx2rtkp, posicionamiento puntual) da exactamente la misma solución con los dos archivos sueltos que
    con el archivo unido por el motor: la unión no pierde ni cambia observaciones."""
    a, b = unzip("UGTO_a.rnx.gz"), unzip("UGTO_b.rnx.gz")
    nav = unzip("ITLA00MEX_R_20262931400_01H_MN.rnx.gz")
    merged = rinex.merge_observations([a, b], tmp_path / "unido.obs").output
    exe = str(tools.find_tool("rnx2rtkp"))

    def solve(files, name):
        out = tmp_path / name
        tools.run([exe, "-p", "0", "-e", "-o", str(out)] + [str(f) for f in files] + [str(nav)], cwd=tmp_path)
        return [line.split()[:5] for line in out.read_text().splitlines() if line and not line.startswith("%")]

    # dos archivos de un mismo receptor se le pasan a RTKLIB con comodín (dos rutas serían rover y base)
    folder = tmp_path / "sueltos"
    folder.mkdir()
    shutil.copy2(a, folder / "UGTO_a.rnx")
    shutil.copy2(b, folder / "UGTO_b.rnx")
    separate = solve([folder / "UGTO_*.rnx"], "sueltos.pos")
    together = solve([merged], "unido.pos")
    assert len(together) == 21 and separate == together
