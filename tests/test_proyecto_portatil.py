"""Proyecto portátil: tamaño y limpieza de la carpeta de trabajo, buscar archivos movidos y empaquetar (ZIP)."""
import json
import os
import zipfile
from pathlib import Path

import pytest

from tresvizo_ppk import project as proj
from tresvizo_ppk.model import Ephemeris, ProcessingOptions, Project


def make(tmp_path, folder_name='datos'):
    data = tmp_path/folder_name
    (data/'sub').mkdir(parents=True)
    rover = data/'UGTO293m.zip'
    rover.write_bytes(b'PK rover ' * 100)
    base = data/'sub'/'uqro2930.26d.Z'
    base.write_bytes(b'rinex base ' * 50)
    sp3 = data/'ESA0OPSFIN_ORB.SP3'
    sp3.write_bytes(b'orbitas ' * 30)
    atx = data/'mi_antena.atx'
    atx.write_bytes(b'ANTEX importado ' * 10)
    csv = data/'jornada.csv'
    csv.write_text('Name,Code\nP1,PT\n', encoding='utf-8')
    p = Project(kind='points', point_name='J1',
                options=ProcessingOptions(ephemeris=Ephemeris.PRECISE, precise_files=[sp3], antex_files=[atx]))
    files = {'rover': [rover], 'base:UQRO': [base], 'csv': [csv], 'precise': [sp3], 'antex': [atx]}
    return p, files


def saved(tmp_path, folder_name='datos'):
    p, files = make(tmp_path, folder_name)
    projects = tmp_path/'proyectos'
    proj.set_inputs(p, files, projects)
    path = proj.save(p, projects/'Jornada sur', '0.1.0')
    return path, files


def test_sizes_are_saved_and_read(tmp_path):
    path, files = saved(tmp_path)
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['sizes']['../datos/UGTO293m.zip'] == 900
    assert proj.load(path).sizes == raw['sizes']


def work_tree(path: Path):
    work = path.with_name(path.stem+'.trabajo')
    files = {
        'rover/UGTO.obs': 4000, 'rover/entrada/UGTO_MN.rnx': 300, 'UQRO/UQRO_libre.pos': 200,
        'UQRO/UQRO_libre.pos.stat': 1500, 'UQRO/UQRO_libre.conf': 30, 'productos/igs20_proyecto.atx': 2500,
        'productos/igs20_proyecto.atx.firma': 65, 'trayectoria.pos': 100, 'Informe Jornada sur.pdf': 700,
        'Bitácora del proceso.txt': 50, 'efemerides/precisas/ESA_ORB.SP3': 800}
    for rel, size in files.items():
        f = work/rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'x'*size)
    return work, files


def test_size_and_clean_intermediates_keep_report_log_and_downloads(tmp_path):
    path, _ = saved(tmp_path)
    work, files = work_tree(path)
    os.link(work/'rover'/'UGTO.obs', work/'UQRO'/'enlace.obs')       # un enlace duro se cuenta una vez
    size = proj.project_size(path)
    kept = {'Informe Jornada sur.pdf', 'Bitácora del proceso.txt', 'efemerides/precisas/ESA_ORB.SP3'}
    assert size.total == path.stat().st_size + sum(files.values())
    assert size.intermediates == sum(v for k, v in files.items() if k not in kept)
    freed = proj.clean_intermediates(path)
    assert freed == size.intermediates
    left = sorted(p.relative_to(work).as_posix() for p in work.rglob('*') if p.is_file())
    assert left == sorted(kept)
    assert sorted(p.name for p in work.iterdir()) == ['Bitácora del proceso.txt', 'Informe Jornada sur.pdf',
                                                     'efemerides']
    assert proj.project_size(path).intermediates == 0
    assert path.is_file() and proj.load(path).point_name == 'J1'


def test_clean_without_workdir_is_harmless(tmp_path):
    path, _ = saved(tmp_path)
    assert proj.clean_intermediates(path) == 0
    assert proj.project_size(path).total == path.stat().st_size


def test_relink_finds_moved_and_renamed_files_by_sha256(tmp_path):
    path, files = saved(tmp_path)
    old = tmp_path/'datos'
    new = tmp_path/'disco externo'/'campaña 2026'
    new.parent.mkdir()
    old.rename(new)
    (new/'ESA0OPSFIN_ORB.SP3').rename(new/'orbita renombrada.sp3')       # renombrado: se halla por tamaño
    decoy = tmp_path/'disco externo'/'otra'/'UGTO293m.zip'                  # mismo nombre, otro contenido
    decoy.parent.mkdir()
    decoy.write_bytes(b'PK otro ' * 112 + b'P')
    assert decoy.stat().st_size == 897
    project = proj.load(path)
    assert len(proj.missing_inputs(project)) == 5
    result = proj.relink(project, tmp_path/'disco externo')
    assert result.missing == [] and len(result.found) == 5
    assert result.found['../datos/UGTO293m.zip'] == (new/'UGTO293m.zip').resolve()
    assert project.options.precise_files == [(new/'orbita renombrada.sp3').resolve()]
    assert project.options.antex_files == [(new/'mi_antena.atx').resolve()]
    proj.save(project, path)
    again = proj.load(path)
    assert proj.check_inputs(again) == [] and proj.missing_inputs(again) == []
    assert again.options.precise_files == [(new/'orbita renombrada.sp3').resolve()]
    assert set(again.hashes) == {k for v in again.inputs.values() for k in v}
    assert set(again.sizes) == set(again.hashes)


