"""
ui/main_window.py
Application shell — file browser, status tracking, and workflow orchestration.
"""

import logging
import shutil
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QColor, QKeySequence
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressDialog,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core.dcmpack import (
    DcmPackCorruptError,
    DcmPackPasswordError,
    DcmPackVersionError,
    ImportResult,
    extract_pack,
    open_pack,
    peek_is_password_protected,
    read_manifest,
)
from core.dicom_handler import load_dicom, DicomReadError, raster_path_for
from core import metadata_store
from core.paths import UNLABELED_DIR, RASTER_DIR, LABELED_DIR
from ui.dicom_viewer import DicomViewer
from ui.error_dialog import AppDialog
from ui.pack_info_dialog import show_pack_info
from ui.password_dialog import ask_password

log = logging.getLogger(__name__)

_UNLABELED_DIR = UNLABELED_DIR
_RASTER_DIR    = RASTER_DIR
_LABELED_DIR   = LABELED_DIR


# ---------------------------------------------------------------------------
# File status
# ---------------------------------------------------------------------------

class FileStatus:
    UNLABELED    = "Unlabeled"
    RASTER_READY = "Raster Ready"
    LABELED      = "Labeled"


STATUS_COLORS = {
    FileStatus.UNLABELED:    "#5A7FA8",
    FileStatus.RASTER_READY: "#C8922A",
    FileStatus.LABELED:      "#3E8E41",
}


