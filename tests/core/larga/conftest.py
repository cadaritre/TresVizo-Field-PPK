from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent / "datos"
CORE_DATA = Path(__file__).resolve().parents[1] / "data"


def unpack(name: str, folder: Path, source: Path = DATA) -> Path:
    target = folder / name[:-3]
    if not target.is_file():
        with gzip.open(source / name, "rb") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return target


@pytest.fixture(scope="session")
def larga_data(tmp_path_factory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("larga")
    names = {"ugto": "UGTO00MEX_R_20262930000_01H_30S_MO.rnx.gz", "uqro": "UQRO00MEX_R_20262930000_01H_30S_MO.rnx.gz",
             "nav": "UQRO00MEX_R_20262930000_01H_MN.rnx.gz", "sp3": "ESA0OPSFIN_20262930000_02H_05M_ORB.SP3.gz"}
    out = {k: unpack(v, folder) for k, v in names.items()}
    for k, v in {"itla": "ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz", "itla_nav": "ITLA00MEX_R_20262931400_01H_MN.rnx.gz",
                 "ineg_v2": "INEG2930.26o.gz", "ineg_nav_v2": "INEG2930.26n.gz"}.items():
        out[k] = unpack(v, folder, CORE_DATA)
    return out
