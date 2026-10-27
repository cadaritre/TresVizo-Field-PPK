"""Antenas: el ANTEX incluido completo, los importados, la elección por extremo, la altura con su método de medida
y los avisos de antena sin modelo (core/antex.py, core/antenas.py)."""
import math
from pathlib import Path

import numpy as np
import pytest

from tresvizo_ppk.core import antenas, antex, api
from tresvizo_ppk.errors import UserError
from tresvizo_ppk.model import AntennaSelection, ObservationSet, ProcessingOptions

from motor_util import DATA, needs_tools

HEADER = ("     1.4            M                                       ANTEX VERSION / SYST\n"
          "A                                                           PCV TYPE / REFANT\n"
          "                                                            END OF HEADER\n")


def lab_antex(folder: Path, name: str, *, source=("TRM115000.00", "NONE"), up_mm: float = 50.0) -> Path:
    """Un ANTEX «de laboratorio»: el bloque incluido de `source` con otro nombre (o el mismo) y el PCO vertical de
    G01 y G02 subido `up_mm`; las PCV quedan iguales."""
    out, freq = [], ""
    for line in antex.receiver_block(*source).splitlines(keepends=True):
        label = line[60:].strip()
        if label == "TYPE / SERIAL NO":
            line = name.ljust(16)[:16] + source[1].ljust(4) + line[20:]
        elif label == "START OF FREQUENCY":
            freq = line[3:6]
        elif label == "NORTH / EAST / UP" and freq in ("G01", "G02"):
            line = line[:20] + f"{float(line[20:30]) + up_mm:10.2f}" + line[30:]
        out.append(line)
    path = folder / f"{name.lower()}.atx"
    path.write_text(HEADER + "".join(out), encoding="latin-1")
    return path


# --- ANTEX incluido ---------------------------------------------------------------------------------------------

def test_bundled_antex_has_every_receiver_antenna():
    models = {m.key: m for m in antex.catalog()}
    index = antex.bundled_index()
    assert len(models) == index["receptores"] >= 1100
    assert len(index["receptores_ngs"]) > 600 and index["satelites"] > 400
    # otras marcas, del ANTEX de la IGS
    for key in [("CHCI80", "NONE"), ("EML_REACH_RS2", "NONE"), ("LEIGS18", "NONE"), ("STXS900A", "NONE"),
                ("TRM115000.00", "SCIT"), ("JAVTRIUMPH_2A", "NONE")]:
        assert models[key].source == "IGS (igs20_2437)" and models[key].frequencies["G01"][2] > 0.01, key
    # del NGS, las que la IGS no trae
    assert models[("ASH111661", "NONE")].source == "NGS (ngs20.atx)"
    assert antex.bundled_antex().stat().st_size < 10e6
    assert (antex.DATA_DIR / "ORIGEN.md").is_file()


def test_rtklib_file_has_only_the_needed_receivers(tmp_path):
    with antex.session([], [("CHCI80", "NONE"), ("TRM115000.00", "SCIT"), ("NOEXISTE1", "NONE")]):
        text = antex.ensure_antex(tmp_path).read_text(encoding="latin-1")
    receivers = [line[:20] for line in text.splitlines()
                 if line[60:].strip() == "TYPE / SERIAL NO" and not line[20:40].strip()]
    assert receivers == ["CHCI80          NONE", "TRM115000.00    SCIT"]
    assert text.count("START OF ANTENNA") > 400                    # todas las de satélite


def test_rtklib_file_without_session_is_sorted_for_strstr(tmp_path):
    text = antex.ensure_antex(tmp_path / "todo").read_text(encoding="latin-1")
    types = [line[0:16].strip() for line in text.splitlines()
             if line[60:].strip() == "TYPE / SERIAL NO" and not line[20:40].strip()]
    assert len(types) >= 1100
    # RTKLIB toma la primera antena cuyo nombre contiene el pedido: un tipo nunca va después de otro más largo
    assert [len(t) for t in types] == sorted(len(t) for t in types)


# --- ANTEX importado --------------------------------------------------------------------------------------------

