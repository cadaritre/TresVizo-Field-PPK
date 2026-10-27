"""Ajustes (datos del usuario para el informe, unidades, carpeta de datos, apariencia) y Acerca de."""
from __future__ import annotations

from pathlib import Path
import platform
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from tresvizo_ppk.version import __version__

from app.components import ScrollFrame, card, scrolled, wrap_label
from app.paths import APP_NAME, ROOT
from app.settings import UNITS, SettingsError
from app.themes import BUILTINS, palette


def apply_data_dir(app):
    """La carpeta de datos elegida también guarda lo bajado de Sistemas y geoides."""
    crs = app.backend.crs
    if crs is not None and app.settings.values.data_dir and callable(getattr(crs, 'set_data_dir', None)):
        crs.set_data_dir(Path(app.settings.values.data_dir)/'Sistemas y geoides')


class SettingsPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        ttk.Label(self, text='Ajustes', style='Title.TLabel').pack(anchor='w')
        wrap_label(self, 'Se guardan en este equipo. Nada se envía a ningún lado.').pack(anchor='w', fill='x',
                                                                                        pady=(4, 14))
        footer = ttk.Frame(self, padding=(0, 10, 0, 0))
        footer.pack(side='bottom', fill='x')
        self.save_button = ttk.Button(footer, text='Guardar ajustes', style='Primary.TButton', command=self.save)
        app.icons.bind(self.save_button, 'save')
        self.save_button.pack(side='right')
        self.saved = ttk.Label(footer, text='', style='Subtitle.TLabel')
        self.saved.pack(side='right', padx=12)
        scroll = ScrollFrame(self, padding=(0, 0, 12, 12))
        scroll.pack(fill='both', expand=True)
        body = scroll.body
        v = app.settings.values
        frame, inner = card(body, 'Datos para el informe', 'Encabezado de los informes PDF.')
        frame.pack(fill='x')
        inner.columnconfigure((0, 1), weight=1, uniform='user')
        self.vars = {key: tk.StringVar(value=getattr(v, key)) for key in ('company', 'surveyor', 'license_id')}
        for index, (key, label) in enumerate([('company', 'Empresa'), ('surveyor', 'Responsable'),
                                              ('license_id', 'Cédula profesional')]):
            cell = ttk.Frame(inner, style='Flat.Card.TFrame', padding=(0, 0, 12, 10))
            cell.grid(row=index//2, column=index % 2, sticky='nsew')
            ttk.Label(cell, text=label, style='Card.Strong.TLabel').pack(anchor='w', pady=(0, 4))
            ttk.Entry(cell, textvariable=self.vars[key]).pack(fill='x')
            self.vars[key].trace_add('write', lambda *a: self.touch())
        logo_row = ttk.Frame(inner, style='Flat.Card.TFrame')
        logo_row.grid(row=2, column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.logo_label = ttk.Label(logo_row, style='Card.Subtitle.TLabel')
        self.logo_label.pack(side='left', padx=(0, 14))
        choose = ttk.Button(logo_row, text='Elegir logo…', command=self.choose_logo)
        app.icons.bind(choose, 'image')
        choose.pack(side='left')
        self.clear_logo_button = ttk.Button(logo_row, text='Quitar logo', style='Card.Link.TButton',
                                            command=self.clear_logo)
        self.clear_logo_button.pack(side='left', padx=8)
        self.show_logo()
        frame, inner = card(body, 'Unidades', 'Para mostrar distancias y coordenadas en pantalla. El cálculo, el '
                                              'proyecto y la exportación van siempre en metros.')
        frame.pack(fill='x', pady=(16, 0))
        row = ttk.Frame(inner, style='Flat.Card.TFrame')
        row.pack(fill='x')
        self.units = tk.StringVar(value=UNITS[v.units][0])
        units = ttk.Combobox(row, textvariable=self.units, values=[u[0] for u in UNITS.values()], state='readonly',
                             width=30)
        units.pack(side='left')
        units.bind('<<ComboboxSelected>>', lambda e: self.touch())
        ttk.Label(row, text='Decimales', style='Card.TLabel').pack(side='left', padx=(18, 6))
        self.decimals = tk.StringVar(value=str(v.decimals))
        ttk.Spinbox(row, from_=0, to=4, textvariable=self.decimals, width=4, state='readonly',
                    command=self.touch).pack(side='left')
        frame, inner = card(body, 'Carpeta de datos', 'Donde se proponen los proyectos nuevos y se guardan las '
                                                      'descargas (RINEX de las redes, efemérides, sistemas y geoides).')
        frame.pack(fill='x', pady=(16, 0))
        self.folder = ttk.Label(inner, text=str(app.settings.data_dir()), style='Card.TLabel')
        self.folder.pack(anchor='w')
        row = ttk.Frame(inner, style='Flat.Card.TFrame')
        row.pack(fill='x', pady=(8, 0))
        change = ttk.Button(row, text='Cambiar…', command=self.choose_folder)
        app.icons.bind(change, 'folder-open')
        change.pack(side='left')
        open_button = ttk.Button(row, text='Abrir', style='Card.Link.TButton', command=self.open_folder)
        app.icons.bind(open_button, 'external-link', 16)
        open_button.pack(side='left', padx=8)
        self.new_folder = None
        frame, inner = card(body, 'Apariencia')
        frame.pack(fill='x', pady=(16, 0))
        self.theme = tk.StringVar(value=v.theme)
        row = ttk.Frame(inner, style='Flat.Card.TFrame')
        row.pack(fill='x')
        for name in BUILTINS:
            ttk.Radiobutton(row, text=name, value=name, variable=self.theme, style='Card.TRadiobutton',
                            command=self.preview_theme).pack(side='left', padx=(0, 18))
        self.reduce = tk.BooleanVar(value=v.reduce_motion)
        ttk.Checkbutton(inner, text='Reducir movimiento (sin transiciones)', variable=self.reduce,
                        style='Card.TCheckbutton', command=self.touch).pack(anchor='w', pady=(10, 0))
        self.build_updates(body)
        frame, inner = card(body, 'Registro de errores', 'Si algo falla, el detalle técnico queda en este equipo '
                                                         'para revisarlo; no se envía.')
        frame.pack(fill='x', pady=(16, 0))
        log = ttk.Button(inner, text='Abrir carpeta del registro', style='Card.Link.TButton',
                         command=lambda: self.app.guard(self.open_log))
        app.icons.bind(log, 'folder-open', 16)
        log.pack(anchor='w')

    def build_updates(self, body):
        from tresvizo_ppk.version import __version__ as installed
        frame, inner = card(body, 'Actualizaciones', f'Versión instalada: {installed}. Las versiones nuevas se publican '
                                                     'en github.com/cadaritre/TresVizo-Field-PPK y se instalan solo si '
                                                     'su firma es válida.')
        frame.pack(fill='x', pady=(16, 0))
        self.check_updates = tk.BooleanVar(value=self.app.settings.values.check_updates)
        ttk.Checkbutton(inner, text='Buscar al abrir el programa (solo consulta GitHub; ninguna otra conexión)',
                        variable=self.check_updates, style='Card.TCheckbutton', command=self.touch).pack(anchor='w')
        row = ttk.Frame(inner, style='Flat.Card.TFrame')
        row.pack(fill='x', pady=(10, 0))
        self.update_button = ttk.Button(row, text='Buscar actualizaciones', command=self.look_for_updates)
        self.app.icons.bind(self.update_button, 'refresh-cw')
        self.update_button.pack(side='left')
        self.update_status = ttk.Label(row, text='', style='Card.Subtitle.TLabel')
        self.update_status.pack(side='left', padx=12)
        self.update_body = ttk.Frame(inner, style='Flat.Card.TFrame')
        self.update_body.pack(fill='x')
        self.update_worker = None

    def look_for_updates(self, quiet=False):
        from app import tasks, updates
        self.update_button.state(['disabled'])
        self.update_status.configure(text='Consultando GitHub…')

        def done(release):
            self.update_button.state(['!disabled'])
            self.show_release(release)

        def failed(kind, message, detail):
            self.update_button.state(['!disabled'])
            self.update_status.configure(text=message)
            if kind == 'program':
                self.app.show_error(kind, message, detail)
        tasks.run(self, lambda p, c: updates.latest_release(), on_done=done, on_error=failed)

    def show_release(self, release):
        from app import updates
        for child in self.update_body.winfo_children():
            child.destroy()
        if not updates.is_newer(release.version):
            self.update_status.configure(text=f'Tienes la versión más reciente ({release.version}).')
            return
        self.update_status.configure(text=f'Hay una versión nueva: {release.version}.')
        ttk.Label(self.update_body, text=f'Novedades de {release.name}', style='Card.Strong.TLabel').pack(
            anchor='w', pady=(12, 4))
        notes = tk.Text(self.update_body, height=8, wrap='word', padx=10, pady=8, relief='flat', highlightthickness=1)
        notes.insert('1.0', release.notes or 'La versión no trae notas.')
        notes.configure(state='disabled')
        notes.pack(fill='x')
        row = ttk.Frame(self.update_body, style='Flat.Card.TFrame')
        row.pack(fill='x', pady=(10, 0))
        if updates.installer_name(release.version) is None:
            link = ttk.Button(row, text='Abrir la página de versiones', command=lambda: self.open_page(release.page))
            self.app.icons.bind(link, 'external-link')
            link.pack(side='left')
        else:
            install = ttk.Button(row, text='Bajar e instalar', style='Primary.TButton',
                                 command=lambda: self.install_release(release))
            self.app.icons.bind(install, 'download')
            install.pack(side='left')
            page = ttk.Button(row, text='Ver en GitHub', style='Card.Link.TButton',
                              command=lambda: self.open_page(release.page))
            self.app.icons.bind(page, 'external-link', 16)
            page.pack(side='left', padx=8)
        from app.components import ProgressPanel
        self.update_progress = ProgressPanel(self.update_body, self.app, self.cancel_update)
        self.app.theme.walk(self.update_body)

    def open_page(self, url):
        import webbrowser
        webbrowser.open(url, new=2)

    def cancel_update(self):
        if self.update_worker:
            self.update_worker.cancel.set()
            self.update_progress.cancelling()

    def install_release(self, release):
        from app import tasks, updates
        from app.main_window import open_in_system
        folder = self.app.settings.downloads_dir()/'Actualizaciones'
        self.update_progress.pack(fill='x', pady=(12, 0))
        self.update_progress.start('Comprobando la firma de la versión…')

        def done(path):
            self.update_worker = None
            self.update_progress.stop('Firma y SHA-256 verificados. Se abre el instalador; cierra el programa para '
                                      'que termine.')
            open_in_system(path)

        def failed(kind, message, detail):
            self.update_worker = None
            self.update_progress.stop(message)
            self.app.show_error(kind, message, detail, 'No se instaló la actualización')

        def cancelled(message):
            self.update_worker = None
            self.update_progress.stop('Cancelado. No se instaló nada.')
        self.update_worker = tasks.run(self, lambda p, c: updates.fetch_verified_installer(release, folder, p, c),
                                       on_progress=self.update_progress.update_progress, on_done=done,
                                       on_error=failed, on_cancel=cancelled)

    def touch(self):
        self.saved.configure(text='Cambios sin guardar.')

    def show_logo(self):
        logo = self.app.settings.values.logo
        if logo and Path(logo).is_file():
            with Image.open(logo) as image:
                image = image.convert('RGBA')
                image.thumbnail((140, 56), Image.Resampling.LANCZOS)
                self.logo_photo = ImageTk.PhotoImage(image, master=self)
            self.logo_label.configure(image=self.logo_photo, text='')
            self.clear_logo_button.state(['!disabled'])
        else:
            self.logo_label.configure(image='', text='Sin logo')
            self.clear_logo_button.state(['disabled'])

    def choose_logo(self):
        path = filedialog.askopenfilename(parent=self.app, title='Logo para los informes',
                                          filetypes=[('Imagen PNG o JPEG', '*.png *.jpg *.jpeg')])
        if path:
            try:
                self.app.settings.set_logo(Path(path))
            except (SettingsError, OSError) as exc:
                messagebox.showerror('Logo', str(exc), parent=self.app)
                return
            self.show_logo()
            self.app.set_status('Logo guardado para los informes.')

    def clear_logo(self):
        self.app.settings.clear_logo()
        self.show_logo()

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self.app, title='Carpeta de datos', mustexist=False,
                                       initialdir=str(self.app.settings.data_dir()))
        if path:
            self.new_folder = Path(path)
            self.folder.configure(text=str(self.new_folder))
            self.touch()

    def open_folder(self):
        from app.main_window import open_in_system
        folder = self.new_folder or self.app.settings.data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        open_in_system(folder)

    def open_log(self):
        from app.main_window import open_in_system
        self.app.settings.folder.mkdir(parents=True, exist_ok=True)
        open_in_system(self.app.settings.folder)

    def preview_theme(self):
        self.app.theme.apply(palette(self.theme.get()))
        self.touch()

    def save(self):
        units = next(k for k, u in UNITS.items() if u[0] == self.units.get())
        changes = {key: var.get().strip() for key, var in self.vars.items()}
        changes.update(units=units, decimals=int(self.decimals.get()), theme=self.theme.get(),
                       reduce_motion=self.reduce.get(), check_updates=self.check_updates.get())
        if self.new_folder is not None:
            try:
                self.new_folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror('Carpeta de datos', f'No se pudo usar esa carpeta: {exc}', parent=self.app)
                return
            changes['data_dir'] = str(self.new_folder)
        try:
            self.app.settings.save(**changes)
        except SettingsError as exc:
            messagebox.showerror('Ajustes', str(exc), parent=self.app)
            return
        self.app.theme.reduce_motion = self.reduce.get()
        if self.new_folder is not None:
            self.app.guard(apply_data_dir, self.app)
            self.new_folder = None
        self.saved.configure(text='Ajustes guardados.')
        self.app.set_status('Ajustes guardados.')

    def on_show(self):
        pass


def licenses_text() -> str:
    parts = []
    for path in (ROOT/'third_party'/'LICENCIAS.md', ROOT/'LICENSE', ROOT/'assets'/'icons'/'LICENSE.txt'):
        if path.is_file():
            parts.append(f'— {path.name} —\n\n'+path.read_text(encoding='utf-8'))
    return '\n\n'.join(parts) or 'Los avisos de licencias no se encontraron en esta instalación.'


class AboutPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        scroll = ScrollFrame(self)
        scroll.pack(fill='both', expand=True)
        body = scroll.body
        head = ttk.Frame(body)
        head.pack(fill='x')
        with Image.open(ROOT/'assets'/'marca_icono.png') as image:
            self.logo = ImageTk.PhotoImage(image.convert('RGBA').resize((96, 96), Image.Resampling.LANCZOS),
                                           master=self)
        ttk.Label(head, image=self.logo).pack(side='left', padx=(0, 18))
        words = ttk.Frame(head)
        words.pack(side='left')
        ttk.Label(words, text=APP_NAME, style='Title.TLabel').pack(anchor='w')
        ttk.Label(words, text=f'Versión {__version__} · Python {platform.python_version()} · '
                              f'{platform.system()} {platform.machine()}', style='Subtitle.TLabel').pack(anchor='w')
        frame, inner = card(body, 'Qué es')
        frame.pack(fill='x', pady=(20, 0))
        wrap_label(inner, 'Posproceso de oficina para los receptores MeridianV y MeridianVI y la app TresVizo Field, '
                          'ligado a las redes de estaciones de referencia (RGNA del INEGI y otras): georreferenciar un punto '
                          'estático contra una a tres estaciones con ajuste por mínimos cuadrados, y PPK de los puntos '
                          'de una jornada. Proceso con RTKLIB-EX (sucesor de demo5). Licencia MIT, código abierto en '
                          'github.com/cadaritre/TresVizo-Field-PPK.', style='Card.TLabel', margin=40).pack(fill='x')
        frame, inner = card(body, 'Privacidad')
        frame.pack(fill='x', pady=(16, 0))
        wrap_label(inner, 'Sin telemetría ni conexiones automáticas. La red solo se usa cuando pides una descarga '
                          '(redes, efemérides precisas, sistemas y geoides, actualizaciones) y siempre se dice qué se baja y de dónde.',
                   style='Card.TLabel', margin=40).pack(fill='x')
        frame, inner = card(body, 'Componentes de terceros', 'RTKLIB-EX (BSD 2), RNXCMP (GSI), PROJ/pyproj (MIT), '
                                                             'numpy, matplotlib, ReportLab, Pillow, pypdfium2/PDFium, '
                                                             'tkinterdnd2/tkdnd, iconos Lucide (ISC), geoides y datos '
                                                             'y las redes de estaciones (RGNA del INEGI…), registro EPSG.')
        frame.pack(fill='x', pady=(16, 0))
        button = ttk.Button(inner, text='Ver licencias y avisos', command=self.show_licenses)
        app.icons.bind(button, 'file-text')
        button.pack(anchor='w')

    def show_licenses(self):
        window = tk.Toplevel(self.app)
        window.title('Licencias y avisos')
        window.geometry('820x640')
        window.transient(self.app)
        text = tk.Text(window, wrap='word', padx=16, pady=12, font=self.app.theme.fonts.mono())
        frame = ttk.Frame(window)
        frame.pack(fill='both', expand=True)
        text.destroy()
        text = tk.Text(frame, wrap='word', padx=16, pady=12, font=self.app.theme.fonts.mono())
        scrolled(text, frame)
        text.insert('1.0', licenses_text())
        text.configure(state='disabled')
        self.app.theme.walk(window)
        self.app.set_window_icon(window)
        window.bind('<Escape>', lambda e: window.destroy())

    def on_show(self):
        pass
