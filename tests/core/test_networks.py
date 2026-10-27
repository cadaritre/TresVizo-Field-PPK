import copy
import http.server
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tresvizo_ppk.core import antex, networks, precise
from tresvizo_ppk.errors import UserError

RGNA = json.loads((networks.BUNDLED_DIR / "rgna-inegi.json").read_text(encoding="utf-8"))


def _other_network(url: str = "https://example.org/rinex/{yyyy}/{doy}/{id}{doy}1.zip") -> dict:
    """Una red de otro país, como se publicaría: marco y época propios y descarga por plantilla."""
    return {
        "format": "tresvizo-cors-network", "formatVersion": 1, "id": "red-prueba", "revision": 3,
        "title": "Red de prueba — Brasil", "shortName": "PRB", "countries": ["BR"], "authority": "Prueba",
        "frame": {"name": "SIRGAS2000", "epoch": 2000.4, "ellipsoid": "GRS80"},
        "license": "libre", "attribution": "Fuente: prueba",
        "download": {"type": "url-template", "granularity": "daily", "url": url},
        "stations": [{"id": "BRAZ", "name": "Brasília", "latitude": -15.947, "longitude": -47.878, "height": 1106.0,
                      "antennaHeight": 0.008, "antennaType": "LEIAR10", "radome": "NONE",
                      "notes": ["aviso de prueba"]}],
    }


@pytest.fixture
def installed(tmp_path, monkeypatch):
    folder = tmp_path / "cors-network"
    folder.mkdir()
    monkeypatch.setattr(networks, "_installed_dir", lambda: folder)
    return folder


def test_bundled_rgna_package():
    rgna = networks.network("rgna-inegi")
    assert rgna is not None and rgna.bundled and rgna.frame == "ITRF2008" and rgna.epoch == 2010.0
    assert rgna.countries == ["MX"] and rgna.attribution.startswith("Fuente: INEGI")
    stations = networks.station_catalog("rgna-inegi")
    assert len(stations) == 31
    ugto = networks.find_station("UGTO00MEX")
    assert ugto.network == "rgna-inegi" and ugto.network_name.startswith("RGNA") and ugto.official
    assert ugto.position.frame == "ITRF2008" and ugto.position.epoch == 2010.0
    assert abs(ugto.antenna_height_m - 0.071) < 1e-9 and ugto.antenna_type == "TRM115000.00"
    # la coordenada del paquete es la que el propio INEGI escribe en sus RINEX (APPROX POSITION XYZ)
    assert abs(ugto.position.x - -1164730.1759) < 2e-4 and abs(ugto.position.z - 2272414.2366) < 2e-4
    near = networks.nearest_stations(ugto.position, 3)
    assert near[0][0].id == "UGTO" and near[1][0].id == "UQRO" and 100_000 < near[1][1] < 101_000
    assert networks.find_station("ZZZZ") is None
    assert any("hundimiento" in n for n in networks.station_notes(networks.find_station("INEG")))


def test_bundled_antennas_have_calibration():
    for station in networks.station_catalog():
        assert antex.choose(station.antenna_type, station.radome).model is not None, station.id


def test_rgna_guide_hours_and_files():
    ugto = networks.find_station("UGTO")
    start = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    end = datetime(2026, 10, 21, 1, 15, tzinfo=timezone.utc)
    assert not networks.can_download(ugto)
    text = networks.download_guide(ugto, start, end)
    assert "https://www.inegi.org.mx/app/geo2/rgna/" in text
    assert "«Vigésima cuarta»" in text and "archivo UGTO293x" in text
    assert "archivos UGTO294a…UGTO294b" in text and "«Segunda»" in text
    with pytest.raises(UserError, match="UGTO293x"):
        networks.download_station_rinex(ugto, start, end, Path("."))


def test_other_country_network_installed(installed):
    (installed / "red-prueba.json").write_text(json.dumps(_other_network()), encoding="utf-8")
    ids = [n.id for n in networks.networks()]
    assert "red-prueba" in ids and "rgna-inegi" in ids
    braz = networks.find_station("BRAZ00BRA")
    assert braz.network == "red-prueba" and braz.position.frame == "SIRGAS2000" and braz.position.epoch == 2000.4
    assert networks.can_download(braz)
    text = networks.download_guide(braz, datetime(2026, 1, 9, 23, 55, tzinfo=timezone.utc),
                                   datetime(2026, 1, 10, 2, tzinfo=timezone.utc))
    assert "https://example.org/rinex/2026/009/braz0091.zip" in text and "/2026/010/braz0101.zip" in text
    assert networks.station_notes(braz) == ["aviso de prueba"]


