from pathlib import Path

from tresvizo_ppk.core import quality, rinex
from tresvizo_ppk.model import ObservationSet


def _assess(path: Path):
    return quality.assess(ObservationSet(obs=[path]))


def test_quality_of_clean_file(unzip):
    q = _assess(unzip("ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz"))
    assert q.epochs == 81 and q.expected_epochs == 81 and q.gaps == []
    assert any(s.startswith("G") for s in q.satellites) and any(s.startswith("E") for s in q.satellites)
    assert "L1C" in q.signals["GPS"] and "L2W" in q.signals["GPS"]
    assert sum(q.cycle_slips.values()) <= 2
    # multitrayectoria típica de una estación geodésica: decímetros
    assert 0.05 < q.multipath_m["GPS C1C"] < 1.0
    assert not any("saltos" in w for w in q.warnings)


def test_gap_and_injected_cycle_slip(tmp_path, unzip):
    src = unzip("ITLA00MEX_R_20262931400_20M_15S_MO.rnx.gz")
    h = rinex.read_header(src)
    blocks = list(rinex.iter_epochs(h))
    # hueco de 2 minutos y un salto de 10 ciclos en L1C de un satélite a la mitad
    keep = [b for k, b in enumerate(blocks) if not (20 <= k < 28)]
    target = next(s for s in rinex.epoch_values(h, blocks[0]) if s.startswith("G"))
    l1 = h.obs_types["G"].index("L1C")
    lines = list(h.lines)
    for k, block in enumerate(keep):
        out = list(block.lines)
        if k >= 50:
            for i, row in enumerate(out[1:], start=1):
                if row.startswith(target):
                    start = 3 + 16 * l1
                    value = float(row[start:start + 14]) + 10.0
                    out[i] = row[:start] + f"{value:14.3f}" + row[start + 14:]
        lines += out
    bad = tmp_path / "con_hueco.rnx"
    bad.write_text("\n".join(lines) + "\n", encoding="latin-1")
    q = _assess(bad)
    assert q.epochs == 73 and q.expected_epochs == 81
    assert len(q.gaps) == 1
    gap_start, gap_end = q.gaps[0]
    assert round((gap_end - gap_start).total_seconds()) == 105          # 8 épocas faltantes: de la 1.ª a la 8.ª
    assert q.cycle_slips.get(target, 0) >= 1
    assert any("hueco" in w for w in q.warnings)


def test_overlap_warnings():
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 10, 20, 14, tzinfo=timezone.utc)
    rover = ObservationSet(obs=[], start=t0, end=t0 + timedelta(hours=2), interval_s=1.0)
    base = ObservationSet(obs=[], start=t0 + timedelta(hours=1), end=t0 + timedelta(hours=5), interval_s=15.0)
    warnings = quality.overlap_warnings(rover, base, "UQRO")
    assert any("50 %" in w for w in warnings) and any("15 s" in w for w in warnings)
    far = ObservationSet(obs=[], start=t0 + timedelta(hours=3), end=t0 + timedelta(hours=4))
    assert any("no tiene datos" in w for w in quality.overlap_warnings(rover, far, "UQRO"))