def test_relink_rejects_same_name_with_other_content(tmp_path):
    path, _ = saved(tmp_path)
    (tmp_path/'datos'/'UGTO293m.zip').unlink()
    other = tmp_path/'otra'
    other.mkdir()
    (other/'UGTO293m.zip').write_bytes(b'PK rover ' * 99 + b'PK rovex ')   # mismo tamaño, otra huella
    project = proj.load(path)
    result = proj.relink(project, other)
    assert result.found == {} and result.missing == ['../datos/UGTO293m.zip']
    assert project.inputs['rover'] == ['../datos/UGTO293m.zip']


def test_pack_and_open_elsewhere(tmp_path):
    path, files = saved(tmp_path)
    work, _ = work_tree(path)
    zip_path = proj.pack(path, tmp_path/'entrega'/'Jornada sur', '0.1.0')
    assert zip_path.name == 'Jornada sur.zip'
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
    assert 'paquete.json' in names and 'Jornada sur/Jornada sur.tvppk' in names
    assert 'Jornada sur/datos/rover/UGTO293m.zip' in names
    assert 'Jornada sur/datos/base-UQRO/uqro2930.26d.Z' in names
    assert 'Jornada sur/datos/efemerides/ESA0OPSFIN_ORB.SP3' in names
    assert 'Jornada sur/datos/antex/mi_antena.atx' in names
    assert 'Jornada sur/datos/csv-de-campo/jornada.csv' in names
    assert 'Jornada sur/Jornada sur.trabajo/Informe Jornada sur.pdf' in names
    assert 'Jornada sur/Jornada sur.trabajo/efemerides/precisas/ESA_ORB.SP3' in names
    assert not any(n.endswith(('.obs', '.pos', '.stat', '.conf', '.firma')) or '/productos/' in n for n in names)
    # se abre en otro equipo (otra carpeta), sin los datos originales
    for f in tmp_path.joinpath('datos').rglob('*'):
        if f.is_file():
            f.unlink()
    opened = proj.unpack(zip_path, tmp_path/'otro equipo')
    assert opened == tmp_path/'otro equipo'/'Jornada sur'/'Jornada sur.tvppk'
    project = proj.load(opened)
    assert proj.check_inputs(project) == []
    assert project.inputs['rover'] == ['datos/rover/UGTO293m.zip']
    assert project.options.precise_files == [(opened.parent/'datos'/'efemerides'/'ESA0OPSFIN_ORB.SP3').resolve()]
    assert project.options.antex_files == [(opened.parent/'datos'/'antex'/'mi_antena.atx').resolve()]
    assert (opened.parent/'Jornada sur.trabajo'/'Bitácora del proceso.txt').is_file()
    # abrirlo otra vez no pisa el primero
    second = proj.unpack(zip_path, tmp_path/'otro equipo')
    assert second == tmp_path/'otro equipo'/'Jornada sur (2)'/'Jornada sur.tvppk'
    assert proj.check_inputs(proj.load(second)) == []


def test_pack_refuses_missing_inputs(tmp_path):
    path, files = saved(tmp_path)
    files['csv'][0].unlink()
    with pytest.raises(proj.ProjectError, match='Buscar los archivos'):
        proj.pack(path, tmp_path/'p.zip')
    assert not (tmp_path/'p.zip').exists() and not list(tmp_path.glob('.paquete-*'))


def tamper(zip_path: Path, out: Path, member: str, data: bytes | None = None, extra: tuple[str, bytes] | None = None):
    with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(out, 'w') as dst:
        for info in src.infolist():
            content = src.read(info.filename)
            if info.filename == member and data is not None:
                content = data
            dst.writestr(info.filename, content)
        if extra:
            dst.writestr(*extra)
    return out


def test_open_rejects_damaged_or_foreign_packages(tmp_path):
    path, _ = saved(tmp_path)
    zip_path = proj.pack(path, tmp_path/'j.zip')
    dest = tmp_path/'destino'
    bad = tamper(zip_path, tmp_path/'dañado.zip', 'Jornada sur/datos/rover/UGTO293m.zip', b'PK cambiado')
    with pytest.raises(proj.ProjectError, match='dañado'):
        proj.unpack(bad, dest)
    assert not dest.exists() or list(dest.iterdir()) == []
    extra = tamper(zip_path, tmp_path/'extra.zip', '', extra=('../fuera.txt', b'x'))
    with pytest.raises(proj.ProjectError):
        proj.unpack(extra, dest)
    assert not (tmp_path/'fuera.txt').exists()
    plain = tmp_path/'otro.zip'
    with zipfile.ZipFile(plain, 'w') as z:
        z.writestr('algo.txt', 'hola')
    with pytest.raises(proj.ProjectError, match='no es un paquete'):
        proj.unpack(plain, dest)
    (tmp_path/'no-zip.zip').write_text('texto', encoding='utf-8')
    with pytest.raises(proj.ProjectError, match='no es un paquete'):
        proj.unpack(tmp_path/'no-zip.zip', dest)
