"""
`scry_pythia_search`

Exports:

- `run_pythia_search`
"""

# Initialize the package.
try:
    from importlib.metadata import version, PackageNotFoundError

    try:
        __version__ = version("scry-pythia-search")
    except PackageNotFoundError:
        pass

except ImportError:
    from pkg_resources import get_distribution, DistributionNotFound

    try:
        __version__ = get_distribution("scry-pythia-search").version
    except DistributionNotFound:
        pass

# Here is where we can export public functions and classes.
# from .package import Symbol  # import relative to this package to avoid namespace collisions
from .search import run_pythia_search
