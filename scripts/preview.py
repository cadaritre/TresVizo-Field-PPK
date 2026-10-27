"""Vista previa de cada pantalla con datos sintéticos y los dobles de prueba del motor (tests/dobles.py).

Uso: python scripts/preview.py --view static-result [--theme Oscuro] [--capture captura.png] [--seconds 0]
Usa una carpeta temporal para preferencias y proyectos: nunca toca los datos ni las preferencias reales.
Vistas: home, static-rover, static-bases, static-options, static-process, static-result, static-report,
points-base, points-own, points-rover, points-result, crs, networks, settings, about.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests'))

VIEWS = ['home', 'static-rover', 'static-bases', 'static-options', 'static-process', 'static-result',
         'static-report', 'points-base', 'points-own', 'points-rover', 'points-result', 'crs', 'networks', 'settings',
         'about']


def capture(app, path):
    """Captura de la ventana real (sin marco) en macOS, Windows o Linux; si hay un visor abierto, el visor."""
    import tkinter as tk
    viewers = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_viewable()]
    if viewers:
        app = viewers[-1]
    app.attributes('-topmost', True)
    app.lift()
    app.update()
    x, y, w, h = app.winfo_rootx(), app.winfo_rooty(), app.winfo_width(), app.winfo_height()
    if sys.platform == 'darwin':
        subprocess.run(['screencapture', '-x', '-o', f'-R{x},{y},{w},{h}', str(path)], check=True)
    else:
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(x, y, x+w, y+h), all_screens=True).save(path)
    app.attributes('-topmost', False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--view', choices=VIEWS, default='home')
    parser.add_argument('--theme', default='Claro')
    parser.add_argument('--text-size', default='normal', choices=['normal', 'grande', 'muy grande'])
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=820)
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--seconds', type=float, default=0, help='Cerrar solo después de N segundos.')
    parser.add_argument('--delay', type=float, default=0.15, help='Pausa de cada paso del motor de prueba.')
    args = parser.parse_args()

    import dobles
    from app.main_window import Application
    from app.wizard import BaseEntry, PointsJob, StaticJob
    from tresvizo_ppk import project as projects
    from tresvizo_ppk.model import Project

    with tempfile.TemporaryDirectory(prefix='tvppk-preview-') as folder:
        folder = Path(folder)
        data = folder/'Datos de campo'
        data.mkdir()

        def make(name, size=1):
            path = data/name
            path.write_bytes(b'\0'*size)
            return path
        rover = make('S-20261024-150214.bin', 18_400_000)
        bases = [[make('ineg2970.26d.Z', 2_100_000), make('ineg2970.26n.Z', 90_000)],
                 [make('uagu2970.26d.Z', 1_900_000), make('uagu2970.26n.Z', 88_000)]]
        csv = make('Levantamiento predio Las Flores.csv', 4_000)
        config = folder/'preferencias'
        backend = dobles.backend(delay=args.delay, real_report=True)
        app = Application(config, backend=backend)

        def report(exc, value, tb):
            import traceback
            traceback.print_exception(exc, value, tb)
            app.destroy()
        app.report_callback_exception = report

        def show_error(kind, message, detail='', title=None):
            print('ERROR', kind, title, message, detail, file=sys.stderr)
        app.show_error = show_error
        app.settings.save(company='Topografía y Geodesia del Centro', surveyor='Ing. Laura Méndez',
                          license_id='12345678', data_dir=str(folder/'TresVizo Field PPK'), theme=args.theme)
        from app.themes import palette
        from app.settings import TEXT_SIZES
        app.settings.save(text_size=args.text_size)
        app.theme.fonts.set_scale(TEXT_SIZES[args.text_size])
        app.theme.apply(palette(args.theme))
        app.after(300, app.fit_nav)
        app.geometry(f'{args.width}x{args.height}+40+40')
        for name, kind in (('Vértice Las Flores', 'static'), ('Levantamiento predio Las Flores', 'points'),
                           ('Control norte', 'static')):
            path = projects.save(Project(kind=kind), app.settings.projects_dir()/f'{name}.tvppk')
            app.remember(path)

        static_job = StaticJob(rover_files=[rover], point_name='LF-01', pole_height='1.800',
                               bases=[BaseEntry(bases[0], dobles.STATIONS[0], dobles.STATIONS[0].name),
                                      BaseEntry(bases[1], dobles.STATIONS[1], dobles.STATIONS[1].name)])
        static_job.coordinates = app.settings.coordinates()
        points_job = PointsJob(base=BaseEntry(bases[0], dobles.STATIONS[0], dobles.STATIONS[0].name),
                               rover_files=[rover], csv_file=csv)
        points_job.coordinates = app.settings.coordinates()

        def fill(name, job, step, process=False, report=False, view=None):
            page = app.show(name)
            page.job = job
            page.build_steps()
            page.touched = set(range(page.PROCESS_STEP))
            if process:
                path = app.settings.projects_dir()/f'{getattr(job, "point_name", "") or "Levantamiento predio Las Flores"}.tvppk'
                page.project, page.path, page.dirty = page._write(path), path, False
                page.update_title()
                page.go(page.PROCESS_STEP)
                page.process()

                def wait():
                    if page.output is None:
                        app.after(200, wait)
                        return
                    if report:
                        page.view_report()
                        app.after(2500, done)
                    else:
                        done()
                app.after(200, wait)
                return
            page.go(step)
            if view:
                view(page)
            done()

        def done():
            app.update()
            if args.capture:
                app.after(900, lambda: (capture(app, args.capture), app.destroy()))

        def start():
            v = args.view
            if v in ('home', 'crs', 'networks', 'settings', 'about'):
                app.show({'home': 'Inicio', 'crs': 'Sistemas y geoides', 'networks': 'Estaciones de referencia',
                          'settings': 'Ajustes', 'about': 'Acerca de'}[v])
                if v == 'networks':
                    app.after(600, lambda: app.pages['Estaciones de referencia'].tree.selection_set('INEG'))
                done()
            elif v.startswith('static-'):
                step = {'rover': 0, 'bases': 1, 'options': 2, 'process': 3}.get(v.split('-')[1], 3)
                if v == 'static-rover':
                    # Recién soltada la grabación: el nombre y la altura se llenan solos con su origen.
                    static_job.bases, static_job.point_name, static_job.pole_height = [], '', ''
                fill('Georreferenciar punto', static_job, step, process=v in ('static-result', 'static-report'),
                     report=v == 'static-report')
            elif v == 'points-own':
                own = PointsJob(base_kind='own', base=BaseEntry([make('S-20261024-145011.bin', 22_000_000)]))
                fill('PPK de puntos', own, 0)
            else:
                step = {'base': 0, 'rover': 1}.get(v.split('-')[1], 3)
                fill('PPK de puntos', points_job, step, process=v == 'points-result')
        app.after(400, start)
        if args.seconds:
            app.after(int(args.seconds*1000), app.destroy)
        app.mainloop()


if __name__ == '__main__':
    main()
