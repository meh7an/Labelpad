"""
ui/dicom_viewer.py
Medical-grade DICOM viewer with live Window Center / Window Width controls.

Workflow:
    1. Caller instantiates DicomViewer(dicom_image, parent).
    2. User adjusts WC/WW sliders -- preview updates in real time (debounced).
    3. On "Confirm & Open in LabelMe":
         a. WC/WW saved to Data/
         b. JPG exported to Raster/
         c. confirmed signal emitted with final WindowingParams
    4. Caller (MainWindow) receives the signal and triggers the labelme bridge.
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.dicom_handler import (
    DicomImage,
    WindowingParams,
    apply_windowing,
    export_jpg,
    raster_path_for,
    suggest_slider_range,
)
from core import metadata_store
from core.paths import RASTER_DIR
from ui.error_dialog import AppDialog
from ui.label_overlay import LabelOverlay, load_label_overlay

log = logging.getLogger(__name__)

_RASTER_DIR = RASTER_DIR
_PREVIEW_DEBOUNCE_MS = 60


# ---------------------------------------------------------------------------
# Internal widget: labelled slider row
# ---------------------------------------------------------------------------

class _SliderRow(QWidget):
    """
    A horizontal row containing a label, a QSlider, and a QDoubleSpinBox.
    The slider and spinbox are kept in sync bidirectionally.
    """

    value_changed = pyqtSignal(float)

    def __init__(self, label, minimum, maximum, value, parent=None):
        super().__init__(parent)

        self._scale = 10.0
        self._min = minimum
        self._max = maximum

        self._label = QLabel(label)
        self._label.setStyleSheet("color: #8A98AA; font-size: 11px;")

        self._spin = QDoubleSpinBox()
        self._spin.setMinimum(minimum)
        self._spin.setMaximum(maximum)
        self._spin.setValue(value)
        self._spin.setDecimals(1)
        self._spin.setSingleStep(10.0)
        self._spin.setFixedWidth(100)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setMinimum(int(minimum * self._scale))
        self._slider.setMaximum(int(maximum * self._scale))
        self._slider.setValue(int(value * self._scale))
        self._slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        top_row.addWidget(self._label)
        top_row.addStretch()
        top_row.addWidget(self._spin)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addLayout(top_row)
        col.addWidget(self._slider)

        self._syncing = False
        self._slider.valueChanged.connect(self._on_slider_moved)
        self._spin.valueChanged.connect(self._on_spin_changed)

    def value(self):
        return self._spin.value()

    def set_value(self, v):
        self._syncing = True
        clamped = max(self._min, min(self._max, v))
        self._slider.setValue(int(clamped * self._scale))
        self._spin.setValue(clamped)
        self._syncing = False

    def set_range(self, minimum, maximum):
        self._min = minimum
        self._max = maximum
        self._slider.setMinimum(int(minimum * self._scale))
        self._slider.setMaximum(int(maximum * self._scale))
        self._spin.setMinimum(minimum)
        self._spin.setMaximum(maximum)

    def _on_slider_moved(self, int_val):
        if self._syncing:
            return
        self._syncing = True
        real_val = int_val / self._scale
        self._spin.setValue(real_val)
        self._syncing = False
        self.value_changed.emit(real_val)

    def _on_spin_changed(self, real_val):
        if self._syncing:
            return
        self._syncing = True
        self._slider.setValue(int(real_val * self._scale))
        self._syncing = False
        self.value_changed.emit(real_val)


# ---------------------------------------------------------------------------
# Metadata panel
# ---------------------------------------------------------------------------

class _MetadataPanel(QFrame):
    """Compact read-only panel showing DICOM file metadata."""

    def __init__(self, dicom, parent=None):
        super().__init__(parent)
        self.setObjectName("metadataPanel")
        self.setFrameShape(QFrame.StyledPanel)

        from PyQt5.QtWidgets import QGridLayout
        grid = QGridLayout(self)
        grid.setSpacing(6)
        grid.setContentsMargins(12, 10, 12, 10)

        fields = [
            ("File",        dicom.path.name),
            ("Patient ID",  dicom.patient_id),
            ("Modality",    dicom.modality),
            ("Dimensions",  f"{dicom.pixel_array.shape[1]} x {dicom.pixel_array.shape[0]} px"),
            ("Pixel Range", f"{dicom.pixel_min:.0f}  -  {dicom.pixel_max:.0f}"),
        ]

        for row_idx, (key, val) in enumerate(fields):
            key_lbl = QLabel(key)
            key_lbl.setStyleSheet("color: #5A7FA8; font-size: 11px;")

            val_lbl = QLabel(val)
            val_lbl.setStyleSheet("color: #D4D8DE; font-size: 11px;")
            val_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)

            grid.addWidget(key_lbl, row_idx, 0)
            grid.addWidget(val_lbl, row_idx, 1)

        grid.setColumnStretch(1, 1)


# ---------------------------------------------------------------------------
# Floating legend HUD
# ---------------------------------------------------------------------------

class _LegendHud(QFrame):
    """
    Floating colour-legend overlay parented to the image panel.

    Displays one labelled colour swatch per annotation label and provides a
    Hide / Show toggle that collapses the body while keeping the header
    visible.  Visibility is managed by the parent DicomViewer via populate()
    and reposition().
    """

    _SWATCH_PX    = 12
    _CORNER_MARGIN = 14

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("legendHud")
        self.setStyleSheet(
            "QFrame#legendHud {"
            "  background-color: rgba(13, 19, 32, 215);"
            "  border: 1px solid #1E2A3A;"
            "  border-radius: 6px;"
            "}"
            "QLabel { background: transparent; }"
            "QWidget { background: transparent; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(6)

        # Header row
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(8)

        title = QLabel("LABELS")
        title.setStyleSheet(
            "color: #5A7FA8; font-size: 10px;"
            "font-weight: 600; letter-spacing: 1.5px;"
        )
        hdr.addWidget(title)
        hdr.addStretch()

        self._btn = QPushButton("Hide")
        self._btn.setFixedSize(40, 18)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setStyleSheet(
            "QPushButton {"
            "  color: #5A7FA8; font-size: 10px;"
            "  background: #1A2436; border: 1px solid #2A3A4A;"
            "  border-radius: 3px; padding: 0;"
            "}"
            "QPushButton:hover { color: #8AB0D0; background: #243044; }"
        )
        self._btn.clicked.connect(self._toggle)
        hdr.addWidget(self._btn)

        outer.addLayout(hdr)

        # Collapsible body
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 2, 0, 0)
        self._body_layout.setSpacing(5)
        outer.addWidget(self._body)

        self._expanded = True
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(self, color_map: dict) -> None:
        """
        Rebuild legend rows from a label → RGB tuple mapping.
        Hides the HUD entirely when color_map is empty.
        """
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not color_map:
            self.hide()
            return

        for label, (r, g, b) in sorted(color_map.items()):
            row = QWidget()
            rl  = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)

            swatch = QLabel()
            swatch.setFixedSize(self._SWATCH_PX, self._SWATCH_PX)
            swatch.setStyleSheet(
                f"background-color: rgb({r},{g},{b});"
                "border-radius: 2px;"
                "border: 1px solid rgba(255,255,255,55);"
            )
            rl.addWidget(swatch)

            name_lbl = QLabel(label)
            name_lbl.setStyleSheet("color: #D4D8DE; font-size: 11px;")
            rl.addWidget(name_lbl)
            rl.addStretch()

            self._body_layout.addWidget(row)

        self.show()
        self.raise_()  # ensure HUD renders above sibling layout widgets
        self.adjustSize()

    def reposition(self, panel: QWidget) -> None:
        """
        Snap HUD to the top-right corner of panel.

        The HUD is parented to the dialog (not panel), so self.move() is in
        dialog-local coordinates.  panel.x() + panel.width() gives the right
        edge of the image area in that same coordinate space, which keeps the
        HUD correctly anchored regardless of how narrow the image panel is.
        """
        self._panel = panel  # cache so _toggle can reposition without an argument
        m = self._CORNER_MARGIN
        x = panel.x() + panel.width() - self.width() - m
        self.move(x, m)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._btn.setText("Hide" if self._expanded else "Show")
        self.adjustSize()
        if hasattr(self, "_panel"):
            self.reposition(self._panel)
        self.raise_()


# ---------------------------------------------------------------------------
# Main viewer dialog
# ---------------------------------------------------------------------------

class DicomViewer(QDialog):
    """
    Full-screen DICOM viewer dialog.

    Signals:
        confirmed(WindowingParams): Emitted when the user clicks
            "Confirm & Open in LabelMe" after a successful export.
    """

    confirmed = pyqtSignal(object)

    def __init__(self, dicom, parent=None):
        super().__init__(parent)
        self._dicom           = dicom
        self._current_params  = self._resolve_initial_params()
        self._overlay: Optional[LabelOverlay] = load_label_overlay(dicom.path)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._refresh_preview)

        self._build_ui()

        if self._overlay:
            QTimer.singleShot(50, self._show_legend_hud)

        self._refresh_preview()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.setWindowTitle(f"Labelpad — {self._dicom.path.name}")
        self.setMinimumSize(1100, 780)
        self.setModal(True)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._image_panel = self._build_image_panel()
        root.addWidget(self._image_panel, stretch=3)
        root.addWidget(self._build_control_panel(), stretch=0)

    def _build_image_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("imagePanel")
        panel.setStyleSheet("background-color: #080C12;")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QWidget()
        header.setFixedHeight(40)
        header.setStyleSheet("background-color: #0D1320; border-bottom: 1px solid #1E2A3A;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)

        title = QLabel("Image Preview")
        title.setStyleSheet(
            "color: #5A7FA8; font-size: 11px; font-weight: 600; letter-spacing: 1px;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()

        self._zoom_label = QLabel("Fit")
        self._zoom_label.setStyleSheet("color: #3E4A5C; font-size: 11px;")
        header_layout.addWidget(self._zoom_label)

        layout.addWidget(header)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._image_label.setStyleSheet("background-color: #080C12;")
        layout.addWidget(self._image_label, stretch=1)

        # Legend HUD is parented to the dialog (not panel) so that self.move()
        # inside _LegendHud operates in dialog-local coordinates, keeping the
        # HUD anchored correctly regardless of image panel width.
        self._legend_hud = _LegendHud(self)

        return panel

    def _build_control_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("controlPanel")
        panel.setFixedWidth(320)
        panel.setStyleSheet(
            "background-color: #0D1320;"
            "border-left: 1px solid #1E2A3A;"
        )

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 24, 20, 20)
        layout.setSpacing(20)

        app_title = QLabel("Labelpad")
        app_title.setStyleSheet("color: #2A7AD4; font-size: 16px; font-weight: 700;")
        layout.addWidget(app_title)

        layout.addWidget(self._make_divider())

        section_meta = QLabel("FILE INFO")
        section_meta.setStyleSheet(
            "color: #5A7FA8; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;"
        )
        layout.addWidget(section_meta)
        layout.addWidget(_MetadataPanel(self._dicom))

        layout.addWidget(self._make_divider())

        section_wnd = QLabel("WINDOWING")
        section_wnd.setStyleSheet(
            "color: #5A7FA8; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;"
        )
        layout.addWidget(section_wnd)

        wc_min, wc_max, ww_min, ww_max = suggest_slider_range(self._dicom)

        self._wc_slider = _SliderRow(
            "Window Center (WC)",
            minimum=wc_min, maximum=wc_max,
            value=self._current_params.center,
        )
        self._ww_slider = _SliderRow(
            "Window Width  (WW)",
            minimum=ww_min, maximum=ww_max,
            value=self._current_params.width,
        )

        self._wc_slider.value_changed.connect(self._on_params_changed)
        self._ww_slider.value_changed.connect(self._on_params_changed)

        layout.addWidget(self._wc_slider)
        layout.addWidget(self._ww_slider)

        reset_btn = QPushButton("Reset to DICOM Defaults")
        reset_btn.setObjectName("resetButton")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_to_defaults)
        layout.addWidget(reset_btn)

        layout.addWidget(self._make_divider())

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #3E8E41; font-size: 11px;")
        layout.addWidget(self._status_label)

        layout.addStretch()

        confirm_btn = QPushButton("Confirm and Open in LabelMe")
        confirm_btn.setObjectName("primaryButton")
        confirm_btn.setFixedHeight(44)
        confirm_btn.setCursor(Qt.PointingHandCursor)
        confirm_btn.clicked.connect(self._on_confirm)
        layout.addWidget(confirm_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

        return panel

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #1E2A3A;")
        return line

    def _show_legend_hud(self) -> None:
        """Populate and position the legend HUD after the initial display delay."""
        self._legend_hud.populate(self._overlay.color_map)
        self._legend_hud.reposition(self._image_panel)

    def _resolve_initial_params(self) -> WindowingParams:
        saved = metadata_store.load_windowing(self._dicom.path)
        if saved is not None:
            return saved
        return self._dicom.default_windowing

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_params_changed(self, _):
        self._current_params = WindowingParams(
            center=self._wc_slider.value(),
            width=max(1.0, self._ww_slider.value()),
        )
        self._debounce_timer.start()

    def _refresh_preview(self):
        uint8 = apply_windowing(self._dicom.pixel_array, self._current_params)
        h, w  = uint8.shape

        rgb = np.stack([uint8, uint8, uint8], axis=-1)

        if self._overlay:
            rgb = self._overlay.draw(rgb)

        q_img  = QImage(rgb.tobytes(), w, h, w * 3, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img)

        canvas_size = self._image_label.size()
        scaled = pixmap.scaled(canvas_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image_label.setPixmap(scaled)

        scale_pct = int(scaled.width() / w * 100) if w > 0 else 100
        self._zoom_label.setText(f"{scale_pct}%")

    def _reset_to_defaults(self):
        defaults = self._dicom.default_windowing
        self._wc_slider.set_value(defaults.center)
        self._ww_slider.set_value(defaults.width)
        self._current_params = defaults
        self._refresh_preview()
        self._set_status("Reset to DICOM defaults.", success=True)

    def _on_confirm(self):
        params   = self._current_params
        dcm_path = self._dicom.path

        try:
            metadata_store.save_windowing(dcm_path, params)
            jpg_path = raster_path_for(dcm_path, _RASTER_DIR)
            export_jpg(self._dicom.pixel_array, params, jpg_path)
        except OSError as exc:
            self._set_status(f"Export failed: {exc}", success=False)
            log.error("Export failed for '%s': %s", dcm_path.name, exc)
            AppDialog.error(self, "Export Failed", f"Could not write files to disk.\n\n{exc}", exc=exc)
            return

        log.info("Confirmed: '%s' exported to %s", dcm_path.name, jpg_path)
        self.confirmed.emit(params)
        self.accept()

    def _set_status(self, message: str, success: bool = True) -> None:
        color = "#3E8E41" if success else "#A83040"
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._status_label.setText(message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._debounce_timer.start()
        if self._legend_hud.isVisible():
            self._legend_hud.reposition(self._image_panel)
            self._legend_hud.raise_()