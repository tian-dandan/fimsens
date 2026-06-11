"""
gee_preview.py — Interactive geemap visualisation + ipywidgets selection panel.

Usage
-----
    from fimsense.download.gee_preview import GEEPreview
    preview = GEEPreview()
    m = preview.create_map(results, aoi_input=[xmin, ymin, xmax, ymax])
    display(m)                           # show interactive map in notebook

    sel_widget = preview.create_selection_widget(results)
    display(sel_widget)                  # show checkbox panel

    # After user checks boxes:
    selected_ids = preview.get_selected_ids()
"""
from __future__ import annotations

from typing import Dict, List, Optional

import ee
import geemap
import ipywidgets as widgets

from .gee_utils import parse_aoi


# ─── Visualisation parameters ─────────────────────────────────────────────────

# SAR: R=VV  G=VH  B=VH  (backscatter in dB)
VIS_S1 = dict(
    bands=["VV", "VH", "VH"],
    min=[-20, -25, -25],
    max=[0,   -5,  -5],
)

# Sentinel-2 NIR-R-G  (raw DN, SR scale ~0-3000)
VIS_S2 = dict(
    bands=["B8", "B4", "B3"],
    min=0, max=3000, gamma=1.4,
)

# Landsat NIR-R-G shown after applying C2 scale factors (reflectance 0-1)
VIS_LANDSAT = dict(
    bands=["SR_B5", "SR_B4", "SR_B3"],
    min=0.0, max=0.35, gamma=1.4,
)

# JRC water occurrence (%)
VIS_JRC = dict(
    bands=["occurrence"],
    min=0, max=100,
    palette=["white", "#4DA6FF"],
)

# DEM elevation (m)
VIS_DEM = dict(
    min=0, max=3000,
    palette=["#006633", "#E5FFCC", "#A09060", "#C8C8C8", "#FFFFFF"],
)