def test_installed_revision_wins_and_bad_packages_are_skipped(installed):
    newer = copy.deepcopy(RGNA)
    newer["revision"] = RGNA["revision"] + 1
    newer["stations"] = [s for s in newer["stations"] if s["id"] == "UGTO"]
    (installed / "rgna-inegi.json").write_text(json.dumps(newer), encoding="utf-8")
    (installed / "danado.json").write_text("{no es json", encoding="utf-8")
    assert len(networks.station_catalog("rgna-inegi")) == 1


@pytest.mark.parametrize("change, message", [
    (lambda d: d.update(format="otro"), "no es un paquete"),
    (lambda d: d.update(formatVersion=99), "más nueva"),
    (lambda d: d.update(frame={"name": "X"}), "época"),
    (lambda d: d["stations"].append(dict(d["stations"][0])), "repetida"),
    (lambda d: d["stations"][0].update(latitude=None, x=1.0, y=2.0, z=3.0), "superficie"),
    (lambda d: d.update(download={"type": "url-template", "url": "http://x", "granularity": "daily"}), "https"),
])
def test_package_validation(change, message):
    data = _other_network()
    change(data)
    with pytest.raises(networks.PackageError, match=message):
        networks.parse_package(data)


def test_url_template_download(tmp_path, installed):
    served = tmp_path / "servidor"
    (served / "2026" / "009").mkdir(parents=True)
    (served / "2026" / "009" / "braz0091.zip").write_bytes(b"PK\x03\x04datos")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(served), **k)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        package = _other_network(f"https://127.0.0.1:{server.server_port}/{{yyyy}}/{{doy}}/{{id}}{{doy}}1.zip")
        net = networks.parse_package(package)
        # el paquete exige https; en la prueba local se sirve por http con la misma plantilla
        net.download["url"] = f"http://127.0.0.1:{server.server_port}/{{yyyy}}/{{doy}}/{{id}}{{doy}}1.zip"
        braz = net.station(net.stations[0])
        original = networks.network
        networks.network = lambda nid: net if nid == "red-prueba" else original(nid)
        try:
            files = networks.download_station_rinex(braz, datetime(2026, 1, 9, 10, tzinfo=timezone.utc),
                                                    datetime(2026, 1, 9, 12, tzinfo=timezone.utc), tmp_path)
            assert [f.name for f in files] == ["braz0091.zip"] and files[0].read_bytes().startswith(b"PK")
            with pytest.raises(UserError, match="no tiene publicados"):
                networks.download_station_rinex(braz, datetime(2026, 2, 9, 10, tzinfo=timezone.utc),
                                                datetime(2026, 2, 9, 12, tzinfo=timezone.utc), tmp_path)
        finally:
            networks.network = original
    finally:
        server.shutdown()


def test_network_antenna_blocks_are_merged(tmp_path, installed):
    block = ("                                                            START OF ANTENNA\n"
             "ZZZTEST1        NONE                                        TYPE / SERIAL NO\n"
             "   G01                                                      START OF FREQUENCY\n"
             "      1.00      2.00     90.00                              NORTH / EAST / UP\n"
             "   G01                                                      END OF FREQUENCY\n"
             "                                                            END OF ANTENNA\n")
    package = _other_network()
    package["antennaCalibrations"] = {"source": "prueba", "antex": block}
    (installed / "red-prueba.json").write_text(json.dumps(package), encoding="utf-8")
    choice = antex.choose("ZZZTEST1", "NONE")
    assert choice.model is not None and abs(choice.model.frequencies["G01"][2] - 0.09) < 1e-12
    text = antex.ensure_antex(tmp_path).read_text(encoding="latin-1")
    assert "ZZZTEST1" in text and text.count("TRM115000.00    NONE                                        TYPE / SERIAL NO") == 1


def test_precise_candidates_and_days():
    day = datetime(2026, 10, 20)
    assert precise.gps_week(day) == 2441
    names = [p.filename for p in precise._candidates(day)]
    assert names[0] == "ESA0OPSFIN_20262930000_01D_05M_ORB.SP3.gz"
    assert "IGS0OPSRAP_20262930000_01D_15M_ORB.SP3.gz" in names
    days = precise.session_days(datetime(2026, 10, 20, 0, 30, tzinfo=timezone.utc),
                                datetime(2026, 10, 20, 3, tzinfo=timezone.utc))
    assert [d.day for d in days] == [19, 20]


def test_registered_in_signed_data_library():
    from tresvizo_ppk.datos import biblioteca
    kind = biblioteca.KINDS[networks.KIND]
    assert kind.validate is networks.validate_download
    good = json.dumps(_other_network()).encode()
    kind.validate({"id": "red-prueba", "title": "Prueba"}, good)
    with pytest.raises(UserError, match="otra red"):
        kind.validate({"id": "otra", "title": "Prueba"}, good)
    bad = _other_network()
    bad["frame"] = {"name": "X"}
    with pytest.raises(UserError, match="no es válido"):
        kind.validate({"id": "red-prueba", "title": "Prueba"}, json.dumps(bad).encode())
