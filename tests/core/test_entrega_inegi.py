"""Los ZIP horarios tal como los entrega el INEGI: con almanaque y, a veces, una hora dañada en su servidor.

- Los ZIP de IMIE (Trimble NetR9, RINEX 2.10) traen junto a la observación y la navegación un almanaque `.26a`, que
  no es RINEX. Antes se rechazaba todo el conjunto («No se reconocen estos archivos»); ahora se ignora con una nota.
- Un ZIP dañado (el `MERI196m_304.zip` del 15-07-2026 tiene la cabecera del primer archivo rota: «Bad magic number»)
  tumbaba la estación entera; ahora se dejan fuera esas observaciones, se usan las otras horas y se avisa del hueco.
"""
from __future__ import annotations

import zipfile

import pytest

from motor_util import DATA
from tresvizo_ppk.core.observations import prepare_observations
from tresvizo_ppk.errors import UserError

# almanaque con la forma del .26a de los ZIP de IMIE (columnas por satélite, sin encabezado RINEX)
ALMANAC = "\n".join(["         1         2         3         4         5         6",
                     "         0         0         0         0         0         0",
                     "  0.001821  0.016897  0.006974  0.003805  0.005608  0.003694",
                     "    5153.6    5153.7    5153.7    5153.6    5153.7    5153.7"]) + "\n"


def _zip(path, members: dict):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _hours(tmp_path, unzip):
    a = unzip("UGTO_a.rnx.gz", "UGTO293o.26o").read_bytes()
    b = unzip("UGTO_b.rnx.gz", "UGTO293p.26o").read_bytes()
    nav = unzip("ITLA00MEX_R_20262931400_01H_MN.rnx.gz", "nav.rnx").read_bytes()
    za = _zip(tmp_path / "UGTO293o.zip", {"UGTO293O.26a": ALMANAC, "UGTO293O.26o": a, "UGTO293O.26n": nav})
    zb = _zip(tmp_path / "UGTO293p.zip", {"UGTO293P.26a": ALMANAC, "UGTO293P.26o": b})
    return za, zb


def _damage(path):
    """Rompe la firma de la cabecera local del primer archivo, como en el ZIP dañado del INEGI."""
    data = bytearray(path.read_bytes())
    data[0:4] = b"\0\0\0\0"
    path.write_bytes(bytes(data))
    return path


def test_almanac_is_ignored_with_a_note(tmp_path, unzip):
    za, zb = _hours(tmp_path, unzip)
    obs = prepare_observations([za, zb], tmp_path / "w")
    assert obs.epochs == 21                         # 13 + 11 épocas, 3 repetidas
    assert len(obs.nav) == 1
    note = [w for w in obs.warnings if "Se ignoran" in w]
    assert note and "UGTO293O.26a" in note[0] and "almanaques" in note[0]


def test_many_ignored_files_are_listed_short(tmp_path, unzip):
    a = unzip("UGTO_a.rnx.gz", "x.26o").read_bytes()
    members = {"UGTO293O.26o": a} | {f"UGTO293{c}.26a": ALMANAC for c in "ABCDEFG"}
    obs = prepare_observations([_zip(tmp_path / "x.zip", members)], tmp_path / "w")
    note = next(w for w in obs.warnings if "Se ignoran" in w)
    assert note.endswith("y 3 más.")


def test_only_unrecognized_files_is_still_an_error(tmp_path):
    z = _zip(tmp_path / "solo.zip", {"IMIE196A.26a": ALMANAC})
    with pytest.raises(UserError, match="No se reconocen"):
        prepare_observations([z], tmp_path / "w")


def test_damaged_hour_is_left_out_and_the_gap_reported(tmp_path, unzip):
    za, zb = _hours(tmp_path, unzip)
    _damage(zb)
    obs = prepare_observations([za, zb], tmp_path / "w")
    assert obs.epochs == 13                         # solo la primera hora
    gap = [w for w in obs.warnings if "UGTO293p.zip" in w]
    assert gap and "dañado" in gap[0] and "hueco" in gap[0]
    # lo que dejó a medias el ZIP dañado no queda entre los archivos de entrada
    assert not list((tmp_path / "w" / "entrada").glob("UGTO293P*"))


def test_damaged_only_file_is_an_error(tmp_path, unzip):
    _, zb = _hours(tmp_path, unzip)
    with pytest.raises(UserError, match="dañado"):
        prepare_observations([_damage(zb)], tmp_path / "w")


def test_real_damaged_inegi_zip_message(tmp_path):
    """El mensaje con el ZIP dañado real del INEGI, si está en la carpeta de datos (no va en el repo)."""
    from pathlib import Path
    real = Path.home() / "Documents/TresVizo-Field-PPK-datos/rgna/danados_inegi/MERI196m_304.zip"
    if not real.is_file():
        pytest.skip("sin el ZIP dañado del INEGI")
    good = DATA / "UGTO_a.rnx.gz"
    obs = prepare_observations([good, real], tmp_path / "w")
    assert any("MERI196m_304.zip" in w and "hueco" in w for w in obs.warnings)
