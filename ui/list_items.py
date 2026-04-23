"""
ui/list_items.py
Row-type constants and QListWidgetItem factory for the DICOM file browser.
"""

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QListWidgetItem

from core.folder_store import FolderStore
from core.status import STATUS_COLORS, resolve_status

ROW_FILE      = "file"
ROW_FOLDER    = "folder_header"
ROW_NO_FOLDER = "no_folder_header"


def make_list_item(
    dcm_path: Path,
    store:    FolderStore | None = None,
) -> QListWidgetItem:
    status = resolve_status(dcm_path, store)
    item   = QListWidgetItem(dcm_path.name)
    item.setData(Qt.UserRole,     dcm_path)
    item.setData(Qt.UserRole + 1, status)
    item.setData(Qt.UserRole + 2, ROW_FILE)
    item.setForeground(QColor(STATUS_COLORS[status]))
    item.setToolTip(f"Status: {status}\nPath: {dcm_path}")
    return item