from argparse import Namespace
from datetime import timedelta
from enum import Enum
import json
import logging
import os
import pathlib
import shutil

from psutil import cpu_count

from pyschism import dates
from pyschism.driver import ModelConfig
from pyschism.enums import ForecastProduct
from pyschism.forcing.atmosphere import NWS2, GFS, HRRR
from pyschism.forcing.hydrology import NWM
from pyschism.forcing.tides import Tides
from pyschism.mesh import Hgrid, Vgrid, Fgrid, gridgr3, ManningsN, prop
from pyschism.param.schout import SurfaceOutputVars

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = 'config.json'
STATIC_DIRECTORY = 'static'
FORECAST_DIRECTORY = 'forecast'


class GridGr3Type(Enum):
    ALBEDO = gridgr3.Albedo
    DIFFMAX = gridgr3.Diffmax
    DIFFMIN = gridgr3.Diffmin
    WATERTYPE = gridgr3.Watertype
    SHAPIRO = gridgr3.Shapiro
    WINDROT = gridgr3.Windrot
    ELEV_IC = gridgr3.ElevIc
    NUDGE = gridgr3.Nudge


class PropType(Enum):
    FLUXFLAG = prop.Fluxflag
    TVDFLAG = prop.Tvdflag


class GridGr3Descriptor:

    def __init__(self, gridgr3_type):
        self.type = gridgr3_type

    def __set__(self, obj, val):
        if val is not None:
            assert isinstance(val, self.type)
        self.gridgr3 = val

    def __get__(self, obj, val):
        if not hasattr(self, 'gridgr3'):
            if obj.vgrid.is3D():
                return self.type.default(obj.hgrid)
        else:
            return self.gridgr3


class ForecastCliMeta(type):

    def __new__(meta, name, bases, attrs):
        attrs['surface_output_vars'] = SurfaceOutputVars()
        for gr3type in GridGr3Type:
            attrs[gr3type.name.lower()] = GridGr3Descriptor(gr3type.value)

        return type(name, bases, attrs)