def test_imported_antex_wins_over_bundled(tmp_path):
    lab = lab_antex(tmp_path, "TRM115000.00")                       # mismo tipo y radomo que el incluido
    bundled = antex.choose("TRM115000.00", "NONE").model
    assert bundled.source == "IGS (igs20_2437)"
    with antex.session([lab], [("TRM115000.00", "NONE")]):
        chosen = antex.choose("TRM115000.00", "NONE", "Base").model
        assert chosen.source == "importado (trm115000.00.atx)" and chosen.kind == "imported"
        assert abs(chosen.frequencies["G01"][2] - bundled.frequencies["G01"][2] - 0.050) < 1e-9
        assert antex.receiver_block("TRM115000.00", "NONE").count(f"{(bundled.frequencies['G01'][2] * 1000 + 50):10.2f}")
        text = antex.ensure_antex(tmp_path / "w").read_text(encoding="latin-1")
    assert text.count("TRM115000.00    NONE                                        TYPE / SERIAL NO") == 1
    assert f"{bundled.frequencies['G01'][2] * 1000 + 50:10.2f}" in text
    # fuera de la sesión, otra vez el incluido
    assert antex.choose("TRM115000.00", "NONE").model.source == "IGS (igs20_2437)"
    assert antex.read_antex_file(lab)[0].type == "TRM115000.00"


def test_imported_antex_is_validated(tmp_path):
    bad = tmp_path / "nota.atx"
    bad.write_text("esto no es un ANTEX\n", encoding="latin-1")
    with pytest.raises(antex.AntexError, match="no es un archivo ANTEX"):
        antex.read_antex_file(bad)
    relative = tmp_path / "relativa.atx"
    relative.write_text(HEADER.replace("A      ", "R      ", 1) + antex.receiver_block("CHCI80"), encoding="latin-1")
    with pytest.raises(antex.AntexError, match="relativas"):
        antex.read_antex_file(relative)
    empty = tmp_path / "vacia.atx"
    empty.write_text(HEADER, encoding="latin-1")
    with pytest.raises(antex.AntexError, match="ninguna antena de receptor"):
        antex.read_antex_file(empty)


# --- Altura con su método de medida -----------------------------------------------------------------------------

def test_slant_height_against_hand_calculation():
    # LEIAR10 de la tabla (antenna.gra de la IGS): muesca a 0.0362 m sobre la ARP, ancho 0.2400 m → r = 0.1200
    dims = antenas.slant_dimensions("LEIAR10")
    assert dims["radius_m"] == 0.12 and dims["offset_m"] == 0.0362 and "antenna.gra" in dims["source"]
    sel = AntennaSelection(height_method="slant", measured_m=1.5, slant_radius_m=dims["radius_m"],
                           slant_offset_m=dims["offset_m"])
    # a mano: √(1.5² − 0.12²) = √(2.25 − 0.0144) = √2.2356 = 1.4951923 ; − 0.0362 = 1.4589923
    assert antenas.vertical_height(sel) == pytest.approx(1.4589923, abs=1e-7)
    # Trimble R8s, centro de la defensa: 0.0552 sobre la ARP, ancho 0.1820 → r = 0.0910
    # a mano: √(1.44 − 0.008281) = √1.431719 = 1.1965446 ; − 0.0552 = 1.1413446
    sel = AntennaSelection(height_method="slant", measured_m=1.2, slant_radius_m=0.091, slant_offset_m=0.0552)
    assert antenas.vertical_height(sel) == pytest.approx(math.sqrt(1.44 - 0.008281) - 0.0552, abs=1e-12)
    assert antenas.vertical_height(sel) == pytest.approx(1.1413446, abs=1e-7)
    assert "√(s² − r²) − d = 1.1413 m" in antenas.height_text(sel, antenas.vertical_height(sel))
    with pytest.raises(ValueError, match="mayor que el radio"):
        antenas.vertical_height(AntennaSelection(height_method="slant", measured_m=0.05, slant_radius_m=0.1,
                                                 slant_offset_m=0.0))
    with pytest.raises(ValueError, match="radio"):
        antenas.vertical_height(AntennaSelection(height_method="slant", measured_m=1.5))


