"""Paquetes de redes CORS de otros países (scripts/red_noaa_cors.py, red_cacs_nrcan.py, red_ergnss_ign.py y
red_comun.py): lectores de las fuentes oficiales con muestras pequeñas, armado y validación del paquete, y que la app
instale un paquete desde el índice firmado, lo muestre y avise de lo que trae cada estación."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import red_cacs_nrcan  # noqa: E402
import red_comun  # noqa: E402
import red_ergnss_ign  # noqa: E402
import red_noaa_cors  # noqa: E402
from tresvizo_ppk.core import networks  # noqa: E402
from tresvizo_ppk.core.geodesy import geodetic_to_ecef  # noqa: E402

ATX_HEADER = ("     1.4            M                                       ANTEX VERSION / SYST\n"
              "A                                                           PCV TYPE / REFANT\n"
              "                                                            END OF HEADER\n")


def _block(kind: str, dome: str, up: float = 60.0, serial: str = "") -> str:
    return ("                                                            START OF ANTENNA\n"
            f"{kind:<16s}{dome:<4s}{serial:<20s}                    TYPE / SERIAL NO\n"
            "   G01                                                      START OF FREQUENCY\n"
            f"      0.00      0.00 {up:9.2f}                              NORTH / EAST / UP\n"
            "   G01                                                      END OF FREQUENCY\n"
            "                                                            END OF ANTENNA\n")


@pytest.fixture
def atx(tmp_path):
    igs = tmp_path / "igs20_2437.atx"
    igs.write_text(ATX_HEADER + _block("BLOCK IIF", "", serial="G01") + _block("TRM59800.00", "SCIS")
                   + _block("TRM59800.00", "NONE") + _block("LEIAR20", "NONE") + _block("TRM115000.00", "NONE"),
                   encoding="latin-1")
    ngs = tmp_path / "ngs20.atx"
    ngs.write_text(ATX_HEADER + _block("ASH111661", "NONE", 57.24) + _block("LEIAR20", "SCIT"), encoding="latin-1")
    return [(igs, "IGS, ANTEX igs20_2437"), (ngs, "NGS, ANTEX ngs20.atx")]


def _types(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        if line[60:].strip() == "TYPE / SERIAL NO":
            out.append((line[0:16].strip(), line[16:20].strip()))
    return out


# --- Calibraciones de antena -----------------------------------------------------------------------------------------

def test_calibraciones_solo_las_que_hacen_falta(atx):
    used = {("TRM59800.00", "SCIS"), ("LEIAR20", "LEIM"), ("LEIAR20", "SCIT"), ("ASH111661", "NONE"),
            ("TRM115000.00", "NONE"), ("MPL_WAAS_2225N", "NONE")}
    blocks, source, missing = red_comun.antex_blocks(atx, used, skip={("TRM115000.00", "NONE")})
    # exacta de la IGS; sin el radomo LEIM → LEIAR20 NONE (la que usaría el programa); exacta del NGS antes que NONE;
    # la que ya trae el ANTEX incluido no se repite; la que no está en ninguno, a «missing»
    assert sorted(_types(blocks)) == [("ASH111661", "NONE"), ("LEIAR20", "NONE"), ("LEIAR20", "SCIT"),
                                      ("TRM59800.00", "SCIS")]
    assert missing == {("MPL_WAAS_2225N", "NONE")}
    assert source == "IGS, ANTEX igs20_2437; NGS, ANTEX ngs20.atx (ASH111661 NONE, LEIAR20 SCIT)"
    assert blocks.count("START OF ANTENNA") == 4               # sin duplicados, sin la de satélite


def test_aviso_de_antena_sin_calibracion():
    stations = [{"id": "ZDC1", "antennaType": "MPL_WAAS_2225N", "radome": "NONE"},
                {"id": "GODE", "antennaType": "TRM59800.00", "radome": "SCIS", "notes": ["otro aviso"]}]
    assert red_comun.mark_uncalibrated(stations, {("MPL_WAAS_2225N", "NONE")}) == ["ZDC1"]
    assert stations[0]["notes"][0].startswith(red_comun.UNCALIBRATED_NOTE)
    assert stations[1]["notes"] == ["otro aviso"]
    red_comun.mark_uncalibrated(stations, {("MPL_WAAS_2225N", "NONE")})
    assert len(stations[0]["notes"]) == 1                       # no se repite


def test_site_log_de_la_igs():
    log = """3.2  Receiver Type            : TRIMBLE NETR9
     Date Removed             : 2020-01-01T00:00Z
