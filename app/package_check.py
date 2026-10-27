"""Comprobación del programa empaquetado (`--self-test INFORME.json`): carga todo lo que se incluyó, sin tocar las
preferencias ni los datos del usuario, y deja un informe JSON. La usan el script de distribución y la CI."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import struct
import sys
import tempfile
import traceback


def run_check(report) -> int:
    from tresvizo_ppk.version import __version__
    result = {'version': __version__, 'python': platform.python_version(), 'platform': sys.platform,
              'machine': platform.machine(), 'bits': struct.calcsize('P')*8, 'frozen': bool(getattr(sys, 'frozen', False)),
              'ok': False, 'checks': [], 'failed': []}

    def check(name, fn):
        try:
            detail = fn()
            result['checks'].append({'name': name, 'ok': True, 'detail': detail})
        except Exception:  # noqa: BLE001 - cada comprobación se informa por separado
            result['checks'].append({'name': name, 'ok': False, 'detail': traceback.format_exc()})
            result['failed'].append(name)

    app = None
    with tempfile.TemporaryDirectory(prefix='tvppk-autoprueba-') as folder:
        folder = Path(folder)

        def interface():
            nonlocal app
            from app.main_window import Application
            app = Application(folder/'preferencias')
            app.withdraw()
            app.update()
            assert app.tk.call('package', 'present', 'tkdnd'), 'tkdnd no cargó'
            for name in app.builders():
                app.show(name)
                app.update()
            return f"Tk {app.tk.call('info', 'patchlevel')}, tkdnd {app.tk.call('package', 'present', 'tkdnd')}, " \
                   f'{len(app.pages)} pantallas'

        def modules():
            from app.backend import Backend
            backend = Backend.load()
            assert not backend.errors, backend.errors
            return sorted(backend.parts)

        def tools():
            from tresvizo_ppk.core import tools as core_tools
            found = {}
            for name in ('rnx2rtkp', 'convbin', 'crx2rnx'):
                found[name] = str(core_tools.find_tool(name))
            return found

        def coordinates():
            from tresvizo_ppk.crs import api as crs
            from tresvizo_ppk.model import CoordinateChoice, Ecef
            # Un punto en Aguascalientes: debe caer en la zona 13N y tener cota con GGM10.
            c = crs.transform(Ecef(-1260416.5, -5788557.9, 2360324.3), CoordinateChoice('mexico-itrf2008-utm', '',
                                                                                          'ggm10'))
            assert c.zone == '13N' and c.orthometric_height_m is not None
            return f'{c.crs_name}: N {c.northing_m:.3f} E {c.easting_m:.3f} H {c.orthometric_height_m:.3f}'

        def pdf():
            from reportlab.pdfgen import canvas
            import pypdfium2
            path = folder/'prueba.pdf'
            document = canvas.Canvas(str(path))
            document.drawString(40, 800, 'Comprobación del paquete')
            document.save()
            with pypdfium2.PdfDocument(str(path)) as pages:
                page = pages[0]
                bitmap = page.render(scale=0.5)
                width = bitmap.width
                bitmap.close()
                page.close()
            assert width > 0
            return 'ReportLab + PDFium'

        def charts():
            import matplotlib
            matplotlib.use('Agg')
            from matplotlib import pyplot
            figure = pyplot.figure()
            figure.savefig(folder/'grafica.png')
            pyplot.close(figure)
            return matplotlib.__version__

        def assets():
            from app.paths import ASSETS, ROOT
            for name in ('marca.png', 'marca_icono.png', 'icons/LICENSE.txt', 'icons/house.png'):
                assert (ASSETS/name).is_file(), name
            assert (ROOT/'third_party'/'LICENCIAS.md').is_file(), 'faltan los avisos de terceros'
            # Datos incluidos del motor, los sistemas y el índice firmado.
            package = ROOT/'tresvizo_ppk'
            data = {'crs/datos': ('fabrica.json', 'ggm10.mvgeoid', 'ggm25.mvgeoid'),
                    'datos/incluido': ('datos-index.json',)}
            for folder, names in data.items():
                for name in names:
                    assert (package/folder/name).is_file(), f'falta tresvizo_ppk/{folder}/{name}'
            counts = {folder: sum(1 for p in (package/folder).rglob('*') if p.is_file()) for folder in data}
            return f'marca, iconos, licencias y datos incluidos {counts}'

        for name, fn in [('interfaz', interface), ('modulos', modules), ('rtklib', tools), ('sistemas', coordinates),
                         ('pdf', pdf), ('graficas', charts), ('recursos', assets)]:
            check(name, fn)
        if app is not None:
            try:
                app.destroy()
            except Exception:  # noqa: BLE001
                pass
    result['ok'] = not result['failed']
    Path(report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1
