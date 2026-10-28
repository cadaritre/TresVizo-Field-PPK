"""Ajustes → Actualizaciones en la interfaz real: sin llave de publicación solo avisa y abre la página de descarga
(sin bajar ni abrir instaladores y sin hablar de llaves ni firmas); con llave, el camino firmado de siempre."""
import pytest

tk = pytest.importorskip('tkinter')
from tkinter import ttk  # noqa: E402

from test_ui_widgets import app  # noqa: E402,F401 (el fixture de la ventana)

from app import updates  # noqa: E402


def release(version='9.1.0'):
    return updates.parse_release({'tag_name': f'v{version}', 'name': f'TresVizo Field PPK {version}',
                                  'body': '- Exportar DXF con opciones', 'published_at': '2026-11-02T10:00:00Z',
                                  'html_url': f'{updates.RELEASES_PAGE}/tag/v{version}', 'assets': []})


def texts(widget):
    out = []
    for child in widget.winfo_children():
        try:
            out.append(str(child.cget('text')))
        except tk.TclError:
            pass
        out += texts(child)
    return out


def buttons(widget):
    out = []
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button):
            out.append(child)
        out += buttons(child)
    return out


def test_unsigned_release_opens_its_download_page(app, monkeypatch):
    monkeypatch.setattr(updates, 'PUBLIC_KEY_HEX', '')
    monkeypatch.setattr(updates, 'installer_name', lambda version, *a: f'TresVizo-Field-PPK-{version}-x64.msi')
    opened = []
    monkeypatch.setattr('webbrowser.open', lambda url, new=0: opened.append(url))
    fetched = []
    monkeypatch.setattr(updates, 'fetch_verified_installer', lambda *a, **k: fetched.append(a))
    page = app.show('Ajustes')
    page.show_release(release())
    app.update()
    words = ' '.join(texts(page.update_body)+[page.update_status.cget('text')])
    assert 'Hay una versión nueva: 9.1.0' in words and 'SHA-256' in words
    # Lo único que se dice de firmas es el texto de la primera apertura (sin culpa ni miedo).
    assert 'llave' not in words.lower() and 'firma válida' not in words and 'firma es válida' not in words
    assert 'no está firmada con un certificado comercial' in words
    labels = [b.cget('text') for b in buttons(page.update_body)]
    assert labels == ['Abrir la página de descarga']
    buttons(page.update_body)[0].invoke()
    assert opened == [updates.RELEASES_PAGE+'/tag/v9.1.0'] and not fetched
    card = ' '.join(texts(page))
    assert 'abre su página de descarga' in card and 'firma es válida' not in card


def test_signed_release_keeps_download_and_install(app, monkeypatch):
    monkeypatch.setattr(updates, 'PUBLIC_KEY_HEX', 'ab'*32)
    monkeypatch.setattr(updates, 'installer_name', lambda version, *a: f'TresVizo-Field-PPK-{version}-x64.msi')
    page = app.show('Ajustes')
    page.show_release(release())
    app.update()
    labels = [b.cget('text') for b in buttons(page.update_body)]
    assert labels[:2] == ['Bajar e instalar', 'Ver en GitHub']


def test_up_to_date(app):
    page = app.show('Ajustes')
    page.show_release(release('0.0.1'))
    assert 'versión más reciente' in page.update_status.cget('text')
    assert not buttons(page.update_body)
