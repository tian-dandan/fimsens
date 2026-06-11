"""
fimsense — Flood Inundation Mapping toolset.

Subpackages
-----------
fimsense.download  : Download SAR/optical/DEM/JRC imagery from Google Earth Engine
fimsense.raw       : Raw flood map generation (Lee filter, bimodal gamma fit, region grow)
fimsense.filter    : Commission-error filter for flood objects
fimsense.grow      : Flood growing and Local Water Surface Elevation (LWSE) estimation
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("fimsense")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = ["download", "raw", "filter", "grow"]