class ForecastCli(metaclass=ForecastCliMeta):

    def __init__(self, args: Namespace):

        self.args = args
        coldstart, hotstart = self.get_drivers()
        if self.args.skip_run is True:
            if coldstart is not None:
                coldstart.write(self.coldstart_directory,
                                overwrite=self.args.overwrite)
            hotstart.write(self.hotstart_directory,
                           overwrite=self.args.overwrite)
        else:
            if coldstart is not None:
                coldstart.run(self.coldstart_directory,
                              overwrite=self.args.overwrite)
            hotstart.run(self.hotstart_directory,
                         overwrite=self.args.overwrite)

    def save_user_arguments(self):
        logger.info(
            f"Writing configuration file to path {self.config_file}")
        with open(self.config_file, 'w') as fp:
            json.dump(self.args.__dict__, fp, indent=4)

    def load_user_arguments(self):
        raise NotImplementedError

    def get_drivers(self):
        coldstart = self.get_coldstart()
        # hotstart = self.get_hotstart()
        hotstart = None
        return coldstart, hotstart

    def get_coldstart(self):
        # print(self.start_date)
        # exit()
        return self.config.coldstart(
            timestep=self.args.timestep,
            start_date=self.start_date - self.spinup_time,
            end_date=self.start_date,
            dramp=self.spinup_time,
            drampbc=self.spinup_time,
            dramp_ss=self.spinup_time,
            drampwafo=self.spinup_time,
            drampwind=self.spinup_time,
            nspool=None,
            ihfskip=None,
            nhot_write=None,
            stations=None,
            server_config=None,
            use_param_template=self.args.use_param_template,
            # **surface_outputs()
        )

    def get_hotstart(self):

        def get_surface_outputs():
            surface_outputs = {}
            outvars = []
            for vardata in self.surface_output_vars.values():
                for varname, _ in vardata:
                    outvars.append(varname)
            for key, val in self.args.__dict__.items():
                if key in outvars and val is True:
                    surface_outputs[key] = val
            return surface_outputs

        return self.config.hotstart(
            self.get_hotstart_driver(),
            timestep=self.args.timestep,
            end_date=timedelta(days=2) - timedelta(hours=2),
            nspool=self.args.nspool,
            **get_surface_outputs()
        )

    def get_hotstart_driver(self):
        pass

    @property
    def args(self):
        return self._args

    @args.setter
    def args(self, args: Namespace):

        if args.vgrid is not None:
            self.vgrid = Vgrid.open(args.vgrid)

        if args.fgrid is not None:
            self.fgrid = Fgrid.open(args.fgrid, crs=args.fgrid_crs)

        self._args = args

    @property
    def config(self):
        if not hasattr(self, '_config'):
            self._config = ModelConfig(
                self.hgrid,
                vgrid=self.vgrid,
                fgrid=self.fgrid,
                albedo=self.albedo,
                diffmin=self.diffmin,
                diffmax=self.diffmax,
                watertype=self.watertype,
                fluxflag=self.fluxflag,
                tvdflag=self.tvdflag,
                elev_ic=self.elev_ic,
                windrot=self.windrot,
                tides=self.tides,
                atmosphere=self.atmosphere,
                hydrology=self.hydrology,
                baroclinic=self.baroclinic,
                waves=self.waves,
            )
        return self._config

    @property
    def project_directory(self):
        if not hasattr(self, '_project_directory'):
            self._project_directory = pathlib.Path(self.args.project_directory)
            self._project_directory.mkdir(exist_ok=self.args.overwrite)
        return self._project_directory

    @property
    def config_file(self):
        return self.project_directory / CONFIG_FILE_NAME

    @property
    def static_directory(self):
        if not hasattr(self, '_static_directory'):
            self._static_directory = self.project_directory / STATIC_DIRECTORY
            self._static_directory.mkdir(exist_ok=self.args.overwrite)
        return self._static_directory

    @property
    def forecasts_directory(self):
        return self.project_directory / FORECAST_DIRECTORY

    @property
    def coldstart_directory(self):
        return self.hotstart_directory / 'coldstart'

    @property
    def hotstart_directory(self):
        if not hasattr(self, '_hotstart_directory'):
            timestamp = str(self.start_date).replace(' ', 'T')
            self._hotstart_directory = self.forecasts_directory / f'{timestamp}'
            self._hotstart_directory.parent.mkdir(exist_ok=True)
            self._hotstart_directory.mkdir(exist_ok=True)
        return self._hotstart_directory

    @property
    def hgrid_path(self):
        if not hasattr(self, '_hgrid_path'):
            self._hgrid_path = self.static_directory / 'hgrid.gr3'
            if not self._hgrid_path.exists() or self.args.overwrite is True:
                logger.info(
                    f'Writing initial hgrid file to {self._hgrid_path}.')
                if self.args.overwrite is True:
                    if self._hgrid_path.is_file():
                        os.remove(self._hgrid_path)
                shutil.copy2(self.args.hgrid, self._hgrid_path,
                             follow_symlinks=True)
        return self._hgrid_path

    @property
    def vgrid_path(self):
        if not hasattr(self, '_vgrid_path'):
            self._vgrid_path = self.static_directory / 'vgrid.in'
            if not self._vgrid_path.exists() or self.args.overwrite is True:
                logger.info(
                    f'Writing initial vgrid file to {self._vgrid_path}.')
                if self.args.vgrid_bin is not None:
                    Vgrid.from_binary(
                        self.hgrid,
                        binary=self.args.vgrid_bin
                    ).write(
                        self._vgrid_path,
                        overwrite=self.args.overwrite
                    )
                else:
                    if self.args.vgrid is None:
                        Vgrid.default().write(
                            self._vgrid_path,
                            overwrite=self.args.overwrite)
                    else:
                        if self.args.overwrite is True:
                            if self._vgrid_path.is_file():
                                os.remove(self._vgrid_path)
                        shutil.copy2(self.args.vgrid, self._vgrid_path,
                                     follow_symlinks=True)
        return self._vgrid_path

    @property
    def fgrid_path(self):
        if not hasattr(self, '_fgrid_path'):

            if self.args.fgrid is None:
                if self.vgrid.is2D():
                    self._fgrid_path = self.static_directory / 'manning.gr3'
                    ManningsN.linear_with_depth(
                        self.hgrid).write(self._fgrid_path,
                                          overwrite=self.args.overwrite)
                else:
                    raise NotImplementedError(
                        'User must pass an fgrid for 3D model.')
            else:

                if self.args.fgrid_type == 'auto':
                    # fgrid = pathlib.Path(self.args.fgrid)
                    fgrid = Fgrid.open(
                        self.args.fgrid, crs=self.args.fgrid_crs)
                    self._fgrid_path = self.static_directory / fgrid.name

                else:
                    self._fgrid_path = self.static_directory / \
                        (self.args.fgrid_type + '.gr3')

                if self.args.overwrite is True:
                    if self._fgrid_path.is_file():
                        os.remove(self._fgrid_path)
                shutil.copy2(self.args.fgrid, self._fgrid_path,
                             follow_symlinks=True)
        return self._fgrid_path

    @property
    def hgrid(self):
        if not hasattr(self, '_hgrid'):
            self._hgrid = Hgrid.open(self.hgrid_path, crs=self.args.hgrid_crs)
        return self._hgrid

    @property
    def vgrid(self):
        if not hasattr(self, '_vgrid'):
            self._vgrid = Vgrid.open(self.vgrid_path)
        return self._vgrid

    @property
    def fgrid(self):
        if not hasattr(self, '_fgrid'):
            self._fgrid = Fgrid.open(self.fgrid_path, crs=self.args.fgrid_crs)
        return self._fgrid

    @property
    def fluxflag(self):
        if not hasattr(self, '_fluxflag'):
            # TODO
            fluxflag = None
            self._fluxflag = fluxflag
        return self._fluxflag

    @property
    def tides(self):
        if not hasattr(self, '_tides'):
            if not self.args.all_constituents \
                    and not self.args.major_constituents \
                    and not self.args.constituents:
                return
            else:
                self._tides = Tides(
                    tidal_database=self.args.tidal_database,
                    velocity=self.args.bnd_vel
                )
                if self.args.all_constituents:
                    self._tides.use_all()
                if self.args.major_constituents:
                    self._tides.use_major()
                if self.args.constituents:
                    for constituent in self.args.constituents:
                        self._tides.use_constituent(constituent)
                if self.args.Z0 is not None:
                    self._tides.add_Z0(self.args.Z0)
        return self._tides

    @property
    def atmosphere(self):

        def sflux_1():
            for arg, value in self.args.__dict__.items():
                if 'gdas' in arg:
                    if value is True:
                        raise NotImplementedError(
                            'GDAS product not implemented.')
                elif 'gfs' in arg:
                    if value is True:
                        if arg == 'gfs':
                            return GFS()
                        else:
                            return GFS(product=arg)

        def sflux_2():
            for arg, value in self.args.__dict__.items():
                if 'hrrr' in arg:
                    if value is True:
                        return HRRR()

        if not hasattr(self, '_atmosphere'):
            _sflux_1 = sflux_1()
            if _sflux_1 is not None:
                self._atmosphere = NWS2(
                    _sflux_1,
                    sflux_2()
                    )
            else:
                self._atmosphere = None

        return self._atmosphere

    @property
    def hydrology(self):

        if self.args.hydrology is None:
            return

        if not hasattr(self, '_hydrology'):
            self._hydrology = []
            for hydro in self.args.hydrology:
                if hydro.upper() == "NWM":
                    # obj.logger.debug('Append NWM object.')
                    self._hydrology.append(NWM())

        return self._hydrology

    @property
    def baroclinic(self):
        # TODO: Add baroclinic forcing init
        return

    @property
    def waves(self):
        # TODO: Add waves forcing init
        return

    @property
    def start_date(self):
        if not hasattr(self, '_start_date'):
            self._start_date = dates.nearest_cycle()
        return self._start_date

    @property
    def spinup_time(self):
        if not hasattr(self, '_spinup_time'):
            if self.args.spinup_days is None:
                self._spinup_time = timedelta(days=0.)
            else:
                self._spinup_time = timedelta(
                    days=float(self.args.spinup_days))
        return self._spinup_time


