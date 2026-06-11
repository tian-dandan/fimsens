"""
fix_arcgis_crs.py — Repair spatial reference in already-downloaded GeoTIFFs
so that ArcGIS Pro shows the correct CRS instead of
"Data source information unavailable".

Usage
-----
Run this script once for each output folder that contains downloaded .tif files.
It reads the EPSG code from the first .tif it finds that has any projection info,
or you can hard-code UTM_EPSG below if the files have NO spatial reference at all.

    python fix_arcgis_crs.py

Edit WORKSPACE and UTM_EPSG before running.
"""
import os
import glob
from osgeo import gdal, osr

# ── EDIT THESE ────────────────────────────────────────────────────────────────
WORKSPACE = r"D:/output/gee_downloads"   # folder with your .tif files
UTM_EPSG  = 32615                        # EPSG of the UTM zone (e.g. 32615 = UTM-15N)
# ─────────────────────────────────────────────────────────────────────────────


def fix_tif(tif_path: str, epsg: int) -> None:
    """Embed ESRI-format WKT into a GeoTIFF and write a .prj sidecar."""
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    srs.MorphToESRI()           # ESRI WKT dialect — required by ArcGIS Pro
    esri_wkt = srs.ExportToWkt()

    ds = gdal.Open(tif_path, gdal.GA_Update)
    if ds is None:
        print(f"  [SKIP] Cannot open: {tif_path}")
        return
    ds.SetProjection(esri_wkt)
    ds.FlushCache()
    ds = None

    prj_path = os.path.splitext(tif_path)[0] + ".prj"
    with open(prj_path, "w") as f:
        f.write(esri_wkt)

    print(f"  ✓ Fixed: {os.path.basename(tif_path)}")


def main():
    tifs = glob.glob(os.path.join(WORKSPACE, "**", "Case*.tif"), recursive=True)
    if not tifs:
        print(f"No Case*.tif files found in {WORKSPACE}")
        return

    print(f"Found {len(tifs)} file(s).  Assigning EPSG:{UTM_EPSG} …\n")
    for tif in sorted(tifs):
        fix_tif(tif, UTM_EPSG)

    print(f"\nDone.  Reload the layers in ArcGIS Pro — spatial reference should now show correctly.")


if __name__ == "__main__":
    main()
