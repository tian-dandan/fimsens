"""
object_filter.py
----------------
Rule-based commission-error filter for flood objects.

The ``ObjectFilter`` class applies sequential rules to a parameter DataFrame,
filling a ``predict`` column.  Rules are designed to be applied in order;
each rule only acts on objects not yet classified (predict = NaN).

Predict values
--------------
  NaN  – not yet classified
  0    – false positive: too small (Rule 0)
  1    – true flood: large confirmed object (Rule 1)
  2    – depression-stored water, kept (Rule 2)
  4    – false positive: LWSE too high (Rule 3)
  5    – tentative true flood: LWSE too low (Rule 3)
  6    – false positive: too far from confirmed flood (Rule 4)
  7    – true flood: proximate to confirmed flood (Rule 4)

Usage example
-------------
    filt = ObjectFilter(params_df)
    filt.rule0_remove_small(threshold_px=50)
        .rule1_identify_large()
        .rule2_depression_water()
        .rule3_filter_by_delta_lwse(reference_df)
        .rule4_filter_by_distance(distance_threshold=1300)
    results = filt.get_results()
    filt.validate()
    filt.save_results(output_dir)
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import geopandas as gpd
from scipy.spatial.distance import cdist
from shapely.geometry import Point
from sklearn.cluster import KMeans


#: Explicit shapefile names for columns that get divided by 1e6 in
#: :meth:`ObjectFilter.save_results`.  Both source columns are volumes in m3
#: (pixel count x depth x pixel area), so the scaled unit is million m3.
#: Names must be <= 10 characters (shapefile DBF field-name limit).
SCALED_COLUMN_NAMES = {
    "depre_vol": "depre_vMm3",   # depression storage volume, million m3
    "depth_vol": "depth_vMm3",   # flood water volume, million m3
}


def _scaled_column_name(col: str) -> str:
    """
    Shapefile column name for *col* after it has been divided by 1e6.

    Known volume columns get an explicit unit-bearing name from
    :data:`SCALED_COLUMN_NAMES`.  Anything else falls back to an ``_M``
    suffix ("millions of the original unit").  The result is always at most
    10 characters, so the downstream DBF truncation step leaves it alone.
    """
    if col in SCALED_COLUMN_NAMES:
        return SCALED_COLUMN_NAMES[col]
    return col[:8] + "_M"


class ObjectFilter:
    """Rule-based commission error filter for flood objects."""

    @staticmethod
    def _resolve_centroid_columns(df: pd.DataFrame) -> Optional[Tuple[str, str]]:
        candidates = [
            ("cent_col", "cent_row"),
            ("cent_x", "cent_y"),
            ("x", "y"),
        ]
        for x_col, y_col in candidates:
            if x_col in df.columns and y_col in df.columns:
                return x_col, y_col
        return None

    def __init__(self, params_df: pd.DataFrame) -> None:
        self.df = params_df.copy().drop(columns=["predict"], errors="ignore")
        self.df["predict"] = np.nan

    def rule0_remove_small(self, threshold_px: int = 50) -> "ObjectFilter":
        """
        Mark objects with ``area_px < threshold_px`` as false positive (predict=0).

        Rationale: objects this small have unreliable attribute estimates and
        are almost always noise.

        Parameters
        ----------
        threshold_px : int
            Minimum object size in pixels. Default 50, which is 5,000 m2 at
            10 m resolution. Raise to 200-500 in urban or agricultural areas
            with many small false positives; lower it if small water bodies
            need to be mapped.
        """
        mask = self.df["area_px"] < threshold_px
        self.df.loc[mask, "predict"] = 0
        print(f"[Rule 0] {mask.sum()} objects < {threshold_px} px → predict=0")
        return self

    def rule1_identify_large(
        self, n_clusters: int = 2, n_init: int = 50
    ) -> "ObjectFilter":
        """
        KMeans (k=2) on log(area_km2) among unmarked objects.
        The cluster with the larger centroid → predict=1 (confirmed true flood).

        These large objects serve as anchors for Rule 3 (LWSE reference) and
        Rule 4 (distance filter).

        Parameters
        ----------
        n_clusters : int
            Number of KMeans clusters in log-area space. Default 2
            (small vs large); rarely worth changing.
        n_init : int
            KMeans restarts. Default 50. Lower to 10 for faster processing on
            large datasets — the quality difference is minimal.
        """
        unmarked = self.df[self.df["predict"].isna()].copy()
        areas = unmarked["area_km2"].dropna()

        if len(areas) < n_clusters:
            print("[Rule 1] Not enough unmarked objects for clustering.")
            return self

        log_a = np.log1p(areas.values).reshape(-1, 1)
        km = KMeans(n_clusters=n_clusters, n_init=n_init, random_state=42).fit(log_a)

        centers = km.cluster_centers_.flatten()
        large_cluster = int(np.argmax(centers))
        large_mask = km.labels_ == large_cluster

        large_ids = unmarked.iloc[np.where(large_mask)[0]]["ID"].values
        self.df.loc[self.df["ID"].isin(large_ids), "predict"] = 1

        threshold_km2 = float(np.expm1(centers[large_cluster]))
        print(
            f"[Rule 1] {len(large_ids)} large objects "
            f"(cluster centroid ≈ {threshold_km2:.4f} km²) → predict=1"
        )
        return self

    def rule2_depression_water(self) -> "ObjectFilter":
        """
        Mark objects that contain a topographic pit *and* whose estimated
        flood volume fits within the depression (depth_vol < depre_vol)
        as depression-stored water (predict=2).

        These are real standing water, but not flood water — they are ponds
        and pits that the depression could hold on its own.

        Required columns: ``has_pit``, ``depth_vol``, ``depre_vol``, all added
        by :func:`fimsens.filter.depression.add_depression_attributes`.
        The rule is skipped with a message if any are absent.
        """
        required = {"has_pit", "depth_vol", "depre_vol"}
        missing = required - set(self.df.columns)
        if missing:
            print(f"[Rule 2] Missing columns {missing} — skipped.")
            return self

        uncertain = self.df["predict"].isna()
        mask = (
            uncertain
            & (self.df["has_pit"] == 1)
            & (self.df["depth_vol"] < self.df["depre_vol"])
        )
        self.df.loc[mask, "predict"] = 2
        print(f"[Rule 2] {mask.sum()} depression-water objects → predict=2")
        return self

    def rule3_filter_by_delta_lwse(
        self,
        reference_df: pd.DataFrame,
        threshold_high: Optional[float] = None,
        threshold_low:  Optional[float] = None,
        k_neighbors: int = 5,
        min_threshold: float = 5.0,
    ) -> "ObjectFilter":
        """
        Flag objects whose water surface elevation disagrees with their neighbours.

        For each uncertain object compute::

            delta_LWSE = object_LWSE - mean(LWSE of k nearest reference centroids)

        Objects whose delta_LWSE falls outside the reference distribution are
        outliers:

        * delta_LWSE > threshold_high  -> predict=4  (too high, likely non-flood)
        * delta_LWSE < threshold_low   -> predict=5  (too low, possibly incomplete flood)

        Parameters
        ----------
        reference_df : DataFrame
            Reference segment centroids from
            :func:`fimsens.filter.reference_builder.build_reference_layer`.
            Required columns: ``col``, ``row``, ``LWSE``, ``delta_LWSE``.
            The rule is skipped with a message if any are missing.
        threshold_high : float, optional
            Upper bound in metres. Default None -> auto, computed as
            ``max(ref_mean + 2*ref_std, min_threshold)``. Override (e.g. 10.0)
            when auto-thresholding is too aggressive and removes true flood
            objects on hilly terrain.
        threshold_low : float, optional
            Lower bound in metres. Default None -> auto, ``ref_mean - 2*ref_std``.
            Override when objects below a river's LWSE are wrongly retained.
        k_neighbors : int
            Number of nearest reference centroids used for the local mean.
            Default 5; raise to 10 when the reference layer is sparse (few
            large flood objects).
        min_threshold : float
            Floor on ``threshold_high`` in metres. Default 5.0. Prevents
            over-filtering on very flat terrain where LWSE variance is near
            zero. Lower to 2.0 in flat coastal areas, raise to 10.0 in
            mountainous terrain.

        Notes
        -----
        Also writes two diagnostic columns back to the main table for the
        objects it examined: ``delta_LWSE`` and ``LWSE_local``.
        """
        if "LWSE" not in self.df.columns:
            print("[Rule 3] Column 'LWSE' not found — skipped.")
            return self
        if not {"col", "row", "LWSE", "delta_LWSE"}.issubset(reference_df.columns):
            print("[Rule 3] reference_df missing required columns — skipped.")
            return self

        centroid_cols = self._resolve_centroid_columns(self.df)
        if centroid_cols is None:
            print("[Rule 3] Missing centroid columns — skipped.")
            return self
        cx, cy = centroid_cols

        ref_delta = reference_df["delta_LWSE"].dropna()
        ref_mean = float(ref_delta.mean())
        ref_std  = float(ref_delta.std())

        if threshold_high is None:
            threshold_high = max(ref_mean + 2.0 * ref_std, min_threshold)
        if threshold_low is None:
            threshold_low = ref_mean - 2.0 * ref_std

        print(
            f"[Rule 3] ref delta_LWSE: mean={ref_mean:.4f} m, std={ref_std:.4f} m\n"
            f"         threshold_high={threshold_high:.4f} m, "
            f"threshold_low={threshold_low:.4f} m"
        )

        uncertain_mask = self.df["predict"].isna()
        uncertain = self.df[uncertain_mask].copy().reset_index(drop=True)
        if len(uncertain) == 0:
            print("[Rule 3] No uncertain objects remaining.")
            return self

        ref_coords    = reference_df[["col", "row"]].values
        ref_lwse      = reference_df["LWSE"].values
        target_coords = uncertain[[cx, cy]].values

        dist_matrix = cdist(target_coords, ref_coords, metric="euclidean")
        k = min(k_neighbors, len(ref_coords))
        nearest_idx = np.argsort(dist_matrix, axis=1)[:, :k]

        lwse_local_mean = np.array([ref_lwse[idx].mean() for idx in nearest_idx])
        delta_lwse      = uncertain["LWSE"].values - lwse_local_mean

        for i, obj_id in enumerate(uncertain["ID"].values):
            row_mask = self.df["ID"] == obj_id
            self.df.loc[row_mask, "delta_LWSE"]  = delta_lwse[i]
            self.df.loc[row_mask, "LWSE_local"]  = lwse_local_mean[i]

        too_high_ids = uncertain.loc[delta_lwse > threshold_high, "ID"].values
        too_low_ids  = uncertain.loc[delta_lwse < threshold_low,  "ID"].values

        self.df.loc[self.df["ID"].isin(too_high_ids), "predict"] = 4
        self.df.loc[self.df["ID"].isin(too_low_ids),  "predict"] = 5

        print(
            f"[Rule 3] {len(too_high_ids)} objects too high  → predict=4\n"
            f"         {len(too_low_ids)}  objects too low   → predict=5"
        )
        return self

    def rule4_filter_by_distance(
        self,
        distance_threshold: float = "auto",
        n_init: int = 50,
        reference_df: Optional[pd.DataFrame] = None,
    ) -> "ObjectFilter":
        """
        For remaining uncertain objects, compare distance to nearest predict=1 boundary.

        * distance > threshold  → predict=6  (isolated, likely false positive)
        * distance <= threshold → predict=7  (proximate to confirmed flood, kept)

        Parameters
        ----------
        distance_threshold : float or ``'auto'``
            Threshold in the same units as centroid coordinates (pixels by
            default).

            When ``'auto'`` (default), KMeans (k=2) is run on log(distance+1)
            for uncertain objects to find the natural break between "near
            flood" and "isolated" clusters — analogous to how Rule 1 separates
            large/small objects. The threshold is set to the midpoint between
            the two cluster centroids (in log space, then back-transformed).
            Override with a fixed pixel distance (e.g. 1300) if the automatic
            split gives poor results — check the distance histogram first.
        n_init : int
            KMeans restarts used in auto mode. Default 50.
        reference_df : DataFrame, optional
            Reference boundary-segment table from
            :func:`fimsens.filter.reference_builder.build_reference_layer`.
            When supplied, distances are measured to the nearest boundary
            segment centroid of the large flood objects — better than
            centroid-to-centroid for large, irregular flood bodies. If
            omitted, falls back to predict=1 object centroid distances.
        """
        if "distance" not in self.df.columns:
            print("[Rule 4] 'distance' column not found — computing from predict=1 objects.")
            self._compute_distance_to_flood(reference_df=reference_df)

        nan_mask  = self.df["predict"].isna()
        dist_vals = pd.to_numeric(self.df["distance"], errors="coerce")

        if distance_threshold == "auto":
            uncertain_dists = dist_vals[nan_mask].dropna()
            if len(uncertain_dists) < 2:
                print("[Rule 4] Too few uncertain objects for auto threshold; all → predict=7.")
                self.df.loc[nan_mask, "predict"] = 7
                return self

            log_d = np.log1p(uncertain_dists.values).reshape(-1, 1)
            km = KMeans(n_clusters=2, n_init=n_init, random_state=42).fit(log_d)
            centers = km.cluster_centers_.flatten()
            near_center, far_center = sorted(centers)
            log_mid = (near_center + far_center) / 2.0
            distance_threshold = float(np.expm1(log_mid))
            print(
                f"[Rule 4] Auto threshold: near cluster={np.expm1(near_center):.1f} px, "
                f"far cluster={np.expm1(far_center):.1f} px  →  threshold={distance_threshold:.1f} px"
            )

        gt_mask = nan_mask & (dist_vals > distance_threshold)
        le_mask = nan_mask & (dist_vals <= distance_threshold)

        self.df.loc[gt_mask, "predict"] = 6
        self.df.loc[le_mask, "predict"] = 7

        print(
            f"[Rule 4] distance threshold={distance_threshold:.1f}\n"
            f"         {gt_mask.sum()} objects far from flood  → predict=6\n"
            f"         {le_mask.sum()} objects near confirmed  → predict=7"
        )
        return self

    def _compute_distance_to_flood(
        self, reference_df: Optional[pd.DataFrame] = None
    ) -> None:
        centroid_cols = self._resolve_centroid_columns(self.df)
        if centroid_cols is None:
            print("[Rule 4] Missing centroid columns; setting distance=inf.")
            self.df["distance"] = np.inf
            return
        cx, cy = centroid_cols

        all_coords = self.df[[cx, cy]].values.astype(float)

        if (
            reference_df is not None
            and {"col", "row"}.issubset(reference_df.columns)
            and len(reference_df) > 0
        ):
            ref_coords = reference_df[["col", "row"]].values.astype(float)
            dist_matrix = cdist(all_coords, ref_coords, metric="euclidean")
            self.df["distance"] = dist_matrix.min(axis=1)
            print(
                f"[Rule 4] Distance computed to nearest boundary segment "
                f"({len(ref_coords)} segments from predict=1 objects)."
            )
            return

        flood_df = self.df[self.df["predict"] == 1]
        if len(flood_df) == 0:
            print("[Rule 4] Warning: no predict=1 objects; setting distance=inf.")
            self.df["distance"] = np.inf
            return

        flood_coords = flood_df[[cx, cy]].values.astype(float)
        dist_matrix  = cdist(all_coords, flood_coords, metric="euclidean")
        self.df["distance"] = dist_matrix.min(axis=1)
        print("[Rule 4] Distance computed to nearest predict=1 object centroid.")

    def summary(self) -> pd.Series:
        """
        Print and return predict value counts (including NaN).

        Predict codes:
            0 = false positive, too small        (Rule 0)
            1 = true flood, large                (Rule 1)
            2 = depression-stored water          (Rule 2)
            4 = false positive, LWSE too high    (Rule 3)
            5 = tentative flood, LWSE too low    (Rule 3)
            6 = false positive, too far          (Rule 4)
            7 = true flood, near confirmed       (Rule 4)
            NaN = uncertain, no rule fired
        """
        counts = self.df["predict"].value_counts(dropna=False).sort_index()
        print("\nPredict summary:")
        label_map = {
            0.0: "FP (too small)",
            1.0: "True flood (large)",
            2.0: "Depression water",
            4.0: "FP (too high LWSE)",
            5.0: "Tentative flood (low LWSE)",
            6.0: "FP (too far)",
            7.0: "True flood (near confirmed)",
        }
        for val, cnt in counts.items():
            desc = label_map.get(val, "Uncertain") if pd.notna(val) else "Uncertain (NaN)"
            print(f"  predict={val}: {cnt:5d}  — {desc}")
        return counts

    def get_results(self) -> pd.DataFrame:
        """Return a copy of the full attribute DataFrame with the ``predict`` column."""
        return self.df.copy()

    def validate(self, label_col: str = "label") -> dict:
        """
        Compare predict vs. ground-truth ``label`` (if available).

        Binary mapping used for metrics:
            predict in {1, 5, 7}  -> flood
            everything else       -> non-flood

        Parameters
        ----------
        label_col : str
            Name of the ground-truth column. Default 'label'. Returns an empty
            dict with a message when the column is absent.

        Returns
        -------
        dict
            Keys: TP, FP, FN, TN, precision, recall, f1, accuracy.
        """
        if label_col not in self.df.columns:
            print("[validate] No label column found.")
            return {}

        df = self.df.dropna(subset=[label_col]).copy()
        df[label_col] = df[label_col].replace(3, 0)

        flood_predicts = {1.0, 5.0, 7.0}
        df["pred_bin"]  = df["predict"].apply(lambda p: 1 if p in flood_predicts else 0)
        df["label_bin"] = (df[label_col] == 1).astype(int)

        TP = int(((df["pred_bin"] == 1) & (df["label_bin"] == 1)).sum())
        FP = int(((df["pred_bin"] == 1) & (df["label_bin"] == 0)).sum())
        FN = int(((df["pred_bin"] == 0) & (df["label_bin"] == 1)).sum())
        TN = int(((df["pred_bin"] == 0) & (df["label_bin"] == 0)).sum())

        total = TP + FP + FN + TN
        precision = TP / (TP + FP)   if (TP + FP) > 0 else 0.0
        recall    = TP / (TP + FN)   if (TP + FN) > 0 else 0.0
        f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        accuracy  = (TP + TN) / total if total > 0 else 0.0

        print(
            f"\n[validate] Binary flood/non-flood\n"
            f"  TP={TP}  FP={FP}  FN={FN}  TN={TN}\n"
            f"  Precision={precision:.4f}  Recall={recall:.4f}  "
            f"F1={f1:.4f}  Accuracy={accuracy:.4f}"
        )
        return dict(TP=TP, FP=FP, FN=FN, TN=TN,
                    precision=precision, recall=recall, f1=f1, accuracy=accuracy)

    def save_large_objects(
        self,
        fim_objs_arr: np.ndarray,
        output_path: str,
        meta: dict,
    ) -> None:
        """
        Save a raster containing only predict=1 (large, confirmed) flood objects.

        Used as input for
        :func:`fimsens.filter.reference_builder.build_reference_layer`, which
        derives the LWSE reference layer that Rules 3 and 4 depend on. Run this
        after :meth:`rule1_identify_large`.

        Parameters
        ----------
        fim_objs_arr : ndarray
            Full labeled object raster.
        output_path : str
            Output GeoTIFF path.
        meta : dict
            Rasterio metadata from the object raster. dtype, nodata and
            compression are overridden internally (uint32 / 0 / LZW).
        """
        keep_ids = self.df.loc[self.df["predict"] == 1, "ID"].values
        filtered = fim_objs_arr.copy()
        filtered[~np.isin(filtered, keep_ids)] = 0

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        out_meta = meta.copy()
        out_meta.update({"dtype": "uint32", "nodata": 0,
                         "compress": "lzw", "TILED": "YES"})
        import rasterio
        with rasterio.open(output_path, "w", **out_meta) as dst:
            dst.write(filtered.astype(np.uint32), 1)
        print(f"[save_large_objects] {len(keep_ids)} objects → {output_path}")

    def save_results(
        self,
        output_dir: str,
        filename_prefix: str = "object_predict",
        crs=None,
    ) -> Tuple[str, str]:
        """
        Save the results DataFrame to CSV and point shapefile.

        The CSV is written at full precision with no column or value changes,
        and is the authoritative output — take paper figures and tables from
        it. The shapefile is for visualisation and needs two automatic fixes
        to fit the format's limits:

        * Column names longer than 10 characters are truncated (with
          deduplication) to fit the DBF field-name limit.
        * Numeric columns whose absolute maximum exceeds 1e9 are divided by
          1e6 to stay inside the shapefile's field width, AND renamed so the
          unit is visible in the name — ``depth_vol`` becomes ``depth_vMm3``
          (million m3), ``depre_vol`` becomes ``depre_vMm3``, anything else
          gets an ``_M`` suffix. Without the rename the same column would be
          m3 for a small study area and million-m3 for a large one, with
          nothing in the file to tell them apart. Each rename is printed.

        Parameters
        ----------
        output_dir : str
            Directory for both outputs; created if absent.
        filename_prefix : str
            Base name for the two files. Default 'object_predict'.
        crs : optional
            CRS passed to GeoDataFrame. Pass the object raster's CRS so the
            shapefile is georeferenced; None writes it without a .prj.

        Returns
        -------
        tuple[str, str]
            ``(csv_path, shp_path)``. ``shp_path`` is an empty string when no
            coordinate columns were found and the shapefile was skipped.
        """
        os.makedirs(output_dir, exist_ok=True)
        csv_path = os.path.join(output_dir, f"{filename_prefix}.csv")
        shp_path = os.path.join(output_dir, f"{filename_prefix}.shp")

        self.df.to_csv(csv_path, index=False)
        print(f"[save_results] CSV → {csv_path}")

        for x_col, y_col in [("x", "y"), ("cent_x", "cent_y"), ("X", "Y")]:
            if x_col in self.df.columns and y_col in self.df.columns:
                shp_df = self.df.dropna(subset=[x_col, y_col]).copy()

                # --- Scale oversized float columns (shapefile field-width limit) ---
                # The scaled column is RENAMED so the shapefile is
                # self-documenting: without this, the same column is in m3 for
                # one study area and in millions of m3 for another, with
                # nothing in the file to tell them apart.
                scale_renames: dict[str, str] = {}
                for col in shp_df.select_dtypes(include="number").columns:
                    if col in ("geometry", x_col, y_col):
                        continue
                    col_max = shp_df[col].abs().max()
                    if pd.notna(col_max) and col_max > 1e9:
                        shp_df[col] = shp_df[col] / 1e6
                        new_name = _scaled_column_name(col)
                        # The truncation step below only deduplicates names it
                        # shortens, so collisions introduced here (two long
                        # columns both mapping to "<8 chars>_M") must be
                        # resolved now.
                        taken = set(shp_df.columns) - {col} | set(scale_renames.values())
                        if new_name in taken:
                            n = 1
                            while f"{new_name[:9]}{n}" in taken:
                                n += 1
                            new_name = f"{new_name[:9]}{n}"
                        scale_renames[col] = new_name
                        print(f"[save_results] '{col}' scaled /1e6 for shapefile "
                              f"(max was {col_max:.2e}) -> renamed "
                              f"'{new_name}'. The CSV keeps raw values.")
                if scale_renames:
                    shp_df = shp_df.rename(columns=scale_renames)

                seen: dict[str, int] = {}
                rename_map: dict[str, str] = {}
                for col in shp_df.columns:
                    short = col[:10]
                    if short != col:
                        if short in seen:
                            seen[short] += 1
                            short = short[:9] + str(seen[short])
                        else:
                            seen[short] = 0
                        rename_map[col] = short
                if rename_map:
                    shp_df = shp_df.rename(columns=rename_map)

                shp_df["geometry"] = [
                    Point(x, y) for x, y in zip(shp_df[x_col], shp_df[y_col])
                ]
                gdf = gpd.GeoDataFrame(shp_df, geometry="geometry", crs=crs)
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    gdf.to_file(shp_path)
                print(f"[save_results] SHP  → {shp_path}")
                break
        else:
            print("[save_results] No coordinate columns found; SHP not saved.")
            shp_path = ""

        return csv_path, shp_path