3.3  Receiver Type            : TRIMBLE ALLOY
     Date Removed             : (CCYY-MM-DDThh:mmZ)
3.x  Receiver Type            : (A20, from rcvr_ant.tab; see instructions)
4.   GNSS Antenna Information
4.1  Antenna Type             : TRM159900.00    SCIS
     Marker->ARP Up Ecc. (m)  : 0.035
     Antenna Radome Type      : SCIS
     Date Installed           : 2025-04-08T20:30Z
4.x  Antenna Type             : (A20, from rcvr_ant.tab; see instructions)
5.   Surveyed Local Ties
"""
    out = red_comun.parse_site_log(log)
    assert out == {"antenna": "TRM159900.00", "radome": "SCIS", "antHt": 0.035, "since": "2025-04-08",
                   "receiver": "TRIMBLE ALLOY"}


# --- NOAA CORS -------------------------------------------------------------------------------------------------------

NOAA_XYZ = """October 25, 2026

NAD 83(2011) Cartesian CORS Pos. Ant. Ref. Point (ARP) Computed Vels.
 SITE   EPOCH        x             y             z          Vx      Vy      Vz    Ctry.  State      Site
1LSU  2010.00   -113402.181  -5504362.817   3209404.368     1.4     3.1    -1.1    US      LA      Operational
P674  2010.00  -2508651.645  -4514041.236   3730713.576    -3.7    11.3     8.9    US      CA      Operational
ZSU4  2010.00   2462589.959  -5529373.557   2003724.453    15.0     9.1     4.5    US      PR      Operational
ZOLE  2010.00  -2448970.331  -4284229.102   4029451.236    -2.0     8.6     3.2    US      NV   Decommissioned
CN13  2010.00   1553881.069  -5615969.739   2584863.503     0.4    -0.2    -1.7    BF      BF      Operational
XXXX  2010.00   1553881.069  -5615969.739   2584863.503     0.4    -0.2    -1.7    --      --    IGS_not_CORS
NOEQ  2010.00   -113402.181  -5504362.817   3209404.368     1.4     3.1    -1.1    US      LA      Operational
"""
NOAA_GEO = """October 25, 2026
 SITE   EPOCH       Latitude          Longitude     Ellip. Ht.   Vn      Ve      Vu    Ctry.  State      Site
