"""Genera el símbolo de TresVizo Field PPK (herramienta de desarrollo; solo Pillow, y iconutil en macOS para .icns).

Misma familia que TresVizo Field: hexágono azul de contorno y, dentro, el vértice geodésico (triángulo) con su punto
de estación, que es lo que hace este programa (ligar un punto a la red). El nombre se escribe con texto nativo junto al
símbolo; aquí no se inventa ningún logotipo tipográfico.
"""
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT/'assets'
S = 4096   # lienzo de trabajo (4× el tamaño final) para bordes suaves


def gradient(size, top, bottom):
    image = Image.new('RGBA', (size, size))
    draw = ImageDraw.Draw(image)
    a, b = [tuple(int(c[i:i+2], 16) for i in (1, 3, 5)) for c in (top, bottom)]
    for y in range(size):
        t = y/(size-1)
        draw.line([(0, y), (size, y)], fill=tuple(round(a[i]+(b[i]-a[i])*t) for i in range(3))+(255,))
    return image


def hexagon(cx, cy, r):
    return [(cx+r*math.cos(math.radians(90+60*k)), cy-r*math.sin(math.radians(90+60*k))) for k in range(6)]


def symbol(dark=False):
    c = S/2
    mask = Image.new('L', (S, S))
    draw = ImageDraw.Draw(mask)
    draw.polygon(hexagon(c, c, S*0.47), fill=255)
    draw.polygon(hexagon(c, c, S*0.47-S*0.055), fill=0)
    frame = gradient(S, '#9CCBFF', '#6FA8EE') if dark else gradient(S, '#1F5EAD', '#163F73')
    out = Image.new('RGBA', (S, S))
    out.paste(frame, (0, 0), mask)
    # Vértice geodésico: triángulo con el punto de estación al centro.
    tri = Image.new('L', (S, S))
    td = ImageDraw.Draw(tri)
    h = S*0.40
    top, base = c-h*0.58, c+h*0.42
    half = h/math.sqrt(3)
    td.polygon([(c, top), (c+half, base), (c-half, base)], fill=255)
    td.ellipse((c-S*0.045, c+S*0.02-S*0.045, c+S*0.045, c+S*0.02+S*0.045), fill=0)
    out.paste(gradient(S, '#3AA5FF', '#0A5FD0'), (0, 0), tri)
    # Línea base hacia la red: tres puntos de estación pequeños sobre el hexágono interior.
    dots = Image.new('L', (S, S))
    dd = ImageDraw.Draw(dots)
    for k in (1, 3, 5):
        x, y = hexagon(c, c, S*0.47-S*0.0275)[k]
        r = S*0.052
        dd.ellipse((x-r, y-r, x+r, y+r), fill=255)
    out.paste(gradient(S, '#3AA5FF', '#0A5FD0') if not dark else gradient(S, '#BFE0FF', '#8CC0FF'), (0, 0), dots)
    return out


def tile(mark):
    """Icono de aplicación: fondo claro redondeado (como el de TresVizo Field) con el símbolo."""
    background = gradient(S, '#FBFDFF', '#E3EEFB')
    mask = Image.new('L', (S, S))
    ImageDraw.Draw(mask).rounded_rectangle((S*0.05, S*0.05, S*0.95, S*0.95), radius=S*0.2, fill=255)
    out = Image.new('RGBA', (S, S))
    out.paste(background, (0, 0), mask)
    small = mark.resize((int(S*0.66),)*2, Image.Resampling.LANCZOS)
    out.alpha_composite(small, (int(S*0.17),)*2)
    return out


def main():
    mark, dark = symbol(), symbol(dark=True)
    final = {name: image.resize((1024, 1024), Image.Resampling.LANCZOS)
             for name, image in [('marca', mark), ('marca_oscuro', dark), ('marca_icono', tile(mark))]}
    for name, image in final.items():
        image.save(ASSETS/(name+'.png'), optimize=True)
    # Windows: el símbolo solo se lee mejor a 16–32 px que el mosaico.
    final['marca'].save(ASSETS/'marca.ico', sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    if sys.platform == 'darwin' and shutil.which('iconutil'):
        with tempfile.TemporaryDirectory() as folder:
            iconset = Path(folder)/'marca.iconset'
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    px = size*scale
                    name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
                    final['marca_icono'].resize((px, px), Image.Resampling.LANCZOS).save(iconset/name)
            subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(ASSETS/'marca.icns')], check=True)
    print('Marca generada en', ASSETS)


if __name__ == '__main__':
    main()