def add_forecast_init(actions):
    init = actions.add_parser("init")
    init.add_argument("project_directory")
    init.add_argument('hgrid', help='Horizontal grid file.')
    init.add_argument('--fgrid', help='Friction grid file.')

    vgrid = init.add_mutually_exclusive_group()
    vgrid.add_argument('--vgrid', help='Vertical grid file.')
    vgrid.add_argument('--vgrid-bin')

    mesh_options = init.add_argument_group('mesh_options')
    mesh_options.add_argument('--hgrid-crs')
    mesh_options.add_argument('--fgrid-crs')
    mesh_options.add_argument(
        '--fgrid-type',
        choices=['auto', 'manning', 'drag', 'rough'],
        default='auto')
    init.add_argument("--timestep", type=float, required=True)
    init.add_argument("--forecast-days", type=float, required=True)
    init.add_argument(
        "--spinup-days",
        help="Number of days used for model initialization. "
        "Defaults to 15 days spinup.",
        type=float, default=15.)
    init.add_argument(
        "--skip-run", action="store_true",
        help="Skips running the model.")
    init.add_argument('--nproc', type=int, default=cpu_count(logical=False))
    init.add_argument('--use-param-template', action='store_true')
    _add_tidal_constituents(init)
    _add_atmospheric_forcing(init)
    _add_hydrologic_forcing(init)
    init.add_argument('--shapiro', action='store_true')
    init.add_argument('--shapiro-bin', action='store_true')

    # TODO: Additional forcings.
    # _add_wave_forcing(forecast)
    model_outputs = init.add_argument_group('model_outputs')
    # TODO: Stations outputs.
    _add_stations_outputs(model_outputs)
    _add_surface_outputs(model_outputs)
    server_config = init.add_subparsers(dest="server_config")
    slurm = server_config.add_parser(
        'slurm', help="Add options for slurm run configuration.")
    slurm.add_argument('--account')
    slurm.add_argument('--partition')
    slurm.add_argument('--walltime', type=float, help="In hours, float.")
    slurm.add_argument('--slurm-filename')
    slurm.add_argument('--slurm-rundir')
    slurm.add_argument('--run-name')
    slurm.add_argument('--mail-type')
    slurm.add_argument('--mail-user')
    slurm.add_argument('--log-filename')
    slurm.add_argument('--path-prefix')
    slurm.add_argument('--slurm-nodes')
    slurm.add_argument('--slurm-launcher', default='srun')
    slurm.add_argument('--extra-commands', action='append')
    slurm.add_argument(
        '--module',
        default=list(),
        action='append',
        dest='modules')


