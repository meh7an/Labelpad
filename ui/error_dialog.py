"""
ui/error_dialog.py
Centralised, styled error and warning dialogs.
Replaces raw QMessageBox.critical() calls with consistently branded dialogs
that match the application's dark medical aesthetic.
"""

from __future__ import annotations

import traceback
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Severity levels
# ---------------------------------------------------------------------------

class Severity:
    INFO    = "INFO"
    WARNING = "WARNING"
    ERROR   = "ERROR"


_SEVERITY_COLORS = {
    Severity.INFO:    "#2A7AD4",
    Severity.WARNING: "#C8922A",
    Severity.ERROR:   "#A83040",
}

_SEVERITY_ICONS = {
    Severity.INFO:    "ℹ",
    Severity.WARNING: "⚠",
    Severity.ERROR:   "✕",
}


# ---------------------------------------------------------------------------
# Styled dialog
# ---------------------------------------------------------------------------

class AppDialog(QDialog):
    """
    Branded application dialog for errors, warnings, and informational messages.

    Usage:
        AppDialog.error(parent, "Title", "Something went wrong.")
        AppDialog.warning(parent, "Title", "Proceed with caution.")
        AppDialog.info(parent, "Title", "Operation complete.")
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        title: str,
        message: str,
        severity: str = Severity.ERROR,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._detail = detail
        self._severity = severity
        self._build_ui(title, message)

    # ------------------------------------------------------------------
    # Class-level convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def error(
        cls,
        parent: Optional[QWidget],
        title: str,
        message: str,
        exc: Optional[BaseException] = None,
    ) -> None:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else None
        cls(parent, title, message, severity=Severity.ERROR, detail=detail).exec_()

    @classmethod
    def warning(
        cls,
        parent: Optional[QWidget],
        title: str,
        message: str,
    ) -> None:
        cls(parent, title, message, severity=Severity.WARNING).exec_()

    @classmethod
    def info(
        cls,
        parent: Optional[QWidget],
        title: str,
        message: str,
    ) -> None:
        cls(parent, title, message, severity=Severity.INFO).exec_()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self, title: str, message: str) -> None:
        color = _SEVERITY_COLORS[self._severity]
        icon  = _SEVERITY_ICONS[self._severity]

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet("background-color: #0F1117;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Coloured top bar
        bar = QWidget()
        bar.setFixedHeight(4)
        bar.setStyleSheet(f"background-color: {color};")
        root.addWidget(bar)

        body = QVBoxLayout()
        body.setContentsMargins(28, 24, 28, 20)
        body.setSpacing(16)

        # Icon + title row
        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet(
            f"color: {color}; font-size: 20px; font-weight: 700;"
        )
        icon_lbl.setFixedWidth(28)
        title_row.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #D4D8DE; font-size: 14px; font-weight: 600;")
        title_row.addWidget(title_lbl, stretch=1)
        body.addLayout(title_row)

        # Message
        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("color: #8A98AA; font-size: 12px; line-height: 1.5;")
        msg_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(msg_lbl)

        # Optional detail / traceback (collapsed by default)
        if self._detail:
            self._detail_box = QTextEdit()
            self._detail_box.setReadOnly(True)
            self._detail_box.setPlainText(self._detail)
            self._detail_box.setFixedHeight(160)
            self._detail_box.setVisible(False)
            self._detail_box.setStyleSheet(
                "background: #080C12; color: #5A7FA8; font-family: monospace;"
                "font-size: 10px; border: 1px solid #1E2A3A; border-radius: 4px;"
            )

            toggle_btn = QPushButton("Show technical details ▾")
            toggle_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none;"
                "color: #3E4A5C; font-size: 11px; text-align: left; padding: 0; }"
                "QPushButton:hover { color: #5A7FA8; }"
            )
            toggle_btn.setCursor(Qt.PointingHandCursor)
            toggle_btn.clicked.connect(
                lambda: self._toggle_detail(toggle_btn)
            )
            body.addWidget(toggle_btn)
            body.addWidget(self._detail_box)

        # Button row
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("primaryButton")
        ok_btn.setFixedSize(88, 34)
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.clicked.connect(self.accept)
        btn_box.addWidget(ok_btn)
        body.addLayout(btn_box)

        root.addLayout(body)

    def _toggle_detail(self, btn: QPushButton) -> None:
        visible = self._detail_box.isVisible()
        self._detail_box.setVisible(not visible)
        btn.setText("Hide technical details ▴" if not visible else "Show technical details ▾")
        self.adjustSize()


# ---------------------------------------------------------------------------
# Global exception hook (wires into main.py)
# ---------------------------------------------------------------------------

def install_exception_hook(parent_provider) -> None:
    """
    Replace sys.excepthook so any unhandled exception surfaces as a styled
    dialog rather than a silent crash or a raw console traceback.

    Args:
        parent_provider: A zero-argument callable returning the QWidget to
                         use as the dialog parent (typically lambda: window).
    """
    import sys

    def _hook(exc_type, exc_value, exc_tb):
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        AppDialog(
            parent_provider(),
            title="Unexpected Error",
            message=(
                f"An unexpected error occurred:\n\n"
                f"{exc_type.__name__}: {exc_value}\n\n"
                "The application will attempt to continue."
            ),
            severity=Severity.ERROR,
            detail=detail,
        ).exec_()

    sys.excepthook = _hook