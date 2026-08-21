"""
fimsens_gui.py
---------------
PyQt6 desktop GUI for the FIMsens flood inundation mapping pipeline.

Run directly:
    python fimsens_gui.py

Or after `pip install fimsens[gui]`:
    fimsens-gui
"""

import sys
import os
import traceback
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit,
    QFileDialog, QSpinBox, QDoubleSpinBox, QComboBox,
    QGroupBox, QScrollArea, QSizePolicy, QSplitter,
    QCheckBox, QStatusBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor, QTextCursor, QPalette


# ---------------------------------------------------------------------------
# Colours / style
# ---------------------------------------------------------------------------
ACCENT   = "#2d7dd2"
BG_DARK  = "#1e1e2e"
BG_MID   = "#2a2a3e"
BG_LIGHT = "#313150"
FG       = "#cdd6f4"
FG_DIM   = "#888aaa"
GREEN    = "#a6e3a1"
RED      = "#f38ba8"
YELLOW   = "#f9e2af"

STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {BG_DARK};
    color: {FG};
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}}
QTabWidget::pane {{
    border: 1px solid {BG_LIGHT};
    background: {BG_MID};
}}
QTabBar::tab {{
    background: {BG_DARK};
    color: {FG_DIM};
    padding: 8px 20px;
    border: 1px solid {BG_LIGHT};
    border-bottom: none;
    min-width: 120px;
}}
QTabBar::tab:selected {{
    background: {BG_MID};
    color: {FG};
    border-top: 2px solid {ACCENT};
}}
QGroupBox {{
    border: 1px solid {BG_LIGHT};
    border-radius: 4px;
    margin-top: 10px;
    padding: 8px;
    font-weight: bold;
    color: {FG_DIM};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG_DARK};
    color: {FG};
    border: 1px solid {BG_LIGHT};
    border-radius: 3px;
    padding: 4px 8px;
    min-height: 24px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QPushButton {{
    background: {BG_LIGHT};
    color: {FG};
    border: 1px solid #555;
    border-radius: 4px;
    padding: 5px 14px;
    min-height: 28px;
}}
QPushButton:hover {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background: #1a5fa0;
}}
QPushButton:disabled {{
    color: {FG_DIM};
    background: {BG_DARK};
}}
QPushButton#run_btn {{
    background: {ACCENT};
    font-weight: bold;
    font-size: 14px;
    min-height: 36px;
}}
QPushButton#run_btn:hover {{ background: #3a90e8; }}
QPushButton#run_btn:disabled {{ background: {BG_LIGHT}; color: {FG_DIM}; }}
QTextEdit {{
    background: #0d0d1a;
    color: #b0ffb0;
    border: 1px solid {BG_LIGHT};
    font-family: "Consolas", "Courier New", monospace;
    font-size: 12px;
}}
QLabel#section_label {{
    color: {ACCENT};
    font-weight: bold;
    font-size: 12px;
}}
QLabel#hint {{
    color: {FG_DIM};
    font-size: 11px;
    font-style: italic;
}}
QScrollArea {{ border: none; }}
QSplitter::handle {{ background: {BG_LIGHT}; }}
QStatusBar {{ background: {BG_DARK}; color: {FG_DIM}; }}
"""


# ---------------------------------------------------------------------------
# Worker thread — runs a callable, streams stdout to a Qt signal
# ---------------------------------------------------------------------------

class _StreamCapture:
    """Redirect stdout to a Qt signal."""
    def __init__(self, signal):
        self._signal = signal
        self._orig = sys.stdout

    def write(self, text):
        if text.strip():
            self._signal.emit(text.rstrip())
        self._orig.write(text)

    def flush(self):
        self._orig.flush()


class Worker(QObject):
    log     = pyqtSignal(str)
    error   = pyqtSignal(str)
    done    = pyqtSignal(bool)   # True = success

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        cap = _StreamCapture(self.log)
        sys.stdout = cap
        try:
            self._fn(*self._args, **self._kwargs)
            self.done.emit(True)
        except Exception:
            tb = traceback.format_exc()
            self.error.emit(tb)
            self.done.emit(False)
        finally:
            sys.stdout = cap._orig


# ---------------------------------------------------------------------------
# Reusable helpers
# ---------------------------------------------------------------------------

def _file_row(label_text, placeholder="", mode="open", hint=None):
    """Return (row_widget, line_edit) for a labelled file/folder picker."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)

    lbl = QLabel(label_text)
    lbl.setFixedWidth(160)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    le = QLineEdit()
    le.setPlaceholderText(placeholder)
    btn = QPushButton("Browse…")
    btn.setFixedWidth(80)

    def _browse():
        if mode == "open":
            path, _ = QFileDialog.getOpenFileName(w, f"Select {label_text}")
        elif mode == "open_shp":
            path, _ = QFileDialog.getOpenFileName(w, f"Select {label_text}", filter="Shapefiles (*.shp)")
        elif mode == "save":
            path, _ = QFileDialog.getSaveFileName(w, f"Save {label_text}")
        else:  # folder
            path = QFileDialog.getExistingDirectory(w, f"Select {label_text}")
        if path:
            le.setText(path)

    btn.clicked.connect(_browse)
    h.addWidget(lbl)
    h.addWidget(le, 1)
    h.addWidget(btn)

    if hint:
        outer = QWidget()
        v = QVBoxLayout(outer)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        v.addWidget(w)
        hint_lbl = QLabel(hint)
        hint_lbl.setObjectName("hint")
        hint_lbl.setContentsMargins(168, 0, 0, 0)
        v.addWidget(hint_lbl)
        return outer, le

    return w, le