def add_forecast_update(actions):
    update = actions.add_parser("update")
    update.add_argument("project_directory")


def add_forecast(subparsers):
    forecast = subparsers.add_parser('forecast')
    forecast.add_argument(
        "--overwrite", action="store_true",
        help="Allow overwrite of output directory.")
    actions = forecast.add_subparsers(dest="action")
    add_forecast_init(actions)
    add_forecast_update(actions)


def add_forecastd(subparsers):
    forecastd = subparsers.add_parser('forecastd')
    actions = forecastd.add_subparsers(dest='action')
    actions.required = True
    actions.add_parser('start')
    actions.add_parser('stop')
    actions.add_parser('restart')
    add = actions.add_parser('add')
    add.add_argument("-o", "--output-directory", dest='outdir')
    _add_mesh_options(add)
    _add_tidal_constituents(add)
    _add_atmospheric_forcing(add)
    _add_hydrologic_forcing(add)


def add_viewerd(subparsers):
    viewerd = subparsers.add_parser('viewerd')
    actions = viewerd.add_subparsers(dest='action')
    actions.required = True
    start = actions.add_parser('start')
    start.add_argument('--deploy', action='store_true')
    actions.add_parser('stop')
    actions.add_parser('restart')


def add_autodocd(subparsers):
    autodocd = subparsers.add_parser('autodocd')
    actions = autodocd.add_subparsers(dest='action')
    actions.required = True
    actions.add_parser('start')
    actions.add_parser('stop')
    actions.add_parser('restart')


def add_plot(subparsers):
    plot = subparsers.add_parser('plot')
    plot.add_argument(
        'resource', type=pathlib.Path,
        help='Filename or directory containing SCHISM outputs.')
    plot.add_argument('variable', help='Name of variable to plot.')
    output_type = plot.add_subparsers(dest='output_type')
    output_type.required = True
    surface_plot = output_type.add_parser('surface', help="Plot SCHISM surface"
                                          " outputs.")
    surface_plot.add_argument("--start")
    stations = output_type.add_parser('stations', help="Plot SCHISM stations "
                                      "outputs.")
    stations.add_argument('-s', '--station', nargs='*', type=int,
                          help='Station index in station.in to include in '
                          'plot.')


def _add_mesh_options(parser):
    parser.add_argument('hgrid', help='Horizontal grid file.')
    parser.add_argument('fgrid', help='Friction grid file.')
    mesh_options = parser.add_argument_group('mesh_options')
    mesh_options.add_argument('--vgrid', help='Vertical grid file.')
    mesh_options.add_argument('--hgrid-crs')
    mesh_options.add_argument('--fgrid-crs')


def _add_tidal_constituents(parser):
    tides = parser.add_argument_group('tides')
    options = tides.add_mutually_exclusive_group()
    options.required = True
    options.add_argument("--all-constituents", action="store_true")
    options.add_argument("--major-constituents", action="store_true")
    options.add_argument(
        "-c", "--constituents",
        action='append',
        # choices=["K1", "O1", "P1", "Q1", "MM", "Mf", "M4", "MN4", "MS4",
        #         "2N2", "S1"],
        choices=["Z0", "K2", "S2", "M2", "N2", "K1", "P1", "O1", "Q1"],
        dest='constituents',
        default=False,
        help="Tidal constituent to be forced in the model (case-insensitive).")
    tides.add_argument("--tidal-database", choices=['tpxo', 'hamtide'],
                       default='tpxo')
    tides.add_argument("--include-tidal-velocity",
                       "--bnd-vel", action="store_true", dest='bnd_vel')
    tides.add_argument("--Z0", type=float)


def _add_atmospheric_forcing(parser):
    atmospheric_forcing_1 = parser.add_argument_group(
        'atmospheric forcing level 1')
    data_source_1 = atmospheric_forcing_1.add_mutually_exclusive_group()
    for product in ForecastProduct:
        data_source_1.add_argument(
            f"--{product.value.lower().replace('_', '-')}",
            action="store_true",
            help=f'Use {product.value} as data source.')
    data_source_1.add_argument(
        "--gfs", action="store_true",
        help="Alias for --gfs-0p25-1hr")
    data_source_1.add_argument(
        "--gdas", action='store_true',
        help="Alias for --gdas-0p25",
    )
    atmospheric_forcing_1.add_argument(
        "--no-air-1", action="store_true",
        help="Disables air mass forcing option.")
    atmospheric_forcing_1.add_argument(
        "--no-prc-1", action='store_true',
        help="Disables precipitation option.")
    atmospheric_forcing_1.add_argument(
        "--no-rad-1", action='store_true',
        help="Disables solar radiation flux option.")

    atmospheric_forcing_2 = parser.add_argument_group(
        'atmospheric forcing level 2')
    data_source_2 = atmospheric_forcing_2.add_mutually_exclusive_group()
    for product in ["hrrr"]:
        data_source_2.add_argument(
            f"--{product}",
            help=f'Use {product.upper()} as data source.',
            action='store_true'
        )
    atmospheric_forcing_2.add_argument(
        "--no-air-2", action="store_true",
        help="Disables air mass forcing option.")
    atmospheric_forcing_2.add_argument(
        "--no-prc-2", action='store_true',
        help="Disables precipitation option.")
    atmospheric_forcing_2.add_argument(
        "--no-rad-2", action='store_true',
        help="Disables solar radiation flux option.")