1LSU  2010.00  30 24 26.70942 N   91 10 48.91510 W    -5.204     0.6     1.3    -3.3    US      LA      Operational
P674  2010.00  36  1  0.00000 N  119  3  0.00000 W   100.000    -9.0    22.0     0.5    US      CA      Operational
"""
NOAA_INFO = (
    " 1LSU  LOUISIANA STATE U 2021 258 12 52 00  9999 999 00 00 00   0.0000  DHPAB   0.0000   0.0000  >>TRIMBLE ALLOY"
    "       5.50/6.10             -----  S/N= >>5818R40010     >>TRM115000.00   NONE   S/N= >>1550951639      <\n"
    " P674  GlassMtn__CN2006  2025 231 20 02 00  9999 999 00 00 00   0.0083  DHPAB   0.0000   0.0000  >>SEPT POLARX5"
    "        5.6.0                 -----  S/N= >>3095994        >>TWIVC6050      SCIT   S/N= >>710152 20221209 <\n"
    " ZSU4  San Juan WAAS 4   2012 313 00 00 00  9999 999 00 00 00   0.0000  DHPAB   0.0000   0.0000  >>NOV WAASGII"
    "         2.003                 -----  S/N= >>80C1           >>MPL_WAAS_2225N  NONE   S/N= >>(A*, but note th<\n"
    " CN13  NASSAU            2020 001 00 00 00  9999 999 00 00 00   0.0000  DHPAB   0.0000   0.0000  >>TRIMBLE NETR9"
    "       5.52                  -----  S/N= >>5302K41505     >>TRM59800.00    SCIS   S/N= >>60513G0044      <\n")


def test_noaa_lectores():
    xyz = red_noaa_cors.parse_xyz(NOAA_XYZ)
    assert xyz["1LSU"]["x"] == -113402.181 and xyz["ZOLE"]["status"] == "Decommissioned"
    geo = red_noaa_cors.parse_geo(NOAA_GEO)
    assert geo["1LSU"]["latitude"] == pytest.approx(30 + 24 / 60 + 26.70942 / 3600)
    assert geo["1LSU"]["longitude"] < 0 and geo["1LSU"]["vu"] == -3.3
    info = red_noaa_cors.parse_station_info(NOAA_INFO)
    # la serie de la antena puede llevar espacios («710152 20221209», «(A*, but note th»)
    assert info["P674"]["antenna"] == "TWIVC6050" and info["P674"]["radome"] == "SCIT"
    assert info["ZSU4"]["antenna"] == "MPL_WAAS_2225N" and info["ZSU4"]["radome"] == "NONE"
    assert info["1LSU"]["receiver"] == "TRIMBLE ALLOY"


def test_noaa_paquete(atx):
    xyz = {"2011": red_noaa_cors.parse_xyz(NOAA_XYZ), "pa11": {}, "ma11": {}}
    geo = {"2011": red_noaa_cors.parse_geo(NOAA_GEO), "pa11": {}, "ma11": {}}
    info = red_noaa_cors.parse_station_info(NOAA_INFO)
    package, report = red_noaa_cors.build(xyz, geo, info, atx, "2026-10-27", "2026-10-25")
    net = networks.parse_package(json.loads(json.dumps(package)))
    assert net.frame == "NAD83(2011)" and net.epoch == 2010.0 and net.download["type"] == "url-template"
    ids = [s["id"] for s in package["stations"]]
    assert ids == ["1LSU", "CN13", "P674", "ZSU4"]                # sin las de baja, la de la IGS ni la sin equipo
    assert report["no_equipment"] == ["NOEQ"] and any("NOEQ" in n for n in package["notes"])
    rows = {s["id"]: s for s in package["stations"]}
    assert rows["1LSU"]["antennaHeight"] == 0.0                   # coordenada de la ARP
    assert rows["ZSU4"]["country"] == "PR" and rows["CN13"]["country"] == "BS"
    assert set(package["countries"]) == {"US", "PR", "BS"}
    assert rows["ZSU4"]["notes"][0].startswith(red_comun.UNCALIBRATED_NOTE)
    assert any("se mueve 24 mm/año" in n for n in rows["P674"]["notes"])
    lat, lon, h = rows["1LSU"]["latitude"], rows["1LSU"]["longitude"], rows["1LSU"]["height"]
    assert lat == pytest.approx(30.40741928, abs=1e-7) and lon == pytest.approx(-91.18025419, abs=1e-7)
    assert h == pytest.approx(-5.204, abs=0.002)
    assert "TWIVC6050" not in package["antennaCalibrations"]["antex"]  # no está en ningún ANTEX de prueba


# --- CACS ------------------------------------------------------------------------------------------------------------

CACS_REPORT = """<table><tr><th>Name</th><th>Province</th><th>NTS</th><th>Unique</th><th>Prov</th><th>Network</th></tr>
<h3>Site Identification</h3><table><tr><th>x</th></tr>
		<tr>
			<td>ALGO CACS-GSD</td>
			<td>Ontario</td>
			<td>031E16</td>
			<td>883160</td>
			<td>ALGO</td>
			<td>CSRS</td>
		</tr>
	</table>
		<input type='hidden' name='x' value='0 0 165440.847239'>
		<input type='hidden' name='y' value='W0 0 281056.906491'>
		<input type='hidden' name='z' value='201.9885'>
		<input type='hidden' name='vx' id='geovx' value='-1.793'>
		<input type='hidden' name='vy' id='geovy' value='2.0051'>
		<input type='hidden' name='vz' id='geovz' value='1.9903'>
		<input type='hidden' name='epoch' id='epoch' value='2010'>
		<input name='frame' type='hidden' value='NAD83CSRS'>
<h3 class='panel-title text-center'>Equipment on site</h3>
	<tr>
		<th class='active'>Receiver</th>
		<td>SEPT POLARX5		</td>
		<td>2026-03-25 19:19:00		</td>
	</tr>
		<h3 class='panel-title text-center'>Antenna</h3>
	<tr class='active'><th rowspan='2'>Model</th></tr>
	<tr><th>GPS L1</th></tr>
		<tr>
		<td>AOAD/M_T NONE</td>
		<td>2012-12-20 17:00:00</td>
		<td> 0.100</td>
		<td>91.84</td>
	</tr>
