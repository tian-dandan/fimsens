"""
fimsense.raw — Raw flood map generation from SAR or optical imagery.

Pipeline
--------
1. imagePrep    : mosaic inputs, clip to valid extent, align JRC water mask
2. lee_filter   : Lee / Enhanced Lee speckle filter for SAR
3. region_split : adaptive tile splitting to find bimodal regions
4. multigamma   : bimodal gamma distribution fit → per-tile threshold points
5. filter       : statistical outlier removal from threshold points
6. rgrow        : region-grow flood map from JRC permanent-water seeds
7. postprocess  : morphological clean-up of binary flood map
8. idw2flood    : IDW interpolation of threshold surface → flood map

Public functions
----------------
imagePrep, leeFilt, region_split, bimodalFit, filterOutliers,
regionGrow, postProcess, interpFlood
"""

from .imagePrep import imagePrep
from .lee_filter import leeFilt
from .region_split import region_split
from .multigamma import bimodalFit
from .filter import filterOutliers
from .rgrow import regionGrow
from .postprocess import postProcess
from .idw2flood import interpFlood

__all__ = [
    "imagePrep",
    "leeFilt",
    "region_split",
    "bimodalFit",
    "filterOutliers",
    "regionGrow",
    "postProcess",
    "interpFlood",
]
