"""
gee_download.py — Download GEE images to local GeoTIFF files.

Core tiling / threading / mosaicking logic is a standalone reimplementation
of download_tiles_new.directDownload, kept here so that importing this module
does NOT trigger the top-level ee.Authenticate() / ee.Initialize() calls that
live in download_tiles_new.py.

Usage
-----
    from fimsens.download import GEEDownloader, DEM_CATALOG
    dl = GEEDownloader(workspace=r"D:/output", extent_gcs="-91,33,-90,34")
    dl.download_image(image_id, sensor="S2", date="2020-02-15")
    dl.download_jrc()
    dl.download_dem("SRTM 30m")
"""
from __future__ import annotations

import contextlib
import os
import sys
import time
from queue import Queue
from threading import Thread
from typing import Dict, List, Optional

import numpy as np
import ee
from osgeo import gdal

from .gee_utils import get_utm_zone_and_crs, suppress_stderr


# ─── Band selection per sensor ─────────────────────────────────────────────────
SENSOR_BANDS: Dict[str, List[str]] = {
    "S1":      ["VV", "VH"],
    # S2: Blue Green Red NIR SWIR1 SWIR2
    "S2":      ["B2", "B3", "B4", "B8", "B11", "B12"],
    # Landsat C2 L2: Blue Green Red NIR SWIR1 SWIR2  (raw DN; apply ×2.75e-5 −0.2 for reflectance)
    "Landsat": ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"],
}

# ─── DEM catalogue (same as gee_search.DEM_CATALOG) ──────────────────────────
DEM_CATALOG: Dict[str, dict] = {
    "SRTM 30m":           {"id": "USGS/SRTMGL1_003",       "type": "image",      "band": "elevation", "scale": 30},
    "NASADEM 30m":        {"id": "NASA/NASADEM_HGT/001",    "type": "image",      "band": "elevation", "scale": 30},
    "ALOS AW3D30 30m":    {"id": "JAXA/ALOS/AW3D30/V3_2",  "type": "collection", "band": "DSM",       "scale": 30},
    "Copernicus GLO-30":  {"id": "COPERNICUS/DEM/GLO30",    "type": "collection", "band": "DEM",       "scale": 30},
    "USGS 3DEP 10m":      {"id": "USGS/3DEP/10m",          "type": "image",      "band": "elevation", "scale": 10},
    "USGS 3DEP 1m":       {"id": "USGS/3DEP/1m",           "type": "collection", "band": "elevation", "scale": 1},
}


# ═══════════════════════════════════════════════════════════════════════════════
#  CRS helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _embed_crs(tif_path: str, epsg_code: str, esri_format: bool = False) -> None:
    """
    Embed a CRS into an existing GeoTIFF and, when esri_format=True, also
    write a .prj sidecar in ESRI WKT so ArcGIS Pro can read the spatial ref.

    ee.data.computePixels tiles often carry no CRS header; this call fixes
    each tile before mosaicking, and is called again on the final mosaic
    (esri_format=True) to guarantee ArcGIS compatibility.
    """
    from osgeo import osr
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(epsg_code.split(":")[-1]))
    if esri_format:
        srs.MorphToESRI()   # converts PROJ WKT → ESRI WKT dialect
    wkt = srs.ExportToWkt()

    ds = gdal.Open(tif_path, gdal.GA_Update)
    if ds is not None:
        ds.SetProjection(wkt)
        ds.FlushCache()
        ds = None

    if esri_format:
        prj_path = os.path.splitext(tif_path)[0] + ".prj"
        with open(prj_path, "w") as f:
            f.write(wkt)


# ═══════════════════════════════════════════════════════════════════════════════
#  Low-level engine  (tile → ee.data.computePixels → gdal.Warp mosaic)
# ═══════════════════════════════════════════════════════════════════════════════

def _split_image(x1, y1, x2, y2, width, height, block_size):
    """Divide the image footprint into sub-blocks for very large downloads."""
    nx = int(np.ceil(width  / block_size))
    ny = int(np.ceil(height / block_size))
    dx = (x2 - x1) / nx
    dy = (y2 - y1) / ny
    boxes = []
    for ix in range(nx):
        for iy in range(ny):
            xmin = x1 + ix * dx
            xmax = xmin + dx
            ymin = y1 + iy * dy
            ymax = ymin + dy
            boxes.append(((ix, iy), ee.Geometry.Rectangle([xmin, ymin, xmax, ymax])))
    return boxes


