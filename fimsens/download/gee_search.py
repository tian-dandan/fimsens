"""
gee_search.py — Search Sentinel-1, Sentinel-2, Landsat, JRC, and DEM on GEE.

Usage
-----
    from fimsens.download import GEESearcher, DEM_CATALOG
    searcher = GEESearcher()
    results  = searcher.search_all([xmin, ymin, xmax, ymax], "2020-01-01", "2020-03-01")
    df       = GEESearcher.to_dataframe(results)
"""
from __future__ import annotations

from typing import Dict, List, Optional

import ee
import pandas as pd

from .gee_utils import parse_aoi, format_timestamp


# ─── Collection IDs ────────────────────────────────────────────────────────────
_S1  = "COPERNICUS/S1_GRD"
_S2  = "COPERNICUS/S2_SR_HARMONIZED"
_L8  = "LANDSAT/LC08/C02/T1_L2"
_L9  = "LANDSAT/LC09/C02/T1_L2"
_JRC = "JRC/GSW1_4/GlobalSurfaceWater"


# ─── DEM catalogue ─────────────────────────────────────────────────────────────
DEM_CATALOG: Dict[str, dict] = {
    "SRTM 30m":           {"id": "USGS/SRTMGL1_003",       "type": "image",      "band": "elevation", "scale": 30},
    "NASADEM 30m":        {"id": "NASA/NASADEM_HGT/001",    "type": "image",      "band": "elevation", "scale": 30},
    "ALOS AW3D30 30m":    {"id": "JAXA/ALOS/AW3D30/V3_2",  "type": "collection", "band": "DSM",       "scale": 30},
    "Copernicus GLO-30":  {"id": "COPERNICUS/DEM/GLO30",    "type": "collection", "band": "DEM",       "scale": 30},
    "USGS 3DEP 10m":      {"id": "USGS/3DEP/10m",          "type": "image",      "band": "elevation", "scale": 10},
    "USGS 3DEP 1m":       {"id": "USGS/3DEP/1m",           "type": "collection", "band": "elevation", "scale": 1},
}


