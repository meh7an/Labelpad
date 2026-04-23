"""
ui/pack_export_dialog.py
Pack export dialog — browse all DICOM files in the working directory,
select items to bundle, optionally encrypt, then write a .dcmpack archive.

Workflow
--------
1. File list is populated from UNLABELED_DIR on open.
2. User checks items, names the pack, and optionally sets a password.
3. Clicking "Export Pack..." opens a native save-file dialog.
4. create_pack() runs synchronously behind a brief progress dialog.
5. Dialog closes; call created_path() to retrieve the output path.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QThread  
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.dcmpack import DcmPackError, create_pack
from core.folder_store import FolderStore
from core.paths import LABELED_DIR, UNLABELED_DIR
from core.status import FileStatus, STATUS_COLORS, resolve_status
from ui.workers import PackCreator 

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Theme constants (mirrors values in style.qss)
# ---------------------------------------------------------------------------

_C_BG     = "#0F1117"
_C_INPUT  = "#080C12"
_C_BORDER = "#2E3A50"
_C_ACCENT = "#2A7AD4"
_C_FG     = "#D4D8DE"
_C_MUTED  = "#8A98AA"
_C_DIMMED = "#5A7FA8"
_C_ERROR  = "#A83040"

_MIN_PW_LEN         = 4
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _parse_tags(raw: str) -> list[str]:
    """
    Split a comma-separated tag string into a clean list.
    Strips surrounding whitespace from each entry and drops empty tokens.

    Examples:
        "CT, head,  cohort-A"  → ["CT", "head", "cohort-A"]
        "  ,  "                → []
        ""                     → []
    """
    return [t.strip() for t in raw.split(",") if t.strip()]


def _default_author() -> str:
    """Return the OS login name, or an empty string if unavailable."""
    try:
        return os.getlogin()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Path references — may be overridden in tests via monkeypatch.
# ---------------------------------------------------------------------------

_UNLABELED_DIR: Path = UNLABELED_DIR
_LABELED_DIR:   Path = LABELED_DIR


# ---------------------------------------------------------------------------
# Internal widget: masked password field with Show / Hide toggle
# ---------------------------------------------------------------------------

class _MaskedField(QWidget):
    """
    Labelled, masked QLineEdit with a Show / Hide visibility toggle.

    Self-contained counterpart of password_dialog._PasswordField, kept
    private to this module to avoid cross-module private-name imports.
    """

    def __init__(self, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {_C_DIMMED}; font-size: 11px;")

        self._edit = QLineEdit()
        self._edit.setEchoMode(QLineEdit.Password)
        self._edit.setMinimumHeight(32)
        self._edit.setStyleSheet(
            f"QLineEdit {{"
            f"  background-color: {_C_INPUT}; border: 1px solid {_C_BORDER};"
            f"  border-radius: 4px; padding: 4px 10px;"
            f"  color: {_C_FG}; font-size: 13px;"
            f"}}"
            f"QLineEdit:focus {{ border-color: {_C_ACCENT}; }}"
        )

        self._toggle = QPushButton("Show")
        self._toggle.setFixedSize(52, 32)
        self._toggle.setCheckable(True)
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setStyleSheet(
            f"QPushButton {{"
            f"  background: transparent; border: 1px solid {_C_BORDER};"
            f"  border-radius: 4px; color: {_C_DIMMED}; font-size: 11px;"
            f"}}"
            f"QPushButton:hover {{ border-color: {_C_ACCENT}; color: {_C_FG}; }}"
            f"QPushButton:checked {{ color: {_C_FG}; }}"
        )
        self._toggle.clicked.connect(self._on_toggle)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self._edit)
        row.addWidget(self._toggle)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        col.addWidget(lbl)
        col.addLayout(row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def value(self) -> str:
        return self._edit.text()

    def clear(self) -> None:
        self._edit.clear()

    def set_focus(self) -> None:
        self._edit.setFocus()

    # ------------------------------------------------------------------
    # Slot
    # ------------------------------------------------------------------

    def _on_toggle(self, checked: bool) -> None:
        self._edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._toggle.setText("Hide" if checked else "Show")


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class PackExportDialog(QDialog):
    """
    Dialog for creating a .dcmpack archive from files in the working directory.

    Do not drive this dialog manually — open it via MainWindow._export_pack().
    After exec_() returns QDialog.Accepted, call created_path() to retrieve
    the path of the exported archive.

    The dialog body is hosted inside a QScrollArea so the window remains
    usable on monitors with limited vertical space. The button row is pinned
    outside the scroll area and is always visible.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        preselected_stems: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._created_path: Path | None    = None
        self._preselected_stems: set[str]  = set(preselected_stems or [])
        self._store = FolderStore()
        self._build_ui()
        self._populate_file_list()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def created_path(self) -> Path | None:
        """
        Return the resolved path of the successfully created archive,
        or None if the dialog was cancelled. Must be called only after
        exec_() has returned QDialog.Accepted.
        """
        return self._created_path

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("Export Pack")
        self.setModal(True)
        self.setMinimumWidth(560)
        # Minimum height is intentionally kept short so the dialog fits on
        # small monitors; the scroll area handles overflow vertically.
        self.setMinimumHeight(320)
        self.resize(560, 640)
        self.setSizeGripEnabled(True)
        self.setStyleSheet(f"background-color: {_C_BG};")

        # Root layout: accent bar | scroll area | divider | button row
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Accent bar
        bar = QWidget()
        bar.setFixedHeight(4)
        bar.setStyleSheet(f"background-color: {_C_ACCENT};")
        root.addWidget(bar)

        # --- Scrollable form body ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            # Transparent viewport so the dialog background shows through.
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            f"QScrollBar:vertical {{"
            f"  background: {_C_BG}; width: 8px; margin: 0;"
            f"}}"
            f"QScrollBar::handle:vertical {{"
            f"  background: {_C_BORDER}; border-radius: 4px; min-height: 24px;"
            f"}}"
            f"QScrollBar::handle:vertical:hover {{ background: {_C_DIMMED}; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
        )

        form_widget = QWidget()
        form_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        form_widget.setStyleSheet("background: transparent;")
        body = QVBoxLayout(form_widget)
        body.setContentsMargins(24, 20, 24, 16)
        body.setSpacing(14)

        scroll.setWidget(form_widget)
        root.addWidget(scroll, stretch=1)

        # --- Title + description ---
        title = QLabel("Export Pack")
        title.setStyleSheet(f"color: {_C_FG}; font-size: 14px; font-weight: 600;")
        body.addWidget(title)

        desc = QLabel(
            "Select the DICOM files to bundle. Labeled items automatically "
            "include their raster, windowing metadata, and annotation."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {_C_MUTED}; font-size: 12px;")
        body.addWidget(desc)

        body.addWidget(self._divider())

        # --- Files section ---
        body.addWidget(self._section_label("FILES"))

        self._file_list = QListWidget()
        self._file_list.setMinimumHeight(160)
        self._file_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._file_list.setStyleSheet(
            f"QListWidget {{"
            f"  background-color: {_C_INPUT}; border: 1px solid {_C_BORDER};"
            f"  border-radius: 4px; outline: none;"
            f"}}"
            f"QListWidget::item {{"
            f"  padding: 7px 12px; border-bottom: 1px solid #111820; font-size: 12px;"
            f"}}"
            f"QListWidget::item:hover {{ background-color: #141D2E; }}"
            f"QListWidget::item:selected {{ background-color: #1A3050; }}"
        )
        self._file_list.itemChanged.connect(self._on_item_changed)
        body.addWidget(self._file_list, stretch=1)

        # Selection controls row
        sel_row = QHBoxLayout()
        sel_row.setContentsMargins(0, 2, 0, 0)
        sel_row.setSpacing(8)

        self._sel_label = QLabel("0 files selected")
        self._sel_label.setStyleSheet(f"color: {_C_DIMMED}; font-size: 11px;")
        sel_row.addWidget(self._sel_label)
        sel_row.addStretch()

        _quick_btn_style = (
            f"QPushButton {{"
            f"  background: transparent; border: 1px solid {_C_BORDER};"
            f"  border-radius: 3px; color: {_C_DIMMED};"
            f"  font-size: 11px; padding: 0 10px;"
            f"}}"
            f"QPushButton:hover {{ border-color: {_C_ACCENT}; color: {_C_FG}; }}"
        )
        for text, slot in (
            ("Select All Labeled", self._select_all_labeled),
            ("Select All",         self._select_all),
            ("Clear",              self._clear_all),
        ):
            btn = QPushButton(text)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_quick_btn_style)
            btn.clicked.connect(slot)
            sel_row.addWidget(btn)

        body.addLayout(sel_row)
        body.addWidget(self._divider())

        # --- Pack name section ---
        body.addWidget(self._section_label("PACK NAME"))

        self._name_edit = QLineEdit()
        self._name_edit.setText(datetime.now().strftime("%Y-%m-%d") + "_labelpad")
        self._name_edit.setPlaceholderText("pack_name  (no extension needed)")
        self._name_edit.setMinimumHeight(32)
        self._name_edit.setStyleSheet(
            f"QLineEdit {{"
            f"  background-color: {_C_INPUT}; border: 1px solid {_C_BORDER};"
            f"  border-radius: 4px; padding: 4px 10px;"
            f"  color: {_C_FG}; font-size: 13px;"
            f"}}"
            f"QLineEdit:focus {{ border-color: {_C_ACCENT}; }}"
        )
        body.addWidget(self._name_edit)

        body.addWidget(self._divider())

        # --- Metadata section ---
        body.addWidget(self._section_label("METADATA"))

        _field_style = (
            f"background-color: {_C_INPUT}; border: 1px solid {_C_BORDER};"
            f"border-radius: 4px; padding: 4px 10px;"
            f"color: {_C_FG}; font-size: 13px;"
        )
        _field_focus = f"border-color: {_C_ACCENT};"

        author_lbl = QLabel("Author")
        author_lbl.setStyleSheet(f"color: {_C_DIMMED}; font-size: 11px;")
        self._author_edit = QLineEdit()
        self._author_edit.setText(_default_author())
        self._author_edit.setPlaceholderText("Your name or organisation")
        self._author_edit.setMinimumHeight(32)
        self._author_edit.setStyleSheet(
            f"QLineEdit {{ {_field_style} }}"
            f"QLineEdit:focus {{ {_field_focus} }}"
        )
        body.addWidget(author_lbl)
        body.addWidget(self._author_edit)

        desc_lbl = QLabel("Description")
        desc_lbl.setStyleSheet(f"color: {_C_DIMMED}; font-size: 11px;")
        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setPlaceholderText("Optional description of this pack\u2026")
        self._desc_edit.setFixedHeight(72)
        self._desc_edit.setStyleSheet(
            f"QPlainTextEdit {{ {_field_style} }}"
            f"QPlainTextEdit:focus {{ {_field_focus} }}"
        )
        body.addWidget(desc_lbl)
        body.addWidget(self._desc_edit)

        tags_lbl = QLabel("Tags")
        tags_lbl.setStyleSheet(f"color: {_C_DIMMED}; font-size: 11px;")
        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("e.g.  CT,  head,  cohort-A  (comma-separated)")
        self._tags_edit.setMinimumHeight(32)
        self._tags_edit.setStyleSheet(
            f"QLineEdit {{ {_field_style} }}"
            f"QLineEdit:focus {{ {_field_focus} }}"
        )
        body.addWidget(tags_lbl)
        body.addWidget(self._tags_edit)

        body.addWidget(self._divider())

        body.addWidget(self._section_label("FOLDER STRUCTURE"))

        self._folder_check = QCheckBox("Include folder membership and mandatory labels")
        self._folder_check.setChecked(True)
        self._folder_check.setStyleSheet(
            f"QCheckBox {{ color: {_C_MUTED}; font-size: 12px; spacing: 8px; }}"
            f"QCheckBox::indicator {{"
            f"  width: 16px; height: 16px;"
            f"  border: 1px solid {_C_BORDER}; border-radius: 3px;"
            f"  background: {_C_INPUT};"
            f"}}"
            f"QCheckBox::indicator:checked {{"
            f"  background: {_C_ACCENT}; border-color: {_C_ACCENT};"
            f"}}"
            f"QCheckBox::indicator:hover {{ border-color: {_C_ACCENT}; }}"
        )
        body.addWidget(self._folder_check)

        body.addWidget(self._divider())

        body.addWidget(self._section_label("ENCRYPTION"))

        self._encrypt_check = QCheckBox("Encrypt with password  (AES-256)")
        self._encrypt_check.setStyleSheet(
            f"QCheckBox {{ color: {_C_MUTED}; font-size: 12px; spacing: 8px; }}"
            f"QCheckBox::indicator {{"
            f"  width: 16px; height: 16px;"
            f"  border: 1px solid {_C_BORDER}; border-radius: 3px;"
            f"  background: {_C_INPUT};"
            f"}}"
            f"QCheckBox::indicator:checked {{"
            f"  background: {_C_ACCENT}; border-color: {_C_ACCENT};"
            f"}}"
            f"QCheckBox::indicator:hover {{ border-color: {_C_ACCENT}; }}"
        )
        self._encrypt_check.stateChanged.connect(self._on_encrypt_toggled)
        body.addWidget(self._encrypt_check)

        # Password container — hidden until checkbox is ticked
        self._pw_container = QWidget()
        pw_col = QVBoxLayout(self._pw_container)
        pw_col.setContentsMargins(0, 6, 0, 0)
        pw_col.setSpacing(10)

        self._pw_field      = _MaskedField("Password")
        self._confirm_field = _MaskedField("Confirm Password")
        pw_col.addWidget(self._pw_field)
        pw_col.addWidget(self._confirm_field)

        self._pw_container.setVisible(False)
        body.addWidget(self._pw_container)

        # Error label — hidden until validation fails
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(f"color: {_C_ERROR}; font-size: 11px;")
        self._error_label.setVisible(False)
        body.addWidget(self._error_label)

        # Push content to the top so it does not stretch on large windows.
        body.addStretch()

        # --- Button row (outside the scroll area, always visible) ---
        btn_divider = QFrame()
        btn_divider.setFrameShape(QFrame.HLine)
        btn_divider.setStyleSheet(f"color: {_C_BORDER};")
        root.addWidget(btn_divider)

        btn_row_container = QWidget()
        btn_row_container.setStyleSheet(f"background-color: {_C_BG};")
        btn_row = QHBoxLayout(btn_row_container)
        btn_row.setContentsMargins(24, 10, 24, 14)
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(88, 34)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #1C2333; color: {_C_MUTED};"
            f"  border: 1px solid {_C_BORDER}; border-radius: 4px; font-size: 12px;"
            f"}}"
            f"QPushButton:hover {{ border-color: {_C_ACCENT}; color: {_C_FG}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._export_btn = QPushButton("Export Pack...")
        self._export_btn.setFixedSize(120, 34)
        self._export_btn.setDefault(True)
        self._export_btn.setCursor(Qt.PointingHandCursor)
        self._export_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #1F5FAD; color: #FFFFFF;"
            f"  border: 1px solid {_C_ACCENT}; border-radius: 4px;"
            f"  font-size: 12px; font-weight: 600;"
            f"}}"
            f"QPushButton:hover {{ background-color: {_C_ACCENT}; }}"
            f"QPushButton:pressed {{ background-color: #174E90; }}"
            f"QPushButton:disabled {{"
            f"  background-color: #132540; border-color: #1A3560; color: #3E5A80;"
            f"}}"
        )
        self._export_btn.clicked.connect(self._on_export)
        btn_row.addWidget(self._export_btn)

        root.addWidget(btn_row_container)

    # ------------------------------------------------------------------
    # File list population
    # ------------------------------------------------------------------

    def _populate_file_list(self) -> None:
        self._file_list.blockSignals(True)
        self._file_list.clear()

        try:
            dcm_files = sorted(
                f for f in _UNLABELED_DIR.iterdir()
                if f.suffix.lower() in (".dcm", ".dicom") and f.is_file()
            )
        except OSError:
            dcm_files = []

        if not dcm_files:
            placeholder = QListWidgetItem("No DICOM files found in Unlabeled/")
            placeholder.setFlags(Qt.NoItemFlags)
            placeholder.setForeground(QColor("#2E3A50"))
            self._file_list.addItem(placeholder)
            self._export_btn.setEnabled(False)
        else:
            for dcm_path in dcm_files:
                status = resolve_status(dcm_path, self._store)
                item   = QListWidgetItem(f"{dcm_path.name}    [{status}]")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                state = Qt.Checked if dcm_path.stem in self._preselected_stems else Qt.Unchecked
                item.setCheckState(state)
                item.setData(Qt.UserRole, dcm_path)
                item.setForeground(QColor(STATUS_COLORS[status]))
                self._file_list.addItem(item)

        self._file_list.blockSignals(False)
        self._update_selection_label()

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _select_all_labeled(self) -> None:
        """Check all items whose label JSON exists in LABELED_DIR."""
        self._file_list.blockSignals(True)
        for i in range(self._file_list.count()):
            item     = self._file_list.item(i)
            dcm_path = item.data(Qt.UserRole)
            if dcm_path is None:
                continue
            labeled = (_LABELED_DIR / f"{dcm_path.stem}.json").exists()
            item.setCheckState(Qt.Checked if labeled else Qt.Unchecked)
        self._file_list.blockSignals(False)
        self._update_selection_label()

    def _select_all(self) -> None:
        self._file_list.blockSignals(True)
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            if item.data(Qt.UserRole) is not None:
                item.setCheckState(Qt.Checked)
        self._file_list.blockSignals(False)
        self._update_selection_label()

    def _clear_all(self) -> None:
        self._file_list.blockSignals(True)
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            if item.data(Qt.UserRole) is not None:
                item.setCheckState(Qt.Unchecked)
        self._file_list.blockSignals(False)
        self._update_selection_label()

    def _selected_stems(self) -> list[str]:
        """Return stems of all checked items."""
        return [
            item.data(Qt.UserRole).stem
            for i in range(self._file_list.count())
            if (item := self._file_list.item(i)).data(Qt.UserRole) is not None
            and item.checkState() == Qt.Checked
        ]

    def _update_selection_label(self) -> None:
        n = len(self._selected_stems())
        self._sel_label.setText(f"{n} file{'s' if n != 1 else ''} selected")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {_C_DIMMED}; font-size: 10px;"
            f"font-weight: 600; letter-spacing: 1.5px;"
        )
        return lbl

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {_C_BORDER};")
        return line

    def _show_error(self, msg: str) -> None:
        self._error_label.setText(msg)
        self._error_label.setVisible(True)

    def _clear_error(self) -> None:
        self._error_label.setVisible(False)
        self._error_label.clear()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        self._update_selection_label()
        self._clear_error()

    def _on_encrypt_toggled(self, state: int) -> None:
        visible = state == Qt.Checked
        self._pw_container.setVisible(visible)
        if visible:
            self._pw_field.set_focus()
        else:
            self._pw_field.clear()
            self._confirm_field.clear()
        # No adjustSize() call here — window is now user-resizable,
        # so we let Qt reflow naturally without forcing a resize.

    # ------------------------------------------------------------------
    # Export action
    # ------------------------------------------------------------------

    def _on_export(self) -> None:
        """
        Validate inputs, open a save-file dialog, then hand off to a
        background QThread so the main thread (and progress dialog) stay
        responsive throughout ZIP creation and optional AES encryption.
        """
        self._clear_error()

        # --- File selection ---
        stems = self._selected_stems()
        if not stems:
            self._show_error("Select at least one file to export.")
            return

        # --- Pack name ---
        pack_name = self._name_edit.text().strip()
        if not pack_name:
            self._show_error("Pack name cannot be empty.")
            self._name_edit.setFocus()
            return
        if _INVALID_NAME_CHARS.search(pack_name):
            self._show_error(
                'Pack name contains invalid characters.  Avoid:  < > : " / \\ | ? *'
            )
            self._name_edit.setFocus()
            return

        # --- Password (encryption enabled only) ---
        password: str | None = None
        if self._encrypt_check.isChecked():
            pw  = self._pw_field.value()
            cpw = self._confirm_field.value()
            if not pw:
                self._show_error(
                    "Enter a password, or uncheck encryption to create an unprotected pack."
                )
                self._pw_field.set_focus()
                return
            if pw != cpw:
                self._show_error("Passwords do not match.")
                self._confirm_field.clear()
                self._confirm_field.set_focus()
                return
            if len(pw) < _MIN_PW_LEN:
                self._show_error(
                    f"Password must be at least {_MIN_PW_LEN} characters."
                )
                self._pw_field.set_focus()
                return
            password = pw

        # --- Destination path (ask before starting the thread) ---
        default_dest = str(Path.home() / f"{pack_name}.dcmpack")
        dest_str, _  = QFileDialog.getSaveFileName(
            self,
            "Save Pack As",
            default_dest,
            "DCMPACK Files (*.dcmpack);;All Files (*)",
        )
        if not dest_str:
            return

        dest = Path(dest_str)
        if dest.suffix.lower() != ".dcmpack":
            dest = dest.with_suffix(".dcmpack")

        # Store before the thread starts so _on_export_cancelled can find it.
        self._pending_dest: Path = dest

        # --- Collect optional metadata ---
        author      = self._author_edit.text().strip()
        description = self._desc_edit.toPlainText().strip()
        tags        = _parse_tags(self._tags_edit.text())

        pack_folders = None
        if self._folder_check.isChecked():
            from core.dcmpack import PackFolder
            selected_set = set(stems)
            matched = [
                PackFolder(
                    id=f.id,
                    name=f.name,
                    mandatory_labels=f.mandatory_labels,
                    stems=tuple(s for s in f.stems if s in selected_set),
                )
                for f in self._store.all_folders()
                if any(s in selected_set for s in f.stems)
            ]
            pack_folders = matched or None

        # --- Progress dialog ---
        # Shown before the thread starts so it is visible immediately.
        self._progress = QProgressDialog(
            f"Creating  {dest.name}\u2026",
            "Cancel",        # label only — actual cancellation not yet supported
            0,
            len(stems),
            self,
        )
        self._progress.setWindowTitle("Exporting Pack")
        self._progress.setMinimumWidth(380)
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)
        # Prevent the dialog from auto-dismissing; route Cancel through the worker.
        self._progress.canceled.connect(self._on_export_cancel)
        self._progress.show()

        # Disable the export button while the operation is in flight.
        self._export_btn.setEnabled(False)

        # --- Worker + thread ---
        self._worker = PackCreator(
            stems=stems,
            dest_path=dest,
            password=password,
            author=author,
            description=description,
            tags=tags,
            pack_folders=pack_folders,
        )
        self._thread = QThread(self)

        self._worker.moveToThread(self._thread)

        # Wire signals before starting.
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_export_progress)
        self._worker.finished.connect(self._on_export_done)
        self._worker.cancelled.connect(self._on_export_cancelled)
        self._worker.failed.connect(self._on_export_error)

        # Ensure cleanup regardless of outcome.
        self._worker.finished.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    # ------------------------------------------------------------------
    # Worker callbacks (all called on the main thread via Qt signals)
    # ------------------------------------------------------------------

    def _on_export_progress(self, current: int, total: int) -> None:
        """Update the progress dialog as each item is written."""
        if self._progress is not None:
            self._progress.setLabelText(
                f"Writing item {current + 1} of {total}\u2026"
            )
            self._progress.setValue(current)

    def _on_export_cancel(self) -> None:
        """User clicked Cancel — ask the worker to stop after the current item."""
        if self._worker is not None:
            self._progress.setLabelText("Cancelling\u2026")
            self._progress.setCancelButton(None)   # prevent double-clicks
            self._worker.cancel()

    def _on_export_cancelled(self) -> None:
        """Worker confirmed it stopped cleanly — discard the partial file."""
        partial = getattr(self, "_pending_dest", None)
        self._cleanup_thread()
        if partial and partial.exists():
            try:
                partial.unlink()
                log.info("Removed partial pack '%s' after cancellation.", partial.name)
            except OSError as exc:
                log.warning("Could not remove partial pack '%s': %s", partial.name, exc)

    def _on_export_done(self, out_path: object) -> None:
        """Pack creation succeeded — tidy up and close the dialog."""
        self._cleanup_thread()
        self._created_path = out_path   # type: ignore[assignment]
        log.info(
            "Exported pack '%s'.", Path(str(out_path)).name
        )
        self.accept()

    def _on_export_error(self, message: str) -> None:
        """Pack creation failed — surface the error and re-enable the button."""
        self._cleanup_thread()
        self._show_error(f"Export failed: {message}")
        log.error("Pack export failed: %s", message)

    def _cleanup_thread(self) -> None:
        """Close the progress dialog and re-enable the export button."""
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        self._export_btn.setEnabled(True)
        self._thread = None     # type: ignore[assignment]
        self._worker = None     # type: ignore[assignment]