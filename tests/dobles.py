"""Dobles de prueba con las firmas de la §8 (motor, informe y exportación) para probar y revisar la interfaz sin
el motor real. Los datos son sintéticos: las coordenadas de las estaciones son aproximadas y dicen «doble de prueba»
en su fuente; nada de esto sale en el programa."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import time
from types import SimpleNamespace

from tresvizo_ppk.errors import Cancelled, UserError
from tresvizo_ppk.model import (AdjustmentResult, BaselineSolution, DataQuality, Ecef, Enu, FieldPoint,
                                ObservationSet, PointsResult, ProcessedPoint, Solution, StaticResult, Station)

A, F = 6378137.0, 1/298.257222101
SOURCE = 'Doble de prueba (coordenada aproximada, no oficial)'


def ecef(lat, lon, h):
    e2 = F*(2-F)
    la, lo = math.radians(lat), math.radians(lon)
    n = A/math.sqrt(1-e2*math.sin(la)**2)
    return Ecef((n+h)*math.cos(la)*math.cos(lo), (n+h)*math.cos(la)*math.sin(lo), (n*(1-e2)+h)*math.sin(la))


def offset(p: Ecef, east, north, up=0.0):
    lat = math.atan2(p.z, math.hypot(p.x, p.y))
    lon = math.atan2(p.y, p.x)
    dx = -math.sin(lon)*east-math.sin(lat)*math.cos(lon)*north+math.cos(lat)*math.cos(lon)*up
    dy = math.cos(lon)*east-math.sin(lat)*math.sin(lon)*north+math.cos(lat)*math.sin(lon)*up
    dz = math.cos(lat)*north+math.sin(lat)*up
    return Ecef(p.x+dx, p.y+dy, p.z+dz)


STATIONS = [
    Station('INEG', 'Aguascalientes (INEGI)', ecef(21.8560, -102.2840, 1888.0), Enu(0.002, 0.002, 0.005),
            'TRM59800.00', 'SCIS', 0.0, 'TRIMBLE NETR9', SOURCE),
    Station('UAGU', 'Universidad de Aguascalientes', ecef(21.9135, -102.3170, 1920.0), Enu(0.002, 0.003, 0.006),
            'TRM57971.00', 'NONE', 0.0083, 'TRIMBLE NETR9', SOURCE),
    Station('ZAC1', 'Zacatecas', ecef(22.7710, -102.5740, 2440.0), Enu(0.003, 0.003, 0.007),
            'LEIAR25.R4', 'LEIT', 0.0, 'LEICA GR50', SOURCE),
    Station('GUAX', 'Guanajuato', ecef(21.0170, -101.2560, 2030.0), Enu(0.002, 0.002, 0.006),
            'JAVRINGANT_DM', 'NONE', 0.0, 'JAVAD TRE_3', SOURCE),
]
START = datetime(2026, 10, 24, 15, 2, tzinfo=timezone.utc)


def _wait(progress, cancel, steps, message, delay):
    for i in range(steps):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if progress:
            progress((i+1)/steps, message)
        time.sleep(delay)


def quality(epochs=6180):
    satellites = ([f'G{n:02d}' for n in (2, 5, 7, 11, 13, 15, 18, 20, 29, 30)] +
                  [f'E{n:02d}' for n in (3, 5, 9, 15, 24, 34)] + [f'C{n:02d}' for n in (19, 20, 29, 30, 35)])
    end = START+timedelta(hours=1, minutes=43)
    gap = (START+timedelta(minutes=41), START+timedelta(minutes=41, seconds=20))
    tracks = {}
    for k, sat in enumerate(satellites):
        rise = START+timedelta(minutes=(k % 4)*9)          # unos salen tarde y otros se ponen antes
        sets = end-timedelta(minutes=(k % 3)*12)
        tracks[sat] = [(rise, gap[0]-timedelta(seconds=1)), (gap[1]+timedelta(seconds=1), sets)]
    slip_times = {'G13': [START+timedelta(minutes=12), START+timedelta(minutes=77)],
                  'E24': [START+timedelta(minutes=55)]}
    return DataQuality(epochs=epochs, expected_epochs=epochs+20, gaps=[gap], satellites=satellites,
                       signals={'GPS': ['L1C', 'L2W', 'L5Q'], 'Galileo': ['L1C', 'L5Q', 'L7Q'], 'BeiDou': ['L2I', 'L7I']},
                       cycle_slips={'G13': 2, 'E24': 1}, multipath_m={'L1C': 0.31, 'L2W': 0.42}, tracks=tracks,
                       slip_times=slip_times, interval_s=1.0)


def session_shift(options) -> float:
    """El doble mueve la solución unos milímetros al este por cada satélite o sistema fuera y si se recorta el tiempo,
    para que la comparación del editor de sesión tenga diferencias."""
    window = getattr(options, 'time_window', None)
    return 0.002*len(getattr(options, 'excluded_sats', []) or [])+(0.003 if window is not None else 0.0)


class FakeCore:
    """Motor de prueba: mismas firmas que `tresvizo_ppk.core.api`."""

    def __init__(self, delay=0.0, fail=None, points=14):
        self.delay, self.fail, self.npoints = delay, fail, points
        self.calls = []
        self.rover_point = offset(STATIONS[0].position, 2810.0, -3655.0, 14.0)

    def cors_networks(self):
        return [SimpleNamespace(id='rgna-inegi', title='RGNA — INEGI, México', countries=['MX'], frame='ITRF2008',
                                epoch=2010.0, attribution='Doble de prueba.', license='Solo pruebas.', bundled=True,
                                source={'title': SOURCE}, download={'url': 'https://www.inegi.org.mx/app/geo2/rgna/'})]

    def station_catalog(self, network_id=None):
        return list(STATIONS)

    def station_download_guide(self, station, start, end):
        return f'Baja los RINEX de {station.id} del {start:%Y-%m-%d}, de {start:%H:%M} a {end:%H:%M} UTC.'

    def can_download_station(self, station):
        return False

    def base_coordinate_candidates(self, obs, workdir=None, *, autonomous=None, cancel=None):
        official = self.find_station(obs.marker)
        if official is not None:
            return [official]
        if obs.approx_position is None:
            raise UserError('La base no está en ningún catálogo y su RINEX no trae posición.')
        return [Station(id=(obs.marker or 'BASE')[:9].upper(), name=obs.point_name or 'Base propia',
                        position=obs.approx_position, antenna_type=obs.antenna_type, receiver=obs.receiver,
                        source='Aproximada: APPROX POSITION XYZ del encabezado RINEX (unos metros).', official=False,
                        coordinate_origin='rinex-header', approximate=True)]

    def find_station(self, marker, network_id=None, near=None):
        return next((s for s in STATIONS if s.id == marker.upper()), None)

    def prepare_observations(self, paths, workdir, *, pole_height_m=None, marker=None, window=None, progress=None,
                             cancel=None):
        self.calls.append(('prepare', [Path(p).name for p in paths], pole_height_m, marker))
        _wait(progress, cancel, 4, f'Preparando {marker or "observaciones"}…', self.delay)
        Path(workdir).mkdir(parents=True, exist_ok=True)
        names = [Path(p).name for p in paths]
        recording = any(n.endswith(('.bin', '.part')) for n in names)
        station = next((s for s in STATIONS if names and names[0].upper().startswith(s.id)), None)
        if recording:     # grabación del MeridianV: la sesión trae su nombre y la altura del bastón de campo
            return ObservationSet(obs=[Path(workdir)/'unido.obs'], marker=marker or 'LF-01', receiver='MeridianV',
                                  antenna_type='HA-901A', antenna_height_m=(1.8 if pole_height_m is None else
                                                                            pole_height_m)+0.116,
                                  start=START, end=START+timedelta(hours=1, minutes=43), interval_s=1.0,
                                  rinex_version='3.04', source_files=[Path(p) for p in paths], radome='NONE',
                                  approx_position=offset(self.rover_point, 3.0, -2.0, 1.0), receiver_model='MeridianV',
                                  case_offset_m=0.116, epochs=6180, point_name=marker or 'LF-01',
                                  pole_height_m=1.8 if pole_height_m is None else pole_height_m,
                                  field_sources={'point_name': 'sesión del receptor',
                                                 'pole_height_m': 'sesión del receptor',
                                                 'antenna_type': 'sesión del receptor'})
        return ObservationSet(obs=[Path(workdir)/'unido.obs'], marker=marker or (station.id if station else 'ROVR'),
                              receiver=station.receiver if station else 'TRIMBLE NETR9',
                              antenna_type=station.antenna_type if station else 'TRM59800.00',
                              radome=station.radome if station else 'NONE',
                              antenna_height_m=pole_height_m if pole_height_m is not None else 0.0083,
                              start=START-timedelta(hours=1), end=START+timedelta(hours=3), interval_s=15.0,
                              rinex_version='3.04', source_files=[Path(p) for p in paths],
                              approx_position=station.position if station else self.rover_point, epochs=960,
                              point_name=marker or (station.id if station else 'ROVR'),
                              field_sources={'point_name': 'encabezado RINEX', 'antenna_height_m': 'encabezado RINEX'})

    def assess_quality(self, obs):
        return quality()

    def download_precise_products(self, start, end, workdir, progress=None, cancel=None):
        _wait(progress, cancel, 3, 'Bajando órbitas IGS…', self.delay)
        return [Path(workdir)/'IGS0OPSFIN_20262970000_01D_15M_ORB.SP3']

    def process_static(self, rover, bases, options, workdir, progress=None, cancel=None, point_name=''):
        self.calls.append(('static', [s.id for s, _ in bases], options.elevation_mask_deg))
        self.last_options = options
        _wait(progress, cancel, 10, 'Resolviendo ambigüedades…', self.delay)
        if self.fail:
            raise UserError(self.fail)
        point = offset(self.rover_point, session_shift(options), 0.0)
        baselines = []
        for index, (station, obs) in enumerate(bases):
            v = Ecef(point.x-station.position.x, point.y-station.position.y, point.z-station.position.z)
            length = math.sqrt(v.x**2+v.y**2+v.z**2)
            s = (0.004+length*1e-7)**2
            baselines.append(BaselineSolution(
                base=station, rover_marker=rover.marker, start=START, end=START+timedelta(hours=1, minutes=43),
                epochs_used=6150-index*40, satellites=quality().satellites[:18-index], solution=Solution.FIXED
                if index < 2 else Solution.FLOAT, ratio=12.4-index*3.1, rms_m=0.006+index*0.002, vector_ecef=v,
                vector_enu=Enu(0, 0, 0), length_m=length, azimuth_deg=137.2+index*40, zenith_deg=89.8,
                covariance_ecef=((s, s/10, 0), (s/10, s, s/20), (0, s/20, s*2.5)), rover_position=point,
                ephemeris=options.ephemeris, warnings=[] if index < 2 else
                [f'{station.id}: solución flotante (cociente 6.2 bajo el mínimo).']))
        adjustment = None
        if len(bases) >= 2:
            residuals = {b.base.id: Enu(0.003*(i+1), -0.002*(i+1), 0.006*(i+1)) for i, b in enumerate(baselines)}
            adjustment = AdjustmentResult(position=point, covariance_ecef=((1e-5, 0, 0), (0, 1e-5, 0), (0, 0, 3e-5)),
                                          sigma_enu=Enu(0.004, 0.005, 0.011), residuals_enu=residuals, sigma0=1.12,
                                          chi2=2.4, degrees_of_freedom=3*(len(bases)-1), passed=True)
        return StaticResult(point_name=point_name or rover.marker, rover=rover, rover_quality=quality(), bases=[s for s, _ in bases],
                            baselines=baselines, adjustment=adjustment, position=point,
                            sigma_enu=Enu(0.004, 0.005, 0.011), log=['RTKLIB-EX 2.5.1 (doble de prueba)'],
                            warnings=[] if len(bases) > 1 else ['Una sola estación: sin comprobación entre líneas base.'],
                            base_quality={s.id: quality() for s, _ in bases})

    def read_field_csv(self, path):
        if not Path(path).is_file():
            raise UserError(f'No se encuentra «{Path(path).name}».')
        points = []
        for i in range(self.npoints):
            start = START+timedelta(minutes=5+i*6)
            points.append(FieldPoint(name=f'P{i+1:02d}', code=['ESQ', 'BAN', 'POSTE', 'ARB'][i % 4], start=start,
                                     end=start+timedelta(seconds=60), antenna_height_m=1.8,
                                     field_position=offset(self.rover_point, i*7.5, (i % 5)*9.0, 0.3),
                                     field_solution='FIJO'))
        return points

    def process_points(self, rover, base, points, options, workdir, progress=None, cancel=None):
        station, _ = base
        self.calls.append(('points', station.id, len(points)))
        self.last_options = options
        shift = session_shift(options)
        _wait(progress, cancel, 10, 'Trayectoria cinemática…', self.delay)
        if self.fail:
            raise UserError(self.fail)
        processed = []
        for i, p in enumerate(points):
            solution = Solution.FIXED if i % 7 not in (3, 6) else Solution.FLOAT if i % 7 == 3 else Solution.NONE
            position = None if solution is Solution.NONE else offset(p.field_position, 0.004*(i % 3)+shift, -0.006,
                                                                     -0.01)
            processed.append(ProcessedPoint(
                point=p, solution=solution, position=position,
                sigma_enu=None if position is None else Enu(0.006+0.01*(solution is Solution.FLOAT), 0.007, 0.015),
                epochs_fixed=58 if solution is Solution.FIXED else 0, epochs_total=0 if position is None else 60,
                distance_to_base_m=4600.0+i*7, delta_from_field_enu=None if position is None else
                Enu(0.004*(i % 3), -0.006, -0.01),
                message='' if solution is Solution.FIXED else 'Promedio de épocas flotantes.' if position else
                'Sin épocas con solución en la ocupación (hueco de datos).'))
        return PointsResult(base=station, rover=rover, rover_quality=quality(), points=processed, fix_rate=0.874,
                            base_quality=quality(),
                            warnings=['Hueco de 20 s a las 15:43:00 UTC.'])


class FakeReport:
    def __init__(self):
        self.calls = []

    def _pdf(self, out, title, lines):
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        c = canvas.Canvas(str(out), pagesize=letter)
        c.setFont('Helvetica-Bold', 16)
        c.drawString(60, 720, title)
        c.setFont('Helvetica', 11)
        for i, line in enumerate(lines):
            c.drawString(60, 690-18*i, line)
        c.showPage()
        c.drawString(60, 720, 'Anexo: bitácora (doble de prueba)')
        c.save()
        return out

    def static_report(self, result, project, user, out_pdf):
        self.calls.append(('static', result.point_name, project.name, user.company))
        return self._pdf(out_pdf, 'Informe de línea base (doble de prueba)',
                         [f'Punto {result.point_name}', f'Empresa: {user.company}', f'{len(result.baselines)} líneas'])

    def points_report(self, result, project, user, out_pdf):
        self.calls.append(('points', len(result.points)))
        return self._pdf(out_pdf, 'Informe de PPK (doble de prueba)', [f'{len(result.points)} puntos'])


class FakeExport:
    STATIC_COLUMNS = [('staticSolution', 'Static solution', 'Solución del estático')]

    def __init__(self):
        self.calls = []

    @staticmethod
    def CsvOptions(**options):
        return dict(options)

    def export_points(self, result, choice, formato, out, **options):
        self.calls.append((formato, choice.crs_id, Path(out).name, options))
        Path(out).write_text('\n'.join(p.point.name for p in result.points), encoding='utf-8')
        return Path(out)

    def export_static(self, result, choice, formato, out, **options):
        self.calls.append(('static', formato, choice.crs_id, Path(out).name, options))
        Path(out).write_text(result.point_name, encoding='utf-8')
        return Path(out)

    def export_trajectory(self, result, choice, formato, out, progress=None, cancel=None):
        self.calls.append(('trajectory', formato, Path(out).name))
        Path(out).write_text('trayectoria', encoding='utf-8')
        return Path(out)

    def export_rinex(self, observations, folder):
        self.calls.append(('rinex', Path(folder).name))
        Path(folder).mkdir(parents=True, exist_ok=True)
        out = Path(folder)/'SESION.26O'
        out.write_text('rinex', encoding='utf-8')
        return [out]


def backend(delay=0.0, fail=None, crs=True, real_report=False):
    """Motor de prueba; sistemas reales; informe real si se pide y está disponible (vista previa)."""
    from app.backend import Backend
    parts = dict(core=FakeCore(delay, fail), report=FakeReport(), export=FakeExport())
    if real_report:
        try:
            import tresvizo_ppk.report.api as report_api
            parts['report'] = report_api
        except ImportError:
            pass
    if crs:
        import tresvizo_ppk.crs.api as crs_api
        parts['crs'] = crs_api
    return Backend(**parts)