def _add_hydrologic_forcing(parser):
    src_snk = parser.add_argument_group('Sources and sinks')
    src_snk.add_argument("--hydrology", action="append",
                         help="Add source and sink flow.",
                         choices=['NWM'],
                         default=[])


def _add_wave_forcing(parser):
    wave_forcing = parser.add_argument_group('Waves')
    data_source = wave_forcing.add_mutually_exclusive_group()
    for product in ["WWIII"]:
        data_source.add_argument(f'--{product}')


def hydro():
    """
    hydro output options
    """
    return {
        1: ("elev", "0: off; 1: on - elev. [m]"),
        2: ("air_pressure", "air pressure [Pa]"),
        3: ("air_temperature", "air temperature [C]"),
        4: ("specific_humidity", "Specific humidity [-]"),
        5: ("solar_radiation", "solar (shortwave) radiation [W/m/m]"),
        6: ("sensible_flux", "sensible flux (positive upward) [W/m/m]"),
        7: ("latent_heat", "latent heat flux (positive upward) [W/m/m]"),
        8: ("upward_longwave",
            "upward longwave radiation (positive upward) [W/m/m]"),
        9: ("downward_longwave",
            "downward longwave radiation (positive downward) [W/m/m]"),
        10: ("total_heat_flux", "total flux=-flsu-fllu-(radu-radd) [W/m/m]"),
        11: ("evaporation", "evaporation rate [kg/m/m/s]"),
        12: ("precipitation", "precipitation rate [kg/m/m/s]"),
        13: ("bottom_stress", "Bottom stress vector [kg/m/s^2(Pa)]"),
        14: ("wind_speed", "wind velocity vector [m/s]"),
        15: ("wind_stress", "wind stress vector [m^2/s/s]"),
        16: ("dahv", "depth-averaged vel vector [m/s]"),
        17: ("vertical_velocity", "vertical velocity [m/s]"),
        18: ("temp", "water temperature [C]"),
        19: ("salt", "water salinity [PSU]"),
        20: ("water_density", "water density [kg/m^3]"),
        21: ("diffusivity", "eddy diffusivity [m^2/s]"),
        22: ("viscosity", "eddy viscosity [m^2/s]"),
        23: ("TKE", "turbulent kinetic energy"),
        24: ("mixing-lenght", "turbulent mixing length [m]"),
        25: ("hvel", "horizontal vel vector [m/s]"),
        26: ("hvel_side", "horizontal vel vector defined @side [m/s]"),
        27: ("wvel_elem", "vertical vel. @elem [m/s]"),
        28: ("temp_elem", "T @prism centers [C]"),
        29: ("salt_elem", "S @prism centers [PSU]"),
        30: ("pressure_gradient",
             "Barotropic pressure gradient force vector (m.s-2) @side "
             "centers"),

    }


def wwm():
    """
    WWM output options
    """
    return {
        1: ("WWM_1", "sig. height (m)"),
        2: ("WWM_2", "Mean average period (sec) - TM01"),
        3: ("WWM_3",
            "Zero down crossing period for comparison with buoy (s) - TM02"),
        4: ("WWM_4", "Average period of wave runup/overtopping - TM10"),
        5: ("WWM_5", "Mean wave number (1/m)"),
        6: ("WWM_6", "Mean wave length (m)"),
        7: ("WWM_9",
            "Mean average energy transport direction (degr) - MWD in NDBC?"),
        8: ("WWM_10", "Mean directional spreading (degr)"),
        9: ("WWM_11", "Discrete peak period (sec) - Tp"),
        10: ("WWM_12",
             "Continuous peak period based on higher order moments (sec)"),
        11: ("WWM_13", "Peak phase vel. (m/s)"),
        12: ("WWM_14", "Peak n-factor."),
        13: ("WWM_15", "Peak group vel. (m/s)"),
        14: ("WWM_16", "Peak wave number"),
        15: ("WWM_17", "Peak wave length"),
        16: ("WWM_18", "Peak (dominant) direction (degr)"),
        17: ("WWM_19", "Peak directional spreading"),
        18: ("WWM_20", "Discrete peak direction (radian?) "),
        19: ("WWM_21", "Orbital vel. (m/s) "),
        20: ("WWM_22", "RMS Orbital vel. (m/s) "),
        21: ("WWM_23", "Bottom excursion period (sec?) "),
        22: ("WWM_24", "Bottom wave period (sec) "),
        23: ("WWM_25", "Uresell number based on peak period "),
        24: ("WWM_26", "Friction velocity (m/s?) "),
        25: ("WWM_27", "Charnock coefficient "),
        26: ("WWM_28", "Rougness length "),
        27: ("WWM_energy_dir", "WWM_energy vector"),
        28: ("wave-force",
             "Wave force vector (m.s-2) computed by wwm @side centers and "
             "whole levels"),
    }


