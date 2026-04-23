"""
ui/detail_panel.py
Right-hand file detail panel widget.
"""

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import metadata_store
from core.dicom_handler import raster_path_for
from core.folder_store import FolderStore
from core.paths import LABELED_DIR, RASTER_DIR
from core.status import STATUS_COLORS, resolve_status

_RASTER_DIR  = RASTER_DIR
_LABELED_DIR = LABELED_DIR

_DETAIL_KEYS = ("File", "Folder", "Status", "Windowing", "Raster Export", "Annotation")


class DetailPanel(QWidget):
    open_viewer_requested = pyqtSignal(Path)

    def __init__(
        self,
        parent: QWidget | None     = None,
        store:  FolderStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._dcm_path: Path | None        = None
        self._store:    FolderStore | None = store
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        section = QLabel("FILE DETAILS")
        section.setStyleSheet(
            "color: #5A7FA8; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;"
        )
        layout.addWidget(section)

        info_frame = QFrame()
        info_frame.setStyleSheet(
            "background-color: #0A0F1A; border: 1px solid #1E2A3A; border-radius: 4px;"
        )
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(16, 14, 16, 14)
        info_layout.setSpacing(8)

        self._rows: dict[str, QLabel] = {}
        for key in _DETAIL_KEYS:
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(12)
            k_lbl = QLabel(key)
            k_lbl.setFixedWidth(120)
            k_lbl.setStyleSheet("color: #5A7FA8; font-size: 11px;")
            v_lbl = QLabel("-")
            v_lbl.setStyleSheet("color: #D4D8DE; font-size: 11px;")
            v_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v_lbl.setWordWrap(True)
            row_l.addWidget(k_lbl)
            row_l.addWidget(v_lbl, stretch=1)
            info_layout.addWidget(row_w)
            self._rows[key] = v_lbl

        layout.addWidget(info_frame)
        layout.addSpacing(8)

        self._open_btn = QPushButton("Open in DICOM Viewer")
        self._open_btn.setObjectName("primaryButton")
        self._open_btn.setFixedHeight(44)
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._on_open_clicked)
        layout.addWidget(self._open_btn)

        layout.addStretch()

        self._hint = QLabel("Select a file from the list to get started.")
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setStyleSheet("color: #2E3A50; font-size: 13px;")
        layout.addWidget(self._hint)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def load_file(self, dcm_path: Path, store: FolderStore | None = None) -> None:
        self._dcm_path = dcm_path
        if store is not None:
            self._store = store

        status = resolve_status(dcm_path, self._store)
        color  = STATUS_COLORS[status]

        folder_name = "-"
        if self._store:
            f = self._store.folder_for_stem(dcm_path.stem)
            if f:
                folder_name = f.name
                if f.mandatory_labels:
                    folder_name += f"  ({', '.join(f.mandatory_labels)})"

        saved   = metadata_store.load_windowing(dcm_path)
        wc_ww   = f"WC={saved.center:.0f}  WW={saved.width:.0f}" if saved else "Not set"
        jpg     = raster_path_for(dcm_path, _RASTER_DIR)
        labeled = _LABELED_DIR / (dcm_path.stem + ".json")

        self._rows["File"].setText(dcm_path.name)
        self._rows["Folder"].setText(folder_name)
        self._rows["Status"].setText(status)
        self._rows["Status"].setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600;"
        )
        self._rows["Windowing"].setText(wc_ww)
        self._rows["Raster Export"].setText(
            str(jpg) if jpg.exists() else "Not exported yet"
        )
        self._rows["Annotation"].setText(
            str(labeled) if labeled.exists() else "Not annotated yet"
        )

        self._open_btn.setEnabled(True)
        self._hint.setVisible(False)

    def refresh(self) -> None:
        if self._dcm_path:
            self.load_file(self._dcm_path)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_open_clicked(self) -> None:
        if self._dcm_path:
            self.open_viewer_requested.emit(self._dcm_path)