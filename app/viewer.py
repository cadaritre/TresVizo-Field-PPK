"""Vista previa de un PDF (pypdfium2) con páginas, zoom y «Guardar PDF…», como en TresVizo Med."""
from __future__ import annotations

from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import ImageTk

from app import tasks


def render_page(path: Path, page: int, width_px: int):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(str(path)) as document:
        count = len(document)
        pdf_page = document[min(max(0, page), count-1)]
        try:
            scale = max(.2, min(4.0, width_px/pdf_page.get_width()))
            bitmap = pdf_page.render(scale=scale)
            try:
                image = bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            pdf_page.close()
    return image, count


class DocumentViewer(tk.Toplevel):
    def __init__(self, app, path, title='Informe', suggested_name='informe.pdf'):
        super().__init__(app)
        self.app, self.path, self.page, self.zoom, self.pages = app, Path(path), 0, 1.0, 1
        self.suggested_name = suggested_name
        self.request = 0
        self.title(title)
        width = min(int(940*app.ui_scale), self.winfo_screenwidth()-60)
        height = min(int(820*app.ui_scale), self.winfo_screenheight()-90)
        self.geometry(f'{width}x{height}')
        self.minsize(560, 420)
        self.transient(app)
        bar = ttk.Frame(self, padding=(12, 10))
        bar.pack(fill='x')
        save = ttk.Button(bar, text='Guardar PDF…', style='Primary.TButton', command=self.save)
        app.icons.bind(save, 'download')
        save.pack(side='left')
        for text, icon, command, tip in [('', 'chevron-left', lambda: self.turn(-1), 'Página anterior (Re Pág)'),
                                         ('', 'chevron-right', lambda: self.turn(1), 'Página siguiente (Av Pág)')]:
            button = ttk.Button(bar, text=tip, command=command)
            app.icons.bind(button, icon, show_text=False)
            button.pack(side='left', padx=(8, 0))
        for text, command in [('−', lambda: self.scale(.8)), ('+', lambda: self.scale(1.25)), ('Ajustar', self.fit)]:
            ttk.Button(bar, text=text, command=command, width=7 if text == 'Ajustar' else 3).pack(side='left',
                                                                                                padx=(8, 0))
        self.info = ttk.Label(bar, style='Subtitle.TLabel')
        self.info.pack(side='right')
        frame = ttk.Frame(self)
        frame.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(frame, highlightthickness=0, borderwidth=0)
        self.canvas.scroll_surface = True
        vs = ttk.Scrollbar(frame, command=self.canvas.yview)
        hs = ttk.Scrollbar(frame, orient='horizontal', command=self.canvas.xview)
        vs.pack(side='right', fill='y')
        hs.pack(side='bottom', fill='x')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.canvas.bind('<ButtonPress-1>', lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind('<B1-Motion>', lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind('<MouseWheel>', lambda e: self.canvas.yview_scroll(
            -e.delta if self.tk.call('tk', 'windowingsystem') == 'aqua' else -int(e.delta/120), 'units'))
        for key, delta in (('<Prior>', -1), ('<Next>', 1), ('<Left>', -1), ('<Right>', 1)):
            self.bind(key, lambda e, d=delta: self.turn(d))
        self.bind('<Escape>', lambda e: self.destroy())
        self.bind('<Control-s>', lambda e: self.save())
        self.bind('<Command-s>', lambda e: self.save())
        app.theme.walk(self)
        self.after(80, self.fit)
        self.focus_set()

    def turn(self, delta):
        self.page = max(0, min(self.pages-1, self.page+delta))
        self.render()

    def scale(self, factor):
        self.zoom = max(.3, min(3.0, self.zoom*factor))
        self.render()

    def fit(self):
        self.zoom = 1.0
        self.render()

    def render(self):
        self.request += 1
        request, page = self.request, self.page
        width = int(max(300, self.canvas.winfo_width()-24)*self.zoom)
        self.info.configure(text='Cargando página…')

        def done(result):
            if not self.winfo_exists() or request != self.request:
                return
            image, self.pages = result
            self.photo = ImageTk.PhotoImage(image, master=self)
            self.canvas.delete('all')
            x = max(12, (self.canvas.winfo_width()-image.width)//2)
            self.canvas.create_image(x, 12, image=self.photo, anchor='nw')
            self.canvas.configure(scrollregion=(0, 0, image.width+24, image.height+24))
            self.info.configure(text=f'Página {self.page+1} de {self.pages}')

        def failed(kind, message, detail):
            if self.winfo_exists():
                self.info.configure(text='No se pudo mostrar la página.')
                tasks.log_error(self.app.settings.folder, 'Vista previa del PDF', detail)
        tasks.run(self, lambda progress, cancel: render_page(self.path, page, width), on_done=done, on_error=failed)

    def save(self):
        target = filedialog.asksaveasfilename(parent=self, defaultextension='.pdf', initialfile=self.suggested_name,
                                              initialdir=self.app.settings.values.last_folder or None,
                                              filetypes=[('Documento PDF', '*.pdf')])
        if target:
            shutil.copyfile(self.path, target)
            self.app.set_status(f'Informe guardado en {target}.')
