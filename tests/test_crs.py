"""Sistemas, datums y geoides contra los vectores de las apps TresVizo Field (tests/datos/apps/FUENTE.md)."""
from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import io
import json
import math
import os
import threading
import urllib.error
from pathlib import Path

import pytest

from tresvizo_ppk.crs import api, definicion, motor, mvgeoid
from tresvizo_ppk.datos import biblioteca, ed25519, indice
from tresvizo_ppk.errors import Cancelled, UserError
from tresvizo_ppk.model import CoordinateChoice, Ecef, Enu

APPS = Path(__file__).parent / "datos" / "apps"
DATOS = Path(api.PACKAGE_DATA)
INDEX = biblioteca.BUNDLED_INDEX
TEST_SECRET = hashlib.sha256(b"TresVizo llave de prueba, no publicar con ella").digest()


def _gz_json(path: Path):
    return json.loads(gzip.decompress(path.read_bytes()))


@pytest.fixture(scope="module")
def reference():
    return _gz_json(APPS / "crs_reference.json.gz")


@pytest.fixture(scope="module")
def fixtures():
    return json.loads((APPS / "fixtures.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def registry():
    """Fábrica y los nueve paquetes publicados, unidos como en las apps."""
    reg: dict[str, definicion.Definition] = {}
    files = [api.FACTORY_FILE.read_bytes()] + [gzip.decompress(p.read_bytes())
                                                       for p in sorted((APPS / "paquetes").glob("*.json.gz"))]
    for data in files:
        for d in definicion.read_package(data).systems:
            reg[d.code] = definicion.merge(reg[d.code], d) if d.code in reg else d
    return reg


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Una carpeta de datos vacía, sin el índice incluido, para las pruebas de instalación."""
    api.set_data_dir(tmp_path / "datos")
    yield tmp_path / "datos"
    api.set_data_dir(None)


# --- Geoides ---------------------------------------------------------------------------------------------------------

def test_geoides_incluidos_son_los_de_las_apps_byte_a_byte(fixtures):
    sums = {"ggm10": "725204b865301215611cb5bd4e9f1de46664a2acef3e90dc3f36c26951c71989",
            "ggm25": "bc6e024d6ad2590edd10f73c1f0745db9471ee9a1cf4c0e2ad98de245a0ea5ab"}
    for gid, digest in sums.items():
        path = DATOS / f"{gid}.mvgeoid"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        grid = mvgeoid.read(path)
        assert grid.checksum == fixtures["bundled"][gid]["dataChecksum"]
        assert (grid.rows, grid.columns) == (fixtures["bundled"][gid]["rows"], fixtures["bundled"][gid]["columns"])


def test_ondulaciones_identicas_a_las_apps(fixtures):
    n = 0
    for gid in ("ggm10", "ggm25"):
        grid = mvgeoid.read(DATOS / f"{gid}.mvgeoid")
        for v in fixtures["bundled"][gid]["vectors"]:
            assert grid.undulation(v["latitude"], v["longitude"]) == v["undulation"]   # el mismo número, no «casi»
            n += 1
    assert n == 56


def test_rejilla_pequena_y_sin_dato(fixtures):
    g = fixtures["grid"]
    grid = mvgeoid.decode(base64.b64decode(g["base64"]))
    assert grid.samples.ravel().tolist() == g["samples"]
    assert (grid.rows, grid.columns, grid.latitude_minimum, grid.longitude_minimum) == (
        g["rows"], g["columns"], g["latitudeMinimum"], g["longitudeMinimum"])
    lat = g["latitudeMinimum"] + 2 * g["latitudeStep"]
    lon = g["longitudeMinimum"] + 3.5 * g["longitudeStep"]
    assert grid.undulation(lat, lon) is None                 # una esquina sin dato: no se interpola
    assert grid.undulation(g["latitudeMinimum"] - 1e-9, g["longitudeMinimum"]) is None   # fuera: no se extrapola
    assert grid.undulation(g["latitudeMinimum"], g["longitudeMinimum"]) == -32767 / 100


def test_geoide_danado_se_rechaza():
    data = bytearray((DATOS / "ggm10.mvgeoid").read_bytes())
    data[-100] ^= 0xFF
    with pytest.raises(mvgeoid.FormatError):
        mvgeoid.decode(bytes(data))
    with pytest.raises(mvgeoid.FormatError):
        mvgeoid.decode(b"no es un geoide" * 10)


# --- Índice firmado --------------------------------------------------------------------------------------------------

def test_indice_firmado_vectores_de_las_apps(fixtures):
    fx = fixtures["index"]
    trusted = {fx["testKeyID"]: base64.b64decode(fx["testPublicKey"])}
    assert ed25519.key_id(base64.b64decode(fx["testPublicKey"])) == fx["testKeyID"]
    good = indice.verify(json.dumps(fx["good"]).encode(), trusted)
    assert good["sequence"] == fx["expected"]["sequence"]
    assert [i["id"] for i in good["items"]] == fx["expected"]["items"]
    unknown = indice.verify(json.dumps(fx["withUnknownKind"]).encode(), trusted)
    assert [i["id"] for i in unknown["items"]] == fx["expected"]["items"]       # la clase desconocida se salta
    with pytest.raises(indice.IndexError_, match="relativa"):
        indice.verify(json.dumps(fx["badPath"]).encode(), trusted)
    with pytest.raises(indice.IndexError_) as error:
        indice.verify(json.dumps(fx["tampered"]).encode(), trusted)
    assert str(error.value) == indice.UNTRUSTED
    with pytest.raises(indice.IndexError_) as error:             # firmado con una llave que no es de confianza
        indice.verify(json.dumps(fx["good"]).encode())
    assert str(error.value) == indice.UNTRUSTED
    with pytest.raises(indice.IndexError_):
        indice.verify(b"<html>no</html>", trusted)


def test_ed25519_rfc8032():
    sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pk = ed25519.public_key(sk)
    assert pk.hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    sig = ed25519.sign(sk, b"")
    assert sig.hex() == ("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9"
                         "b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519.verify(pk, b"", sig) and not ed25519.verify(pk, b"x", sig)


def test_indice_incluido_lo_firmo_la_llave_de_las_apps():
    payload = indice.verify(INDEX.read_bytes())
    assert payload["sequence"] >= 3
    ids = {i["id"] for i in payload["items"]}
    assert {"crs-mx", "crs-co", "crs-world", "egm2008", "qgeoidcol2023"} <= ids
    assert "ggm10" not in ids and "ggm25" not in ids            # incluidos, no descargables


def test_paquetes_de_prueba_son_los_publicados():
    payload = indice.verify(INDEX.read_bytes())
    items = {i["id"]: i for i in payload["items"]}
    files = sorted((APPS / "paquetes").glob("*.json.gz"))
    assert len(files) == 34
    for p in files:
        data = gzip.decompress(p.read_bytes())
        item = items[p.name[: -len(".json.gz")]]
        assert hashlib.sha256(data).hexdigest() == item["file"]["sha256"]
        assert len(data) == item["file"]["bytes"]


# --- Sistemas: crs_reference.json ------------------------------------------------------------------------------------

def test_definiciones_de_fabrica_son_las_de_las_apps(reference, registry):
    builtin = [s for s in reference["systems"] if s["builtIn"]]
    assert [s["code"] for s in builtin] == [d.code for d in definicion.read_package(
        api.FACTORY_FILE.read_bytes()).systems]
    for s in builtin:
        assert registry[s["code"]].raw == s["definition"]


def test_todos_los_vectores_de_crs_reference(reference, registry):
    """La prueba de las apps: ida ≤ 1 µm, vuelta ≤ 1e-10°, escala ≤ 1e-9, convergencia ≤ 1e-7°; en los heredados la
    misma región y el punto del datum viejo ≤ 1e-10°."""
    assert len(reference["systems"]) == 1255
    points = legacy = only = 0
    worst = dict(e=0.0, back=0.0, k=0.0, conv=0.0, datum=0.0)
    for s in reference["systems"]:
        d = registry[s["code"]]
        ref = s["definition"]
        assert d.name == ref["name"] and d.ellipsoid.a == ref["ellipsoid"]["semiMajorAxisMeters"]
        assert d.projection == ref.get("projection")
        for p in s["points"]:
            if "e" not in p:
                continue
            points += 1
            if p.get("projectionOnly"):
                # Datum por rejilla NTv2 real, que las pruebas no llevan: la proyección sola, desde el datum.
                only += 1
                e, n, conv, k = motor.project(d, p["datumLat"], p["datumLon"])
                worst["e"] = max(worst["e"], abs(e - p["e"]), abs(n - p["n"]))
                worst["conv"] = max(worst["conv"], abs(conv - p["convergence_deg"]))
                worst["k"] = max(worst["k"], abs(k - p["scale"]))
                b_lat, b_lon = motor.unproject(d, e, n)
                worst["back"] = max(worst["back"], abs(b_lat - p["datumLat"]), abs(b_lon - p["datumLon"]))
                continue
            lat, lon, t, _ = motor.datum_point(d, p["lat"], p["lon"])
            if "transformation" in p:
                legacy += 1
                assert t is not None and t.code == p["transformation"]
                worst["datum"] = max(worst["datum"], abs(lat - p["datumLat"]), abs(lon - p["datumLon"]))
                r_lat, r_lon, t2, _ = motor.receiver_point(d, lat, lon)
                assert t2.code == t.code
                worst["back"] = max(worst["back"], abs(r_lat - p["lat"]), abs(r_lon - p["lon"]))
            else:
                assert t is None
            e, n, conv, k = motor.project(d, lat, lon)
            worst["e"] = max(worst["e"], abs(e - p["e"]), abs(n - p["n"]))
            worst["conv"] = max(worst["conv"], abs(conv - p["convergence_deg"]))
            worst["k"] = max(worst["k"], abs(k - p["scale"]))
            b_lat, b_lon = motor.unproject(d, e, n)
            worst["back"] = max(worst["back"], abs(b_lat - lat), abs(b_lon - lon))
    assert points == 10904 and legacy == 628 and only == 8
    assert worst["e"] <= 1e-6 and worst["back"] <= 1e-10 and worst["k"] <= 1e-9 and worst["conv"] <= 1e-7
    assert worst["datum"] <= 1e-10


def test_proyecciones_con_el_registro_epsg(registry):
    projected = [d for d in registry.values() if d.is_projected]
    assert len(projected) == 1223
    assert all(motor.uses_epsg_registry(d) for d in projected)


def test_ntv2_contra_proj(reference):
    motor.add_grid_directory(APPS)
    grs80 = {"name": "GRS 1980", "semiMajorAxisMeters": 6378137.0, "inverseFlattening": 298.257222101}
    t = definicion.Transformation.parse({
        "code": "PRUEBA", "name": "Rejilla de prueba", "method": "ntv2", "gridFile": "prueba_ntv2.gsb",
        "target": {"code": "EPSG:4674", "frame": "SIRGAS 2000", "ellipsoid": grs80}, "accuracyMeters": 1.0,
        "areaOfUse": {"south": -30, "north": -20, "west": -50, "east": -40}, "source": "prueba"})
    source = definicion.Ellipsoid("GRS 1967 Modified", 6378160.0, 298.25)
    for p in reference["ntv2"]["points"]:
        lat, lon = motor.datum_forward(t, p["lat"], p["lon"], source)
        assert abs(lat - p["latShifted"]) <= 1e-9 and abs(lon - p["lonShifted"]) <= 1e-9
        b_lat, b_lon = motor.datum_inverse(t, lat, lon, source)
        assert abs(b_lat - p["lat"]) <= 1e-9 and abs(b_lon - p["lon"]) <= 1e-9
    with pytest.raises(motor.OutsideGrid):
        motor.datum_forward(t, 10.0, 10.0, source)


def test_paquete_mal_formado_se_rechaza():
    with pytest.raises(definicion.DefinitionError):
        definicion.read_package(b'{"format": "otro"}')
    with pytest.raises(definicion.DefinitionError, match="más nueva"):
        definicion.read_package(json.dumps({"format": "tresvizo-crs-package", "formatVersion": 2, "systems": [1]}))
    with pytest.raises(definicion.DefinitionError):
        definicion.read_package(json.dumps({"format": "tresvizo-crs-package", "formatVersion": 1, "systems": []}))


# --- transform(): de ECEF al sistema elegido -------------------------------------------------------------------------

def _ecef(lat, lon, h=0.0, code="EPSG:6365"):
    e = api.definition(code).ellipsoid
    return Ecef(*motor.geocentric(lat, lon, h, e))


def _elevation_factor(lat, h):
    """R/(R+h) con R = √(M·N) de GRS 1980: un metro en el terreno a la altura h es menos en el elipsoide."""
    a, f = 6378137.0, 1 / 298.257222101
    e2 = f * (2 - f)
    s2 = math.sin(math.radians(lat)) ** 2
    r = a * math.sqrt(1 - e2) / (1 - e2 * s2)
    return r / (r + h)


def test_transform_desde_ecef_da_los_vectores_de_las_apps(reference):
    worst = 0.0
    for s in reference["systems"]:
        if not s["builtIn"] or s["definition"]["kind"] != "projected":
            continue
        for p in s["points"]:
            c = api.transform(_ecef(p["lat"], p["lon"], 100.0, s["code"]), CoordinateChoice(s["code"], "", None))
            worst = max(worst, abs(c.easting_m - p["e"]), abs(c.northing_m - p["n"]))
            assert abs(c.scale_factor - p["scale"]) <= 1e-9 and abs(c.convergence_deg - p["convergence_deg"]) <= 1e-7
            assert abs(c.ellipsoidal_height_m - 100.0) < 1e-6
    assert worst <= 1e-6


def test_zona_automatica_y_geoide():
    pos = _ecef(21.856, -102.284, 1900.0)
    c = api.transform(pos, CoordinateChoice())                   # Mexico ITRF2008 / UTM, zona automática, GGM10
    assert c.crs_code == "EPSG:6368" and c.zone == "13N"
    assert c.label == "Mexico ITRF2008 / UTM zona 13N + GGM10 height"
    assert c.frame_text == "ITRF2008, época 2010.0" and not c.warnings
    n = mvgeoid.read(DATOS / "ggm10.mvgeoid").undulation(c.receiver_latitude_deg, c.receiver_longitude_deg)
    assert c.undulation_m == n and c.orthometric_height_m == pytest.approx(1900.0 - n, abs=1e-6)
    assert c.height_kind == "ortométrica" and c.geoid_uncertainty_m == pytest.approx(0.20)
    assert any("INEGI" in a for a in c.attribution)
    z14 = api.transform(pos, CoordinateChoice("mexico-itrf2008-utm", "14N", "ggm25"))
    assert z14.crs_code == "EPSG:6369" and z14.label.endswith("+ GGM25 height")
    with pytest.raises(UserError, match="fuera de las de México"):
        api.transform(_ecef(40.0, -3.7), CoordinateChoice())
    with pytest.raises(UserError, match="Mundo"):
        api.transform(_ecef(40.0, -3.7), CoordinateChoice("wgs84-utm", "", None))
    with pytest.raises(UserError, match="no está en este equipo"):
        api.transform(pos, CoordinateChoice("EPSG:9377", "", None))


def test_avisos_de_marco_y_de_cobertura():
    pos = _ecef(21.856, -102.284, 1900.0)
    w = api.transform(pos, CoordinateChoice("wgs84", "", None))
    assert w.northing_m is None and w.axis_order == "N,E"
    assert any("no transforma entre marcos ni épocas" in x for x in w.warnings)
    out = api.transform(_ecef(35.5, -100.0), CoordinateChoice("EPSG:6369", "", "ggm10"))
    assert out.orthometric_height_m is None and any("fuera de la cobertura" in x for x in out.warnings)
    assert any("fuera del área de uso" in x for x in out.warnings)


def test_transform_sigma():
    pos = _ecef(19.4, -99.13, 2240.0)
    choice = CoordinateChoice("EPSG:6369", "", None)
    c = api.transform(pos, choice)
    s = api.transform_sigma(pos, Enu(0.010, 0.010, 0.020), choice)
    k = c.scale_factor * _elevation_factor(19.4, 2240.0)     # factor combinado: escala de la proyección y elevación
    assert s.e == pytest.approx(0.010 * k, rel=1e-5) and s.n == pytest.approx(0.010 * k, rel=1e-5)
    assert s.u == 0.020
    # Con la matriz completa (σE ≠ σN): la convergencia gira la elipse y la diagonal cambia.
    r = api.enu_rotation(c.receiver_latitude_deg, c.receiver_longitude_deg)
    enu = [[0.03 ** 2, 0, 0], [0, 0.01 ** 2, 0], [0, 0, 0.02 ** 2]]
    cov = [[sum(r[k][i] * enu[k][m] * r[m][j] for k in range(3) for m in range(3)) for j in range(3)]
           for i in range(3)]
    g = api.transform_sigma(pos, Enu(0, 0, 0), choice, covariance_ecef=cov)
    gamma = math.radians(c.convergence_deg)
    expected_e = k * math.sqrt((0.03 * math.cos(gamma)) ** 2 + (0.01 * math.sin(gamma)) ** 2)
    expected_n = k * math.sqrt((0.03 * math.sin(gamma)) ** 2 + (0.01 * math.cos(gamma)) ** 2)
    assert g.e == pytest.approx(expected_e, rel=1e-5) and g.n == pytest.approx(expected_n, rel=1e-5)
    assert g.u == pytest.approx(0.02, rel=1e-9)
    assert api.height_sigma(0.02, api.transform(pos, CoordinateChoice())) == pytest.approx(math.hypot(0.02, 0.20))


# --- Descargar e instalar (red de mentira) ---------------------------------------------------------------------------

class _FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data))}


def _publish(monkeypatch, files: dict[str, bytes], items: list[dict], sequence: int = 10):
    """Un índice firmado con la llave de prueba y un servidor de mentira que lo sirve."""
    payload = {"format": indice.PAYLOAD_FORMAT, "formatVersion": 1, "sequence": sequence,
               "publishedAt": "2026-10-26T00:00:00Z", "release": "datos-prueba", "items": items}
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    public = ed25519.public_key(TEST_SECRET)
    envelope = {"format": indice.ENVELOPE_FORMAT, "formatVersion": 1, "algorithm": "Ed25519",
                "keyID": ed25519.key_id(public), "payload": base64.b64encode(body).decode(),
                "signature": base64.b64encode(ed25519.sign(TEST_SECRET, indice.SIGNING_CONTEXT + body)).decode()}
    served = dict(files)
    served[indice.INDEX_PATH] = json.dumps(envelope).encode()
    requests: list[tuple[str, dict]] = []

    def opener(url, headers):
        requests.append((url, headers))
        path = url[len(indice.BASE_URL):]
        if path not in served:
            raise urllib.error.HTTPError(url, 404, "no", {}, None)
        return _FakeResponse(served[path])

    monkeypatch.setattr(indice, "TRUSTED_KEYS", {ed25519.key_id(public): public})
    monkeypatch.setattr(biblioteca, "BUNDLED_INDEX", Path("/no/existe/datos-index.json"))
    monkeypatch.setattr(biblioteca, "opener", opener)
    monkeypatch.setattr(biblioteca.time, "sleep", lambda s: None)
    api.reload()
    return served, requests


def _items_for(fixtures):
    cr = gzip.decompress((APPS / "paquetes" / "crs-cr.json.gz").read_bytes())
    grid_bytes = base64.b64decode(fixtures["grid"]["base64"])
    grid = mvgeoid.decode(grid_bytes)
    bounds = {"south": 19.0, "north": 20.0, "west": -99.5, "east": -96.5}
    common = {"countries": ["MX"], "region": "México", "authority": "TresVizo", "license": "Prueba",
              "attribution": "Prueba", "bounds": bounds, "requires": []}
    items = [
        dict(common, kind="crs-package", id="crs-cr", version="1", revision=1, title="Costa Rica",
             file={"path": "download/datos-prueba/crs-cr-r1.json", "bytes": len(cr),
                   "sha256": hashlib.sha256(cr).hexdigest()},
             crsPackage={"format": "tresvizo-crs-package", "formatVersion": 1, "systems": 6}),
        dict(common, kind="geoid", id="prueba", version="1", revision=1, title="Rejilla de prueba",
             file={"path": "download/datos-prueba/prueba-r1.mvgeoid", "bytes": len(grid_bytes),
                   "sha256": hashlib.sha256(grid_bytes).hexdigest()}, geoid=grid.metadata),
    ]
    files = {"download/datos-prueba/crs-cr-r1.json": cr, "download/datos-prueba/prueba-r1.mvgeoid": grid_bytes}
    return items, files


def test_descargar_indice_e_instalar(data_dir, monkeypatch, fixtures):
    items, files = _items_for(fixtures)
    _publish(monkeypatch, files, items)
    with pytest.raises(UserError, match="no hay catálogo|bájalo"):
        api.install("crs-cr")
    progress = []
    api.download_index(progress=lambda f, m: progress.append((f, m)))
    assert progress and progress[-1][0] == 1.0
    assert {c.id for c in api.catalog() if c.downloadable} == {"crs-cr", "prueba"}
    item = api.install("crs-cr")
    assert item.installed and not item.update_available
    assert (data_dir / "sistemas" / "crs-cr.json").read_bytes() == files["download/datos-prueba/crs-cr-r1.json"]
    c = api.transform(_ecef(9.93, -84.08, 1150.0), CoordinateChoice("EPSG:5367", "", None))
    assert c.crs_name == "CR05 / CRTM05" and "EPSG:9751" in c.transformation and c.transformation_accuracy_m == 0.5
    assert api.install("prueba").installed
    g = api.transform(_ecef(19.6, -98.0, 10.0), CoordinateChoice("EPSG:6369", "", "prueba"))
    assert g.undulation_m == mvgeoid.decode(files["download/datos-prueba/prueba-r1.mvgeoid"]).undulation(
        g.receiver_latitude_deg, g.receiver_longitude_deg)
    api.uninstall("crs-cr")
    with pytest.raises(UserError, match="no está en este equipo"):
        api.transform(_ecef(9.93, -84.08), CoordinateChoice("EPSG:5367", "", None))
    with pytest.raises(UserError, match="fábrica"):
        api.uninstall("ggm10")


def test_archivo_alterado_no_se_instala(data_dir, monkeypatch, fixtures):
    items, files = _items_for(fixtures)
    tampered = bytearray(files["download/datos-prueba/crs-cr-r1.json"])
    tampered[100] ^= 1
    files["download/datos-prueba/crs-cr-r1.json"] = bytes(tampered)
    _publish(monkeypatch, files, items)
    api.download_index()
    with pytest.raises(UserError, match="SHA-256"):
        api.install("crs-cr")
    assert not (data_dir / "sistemas" / "crs-cr.json").exists()


def test_indice_mas_viejo_no_se_acepta(data_dir, monkeypatch, fixtures):
    items, files = _items_for(fixtures)
    _publish(monkeypatch, files, items, sequence=10)
    api.download_index()
    _publish(monkeypatch, files, items, sequence=9)
    with pytest.raises(UserError) as error:
        api.download_index()
    assert str(error.value) == indice.OLDER
    assert biblioteca.accepted_index()["sequence"] == 10


def test_indice_con_firma_mala_no_se_acepta(data_dir, monkeypatch, fixtures):
    items, files = _items_for(fixtures)
    served, _ = _publish(monkeypatch, files, items)
    env = json.loads(served[indice.INDEX_PATH])
    env["signature"] = base64.b64encode(bytes(64)).decode()
    served[indice.INDEX_PATH] = json.dumps(env).encode()
    with pytest.raises(UserError) as error:
        api.download_index()
    assert str(error.value) == indice.UNTRUSTED
    assert biblioteca.accepted_index() is None


def test_cancelar_y_sin_conexion(data_dir, monkeypatch, fixtures):
    items, files = _items_for(fixtures)
    _publish(monkeypatch, files, items)
    api.download_index()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        api.install("crs-cr", cancel=cancel)

    def offline(url, headers):
        raise urllib.error.URLError("sin red")
    monkeypatch.setattr(biblioteca, "opener", offline)
    with pytest.raises(UserError, match="no hay conexión"):
        api.install("crs-cr")


def test_reanuda_una_descarga_cortada(tmp_path, monkeypatch):
    data = os.urandom(200_000)
    calls = []

    def opener(url, headers):
        calls.append(headers.get("Range"))
        if len(calls) == 1:                      # la primera se corta a la mitad
            class Cut(_FakeResponse):
                def read(self, n=-1):
                    if self.tell() >= 100_000:
                        raise ConnectionError("cortada")
                    return super().read(min(n, 100_000 - self.tell()))
            return Cut(data)
        start = int(headers["Range"].split("=")[1].rstrip("-"))
        return _FakeResponse(data[start:], status=206)
    monkeypatch.setattr(biblioteca, "opener", opener)
    monkeypatch.setattr(biblioteca.time, "sleep", lambda s: None)
    out = biblioteca.download("https://x/y", tmp_path / "y.bin", expected_bytes=len(data))
    assert out.read_bytes() == data and calls == [None, "bytes=100000-"]


@pytest.mark.skipif(os.environ.get("TRESVIZO_PPK_RED") != "1",
                    reason="usa la red: TRESVIZO_PPK_RED=1 para comprobar el índice publicado de verdad")
def test_indice_publicado_de_verdad(data_dir):
    payload_items = api.download_index()
    assert any(c.id == "crs-mx" for c in payload_items)
    assert api.install("crs-sv").installed


def test_cualquier_clase_registrada_se_instala_con_la_misma_verificacion(data_dir, monkeypatch, fixtures):
    """Las redes CORS (§3.0) van por el mismo índice firmado y la misma instalación comprobada."""
    items, files = _items_for(fixtures)
    # paquete válido según docs/formato-red-cors.md (el motor registra la comprobación completa de la clase)
    red = json.dumps({"format": "tresvizo-cors-network", "formatVersion": 1, "id": "rgna", "revision": 1,
                      "title": "RGNA — INEGI, México", "countries": ["MX"], "authority": "INEGI",
                      "frame": {"name": "ITRF2008", "epoch": 2010.0}, "license": "libre con atribución",
                      "attribution": "Fuente: INEGI", "stations": [{"id": "UGTO", "x": -1164730.1759,
                                                                    "y": -5843944.6861, "z": 2272414.2366}]}).encode()
    common = {k: v for k, v in items[0].items() if k in ("countries", "region", "authority", "license", "attribution",
                                                          "bounds", "requires")}
    items.append(dict(common, kind="cors-network", id="rgna", version="1", revision=1, title="RGNA — INEGI, México",
                      file={"path": "download/datos-prueba/rgna-r1.json", "bytes": len(red),
                            "sha256": hashlib.sha256(red).hexdigest()}))
    files["download/datos-prueba/rgna-r1.json"] = red
    _publish(monkeypatch, files, items)
    api.download_index()
    assert any(c.id == "rgna" and c.kind == "cors-network" for c in api.catalog())
    entry = biblioteca.install("rgna")
    assert entry["title"] == "RGNA — INEGI, México"
    assert biblioteca.read_installed("cors-network", "rgna") == red
    assert [i["id"] for i in biblioteca.items("cors-network")] == ["rgna"]
    biblioteca.uninstall("rgna")
    assert biblioteca.read_installed("cors-network", "rgna") is None