def _param_row(label_text, widget, hint=None):
    """Return (row_widget,) for a labelled parameter field."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)

    lbl = QLabel(label_text)
    lbl.setFixedWidth(160)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    h.addWidget(lbl)
    h.addWidget(widget)
    h.addStretch()
    return w


def _section(title):
    lbl = QLabel(title)
    lbl.setObjectName("section_label")
    return lbl


def _make_run_button(text="▶  Run"):
    btn = QPushButton(text)
    btn.setObjectName("run_btn")
    return btn


def _scrollable(inner_widget):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner_widget)
    return sa


# ---------------------------------------------------------------------------
# Log panel (shared across all tabs)
# ---------------------------------------------------------------------------

class LogPanel(QTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMinimumHeight(160)

    def append_log(self, text):
        self.append(f"<span style='color:#b0ffb0'>{text}</span>")
        self.moveCursor(QTextCursor.MoveOperation.End)

    def append_error(self, text):
        for line in text.splitlines():
            self.append(f"<span style='color:{RED}'>{line}</span>")
        self.moveCursor(QTextCursor.MoveOperation.End)

    def append_success(self):
        self.append(f"<span style='color:{GREEN}'>✔  Done.</span>")
        self.moveCursor(QTextCursor.MoveOperation.End)

    def append_info(self, text):
        self.append(f"<span style='color:{YELLOW}'>{text}</span>")
        self.moveCursor(QTextCursor.MoveOperation.End)


# ---------------------------------------------------------------------------
# Base tab class
# ---------------------------------------------------------------------------

class BaseTab(QWidget):
    def __init__(self, log_panel: LogPanel, status_bar: QStatusBar):
        super().__init__()
        self._log = log_panel
        self._status = status_bar
        self._thread = None
        self._worker = None

    def _run_worker(self, fn, *args, run_btn=None, **kwargs):
        if self._thread and self._thread.isRunning():
            self._log.append_info("⚠ Already running — please wait.")
            return

        self._thread = QThread()
        self._worker = Worker(fn, *args, **kwargs)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log.append_log)
        self._worker.error.connect(self._log.append_error)
        self._worker.done.connect(lambda ok: self._on_done(ok, run_btn))
        self._worker.done.connect(lambda _: self._thread.quit())

        if run_btn:
            run_btn.setEnabled(False)
        self._status.showMessage("Running…")
        self._thread.start()

    def _on_done(self, ok, run_btn):
        if run_btn:
            run_btn.setEnabled(True)
        if ok:
            self._log.append_success()
            self._status.showMessage("Done.", 4000)
        else:
            self._log.append_info("✘ An error occurred — see log above.")
            self._status.showMessage("Error.", 4000)

    def _val(self, widget):
        """Get value from any supported widget type."""
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return widget.value()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        return None

    def _require(self, *fields):
        """Check that all (label, lineedit) pairs have values. Returns False if any missing."""
        for label, le in fields:
            if not self._val(le):
                self._log.append_info(f"⚠ Please set: {label}")
                return False
        return True


# ---------------------------------------------------------------------------
# Tab 1 — Download
# ---------------------------------------------------------------------------

class DownloadTab(BaseTab):
    def __init__(self, log_panel, status_bar):
        super().__init__(log_panel, status_bar)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(10)

        # ── GEE note ───────────────────────────────────────────────────────
        note = QLabel(
            "Google Earth Engine must be authenticated before use.\n"
            "Run  ee.Authenticate()  and  ee.Initialize()  once in a Python session."
        )
        note.setWordWrap(True)
        note.setObjectName("hint")
        layout.addWidget(note)

        # ── Search ─────────────────────────────────────────────────────────
        grp_search = QGroupBox("Search")
        g = QVBoxLayout(grp_search)

        row_aoi, self.le_aoi = _file_row(
            "AOI shapefile", "path/to/aoi.shp", mode="open_shp",
            hint="Polygon shapefile defining the area of interest"
        )
        g.addWidget(row_aoi)

        row_coll = QWidget()
        h = QHBoxLayout(row_coll); h.setContentsMargins(0,0,0,0)
        lbl = QLabel("Collection"); lbl.setFixedWidth(160); lbl.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        self.cb_collection = QComboBox()
        self.cb_collection.addItems([
            "COPERNICUS/S1_GRD",
            "COPERNICUS/S2_SR_HARMONIZED",
            "COPERNICUS/S2_SR",
            "LANDSAT/LC09/C02/T1_L2",
            "LANDSAT/LC08/C02/T1_L2",
        ])
        self.cb_collection.setEditable(True)
        h.addWidget(lbl); h.addWidget(self.cb_collection); h.addStretch()
        g.addWidget(row_coll)

        row_start = QWidget()
        h2 = QHBoxLayout(row_start); h2.setContentsMargins(0,0,0,0)
        lbl2 = QLabel("Start date"); lbl2.setFixedWidth(160); lbl2.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        self.le_start = QLineEdit(); self.le_start.setPlaceholderText("YYYY-MM-DD")
        lbl3 = QLabel("End date"); lbl3.setContentsMargins(12,0,0,0)
        self.le_end = QLineEdit(); self.le_end.setPlaceholderText("YYYY-MM-DD")
        h2.addWidget(lbl2); h2.addWidget(self.le_start); h2.addWidget(lbl3); h2.addWidget(self.le_end); h2.addStretch()
        g.addWidget(row_start)

        btn_search = QPushButton("Search GEE")
        btn_search.clicked.connect(self._run_search)
        g.addWidget(btn_search)
        layout.addWidget(grp_search)

        # ── Download ───────────────────────────────────────────────────────
        grp_dl = QGroupBox("Download")
        gd = QVBoxLayout(grp_dl)

        row_out, self.le_out = _file_row("Output folder", mode="folder")
        gd.addWidget(row_out)

        row_idx = QWidget()
        h3 = QHBoxLayout(row_idx); h3.setContentsMargins(0,0,0,0)
        lbl4 = QLabel("Result index"); lbl4.setFixedWidth(160); lbl4.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        self.sp_idx = QSpinBox(); self.sp_idx.setRange(0, 999); self.sp_idx.setValue(0)
        h3.addWidget(lbl4); h3.addWidget(self.sp_idx); h3.addStretch()
        gd.addWidget(row_idx)

        btn_dl = _make_run_button("▶  Download")
        btn_dl.clicked.connect(lambda: self._run_download(btn_dl))
        gd.addWidget(btn_dl)
        layout.addWidget(grp_dl)

        layout.addStretch()
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(_scrollable(inner))

        self._search_results = []

    def _run_search(self):
        if not self._require(("AOI shapefile", self.le_aoi),
                              ("Start date", self.le_start),
                              ("End date", self.le_end)):
            return
        def _search():
            from fimsens.download import GEESearcher
            import ee
            ee.Initialize()
            searcher = GEESearcher()
            self._search_results = searcher.search(
                aoi=self.le_aoi.text().strip(),
                start_date=self.le_start.text().strip(),
                end_date=self.le_end.text().strip(),
                collection=self.cb_collection.currentText().strip(),
            )
            print(f"Found {len(self._search_results)} images.")
            for i, r in enumerate(self._search_results[:10]):
                print(f"  [{i}] {r}")
        self._run_worker(_search)

    def _run_download(self, btn):
        if not self._require(("Output folder", self.le_out)):
            return
        if not self._search_results:
            self._log.append_info("⚠ Run Search first.")
            return
        idx = self.sp_idx.value()
        result = self._search_results[idx]
        out_dir = self.le_out.text().strip()
        def _dl():
            from fimsens.download import GEEDownloader
            dl = GEEDownloader()
            dl.direct_download(result, output_dir=out_dir)
        self._run_worker(_dl, run_btn=btn)


# ---------------------------------------------------------------------------
# Tab 2 — Raw FIM
# ---------------------------------------------------------------------------

class RawTab(BaseTab):
    def __init__(self, log_panel, status_bar):
        super().__init__(log_panel, status_bar)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(10)

        # ── Workspace ─────────────────────────────────────────────────────
        row_ws, self.le_ws = _file_row(
            "Workspace folder", mode="folder",
            hint="All intermediate and output files are written here"
        )
        layout.addWidget(row_ws)

        # ── 1. Speckle filter ──────────────────────────────────────────────
        grp1 = QGroupBox("1. Speckle Filter (Lee)")
        g1 = QVBoxLayout(grp1)
        row_in1, self.le_lee_in = _file_row("Input SAR raster", "rs_band.tif")
        row_out1, self.le_lee_out = _file_row("Output raster", "rs_lee.tif", mode="save")
        g1.addWidget(row_in1); g1.addWidget(row_out1)

        pw = QWidget(); ph = QHBoxLayout(pw); ph.setContentsMargins(0,0,0,0)
        self.sp_window = QSpinBox(); self.sp_window.setRange(3,21); self.sp_window.setSingleStep(2); self.sp_window.setValue(7)
        self.sp_enl = QDoubleSpinBox(); self.sp_enl.setRange(0.1,20); self.sp_enl.setValue(4.9); self.sp_enl.setSingleStep(0.1)
        self.cb_enhanced = QCheckBox("Enhanced Lee")
        self.cb_db = QCheckBox("dB input")
        ph.addWidget(QLabel("Window:")); ph.addWidget(self.sp_window)
        ph.addWidget(QLabel("  ENL:")); ph.addWidget(self.sp_enl)
        ph.addWidget(self.cb_enhanced); ph.addWidget(self.cb_db); ph.addStretch()
        g1.addWidget(pw)

        btn1 = _make_run_button("▶  Run Lee Filter")
        btn1.clicked.connect(lambda: self._run_lee(btn1))
        g1.addWidget(btn1)
        layout.addWidget(grp1)

        # ── 2. Region split ────────────────────────────────────────────────
        grp2 = QGroupBox("2. Adaptive Tile Splitting")
        g2 = QVBoxLayout(grp2)
        row_in2, self.le_rs_in = _file_row("SAR raster", "rs_lee.tif")
        g2.addWidget(row_in2)

        p2 = QWidget(); h2 = QHBoxLayout(p2); h2.setContentsMargins(0,0,0,0)
        self.sp_threshold = QDoubleSpinBox(); self.sp_threshold.setRange(0.01,0.99); self.sp_threshold.setValue(0.3); self.sp_threshold.setSingleStep(0.05)
        self.sp_tile_size = QSpinBox(); self.sp_tile_size.setRange(100,50000); self.sp_tile_size.setValue(5000); self.sp_tile_size.setSingleStep(500)
        self.sp_levels = QSpinBox(); self.sp_levels.setRange(1,8); self.sp_levels.setValue(4)
        h2.addWidget(QLabel("Threshold:")); h2.addWidget(self.sp_threshold)
        h2.addWidget(QLabel("  Tile size (m):")); h2.addWidget(self.sp_tile_size)
        h2.addWidget(QLabel("  Levels:")); h2.addWidget(self.sp_levels); h2.addStretch()
        g2.addWidget(p2)

        btn2 = _make_run_button("▶  Run Region Split")
        btn2.clicked.connect(lambda: self._run_split(btn2))
        g2.addWidget(btn2)
        layout.addWidget(grp2)

        # ── 3. Bimodal gamma fit ───────────────────────────────────────────
        grp3 = QGroupBox("3. Bimodal Gamma Threshold Fitting")
        g3 = QVBoxLayout(grp3)
        row_in3, self.le_sar = _file_row("SAR raster", "rs_lee.tif")
        g3.addWidget(row_in3)

        p3 = QWidget(); h3 = QHBoxLayout(p3); h3.setContentsMargins(0,0,0,0)
        lbl_g = QLabel("Gamma init (α1 α2 r1 r2):")
        lbl_g.setFixedWidth(200)
        self.le_gamma = QLineEdit("18 31 1 2")
        self.cb_imgtype = QComboBox(); self.cb_imgtype.addItems(["SAR", "optical"])
        h3.addWidget(lbl_g); h3.addWidget(self.le_gamma); h3.addWidget(QLabel("  Type:")); h3.addWidget(self.cb_imgtype); h3.addStretch()
        g3.addWidget(p3)

        btn3 = _make_run_button("▶  Run Bimodal Fit")
        btn3.clicked.connect(lambda: self._run_gamma(btn3))
        g3.addWidget(btn3)
        layout.addWidget(grp3)

        # ── 4. Outlier filter ──────────────────────────────────────────────
        grp4 = QGroupBox("4. Threshold Outlier Filter")
        g4 = QVBoxLayout(grp4)
        p4 = QWidget(); h4 = QHBoxLayout(p4); h4.setContentsMargins(0,0,0,0)
        self.sp_bc = QDoubleSpinBox(); self.sp_bc.setRange(0,1); self.sp_bc.setValue(0.555); self.sp_bc.setSingleStep(0.05)
        h4.addWidget(QLabel("Bimodality coeff threshold:")); h4.addWidget(self.sp_bc); h4.addStretch()
        g4.addWidget(p4)
        btn4 = _make_run_button("▶  Filter Outliers")
        btn4.clicked.connect(lambda: self._run_outliers(btn4))
        g4.addWidget(btn4)
        layout.addWidget(grp4)

        # ── 5. IDW flood ───────────────────────────────────────────────────
        grp5 = QGroupBox("5. IDW Flood Classification")
        g5 = QVBoxLayout(grp5)
        row_in5, self.le_idw_sar = _file_row("SAR raster", "rs_lee.tif")
        g5.addWidget(row_in5)
        p5 = QWidget(); h5 = QHBoxLayout(p5); h5.setContentsMargins(0,0,0,0)
        self.sp_idw_power = QDoubleSpinBox(); self.sp_idw_power.setRange(0.1,10); self.sp_idw_power.setValue(2.0); self.sp_idw_power.setSingleStep(0.5)
        h5.addWidget(QLabel("IDW power:")); h5.addWidget(self.sp_idw_power); h5.addStretch()
        g5.addWidget(p5)
        btn5 = _make_run_button("▶  IDW Flood")
        btn5.clicked.connect(lambda: self._run_idw(btn5))
        g5.addWidget(btn5)
        layout.addWidget(grp5)

        # ── 6. Region grow ─────────────────────────────────────────────────
        grp6 = QGroupBox("6. Region Grow (from JRC seed)")
        g6 = QVBoxLayout(grp6)
        row_seed, self.le_seed = _file_row("JRC seed raster", "jrc_seed.tif")
        row_grow, self.le_grow_sar = _file_row("SAR raster", "rs_lee.tif")
        g6.addWidget(row_seed); g6.addWidget(row_grow)
        p6 = QWidget(); h6 = QHBoxLayout(p6); h6.setContentsMargins(0,0,0,0)
        self.sp_grow_thresh = QDoubleSpinBox(); self.sp_grow_thresh.setRange(0.001,1.0); self.sp_grow_thresh.setValue(0.05); self.sp_grow_thresh.setSingleStep(0.005); self.sp_grow_thresh.setDecimals(4)
        h6.addWidget(QLabel("Threshold:")); h6.addWidget(self.sp_grow_thresh); h6.addStretch()
        g6.addWidget(p6)
        btn6 = _make_run_button("▶  Region Grow")
        btn6.clicked.connect(lambda: self._run_grow(btn6))
        g6.addWidget(btn6)
        layout.addWidget(grp6)

        # ── 7. Post-process ────────────────────────────────────────────────
        grp7 = QGroupBox("7. Morphological Post-Processing")
        g7 = QVBoxLayout(grp7)
        row_pp_in, self.le_pp_in = _file_row("Input flood raster", "water_connect.tif")
        row_pp_out, self.le_pp_out = _file_row("Output raster", "flood_clean.tif", mode="save")
        g7.addWidget(row_pp_in); g7.addWidget(row_pp_out)
        p7 = QWidget(); h7 = QHBoxLayout(p7); h7.setContentsMargins(0,0,0,0)
        self.sp_minobj = QSpinBox(); self.sp_minobj.setRange(1,100000); self.sp_minobj.setValue(100)
        self.sp_minhole = QSpinBox(); self.sp_minhole.setRange(1,1000000); self.sp_minhole.setValue(10000)
        h7.addWidget(QLabel("Min object (px):")); h7.addWidget(self.sp_minobj)
        h7.addWidget(QLabel("  Min hole (px):")); h7.addWidget(self.sp_minhole); h7.addStretch()
        g7.addWidget(p7)
        btn7 = _make_run_button("▶  Post-Process")
        btn7.clicked.connect(lambda: self._run_postprocess(btn7))
        g7.addWidget(btn7)
        layout.addWidget(grp7)

        layout.addStretch()
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(_scrollable(inner))

    def _ws(self):
        ws = self.le_ws.text().strip()
        if not ws:
            self._log.append_info("⚠ Set the Workspace folder first.")
        return ws

    def _run_lee(self, btn):
        if not self._require(("Input raster", self.le_lee_in), ("Output raster", self.le_lee_out)):
            return
        from fimsens.raw import leeFilt
        self._run_worker(
            leeFilt,
            self.le_lee_in.text().strip(),
            self.le_lee_out.text().strip(),
            window_size=self.sp_window.value(),
            enl=self.sp_enl.value(),
            enhanced=self.cb_enhanced.isChecked(),
            db_input=self.cb_db.isChecked(),
            run_btn=btn,
        )

    def _run_split(self, btn):
        ws = self._ws()
        if not ws or not self._require(("SAR raster", self.le_rs_in)):
            return
        from fimsens.raw import region_split
        self._run_worker(
            region_split, ws,
            self.le_rs_in.text().strip(),
            self.sp_threshold.value(),
            self.sp_tile_size.value(),
            self.sp_levels.value(),
            run_btn=btn,
        )

    def _run_gamma(self, btn):
        ws = self._ws()
        if not ws or not self._require(("SAR raster", self.le_sar)):
            return
        from fimsens.raw import bimodalFit
        regions = os.path.join(ws, "regions.shp")
        self._run_worker(
            bimodalFit, ws,
            self.le_sar.text().strip(),
            regions,
            self.le_gamma.text().strip(),
            image_type=self.cb_imgtype.currentText(),
            run_btn=btn,
        )

    def _run_outliers(self, btn):
        ws = self._ws()
        if not ws:
            return
        from fimsens.raw import filterOutliers
        threshold_shp = os.path.join(ws, "threshold.shp")
        self._run_worker(
            filterOutliers, ws, threshold_shp,
            self.sp_bc.value(),
            run_btn=btn,
        )

    def _run_idw(self, btn):
        ws = self._ws()
        if not ws or not self._require(("SAR raster", self.le_idw_sar)):
            return
        from fimsens.raw import interpFlood
        threshold_filtered = os.path.join(ws, "threshold_filtered.shp")
        self._run_worker(
            interpFlood, ws, threshold_filtered, "threshold",
            self.le_idw_sar.text().strip(),
            self.sp_idw_power.value(),
            run_btn=btn,
        )

    def _run_grow(self, btn):
        ws = self._ws()
        if not ws or not self._require(("JRC seed", self.le_seed), ("SAR raster", self.le_grow_sar)):
            return
        from fimsens.raw import regionGrow
        self._run_worker(
            regionGrow, ws,
            self.le_seed.text().strip(),
            self.le_grow_sar.text().strip(),
            self.sp_grow_thresh.value(),
            run_btn=btn,
        )

    def _run_postprocess(self, btn):
        if not self._require(("Input raster", self.le_pp_in), ("Output raster", self.le_pp_out)):
            return
        from fimsens.raw import postProcess
        self._run_worker(
            postProcess,
            self.le_pp_in.text().strip(),
            self.le_pp_out.text().strip(),
            minObject=self.sp_minobj.value(),
            minHoleSize=self.sp_minhole.value(),
            run_btn=btn,
        )


# ---------------------------------------------------------------------------
# Tab 3 — Filter
# ---------------------------------------------------------------------------

class FilterTab(BaseTab):
    def __init__(self, log_panel, status_bar):
        super().__init__(log_panel, status_bar)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(10)

        row_ws, self.le_ws = _file_row("Workspace folder", mode="folder")
        layout.addWidget(row_ws)

        # ── 1. Generate objects ────────────────────────────────────────────
        grp1 = QGroupBox("1. Generate Flood Objects")
        g1 = QVBoxLayout(grp1)
        row_fim, self.le_fim = _file_row("Binary FIM raster", "flood_clean.tif")
        g1.addWidget(row_fim)
        p1 = QWidget(); h1 = QHBoxLayout(p1); h1.setContentsMargins(0,0,0,0)
        self.sp_min_front = QSpinBox(); self.sp_min_front.setRange(0,100000); self.sp_min_front.setValue(0)
        self.sp_min_back  = QSpinBox(); self.sp_min_back.setRange(0,100000); self.sp_min_back.setValue(100)
        h1.addWidget(QLabel("Min obj size (px):")); h1.addWidget(self.sp_min_front)
        h1.addWidget(QLabel("  Fill holes ≤ (px):")); h1.addWidget(self.sp_min_back); h1.addStretch()
        g1.addWidget(p1)
        btn1 = _make_run_button("▶  Generate Objects")
        btn1.clicked.connect(lambda: self._run_gen_objects(btn1))
        g1.addWidget(btn1)
        layout.addWidget(grp1)

        # ── 2. Compute attributes ──────────────────────────────────────────
        grp2 = QGroupBox("2. Compute Object Attributes")
        g2 = QVBoxLayout(grp2)
        row_dem, self.le_dem = _file_row("DEM raster (optional)", "dem.tif")
        g2.addWidget(row_dem)
        btn2 = _make_run_button("▶  Compute Attributes")
        btn2.clicked.connect(lambda: self._run_attrs(btn2))
        g2.addWidget(btn2)
        layout.addWidget(grp2)

        # ── 3. Build reference layer ───────────────────────────────────────
        grp3 = QGroupBox("3. Build LWSE Reference Layer")
        g3 = QVBoxLayout(grp3)
        p3 = QWidget(); h3 = QHBoxLayout(p3); h3.setContentsMargins(0,0,0,0)
        self.sp_n_pts = QSpinBox(); self.sp_n_pts.setRange(20,1000); self.sp_n_pts.setValue(200)
        self.sp_knn   = QSpinBox(); self.sp_knn.setRange(1,20); self.sp_knn.setValue(5)
        h3.addWidget(QLabel("Points/segment:")); h3.addWidget(self.sp_n_pts)
        h3.addWidget(QLabel("  k-NN:")); h3.addWidget(self.sp_knn); h3.addStretch()
        g3.addWidget(p3)
        btn3 = _make_run_button("▶  Build Reference")
        btn3.clicked.connect(lambda: self._run_reference(btn3))
        g3.addWidget(btn3)
        layout.addWidget(grp3)

        # ── 4. Depression attributes ───────────────────────────────────────
        grp4 = QGroupBox("4. Add Depression Attributes")
        g4 = QVBoxLayout(grp4)
        p4 = QWidget(); h4 = QHBoxLayout(p4); h4.setContentsMargins(0,0,0,0)
        self.sp_gauss = QDoubleSpinBox(); self.sp_gauss.setRange(0,10); self.sp_gauss.setValue(1.5); self.sp_gauss.setSingleStep(0.5)
        h4.addWidget(QLabel("Gaussian sigma:")); h4.addWidget(self.sp_gauss); h4.addStretch()
        g4.addWidget(p4)
        btn4 = _make_run_button("▶  Add Depression Attrs")
        btn4.clicked.connect(lambda: self._run_depression(btn4))
        g4.addWidget(btn4)
        layout.addWidget(grp4)

        # ── 5. Object filter ───────────────────────────────────────────────
        grp5 = QGroupBox("5. Apply Object Filter Rules")
        g5 = QVBoxLayout(grp5)
        p5 = QWidget(); h5 = QHBoxLayout(p5); h5.setContentsMargins(0,0,0,0)
        self.sp_r0_px = QSpinBox(); self.sp_r0_px.setRange(1,10000); self.sp_r0_px.setValue(50)
        h5.addWidget(QLabel("Rule 0 min size (px):")); h5.addWidget(self.sp_r0_px); h5.addStretch()
        g5.addWidget(p5)
        btn5 = _make_run_button("▶  Run Filter")
        btn5.clicked.connect(lambda: self._run_filter(btn5))
        g5.addWidget(btn5)
        layout.addWidget(grp5)

        layout.addStretch()
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(_scrollable(inner))

        self._fim_objs = None
        self._fim_meta = None
        self._attrs_df = None
        self._ref_df   = None

    def _ws(self):
        ws = self.le_ws.text().strip()
        if not ws:
            self._log.append_info("⚠ Set the Workspace folder first.")
        return ws

    def _run_gen_objects(self, btn):
        ws = self._ws()
        if not ws or not self._require(("Binary FIM", self.le_fim)):
            return
        def _fn():
            from fimsens.filter import generate_flood_objects
            out = os.path.join(ws, "fim_objects.tif")
            self._fim_objs, self._fim_meta, n = generate_flood_objects(
                self.le_fim.text().strip(), out,
                min_front=self.sp_min_front.value(),
                min_back=self.sp_min_back.value(),
            )
            print(f"Generated {n} flood objects → {out}")
        self._run_worker(_fn, run_btn=btn)

    def _run_attrs(self, btn):
        ws = self._ws()
        if not ws:
            return
        if self._fim_objs is None:
            self._log.append_info("⚠ Run Step 1 first.")
            return
        dem = self.le_dem.text().strip() or None
        def _fn():
            from fimsens.filter import compute_object_attributes
            self._attrs_df = compute_object_attributes(
                self._fim_objs, self._fim_meta, dem_path=dem
            )
            print(f"Attributes computed for {len(self._attrs_df)} objects.")
        self._run_worker(_fn, run_btn=btn)

    def _run_reference(self, btn):
        ws = self._ws()
        if not ws:
            return
        if self._fim_objs is None or self._attrs_df is None:
            self._log.append_info("⚠ Run Steps 1–2 first.")
            return
        dem = self.le_dem.text().strip()
        if not dem:
            self._log.append_info("⚠ DEM raster required for reference layer.")
            return
        def _fn():
            import numpy as np
            import rasterio
            from fimsens.filter import ObjectFilter, build_reference_layer
            # save large objects raster
            filt_tmp = ObjectFilter(self._attrs_df)
            filt_tmp.rule0_remove_small(50).rule1_identify_large()
            large_path = os.path.join(ws, "large_objects.tif")
            filt_tmp.save_large_objects(self._fim_objs, large_path, self._fim_meta)
            self._ref_df = build_reference_layer(large_path, dem, ws,
                                                  n_points=self.sp_n_pts.value(),
                                                  k_neighbors=self.sp_knn.value())
            print(f"Reference layer built: {len(self._ref_df)} segments.")
        self._run_worker(_fn, run_btn=btn)

    def _run_depression(self, btn):
        ws = self._ws()
        if not ws:
            return
        if self._attrs_df is None or self._fim_objs is None:
            self._log.append_info("⚠ Run Steps 1–2 first.")
            return
        dem = self.le_dem.text().strip()
        if not dem:
            self._log.append_info("⚠ DEM raster required.")
            return
        def _fn():
            import rasterio
            from fimsens.filter import add_depression_attributes
            with rasterio.open(dem) as src:
                pix_area = abs(src.transform.a * src.transform.e)
            self._attrs_df = add_depression_attributes(
                self._attrs_df, self._fim_objs, dem, pix_area,
                output_dir=ws, gaussian_sigma=self.sp_gauss.value()
            )
            print("Depression attributes added.")
        self._run_worker(_fn, run_btn=btn)

    def _run_filter(self, btn):
        ws = self._ws()
        if not ws:
            return
        if self._attrs_df is None:
            self._log.append_info("⚠ Run Steps 1–2 first.")
            return
        def _fn():
            from fimsens.filter import ObjectFilter
            filt = ObjectFilter(self._attrs_df)
            filt.rule0_remove_small(self.sp_r0_px.value()).rule1_identify_large()
            filt.rule2_depression_water()
            if self._ref_df is not None:
                filt.rule3_filter_by_delta_lwse(self._ref_df)
            filt.rule4_filter_by_distance(reference_df=self._ref_df)
            filt.summary()
            csv_path, shp_path = filt.save_results(ws)
            print(f"Results saved: {csv_path}")
        self._run_worker(_fn, run_btn=btn)


# ---------------------------------------------------------------------------
# Tab 4 — Grow / Depth
# ---------------------------------------------------------------------------

class GrowTab(BaseTab):
    def __init__(self, log_panel, status_bar):
        super().__init__(log_panel, status_bar)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(10)

        row_ws, self.le_ws = _file_row("Workspace folder", mode="folder")
        layout.addWidget(row_ws)

        # ── 1. LWSE layers ─────────────────────────────────────────────────
        grp1 = QGroupBox("1. Generate LWSE Layers")
        g1 = QVBoxLayout(grp1)
        row_flood, self.le_flood = _file_row("Binary flood raster", "flood_clean.tif",
                                              hint="flood = 1, background = 0")
        row_dem, self.le_dem = _file_row("DEM raster", "dem.tif")
        g1.addWidget(row_flood); g1.addWidget(row_dem)

        p1 = QWidget(); h1 = QHBoxLayout(p1); h1.setContentsMargins(0,0,0,0)
        self.sp_n_pts   = QSpinBox(); self.sp_n_pts.setRange(20,1000); self.sp_n_pts.setValue(200)
        self.sp_min_px  = QSpinBox(); self.sp_min_px.setRange(1,1000); self.sp_min_px.setValue(20)
        self.sp_knn     = QSpinBox(); self.sp_knn.setRange(1,30); self.sp_knn.setValue(5)
        h1.addWidget(QLabel("Points/seg:")); h1.addWidget(self.sp_n_pts)
        h1.addWidget(QLabel("  Min px:")); h1.addWidget(self.sp_min_px)
        h1.addWidget(QLabel("  k-NN:")); h1.addWidget(self.sp_knn); h1.addStretch()
        g1.addWidget(p1)

        btn1 = _make_run_button("▶  Generate LWSE Layers")
        btn1.clicked.connect(lambda: self._run_lwse(btn1))
        g1.addWidget(btn1)
        layout.addWidget(grp1)

        # ── 2. Depth + grow ────────────────────────────────────────────────
        grp2 = QGroupBox("2. IDW Flood Depth + Region Grow")
        g2 = QVBoxLayout(grp2)
        row_centroid, self.le_centroid = _file_row("Centroid LWSE shapefile",
                                                    "centroid_elev.shp", mode="open_shp",
                                                    hint="Output of Step 1 (or edited)")
        row_dem2, self.le_dem2 = _file_row("DEM raster", "dem.tif")
        row_seed, self.le_seed = _file_row("Seed flood raster", "flood_clean.tif")
        g2.addWidget(row_centroid); g2.addWidget(row_dem2); g2.addWidget(row_seed)

        p2 = QWidget(); h2 = QHBoxLayout(p2); h2.setContentsMargins(0,0,0,0)
        lbl_f = QLabel("LWSE field:"); lbl_f.setFixedWidth(90)
        self.le_field = QLineEdit("LWSE_new")
        self.sp_maxpts = QSpinBox(); self.sp_maxpts.setRange(1,100); self.sp_maxpts.setValue(12)
        self.sp_power  = QDoubleSpinBox(); self.sp_power.setRange(0.5,10); self.sp_power.setValue(3.0); self.sp_power.setSingleStep(0.5)
        self.sp_flood_class = QSpinBox(); self.sp_flood_class.setRange(1,255); self.sp_flood_class.setValue(1)
        h2.addWidget(lbl_f); h2.addWidget(self.le_field)
        h2.addWidget(QLabel("  Max pts:")); h2.addWidget(self.sp_maxpts)
        h2.addWidget(QLabel("  Power:")); h2.addWidget(self.sp_power)
        h2.addWidget(QLabel("  Seed class:")); h2.addWidget(self.sp_flood_class)
        h2.addStretch()
        g2.addWidget(p2)

        btn2 = _make_run_button("▶  Run IDW Depth + Grow")
        btn2.clicked.connect(lambda: self._run_depth(btn2))
        g2.addWidget(btn2)
        layout.addWidget(grp2)

        # ── 3. Polygonize ──────────────────────────────────────────────────
        grp3 = QGroupBox("3. Polygonize Flood Map (optional)")
        g3 = QVBoxLayout(grp3)
        row_cls, self.le_class_raster = _file_row("Classification raster", "FloodClass.tif")
        g3.addWidget(row_cls)
        btn3 = _make_run_button("▶  Polygonize")
        btn3.clicked.connect(lambda: self._run_polygonize(btn3))
        g3.addWidget(btn3)
        layout.addWidget(grp3)

        layout.addStretch()
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(_scrollable(inner))

    def _ws(self):
        ws = self.le_ws.text().strip()
        if not ws:
            self._log.append_info("⚠ Set the Workspace folder first.")
        return ws

    def _run_lwse(self, btn):
        ws = self._ws()
        if not ws or not self._require(("Flood raster", self.le_flood), ("DEM", self.le_dem)):
            return
        from fimsens.grow import generate_lwse_layers
        bnd = os.path.join(ws, "boundary_elev.shp")
        cnt = os.path.join(ws, "centroid_elev.shp")
        self._run_worker(
            generate_lwse_layers,
            self.le_flood.text().strip(),
            self.le_dem.text().strip(),
            self.sp_n_pts.value(),
            self.sp_min_px.value(),
            self.sp_knn.value(),
            bnd, cnt,
            run_btn=btn,
        )

    def _run_depth(self, btn):
        ws = self._ws()
        if not ws or not self._require(
            ("Centroid LWSE shapefile", self.le_centroid),
            ("DEM raster", self.le_dem2),
            ("Seed raster", self.le_seed),
        ):
            return
        from fimsens.grow import idw2Flood1, raster_to_polygons
        out_depth = os.path.join(ws, "FloodDepth.tif")
        out_class = os.path.join(ws, "FloodClass.tif")
        idw_tmp   = os.path.join(ws, "idw_temp.tif")
        shp_tmp   = os.path.join(ws, "reprojected.shp")
        aligned   = os.path.join(ws, "aligned_seed.tif")
        self._run_worker(
            idw2Flood1,
            self.le_centroid.text().strip(),
            self.le_field.text().strip(),
            self.le_dem2.text().strip(),
            self.le_seed.text().strip(),
            idw_tmp, out_depth, out_class, shp_tmp, aligned,
            self.sp_maxpts.value(),
            self.sp_power.value(),
            self.sp_flood_class.value(),
            run_btn=btn,
        )
        # Update class raster field for polygonize convenience
        self.le_class_raster.setText(out_class)

    def _run_polygonize(self, btn):
        ws = self._ws()
        if not ws or not self._require(("Classification raster", self.le_class_raster)):
            return
        from fimsens.grow import raster_to_polygons
        out_poly = os.path.join(ws, "flood_classified_polygons.shp")
        self._run_worker(
            raster_to_polygons,
            self.le_class_raster.text().strip(),
            out_poly,
            run_btn=btn,
        )


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FIMsens  —  Flood Inundation Mapping Toolset")
        self.resize(860, 780)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready.")

        # Log panel (shared)
        self._log = LogPanel()

        # Tabs
        tabs = QTabWidget()
        tabs.addTab(DownloadTab(self._log, self._status),  "📡  Download")
        tabs.addTab(RawTab(self._log, self._status),       "🌊  Raw FIM")
        tabs.addTab(FilterTab(self._log, self._status),    "🔍  Filter")
        tabs.addTab(GrowTab(self._log, self._status),      "📈  Grow / Depth")

        # Splitter: tabs on top, log panel on bottom
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(tabs)

        log_container = QWidget()
        lv = QVBoxLayout(log_container)
        lv.setContentsMargins(4, 0, 4, 4)
        hdr = QWidget()
        hh = QHBoxLayout(hdr); hh.setContentsMargins(0,0,0,0)
        hh.addWidget(QLabel("  Output Log"))
        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(60)
        btn_clear.clicked.connect(self._log.clear)
        hh.addStretch(); hh.addWidget(btn_clear)
        lv.addWidget(hdr)
        lv.addWidget(self._log)
        splitter.addWidget(log_container)
        splitter.setSizes([520, 220])

        self.setCentralWidget(splitter)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("FIMsens")
    app.setStyleSheet(STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
