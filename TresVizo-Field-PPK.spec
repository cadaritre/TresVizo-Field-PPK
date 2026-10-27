# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: un programa en carpeta (Windows: TresVizo-Field-PPK.exe + _internal; macOS: TresVizo Field PPK.app).
# Lo arma scripts/distribuir.py; los binarios de RTKLIB los compila antes scripts/build_rtklib.py.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))
from tresvizo_ppk.version import __version__  # noqa: E402
from tresvizo_ppk.core.tools import platform_tag  # noqa: E402

NAME = 'TresVizo-Field-PPK'
BIN = ROOT/'third_party'/'bin'/platform_tag()
TOOLS = [p for p in BIN.glob('*') if p.is_file() and p.name.split('.')[0] in ('rnx2rtkp', 'convbin', 'crx2rnx')]
if len(TOOLS) < 3:
    raise SystemExit(f'Faltan los programas de RTKLIB en {BIN}: corre antes python scripts/build_rtklib.py')

datas = [(str(ROOT/'assets'), 'assets'), (str(ROOT/'LICENSE'), '.'),
         (str(ROOT/'third_party'/'LICENCIAS.md'), 'third_party')]
for folder in ('rtklib', 'rnxcmp'):
    for name in ('license.txt', 'LICENSE', 'ORIGEN.md', 'README.md'):
        path = ROOT/'third_party'/folder/name
        if path.is_file():
            datas.append((str(path), f'third_party/{folder}'))
datas += collect_data_files('tresvizo_ppk', excludes=['**/__pycache__/**'])
# Explícitos además (la autoprueba comprueba que estén): sistemas y geoides de fábrica e índice firmado incluido.
for folder in ('crs/datos', 'datos/incluido'):
    datas.append((str(ROOT/'tresvizo_ppk'/folder), f'tresvizo_ppk/{folder}'))
binaries = [(str(p), 'bin') for p in TOOLS]
hiddenimports = ['tresvizo_ppk.core.api', 'tresvizo_ppk.crs.api', 'tresvizo_ppk.report.api', 'tresvizo_ppk.export.api',
                 'app.home', 'app.static_ui', 'app.points_ui', 'app.crs_ui', 'app.stations_ui', 'app.settings_ui']
for package in ('pypdfium2', 'pypdfium2_raw', 'tkinterdnd2', 'pyproj'):
    extra_data, extra_binaries, extra_imports = collect_all(package)
    datas += extra_data
    binaries += extra_binaries
    hiddenimports += extra_imports

a = Analysis(['main.py'], pathex=[str(ROOT)], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], hooksconfig={'matplotlib': {'backends': ['Agg']}}, runtime_hooks=[],
             excludes=['pytest', 'pypdf', 'IPython', 'tkinter.test', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6'],
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
icon = str(ROOT/'assets'/('marca.icns' if sys.platform == 'darwin' else 'marca.ico'))
version_file = ROOT/'build'/'windows-version.txt'
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None, icon=[icon],
          version=str(version_file) if sys.platform == 'win32' and version_file.is_file() else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name=NAME)

if sys.platform == 'darwin':
    app = BUNDLE(coll, name='TresVizo Field PPK.app', icon=icon, bundle_identifier='com.tresvizo.fieldppk',
                 version=__version__,
                 info_plist={
                     'CFBundleName': 'TresVizo Field PPK', 'CFBundleDisplayName': 'TresVizo Field PPK',
                     'CFBundleShortVersionString': __version__, 'CFBundleVersion': __version__,
                     'CFBundleDevelopmentRegion': 'es', 'NSHighResolutionCapable': True,
                     'LSMinimumSystemVersion': '11.0', 'NSHumanReadableCopyright': 'Licencia MIT · TresVizo',
                     'CFBundleDocumentTypes': [{
                         'CFBundleTypeName': 'Proyecto de TresVizo Field PPK', 'CFBundleTypeRole': 'Editor',
                         'LSHandlerRank': 'Owner', 'CFBundleTypeExtensions': ['tvppk'],
                         'LSItemContentTypes': ['com.tresvizo.fieldppk.proyecto']}],
                     'UTExportedTypeDeclarations': [{
                         'UTTypeIdentifier': 'com.tresvizo.fieldppk.proyecto',
                         'UTTypeDescription': 'Proyecto de TresVizo Field PPK',
                         'UTTypeConformsTo': ['public.json', 'public.data'],
                         'UTTypeTagSpecification': {'public.filename-extension': ['tvppk']}}]})
