"""
ui/new_folder_dialog.py
Modal dialog for creating a new folder or editing an existing one.
Used by both the "New Folder" toolbar button and the folder context menu.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.folder_store import Folder

_C_BG     = "#0F1117"
_C_INPUT  = "#080C12"
_C_BORDER = "#2E3A50"
_C_ACCENT = "#2A7AD4"
_C_FG     = "#D4D8DE"
_C_MUTED  = "#8A98AA"
_C_DIMMED = "#5A7FA8"
_C_ERROR  = "#A83040"


def _split_labels(raw: str) -> list[str]:
    """Parse a comma-separated label string into a clean list."""
    return [t.strip() for t in raw.split(",") if t.strip()]


class NewFolderDialog(QDialog):
    """
    Create a new folder or edit an existing one.

    Pass an existing Folder to pre-fill fields for editing;
    omit (or pass None) to open in creation mode.

    After exec_() returns Accepted:
        name()             → validated folder name
        mandatory_labels() → list of label strings (may be empty)
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        folder: Optional[Folder]  = None,
    ) -> None:
        super().__init__(parent)
        self._editing = folder is not None
        self._build_ui(folder)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def name(self) -> str:
        return self._name_edit.text().strip()

    def mandatory_labels(self) -> list[str]:
        return _split_labels(self._labels_edit.text())

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self, folder: Optional[Folder]) -> None:
        title_text = "Edit Folder" if self._editing else "New Folder"
        self.setWindowTitle(title_text)
        self.setModal(True)
        self.setFixedWidth(420)
        self.setStyleSheet(f"background-color: {_C_BG};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(4)
        bar.setStyleSheet(f"background-color: {_C_ACCENT};")
        root.addWidget(bar)

        body = QVBoxLayout()
        body.setContentsMargins(26, 22, 26, 20)
        body.setSpacing(14)

        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet(f"color: {_C_FG}; font-size: 14px; font-weight: 600;")
        body.addWidget(title_lbl)

        body.addWidget(self._divider())

        body.addWidget(self._field_label("Folder Name"))
        self._name_edit = self._field(placeholder="e.g.  Brain CT April")
        if folder:
            self._name_edit.setText(folder.name)
        self._name_edit.returnPressed.connect(self.accept)
        body.addWidget(self._name_edit)

        body.addWidget(self._field_label("Mandatory Labels  (comma-separated, optional)"))
        self._labels_edit = self._field(placeholder="e.g.  tumor,  edema,  lesion")
        if folder:
            self._labels_edit.setText(",  ".join(folder.mandatory_labels))
        self._labels_edit.returnPressed.connect(self.accept)
        body.addWidget(self._labels_edit)

        hint = QLabel(
            "Files in this folder must carry all mandatory labels "
            "to reach Labeled status."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: #3E4A5C; font-size: 11px;")
        body.addWidget(hint)

        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(f"color: {_C_ERROR}; font-size: 11px;")
        self._error_lbl.setVisible(False)
        body.addWidget(self._error_lbl)

        body.addWidget(self._divider())

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(88, 34)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(self._btn_style(primary=False))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("Save" if self._editing else "Create")
        ok_btn.setFixedSize(88, 34)
        ok_btn.setDefault(True)
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.setStyleSheet(self._btn_style(primary=True))
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)

        body.addLayout(btn_row)
        root.addLayout(body)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {_C_DIMMED}; font-size: 11px;")
        return lbl

    def _field(self, placeholder: str = "") -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumHeight(34)
        edit.setStyleSheet(
            f"QLineEdit {{ background-color: {_C_INPUT}; border: 1px solid {_C_BORDER};"
            f"border-radius: 4px; padding: 4px 10px; color: {_C_FG}; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {_C_ACCENT}; }}"
        )
        return edit

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {_C_BORDER};")
        return line

    def _btn_style(self, primary: bool) -> str:
        if primary:
            return (
                f"QPushButton {{ background: #1F5FAD; color: #FFFFFF;"
                f"border: 1px solid {_C_ACCENT}; border-radius: 4px;"
                f"font-size: 12px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: {_C_ACCENT}; }}"
                f"QPushButton:pressed {{ background: #174E90; }}"
            )
        return (
            f"QPushButton {{ background: #1C2333; color: {_C_MUTED};"
            f"border: 1px solid {_C_BORDER}; border-radius: 4px; font-size: 12px; }}"
            f"QPushButton:hover {{ border-color: {_C_ACCENT}; color: {_C_FG}; }}"
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def accept(self) -> None:
        if not self.name():
            self._error_lbl.setText("Folder name cannot be empty.")
            self._error_lbl.setVisible(True)
            self._name_edit.setFocus()
            return
        self._error_lbl.setVisible(False)
        super().accept()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._name_edit.setFocus()