def _tile_worker(thread_id, workspace, transform, proj_crs, geeimg, block_id, q, out_q):
    """Thread worker: pull tile specs from q, call computePixels, write .tif."""
    while True:
        elem = q.get()
        if elem is None:
            q.task_done()
            break
        t_idx, tile = elem
        tdim  = tile[1] - tile[0], tile[3] - tile[2]
        tilex = transform[2] + tile[0] * transform[0]
        tiley = transform[5] + tile[2] * transform[4]
        grid  = {
            "dimensions": {"width": tdim[0], "height": tdim[1]},
            "affineTransform": {
                "scaleX": transform[0], "shearX": 0, "translateX": tilex,
                "shearY": 0, "scaleY": transform[4], "translateY": tiley,
            },
            "crsCode": proj_crs,
        }
        request = {"expression": geeimg, "fileFormat": "GEO_TIFF", "grid": grid}
        data    = ee.data.computePixels(request)

        fname = f"_{block_id}_t{thread_id}_{t_idx}_{int(time.time())}.tif"
        fpath = os.path.join(workspace, fname)
        with open(fpath, "wb") as f:
            f.write(data)
        # computePixels tiles often have no CRS in the header — embed it now
        # so gdal.Warp can mosaic them correctly.
        _embed_crs(fpath, proj_crs, esri_format=False)
        out_q.put(fpath)
        q.task_done()


def direct_download(
    workspace: str,
    extent_gcs: str,
    geeimg: ee.Image,
    polygon_id: str,
    target_scale: int = 10,
    tile_size: int = 1000,
    n_threads: int = 10,
    block_threshold: int = 10_000,
) -> str:
    """
    Download a GEE image to a single mosaicked GeoTIFF.

    Output file: <workspace>/Case<polygon_id>.tif
    (For split images: Case<polygon_id>_p0_0.tif, Case<polygon_id>_p0_1.tif …)

    Parameters
    ----------
    workspace        : output directory (created if absent)
    extent_gcs       : "xmin,ymin,xmax,ymax" in WGS-84
    geeimg           : EE image to download
    polygon_id       : identifier used for output file naming
    target_scale     : resolution in metres
    tile_size        : download tile side in pixels
    n_threads        : concurrent download threads
    block_threshold  : pixel threshold above which the image is split into blocks
    """
    from pyproj import Proj

    os.makedirs(workspace, exist_ok=True)
    q     = Queue()
    out_q = Queue()

    x1, y1, x2, y2 = map(float, extent_gcs.split(","))
    lon, lat = ee.Geometry.BBox(x1, y1, x2, y2).centroid(maxError=1).coordinates().getInfo()
    crs_code = get_utm_zone_and_crs(lat, lon)

    utm_prj  = ee.Projection(crs_code).atScale(target_scale)
    geeimg   = geeimg.reproject(utm_prj)
    proj      = geeimg.projection().getInfo()
    transform = proj["transform"]
    # Always use the clean EPSG code (e.g. "EPSG:32615") rather than whatever
    # string GEE embeds in proj["crs"] / proj["wkt"].  Some GEE projections return
    # a PROJ WKT or AUTHORITY string that GDAL accepts but ArcGIS Pro cannot
    # resolve, causing "Data source information unavailable".
    proj_crs  = crs_code   # e.g. "EPSG:32615"

    wp       = Proj(crs_code)
    ub       = np.array(wp([x1, x2, x2, x1], [y1, y1, y2, y2]))
    ub      -= ub % 60
    west,  east  = ub[0].min(), ub[0].max()
    south, north = ub[1].min(), ub[1].max()

    xpr = (np.array([west, east])   - transform[2]) / transform[0]
    ypr = (np.array([south, north]) - transform[5]) / transform[4]
    rdim    = int(xpr[1] - xpr[0]), int(ypr[1] - ypr[0])
    porigin = xpr[0], ypr[0]

    # ── Inner helper: one block ────────────────────────────────────────────────
    def _download_block(block_id, _rdim, _porigin, _transform, outname):
        tg = int(np.ceil(_rdim[0] / tile_size)), int(np.ceil(_rdim[1] / tile_size))
        ptiles = []
        for ti in range(tg[0]):
            for tj in range(tg[1]):
                ti0 = max(_porigin[0], _porigin[0] + ti * tile_size)
                ti1 = min(_porigin[0] + (ti + 1) * tile_size, _porigin[0] + _rdim[0])
                tj0 = max(_porigin[1], _porigin[1] + tj * tile_size)
                tj1 = min(_porigin[1] + (tj + 1) * tile_size, _porigin[1] + _rdim[1])
                ptiles.append([ti0, ti1, tj0, tj1])

        print(f"  Downloading {len(ptiles)} tiles for {block_id} using {n_threads} threads …")
        for idx, tile in enumerate(ptiles):
            q.put((idx, tile))
        for _ in range(n_threads):
            q.put(None)

        threads = [
            Thread(
                target=_tile_worker,
                args=(tid, workspace, _transform, proj_crs, geeimg, block_id, q, out_q),
                daemon=True,
            )
            for tid in range(n_threads)
        ]
        for t in threads:
            t.start()
        q.join()

        files = []
        while not out_q.empty():
            files.append(out_q.get())
        files = sorted(f for f in files if os.path.exists(f))

        if not files:
            print(f"  [WARNING] No valid tiles for {block_id} – skipping mosaic.")
            return

        out_path = os.path.join(workspace, outname)
        opts = gdal.WarpOptions(
            format="GTiff",
            multithread=True,
            resampleAlg="nearest",
            srcSRS=crs_code,   # tell Warp what CRS the (already-fixed) tiles are in
            dstSRS=crs_code,   # write the same CRS to the output
        )
        with suppress_stderr():
            gdal.Warp(out_path, files, options=opts)

        # Re-embed CRS in ESRI WKT format + write .prj sidecar.
        _embed_crs(out_path, crs_code, esri_format=True)

        print(f"  ✓ Mosaic saved → {out_path}")
        for f in files:
            try:
                os.remove(f)
            except OSError:
                pass

    # ── Dispatch ──────────────────────────────────────────────────────────────
    if max(rdim) <= block_threshold:
        _download_block(polygon_id, rdim, porigin, transform, f"Case{polygon_id}.tif")
    else:
        print(f"  Image size {rdim} > block_threshold {block_threshold} → splitting …")
        boxes = _split_image(x1, y1, x2, y2, rdim[0], rdim[1], block_threshold)
        print(f"  → {len(boxes)} blocks")
        for (ix, iy), geom in boxes:
            sub_id = f"{polygon_id}_p{ix}_{iy}"
            coords = geom.bounds().coordinates().getInfo()[0]
            xs = [p[0] for p in coords]
            ys = [p[1] for p in coords]
            sx1, sx2 = min(xs), max(xs)
            sy1, sy2 = min(ys), max(ys)
            sb = np.array(wp([sx1, sx2, sx2, sx1], [sy1, sy1, sy2, sy2]))
            sb -= sb % 60
            xpr_ = (np.array([sb[0].min(), sb[0].max()]) - transform[2]) / transform[0]
            ypr_ = (np.array([sb[1].min(), sb[1].max()]) - transform[5]) / transform[4]
            sub_rdim    = int(xpr_[1] - xpr_[0]), int(ypr_[1] - ypr_[0])
            sub_porigin = xpr_[0], ypr_[0]
            _download_block(sub_id, sub_rdim, sub_porigin, transform, f"Case{sub_id}.tif")

    return polygon_id


