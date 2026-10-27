"""Trabajo pesado en un hilo aparte con progreso y cancelar (§8: `progress(fraccion, mensaje)` y `threading.Event`).

La parte sin Tk (`Worker`, `Stages`, `classify`) se prueba sin ventanas; `run` la engancha al bucle de Tk.
"""
from __future__ import annotations

from datetime import datetime
import queue
import threading
import traceback

try:
    from tresvizo_ppk.errors import Cancelled, UserError
except ImportError:  # pragma: no cover - el módulo existe desde la especificación §8
    class UserError(Exception):
        pass

    class Cancelled(UserError):
        pass


def classify(exc: BaseException) -> tuple[str, str]:
    """('cancelled' | 'user' | 'program', mensaje para el usuario)."""
    if isinstance(exc, Cancelled):
        return 'cancelled', str(exc) or 'Cancelado.'
    if isinstance(exc, UserError):
        return 'user', str(exc)
    from tresvizo_ppk.project import ProjectError
    if isinstance(exc, ProjectError):
        return 'user', str(exc)
    if isinstance(exc, PermissionError):
        return 'user', f'No hay permiso para usar «{getattr(exc, "filename", "") or "el archivo"}». ' \
                       'Elige otra carpeta o revisa sus permisos.'
    if isinstance(exc, FileNotFoundError):
        return 'user', f'No se encuentra «{getattr(exc, "filename", "") or "el archivo"}».'
    if isinstance(exc, OSError) and getattr(exc, 'errno', None) == 28:
        return 'user', 'No queda espacio en el disco. Libera espacio y vuelve a intentarlo.'
    return 'program', 'Ocurrió un error inesperado del programa. Se guardó el detalle en el registro de errores.'


class Worker:
    def __init__(self, work):
        """work(progress, cancel) → resultado. Corre en otro hilo; nunca toca Tk."""
        self.work = work
        self.cancel = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.finished = False
        self.last = (0.0, '')

    def start(self):
        self.thread.start()
        return self

    def progress(self, fraction: float, message: str = '') -> None:
        fraction = 0.0 if fraction != fraction else max(0.0, min(1.0, float(fraction)))
        self.events.put(('progress', fraction, message))

    def _run(self):
        try:
            result = self.work(self.progress, self.cancel)
        except BaseException as exc:  # noqa: BLE001 - se clasifica y se entrega al hilo de la interfaz
            kind, message = classify(exc)
            if kind == 'user' and self.cancel.is_set():
                kind = 'cancelled'
            self.events.put((kind, exc, message, traceback.format_exc()))
        else:
            if self.cancel.is_set():
                self.events.put(('cancelled', None, 'Cancelado.', ''))
            else:
                self.events.put(('done', result))

    def poll(self) -> list[tuple]:
        """Eventos pendientes; el progreso se resume en el último para no saturar la interfaz."""
        out, progress = [], None
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'progress':
                progress = event
                self.last = event[1:]
            else:
                if progress:
                    out.append(progress)
                    progress = None
                out.append(event)
                self.finished = True
        if progress:
            out.append(progress)
        return out


class Stages:
    """Reparte una barra de progreso entre etapas con peso (preparar rover, bases, procesar, transformar…)."""

    def __init__(self, progress, weights: list[float]):
        total = sum(weights) or 1.0
        self.progress = progress
        self.starts, acc = [], 0.0
        for w in weights:
            self.starts.append((acc/total, w/total))
            acc += w

    def stage(self, index: int):
        start, span = self.starts[index]

        def report(fraction: float, message: str = ''):
            fraction = 0.0 if fraction != fraction else max(0.0, min(1.0, float(fraction)))
            self.progress(start+span*fraction, message)
        return report


def log_error(folder, title: str, detail: str) -> None:
    """Registro local de errores del programa (sin datos del usuario más que rutas); nunca se envía a ningún lado."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder/'registro-de-errores.txt'
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(path.with_suffix('.anterior.txt'))
        with open(path, 'a', encoding='utf-8') as out:
            out.write(f'--- {datetime.now().isoformat(timespec="seconds")} · {title}\n{detail}\n')
    except OSError:
        pass


def run(widget, work, *, on_progress=None, on_done=None, on_error=None, on_cancel=None, interval=60):
    """Arranca `work` y entrega sus eventos en el hilo de Tk. Devuelve el Worker (su `.cancel` es el Event)."""
    worker = Worker(work).start()

    def tick():
        if not widget.winfo_exists():
            worker.cancel.set()
            return
        for event in worker.poll():
            kind = event[0]
            if kind == 'progress' and on_progress:
                on_progress(event[1], event[2])
            elif kind == 'done' and on_done:
                on_done(event[1])
            elif kind == 'cancelled' and on_cancel:
                on_cancel(event[2])
            elif kind in ('user', 'program') and on_error:
                on_error(kind, event[2], event[3])
        if not worker.finished:
            widget.after(interval, tick)
    widget.after(interval, tick)
    return worker
