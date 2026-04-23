"""
ui/pack_info_dialog.py
Read-only pack preview dialog shown before every import.

The dialog receives an already-parsed DcmPackManifest — the caller is
responsible for password resolution and manifest reading. This keeps the
dialog stateless and testable without any filesystem access.

Usage
-----
    with open_pack(path, password) as zf:
        manifest = read_manifest(zf)

    if show_pack_info(parent, manifest, path):
        # user confirmed — proceed with extraction
        ...
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.dcmpack import DcmPackManifest

# ---------------------------------------------------------------------------
# Theme constants (mirrors values in style.qss)
# ---------------------------------------------------------------------------

_C_BG       = "#0F1117"
_C_PANEL    = "#0A0F1A"
_C_BORDER   = "#1E2A3A"
_C_ACCENT   = "#2A7AD4"
_C_FG       = "#D4D8DE"
_C_MUTED    = "#8A98AA"
_C_DIMMED   = "#5A7FA8"
_C_LABELED  = "#3E8E41"
_C_UNLABELED = "#5A7FA8"
_C_WARNING  = "#C8922A"
_C_ERROR    = "#A83040"

_LABEL_PROTECTED   = "PROTECTED"
_LABEL_UNPROTECTED = "OPEN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_datetime(iso_str: str) -> str:
    """
    Convert an ISO 8601 string to a human-readable local representation.
    Falls back to the raw string if parsing fails, or 'Unknown' if empty.
    """
    if not iso_str:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(iso_str)
        # Normalise to UTC for display consistency across machines.
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.strftime("%-d %B %Y  \u00b7  %H:%M UTC")
    except (ValueError, OSError):
        return iso_str


def _format_tags(tags: tuple[str, ...]) -> str:
    return ",  ".join(tags) if tags else "\u2014"  # em-dash when empty


# ---------------------------------------------------------------------------
# Internal widgets
# ---------------------------------------------------------------------------

class _Badge(QLabel):
    """
    Small coloured pill label used for the password-protection indicator.
    Styled inline so it does not rely on QSS object names.
    """

    def __init__(self, text: str, color: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setStyleSheet(
            f"QLabel {{"
            f"  background-color: {color}22;"    # 13 % opacity fill
            f"  color: {color};"
            f"  border: 1px solid {color}66;"    # 40 % opacity border
            f"  border-radius: 3px;"
            f"  font-size: 10px;"
            f"  font-weight: 700;"
            f"  letter-spacing: 1px;"
            f"  padding: 2px 7px;"
            f"}}"
        )
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class PackInfoDialog(QDialog):
    """
    Read-only preview dialog for a .dcmpack archive.

    Displays all manifest metadata and the full item list. The user can
    either confirm the import or cancel. No extraction is performed here.

    Args:
        parent:    Qt parent widget (may be None).
        manifest:  Already-parsed DcmPackManifest for the archive.
        pack_path: Filesystem path to the .dcmpack file — used for the
                   filename display only; the file is not re-opened.
    """

    def __init__(
        self,
        parent:    Optional[QWidget],
        manifest:  DcmPackManifest,
        pack_path: Path,
    ) -> None:
        super().__init__(parent)
        self._manifest  = manifest
        self._pack_path = pack_path
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("Pack Info")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(f"background-color: {_C_BG};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Accent bar
        bar = QWidget()
        bar.setFixedHeight(4)
        bar.setStyleSheet(f"background-color: {_C_ACCENT};")
        root.addWidget(bar)

        body = QVBoxLayout()
        body.setContentsMargins(26, 20, 26, 20)
        body.setSpacing(14)

        body.addLayout(self._build_header())
        body.addWidget(self._divider())
        body.addWidget(self._section_label("METADATA"))
        body.addWidget(self._build_metadata_panel())
        body.addWidget(self._divider())
        body.addWidget(self._section_label("CONTENTS"))
        body.addWidget(self._build_item_list())
        body.addWidget(self._divider())
        body.addLayout(self._build_button_row())

        root.addLayout(body)

    def _build_header(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(6)

        # Pack name (large) + protection badge on the same row
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        name_row.setContentsMargins(0, 0, 0, 0)

        name_lbl = QLabel(self._manifest.pack_name or self._pack_path.stem)
        name_lbl.setStyleSheet(
            f"color: {_C_FG}; font-size: 15px; font-weight: 700;"
        )
        name_row.addWidget(name_lbl)

        if self._manifest.password_protected:
            name_row.addWidget(_Badge(_LABEL_PROTECTED, _C_WARNING))
        else:
            name_row.addWidget(_Badge(_LABEL_UNPROTECTED, _C_DIMMED))

        name_row.addStretch()
        layout.addLayout(name_row)

        # Filename (smaller, muted)
        file_lbl = QLabel(self._pack_path.name)
        file_lbl.setStyleSheet(f"color: {_C_DIMMED}; font-size: 11px;")
        layout.addWidget(file_lbl)

        return layout

    def _build_metadata_panel(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"background-color: {_C_PANEL};"
            f"border: 1px solid {_C_BORDER}; border-radius: 4px;"
        )

        m    = self._manifest
        n    = len(m.items)
        n_lb = sum(1 for i in m.items if i.labeled)

        if n == 1:
            items_str = f"1 item  \u00b7  {n_lb} labeled"
        else:
            items_str = f"{n} items  \u00b7  {n_lb} labeled,  {n - n_lb} unlabeled"

        rows = [
            ("Created",     _format_datetime(m.created_at)),
            ("Author",      m.author      or "\u2014"),
            ("Description", m.description or "\u2014"),
            ("Tags",        _format_tags(m.tags)),
            ("Items",       items_str),
        ]

        grid = QGridLayout(frame)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        for row_idx, (key, val) in enumerate(rows):
            k_lbl = QLabel(key)
            k_lbl.setStyleSheet(f"color: {_C_DIMMED}; font-size: 11px;")
            k_lbl.setFixedWidth(90)

            v_lbl = QLabel(val)
            v_lbl.setStyleSheet(f"color: {_C_FG}; font-size: 11px;")
            v_lbl.setWordWrap(True)
            v_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)

            grid.addWidget(k_lbl, row_idx, 0, Qt.AlignTop)
            grid.addWidget(v_lbl, row_idx, 1)

        return frame

    def _build_item_list(self) -> QListWidget:
        lw = QListWidget()
        lw.setMaximumHeight(200)
        lw.setFocusPolicy(Qt.NoFocus)
        lw.setStyleSheet(
            f"QListWidget {{"
            f"  background-color: {_C_PANEL};"
            f"  border: 1px solid {_C_BORDER}; border-radius: 4px; outline: none;"
            f"}}"
            f"QListWidget::item {{"
            f"  padding: 6px 12px;"
            f"  border-bottom: 1px solid #111820; font-size: 12px;"
            f"}}"
            f"QListWidget::item:selected {{ background: transparent; }}"
        )

        for item in self._manifest.items:
            status   = "Labeled"   if item.labeled else "Unlabeled"
            color    = _C_LABELED  if item.labeled else _C_UNLABELED
            row_text = f"{item.stem}    [{status}]"

            lw_item = QListWidgetItem(row_text)
            lw_item.setFlags(Qt.ItemIsEnabled)   # visible but not selectable
            lw_item.setForeground(QColor(color))
            lw.addItem(lw_item)

        if not self._manifest.items:
            empty = QListWidgetItem("Pack contains no items.")
            empty.setFlags(Qt.NoItemFlags)
            empty.setForeground(QColor("#2E3A50"))
            lw.addItem(empty)

        return lw

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch()

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
        row.addWidget(cancel_btn)

        import_btn = QPushButton("Import")
        import_btn.setFixedSize(100, 34)
        import_btn.setDefault(True)
        import_btn.setCursor(Qt.PointingHandCursor)
        import_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #1F5FAD; color: #FFFFFF;"
            f"  border: 1px solid {_C_ACCENT}; border-radius: 4px;"
            f"  font-size: 12px; font-weight: 600;"
            f"}}"
            f"QPushButton:hover {{ background-color: {_C_ACCENT}; }}"
            f"QPushButton:pressed {{ background-color: #174E90; }}"
        )
        import_btn.clicked.connect(self.accept)
        row.addWidget(import_btn)

        return row

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


# ---------------------------------------------------------------------------
# Public convenience wrapper
# ---------------------------------------------------------------------------

def show_pack_info(
    parent:    Optional[QWidget],
    manifest:  DcmPackManifest,
    pack_path: Path,
) -> bool:
    """
    Show the PackInfoDialog and return True if the user clicked Import.

    This is the preferred entry point for all callers. The dialog is
    modal and blocks until the user confirms or cancels.

    Args:
        parent:    Qt parent widget (may be None).
        manifest:  Already-parsed DcmPackManifest (caller owns the ZipFile).
        pack_path: Path to the .dcmpack file (used for display only).

    Returns:
        True  — user confirmed, caller should proceed with extraction.
        False — user cancelled, caller should abort silently.
    """
    dlg = PackInfoDialog(parent, manifest, pack_path)
    return dlg.exec_() == QDialog.Accepted