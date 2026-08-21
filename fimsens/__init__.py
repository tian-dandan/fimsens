"""
fimsens — Flood Inundation Mapping toolset.

Subpackages
-----------
fimsens.download  : Download SAR/optical/DEM/JRC imagery from Google Earth Engine
fimsens.raw       : Raw flood map generation (Lee filter, bimodal gamma fit, region grow)
fimsens.filter    : Commission-error filter for flood objects
fimsens.grow      : Flood growing and Local Water Surface Elevation (LWSE) estimation
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("fimsens")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = ["download", "raw", "filter", "grow"]
