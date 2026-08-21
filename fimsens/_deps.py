"""
fimsens._deps — turn a bare ModuleNotFoundError into an actionable message.

Several fimsens subpackages import heavy third-party libraries at module level.
When one is missing, Python's default message is a bare::

    ModuleNotFoundError: No module named 'osgeo'

which does not tell the user that 'osgeo' means GDAL, that GDAL cannot be
installed with pip, or which conda command fixes it. :func:`helpful_import`
wraps a subpackage's imports and re-raises with that information attached.

Usage, in a subpackage ``__init__.py``::

    from .._deps import helpful_import

    with helpful_import("raw"):
        from .imagePrep import imagePrep
        from .lee_filter import leeFilt

Only the message changes — the exception is still an ImportError, still raised
at the same point, and the original is preserved as ``__cause__``.
"""

from contextlib import contextmanager

__all__ = ["helpful_import", "GDAL_HINT"]


GDAL_HINT = (
    "GDAL (the 'osgeo' module) is not installed.\n"
    "\n"
    "GDAL cannot be installed with pip alone: PyPI ships no standalone binary\n"
    "wheel, so 'pip install gdal' tries to compile against a system libgdal that\n"
    "must already be present. Use conda instead:\n"
    "\n"
    "    conda install -c conda-forge gdal\n"
    "\n"
    "See the project README for the full recommended environment setup."
)

_GEE_HINT = (
    "The Google Earth Engine extras are not installed.\n"
    "\n"
    "    pip install fimsens[gee]\n"
    "\n"
    "This installs earthengine-api, geemap, ipywidgets and ipyleaflet.\n"
    "You also need a Google Earth Engine account: run ee.Authenticate() once,\n"
    "then ee.Initialize(project='your-project-id') at the start of each session."
)

_VIZ_HINT = (
    "The plotting extras are not installed.\n"
    "\n"
    "    pip install fimsens[viz]"
)

_CORE_HINT = (
    "A core dependency of fimsens is missing, which usually means the package\n"
    "was installed incompletely or into a different environment than the one\n"
    "you are running.\n"
    "\n"
    "    pip install --force-reinstall fimsens\n"
    "\n"
    "If you are working from a source checkout:\n"
    "\n"
    "    pip install -e ."
)

#: Missing top-level module name -> what the user should actually do about it.
_HINTS = {
    "osgeo": GDAL_HINT,
    "ee": _GEE_HINT,
    "geemap": _GEE_HINT,
    "ipywidgets": _GEE_HINT,
    "ipyleaflet": _GEE_HINT,
    "matplotlib": _VIZ_HINT,
    "rasterio": _CORE_HINT,
    "geopandas": _CORE_HINT,
    "shapely": _CORE_HINT,
    "skimage": _CORE_HINT,
    "sklearn": _CORE_HINT,
    "scipy": _CORE_HINT,
    "numpy": _CORE_HINT,
    "pandas": _CORE_HINT,
    "pyproj": _CORE_HINT,
    "cv2": _CORE_HINT,
    "tqdm": _CORE_HINT,
}


def hint_for(module_name):
    """
    Return the installation hint for a missing module, or None if unknown.

    Parameters
    ----------
    module_name : str
        Module name as reported by ImportError, e.g. ``'osgeo'`` or
        ``'osgeo.gdal'``. Only the top-level part is matched.
    """
    if not module_name:
        return None
    return _HINTS.get(module_name.split(".")[0])


@contextmanager
def helpful_import(subpackage):
    """
    Re-raise ImportError from the wrapped block with an installation hint.

    Parameters
    ----------
    subpackage : str
        Name used in the message, e.g. ``'raw'`` for ``fimsens.raw``.

    Raises
    ------
    ImportError
        With a hint appended when the missing module is one fimsens knows
        about. Unrecognised modules are re-raised untouched, so genuine bugs
        are never disguised as a missing dependency.
    """
    try:
        yield
    except ImportError as exc:
        hint = hint_for(getattr(exc, "name", None))
        if hint is None:
            raise
        raise ImportError(
            f"fimsens.{subpackage} could not be imported.\n\n{hint}\n\n"
            f"(original error: {exc})"
        ) from exc