<p>Use of Canadian Geodetic Survey products and data is subject to the <a target='_blank'
 href='http://open.canada.ca/en/open-government-licence-canada'>Open Government Licence - Canada</a></p>
"""


def test_cacs_informe_y_paquete(atx):
    rep = red_cacs_nrcan.parse_report(CACS_REPORT)
    assert rep["latitude"] == pytest.approx(45 + 57 / 60 + 20.847239 / 3600)
    assert rep["longitude"] == pytest.approx(-(78 + 4 / 60 + 16.906491 / 3600))
    assert rep["height"] == 201.9885 and rep["frame"] == "NAD83CSRS" and rep["epoch"] == 2010.0
    assert (rep["antenna"], rep["radome"], rep["antHt"]) == ("AOAD/M_T", "NONE", 0.1)
    assert rep["license"] == red_cacs_nrcan.OGL and rep["receiver"] == "SEPT POLARX5"
    nova = dict(rep, license="Nova Scotia License")
    rows = [{"n": "ALGO", "id": "883160", "net": "CSRS", "town": "ALGONQUIN PARK"},
            {"n": "TSKT", "id": "1", "net": "NSACS"}, {"n": "SHBR", "id": "2", "net": "DISCONTINUED"},
            {"n": "WOSB", "id": "3", "net": "WCDA"}]
    reports = {"ALGO": rep, "SHBR": nova, "WOSB": {k: v for k, v in rep.items() if k != "antenna"}}
    package, report = red_cacs_nrcan.build(rows, reports, {"ALGO"}, atx, "2026-10-27", "2026-10-17")
    net = networks.parse_package(json.loads(json.dumps(package)))
    assert net.frame == "NAD83(CSRS)" and net.epoch == 2010.0 and [s["id"] for s in package["stations"]] == ["ALGO"]
    assert "Nueva Escocia" in report["left_out"]["TSKT"] and "Nueva Escocia" in report["left_out"]["SHBR"]
    assert "antena" in report["left_out"]["WOSB"]
    algo = package["stations"][0]
    assert algo["name"] == "Algonquin Park (ON)" and algo["antennaHeight"] == 0.1
    x, y, z = geodetic_to_ecef(algo["latitude"], algo["longitude"], algo["height"])
    assert (algo["x"], algo["y"], algo["z"]) == (round(x, 4), round(y, 4), round(z, 4))
    assert abs(algo["x"] - 918130.08) < 0.5                     # la lista del CGS la da al cm


# --- ERGNSS ----------------------------------------------------------------------------------------------------------

def _ficha(code, name, lon_text, lat_text, h, xyz, frame="ETRS89", antenna="LEIAR20         LEIM",
           height="0.0771 m (BAM)", date_text="24-oct-2026", municipio="Garafía"):
    return f"""                                             Reseña de Estación Permanente - ERGNSS          {date_text}
     Situación:
Código.........: {code}                         Municipio: {municipio}
Nombre........: {name}
Código IERS: 81701M001                        Provincia: Santa Cruz de Tenerife
     Coordenadas {frame}:
Longitud............:{lon_text}     X....................:    {xyz[0]:.3f} m
Latitud...............: {lat_text}    Y....................:   {xyz[1]:.3f} m
Altitud elipsoidal:           {h:.3f} m      Z....................:    {xyz[2]:.3f} m
     Instrumentación:
