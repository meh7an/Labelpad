"""
ui/main_window.py
Application shell — orchestrates panels, threading, and top-level commands.
"""

import logging
import shutil
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QShortcut,
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
    open_pack,
    peek_is_password_protected,
    read_manifest,
)
from core.folder_store import FolderStore
from core.paths import UNLABELED_DIR
from core.purge import purge_dicom
from core.updater import (
    RELEASE_PAGE_URL,
    UpdateError,
    UpdateInfo,
    clean_stale_staging,
    extract_payload,
    install_root,
    is_root_writable,
    launch_swap,
    staging_dir,
    verify_zip,
)
from core.version import __version__
from ui.detail_panel import DetailPanel
from ui.dicom_viewer import DicomViewer
from ui.error_dialog import AppDialog
from ui.file_panel_widget import FilePanelWidget
from ui.pack_info_dialog import show_pack_info
from ui.password_dialog import ask_password
from ui.workers import (
    DicomLoader,
    PackExtractor,
    UpdateCheckWorker,
    UpdateDownloadWorker,
)

log = logging.getLogger(__name__)

_UNLABELED_DIR = UNLABELED_DIR


class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self._loader_thread:       QThread | None = None
        self._extractor_thread:    QThread | None = None
        self._update_check_thread: QThread | None = None
        self._update_dl_thread:    QThread | None = None
        self._pending_update:      UpdateInfo | None = None
        self._active_session = None
        self._folder_store   = FolderStore()
        self._build_ui()
        self._setup_shortcuts()
        self._file_panel.scan()

        # In-app updating: clear leftovers from earlier attempts, then check
        # shortly after launch and every hour afterwards.
        clean_stale_staging()
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(3_600_000)
        self._update_timer.timeout.connect(self._start_update_check)
        self._update_timer.start()
        QTimer.singleShot(5000, self._start_update_check)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(f"Labelpad v{__version__}")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 820)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        self._file_panel = FilePanelWidget(store=self._folder_store)
        self._file_panel.file_selected.connect(self._on_file_selected)
        self._file_panel.open_viewer_requested.connect(self._open_viewer)
        self._file_panel.move_paths_requested.connect(self._cmd_move_paths_to_folder)
        self._file_panel.cut_paths_requested.connect(self._cmd_cut_paths)
        self._file_panel.delete_paths_requested.connect(self._cmd_delete_paths)
        self._file_panel.bind_folder_actions(
            on_rename=self._cmd_rename_folder,
            on_export=self._cmd_export_folder,
            on_delete=self._cmd_delete_folder,
        )

        self._detail_panel = DetailPanel(store=self._folder_store)
        self._detail_panel.open_viewer_requested.connect(self._open_viewer)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #1E2A3A; }")
        splitter.addWidget(self._file_panel)
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
        header.setStyleSheet("background-color: #0A0F1A; border-bottom: 1px solid #1E2A3A;")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(10)

        app_name = QLabel("Labelpad")
        app_name.setStyleSheet(
            "color: #2A7AD4; font-size: 17px; font-weight: 700; letter-spacing: 0.5px;"
        )
        layout.addWidget(app_name)

        version_lbl = QLabel(f"v{__version__}")
        version_lbl.setStyleSheet("color: #5A7FA8; font-size: 11px; margin-top: 6px;")
        layout.addWidget(version_lbl)
        layout.addStretch()

        self._update_btn = QPushButton("Update")
        self._update_btn.setCursor(Qt.PointingHandCursor)
        self._update_btn.setFixedHeight(30)
        self._update_btn.setStyleSheet(
            "QPushButton { background: #14301F; border: 1px solid #2E8050;"
            "border-radius: 4px; color: #3ECF6E; font-size: 11px;"
            "font-weight: 600; padding: 0 12px; }"
            "QPushButton:hover { border-color: #3ECF6E; color: #D4FFE4; }"
            "QPushButton:pressed { background: #0F2A1A; }"
        )
        self._update_btn.setVisible(False)
        self._update_btn.clicked.connect(self._cmd_run_update)
        layout.addWidget(self._update_btn)

        _sec = (
            "QPushButton { background: #1C2333; border: 1px solid #2E3A50;"
            "border-radius: 4px; color: #5A7FA8; font-size: 11px; padding: 0 12px; }"
            "QPushButton:hover { border-color: #4A7FB5; color: #D4D8DE; }"
            "QPushButton:pressed { background: #1A2740; }"
        )
        for label, tip, slot in (
            ("Refresh",    None,                             lambda: self._file_panel.scan()),
            ("New Folder", "Create a new folder  (Ctrl+N)", self._cmd_new_folder),
        ):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(30)
            btn.setStyleSheet(_sec)
            if tip:
                btn.setToolTip(tip)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        self._paste_btn = QPushButton("Paste \u25be")
        self._paste_btn.setCursor(Qt.PointingHandCursor)
        self._paste_btn.setFixedHeight(30)
        self._paste_btn.setToolTip("Paste cut files into a folder  (Ctrl+V)")
        self._paste_btn.setStyleSheet(
            "QPushButton { background: #1C2740; border: 1px solid #2A5080;"
            "border-radius: 4px; color: #4A9AEF; font-size: 11px; padding: 0 12px; }"
            "QPushButton:hover { border-color: #4A9AEF; color: #D4D8DE; }"
            "QPushButton:pressed { background: #1A2050; }"
        )
        self._paste_btn.setVisible(False)
        self._paste_btn.clicked.connect(self._cmd_paste)
        layout.addWidget(self._paste_btn)

        for label, tip, slot, style, attr in (
            ("Export Pack...", "Bundle DICOM files into a .dcmpack archive  (Ctrl+E)",
             self._export_pack,
             "QPushButton { background: #1C2333; border: 1px solid #2E3A50;"
             "border-radius: 4px; color: #8A98AA; font-size: 11px; padding: 0 12px; }"
             "QPushButton:hover { border-color: #4A7FB5; color: #D4D8DE; }"
             "QPushButton:pressed { background: #1A2740; }",
             None),
            ("Import Pack", "Import a .dcmpack archive  (Ctrl+Shift+I)",
             self._import_dcmpack,
             "QPushButton { background: #1C2333; border: 1px solid #2E3A50;"
             "border-radius: 4px; color: #8A98AA; font-size: 11px; padding: 0 14px; }"
             "QPushButton:hover { border-color: #4A7FB5; color: #D4D8DE; }"
             "QPushButton:pressed { background: #1A2740; }"
             "QPushButton:disabled { color: #3E4A5C; border-color: #1E2530; }",
             "_pack_btn"),
            ("Import DICOMs", "Import individual DICOM files  (Ctrl+I)",
             self._import_dicoms,
             "QPushButton { background: #1F5FAD; border: 1px solid #2A7AD4;"
             "border-radius: 4px; color: #FFFFFF; font-size: 11px;"
             "font-weight: 600; padding: 0 14px; }"
             "QPushButton:hover { background: #2A7AD4; }"
             "QPushButton:disabled { background: #132540; border-color: #1A3560; color: #3E5A80; }",
             "_import_btn"),
        ):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(30)
            btn.setToolTip(tip)
            btn.setStyleSheet(style)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
            if attr:
                setattr(self, attr, btn)

        return header

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
        lbl = QLabel("FILE DETAILS")
        lbl.setStyleSheet(
            "color: #5A7FA8; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;"
        )
        ph_layout.addWidget(lbl)
        layout.addWidget(ph)
        layout.addWidget(self._detail_panel, stretch=1)
        return panel

    # ------------------------------------------------------------------
    # Shortcuts
    # ------------------------------------------------------------------

    def _setup_shortcuts(self) -> None:
        bindings = {
            "F5":           lambda: self._file_panel.scan(),
            "Ctrl+I":       self._import_dicoms,
            "Ctrl+E":       self._export_pack,
            "Ctrl+Shift+I": self._import_dcmpack,
            "Ctrl+N":       self._cmd_new_folder,
            "Ctrl+F":       lambda: self._file_panel.focus_filter(),
            "Ctrl+X":       lambda: self._cmd_cut_paths(self._file_panel.selected_file_paths()),
            "Ctrl+V":       self._cmd_paste,
            "Delete":       lambda: self._cmd_move_paths_to_folder(self._file_panel.selected_file_paths() or
                                    ([self._file_panel.current_file_path()] if self._file_panel.current_file_path() else []), None),
            "Shift+Delete": lambda: self._cmd_delete_paths(self._file_panel.selected_file_paths() or
                                    ([self._file_panel.current_file_path()] if self._file_panel.current_file_path() else [])),
            "Return":       lambda: self._open_viewer(p) if (p := self._file_panel.current_file_path()) else None,
            "Down":         self._file_panel.select_next,
            "Up":           self._file_panel.select_prev,
        }
        for key, slot in bindings.items():
            QShortcut(QKeySequence(key), self).activated.connect(slot)

    # ------------------------------------------------------------------
    # Folder commands
    # ------------------------------------------------------------------

    def _cmd_new_folder(self) -> None:
        from ui.new_folder_dialog import NewFolderDialog
        dlg = NewFolderDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            try:
                f = self._folder_store.create_folder(dlg.name(), dlg.mandatory_labels())
                self._status_bar.showMessage(f"Created folder \"{f.name}\".")
                self._file_panel.scan()
            except Exception as exc:
                AppDialog.error(self, "Create Folder Failed", str(exc))

    def _cmd_rename_folder(self, folder_id: str) -> None:
        from ui.new_folder_dialog import NewFolderDialog
        try:
            folder = self._folder_store.get_folder(folder_id)
        except Exception:
            return
        dlg = NewFolderDialog(self, folder=folder)
        if dlg.exec_() == QDialog.Accepted:
            try:
                self._folder_store.rename_folder(folder_id, dlg.name())
                self._folder_store.set_mandatory_labels(folder_id, dlg.mandatory_labels())
                self._file_panel.scan()
                self._detail_panel.refresh()
            except Exception as exc:
                AppDialog.error(self, "Edit Folder Failed", str(exc))

    def _cmd_delete_folder(self, folder_id: str) -> None:
        try:
            folder = self._folder_store.get_folder(folder_id)
        except Exception:
            return
        reply = QMessageBox.question(
            self, "Delete Folder",
            f"Delete folder \"{folder.name}\"?\n\nFiles will not be deleted — they will become unassigned.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if reply == QMessageBox.Yes:
            self._folder_store.delete_folder(folder_id)
            self._file_panel.scan()
            self._status_bar.showMessage(f"Deleted folder \"{folder.name}\".")

    def _cmd_export_folder(self, folder_id: str) -> None:
        try:
            folder = self._folder_store.get_folder(folder_id)
        except Exception:
            return
        from ui.pack_export_dialog import PackExportDialog
        from main import _apply_dark_titlebar
        dlg = PackExportDialog(self, preselected_stems=list(folder.stems))
        _apply_dark_titlebar(dlg)
        if dlg.exec_() == QDialog.Accepted and (created := dlg.created_path()):
            self._status_bar.showMessage(f"Pack exported  —  {created.name}")

    # ------------------------------------------------------------------
    # Clipboard / move
    # ------------------------------------------------------------------

    def _cmd_cut_paths(self, paths: list[Path]) -> None:
        if not paths:
            return
        self._file_panel.set_cut_stems({p.stem for p in paths})
        self._paste_btn.setVisible(True)
        n = len(paths)
        self._status_bar.showMessage(
            f"Cut {n} file{'s' if n != 1 else ''}  —  right-click a folder or press Ctrl+V to paste."
        )

    def _cmd_paste(self) -> None:
        if not self._file_panel.cut_stems:
            return
        menu = QMenu(self)
        for folder in self._folder_store.all_folders():
            menu.addAction(folder.name).triggered.connect(
                lambda _, fid=folder.id: self._do_paste(fid)
            )
        menu.addSeparator()
        menu.addAction("(No Folder / Unassign)").triggered.connect(lambda: self._do_paste(None))
        menu.exec_(self._paste_btn.mapToGlobal(self._paste_btn.rect().bottomLeft()))

    def _do_paste(self, folder_id: str | None) -> None:
        stems = list(self._file_panel.cut_stems)
        self._file_panel.clear_cut_stems()
        self._paste_btn.setVisible(False)
        self._cmd_move_paths_to_folder(
            [_UNLABELED_DIR / f"{s}.dcm" for s in stems], folder_id
        )

    def _cmd_move_paths_to_folder(self, paths: list[Path], folder_id: str | None) -> None:
        stems = [p.stem for p in paths]
        if folder_id is not None:
            self._folder_store.add_stems(folder_id, stems)
        else:
            for stem in stems:
                if f := self._folder_store.folder_for_stem(stem):
                    self._folder_store.remove_stems(f.id, [stem])
        self._file_panel.scan()
        self._detail_panel.refresh()
        if folder_id is not None:
            name = self._folder_store.get_folder(folder_id).name
            self._status_bar.showMessage(
                f"Moved {len(stems)} file{'s' if len(stems) != 1 else ''} to \"{name}\"."
            )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _cmd_delete_paths(self, paths: list[Path]) -> None:
        if not paths:
            return
        if any(self._folder_store.folder_for_stem(p.stem) for p in paths):
            self._status_bar.showMessage(
                "Only unassigned files can be deleted — remove them from their folder first."
            )
            return

        n      = len(paths)
        target = f"{n} files" if n > 1 else f"\"{paths[0].name}\""
        reply  = QMessageBox.question(
            self, "Delete File Permanently",
            f"Permanently delete {target}?\n\n"
            "The DICOM, its annotations, raster export, and metadata will be "
            "removed from disk. This cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if reply != QMessageBox.Yes:
            return

        deleted: set[str] = set()
        failed:  list[str] = []
        for p in paths:
            try:
                purge_dicom(p)
                deleted.add(p.stem)
            except OSError as exc:
                failed.append(f"{p.name} — {exc}")

        if deleted & self._file_panel.cut_stems:
            self._file_panel.set_cut_stems(self._file_panel.cut_stems - deleted)
            if not self._file_panel.cut_stems:
                self._paste_btn.setVisible(False)
        self._file_panel.scan()
        self._detail_panel.refresh()

        if failed:
            AppDialog.error(self, "Delete Failed", "Could not delete:\n" + "\n".join(failed))
        if deleted:
            self._status_bar.showMessage(
                f"Permanently deleted {len(deleted)} file{'s' if len(deleted) != 1 else ''}."
            )

    # ------------------------------------------------------------------
    # In-app updating
    # ------------------------------------------------------------------

    def _start_update_check(self) -> None:
        if self._update_check_thread is not None:
            return
        self._update_checker      = UpdateCheckWorker()
        self._update_check_thread = QThread(self)
        self._update_checker.moveToThread(self._update_check_thread)
        self._update_check_thread.started.connect(self._update_checker.run)
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.finished.connect(self._on_update_check_done)
        self._update_checker.finished.connect(self._update_check_thread.quit)
        self._update_check_thread.finished.connect(self._update_check_thread.deleteLater)
        self._update_check_thread.start()

    def _on_update_available(self, info: UpdateInfo) -> None:
        self._pending_update = info
        self._update_btn.setText(f"Update to v{info.latest}")
        self._update_btn.setToolTip(
            f"{info.release_name} is available. One click updates and restarts Labelpad."
        )
        self._update_btn.setVisible(True)
        log.info("Update available: v%s -> v%s.", info.current, info.latest)

    def _on_update_check_done(self) -> None:
        self._update_check_thread = None

    def _cmd_run_update(self) -> None:
        info = self._pending_update
        if info is None:
            return
        root = install_root()
        if root is None or not is_root_writable(root):
            QDesktopServices.openUrl(QUrl(RELEASE_PAGE_URL))
            self._status_bar.showMessage(
                "This install cannot update itself — opened the release page instead."
            )
            return

        notes = info.release_notes.strip()
        if len(notes) > 400:
            notes = notes[:400] + "..."
        lines = [f"Update from v{info.current} to v{info.latest}?"]
        if notes:
            lines += ["", notes]
        lines += ["", "Labelpad will download the update, then restart to apply it."]
        reply = QMessageBox.question(
            self, "Update Labelpad", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self._run_update_download(info, root)

    def _run_update_download(self, info: UpdateInfo, root: Path) -> None:
        self._update_btn.setEnabled(False)
        staging_dir().mkdir(parents=True, exist_ok=True)
        dest = staging_dir() / info.asset_name

        progress = QProgressDialog(
            f"Downloading {info.asset_name}…", "Cancel", 0, 100, self,
        )
        progress.setWindowTitle("Updating")
        progress.setMinimumWidth(400)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()

        self._update_dl        = UpdateDownloadWorker(info.asset_url, dest)
        self._update_dl_thread = QThread(self)
        self._update_dl.moveToThread(self._update_dl_thread)
        self._update_dl_thread.started.connect(self._update_dl.run)
        progress.canceled.connect(lambda: self._update_dl.cancel())
        self._update_dl.progress.connect(
            lambda done, total: progress.setValue(int(done * 100 / total)) if total else None
        )
        self._update_dl.finished.connect(
            lambda p: self._on_update_downloaded(p, root, progress)
        )
        self._update_dl.failed.connect(lambda msg: self._on_update_dl_failed(msg, progress))
        self._update_dl.cancelled.connect(lambda: self._on_update_dl_cancelled(progress))
        self._update_dl.finished.connect(self._update_dl_thread.quit)
        self._update_dl.failed.connect(self._update_dl_thread.quit)
        self._update_dl.cancelled.connect(self._update_dl_thread.quit)
        self._update_dl_thread.finished.connect(self._update_dl_thread.deleteLater)
        self._update_dl_thread.start()

    def _on_update_downloaded(self, zip_path: Path, root: Path, progress) -> None:
        progress.close()
        self._update_dl_thread = None
        self._status_bar.showMessage("Preparing update...")
        try:
            if not verify_zip(zip_path):
                raise UpdateError("The downloaded update file is corrupted — try again later.")
            payload = extract_payload(zip_path)
            launch_swap(payload, root)
        except UpdateError as exc:
            self._update_btn.setEnabled(True)
            self._status_bar.showMessage("Update failed.")
            AppDialog.error(self, "Update Failed", str(exc))
            return
        except Exception as exc:
            self._update_btn.setEnabled(True)
            self._status_bar.showMessage("Update failed.")
            AppDialog.error(self, "Update Failed", f"Could not prepare the update.\n\n{exc}")
            return
        log.info("Swap helper running — restarting to apply the update.")
        QApplication.quit()

    def _on_update_dl_failed(self, message: str, progress) -> None:
        progress.close()
        self._update_dl_thread = None
        self._update_btn.setEnabled(True)
        self._status_bar.showMessage("Update download failed — will retry on the next check.")
        AppDialog.error(self, "Update Failed", message)

    def _on_update_dl_cancelled(self, progress) -> None:
        progress.close()
        self._update_dl_thread = None
        self._update_btn.setEnabled(True)
        self._status_bar.showMessage("Update cancelled.")

    # ------------------------------------------------------------------
    # Import DICOMs
    # ------------------------------------------------------------------

    def _import_dicoms(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import DICOM Files", str(Path.home()),
            "DICOM Files (*.dcm *.dicom *.DCM *.DICOM);;All Files (*)",
        )
        if not paths:
            return

        progress = QProgressDialog("Importing DICOM files...", "Cancel", 0, len(paths), self)
        progress.setWindowTitle("Importing")
        progress.setMinimumWidth(360)
        progress.setWindowModality(Qt.WindowModal)

        copied = 0; skipped = 0; errors: list[str] = []
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
        self._file_panel.scan()

        parts = []
        if copied:  parts.append(f"{copied} file(s) imported")
        if skipped: parts.append(f"{skipped} skipped (already exist)")
        if errors:  parts.append(f"{len(errors)} failed")
        self._status_bar.showMessage("  |  ".join(parts))
        if errors:
            AppDialog.warning(self, "Import Errors",
                              "Some files could not be imported:\n\n" + "\n".join(errors))

    # ------------------------------------------------------------------
    # Import / Export DCMPACK
    # ------------------------------------------------------------------

    def _import_dcmpack(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Import DCMPACK Archive", str(Path.home()),
            "DCMPACK Files (*.dcmpack *.DCMPACK);;All Files (*)",
        )
        if path_str:
            self._open_pack_from_path(Path(path_str))

    def _open_pack_from_path(self, path: Path) -> None:
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

    def _export_pack(self) -> None:
        from ui.pack_export_dialog import PackExportDialog
        from main import _apply_dark_titlebar
        dlg = PackExportDialog(self)
        _apply_dark_titlebar(dlg)
        if dlg.exec_() == QDialog.Accepted and (created := dlg.created_path()):
            self._status_bar.showMessage(f"Pack exported  —  {created.name}")
            log.info("Pack exported to %s.", created)

    def _run_pack_extraction(self, path: Path, password: str | None) -> None:
        self._status_bar.showMessage(f"Extracting {path.name}...")
        self._set_import_controls_enabled(False)

        progress = QProgressDialog(
            f"Extracting contents of  {path.name}\u2026", None, 0, 0, self,
        )
        progress.setWindowTitle("Importing Pack")
        progress.setMinimumWidth(400)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()

        self._pack_extractor   = PackExtractor(path, password)
        self._extractor_thread = QThread(self)
        self._pack_extractor.moveToThread(self._extractor_thread)
        self._extractor_thread.started.connect(self._pack_extractor.run)
        self._pack_extractor.finished.connect(
            lambda result: self._on_pack_extracted(result, path, progress)
        )
        self._pack_extractor.failed.connect(
            lambda msg: self._on_pack_extract_failed(msg, path, progress)
        )
        self._pack_extractor.finished.connect(self._extractor_thread.quit)
        self._pack_extractor.failed.connect(self._extractor_thread.quit)
        self._extractor_thread.finished.connect(self._extractor_thread.deleteLater)
        self._extractor_thread.start()

    def _on_pack_extracted(self, result: ImportResult, path: Path, progress) -> None:
        progress.close()
        self._set_import_controls_enabled(True)
        self._extractor_thread = None
        self._folder_store.reload()
        self._detail_panel._store = self._folder_store
        self._file_panel.scan()
        self._status_bar.showMessage(f"{path.name}  —  {result.summary}")
        if result.failed:
            lines = "\n".join(f"  {s}: {r}" for s, r in result.failed)
            AppDialog.warning(self, "Import Incomplete",
                f"Most items were imported, but {len(result.failed)} item(s) in "
                f"'{path.name}' could not be extracted:\n\n{lines}")

    def _on_pack_extract_failed(self, message: str, path: Path, progress) -> None:
        progress.close()
        self._set_import_controls_enabled(True)
        self._extractor_thread = None
        self._status_bar.showMessage(f"Failed to import {path.name}.")
        if "password" in message.lower():
            AppDialog.error(self, "Wrong Password",
                f"The password entered for '{path.name}' is incorrect.\n\n"
                "Please try importing again with the correct password.")
        else:
            AppDialog.error(self, "Pack Import Failed",
                            f"Could not extract '{path.name}'.\n\n{message}")

    def _set_import_controls_enabled(self, enabled: bool) -> None:
        self._pack_btn.setEnabled(enabled)
        self._import_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # DICOM viewer
    # ------------------------------------------------------------------

    def _open_viewer(self, dcm_path: Path) -> None:
        self._status_bar.showMessage(f"Loading {dcm_path.name}...")
        self.setEnabled(False)
        self._loader        = DicomLoader(dcm_path)
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
        from main import _apply_dark_titlebar
        viewer = DicomViewer(dicom, parent=self)
        viewer.setWindowIcon(self.windowIcon())
        _apply_dark_titlebar(viewer)
        viewer.confirmed.connect(lambda _p: self._on_viewer_confirmed(dicom.path))
        viewer.exec_()

    def _on_dicom_load_failed(self, error_msg: str) -> None:
        self.setEnabled(True)
        self._status_bar.showMessage("Failed to load DICOM file.")
        AppDialog.error(self, "DICOM Load Error",
                        f"Could not open the selected file.\n\n{error_msg}")

    def _on_viewer_confirmed(self, dcm_path: Path) -> None:
        from core.labelme_bridge import launch_labelme, LabelmeNotFoundError, RasterNotFoundError
        self._file_panel.refresh_item_status(dcm_path)
        self._detail_panel.refresh()
        self._status_bar.showMessage(f"Launching LabelMe for {dcm_path.name}...")
        try:
            self._active_session = launch_labelme(
                dcm_path,
                on_started=lambda pid: self._status_bar.showMessage(
                    f"LabelMe running (PID {pid}) — annotate and save to finish."
                ),
                on_exit=lambda: self._on_labelme_exit(dcm_path),
                on_error=self._on_labelme_error,
            )
        except (LabelmeNotFoundError, RasterNotFoundError) as exc:
            AppDialog.error(self, "LabelMe Launch Error", str(exc), exc=exc)

    def _on_labelme_exit(self, dcm_path: Path) -> None:
        self._active_session = None
        self._file_panel.refresh_item_status(dcm_path)
        self._detail_panel.refresh()
        self._status_bar.showMessage(f"Annotation complete for {dcm_path.name}")

    def _on_labelme_error(self, message: str) -> None:
        self._active_session = None
        self._status_bar.showMessage("LabelMe failed to launch.")
        AppDialog.error(self, "LabelMe Error", message)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_file_selected(self, dcm_path: Path) -> None:
        self._detail_panel.load_file(dcm_path, self._folder_store)
        self._status_bar.showMessage(f"Selected: {dcm_path.name}")

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