def gen():
    """
    gen output options
    """
    return {
        1: ("GEN_1", "1st tracer"),
        2: ("GEN_2", "2nd tracer"),
    }


def age():
    """
    age output options
    """
    return {
        1: ("AGE_1", "Indices from \"1\" to \"ntracer_age/2\"; [days]"),
        2: ("AGE_2", "Indices from \"1\" to \"ntracer_age/2\"; [days]"),
    }


def sed():
    """
    sed output options
    """
    return {
        1: ("SED_depth_change",
            "bottom depth _change_ from init. condition (m)"),
        2: ("SED_D50", " Bed median grain size in the active layer (mm)"),
        3: ("SED_bed_stress", " Bottom shear stress (Pa)"),
        4: ("SED_bed_roughness", " Bottom roughness lenghth (mm)"),
        5: ("SED_TSC", "total suspended concentration (g/L)"),
        6: ("bed_thickness", " total bed thickness @elem (m)"),
        7: ("bed_age", " total bed age over all layers @elem (sec)"),
        8: ("z0st",
            " Sediment transport roughness length @elem (m) (z0st_elem)"),
        9: ("z0cr", "current-ripples roughness length @elem (m) (z0cr_elem)"),
        10: ("z0sw", "sand-waves roughness length (m) @elem (z0sw_elem)"),
        11: ("z0wr", "wave-ripples roughness length @elem (m) (z0wr_elem)"),
        12: ("SED3D_1",
             "conc. of 1st class (one output need by each class) [g/L]"),
        13: ("SED_bdld_1",
             "Bedload transport rate vector (kg.m-1.s-1) for 1st tracer (one "
             "output need by tracer)"),
        14: ("SED_bedfrac_1",
             "Bed fraction 1st tracer (one output need by each class) [-]"),
        15: ("SED3D_2", "conc. of 2nd class"),
        16: ("SED_bdld_2", "Bedload transport of 2nd class"),
        17: ("SED_bedfrac_3", "Bed fraction of 2nd class"),
    }


def eco():
    """
    EcoSim output options
    """
    return {
        1: ("ECO_1", "EcoSim outputs")
    }


