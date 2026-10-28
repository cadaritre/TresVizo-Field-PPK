"""Inicio: empezar un trabajo nuevo, abrir un proyecto o retomar uno reciente, y lo que se hace con un proyecto
entero: ver cuánto ocupa, limpiar sus intermedios, buscar sus archivos si se movieron, empaquetarlo en un ZIP para
archivarlo o entregarlo y abrir un paquete."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app import tasks
from app.components import (Banner, CellTips, FlowFrame, ProgressPanel, ScrollFrame, card, fit_columns, scrolled,
                            size_text, wrap_label)

KINDS = {'static': 'Georreferenciar punto', 'points': 'PPK de puntos', 'network': 'Red estática'}


def project_summary(path: Path) -> tuple[str, str, str]:
    """(tipo, carpeta, modificado) leyendo solo lo necesario; «no está» si ya no existe."""
    try:
        stat = path.stat()
        kind = '—'
        if stat.st_size < 5_000_000:
            data = json.loads(path.read_text(encoding='utf-8'))
            kind = KINDS.get(data.get('kind'), '—')
        return kind, str(path.parent), datetime.fromtimestamp(stat.st_mtime).strftime('%d/%m/%Y %H:%M')
    except FileNotFoundError:
        return 'No está en su carpeta', str(path.parent), '—'
    except (OSError, ValueError):
        return 'No se pudo leer', str(path.parent), '—'


class HomePage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        scroll = ScrollFrame(self)
        scroll.pack(fill='both', expand=True)
        body = scroll.body
        ttk.Label(body, text='Inicio', style='Title.TLabel').pack(anchor='w')
        wrap_label(body, 'Posproceso de los receptores MeridianV y MeridianVI ligado a las redes de estaciones de '
                         'referencia (RGNA del INEGI y otras). Elige qué quieres hacer o retoma un proyecto.').pack(anchor='w', fill='x',
                                                                                                 pady=(4, 18))
        self.notices = ttk.Frame(body)
        self.notices.pack(fill='x')
        tiles = ttk.Frame(body)
        tiles.pack(fill='x')
        tiles.columnconfigure((0, 1, 2), weight=1, uniform='tiles')
        for column, (name, icon, text) in enumerate([
                ('Georreferenciar punto', 'map-pin',
                 'Un punto estático contra una a tres estaciones de referencia, con ajuste por mínimos cuadrados e informe '
                 'de línea base.'),
                ('PPK de puntos', 'route',
                 'La jornada cinemática del receptor y el CSV de TresVizo Field, con base de una red o propia.'),
                ('Red estática', 'chart-scatter',
                 'Varios puntos de control con uno o más receptores: líneas a las estaciones y entre receptores, '
                 'ajuste de red, cierres de bucle e informe de ajuste.')]):
            frame = ttk.Frame(tiles, style='Card.TFrame', padding=18)
            frame.grid(row=0, column=column, sticky='nsew', padx=(0 if column == 0 else 8, 0 if column == 2 else 8))
            app.icons.label(frame, icon, 30, token='primary', background='surface', style='Card.TLabel').pack(
                anchor='w')
            ttk.Label(frame, text=name, style='Card.Section.TLabel').pack(anchor='w', pady=(10, 4))
            wrap_label(frame, text, style='Card.Subtitle.TLabel', margin=40).pack(anchor='w', fill='x')
            button = ttk.Button(frame, text='Empezar', style='Primary.TButton',
                                command=lambda n=name: app.new_job(n))
            app.icons.bind(button, 'arrow-right')
            button.pack(anchor='w', pady=(14, 0))
        recents, inner = card(body, 'Proyectos recientes',
                              'Doble clic o Intro para abrir. También puedes arrastrar un archivo .tvppk a la ventana.')
        recents.pack(fill='both', expand=True, pady=(24, 0))
        holder = ttk.Frame(inner, style='Flat.Card.TFrame')
        holder.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(holder, columns=('name', 'kind', 'folder', 'date', 'size'), show='headings',
                                 height=8, selectmode='browse')
        for key, title, width, stretch in [('name', 'Proyecto', 220, False), ('kind', 'Tipo', 170, False),
                                           ('folder', 'Carpeta', 320, True), ('date', 'Modificado', 140, False),
                                           ('size', 'Tamaño', 110, False)]:
            self.tree.heading(key, text=title, anchor='w')
            self.tree.column(key, width=width, minwidth=80, stretch=stretch, anchor='w')
        scrolled(self.tree, holder)
        CellTips(self.tree, app)
        self.tree.bind('<Double-1>', lambda e: self.open_selected())
        self.tree.bind('<Return>', lambda e: self.open_selected())
        self.empty = ttk.Label(inner, text='Todavía no hay proyectos. Empieza uno de los dos trabajos de arriba.',
                               style='Card.Subtitle.TLabel', padding=(0, 8))
        # las acciones se envuelven en filas si la ventana es angosta o la letra muy grande
        actions = FlowFrame(inner, style='Flat.Card.TFrame')
        actions.pack(fill='x', pady=(12, 0))
        open_button = ttk.Button(actions, text='Abrir proyecto…', command=app.ask_open_project)
        app.icons.bind(open_button, 'folder-open')
        actions.add(open_button)
        self.open_selected_button = ttk.Button(actions, text='Abrir seleccionado', command=self.open_selected)
        app.icons.bind(self.open_selected_button, 'arrow-right')
        actions.add(self.open_selected_button)
        self.more = ttk.Menubutton(actions, text='Proyecto')
        app.icons.bind(self.more, 'hard-drive')
        menu = tk.Menu(self.more, tearoff=False)
        menu.add_command(label='Limpiar intermedios…', command=self.clean_selected)
        menu.add_command(label='Buscar los archivos en…', command=self.relink_selected)
        menu.add_command(label='Empaquetar proyecto…', command=self.pack_selected)
        self.more.configure(menu=menu)
        self.more_menu = menu
        actions.add(self.more)
        self.forget_button = ttk.Button(actions, text='Quitar de la lista', style='Card.Link.TButton',
                                        command=self.forget)
        app.icons.bind(self.forget_button, 'x', 16)
        actions.add(self.forget_button)
        self.package_button = ttk.Button(actions, text='Abrir paquete…', style='Card.Link.TButton',
                                         command=self.ask_open_package)
        app.icons.bind(self.package_button, 'file-up', 16)
        actions.add(self.package_button)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.update_buttons())
        # trabajo con un proyecto entero (limpiar, buscar, empaquetar, abrir un paquete): una cosa a la vez
        self.job_card, inner = card(body, 'Proyecto')
        self.job_progress = ProgressPanel(inner, app, self.cancel)
        self.job_progress.pack(fill='x')
        self.worker = None
        self.sizes_worker = None
        self.sizes: dict[str, str] = {}
        self.size_tips: dict[str, str] = {}

    def on_show(self):
        self.refresh()

    def refresh(self):
        for child in self.notices.winfo_children():
            child.destroy()
        for key, error in self.app.backend.errors.items():
            Banner(self.notices, self.app, 'error', self.app.backend.missing_message(key)).pack(fill='x', pady=(0, 12))
        self.tree.delete(*self.tree.get_children())
        for path in self.app.settings.values.recents:
            kind, folder, date = project_summary(Path(path))
            self.tree.insert('', 'end', iid=path, values=(Path(path).stem, kind, folder, date,
                                                          self.sizes.get(path, '…')))
        self.tree.configure(height=max(3, min(8, len(self.app.settings.values.recents))))
        fit_columns(self.tree, self.app, stretch='folder', maximum=360)
        if self.app.settings.values.recents:
            self.empty.pack_forget()
            first = self.tree.get_children()[0]
            if not self.tree.selection():
                self.tree.selection_set(first)
                self.tree.focus(first)
        else:
            self.empty.pack(anchor='w', before=self.tree.master)
        self.update_buttons()
        self.measure()

    def update_buttons(self):
        state = ['!disabled'] if self.tree.selection() else ['disabled']
        self.open_selected_button.state(state)
        self.forget_button.state(state)
        self.more.state(['!disabled'] if self.tree.selection() and not self.busy() else ['disabled'])
        self.package_button.state(['disabled'] if self.busy() else ['!disabled'])

    # --- Tamaño de cada proyecto (en otro hilo: una carpeta de trabajo puede tener cientos de archivos) ------------

    def measure(self):
        from tresvizo_ppk import project as projects
        if self.sizes_worker is not None:
            self.sizes_worker.cancel.set()
        paths = list(self.app.settings.values.recents)

        def work(progress, cancel):
            out = {}
            for path in paths:
                if cancel.is_set():
                    break
                p = Path(path)
                if not p.is_file():
                    out[path] = '—'
                    continue
                size = projects.project_size(p)
                out[path] = size_text(size.total)
                if size.intermediates:
                    out[path+'|tip'] = f'{size_text(size.intermediates)} de intermedios'
            return out

        def done(result):
            self.sizes_worker = None
            self.sizes.update({k: v for k, v in result.items() if not k.endswith('|tip')})
            self.size_tips = {k[:-4]: v for k, v in result.items() if k.endswith('|tip')}
            for path, text in self.sizes.items():
                if self.tree.exists(path):
                    tip = self.size_tips.get(path)
                    self.tree.set(path, 'size', f'{text} ({tip})' if tip else text)
            fit_columns(self.tree, self.app, stretch='folder', maximum=360)

        self.sizes_worker = tasks.run(self, work, on_done=done, on_error=lambda *a: setattr(self, 'sizes_worker', None),
                                      on_cancel=lambda *a: None)

    # --- Trabajo con un proyecto entero ---------------------------------------------------------------------------

    def busy(self):
        return self.worker is not None

    def cancel(self):
        if self.worker is not None:
            self.worker.cancel.set()
            self.job_progress.cancelling()

    def selected(self) -> Path | None:
        return Path(self.tree.selection()[0]) if self.tree.selection() else None

    def open_page(self, path: Path):
        """La página que tiene abierto ese proyecto, si alguna."""
        for name in ('Georreferenciar punto', 'PPK de puntos', 'Red estática'):
            page = self.app.pages.get(name)
            if page is not None and getattr(page, 'path', None) is not None:
                try:
                    if Path(page.path).resolve() == Path(path).resolve():
                        return page
                except OSError:
                    continue
        return None

    def ready(self, path: Path, action: str) -> bool:
        """Que no haya otro trabajo en Inicio ni un proceso de ese proyecto en marcha; los cambios sin guardar del
        proyecto abierto se guardan o se descartan antes (el trabajo lee el proyecto del disco)."""
        if self.busy():
            return False
        if not Path(path).is_file():
            messagebox.showerror(action, f'No se encuentra el proyecto «{Path(path).name}».', parent=self.app)
            return False
        page = self.open_page(path)
        if page is not None and page.busy():
            messagebox.showerror(action, f'«{Path(path).stem}» se está procesando. Espera a que termine o '
                                         'cancélalo.', parent=self.app)
            return False
        if page is not None and getattr(page, 'dirty', False) and not page.confirm_discard():
            return False
        return True

    def start(self, title, work, on_done):
        self.job_card.pack(fill='x', pady=(16, 0))
        self.job_card.winfo_children()[0].configure(text=title)
        self.job_progress.start('Preparando…')

        def finished(result):
            self.worker = None
            self.job_progress.stop('Listo.')
            self.job_card.pack_forget()
            self.update_buttons()
            on_done(result)

        def failed(kind, message, detail):
            self.worker = None
            self.job_progress.stop(message)
            self.job_card.pack_forget()
            self.update_buttons()
            self.app.show_error(kind, message, detail, title)

        def cancelled(message):
            self.worker = None
            self.job_progress.stop(message)
            self.job_card.pack_forget()
            self.update_buttons()
            self.app.set_status(f'{title}: cancelado.')

        self.worker = tasks.run(self, work, on_progress=self.job_progress.update_progress, on_done=finished,
                                on_error=failed, on_cancel=cancelled)
        self.update_buttons()

    def clean_selected(self):
        path = self.selected()
        if path is not None:
            self.clean(path)

    def clean(self, path: Path):
        from tresvizo_ppk import project as projects
        title = 'Limpiar intermedios'
        if not self.ready(path, title):
            return
        size = projects.project_size(path)
        if not size.intermediates:
            messagebox.showinfo(title, f'«{path.stem}» no tiene intermedios que limpiar.', parent=self.app)
            return
        page = self.open_page(path)
        extra = ('\n\nEl resultado que está en pantalla se queda, pero sus gráficas del informe y la exportación '
                 'de la trayectoria y del RINEX piden volver a procesar.') if page is not None and \
            getattr(page, 'output', None) else ''
        if not messagebox.askyesno(
                title, f'Se borran {size_text(size.intermediates)} de intermedios de «{path.stem}» (RINEX unidos, '
                       'configuración de RTKLIB, .pos, .stat y ANTEX del proceso). Se quedan el proyecto con su '
                       'resultado, los informes, la bitácora y las efemérides bajadas. Al volver a procesar se rehacen '
                       'y dan la misma coordenada.'+extra+'\n\n¿Limpiar?', parent=self.app):
            return

        def done(freed):
            self.sizes.pop(str(path), None)
            self.refresh()
            self.app.set_status(f'«{path.stem}»: {size_text(freed)} de intermedios borrados.')

        self.start(title, lambda p, c: projects.clean_intermediates(path, cancel=c), done)

    def relink_selected(self):
        path = self.selected()
        if path is not None:
            self.relink(path)

    def relink(self, path: Path, reopen: bool = False):
        """Busca en la carpeta que diga el usuario los archivos de entrada que faltan (por nombre y SHA-256) y guarda
        el proyecto con sus rutas nuevas. `reopen`: lo abre después (viene de abrirlo con archivos faltantes)."""
        from tresvizo_ppk import __version__
        from tresvizo_ppk import project as projects
        title = 'Buscar los archivos'
        if not self.ready(path, title):
            return
        try:
            project = projects.load(path)
        except projects.ProjectError as exc:
            messagebox.showerror(title, str(exc), parent=self.app)
            return
        missing = projects.missing_inputs(project)
        if not missing:
            messagebox.showinfo(title, f'A «{path.stem}» no le falta ningún archivo de entrada.', parent=self.app)
            if reopen:
                self.app.open_project(path, find_missing=False)
            return
        folder = filedialog.askdirectory(parent=self.app, mustexist=True,
                                         title=f'Carpeta donde buscar los {len(missing)} archivos que faltan',
                                         initialdir=str(path.parent))
        if not folder:
            if reopen:
                self.app.open_project(path, find_missing=False)
            return

        def work(progress, cancel):
            result = projects.relink(project, Path(folder), progress, cancel)
            if result.found:
                projects.save(project, path, __version__)
            return result

        def done(result):
            if result.missing:
                names = '\n'.join('· '+Path(m).name for m in result.missing[:12])
                more = f'\n… y {len(result.missing)-12} más' if len(result.missing) > 12 else ''
                messagebox.showwarning(title, f'Se encontraron {len(result.found)} de {len(missing)} archivos '
                                              f'(revisados {result.searched}). Siguen faltando:\n{names}{more}\n\n'
                                              'Solo se acepta un archivo con la misma huella SHA-256 que el '
                                              'guardado.', parent=self.app)
            else:
                self.app.set_status(f'«{path.stem}»: los {len(result.found)} archivos que faltaban se encontraron '
                                    'y el proyecto quedó guardado con sus rutas nuevas.')
            page = self.open_page(path)
            if reopen or (page is not None and result.found):
                self.app.open_project(path, find_missing=False)

        self.start(title, work, done)

    def pack_selected(self):
        path = self.selected()
        if path is not None:
            self.pack(path)

    def pack(self, path: Path):
        from tresvizo_ppk import __version__
        from tresvizo_ppk import project as projects
        title = 'Empaquetar proyecto'
        if not self.ready(path, title):
            return
        target = filedialog.asksaveasfilename(parent=self.app, title='Guardar el paquete del proyecto',
                                              initialdir=str(path.parent), initialfile=path.stem+'.zip',
                                              defaultextension='.zip', filetypes=[('Paquete de proyecto', '*.zip')])
        if not target:
            return

        def done(out):
            self.app.set_status(f'Paquete listo: {out.name} ({size_text(out.stat().st_size)}).', folder=out.parent)

        self.start(title, lambda p, c: projects.pack(path, Path(target), __version__, p, c), done)

    def ask_open_package(self):
        from tresvizo_ppk import project as projects
        if self.busy():
            return
        title = 'Abrir paquete de proyecto'
        package = filedialog.askopenfilename(parent=self.app, title=title,
                                             initialdir=self.app.settings.values.last_folder or None,
                                             filetypes=[('Paquete de proyecto', '*.zip')])
        if not package:
            return
        folder = filedialog.askdirectory(parent=self.app, title='Carpeta donde dejar el proyecto',
                                         initialdir=str(self.app.settings.projects_dir()))
        if not folder:
            return

        def done(path):
            self.app.set_status(f'Paquete abierto en {path.parent}.', folder=path.parent)
            self.app.open_project(path)

        self.start(title, lambda p, c: projects.unpack(Path(package), Path(folder), p, c), done)

    def open_selected(self):
        if self.tree.selection():
            self.app.open_project(Path(self.tree.selection()[0]))

    def forget(self):
        if self.tree.selection():
            self.app.settings.remove_recent(self.tree.selection()[0])
            self.refresh()
