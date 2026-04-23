"""
ui/workers.py
Background QObject workers for off-thread DICOM loading, pack extraction,
and pack creation.

Threading contract
------------------
Each worker is a plain QObject (not a QThread subclass).  Callers must:
    1. Create a QThread.
    2. Move the worker onto it via worker.moveToThread(thread).
    3. Connect thread.started -> worker.run.
    4. Connect the finished/failed signals to slots that call thread.quit().
    5. Connect thread.finished -> thread.deleteLater() (and worker.deleteLater()).
    6. Start the thread.

Keeping a reference to both thread and worker on the caller prevents
premature garbage-collection while the operation is in flight.
"""

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

import threading

from core.dcmpack import (
    DcmPackCancelledError,
    DcmPackCorruptError,
    DcmPackPasswordError,
    DcmPackVersionError,
    ImportResult,
    PackFolder,
    create_pack,
    extract_pack,
)
from core.dicom_handler import DicomReadError, load_dicom


# ---------------------------------------------------------------------------
# DICOM loader
# ---------------------------------------------------------------------------

class DicomLoader(QObject):
    """Load a single DICOM file off the main thread."""

    finished = pyqtSignal(object)   # emits DicomData
    failed   = pyqtSignal(str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:
        try:
            self.finished.emit(load_dicom(self._path))
        except DicomReadError as exc:
            self.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Pack extractor
# ---------------------------------------------------------------------------

class PackExtractor(QObject):
    """
    Extract a .dcmpack archive off the main thread.

    Signals:
        progress(current, total): emitted before each item is processed.
        finished(ImportResult):   emitted on success.
        failed(str):              emitted on any handled exception.
    """

    progress = pyqtSignal(int, int)   # (current_index, total_items)
    finished = pyqtSignal(object)     # ImportResult
    failed   = pyqtSignal(str)

    def __init__(self, path: Path, password: Optional[str]) -> None:
        super().__init__()
        self._path     = path
        self._password = password

    def run(self) -> None:
        try:
            result = extract_pack(
                self._path,
                self._password,
                on_conflict="skip",
                progress_callback=self._emit_progress,
            )
            self.finished.emit(result)
        except (DcmPackPasswordError, DcmPackCorruptError, DcmPackVersionError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error during extraction: {exc}")

    def _emit_progress(self, current: int, total: int) -> None:
        self.progress.emit(current, total)


# ---------------------------------------------------------------------------
# Pack creator
# ---------------------------------------------------------------------------

class PackCreator(QObject):
    """
    Create a .dcmpack archive off the main thread.

    Signals:
        progress(current, total): emitted before each item is written.
        finished(Path):           resolved path of the created archive.
        failed(str):              emitted on any handled exception.
    """

    progress  = pyqtSignal(int, int)   # (current_index, total_items)
    finished  = pyqtSignal(object)     # Path
    cancelled = pyqtSignal()
    failed    = pyqtSignal(str)

    def __init__(
        self,
        stems:        list[str],
        dest_path:    Path,
        password:     Optional[str]           = None,
        author:       str                     = "",
        description:  str                     = "",
        tags:         Optional[list[str]]     = None,
        pack_folders: Optional[list[PackFolder]] = None,
    ) -> None:
        super().__init__()
        self._stems        = stems
        self._dest_path    = dest_path
        self._password     = password
        self._author       = author
        self._description  = description
        self._tags         = tags
        self._pack_folders = pack_folders
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Signal the worker to abort after the current item finishes writing."""
        self._cancel_event.set()

    def run(self) -> None:
        try:
            out_path = create_pack(
                self._stems,
                self._dest_path,
                password=self._password,
                author=self._author,
                description=self._description,
                tags=self._tags,
                pack_folders=self._pack_folders,
                progress_callback=self._emit_progress,
            )
            self.finished.emit(out_path)
        except DcmPackCancelledError:
            self.cancelled.emit()
        except OSError as exc:
            self.failed.emit(f"Could not write to disk: {exc}")
        except Exception as exc:
            self.failed.emit(str(exc))

    def _emit_progress(self, current: int, total: int) -> None:
        if self._cancel_event.is_set():
            raise DcmPackCancelledError("Export cancelled by user.")
        self.progress.emit(current, total)