def _resolve_status(dcm_path: Path) -> str:
    if (_LABELED_DIR / (dcm_path.stem + ".json")).exists():
        return FileStatus.LABELED
    if raster_path_for(dcm_path, _RASTER_DIR).exists():
        return FileStatus.RASTER_READY
    return FileStatus.UNLABELED


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _DicomLoader(QObject):
    finished = pyqtSignal(object)
    failed   = pyqtSignal(str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:
        try:
            self.finished.emit(load_dicom(self._path))
        except DicomReadError as exc:
            self.failed.emit(str(exc))


class _PackExtractor(QObject):
    """
    Runs extract_pack() on a background thread and marshals the result
    back to the main thread via Qt signals.

    Mirrors the _DicomLoader pattern: constructed on the main thread,
    moved to a QThread, connected via signals, never touched directly
    after start() is called on the thread.
    """

    finished = pyqtSignal(object)  # ImportResult
    failed   = pyqtSignal(str)     # human-readable error message

    def __init__(self, path: Path, password: str | None) -> None:
        super().__init__()
        self._path     = path
        self._password = password

    def run(self) -> None:
        try:
            result = extract_pack(self._path, self._password, on_conflict="skip")
            self.finished.emit(result)
        except (DcmPackPasswordError, DcmPackCorruptError, DcmPackVersionError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error during extraction: {exc}")


# ---------------------------------------------------------------------------
# File list item factory
# ---------------------------------------------------------------------------

def _make_list_item(dcm_path: Path) -> QListWidgetItem:
    status = _resolve_status(dcm_path)
    item = QListWidgetItem(dcm_path.name)
    item.setData(Qt.UserRole,     dcm_path)
    item.setData(Qt.UserRole + 1, status)
    item.setForeground(QColor(STATUS_COLORS[status]))
    item.setToolTip(f"Status: {status}\nPath: {dcm_path}")
    return item


# ---------------------------------------------------------------------------
# Detail panel (right side)
# ---------------------------------------------------------------------------

class _DetailPanel(QWidget):
    open_viewer_requested = pyqtSignal(Path)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dcm_path: Path | None = None
        self._build_ui()

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
        for key in ("File", "Status", "Windowing", "Raster Export", "Annotation"):
            row_w  = QWidget()
            row_l  = QHBoxLayout(row_w)
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

    def load_file(self, dcm_path: Path) -> None:
        self._dcm_path = dcm_path
        status = _resolve_status(dcm_path)
        color  = STATUS_COLORS[status]

        saved   = metadata_store.load_windowing(dcm_path)
        wc_ww   = f"WC={saved.center:.0f}  WW={saved.width:.0f}" if saved else "Not set"
        jpg     = raster_path_for(dcm_path, _RASTER_DIR)
        labeled = _LABELED_DIR / (dcm_path.stem + ".json")

        self._rows["File"].setText(dcm_path.name)
        self._rows["Status"].setText(status)
        self._rows["Status"].setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")
        self._rows["Windowing"].setText(wc_ww)
        self._rows["Raster Export"].setText(str(jpg) if jpg.exists() else "Not exported yet")
        self._rows["Annotation"].setText(str(labeled) if labeled.exists() else "Not annotated yet")

        self._open_btn.setEnabled(True)
        self._hint.setVisible(False)

    def refresh(self) -> None:
        if self._dcm_path:
            self.load_file(self._dcm_path)

    def _on_open_clicked(self) -> None:
        if self._dcm_path:
            self.open_viewer_requested.emit(self._dcm_path)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self._loader_thread:    QThread | None = None
        self._extractor_thread: QThread | None = None
        self._active_session = None
        self._build_ui()
        self._setup_shortcuts()
        self._scan_unlabeled()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("Labelpad")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 820)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #1E2A3A; }")
        splitter.addWidget(self._build_file_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([340, 760])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        root.addWidget(splitter, stretch=1)

        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "background-color: #080D14; color: #5A7FA8;"
            "border-top: 1px solid #1E2A3A; font-size: 11px;"
        )
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setFixedHeight(56)
        header.setStyleSheet(
            "background-color: #0A0F1A; border-bottom: 1px solid #1E2A3A;"
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(10)

        app_name = QLabel("Labelpad")
        app_name.setStyleSheet(
            "color: #2A7AD4; font-size: 17px; font-weight: 700; letter-spacing: 0.5px;"
        )
        layout.addWidget(app_name)
        layout.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setFixedHeight(30)
        refresh_btn.setStyleSheet(
            "QPushButton { background: #1C2333; border: 1px solid #2E3A50;"
            "border-radius: 4px; color: #5A7FA8; font-size: 11px; padding: 0 12px; }"
            "QPushButton:hover { border-color: #4A7FB5; color: #D4D8DE; }"
        )
        refresh_btn.clicked.connect(self._scan_unlabeled)
        layout.addWidget(refresh_btn)

        # Export Pack — secondary action.
        export_btn = QPushButton("Export Pack...")
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.setFixedHeight(30)
        export_btn.setToolTip("Bundle DICOM files into a .dcmpack archive  (Ctrl+E)")
        export_btn.setStyleSheet(
            "QPushButton { background: #1C2333; border: 1px solid #2E3A50;"
            "border-radius: 4px; color: #8A98AA; font-size: 11px; padding: 0 12px; }"
            "QPushButton:hover { border-color: #4A7FB5; color: #D4D8DE; }"
            "QPushButton:pressed { background: #1A2740; }"
        )
        export_btn.clicked.connect(self._export_pack)
        layout.addWidget(export_btn)

        # Import Pack — secondary action, placed left of the primary import.
        pack_btn = QPushButton("Import Pack")
        pack_btn.setCursor(Qt.PointingHandCursor)
        pack_btn.setFixedHeight(30)
        pack_btn.setToolTip("Import a .dcmpack archive  (Ctrl+Shift+I)")
        pack_btn.setStyleSheet(
            "QPushButton { background: #1C2333; border: 1px solid #2E3A50;"
            "border-radius: 4px; color: #8A98AA; font-size: 11px; padding: 0 14px; }"
            "QPushButton:hover { border-color: #4A7FB5; color: #D4D8DE; }"
            "QPushButton:pressed { background: #1A2740; }"
            "QPushButton:disabled { color: #3E4A5C; border-color: #1E2530; }"
        )
        pack_btn.clicked.connect(self._import_dcmpack)
        layout.addWidget(pack_btn)
        self._pack_btn = pack_btn

        # Import DICOMs — primary action.
        import_btn = QPushButton("Import DICOMs")
        import_btn.setCursor(Qt.PointingHandCursor)
        import_btn.setFixedHeight(30)
        import_btn.setToolTip("Import individual DICOM files  (Ctrl+I)")
        import_btn.setStyleSheet(
            "QPushButton { background: #1F5FAD; border: 1px solid #2A7AD4;"
            "border-radius: 4px; color: #FFFFFF; font-size: 11px;"
            "font-weight: 600; padding: 0 14px; }"
            "QPushButton:hover { background: #2A7AD4; }"
            "QPushButton:disabled { background: #132540; border-color: #1A3560;"
            "color: #3E5A80; }"
        )
        import_btn.clicked.connect(self._import_dicoms)
        layout.addWidget(import_btn)
        self._import_btn = import_btn

        return header

    def _build_file_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background-color: #0D1118;")
        panel.setFixedWidth(340)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        ph = QWidget()
        ph.setFixedHeight(40)
        ph.setStyleSheet("background-color: #0A0F1A; border-bottom: 1px solid #1E2A3A;")
        ph_layout = QHBoxLayout(ph)
        ph_layout.setContentsMargins(16, 0, 16, 0)
        ph_layout.addWidget(self._section_label("DICOM FILES"))
        ph_layout.addStretch()

        self._count_label = QLabel("0 files")
        self._count_label.setStyleSheet("color: #3E4A5C; font-size: 10px;")
        ph_layout.addWidget(self._count_label)
        layout.addWidget(ph)

        legend = QWidget()
        legend.setStyleSheet("background-color: #080D14; border-bottom: 1px solid #1E2A3A;")
        leg_layout = QHBoxLayout(legend)
        leg_layout.setContentsMargins(16, 6, 16, 6)
        leg_layout.setSpacing(14)
        for status, color in STATUS_COLORS.items():
            dot = QLabel("*")
            dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            lbl = QLabel(status)
            lbl.setStyleSheet("color: #3E4A5C; font-size: 10px;")
            leg_layout.addWidget(dot)
            leg_layout.addWidget(lbl)
        leg_layout.addStretch()
        layout.addWidget(legend)

        self._file_list = QListWidget()
        self._file_list.setSpacing(2)
        self._file_list.setStyleSheet(
            "QListWidget { background: #0D1118; border: none; outline: none; }"
            "QListWidget::item { padding: 10px 16px; border-bottom: 1px solid #111820; font-size: 12px; }"
            "QListWidget::item:selected { background: #1A3050; border-left: 3px solid #2A7AD4; color: #FFFFFF; }"
            "QListWidget::item:hover:!selected { background: #141D2E; }"
        )
        self._file_list.currentItemChanged.connect(self._on_file_selected)
        layout.addWidget(self._file_list, stretch=1)

        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background-color: #0F1117;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        ph = QWidget()
        ph.setFixedHeight(40)
        ph.setStyleSheet("background-color: #0A0F1A; border-bottom: 1px solid #1E2A3A;")
        ph_layout = QHBoxLayout(ph)
        ph_layout.setContentsMargins(16, 0, 16, 0)
        ph_layout.addWidget(self._section_label("FILE DETAILS"))
        layout.addWidget(ph)

        self._detail_panel = _DetailPanel()
        self._detail_panel.open_viewer_requested.connect(self._open_viewer)
        layout.addWidget(self._detail_panel, stretch=1)

        return panel

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #5A7FA8; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;"
        )
        return lbl

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"),           self).activated.connect(self._scan_unlabeled)
        QShortcut(QKeySequence("Ctrl+I"),       self).activated.connect(self._import_dicoms)
        QShortcut(QKeySequence("Ctrl+E"),       self).activated.connect(self._export_pack)
        QShortcut(QKeySequence("Ctrl+Shift+I"), self).activated.connect(self._import_dcmpack)
        QShortcut(QKeySequence("Return"),       self).activated.connect(self._open_selected)
        QShortcut(QKeySequence("Down"),         self).activated.connect(self._select_next)
        QShortcut(QKeySequence("Up"),           self).activated.connect(self._select_prev)

    def _open_selected(self) -> None:
        item = self._file_list.currentItem()
        if item:
            self._open_viewer(item.data(Qt.UserRole))

    def _select_next(self) -> None:
        row = self._file_list.currentRow()
        if row < self._file_list.count() - 1:
            self._file_list.setCurrentRow(row + 1)

    def _select_prev(self) -> None:
        row = self._file_list.currentRow()
        if row > 0:
            self._file_list.setCurrentRow(row - 1)

    # ------------------------------------------------------------------
    # Import DICOMs (original flow, unchanged)
    # ------------------------------------------------------------------

    def _import_dicoms(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import DICOM Files",
            str(Path.home()),
            "DICOM Files (*.dcm *.dicom *.DCM *.DICOM);;All Files (*)",
        )
        if not paths:
            return

        progress = QProgressDialog("Importing DICOM files...", "Cancel", 0, len(paths), self)
        progress.setWindowTitle("Importing")
        progress.setMinimumWidth(360)
        progress.setWindowModality(Qt.WindowModal)

        copied  = 0
        skipped = 0
        errors: list[str] = []

        for i, src in enumerate(paths):
            progress.setValue(i)
            progress.setLabelText(f"Importing {Path(src).name}  ({i + 1} of {len(paths)})")

            if progress.wasCanceled():
                break

            dest = _UNLABELED_DIR / Path(src).name
            if dest.exists():
                skipped += 1
                continue

            try:
                shutil.copy2(src, dest)
                copied += 1
            except OSError as exc:
                errors.append(f"{Path(src).name}: {exc}")

        progress.setValue(len(paths))
        self._scan_unlabeled()

        parts = []
        if copied:
            parts.append(f"{copied} file(s) imported")
        if skipped:
            parts.append(f"{skipped} skipped (already exist)")
        if errors:
            parts.append(f"{len(errors)} failed")

        self._status_bar.showMessage("  |  ".join(parts))

        if errors:
            AppDialog.warning(
                self,
                "Import Errors",
                "Some files could not be imported:\n\n" + "\n".join(errors),
            )

    # ------------------------------------------------------------------
    # Import DCMPACK (M4)
    # ------------------------------------------------------------------

    def _import_dcmpack(self) -> None:
        """Open a file picker and delegate to _open_pack_from_path."""
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Import DCMPACK Archive",
            str(Path.home()),
            "DCMPACK Files (*.dcmpack *.DCMPACK);;All Files (*)",
        )
        if not path_str:
            return
        self._open_pack_from_path(Path(path_str))

    def _open_pack_from_path(self, path: Path) -> None:
        """
        Single entry point for every pack-open trigger: the Import Pack
        button, OS double-click association (M5), and any future caller.

        Flow:
            1. Peek for password protection without opening the archive.
            2. Prompt for password if needed — cancel aborts silently.
            3. Open the archive and read the manifest.
            4. Show PackInfoDialog — cancel aborts silently.
            5. Hand off to _run_pack_extraction for background extraction.

        Args:
            path: Absolute path to the .dcmpack file to open.
        """
        password: str | None = None

        if peek_is_password_protected(path):
            password = ask_password(self, mode="open")
            if password is None:
                self._status_bar.showMessage("Import cancelled.")
                return

        try:
            with open_pack(path, password) as zf:
                manifest = read_manifest(zf)
        except (DcmPackPasswordError, DcmPackCorruptError, DcmPackVersionError) as exc:
            AppDialog.error(self, "Cannot Read Pack", str(exc))
            return

        if not show_pack_info(self, manifest, path):
            self._status_bar.showMessage("Import cancelled.")
            return

        self._run_pack_extraction(path, password)

    # ------------------------------------------------------------------
    # Export DCMPACK (M5)
    # ------------------------------------------------------------------

    def _export_pack(self) -> None:
        """Open the pack export dialog and report the result in the status bar."""
        from ui.pack_export_dialog import PackExportDialog
        dlg = PackExportDialog(self)
        from main import _apply_dark_titlebar
        _apply_dark_titlebar(dlg)
        if dlg.exec_() == QDialog.Accepted:
            created = dlg.created_path()
            if created:
                self._status_bar.showMessage(f"Pack exported  —  {created.name}")
                log.info("Pack exported to %s.", created)

    def _run_pack_extraction(self, path: Path, password: str | None) -> None:
        """Disable the UI, show a busy progress dialog, and start the extractor thread."""
        self._status_bar.showMessage(f"Extracting {path.name}...")
        self._set_import_controls_enabled(False)

        progress = QProgressDialog(
            f"Extracting contents of  {path.name}…",
            None,   # no cancel button — extraction is atomic
            0, 0,   # indeterminate (busy) indicator
            self,
        )
        progress.setWindowTitle("Importing Pack")
        progress.setMinimumWidth(400)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()

        self._pack_extractor = _PackExtractor(path, password)
        self._extractor_thread = QThread(self)
        self._pack_extractor.moveToThread(self._extractor_thread)

        self._extractor_thread.started.connect(self._pack_extractor.run)

        self._pack_extractor.finished.connect(
            lambda result: self._on_pack_extracted(result, path, progress)
        )
        self._pack_extractor.failed.connect(
            lambda msg: self._on_pack_extract_failed(msg, path, progress)
        )

        # Thread teardown — same pattern as _DicomLoader
        self._pack_extractor.finished.connect(self._extractor_thread.quit)
        self._pack_extractor.failed.connect(self._extractor_thread.quit)
        self._extractor_thread.finished.connect(self._extractor_thread.deleteLater)

        self._extractor_thread.start()

    def _on_pack_extracted(
        self,
        result: ImportResult,
        path: Path,
        progress: QProgressDialog,
    ) -> None:
        """Called on the main thread when extraction completes successfully."""
        progress.close()
        self._set_import_controls_enabled(True)
        self._extractor_thread = None

        self._scan_unlabeled()
        self._status_bar.showMessage(f"{path.name}  —  {result.summary}")

        if result.failed:
            failed_lines = "\n".join(
                f"  {stem}: {reason}" for stem, reason in result.failed
            )
            AppDialog.warning(
                self,
                "Import Incomplete",
                f"Most items were imported, but {len(result.failed)} "
                f"item(s) in '{path.name}' could not be extracted:\n\n"
                f"{failed_lines}",
            )

    def _on_pack_extract_failed(
        self,
        message: str,
        path: Path,
        progress: QProgressDialog,
    ) -> None:
        """Called on the main thread when extraction fails at the archive level."""
        progress.close()
        self._set_import_controls_enabled(True)
        self._extractor_thread = None

        self._status_bar.showMessage(f"Failed to import {path.name}.")

        # Surface a more actionable message for the most common failure cause.
        if "password" in message.lower():
            AppDialog.error(
                self,
                "Wrong Password",
                f"The password entered for '{path.name}' is incorrect.\n\n"
                "Please try importing again with the correct password.",
            )
        else:
            AppDialog.error(
                self,
                "Pack Import Failed",
                f"Could not extract '{path.name}'.\n\n{message}",
            )

    def _set_import_controls_enabled(self, enabled: bool) -> None:
        """
        Enable or disable the header import buttons during a background
        extraction, preventing concurrent imports on the same thread pool.
        The rest of the window (file list, viewer) remains interactive.
        """
        self._pack_btn.setEnabled(enabled)
        self._import_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # File scanning
    # ------------------------------------------------------------------

    def _scan_unlabeled(self) -> None:
        self._file_list.clear()
        dcm_files = sorted(
            f for f in _UNLABELED_DIR.iterdir()
            if f.suffix.lower() in (".dcm", ".dicom") and f.is_file()
        )

        if not dcm_files:
            self._count_label.setText("0 files")
            self._status_bar.showMessage("No DICOM files found in Unlabeled/")
            placeholder = QListWidgetItem(
                "No files yet.\nImport DICOMs or a .dcmpack to get started."
            )
            placeholder.setFlags(Qt.NoItemFlags)
            placeholder.setForeground(QColor("#2E3A50"))
            placeholder.setTextAlignment(Qt.AlignCenter)
            self._file_list.addItem(placeholder)
            return

        for dcm_path in dcm_files:
            self._file_list.addItem(_make_list_item(dcm_path))

        n = len(dcm_files)
        self._count_label.setText(f"{n} file{'s' if n != 1 else ''}")
        self._status_bar.showMessage(f"Found {n} DICOM file(s)")
        log.info("Scanned Unlabeled/ — %d file(s) found.", n)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_file_selected(self, current: QListWidgetItem, _prev) -> None:
        if current is None:
            return
        dcm_path: Path = current.data(Qt.UserRole)
        self._detail_panel.load_file(dcm_path)
        self._status_bar.showMessage(f"Selected: {dcm_path.name}")

    def _open_viewer(self, dcm_path: Path) -> None:
        self._status_bar.showMessage(f"Loading {dcm_path.name}...")
        self.setEnabled(False)

        self._loader      = _DicomLoader(dcm_path)
        self._loader_thread = QThread(self)
        self._loader.moveToThread(self._loader_thread)

        self._loader_thread.started.connect(self._loader.run)
        self._loader.finished.connect(self._on_dicom_loaded)
        self._loader.failed.connect(self._on_dicom_load_failed)
        self._loader.finished.connect(self._loader_thread.quit)
        self._loader.failed.connect(self._loader_thread.quit)
        self._loader_thread.finished.connect(self._loader_thread.deleteLater)

        self._loader_thread.start()

    def _on_dicom_loaded(self, dicom) -> None:
        self.setEnabled(True)
        self._status_bar.showMessage(f"Loaded: {dicom.path.name}")
        viewer = DicomViewer(dicom, parent=self)
        viewer.setWindowIcon(self.windowIcon())
        from main import _apply_dark_titlebar
        _apply_dark_titlebar(viewer)
        viewer.confirmed.connect(lambda _p: self._on_viewer_confirmed(dicom.path))
        viewer.exec_()

    def _on_dicom_load_failed(self, error_msg: str) -> None:
        self.setEnabled(True)
        self._status_bar.showMessage("Failed to load DICOM file.")
        AppDialog.error(self, "DICOM Load Error", f"Could not open the selected file.\n\n{error_msg}")

    def _on_viewer_confirmed(self, dcm_path: Path) -> None:
        from core.labelme_bridge import launch_labelme, LabelmeNotFoundError, RasterNotFoundError
        self._refresh_item_status(dcm_path)
        self._detail_panel.refresh()
        self._status_bar.showMessage(f"Launching LabelMe for {dcm_path.name}...")

        try:
            self._active_session = launch_labelme(
                dcm_path,
                on_started=lambda pid: self._status_bar.showMessage(
                    f"LabelMe running (PID {pid}) — annotate and save to finish."
                ),
                on_exit=lambda: self._on_labelme_exit(dcm_path),
                on_error=lambda msg: self._on_labelme_error(msg),
            )
        except (LabelmeNotFoundError, RasterNotFoundError) as exc:
            AppDialog.error(self, "LabelMe Launch Error", str(exc), exc=exc)

    def _on_labelme_exit(self, dcm_path: Path) -> None:
        self._active_session = None
        self._refresh_item_status(dcm_path)
        self._detail_panel.refresh()
        self._status_bar.showMessage(f"Annotation complete for {dcm_path.name}")

    def _on_labelme_error(self, message: str) -> None:
        self._active_session = None
        self._status_bar.showMessage("LabelMe failed to launch.")
        AppDialog.error(self, "LabelMe Error", message)

    def _refresh_item_status(self, dcm_path: Path) -> None:
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            if item.data(Qt.UserRole) == dcm_path:
                status = _resolve_status(dcm_path)
                item.setForeground(QColor(STATUS_COLORS[status]))
                item.setData(Qt.UserRole + 1, status)
                break

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._active_session:
            self._active_session.terminate()

        if self._extractor_thread and self._extractor_thread.isRunning():
            log.info("Waiting for pack extractor thread to finish before closing.")
            self._extractor_thread.quit()
            self._extractor_thread.wait(3000)

        event.accept()