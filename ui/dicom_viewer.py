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
from ui.label_overlay import load_label_overlay

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

        # Top row: label left, spinbox right
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        top_row.addWidget(self._label)
        top_row.addStretch()
        top_row.addWidget(self._spin)

        # Stack: top row, then full-width slider below
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
            ("File",       dicom.path.name),
            ("Patient ID", dicom.patient_id),
            ("Modality",   dicom.modality),
            ("Dimensions", f"{dicom.pixel_array.shape[1]} x {dicom.pixel_array.shape[0]} px"),
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
        self._dicom = dicom
        self._current_params = self._resolve_initial_params()

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._refresh_preview)

        self._build_ui()
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

        root.addWidget(self._build_image_panel(), stretch=3)
        root.addWidget(self._build_control_panel(), stretch=0)

    def _build_image_panel(self):
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
        title.setStyleSheet("color: #5A7FA8; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
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

        return panel

    def _build_control_panel(self):
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
        app_title.setStyleSheet(
            "color: #2A7AD4; font-size: 16px; font-weight: 700;"
        )
        layout.addWidget(app_title)

        layout.addWidget(self._make_divider())

        section_meta = QLabel("FILE INFO")
        section_meta.setStyleSheet("color: #5A7FA8; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;")
        layout.addWidget(section_meta)
        layout.addWidget(_MetadataPanel(self._dicom))

        layout.addWidget(self._make_divider())

        section_wnd = QLabel("WINDOWING")
        section_wnd.setStyleSheet("color: #5A7FA8; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;")
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

        confirm_btn = QPushButton("Confirm & Open in LabelMe")
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

    def _make_divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #1E2A3A;")
        return line

    def _resolve_initial_params(self):
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
        h, w = uint8.shape

        # Convert greyscale to RGB so we can paint coloured polygons on top
        rgb = np.stack([uint8, uint8, uint8], axis=-1)

        # Draw label overlay if annotations exist
        overlay = load_label_overlay(self._dicom.path)
        if overlay:
            rgb = overlay.draw(rgb)

        q_img = QImage(rgb.tobytes(), w, h, w * 3, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img)

        canvas_size = self._image_label.size()
        scaled = pixmap.scaled(
            canvas_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
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
        params = self._current_params
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

    def _set_status(self, message, success=True):
        color = "#3E8E41" if success else "#A83040"
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._status_label.setText(message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._debounce_timer.start()