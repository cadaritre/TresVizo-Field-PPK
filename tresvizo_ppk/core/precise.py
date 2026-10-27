"""Efemérides precisas (órbitas SP3) a petición del usuario, de servidores públicos sin cuenta.

Fuentes, en orden:
1. ESA/ESOC, Navigation Support Office (https://navigation-office.esa.int/products/gnss-products/): productos
   ESA0OPS finales, rápidos y ultrarrápidos, multi-GNSS (GPS, GLONASS, Galileo, BeiDou y QZSS).
2. IGS en el BKG (https://igs.bkg.bund.de/root_ftp/IGS/products/): la combinación IGS0OPS (GPS y GLONASS) si la ESA
   no contesta.

Para posicionamiento relativo bastan las órbitas: los relojes de satélite se cancelan en las dobles diferencias
(RTKLIB toma el reloj del propio SP3). Se baja cada día que toque la sesión, más el anterior y el siguiente si la
sesión está a menos de 2 h del cambio de día (la interpolación de la órbita necesita puntos a los dos lados).
"""
from __future__ import annotations

import gzip
import shutil
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errors import UserError
from . import tools
from .geodesy import utc_to_gps

USER_AGENT = "TresVizo-Field-PPK"
TIMEOUT_S = 60


@dataclass(frozen=True)
class Product:
    server: str
    center: str
    kind: str                     # «final», «rápida», «ultrarrápida»
    url: str
    filename: str


def gps_week(day: datetime) -> int:
    return (day.replace(tzinfo=None) - datetime(1980, 1, 6)).days // 7


def _candidates(day: datetime) -> list[Product]:
    """Archivos SP3 que cubren un día (UTC/GPS), del mejor al peor."""
    week = gps_week(day)
    stamp = f"{day.year:04d}{day.timetuple().tm_yday:03d}"
    esa = f"https://navigation-office.esa.int/products/gnss-products/{week}/"
    bkg = f"https://igs.bkg.bund.de/root_ftp/IGS/products/{week}/"
    out = [
        Product("ESA/ESOC", "ESA", "final", esa, f"ESA0OPSFIN_{stamp}0000_01D_05M_ORB.SP3.gz"),
        Product("IGS (BKG)", "IGS", "final", bkg, f"IGS0OPSFIN_{stamp}0000_01D_15M_ORB.SP3.gz"),
        Product("ESA/ESOC", "ESA", "rápida", esa, f"ESA0OPSRAP_{stamp}0000_01D_05M_ORB.SP3.gz"),
        Product("IGS (BKG)", "IGS", "rápida", bkg, f"IGS0OPSRAP_{stamp}0000_01D_15M_ORB.SP3.gz"),
    ]
    # Ultrarrápidas: 48 h desde las 00, 06, 12 o 18 h; la de las 00 h del día cubre el día completo.
    out.append(Product("ESA/ESOC", "ESA", "ultrarrápida", esa, f"ESA0OPSULT_{stamp}0000_02D_05M_ORB.SP3.gz"))
    out.append(Product("IGS (BKG)", "IGS", "ultrarrápida", bkg, f"IGS0OPSULT_{stamp}0000_02D_15M_ORB.SP3.gz"))
    return out


def _fetch(url: str, target: Path, cancel: threading.Event | None) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response, open(tmp, "wb") as out:
            while True:
                tools.check(cancel)
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
    except urllib.error.HTTPError as error:
        tmp.unlink(missing_ok=True)
        if error.code in (403, 404):
            return False
        raise
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(target)
    return True


def session_days(start: datetime, end: datetime) -> list[datetime]:
    start_g = utc_to_gps(start.astimezone(timezone.utc)) - timedelta(hours=2)
    end_g = utc_to_gps(end.astimezone(timezone.utc)) + timedelta(hours=2)
    day = start_g.replace(hour=0, minute=0, second=0, microsecond=0)
    days = []
    while day <= end_g:
        days.append(day)
        day += timedelta(days=1)
    return days


def download_precise_products(start: datetime, end: datetime, workdir: Path,
                              progress: tools.Progress | None = None,
                              cancel: threading.Event | None = None) -> list[Path]:
    """Baja (o reutiliza) las órbitas SP3 que cubren [start, end] (UTC) y devuelve los SP3 descomprimidos.

    Por cada día prueba final → rápida → ultrarrápida en la ESA y en el BKG. Si a algún día no le encuentra
    nada, avisa con UserError (el usuario puede procesar con efemérides difundidas)."""
    folder = workdir / "precisas"
    folder.mkdir(parents=True, exist_ok=True)
    days = session_days(start, end)
    result: list[Path] = []
    missing: list[str] = []
    for i, day in enumerate(days):
        tools.check(cancel)
        found = None
        for product in _candidates(day):
            sp3 = folder / product.filename[:-3]
            if sp3.is_file() and sp3.stat().st_size > 0:
                found = sp3
                break
            if progress:
                progress(i / max(1, len(days)), f"Bajando la órbita {product.kind} de {product.server} del "
                                                f"{day:%Y-%m-%d}: {product.url}{product.filename}")
            gz = folder / product.filename
            try:
                ok = _fetch(product.url + product.filename, gz, cancel)
            except (urllib.error.URLError, TimeoutError, OSError):
                ok = False
            if not ok:
                continue
            try:
                with gzip.open(gz, "rb") as src, open(sp3, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            except (OSError, EOFError):
                sp3.unlink(missing_ok=True)
                continue
            finally:
                gz.unlink(missing_ok=True)
            found = sp3
            break
        if found is None:
            missing.append(f"{day:%Y-%m-%d}")
        else:
            result.append(found)
    if missing:
        raise UserError("No hay órbitas precisas publicadas para " + ", ".join(missing) + " (ni finales, ni "
                        "rápidas, ni ultrarrápidas) o no hay conexión. Procesa con efemérides difundidas o intenta "
                        "más tarde.")
    if progress:
        progress(1.0, "Órbitas precisas listas.")
    return result


def describe(files: list[Path]) -> str:
    """«ESA final (ESA0OPSFIN_…), …» para el informe."""
    names = []
    for f in files:
        n = f.name
        kind = "final" if "FIN_" in n else "rápida" if "RAP_" in n else "ultrarrápida" if "ULT_" in n else ""
        names.append(f"{n[:3]} {kind} ({n})" if kind else n)
    return "; ".join(names)
