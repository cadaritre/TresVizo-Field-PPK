"""Intermedios livianos con RTKLIB de verdad: un solo juego de productos, nada de ventanas ni RINEX de las bases al
terminar, y limpiar y volver a procesar da la misma coordenada (ITLA–ICEP, 30.7 km, 20 min, 20-10-2026)."""
import shutil
from pathlib import Path

import numpy as np
import pytest

from tresvizo_ppk import project as proj
from tresvizo_ppk.core import api, intermedios
from tresvizo_ppk.model import Ephemeris, FieldPoint, ProcessingOptions

from motor_util import DATA, needs_tools

ROVER = [DATA / "ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz", DATA / "ITLA00MEX_R_20262931400_01H_MN.rnx.gz"]
BASE = [DATA / "ICEP00MEX_R_20262931400_20M_15S_MO.rnx.gz"]
LARGA = Path(__file__).resolve().parent / "larga" / "datos"


def static_like_app(sources: Path, work: Path, options=None):
    """Lo que hace la app (static_ui.work): rover y bases preparados dentro de la carpeta de trabajo."""
    rover = api.prepare_observations([sources / p.name for p in ROVER], work / "rover")
    base = api.prepare_observations([sources / p.name for p in BASE], work / "base-ICEP", marker="ICEP")
    return api.process_static(rover, [(api.find_station("ICEP"), base)], options or ProcessingOptions(), work,
                              point_name="ITLA")


def files(work: Path) -> list[str]:
    return sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file())


@needs_tools
def test_static_leaves_only_what_the_result_needs_and_reprocesses_identically(tmp_path):
    sources = tmp_path / "datos"
    sources.mkdir()
    for p in ROVER + BASE:
        shutil.copy2(p, sources / p.name)
    path = tmp_path / "ITLA.tvppk"
    project = proj.Project(kind="static", point_name="ITLA")
    proj.set_inputs(project, {"rover": [sources / p.name for p in ROVER],
                              "base:ICEP": [sources / p.name for p in BASE]}, tmp_path)
    proj.save(project, path)
    work = proj.workdir(project, path)
    first = static_like_app(sources, work)
    left = files(work)
    assert not any(f.endswith("_ventana.obs") for f in left), left
    assert "base-ICEP/ICEP.obs" not in left and not any(f.startswith("base-ICEP/") for f in left), left
    assert not any("/entrada/" in f and "_MO" in f for f in left), left           # lo descomprimido antes de unir
    atx = [f for f in left if f.endswith(".atx")]
    assert atx == [f for f in atx if f.startswith("productos/")] and len(atx) == 1, atx
    assert not any(f.startswith("_rtklib_") or "orbitas" in f for f in left), left
    assert "rover/ITLA.obs" in left and all(Path(p).is_file() for p in first.rover.obs + first.rover.nav)
    assert intermedios.LOG_NAME in left
    log = (work / intermedios.LOG_NAME).read_text(encoding="utf-8")
    assert "RTKLIB-EX 2.5.1" in log and "Avisos:" in log
    b = first.baselines[0]
    assert b.stat_file.is_file() and b.config_file.is_file()
    # nada fuera de la carpeta de trabajo ni de lo que entregó el usuario
    assert sorted(p.name for p in sources.iterdir()) == sorted(p.name for p in ROVER + BASE)
    (work / "Informe ITLA.pdf").write_bytes(b"%PDF-1.4 informe")
    freed = proj.clean_intermediates(path)
    assert freed > 0 and files(work) == sorted([intermedios.LOG_NAME, "Informe ITLA.pdf"])
    second = static_like_app(sources, work)
    a = np.array([first.position.x, first.position.y, first.position.z])
    c = np.array([second.position.x, second.position.y, second.position.z])
    assert np.abs(a - c).max() < 1e-4                                              # 0.1 mm
    assert (work / "Informe ITLA.pdf").is_file()


@needs_tools
def test_compressed_orbits_are_unpacked_once_and_removed(tmp_path):
    work = tmp_path / "p.trabajo"
    rover = api.prepare_observations([LARGA / "UGTO00MEX_R_20262930000_01H_30S_MO.rnx.gz",
                                      LARGA / "UQRO00MEX_R_20262930000_01H_MN.rnx.gz"], work / "rover")
    base = api.prepare_observations([LARGA / "UQRO00MEX_R_20262930000_01H_30S_MO.rnx.gz"], work / "base-UQRO")
    sp3 = tmp_path / "ESA0OPSFIN_20262930000_02H_05M_ORB.SP3.gz"
    shutil.copy2(LARGA / sp3.name, sp3)
    options = ProcessingOptions(ephemeris=Ephemeris.PRECISE, precise_files=[sp3])
    result = api.process_static(rover, [(api.find_station("UQRO"), base)], options, work)
    left = files(work)
    assert not any(f.upper().endswith(".SP3") for f in left), left                # descomprimidas: se borran
    assert not any("orbitas" in f for f in left), left
    assert result.precise_files == [sp3.name] and sp3.is_file()
    assert any(line.startswith("Órbitas precisas:") for line in result.log)


@needs_tools
def test_points_leave_no_base_window(tmp_path):
    from datetime import datetime, timezone
    work = tmp_path / "j.trabajo"
    rover = api.prepare_observations(ROVER, work / "rover")
    base = api.prepare_observations(BASE, work / "base")
    point = FieldPoint(name="P1", code="", start=datetime(2026, 10, 20, 14, 2, tzinfo=timezone.utc),
                       end=datetime(2026, 10, 20, 14, 5, tzinfo=timezone.utc), antenna_height_m=0.0,
                       total_height_m=0.067)
    result = api.process_points(rover, (api.find_station("ICEP"), base), [point], ProcessingOptions(), work)
    left = files(work)
    assert not any(f.startswith("base/") for f in left), left
    assert "trayectoria.pos" in left and "trayectoria.pos.stat" in left and intermedios.LOG_NAME in left
    assert [f for f in left if f.endswith(".atx")] == [f for f in left if f.startswith("productos/") and
                                                        f.endswith(".atx")]
    assert result.pos_file.is_file() and result.points[0].epochs_total > 0


def test_discard_never_leaves_the_workdir(tmp_path):
    from tresvizo_ppk.model import ObservationSet
    outside = tmp_path / "fuera" / "base.obs"
    outside.parent.mkdir()
    outside.write_text("x")
    (tmp_path / "fuera" / "entrada").mkdir()
    (tmp_path / "fuera" / "entrada" / "hora.rnx").write_text("x")
    work = tmp_path / "w"
    (work / "base" / "entrada").mkdir(parents=True)
    mine = work / "base" / "B.obs"
    mine.write_text("y")
    given = work / "base" / "entrada" / "del_usuario.rnx"
    given.write_text("z")
    drop = [ObservationSet(obs=[outside]), ObservationSet(obs=[mine], source_files=[given])]
    intermedios.discard(work, keep=[], drop=drop)
    assert outside.is_file() and (tmp_path / "fuera" / "entrada" / "hora.rnx").is_file()
    assert not mine.exists() and given.is_file()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