# ═══════════════════════════════════════════════════════════════════════════════
#  High-level downloader class
# ═══════════════════════════════════════════════════════════════════════════════

class GEEDownloader:
    """
    Download GEE satellite images and auxiliary datasets to local GeoTIFFs.

    Parameters
    ----------
    workspace       : str   Output directory (created if absent)
    extent_gcs      : str   "xmin,ymin,xmax,ymax" in WGS-84
    target_scale    : int   Default resolution in metres          (default 10)
    tile_size       : int   Download tile side in pixels          (default 1000)
    inner_threads   : int   Concurrent download threads           (default 10)
    block_threshold : int   Pixel threshold for block-splitting   (default 10 000)
    """

    def __init__(
        self,
        workspace: str,
        extent_gcs: str,
        target_scale: int = 10,
        tile_size: int = 1000,
        inner_threads: int = 10,
        block_threshold: int = 10_000,
    ):
        self.workspace       = workspace
        self.extent_gcs      = extent_gcs
        self.target_scale    = target_scale
        self.tile_size       = tile_size
        self.inner_threads   = inner_threads
        self.block_threshold = block_threshold
        os.makedirs(workspace, exist_ok=True)

    # ── Single image ──────────────────────────────────────────────────────────

    def download_image(
        self,
        image_id: str,
        sensor: str,
        date: str,
        scale: Optional[int] = None,
    ) -> str:
        """
        Download a GEE image by its full asset ID.

        Parameters
        ----------
        image_id : str   Full GEE image ID (e.g. "COPERNICUS/S1_GRD/…")
        sensor   : str   "S1" | "S2" | "Landsat"
        date     : str   'YYYY-MM-DD'  (used in output filename)
        scale    : int   Override resolution (metres); defaults to self.target_scale

        Returns
        -------
        str  polygon_id used for output file naming
        """
        if sensor not in SENSOR_BANDS:
            raise ValueError(f"Unknown sensor '{sensor}'. Use: {list(SENSOR_BANDS)}")

        bands = SENSOR_BANDS[sensor]
        img   = ee.Image(image_id).select(bands)

        if sensor == "S2":
            img = img.multiply(0.0001).clamp(0.0, 1.0).toFloat()
            units = "surface reflectance [0–1]"
        elif sensor == "Landsat":
            img = img.multiply(0.0000275).add(-0.2).clamp(0.0, 1.0).toFloat()
            units = "surface reflectance [0–1]"
        else:
            img = img.toFloat()
            units = "backscatter dB"

        res = scale or self.target_scale
        pid = f"{sensor}_{date.replace('-','')}_{image_id.split('/')[-1][:18]}"

        print(f"\n{'─'*60}")
        print(f"  Sensor : {sensor}")
        print(f"  Date   : {date}")
        print(f"  Bands  : {bands}")
        print(f"  Units  : {units}")
        print(f"  Scale  : {res} m")
        print(f"  ID     : {image_id}")

        direct_download(
            workspace=self.workspace,
            extent_gcs=self.extent_gcs,
            geeimg=img,
            polygon_id=pid,
            target_scale=res,
            tile_size=self.tile_size,
            n_threads=self.inner_threads,
            block_threshold=self.block_threshold,
        )
        return pid

    # ── Batch ─────────────────────────────────────────────────────────────────

    def download_images(
        self,
        records: List[Dict],
        scale_map: Optional[Dict[str, int]] = None,
    ) -> List[str]:
        """
        Download a list of image records returned by GEESearcher.

        Parameters
        ----------
        records   : list of dicts with 'image_id', 'sensor', 'date'
        scale_map : optional per-sensor resolution override
                    e.g. {"S1": 10, "S2": 10, "Landsat": 30}
        """
        sm   = scale_map or {}
        pids = []
        for rec in records:
            pid = self.download_image(
                image_id=rec["image_id"],
                sensor=rec["sensor"],
                date=rec["date"],
                scale=sm.get(rec["sensor"], self.target_scale),
            )
            pids.append(pid)
        return pids

    # ── JRC ───────────────────────────────────────────────────────────────────

    def download_jrc(self, scale: int = 30) -> str:
        """Download JRC Global Surface Water occurrence layer."""
        jrc = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").toFloat()
        pid = "JRC_water_occurrence"
        print(f"\n{'─'*60}")
        print("  Downloading JRC Global Surface Water Occurrence …")
        direct_download(
            workspace=self.workspace,
            extent_gcs=self.extent_gcs,
            geeimg=jrc,
            polygon_id=pid,
            target_scale=scale,
            tile_size=self.tile_size,
            n_threads=self.inner_threads,
            block_threshold=self.block_threshold,
        )
        return pid

    # ── DEM ───────────────────────────────────────────────────────────────────

    def download_dem(
        self,
        dem_name: str = "SRTM 30m",
        ee_geometry: Optional[ee.Geometry] = None,
    ) -> str:
        """
        Download a DEM for the configured AOI.

        Parameters
        ----------
        dem_name    : str  Key from DEM_CATALOG (shown in notebook dropdown)
        ee_geometry : ee.Geometry  Used to filter collection-based DEMs
        """
        info = DEM_CATALOG.get(dem_name)
        if info is None:
            raise KeyError(f"Unknown DEM '{dem_name}'. Options: {list(DEM_CATALOG)}")

        if info["type"] == "image":
            img = ee.Image(info["id"]).select(info["band"]).toFloat()
        else:
            col = ee.ImageCollection(info["id"])
            if ee_geometry is not None:
                col = col.filterBounds(ee_geometry)
            img = col.select(info["band"]).mosaic().toFloat()

        pid = f"DEM_{dem_name.replace(' ', '_')}"
        print(f"\n{'─'*60}")
        print(f"  Downloading DEM: {dem_name} @ {info['scale']} m …")
        direct_download(
            workspace=self.workspace,
            extent_gcs=self.extent_gcs,
            geeimg=img,
            polygon_id=pid,
            target_scale=info["scale"],
            tile_size=self.tile_size,
            n_threads=self.inner_threads,
            block_threshold=self.block_threshold,
        )
        return pid
