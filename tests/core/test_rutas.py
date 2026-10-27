"""RTKLIB con rutas que no son ASCII (proyecto «Vértice…», carpeta del usuario con acentos) y con órbitas precisas
comprimidas (.gz): corre en una carpeta de nombres ASCII con rutas relativas (rtklib.Stage)."""
from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import pytest

from motor_util import DATA, needs_tools
from tresvizo_ppk.core import api, rtklib
from tresvizo_ppk.model import Ephemeris, ProcessingOptions, Solution

LARGA = Path(__file__).resolve().parent / "larga" / "datos"


def test_ascii_names():
    assert rtklib.ascii_name("Vértice Ñandú 1.26O") == "Vertice_Nandu_1.26O"
    assert rtklib.ascii_name("añ/ó\\x") == "an_o_x"
    assert rtklib.ascii_name("ñ") == "n" and rtklib.ascii_name("…") == "archivo"


def test_stage_rewrites_config_and_decompresses(tmp_path):
    folder = tmp_path / "Proyecto «Vértice» año"
    folder.mkdir()
    antex = folder / "antenas ñ.atx"
    antex.write_text("ANTEX", encoding="ascii")
    conf = folder / "línea.conf"
    cfg = rtklib.RtkConfig()
    cfg.set("pos1-posmode", "static")
    cfg.set("file-satantfile", str(antex))
    cfg.set("file-rcvantfile", str(antex))
    cfg.set("file-dcbfile", "")
    cfg.write(conf)
    sp3 = folder / "órbita.SP3.gz"
    with gzip.open(sp3, "wb") as handle:
        handle.write(b"#dP2026 10 20\n")
    stage = rtklib.Stage(folder, "línea")
    name = stage.config(conf)
    text = (stage.dir / name).read_text(encoding="utf-8")
    values = {k.strip(): v for k, v in (line.split("=", 1) for line in text.splitlines() if "=" in line)}
    for key in ("file-satantfile", "file-rcvantfile"):
        rel = values[key].strip()
        assert rel.isascii() and not Path(rel).is_absolute() and (stage.dir / rel).read_text() == "ANTEX"
    assert values["file-dcbfile"].strip() == ""
    pattern = stage.folder([sp3], "órbitas")
    assert pattern.isascii() and pattern.endswith("*")
    unpacked = list((stage.dir / pattern[:-2]).iterdir())
    assert len(unpacked) == 1 and unpacked[0].name.endswith(".SP3") and unpacked[0].read_bytes().startswith(b"#dP")
    stage.close()
    assert not stage.dir.exists()


@needs_tools
def test_static_in_non_ascii_project_folder(tmp_path):
    root = tmp_path / "José Ñúñez" / "Vértice 1 – año 2026"
    rover = api.prepare_observations([DATA / "ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz",
                                      DATA / "ITLA00MEX_R_20262931400_01H_MN.rnx.gz"], root / "rover")
    base = api.prepare_observations([DATA / "ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz"], root / "base")
    station = api.find_station("ICEP")
    result = api.process_static(rover, [(station, base)], ProcessingOptions(), root / "proceso")
    b = result.baselines[0]
    assert b.solution in (Solution.FIXED, Solution.FLOAT) and b.stat_file.is_file()
    assert not any(line.strip().lower().startswith(("error", "pcv file")) for line in result.log)
    assert not any(p.name.startswith("_rtklib_") for p in (root / "proceso").rglob("*"))   # se limpia


@needs_tools
def test_compressed_precise_orbits(tmp_path):
    rover = api.prepare_observations([LARGA / "UGTO00MEX_R_20262930000_01H_30S_MO.rnx.gz",
                                      LARGA / "UQRO00MEX_R_20262930000_01H_MN.rnx.gz"], tmp_path / "Órbitas" / "r")
    base = api.prepare_observations([LARGA / "UQRO00MEX_R_20262930000_01H_30S_MO.rnx.gz"], tmp_path / "Órbitas" / "b")
    sp3 = tmp_path / "Órbitas" / "ESA0OPSFIN_20262930000_02H_05M_ORB.SP3.gz"
    shutil.copy2(LARGA / sp3.name, sp3)
    options = ProcessingOptions(ephemeris=Ephemeris.PRECISE, precise_files=[sp3])
    result = api.process_static(rover, [(api.find_station("UQRO"), base)], options, tmp_path / "Órbitas" / "p")
    b = result.baselines[0]
    assert b.stat_file is not None and b.stat_file.is_file()        # la libre de ionosfera de RTKLIB con el SP3
    assert b.engine == "TresVizo" and b.solution in (Solution.FIXED, Solution.FLOAT)
    assert any(line.startswith("Órbitas precisas:") for line in result.log)
    assert not any("falló" in w for w in result.warnings)
