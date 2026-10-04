"""EDA world state; independent of any agent runtime."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("eda-harness")
except PackageNotFoundError:
    # Source-only imports have no release identity; never advertise an obsolete release.
    __version__ = "0+unknown"
