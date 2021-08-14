from datetime import datetime, timedelta
from enum import Enum
import pathlib
import tempfile
from typing import Union
import logging


import pytz
from matplotlib.transforms import Bbox
from netCDF4 import Dataset
import numpy as np

from pyschism.forcing.nws.nws2.sflux import (
    SfluxDataset,
    AirComponent,
    PrcComponent,
    RadComponent,
)
from pyschism.dates import nearest_zulu, localize_datetime, nearest_cycle

BASE_URL = 'https://nomads.ncep.noaa.gov/dods'
logger = logging.getLogger(__name__)


class HRRRInventory:

    def __init__(self, start_date=None, bbox=None):
        self.start_date = nearest_cycle() if start_date is None else \
            localize_datetime(start_date).astimezone(pytz.utc)
        #self.rnday = rnday if isinstance(rnday, timedelta) else \
        #    timedelta(days=rnday)
        #if self.rnday > timedelta(days=2) - timedelta(hours=1):
        #    raise ValueError(
        #        'Maximum run days for HRRR is '
        #        f'{timedelta(days=2) - timedelta(hours=1)} but got {rnday}.')

        if self.start_date != nearest_cycle(self.start_date):
            raise NotImplementedError(
                'Argment start_date is does not align with any HRRR cycle '
                'times.')
        #self._files = {_: None for _ in np.arange(
        #    self.start_date,
        #    self.start_date + self.rnday + self.output_interval,
        #    self.output_interval
        #).astype(datetime)}

        #for dt in self.nearest_zulus:
        #    if None not in list(self._files.values()):
        #        break
        base_url = BASE_URL + f'/{self.product}' + \
            f'/hrrr{start_date.strftime("%Y%m%d")}'
            # cycle
            #for cycle in reversed(range(0, 24, int(self.output_interval.total_seconds() / 3600))):
        test_url = f'{base_url}/hrrr_sfc.t00z'
        try:
            logger.info(f'Checking url: {test_url}')
            nc = Dataset(test_url)
            logger.info('Success!')
        except OSError as e:
            if e.errno == -70:
                print()
                #continue
            elif e.errno == -73:
                nc = False

                def retry():
                    try:
                        return Dataset(test_url)
                    except Exception:
                        return False

                while not isinstance(nc, Dataset):
                    nc = retry()
            else:
                raise e
        self.nc = nc
        #file_dates = self.get_nc_datevector(nc)
        #for _datetime in reversed(list(self._files.keys())):
        #            if _datetime in file_dates:
        #                if self._files[_datetime] is None:
        #                    self._files[_datetime] = nc
        #            else:
        #                logger.debug(f'No data for time {str(_datetime)} in '
        #                             f'{test_url}.')
        #        if not any(nc is None for nc in self._files.values()):
        #            break

        #missing_records = [dt for dt, nc in self._files.items() if nc is None]
        #if len(missing_records) > 0:
        #    raise ValueError(f'No HRRR data for dates: {missing_records}.')

        self._bbox = self._modified_bbox(bbox)

    def put_sflux_field(self, hrrr_varname: str, dst: Dataset,
                        sflux_varname: str):

        lon_idxs, lat_idxs = self._bbox_indexes(self._bbox)
        #for i, (dt, nc) in enumerate(self._files.items()):
        logger.info(
            f'Putting HRRR field {hrrr_varname} for as '
            f'{sflux_varname} from file '
            f'{self.nc.filepath().replace(f"{BASE_URL}/", "")}.')

        def put_nc_field():
            try:
                dst[sflux_varname][:, :, :] = self.nc.variables[hrrr_varname][
                        :, lat_idxs, lon_idxs]
                return True
            except RuntimeError:
                logger.info('Failed! retrying...')
                return False

        success = False
        while success is False:
            success = put_nc_field()
        dst.sync()

    def get_nc_time_index(self, nc, dt):
        return np.where(np.in1d(self.get_nc_datevector(nc), [dt]))[0][0]

    def get_nc_datevector(self, nc):
        try:
            base_date = localize_datetime(
                datetime.strptime(
                    nc['time'].minimum.split('z')[-1],
                    '%d%b%Y')) + timedelta(
                hours=float(nc['time'].minimum.split('z')[0]))
            return np.arange(
                base_date + self.output_interval,
                base_date + len(nc['time'][:])*self.output_interval,
                self.output_interval
            ).astype(datetime)
        except RuntimeError:
            return self.get_nc_datevector(nc)

    def get_sflux_timevector(self):
        #timevec = list(self._files.keys())
        timevec = list(self.get_nc_datevector(self.nc))
        _nearest_zulu = nearest_zulu(np.min(timevec))
        return [(localize_datetime(x) - _nearest_zulu) / timedelta(days=1)
                for x in timevec]

    def xy_grid(self):
        lon_idxs, lat_idxs = self._bbox_indexes(self._bbox)
        return np.meshgrid(self.lon[lon_idxs], self.lat[lat_idxs])

    @property
    def nearest_zulu(self):
        if not hasattr(self, '_nearest_zulu'):
            self._nearest_zulu = nearest_zulu()
        return self._nearest_zulu

    @property
    def nearest_zulus(self):
        return np.arange(
            self.nearest_zulu,
            self.nearest_zulu - timedelta(days=2),
            -timedelta(days=1),
        ).astype(datetime)

    @property
    def output_interval(self):
        return timedelta(hours=1)

    @property
    def product(self):
        return 'hrrr'

    @property
    def lon(self):
        if not hasattr(self, '_lon'):
            #nc = self._files[list(self._files.keys())[0]]
            self._lon = self.nc.variables['lon'][:]
            if not hasattr(self, '_lat'):
                self._lat = self.nc.variables['lat'][:]
        return self._lon

    @property
    def lat(self):
        if not hasattr(self, '_lat'):
            #nc = self._files[list(self._files.keys())[0]]
            self._lat = self.nc.variables['lat'][:]
            if not hasattr(self, '_lon'):
                self._lon = self.nc.variables['lon'][:]
        return self._lat

    def _modified_bbox(self, bbox=None):
        if bbox is None:
            return Bbox.from_extents(
                np.min(self.lon),
                np.min(self.lat),
                np.max(self.lon),
                np.max(self.lat)
            )
        return bbox

    def _bbox_indexes(self, bbox):
        lat_idxs = np.where((self.lat >= bbox.ymin)
                            & (self.lat <= bbox.ymax))[0]
        lon_idxs = np.where((self.lon >= bbox.xmin)
                            & (self.lon <= bbox.xmax))[0]
        return lon_idxs, lat_idxs


