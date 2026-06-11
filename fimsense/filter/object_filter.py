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
        mask = self.df["area_px"] < threshold_px
        self.df.loc[mask, "predict"] = 0
        print(f"[Rule 0] {mask.sum()} objects < {threshold_px} px → predict=0")
        return self

    def rule1_identify_large(
        self, n_clusters: int = 2, n_init: int = 50
    ) -> "ObjectFilter":
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
        return self.df.copy()

    def validate(self, label_col: str = "label") -> dict:
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
        os.makedirs(output_dir, exist_ok=True)
        csv_path = os.path.join(output_dir, f"{filename_prefix}.csv")
        shp_path = os.path.join(output_dir, f"{filename_prefix}.shp")

        self.df.to_csv(csv_path, index=False)
        print(f"[save_results] CSV → {csv_path}")

        for x_col, y_col in [("x", "y"), ("cent_x", "cent_y"), ("X", "Y")]:
            if x_col in self.df.columns and y_col in self.df.columns:
                shp_df = self.df.dropna(subset=[x_col, y_col]).copy()

                for col in shp_df.select_dtypes(include="number").columns:
                    if col == "geometry":
                        continue
                    col_max = shp_df[col].abs().max()
                    if pd.notna(col_max) and col_max > 1e9:
                        shp_df[col] = shp_df[col] / 1e6

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