def test_mount_and_arp_heights():
    meridian = ObservationSet(obs=[], case_offset_m=0.116)
    sel = AntennaSelection(height_method="mount", measured_m=1.8)
    assert antenas.vertical_height(sel, mount_offset_m=antenas.default_mount_offset(meridian)) == \
        pytest.approx(1.916)
    assert antenas.default_mount_offset(ObservationSet(obs=[])) == 0.0
    sel = AntennaSelection(height_method="mount", measured_m=1.5, mount_offset_m=0.0335)   # gancho de la base nivelante
    assert antenas.vertical_height(sel) == pytest.approx(1.5335)
    assert antenas.vertical_height(AntennaSelection(measured_m=1.234)) == 1.234
    with pytest.raises(ValueError):
        antenas.vertical_height(AntennaSelection(measured_m=-1.0))


def test_rover_selection_applies_antenna_and_height():
    rover = ObservationSet(obs=[], antenna_type="HARXON HA-901A", radome="NONE", antenna_height_m=1.916,
                           case_offset_m=0.116, receiver_model="MeridianV")
    options = ProcessingOptions(antennas={"rover": AntennaSelection(antenna_type="CHCI80", radome="",
                                                                    height_method="slant", measured_m=2.0,
                                                                    slant_radius_m=0.062, slant_offset_m=-0.0015)})
    out, text = antenas.apply_rover(rover, options)
    assert out.antenna_type == "CHCI80" and out.radome == "NONE"
    assert out.antenna_height_m == pytest.approx(math.sqrt(4 - 0.062 ** 2) + 0.0015)
    assert out.field_sources["antenna_type"] == "elegida por el usuario" and "inclinada" in text
    # sin altura medida, la de los datos
    out, _ = antenas.apply_rover(rover, ProcessingOptions(antennas={"rover": AntennaSelection(radome="SCIT")}))
    assert out.antenna_height_m == 1.916 and out.antenna_type == "HARXON HA-901A" and out.radome == "SCIT"


# --- Avisos -----------------------------------------------------------------------------------------------------

def test_unmodelled_antenna_warning_levels():
    red = antenas.info("Base XYZ1", "ZZZSINMODELO", "NONE", height_m=1.0, height_text_="", length_m=12_000)
    assert red.level == "error" and not red.calibrated and "12.0 km" in red.note and red.source == "sin modelo"
    yellow = antenas.info("Base XYZ1", "ZZZSINMODELO", "NONE", height_m=1.0, height_text_="", length_m=3_000)
    assert yellow.level == "warning"
    ok = antenas.info("Base XYZ1", "CHCI80", "NONE", height_m=1.0, height_text_="", length_m=30_000)
    assert ok.level == "ok" and ok.model_type == "CHCI80" and ok.source == "IGS (igs20_2437)"
    # con radomo que no está: el tipo sin radomo
    bare = antenas.info("Rover", "CHCI80", "SCIT", height_m=None, height_text_="", length_m=None)
    assert bare.calibrated and bare.model_radome == "NONE" and "sin radomo" not in bare.note


# --- De punta a punta con RTKLIB ---------------------------------------------------------------------------------

ROVER = [DATA / "ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz", DATA / "ITLA00MEX_R_20262931400_01H_MN.rnx.gz"]
BASE = [DATA / "ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz"]


def _up_mm(result):
    from tresvizo_ppk.core.geodesy import ecef_delta_to_enu
    off = api.find_station("ITLA").position
    origin = np.array([off.x, off.y, off.z])
    p = result.position
    return np.array(ecef_delta_to_enu(origin, np.array([p.x, p.y, p.z]) - origin)) * 1000