Receptor: LEICA GR50
Antena: {antenna}         Altura: {height}
"""


def test_ergnss_reseñas(atx):
    lat, lon, h = 28 + 45 / 60 + 49.93114 / 3600, -(17 + 53 / 60 + 37.78689 / 3600), 2199.233
    xyz = geodetic_to_ecef(lat, lon, h)
    lpal = red_ergnss_ign.parse_ficha(_ficha("LPAL", "La Palma", "- 17° 53' 37,78689''", "28° 45' 49,93114''", h,
                                             xyz, frame="REGCAN95"))
    assert lpal["frame"] == "REGCAN95" and lpal["date"] == "2026-10-24" and lpal["antenna"] == "LEIAR20"
    assert lpal["longitude"] == pytest.approx(lon) and lpal["htCode"] == "BAM" and lpal["antHt"] == 0.0771
    # reseña sin el signo de la longitud oeste: manda la cartesiana y se anota
    tn07 = red_ergnss_ign.parse_ficha(_ficha("TN07", "CCAN", " 17° 53' 37,78689''", "28° 45' 49,93114''", h, xyz,
                                             frame="REGCAN95"))
    bad = red_ergnss_ign.parse_ficha(_ficha("LABG", "Mal", "- 17° 53' 37,78689''", "28° 45' 49,93114''", h + 3.4,
                                            xyz))
    prov = red_ergnss_ign.parse_ficha(_ficha("IRUX", "Prov", "- 17° 53' 37,00000''", "28° 45' 49,00000''", 123.0,
                                             xyz))
    other_height = red_ergnss_ign.parse_ficha(_ficha("ODDH", "Otra", "- 17° 53' 37,78689''", "28° 45' 49,93114''",
                                                     h, xyz, height="1.000 m (TOP)"))
    ujae = red_ergnss_ign.parse_ficha(_ficha("UJAE", "Jaén", "- 17° 53' 37,78689''", "28° 45' 49,93114''", h, xyz))
    long_names = {"LPAL": "LPAL00ESP", "TN07": "TN0700ESP"}
    package, report = red_ergnss_ign.build([lpal, tn07, bad, prov, other_height, ujae], {"LPAL"}, long_names, atx,
                                           "2026-10-27", "2026-10-20")
    net = networks.parse_package(json.loads(json.dumps(package)))
    assert net.frame == "ETRS89" and net.epoch == 2017.0
    rows = {s["id"]: s for s in package["stations"]}
    assert set(rows) == {"LPAL", "TN07"}
    assert rows["LPAL"]["frame"] == "REGCAN95" and rows["LPAL"]["epoch"] == 1994.9
    assert report["sign_fixed"] == ["TN07"] and rows["TN07"]["longitude"] < 0
    assert "no coinciden" in report["left_out"]["LABG"] and "provisional" in report["left_out"]["IRUX"]
    assert "ARP" in report["left_out"]["ODDH"] and "2018" in report["left_out"]["UJAE"]
    assert any("No estaba en el archivo" in n for n in rows["TN07"]["notes"]) and "notes" not in rows["LPAL"]
    t = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    url = net.download["url"].format(ID="LPAL", yyyy="2026", mm="10", dd="20", doy=f"{t.timetuple().tm_yday:03d}")
    assert url == ("https://datos-geodesia.ign.es/ERGNSS/diario_30s/2026/20261020/"
                   "LPAL00ESP_R_20262930000_01D_30S_MO.crx.gz")


# --- La app instala y muestra los paquetes del índice firmado ---------------------------------------------------------

PACKAGES = red_comun.PACKAGES
TEST_SECRET = hashlib.sha256(b"TresVizo llave de prueba, no publicar con ella").digest()


class _Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.status = 200
        self.headers = {"Content-Length": str(len(data))}


def _publish(monkeypatch, files: dict[str, bytes], items: list[dict]):
    from tresvizo_ppk.crs import api
    from tresvizo_ppk.datos import biblioteca, ed25519, indice
    payload = {"format": indice.PAYLOAD_FORMAT, "formatVersion": 1, "sequence": 20,
               "publishedAt": "2026-10-27T00:00:00Z", "release": "datos-prueba", "items": items}
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    public = ed25519.public_key(TEST_SECRET)
    envelope = {"format": indice.ENVELOPE_FORMAT, "formatVersion": 1, "algorithm": "Ed25519",
                "keyID": ed25519.key_id(public), "payload": base64.b64encode(body).decode(),
                "signature": base64.b64encode(ed25519.sign(TEST_SECRET, indice.SIGNING_CONTEXT + body)).decode()}
    served = dict(files)
    served[indice.INDEX_PATH] = json.dumps(envelope).encode()

    def opener(url, headers):
        path = url[len(indice.BASE_URL):]
        if path not in served:
            raise urllib.error.HTTPError(url, 404, "no", {}, None)
        return _Response(served[path])
    monkeypatch.setattr(indice, "TRUSTED_KEYS", {ed25519.key_id(public): public})
    monkeypatch.setattr(biblioteca, "BUNDLED_INDEX", Path("/no/existe/datos-index.json"))
    monkeypatch.setattr(biblioteca, "opener", opener)
    monkeypatch.setattr(biblioteca.time, "sleep", lambda s: None)
    api.reload()


def _item(data: bytes, package: dict) -> dict:
    """La entrada que escribe `publicar.py --redes` (repositorio de TresVizo Field) para un paquete."""
    return {"kind": "cors-network", "id": package["id"], "version": package["version"], "revision": 1,
            "title": package["title"], "countries": package["countries"], "region": package["region"],
            "authority": package["authority"], "license": package["license"], "licenseURL": package["licenseURL"],
            "attribution": package["attribution"], "transformationNotice": "", "bounds": package["bounds"],
            "requires": [],
            "file": {"path": f"download/datos-prueba/{package['id']}-r1.json", "bytes": len(data),
                     "sha256": hashlib.sha256(data).hexdigest()},
            "corsNetwork": {"format": package["format"], "formatVersion": package["formatVersion"],
                            "stations": len(package["stations"]), "frame": package["frame"]["name"],
                            "epoch": package["frame"]["epoch"]}}


@pytest.mark.skipif(not all((PACKAGES / f"{i}.json").is_file() for i in ("noaa-cors", "cacs-nrcan", "ergnss-ign")),
                    reason="los paquetes se arman con scripts/red_*.py en ~/Documents/TresVizo-Field-PPK-datos")
def test_la_app_instala_y_muestra_las_redes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app import networks as app_networks
    from tresvizo_ppk.core import api as core_api
    from tresvizo_ppk.crs import api
    from tresvizo_ppk.datos import biblioteca
    api.set_data_dir(tmp_path / "datos")
    try:
        files, items = {}, []
        for network_id in ("noaa-cors", "cacs-nrcan", "ergnss-ign"):
            data = (PACKAGES / f"{network_id}.json").read_bytes()
            package = json.loads(data)
            files[f"download/datos-prueba/{network_id}-r1.json"] = data
            items.append(_item(data, package))
        _publish(monkeypatch, files, items)
        api.download_index()
        for network_id in ("noaa-cors", "cacs-nrcan", "ergnss-ign"):
            assert any(c.id == network_id and c.kind == "cors-network" for c in api.catalog())
            biblioteca.install(network_id)
        found = {n.id: n for n in networks.networks()}
        assert found["noaa-cors"].frame == "NAD83(2011)" and found["cacs-nrcan"].frame == "NAD83(CSRS)"
        assert found["ergnss-ign"].frame == "ETRS89" and "rgna-inegi" in found
        # la pantalla «Estaciones de referencia» las lista con su marco y sus estaciones
        shown = {n.id: n for n in app_networks.load(core_api)}
        assert shown["noaa-cors"].frame_text == "NAD83(2011), época 2010" and shown["noaa-cors"].origin == "instalada"
        assert len(shown["noaa-cors"].stations) == len(found["noaa-cors"].stations) > 1000
        assert len(shown["cacs-nrcan"].stations) > 100 and len(shown["ergnss-ign"].stations) > 100
        # el aviso de antena sin calibración sale en la ficha de la estación y en los avisos del proceso
        zdc1 = networks.find_station("ZDC100USA")
        assert zdc1 is not None and zdc1.network == "noaa-cors"
        assert any(n.startswith(red_comun.UNCALIBRATED_NOTE) for n in app_networks.station_notes(core_api, zdc1))
        rvdi = networks.find_station("RVDI")                    # ASH111661: calibración del NGS en el paquete
        assert rvdi is not None and not networks.station_notes(rvdi)
        assert any("ASH111661" in b for b in networks.antex_blocks())
        # Canarias en REGCAN95, la Península en ETRS89
        assert networks.find_station("LPAL").position.frame == "REGCAN95"
        assert networks.find_station("YEBE").position.frame == "ETRS89"
        # descarga directa con la plantilla documentada de cada red
        start = datetime(2026, 10, 20, 15, tzinfo=timezone.utc)
        algo = networks.find_station("ALGO")
        assert networks.can_download(algo)
        assert "https://cacs.nrcan.gc.ca/gps/data/gpsdata/26293/26d/algo2930.26d.Z" in \
            networks.download_guide(algo, start, start)
        gode = networks.find_station("GODE")
        assert "https://geodesy.noaa.gov/corsdata/rinex/2026/293/gode/gode2930.26d.gz" in \
            networks.download_guide(gode, start, start)
        assert app_networks.can_download(SimpleNamespace(can_download_station=networks.can_download), gode)
    finally:
        api.set_data_dir(None)
        api.reload()
