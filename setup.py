#!/usr/bin/env python
import pathlib
import setuptools  # type: ignore[import]

try:
    from dunamai import Version
except ImportError:
    import sys
    import subprocess

    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'dunamai'])
    from dunamai import Version  # type: ignore[import]


parent = pathlib.Path(__file__).parent.absolute()
conf = setuptools.config.read_configuration(parent / 'setup.cfg')
meta = conf['metadata']
setuptools.setup(
    name=meta['name'],
    version=Version.from_any_vcs().serialize(),
    author=meta['author'],
    author_email=meta['author_email'],
    description=meta['description'],
    long_description=meta['long_description'],
    long_description_content_type="text/markdown",
    url=meta['url'],
    packages=setuptools.find_packages(),
    python_requires='>=3.6',
    setup_requires=['setuptools_scm', 'setuptools>=41.2',
                    'netcdf-flattener>=1.2.0'],
    include_package_data=True,
    extras_require={'dev': ['coverage', 'flake8', 'nose']},
    install_requires=[
        'matplotlib',
        'netCDF4',
        'pyproj',
        'shapely',
        'fiona',
        'f90nml',
        'psutil',
        'scipy',
        'wget',
        'appdirs',
        'cf-python',
        'sqlalchemy',
        'geopandas',
        'pyugrid',
        'pytz',
    ],
    entry_points={'console_scripts': ['pyschism = pyschism.__main__:main']},
    tests_require=['nose'],
    test_suite='nose.collector',
)