def icm():
    """
    ICM output options
    """
    return {
        1: ("ICM_Chl", "Chlorophyll"),
        2: ("ICM_pH", "PH values (ICM_PH on)"),
        3: ("ICM_PrmPrdt", "ICM primary production @elem [gC/m^3/day]"),
        4: ("ICM_DIN", "ICM totoal inorganic nitrogen (DIN) @elem [gN/m^3]"),
        5: ("ICM_PON", "ICM paticulate organic nitrogen (PON) @elem [gN/m^3]"),
        6: ("ICM_SED_BENDOC",
            "ICM bed sediment flux arrays: SED_BENDOC (output "
            "name:ICM_SED_BENDOC) @elem [gC/(m^2 day)]"),
        7: ("ICM_SED_BENNH4",
            "ICM bed sediment flux arrays: SED_BENNH4 (output "
            "name:ICM_SED_BENNH4) @elem [gC/(m^2 day)]"),
        8: ("ICM_SED_BENNO3",
            "ICM bed sediment flux arrays: SED_BENNO3 (output "
            "name:ICM_SED_BENNO3)@elem [gC/(m^2 day)]"),
        9: ("ICM_SED_BENPO4",
            "ICM bed sediment flux arrays: SED_BENPO4 (output "
            "name:ICM_SED_BENPO4) @elem [gC/(m^2 day)]"),
        10: ("ICM_SED_BENCOD",
             "ICM bed sediment flux arrays: SED_BENCOD (output "
             "name:ICM_SED_BENCOD) @elem [gC/(m^2 day)]"),
        11: ("ICM_SED_BENDO",
             "ICM bed sediment flux arrays: SED_BENDO (output "
             "name:ICM_SED_BENDO) @elem [gC/(m^2 day)]"),
        12: ("ICM_SED_BENSA",
             "ICM bed sediment flux arrays: SED_BENSA (output "
             "name:ICM_SED_BENSA) @elem [gC/(m^2 day)]"),
        13: ("ICM_lfsav",
             "ICM SAV leaf biomass @elem [gC/m^3] (k=1 is surface)"),
        14: ("ICM_stsav", "ICM SAV stem biomass @elem [gC/m^3]"),
        15: ("ICM_rtsav", "ICM SAV root biomass @elem [gC/m^3]"),
        16: ("ICM_tlfsav", "ICM SAV total leaf biomass @elem [gC/m^2]"),
        17: ("ICM_tstsav", "ICM SAV total stem biomass @elem [gC/m^2]"),
        18: ("ICM_trtsav", "ICM SAV total root biomass @elem [gC/m^2]"),
        19: ("ICM_hcansav", "ICM SAV canopy height @elem [m]"),
        20: ("ICM_CNH4", "bottom NH4 conc"),
        21: ("ICM_CNH3", "bottom NO3 conc"),
        22: ("ICM_CPIP", "bottom P conc"),
        23: ("ICM_CPOS", "bottom Si conc"),
        24: ("ICM_CCH4", "bottom CH4 conc"),
        25: ("ICM_CSO4", "bottom SO4 conc"),
        26: ("ICM_CH2S", "bottom H2S conc"),
        27: ("ICM_SEDPON1", "bottom PON g1 conc"),
        28: ("ICM_SEDPON2", "bottom PON g2 conc"),
        29: ("ICM_SEDPON3", "bottom PON g3 conc"),
        30: ("ICM_SEDPOP1", "bottom POP g1 conc"),
        31: ("ICM_SEDPOP2", "bottom POP g2 conc"),
        32: ("ICM_SEDPOP3", "bottom POP g3 conc"),
        33: ("ICM_SEDPOC1", "bottom POC g1 conc"),
        34: ("ICM_SEDPOC2", "bottom POC g2 conc"),
        35: ("ICM_SEDPOC3", "bottom POC g3 conc"),
        36: ("ICM_EROH2S", "erosion flux H2S"),
        37: ("ICM_EROLPOC", "erosion flux LPOC"),
        38: ("ICM_ERORPOC", "erosion flux RPOC"),
        39: ("ICM_DO_consumption", "DO consumption"),
        40: ("ICM_GP1", "PB growth #1"),
        41: ("ICM_GP2", "PB growth #2"),
        42: ("ICM_GP3", "PB growth #3"),
        43: ("ICM_1", "Zoo. #1"),
        44: ("ICM_2", "Zoo. #2"),
        45: ("ICM_3", "phyto #1"),
        46: ("ICM_4", "phyto #2"),
        47: ("ICM_5", "phyto #3"),
        48: ("ICM_6", "RPOC"),
        49: ("ICM_7", "LPOC"),
        50: ("ICM_8", "DOC"),
        51: ("ICM_9", "RPON"),
        52: ("ICM_10", "LPON"),
        53: ("ICM_11", "DON"),
        54: ("ICM_12", "NH4"),
        55: ("ICM_13", "NO3"),
        56: ("ICM_14", "RPOP"),
        57: ("ICM_15", "LPOP"),
        58: ("ICM_16", "DOP"),
        59: ("ICM_17", "PO4t"),
        60: ("ICM_18", "Si- biogenic"),
        61: ("ICM_19", "available Si"),
        62: ("ICM_20", "COD: Chemical oxygen demand"),
        63: ("ICM_21", "DO"),
        64: ("ICM_22", "TIC"),
        65: ("ICM_23", "ALK"),
        66: ("ICM_24", "CA"),
        67: ("ICM_25", "CACO3"),
    }


def cos():
    """
    CoSINE output options
    """
    return {
        1: ("COS_1", "COS_1"),
        2: ("COS_2", "COS_2"),
        3: ("COS_3", "COS_3"),
        4: ("COS_4", "COS_4"),
        5: ("COS_5", "COS_5"),
        6: ("COS_6", "COS_6"),
        7: ("COS_7", "COS_7"),
        8: ("COS_8", "COS_8"),
        9: ("COS_9", "COS_9"),
        10: ("COS_10", "COS_10"),
        11: ("COS_11", "COS_11"),
        12: ("COS_12", "COS_12"),
        13: ("COS_13", "COS_13"),
    }


def fib():
    """
    Fecal indicating bacteria output options
    """
    return {
        1: ("FIB_1", "FIB_1")
    }


