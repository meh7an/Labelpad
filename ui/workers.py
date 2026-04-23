"""
ui/workers.py
Background QObject workers for off-thread DICOM loading and pack extraction.
"""

from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal

from core.dcmpack import (
    DcmPackCorruptError,
    DcmPackPasswordError,
    DcmPackVersionError,
    extract_pack,
)
from core.dicom_handler import DicomReadError, load_dicom


class DicomLoader(QObject):
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


class PackExtractor(QObject):
    finished = pyqtSignal(object)
    failed   = pyqtSignal(str)

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