class HRRR(SfluxDataset):

    def __init__(
            self,
            product: str = None,
    ):
        self.prmsl_name = 'pressfc'
        self.spfh_name = 'spfh2m'
        self.stmp_name = 'tmpsfc'
        self.uwind_name = 'ugrd10m'
        self.vwind_name = 'vgrd10m'
        self.prate_name = 'pratesfc'
        self.dlwrf_name = 'dlwrfsfc'
        self.dswrf_name = 'dswrfsfc'

    def fetch_data(
            self,
            start_date: datetime = None,
            rnday: Union[float, timedelta] = 1,
            air: bool = True,
            prc: bool = True,
            rad: bool = True,
            bbox: Bbox = None,
    ):
        """Fetches HRRR data from NOMADS server. """
        logger.info('Fetching HRRR data.')
        self.start_date = nearest_cycle() if start_date is None else \
            localize_datetime(start_date).astimezone(pytz.utc)
        self.rnday = rnday if isinstance(rnday, timedelta) else \
            timedelta(days=rnday)
        inventory = HRRRInventory(
            self.start_date,
            #self.rnday + self.output_interval,
            bbox
        )
        nx_grid, ny_grid = inventory.xy_grid()
        if air is True:
            with Dataset(
                self.tmpdir /
                f"air_{inventory.product}_"
                f"{str(self.start_date)}.nc",
                'w', format='NETCDF3_CLASSIC'
                    ) as dst:

                # global attributes
                dst.setncatts({"Conventions": "CF-1.0"})
                # dimensions
                dst.createDimension('nx_grid', nx_grid.shape[1])
                dst.createDimension('ny_grid', ny_grid.shape[0])
                dst.createDimension('time', None)
                # variables
                # lon
                dst.createVariable('lon', 'f4', ('ny_grid', 'nx_grid'))
                dst['lon'].long_name = "Longitude"
                dst['lon'].standard_name = "longitude"
                dst['lon'].units = "degrees_east"
                dst['lon'][:] = nx_grid
                # lat
                dst.createVariable('lat', 'f4', ('ny_grid', 'nx_grid'))
                dst['lat'].long_name = "Latitude"
                dst['lat'].standard_name = "latitude"
                dst['lat'].units = "degrees_north"
                dst['lat'][:] = ny_grid
                # time
                dst.createVariable('time', 'f4', ('time',))
                dst['time'].long_name = 'Time'
                dst['time'].standard_name = 'time'
                date = nearest_zulu(self.start_date)
                dst['time'].units = f'days since {date.year}-{date.month}'\
                                    f'-{date.day} 00:00'\
                                    f'{date.tzinfo}'
                dst['time'].base_date = (date.year, date.month, date.day, 0)
                dst['time'][:] = inventory.get_sflux_timevector()

                for var in AirComponent.var_types:
                    dst.createVariable(
                        var,
                        'f4',
                        ('time', 'ny_grid', 'nx_grid')
                    )
                    logger.info(f'Put field {var}')
                    inventory.put_sflux_field(getattr(self, f'{var}_name'), dst, var)

                # prmsl
                dst['prmsl'].long_name = "Pressure reduced to MSL"
                dst['prmsl'].standard_name = "air_pressure_at_sea_level"
                dst['prmsl'].units = "Pa"

                # spfh
                dst['spfh'].long_name = "Surface Specific Humidity "\
                                        "(2m AGL)"
                dst['spfh'].standard_name = "specific_humidity"
                dst['spfh'].units = "1"

                # stmp
                dst['stmp'].long_name = "Surface Air Temperature (2m AGL)"
                dst['stmp'].standard_name = "air_temperature"
                dst['stmp'].units = "K"

                # uwind
                dst['uwind'].long_name = "Surface Eastward Air Velocity "\
                    "(10m AGL)"
                dst['uwind'].standard_name = "eastward_wind"
                dst['uwind'].units = "m/s"

                # vwind
                dst['vwind'].long_name = "Surface Northward Air Velocity "\
                    "(10m AGL)"
                dst['vwind'].standard_name = "northward_wind"
                dst['vwind'].units = "m/s"

        if prc is True:
            with Dataset(
                self.tmpdir /
                f"prc_{inventory.product}_"
                f"{str(self.start_date)}.nc",
                'w', format='NETCDF3_CLASSIC'
                    ) as dst:

                # global attributes
                dst.setncatts({"Conventions": "CF-1.0"})
                # dimensions
                dst.createDimension('nx_grid', nx_grid.shape[1])
                dst.createDimension('ny_grid', ny_grid.shape[0])
                dst.createDimension('time', None)
                # lon
                dst.createVariable('lon', 'f4', ('ny_grid', 'nx_grid'))
                dst['lon'].long_name = "Longitude"
                dst['lon'].standard_name = "longitude"
                dst['lon'].units = "degrees_east"
                dst['lon'][:] = nx_grid
                # lat
                dst.createVariable('lat', 'f4', ('ny_grid', 'nx_grid'))
                dst['lat'].long_name = "Latitude"
                dst['lat'].standard_name = "latitude"
                dst['lat'].units = "degrees_north"
                dst['lat'][:] = ny_grid
                # time
                dst.createVariable('time', 'f4', ('time',))
                dst['time'].long_name = 'Time'
                dst['time'].standard_name = 'time'
                date = nearest_zulu(self.start_date)
                dst['time'].units = f'days since {date.year}-{date.month}'\
                                    f'-{date.day} 00:00'\
                                    f'{date.tzinfo}'
                dst['time'].base_date = (date.year, date.month, date.day, 0)
                dst['time'][:] = inventory.get_sflux_timevector()

                for var in PrcComponent.var_types:
                    dst.createVariable(var, float,
                                       ('time', 'ny_grid', 'nx_grid'))
                    logger.info(f'Put field {var}')
                    inventory.put_sflux_field(getattr(self, f'{var}_name'), dst, var)
                # prate
                dst['prate'].long_name = "Surface Precipitation Rate"
                dst['prate'].standard_name = "air_pressure_at_sea_level"
                dst['prate'].units = "kg/m^2/s"

        if rad is True:
            with Dataset(
                self.tmpdir /
                f"rad_{inventory.product}_"
                f"{str(self.start_date)}.nc",
                'w', format='NETCDF3_CLASSIC'
                    ) as dst:
                # global attributes
                dst.setncatts({"Conventions": "CF-1.0"})
                # dimensions
                dst.createDimension('nx_grid', nx_grid.shape[1])
                dst.createDimension('ny_grid', ny_grid.shape[0])
                dst.createDimension('time', None)
                # lon
                dst.createVariable('lon', 'f4', ('ny_grid', 'nx_grid'))
                dst['lon'].long_name = "Longitude"
                dst['lon'].standard_name = "longitude"
                dst['lon'].units = "degrees_east"
                dst['lon'][:] = nx_grid
                # lat
                dst.createVariable('lat', 'f4', ('ny_grid', 'nx_grid'))
                dst['lat'].long_name = "Latitude"
                dst['lat'].standard_name = "latitude"
                dst['lat'].units = "degrees_north"
                dst['lat'][:] = ny_grid
                # time
                dst.createVariable('time', 'f4', ('time',))
                dst['time'].long_name = 'Time'
                dst['time'].standard_name = 'time'
                date = nearest_zulu(self.start_date)
                dst['time'].units = f'days since {date.year}-{date.month}'\
                                    f'-{date.day} 00:00'\
                                    f'{date.tzinfo}'
                dst['time'].base_date = (date.year, date.month, date.day, 0)
                dst['time'][:] = inventory.get_sflux_timevector()

                for var in RadComponent.var_types:
                    dst.createVariable(var, float,
                                       ('time', 'ny_grid', 'nx_grid'))
                    logger.info(f'Put field {var}')
                    inventory.put_sflux_field(getattr(self, f'{var}_name'), dst, var)

                # dlwrf
                dst['dlwrf'].long_name = "Downward Long Wave Radiation "\
                                         "Flux"
                dst['dlwrf'].standard_name = "surface_downwelling_"\
                                             "longwave_flux_in_air"
                dst['dlwrf'].units = "W/m^2"

                # dswrf
                dst['dswrf'].long_name = "Downward Short Wave Radiation "\
                                         "Flux"
                dst['dswrf'].standard_name = "surface_downwelling_"\
                                             "shortwave_flux_in_air"
                dst['dswrf'].units = "W/m^2"

        self.resource = self.tmpdir
        if air is True:
            self.air = AirComponent(self.fields)
        if prc is True:
            self.prc = PrcComponent(self.fields)
        if rad is True:
            self.rad = RadComponent(self.fields)

    @property
    def tmpdir(self):
        if not hasattr(self, '_tmpdir'):
            self._tmpdir = tempfile.TemporaryDirectory()
        return pathlib.Path(self._tmpdir.name)

    @property
    def output_interval(self):
        return timedelta(hours=1)