def sed2d():
    """
    SED2D output options
    """
    return {
        1: ("SED2D_depth_change",
            "bottom depth _change_ from init. condition (m)"),
        2: ("SED2D_Cd", "drag coefficient used in transport formulae"),
        3: ("SED2D_cflsed", "Courant number (b.qtot.dt / h.dx)"),
        4: ("SED2D_d50", "Top layer d50 (m)"),
        5: ("SED2D_total_transport", "total transport rate vector (kg/m/s)"),
        6: ("SED2D_susp_load", "suspended tranport rate vector (kg/m/s)"),
        7: ("SED2D_bed_load", "bedload transport rate vector (kg/m/s)"),
        8: ("SED2D_average_transport",
            "time averaged total transport rate vector (kg/m/s)"),
        9: ("SED2D_bottom_slope",
            "bottom slope vector (m/m); negative uphill"),
        10: ("z0eq2d", "Total roughness length @elem (m) (z0eq)"),
        11: ("z0cr2d", "current-ripples roughness length @elem (m) (z0cr)"),
        12: ("z0sw2d", "sand-waves roughness length @elem (m) (z0sw)"),
        13: ("z0wr2d", "wave-ripples roughness length @elem (m) (z0wr)"),
    }


def mar():
    """
    marsh output options
    """
    return {
        1: ("marsh_flag", "marsh_flag"),
    }


def ice():
    """
    ice output options
    """
    return {
        1: ("ICE_velocity", "ice advective velcoity vector [m/s]"),
        2: ("ICE_strain_rate", "strain rate @ elem [1/sec]"),
        3: ("ICE_net_heat_flux",
            "net heat flux to ocean (>0 warm up SST) [W/m/m]"),
        4: ("ICE_fresh_water_flux",
            "net fresh water flux to ocean (>0 freshens up SSS) [kg/s/m/m]"),
        5: ("ICE_top_T", "ice temperature [C] at air-ice interface"),
        6: ("ICE_tracer_1", "ice volume [m]"),
        7: ("ICE_tracer_2", "ice concentration [-]"),
        8: ("ICE_tracer_3", "snow volume [m]"),
    }


def ana():
    return {
        1: ("ANA_air_pres_grad_x", "x-component of 𝛁air_pres/ρ0 [m/s/s]"),
        2: ("ANA_air_pres_grad_y", "y-component of 𝛁air_pres/ρ0 [m/s/s]"),
        3: ("ANA_tide_pot_grad_x",
            "α*g*𝛁Ψ [m/s/s] (gradient of tidal potential)"),
        4: ("ANA_tide_pot_grad_y", "α*g*𝛁Ψ [m/s/s]"),
        5: ("ANA_hor_viscosity_x", "𝛁·(μ𝛁u) [m/s/s] (horizontal viscosity)"),
        6: ("ANA_hor_viscosity_y", "𝛁·(μ𝛁u) [m/s/s]"),
        7: ("ANA_bclinic_force_x",
            "-g/rho0* ∫_z^η dr_dx dz  [m/s/s] (b-clinic gradient)"),
        8: ("ANA_bclinic_force_y", "-g/rho0* ∫_z^η dr_dy dz  [m/s/s]"),
        9: ("ANA_vert_viscosity_x",
            "d (ν du/dz)/dz [m/s/s] - no vegetation effects (vertical "
            "viscosity)"),
        10: ("ANA_vert_viscosity_y",
             "d (ν dv/dz)/dz [m/s/s] - no vegetation effects"),
        11: ("ANA_mom_advection_x", "(u·𝛁) u [m/s/s] (momentum advection)"),
        12: ("ANA_mom_advection_y", "(u·𝛁) u [m/s/s]"),
        13: ("ANA_Richardson", "gradient Richardson number [-]"),
        14: ("ANA_transport_min_dt_elem",
             "min time step at each element over all subcycles in horizontal "
             "transport solver [s]  "),
    }


def _add_surface_outputs(parser):

    parser.add_argument(
        '--nspool',
        help='If passing an integer, it is interpreted as timestep, '
             'if passing a float, it is interpreted as hours.')

    outputs = {
        'hyd': hydro(),
        'wwm': wwm(),
        'gen': gen(),
        'age': age(),
        'sed': sed(),
        'eco': eco(),
        'icm': icm(),
        'cos': cos(),
        'fib': fib(),
        'sed2d': sed2d(),
        'mar': mar(),
        'ice': ice(),
        'ana': ana(),
    }
    for short_name, output in outputs.items():
        for id, (long_name, help_msg) in output.items():
            parser.add_argument(
                f"--{long_name.lower().replace('_', '-')}",
                f"-{short_name}{id}",
                help=help_msg,
                action='store_true'
            )


def _add_stations_outputs(parser):
    parser.add_argument('--stations-file')
    parser.add_argument('--stations-file-crs')
    parser.add_argument('--nspool-sta')
    parser.add_argument('--stations-elev', action='store_true')
    parser.add_argument('--stations-prmsl', action='store_true')
    parser.add_argument('--stations-uwind', action='store_true')
    parser.add_argument('--stations-vwind', action='store_true')
    parser.add_argument('--stations-temp', action='store_true')
    parser.add_argument('--stations-sal', action='store_true')
    parser.add_argument('--stations-uvel', action='store_true')
    parser.add_argument('--stations-vvel', action='store_true')
    parser.add_argument('--stations-wvel', action='store_true')