@needs_tools
def test_other_brand_base_and_imported_antex_change_the_solution(tmp_path):
    """ITLA–ICEP (30.7 km) con RTKLIB libre de ionosfera: la antena de la base declarada como CHC (CHCI80, del igs20)
    toma su PCO y su PCV; un ANTEX importado con +50 mm de PCO sube el rover 50 mm; sin modelo, aviso rojo."""
    rover = api.prepare_observations(ROVER, tmp_path / "rover")
    base = api.prepare_observations(BASE, tmp_path / "base")
    station = api.find_station(base.marker)
    lab = lab_antex(tmp_path, "ZZZLAB-PRUEBA")

    def run(name, files=()):
        options = ProcessingOptions(ionosphere="iono-free", antex_files=list(files))
        if name:
            options.antennas = {"base:ICEP": AntennaSelection(antenna_type=name, radome="NONE")}
        return api.process_static(rover, [(station, base)], options, tmp_path / f"p_{name}")

    catalog, chc, imported, none = run(None), run("CHCI80"), run("ZZZLAB-PRUEBA", [lab]), run("ZZZSINMODELO")
    assert catalog.baselines[0].engine == "RTKLIB"
    conf = chc.baselines[0].config_file.read_text(encoding="latin-1")
    assert "ant2-anttype      =CHCI80 NONE" in conf
    atx = next(chc.baselines[0].config_file.parent.glob("*.atx")).read_text(encoding="latin-1")
    assert "CHCI80          NONE                                        TYPE / SERIAL NO" in atx
    base_info = {r.role: r for r in chc.antennas}["Base ICEP"]
    assert base_info.source == "IGS (igs20_2437)" and base_info.model_type == "CHCI80"
    # PCO libre de ionosfera de la CHCI80 en la vertical: 2.546·131.04 − 1.546·107.77 = 167 mm; con su PCV el
    # rover sube algo menos (139 mm en esta sesión). La horizontal casi no cambia.
    f = antex.choose("CHCI80").model.frequencies
    pco_if = (2.5457 * f["G01"][2] - 1.5457 * f["G02"][2]) * 1000
    d = _up_mm(chc) - _up_mm(none)
    assert 0.6 * pco_if < d[2] < 1.0 * pco_if, (d, pco_if)
    assert math.hypot(d[0], d[1]) < 10
    # el importado gana y su +50 mm de PCO (misma PCV) sube el rover 50 mm respecto del incluido
    assert {r.role: r for r in imported.antennas}["Base ICEP"].source == "importado (zzzlab-prueba.atx)"
    assert (_up_mm(imported) - _up_mm(catalog))[2] == pytest.approx(50.0, abs=1.0)
    # sin modelo a más de 5 km: aviso rojo en el resultado
    info = {r.role: r for r in none.antennas}["Base ICEP"]
    assert info.level == "error" and not info.calibrated
    assert none.warnings[0] == info.note and "30.7 km" in info.note
    assert any("antena cambiada a mano" in w for w in none.warnings)


@needs_tools
def test_bad_slant_height_is_a_user_error(tmp_path):
    rover = api.prepare_observations(ROVER, tmp_path / "rover")
    base = api.prepare_observations(BASE, tmp_path / "base")
    station = api.find_station(base.marker)
    options = ProcessingOptions(antennas={"rover": AntennaSelection(height_method="slant", measured_m=1.5)})
    with pytest.raises(UserError, match="Altura de la antena del rover"):
        api.process_static(rover, [(station, base)], options, tmp_path / "p")


# --- Informe ----------------------------------------------------------------------------------------------------

def test_report_says_which_antenna_model_and_source(tmp_path):
    import re
    import sys
    from pypdf import PdfReader
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import sinteticos as s
    from tresvizo_ppk.report import api as report

    def text(pdf):
        return re.sub(r"\s+", " ", " ".join(p.extract_text() or "" for p in PdfReader(str(pdf)).pages))

    result = s.static_result(tmp_path)
    sel = AntennaSelection(height_method="slant", measured_m=1.5, slant_radius_m=0.12, slant_offset_m=0.0362)
    result.antennas = [
        antenas.info("Rover", "CHCI80", "NONE", height_m=1.459, length_m=12_000,
                     height_text_=antenas.height_text(sel, antenas.vertical_height(sel))),
        antenas.info("Base ICEP", "ZZZSINMODELO", "NONE", height_m=0.121, height_text_="vertical a la ARP 0.1210 m",
                     length_m=12_000)]
    t = text(report.static_report(result, s.static_project(tmp_path), s.user(), tmp_path / "e.pdf"))
    assert "Modelo aplicado" in t and "IGS (igs20_2437)" in t and "sin modelo (PCO/PCV 0)" in t
    assert "inclinada al borde s = 1.5000 m" in t
    assert "Antena sin modelo de centro de fase en una línea de más de 5 km" in t
    points = s.points_result(tmp_path)
    points.antennas = [antenas.info("Base X", "ASH111661", "NONE", height_m=1.0, height_text_="vertical a la ARP",
                                    length_m=800)]
    t = text(report.points_report(points, s.points_project(tmp_path), s.user(), tmp_path / "p.pdf"))
    assert "NGS (ngs20.atx)" in t and "más de 5 km" not in t
