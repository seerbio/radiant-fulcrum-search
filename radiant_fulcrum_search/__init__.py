"""
``radiant_fulcrum_search`z

Exports:

- `run_radiant_search`
"""

# Initialize the package.
try:
    from importlib.metadata import version, PackageNotFoundError

    try:
        __version__ = version("radiant-fulcrum-search")
    except PackageNotFoundError:
        pass

except ImportError:
    from pkg_resources import get_distribution, DistributionNotFound

    try:
        __version__ = get_distribution("radiant-fulcrum-search").version
    except DistributionNotFound:
        pass

# Here is where we can export public functions and classes.
from .search import run_radiant_search