class GEESearcher:
    """Query GEE collections and return structured metadata lists / DataFrames."""

    # ── Sentinel-1 ─────────────────────────────────────────────────────────────
    def search_sentinel1(
        self,
        ee_geom: ee.Geometry,
        start: str,
        end: str,
    ) -> List[Dict]:
        """Search Sentinel-1 IW GRD images with VV + VH polarisation."""
        col = (
            ee.ImageCollection(_S1)
            .filterDate(start, end)
            .filterBounds(ee_geom)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
            .sort("system:time_start")
        )
        return self._unpack(
            col, sensor="S1",
            extra=["orbitProperties_pass", "relativeOrbitNumber_start", "platform_number"],
        )

    # ── Sentinel-2 ─────────────────────────────────────────────────────────────
    def search_sentinel2(
        self,
        ee_geom: ee.Geometry,
        start: str,
        end: str,
        max_cloud: float = 30,
    ) -> List[Dict]:
        """Search Sentinel-2 SR Harmonised images (COPERNICUS/S2_SR_HARMONIZED)."""
        col = (
            ee.ImageCollection(_S2)
            .filterDate(start, end)
            .filterBounds(ee_geom)
            .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", max_cloud))
            .sort("system:time_start")
        )
        return self._unpack(
            col, sensor="S2",
            extra=["CLOUDY_PIXEL_PERCENTAGE", "MGRS_TILE"],
        )

    # ── Landsat 8 / 9 ──────────────────────────────────────────────────────────
    def search_landsat(
        self,
        ee_geom: ee.Geometry,
        start: str,
        end: str,
        max_cloud: float = 30,
    ) -> List[Dict]:
        """Search Landsat 8 and 9 Collection-2 Tier-1 Level-2 images."""
        l8 = (
            ee.ImageCollection(_L8)
            .filterDate(start, end)
            .filterBounds(ee_geom)
            .filter(ee.Filter.lte("CLOUD_COVER", max_cloud))
        )
        l9 = (
            ee.ImageCollection(_L9)
            .filterDate(start, end)
            .filterBounds(ee_geom)
            .filter(ee.Filter.lte("CLOUD_COVER", max_cloud))
        )
        col = l8.merge(l9).sort("system:time_start")
        return self._unpack(
            col, sensor="Landsat",
            extra=["SPACECRAFT_ID", "CLOUD_COVER", "WRS_PATH", "WRS_ROW"],
        )

    # ── Combined ───────────────────────────────────────────────────────────────
    def search_all(
        self,
        aoi_input,
        start: str,
        end: str,
        max_cloud: float = 30,
    ) -> Dict:
        """
        Search S1, S2, and Landsat for the given AOI and date window.

        Parameters
        ----------
        aoi_input : list[float] | str | GeoDataFrame
            BBox [xmin, ymin, xmax, ymax],  "xmin,ymin,xmax,ymax",  or file path.
        start, end : str
            Date strings 'YYYY-MM-DD'.
        max_cloud : float
            Maximum cloud cover percentage for optical sensors.

        Returns
        -------
        dict with keys:
            's1', 's2', 'landsat'  – lists of image record dicts
            'ee_geometry'          – ee.Geometry.BBox
            'extent_gcs'           – "xmin,ymin,xmax,ymax"
        """
        ee_geom, extent_gcs = parse_aoi(aoi_input)

        print(f"Searching Sentinel-1  ({start} → {end}) …")
        s1 = self.search_sentinel1(ee_geom, start, end)
        print(f"  → {len(s1)} scenes found")

        print(f"Searching Sentinel-2  (cloud ≤ {max_cloud}%) …")
        s2 = self.search_sentinel2(ee_geom, start, end, max_cloud)
        print(f"  → {len(s2)} scenes found")

        print(f"Searching Landsat 8/9 (cloud ≤ {max_cloud}%) …")
        landsat = self.search_landsat(ee_geom, start, end, max_cloud)
        print(f"  → {len(landsat)} scenes found")

        total = len(s1) + len(s2) + len(landsat)
        print(f"\nTotal: {total} scenes")

        return dict(
            s1=s1, s2=s2, landsat=landsat,
            ee_geometry=ee_geom,
            extent_gcs=extent_gcs,
        )

    # ── Internal unpacker ──────────────────────────────────────────────────────
    @staticmethod
    def _unpack(
        col: ee.ImageCollection,
        sensor: str,
        extra: List[str],
    ) -> List[Dict]:
        """
        Fetch image metadata from EE using batch aggregate_array calls.

        Uses 'system:id' (full asset path, e.g. 'COPERNICUS/S1_GRD/S1A_IW_...')
        rather than img.id() which returns only the short scene ID and causes
        ee.Image(id) lookups to fail.
        """
        n = col.size().getInfo()
        if n == 0:
            return []

        # Batch-fetch IDs and timestamps in two server calls
        ids     = col.aggregate_array("system:id").getInfo()
        ts_list = col.aggregate_array("system:time_start").getInfo()

        # Batch-fetch each extra property (one call per property)
        extra_data: Dict[str, list] = {}
        for k in extra:
            try:
                extra_data[k] = col.aggregate_array(k).getInfo()
            except Exception:
                extra_data[k] = [None] * n

        records = []
        for i in range(n):
            rec = {
                "sensor":   sensor,
                "image_id": ids[i],                         # full path ✓
                "date":     format_timestamp(ts_list[i] or 0),
                "_ts":      ts_list[i] or 0,
            }
            for k in extra:
                vals = extra_data.get(k, [])
                rec[k] = vals[i] if i < len(vals) else None
            records.append(rec)
        return records

    # ── DataFrame helper ───────────────────────────────────────────────────────
    @staticmethod
    def to_dataframe(results: Dict) -> pd.DataFrame:
        """Flatten search results dict into a single sorted DataFrame."""
        rows = (
            results.get("s1",      []) +
            results.get("s2",      []) +
            results.get("landsat", [])
        )
        if not rows:
            return pd.DataFrame()
        df = (
            pd.DataFrame(rows)
            .sort_values("_ts")
            .drop(columns=["_ts"])
            .reset_index(drop=True)
        )
        df.index.name = "idx"
        return df

    # ── DEM / JRC helpers ──────────────────────────────────────────────────────
    @staticmethod
    def get_jrc() -> ee.Image:
        """Return JRC Global Surface Water occurrence band."""
        return ee.Image(_JRC).select("occurrence")

    @staticmethod
    def get_dem(dem_name: str, ee_geom: Optional[ee.Geometry] = None) -> ee.Image:
        """
        Return a DEM ee.Image for the given source name.

        Parameters
        ----------
        dem_name : str
            One of the keys in DEM_CATALOG.
        ee_geom : ee.Geometry, optional
            Used to filter collection-based DEMs.
        """
        if dem_name not in DEM_CATALOG:
            raise KeyError(f"Unknown DEM '{dem_name}'. Options: {list(DEM_CATALOG)}")
        info = DEM_CATALOG[dem_name]
        if info["type"] == "image":
            return ee.Image(info["id"]).select(info["band"])
        col = ee.ImageCollection(info["id"])
        if ee_geom is not None:
            col = col.filterBounds(ee_geom)
        return col.select(info["band"]).mosaic()
