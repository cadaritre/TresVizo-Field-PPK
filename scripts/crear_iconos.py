"""Rasteriza los iconos Lucide que usa la interfaz (herramienta de desarrollo, no se incluye en el programa).

Uso: python scripts/crear_iconos.py RUTA/lucide-static/package
Requiere cairosvg (y la biblioteca cairo del sistema) solo en el equipo que regenera los iconos. Copia el SVG
original, su versión PNG de 96 px (solo el canal alfa importa: el color se aplica en tiempo de ejecución con los
tokens del tema) y la licencia ISC de Lucide.
"""
import io
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'assets'/'icons'
NAMES = ['house', 'map-pin', 'route', 'globe', 'radio-tower', 'settings', 'info', 'folder-open', 'folder-plus',
         'file-plus', 'save', 'play', 'circle-stop', 'x', 'file-text', 'download', 'upload', 'file-up', 'check',
         'circle-check', 'triangle-alert', 'circle-alert', 'chevron-left', 'chevron-right', 'trash-2', 'refresh-cw',
         'search', 'satellite', 'layers', 'mountain', 'ruler', 'image', 'external-link', 'arrow-left', 'arrow-right',
         'crosshair', 'map', 'chart-scatter', 'list', 'clock', 'hard-drive', 'file-down', 'share', 'eye', 'pencil',
         'plus', 'copy', 'circle-x']


def main(package):
    import cairosvg
    from PIL import Image
    package = Path(package)
    version = json.loads((package/'package.json').read_text(encoding='utf-8'))['version']
    OUT.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        svg = (package/'icons'/(name+'.svg')).read_bytes()
        (OUT/(name+'.svg')).write_bytes(svg)
        png = cairosvg.svg2png(bytestring=svg.replace(b'currentColor', b'#000000'), output_width=96, output_height=96)
        with Image.open(io.BytesIO(png)) as image:
            image.convert('RGBA').save(OUT/(name+'.png'), optimize=True)
    shutil.copyfile(package/'LICENSE', OUT/'LICENSE.txt')
    (OUT/'FUENTE.txt').write_text(f'Lucide {version} (paquete npm lucide-static), licencia ISC (LICENSE.txt).\n'
                                  'https://lucide.dev · https://github.com/lucide-icons/lucide\n'
                                  'Regenerar con: python scripts/crear_iconos.py RUTA/lucide-static/package\n', encoding='utf-8')
    print(f'{len(NAMES)} iconos de Lucide {version} en {OUT}')


if __name__ == '__main__':
    main(sys.argv[1])
