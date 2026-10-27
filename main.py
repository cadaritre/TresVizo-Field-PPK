"""TresVizo Field PPK: arranque de la aplicación de escritorio."""
import argparse
import os
import sys


def main():
    from tresvizo_ppk.version import __version__
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(prog='TresVizo Field PPK', description='Posproceso GNSS ligado a la RGNA.')
    parser.add_argument('proyecto', nargs='?', help='Proyecto .tvppk que se abre al arrancar.')
    parser.add_argument('--config-dir', help='Carpeta de preferencias separada (pruebas).')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--self-test', metavar='INFORME_JSON',
                        help='Comprueba el paquete instalado con datos temporales y termina.')
    # macOS pasa «-psn_…» al abrir desde el Finder en versiones antiguas: se ignora.
    args, _ = parser.parse_known_args([a for a in sys.argv[1:] if not a.startswith('-psn_')])
    if args.self_test:
        from app.package_check import run_check
        return run_check(args.self_test)
    from app.main_window import Application
    app = Application(args.config_dir, open_path=args.proyecto)
    app.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