class GEEPreview:
    """
    Build and maintain a geemap interactive map for satellite image preview,
    and an ipywidgets checkbox panel for image selection.
    """

    def __init__(self):
        self.map: Optional[geemap.Map] = None
        self._checkboxes: Dict[str, widgets.Checkbox] = {}

    # ─── Map ──────────────────────────────────────────────────────────────────

    def create_map(
        self,
        results: Dict,
        aoi_input=None,
        zoom: int = 8,
    ) -> geemap.Map:
        """
        Create a geemap.Map with all found images as toggleable layers.
        """
        m = geemap.Map(add_google_map=False)
        m.add_basemap("SATELLITE")

        if aoi_input is not None:
            _, ext = parse_aoi(aoi_input)
            x1, y1, x2, y2 = map(float, ext.split(","))
            m.setCenter((x1 + x2) / 2, (y1 + y2) / 2, zoom)

        if "ee_geometry" in results:
            try:
                fc = ee.FeatureCollection([ee.Feature(results["ee_geometry"])])
                m.addLayer(fc, {"color": "FF4444", "fillColor": "00000000"}, "AOI")
            except Exception:
                pass

        for rec in results.get("s1", []):
            try:
                img   = ee.Image(rec["image_id"]).select(["VV", "VH"])
                orbit = (rec.get("orbitProperties_pass") or "")[:3]
                suffix = rec["image_id"].split("/")[-1][-4:]
                label  = f"S1  {rec['date']}  {orbit}  …{suffix}"
                m.addLayer(img, VIS_S1, label, shown=False)
            except Exception as exc:
                print(f"  [skip S1 layer] {exc}")

        for i, rec in enumerate(results.get("s2", [])):
            try:
                img   = ee.Image(rec["image_id"]).select(["B8", "B4", "B3"])
                cc    = rec.get("CLOUDY_PIXEL_PERCENTAGE")
                cc_s  = f" ☁{cc:.0f}%" if cc is not None else ""
                tile  = rec.get("MGRS_TILE") or rec["image_id"].split("/")[-1][-6:]
                label = f"S2  {rec['date']}{cc_s}  {tile}"
                m.addLayer(img, VIS_S2, label, shown=(i == 0))
            except Exception as exc:
                print(f"  [skip S2 layer] {exc}")

        for rec in results.get("landsat", []):
            try:
                img = (
                    ee.Image(rec["image_id"])
                    .select(["SR_B5", "SR_B4", "SR_B3"])
                    .multiply(0.0000275).add(-0.2)
                    .clamp(0, 1)
                )
                cc   = rec.get("CLOUD_COVER")
                sc   = (rec.get("SPACECRAFT_ID") or "LS").replace("LANDSAT_", "L")
                cc_s = f" ☁{cc:.0f}%" if cc is not None else ""
                pr   = f"  p{rec.get('WRS_PATH','?')}r{rec.get('WRS_ROW','?')}"
                label = f"{sc}  {rec['date']}{cc_s}{pr}"
                m.addLayer(img, VIS_LANDSAT, label, shown=False)
            except Exception as exc:
                print(f"  [skip Landsat layer] {exc}")

        self.map = m
        return m

    def add_jrc(self, jrc_img: ee.Image) -> None:
        """Overlay JRC water occurrence on the existing map."""
        if self.map is not None:
            self.map.addLayer(jrc_img, VIS_JRC, "JRC Water Occurrence", shown=False)

    def add_dem(self, dem_img: ee.Image, dem_name: str) -> None:
        """Overlay a DEM layer on the existing map."""
        if self.map is not None:
            self.map.addLayer(dem_img, VIS_DEM, f"DEM – {dem_name}", shown=False)

    # ─── Selection widget ─────────────────────────────────────────────────────

    def create_selection_widget(self, results: Dict) -> widgets.VBox:
        """
        Build a scrollable checkbox panel grouped by sensor.
        Check the boxes you want, then call ``get_selected_ids()``.
        """
        self._checkboxes = {}
        sections: List[widgets.Widget] = []

        sensor_cfg = [
            ("s1",      "🛰  Sentinel-1",   self._label_s1),
            ("s2",      "🛰  Sentinel-2",   self._label_s2),
            ("landsat", "🛰  Landsat 8/9",  self._label_ls),
        ]

        for key, header_text, labeller in sensor_cfg:
            recs = results.get(key, [])
            if not recs:
                continue

            header = widgets.HTML(
                f"<h4 style='margin:10px 0 4px;color:#2c3e50'>"
                f"{header_text} "
                f"<span style='font-weight:normal;color:#888;font-size:0.9em'>"
                f"({len(recs)} scenes)</span></h4>"
            )
            cbs = []
            for rec in recs:
                cb = widgets.Checkbox(
                    value=False,
                    description=labeller(rec),
                    layout=widgets.Layout(width="100%"),
                    style={"description_width": "initial"},
                )
                self._checkboxes[rec["image_id"]] = cb
                cbs.append(cb)

            sections.append(widgets.VBox([header, *cbs]))

        if not sections:
            return widgets.VBox([widgets.HTML("<i>No images found.</i>")])

        btn_all  = widgets.Button(
            description="✔ Select all", button_style="info",
            layout=widgets.Layout(width="130px"),
        )
        btn_none = widgets.Button(
            description="✖ Clear all", button_style="warning",
            layout=widgets.Layout(width="130px"),
        )
        btn_all .on_click(lambda _: [setattr(c, "value", True)  for c in self._checkboxes.values()])
        btn_none.on_click(lambda _: [setattr(c, "value", False) for c in self._checkboxes.values()])

        return widgets.VBox([
            widgets.HTML("<h3 style='color:#2c3e50'>Select images to download</h3>"),
            widgets.HBox([btn_all, btn_none]),
            *sections,
        ])

    def get_selected_ids(self) -> List[str]:
        """Return list of image IDs whose checkbox is ticked."""
        return [iid for iid, cb in self._checkboxes.items() if cb.value]

    def get_selected_records(self, results: Dict) -> List[Dict]:
        """Return full record dicts for all selected images."""
        selected_ids = set(self.get_selected_ids())
        all_records  = (
            results.get("s1",      []) +
            results.get("s2",      []) +
            results.get("landsat", [])
        )
        return [r for r in all_records if r["image_id"] in selected_ids]

    # ─── Label helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _label_s1(rec: dict) -> str:
        orbit = (rec.get("orbitProperties_pass") or "N/A")
        rel   = rec.get("relativeOrbitNumber_start", "–")
        short = rec["image_id"].split("/")[-1]
        return f"{rec['date']}  |  {orbit[:3]}  |  rel-orbit {rel}  |  {short}"

    @staticmethod
    def _label_s2(rec: dict) -> str:
        cc   = rec.get("CLOUDY_PIXEL_PERCENTAGE")
        tile = rec.get("MGRS_TILE", "N/A")
        cc_s = f"cloud {cc:.1f}%" if cc is not None else "cloud N/A"
        return f"{rec['date']}  |  {cc_s}  |  tile {tile}"

    @staticmethod
    def _label_ls(rec: dict) -> str:
        sc   = (rec.get("SPACECRAFT_ID") or "?").replace("LANDSAT_", "L")
        cc   = rec.get("CLOUD_COVER")
        path = rec.get("WRS_PATH", "–")
        row  = rec.get("WRS_ROW",  "–")
        cc_s = f"cloud {cc:.1f}%" if cc is not None else "cloud N/A"
        return f"{rec['date']}  |  {sc}  |  {cc_s}  |  path/row {path}/{row}"
