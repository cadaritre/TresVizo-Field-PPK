"""Ventana principal: barra lateral con iconos (estilo TresVizo Med), páginas que conservan su estado y barra de estado."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk
import traceback

from PIL import Image, ImageTk
from tkinterdnd2 import TkinterDnD

from tresvizo_ppk.version import __version__

from app import tasks
from app.backend import Backend
from app.components import Tooltip
from app.icons import Icons
from app.paths import APP_NAME, ASSETS
from app.settings import Settings
from app.themes import luminance, palette
from app.ui_theme import ThemeManager

APP_USER_MODEL_ID = 'TresVizo.FieldPPK.Desktop'
NAV = [('Inicio', 'house'), ('Georreferenciar punto', 'map-pin'), ('PPK de puntos', 'route'),
       ('Sistemas y geoides', 'globe'), ('Estaciones RGNA', 'radio-tower')]
NAV_BOTTOM = [('Ajustes', 'settings'), ('Acerca de', 'info')]


def register_windows_identity() -> bool:
    """Separar la app del anfitrión Python en la barra de tareas de Windows (antes de crear ventanas)."""
    if os.name != 'nt':
        return False
    import ctypes
    try:
        register = ctypes.WinDLL('shell32').SetCurrentProcessExplicitAppUserModelID
        register.argtypes = [ctypes.c_wchar_p]
        register.restype = ctypes.c_long
        return register(APP_USER_MODEL_ID) >= 0
    except (AttributeError, OSError):
        return False


def open_in_system(path: Path) -> None:
    """Abre una carpeta o archivo con la aplicación del sistema (solo a petición del usuario)."""
    path = Path(path)
    if sys.platform == 'win32':
        os.startfile(str(path))  # noqa: S606 - ruta local elegida por el usuario
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path)])


class Application(TkinterDnD.Tk):
    def __init__(self, settings_dir=None, backend=None, open_path=None):
        self.windows_identity_registered = register_windows_identity()
        super().__init__(className='TresVizoFieldPPK')
        self.withdraw()
        self.settings = Settings(settings_dir)
        self.backend = backend if backend is not None else Backend.load()
        from app.settings_ui import apply_data_dir
        try:
            apply_data_dir(self)
        except Exception as exc:  # noqa: BLE001 - se sigue con la carpeta por omisión y se registra
            tasks.log_error(self.settings.folder, 'Carpeta de datos', repr(exc))
        self.mod = 'Command' if self.tk.call('tk', 'windowingsystem') == 'aqua' else 'Control'
        self.mod_text = '⌘' if self.mod == 'Command' else 'Ctrl+'
        scale = max(1.0, float(self.tk.call('tk', 'scaling'))/(96/72)) if sys.platform == 'win32' else 1.0
        self.ui_scale = scale
        self.theme = ThemeManager(self)
        self.theme.reduce_motion = self.settings.values.reduce_motion
        self.icons = Icons(self.theme)
        self.title(APP_NAME)
        width = min(int(1240*scale), self.winfo_screenwidth()-40)
        height = min(int(820*scale), self.winfo_screenheight()-80)
        self.geometry(f'{width}x{height}')
        self.minsize(min(900, width), min(600, height))
        self.set_window_icon(self)
        self.theme.apply(palette(self.settings.values.theme))
        self.status = tk.StringVar(value='Listo. Nada sale de este equipo salvo las descargas que pidas.')
        self.pages: dict[str, ttk.Frame] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.current_page = ''
        self.catalog_cache = None
        self.shell()
        self.bind_shortcuts()
        if sys.platform == 'darwin':
            self.mac_menu()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.show('Inicio')
        self.deiconify()
        if self.settings.recovered_from:
            self.after(300, lambda: messagebox.showwarning(
                'Preferencias restablecidas', 'Las preferencias estaban dañadas y se restablecieron. El archivo '
                f'anterior se conservó en:\n{self.settings.recovered_from}', parent=self))
        if open_path:
            self.after(200, lambda: self.open_project(Path(open_path)))

    # --- Marca y ventana ------------------------------------------------------------------------------------------

    def set_window_icon(self, window):
        with Image.open(ASSETS/'marca_icono.png') as image:
            window.icon_photo = ImageTk.PhotoImage(image.convert('RGBA').resize((128, 128), Image.Resampling.LANCZOS),
                                                   master=window)
        window.iconphoto(window is self, window.icon_photo)
        if sys.platform == 'win32':
            window.iconbitmap(default=str(ASSETS/'marca.ico')) if window is self else \
                window.iconbitmap(str(ASSETS/'marca.ico'))

    def brand_photo(self, size):
        dark = bool(self.theme.tokens) and luminance(self.theme.tokens['sidebar']) < .25
        with Image.open(ASSETS/('marca_oscuro.png' if dark else 'marca.png')) as image:
            image = image.convert('RGBA').resize((size, size), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image, master=self)

    def shell(self):
        self.status_bar = ttk.Label(self, textvariable=self.status, style='Subtitle.TLabel', padding=(20, 6))
        self.status_bar.pack(side='bottom', fill='x')
        ttk.Separator(self, orient='horizontal').pack(side='bottom', fill='x')
        nav = self.nav = ttk.Frame(self, style='Nav.TFrame', padding=(10, 14), width=int(258*self.ui_scale))
        nav.pack(side='left', fill='y')
        nav.pack_propagate(False)
        brand = ttk.Frame(nav, style='Nav.TFrame')
        brand.pack(fill='x', pady=(4, 22), padx=4)
        logo = ttk.Label(brand, style='Nav.TLabel')
        logo.pack(side='left')

        def refresh_logo(tokens):
            logo.photo = self.brand_photo(int(40*self.ui_scale))
            logo.configure(image=logo.photo)
        self.theme.subscribe(logo, refresh_logo)
        words = ttk.Frame(brand, style='Nav.TFrame')
        words.pack(side='left', padx=(10, 0))
        ttk.Label(words, text='TresVizo', style='Brand.TLabel').pack(anchor='w')
        ttk.Label(words, text='Field PPK', style='BrandSub.TLabel').pack(anchor='w')
        for index, (name, icon) in enumerate(NAV, 1):
            self.nav_button(nav, name, icon, f'{name} ({self.mod_text}{index})').pack(fill='x', pady=2)
        bottom = ttk.Frame(nav, style='Nav.TFrame')
        bottom.pack(side='bottom', fill='x')
        for name, icon in NAV_BOTTOM:
            self.nav_button(bottom, name, icon, name).pack(fill='x', pady=2)
        self.content = ttk.Frame(self, padding=(28, 20, 28, 12))
        self.content.pack(side='left', fill='both', expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

    def nav_button(self, parent, name, icon, tip):
        button = ttk.Button(parent, text=name, style='Nav.TButton', command=lambda: self.show(name))
        self.icons.bind(button, icon, 18)
        Tooltip(button, tip, self.theme)
        self.nav_buttons[name] = button
        return button

    def bind_shortcuts(self):
        m = self.mod
        for index, (name, _) in enumerate(NAV, 1):
            self.bind_all(f'<{m}-Key-{index}>', lambda e, n=name: self.show(n))
        self.bind_all(f'<{m}-o>', lambda e: self.ask_open_project())
        self.bind_all(f'<{m}-s>', lambda e: self.save_current())
        self.bind_all(f'<{m}-S>', lambda e: self.save_current(as_new=True))
        self.bind_all(f'<{m}-comma>', lambda e: self.show('Ajustes'))
        if hasattr(self, 'drop_target_register'):
            self.drop_target_register('DND_Files')
            self.dnd_bind('<<Drop>>', self.on_window_drop)

    def mac_menu(self):
        menubar = tk.Menu(self)
        app_menu = tk.Menu(menubar, name='apple', tearoff=False)
        app_menu.add_command(label=f'Acerca de {APP_NAME}', command=lambda: self.show('Acerca de'))
        menubar.add_cascade(menu=app_menu)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label='Georreferenciar punto nuevo', command=lambda: self.new_job('Georreferenciar punto'))
        file_menu.add_command(label='PPK de puntos nuevo', command=lambda: self.new_job('PPK de puntos'))
        file_menu.add_separator()
        file_menu.add_command(label='Abrir proyecto…', accelerator='⌘O', command=self.ask_open_project)
        file_menu.add_command(label='Guardar proyecto', accelerator='⌘S', command=self.save_current)
        file_menu.add_command(label='Guardar proyecto como…', accelerator='⇧⌘S',
                              command=lambda: self.save_current(as_new=True))
        menubar.add_cascade(label='Archivo', menu=file_menu)
        window_menu = tk.Menu(menubar, name='window', tearoff=False)
        menubar.add_cascade(label='Ventana', menu=window_menu)
        self.configure(menu=menubar)
        self.createcommand('tk::mac::ShowPreferences', lambda: self.show('Ajustes'))
        self.createcommand('tk::mac::Quit', self.close)
        self.createcommand('::tk::mac::OpenDocument', lambda *paths: paths and self.open_project(Path(paths[0])))

    # --- Páginas --------------------------------------------------------------------------------------------------

    def builders(self):
        from app.home import HomePage
        from app.static_ui import StaticPage
        from app.points_ui import PointsPage
        from app.crs_ui import CrsPage
        from app.stations_ui import StationsPage
        from app.settings_ui import SettingsPage, AboutPage
        return {'Inicio': HomePage, 'Georreferenciar punto': StaticPage, 'PPK de puntos': PointsPage,
                'Sistemas y geoides': CrsPage, 'Estaciones RGNA': StationsPage, 'Ajustes': SettingsPage,
                'Acerca de': AboutPage}

    def page(self, name):
        if name not in self.pages:
            frame = self.builders()[name](self.content, self)
            frame.grid(row=0, column=0, sticky='nsew')
            self.pages[name] = frame
            self.theme.walk(frame)
        return self.pages[name]

    def show(self, name):
        previous = self.pages.get(self.current_page)
        page = self.page(name)
        focus = self.focus_get()
        if previous is not None and previous is not page and focus and str(focus).startswith(str(previous)+'.'):
            previous.return_focus = focus
        page.tkraise()
        self.current_page = name
        for key, button in self.nav_buttons.items():
            button.configure(style='Active.Nav.TButton' if key == name else 'Nav.TButton')
            button.refresh_icon()
        if hasattr(page, 'on_show'):
            self.guard(page.on_show)
        target = getattr(page, 'return_focus', None)
        if target is not None and target.winfo_exists() and target.winfo_viewable():
            target.focus_set()
        return page

    def new_job(self, name):
        page = self.page(name)
        if page.confirm_discard():
            page.reset()
        self.show(name)

    # --- Proyectos ------------------------------------------------------------------------------------------------

    def ask_open_project(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self, title='Abrir proyecto',
                                          initialdir=self.settings.values.last_folder or self.settings.projects_dir(),
                                          filetypes=[('Proyecto de TresVizo Field PPK', '*.tvppk')])
        if path:
            self.open_project(Path(path))

    def open_project(self, path: Path):
        from tresvizo_ppk import project as projects
        try:
            project = projects.load(path)
        except projects.ProjectError as exc:
            messagebox.showerror('No se pudo abrir el proyecto', str(exc), parent=self)
            if not Path(path).exists():
                self.settings.remove_recent(path)
                if 'Inicio' in self.pages:
                    self.pages['Inicio'].on_show()
            return
        name = 'Georreferenciar punto' if project.kind == 'static' else 'PPK de puntos'
        page = self.page(name)
        if not page.confirm_discard():
            return
        self.show(name)
        page.load_project(project, Path(path))
        self.remember(path)

    def remember(self, path):
        try:
            self.settings.add_recent(Path(path))
        except Exception as exc:  # noqa: BLE001 - la lista de recientes no debe impedir trabajar
            tasks.log_error(self.settings.folder, 'Recientes', repr(exc))

    def save_current(self, as_new=False):
        page = self.pages.get(self.current_page)
        if page is not None and hasattr(page, 'save'):
            page.save(as_new=as_new)
        return 'break'

    def on_window_drop(self, event):
        paths = [Path(p) for p in self.tk.splitlist(event.data)]
        projects = [p for p in paths if p.suffix.lower() == '.tvppk']
        if projects:
            self.open_project(projects[0])
        return event.action

    # --- Mensajes, errores y cierre ----------------------------------------------------------------------------------

    def set_status(self, text):
        self.status.set(text)

    def guard(self, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            kind, message = tasks.classify(exc)
            self.show_error(kind, message, traceback.format_exc())
            return None

    def show_error(self, kind, message, detail='', title=None):
        if kind == 'program':
            tasks.log_error(self.settings.folder, title or 'Error', detail)
            messagebox.showerror(title or 'Error del programa', message+'\n\nRegistro: '
                                 + str(self.settings.folder/'registro-de-errores.txt'), parent=self)
        elif kind == 'cancelled':
            self.set_status(message)
        else:
            messagebox.showerror(title or 'No se pudo completar', message, parent=self)

    def report_callback_exception(self, exc, value, tb):
        detail = ''.join(traceback.format_exception(exc, value, tb))
        kind, message = tasks.classify(value)
        self.show_error(kind, message, detail)

    def running_pages(self):
        return [p for p in self.pages.values() if getattr(p, 'busy', lambda: False)()]

    def close(self):
        running = self.running_pages()
        if running and not messagebox.askyesno('Hay un proceso en curso', '¿Cancelar el proceso y salir?',
                                               icon='warning', parent=self):
            return
        for page in self.pages.values():
            if hasattr(page, 'confirm_discard') and not page.confirm_discard(closing=True):
                return
        for page in running:
            page.cancel()
        self.destroy()
