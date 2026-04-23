"""
ui/password_dialog.py
Password entry dialog for opening and creating encrypted .dcmpack archives.
Styled to match the application's dark medical aesthetic.

Modes
-----
"open"   — single field; used when the caller detects a protected pack and
           needs a password before extraction can begin.
"create" — two fields (password + confirm); used when the user is building a
           pack and may optionally encrypt it. Both fields left empty is a
           valid choice that signals the caller to skip encryption entirely.
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

# ---------------------------------------------------------------------------
# Theme constants (mirrors values in style.qss)
# ---------------------------------------------------------------------------

_C_BG      = "#0F1117"
_C_PANEL   = "#0D1320"
_C_INPUT   = "#080C12"
_C_BORDER  = "#2E3A50"
_C_ACCENT  = "#2A7AD4"
_C_FG      = "#D4D8DE"
_C_MUTED   = "#8A98AA"
_C_DIMMED  = "#5A7FA8"
_C_ERROR   = "#A83040"

_MIN_PW_LEN = 4

# ---------------------------------------------------------------------------
# Per-mode UI strings
# ---------------------------------------------------------------------------

_MODE_CONFIG: dict[str, dict] = {
    "open": {
        "title":       "Open Protected Pack",
        "description": (
            "This archive is password-protected.\n"
            "Enter the password to continue."
        ),
        "ok_label": "Open Pack",
        "confirm":  False,
    },
    "create": {
        "title":       "Set Pack Password",
        "description": (
            "Assign a password to encrypt this archive with AES-256.\n"
            "Leave both fields empty to create an unencrypted pack."
        ),
        "ok_label": "Set Password",
        "confirm":  True,
    },
}


# ---------------------------------------------------------------------------
# Internal widget: labelled password field with Show / Hide toggle
# ---------------------------------------------------------------------------

class _PasswordField(QWidget):
    """
    A labelled, masked QLineEdit with a Show / Hide visibility toggle.

    Follows the same compound-widget pattern as _SliderRow in dicom_viewer.py:
    the widget owns its label, input, and button and exposes a minimal API to
    the parent dialog.
    """

    def __init__(self, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {_C_DIMMED}; font-size: 11px;")

        self._edit = QLineEdit()
        self._edit.setEchoMode(QLineEdit.Password)
        self._edit.setMinimumHeight(34)
        self._edit.setStyleSheet(
            f"QLineEdit {{"
            f"  background-color: {_C_INPUT};"
            f"  border: 1px solid {_C_BORDER};"
            f"  border-radius: 4px;"
            f"  padding: 4px 10px;"
            f"  color: {_C_FG};"
            f"  font-size: 13px;"
            f"}}"
            f"QLineEdit:focus {{"
            f"  border-color: {_C_ACCENT};"
            f"}}"
        )

        self._toggle = QPushButton("Show")
        self._toggle.setFixedSize(52, 34)
        self._toggle.setCheckable(True)
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setStyleSheet(
            f"QPushButton {{"
            f"  background: transparent;"
            f"  border: 1px solid {_C_BORDER};"
            f"  border-radius: 4px;"
            f"  color: {_C_DIMMED};"
            f"  font-size: 11px;"
            f"}}"
            f"QPushButton:hover {{"
            f"  border-color: {_C_ACCENT};"
            f"  color: {_C_FG};"
            f"}}"
            f"QPushButton:checked {{"
            f"  color: {_C_FG};"
            f"}}"
        )
        self._toggle.clicked.connect(self._on_toggle)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)
        input_row.addWidget(self._edit)
        input_row.addWidget(self._toggle)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(lbl)
        col.addLayout(input_row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def value(self) -> str:
        """Return the current text, regardless of visibility state."""
        return self._edit.text()

    def clear(self) -> None:
        self._edit.clear()

    def set_focus(self) -> None:
        self._edit.setFocus()

    def connect_return_pressed(self, slot) -> None:
        """Forward QLineEdit.returnPressed to an external slot."""
        self._edit.returnPressed.connect(slot)

    # ------------------------------------------------------------------
    # Slot
    # ------------------------------------------------------------------

    def _on_toggle(self, checked: bool) -> None:
        self._edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._toggle.setText("Hide" if checked else "Show")


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class PasswordDialog(QDialog):
    """
    Branded password dialog for encrypted .dcmpack operations.

    Do not instantiate directly — use ask_password() instead.

    After exec_() returns QDialog.Accepted, call password() to retrieve
    the entered value. In "create" mode an empty string is a valid result
    and means the caller should skip encryption.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        mode: str = "open",
    ) -> None:
        if mode not in _MODE_CONFIG:
            raise ValueError(f"mode must be 'open' or 'create', got {mode!r}")

        super().__init__(parent)
        self._mode          = mode
        self._cfg           = _MODE_CONFIG[mode]
        self._confirm_field: Optional[_PasswordField] = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def password(self) -> str:
        """
        Return the accepted password string.

        An empty string in "create" mode is intentional — the user chose
        not to encrypt the archive. Must only be called after exec_() has
        returned QDialog.Accepted.
        """
        return self._pw_field.value()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(self._cfg["title"])
        self.setModal(True)
        self.setFixedWidth(420)
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
        body.setContentsMargins(28, 24, 28, 24)
        body.setSpacing(16)

        # Title
        title_lbl = QLabel(self._cfg["title"])
        title_lbl.setStyleSheet(
            f"color: {_C_FG}; font-size: 14px; font-weight: 600;"
        )
        body.addWidget(title_lbl)

        # Description
        desc_lbl = QLabel(self._cfg["description"])
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(
            f"color: {_C_MUTED}; font-size: 12px;"
        )
        body.addWidget(desc_lbl)

        body.addWidget(self._divider())

        # Primary password field
        self._pw_field = _PasswordField("Password")
        self._pw_field.connect_return_pressed(self.accept)
        body.addWidget(self._pw_field)

        # Confirm field — create mode only
        if self._cfg["confirm"]:
            self._confirm_field = _PasswordField("Confirm Password")
            self._confirm_field.connect_return_pressed(self.accept)
            body.addWidget(self._confirm_field)

        # Error label — hidden until validation fails
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(
            f"color: {_C_ERROR}; font-size: 11px;"
        )
        self._error_label.setVisible(False)
        body.addWidget(self._error_label)

        body.addWidget(self._divider())

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(88, 34)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #1C2333;"
            f"  color: {_C_MUTED};"
            f"  border: 1px solid {_C_BORDER};"
            f"  border-radius: 4px;"
            f"  font-size: 12px;"
            f"}}"
            f"QPushButton:hover {{"
            f"  border-color: {_C_ACCENT};"
            f"  color: {_C_FG};"
            f"}}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._ok_btn = QPushButton(self._cfg["ok_label"])
        self._ok_btn.setFixedSize(112, 34)
        self._ok_btn.setDefault(True)
        self._ok_btn.setCursor(Qt.PointingHandCursor)
        self._ok_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #1F5FAD;"
            f"  color: #FFFFFF;"
            f"  border: 1px solid {_C_ACCENT};"
            f"  border-radius: 4px;"
            f"  font-size: 12px;"
            f"  font-weight: 600;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {_C_ACCENT};"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background-color: #174E90;"
            f"}}"
        )
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._ok_btn)

        body.addLayout(btn_row)
        root.addLayout(body)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {_C_BORDER};")
        return line

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.setVisible(True)
        self.adjustSize()

    def _clear_error(self) -> None:
        self._error_label.setVisible(False)
        self._error_label.clear()

    # ------------------------------------------------------------------
    # Validation  (create mode only)
    # ------------------------------------------------------------------

    def _validate_create(self) -> bool:
        """
        Validate both password fields.

        Rules:
          - The two fields must match.
          - If non-empty, the password must meet the minimum length.
          - Empty input in both fields is explicitly permitted — it signals
            the caller to create an unencrypted archive.

        Returns True when input is acceptable; False when a validation error
        was shown and the dialog should remain open.
        """
        pw  = self._pw_field.value()
        cpw = self._confirm_field.value() if self._confirm_field else ""

        if pw != cpw:
            self._show_error("Passwords do not match.")
            self._confirm_field.clear()
            self._confirm_field.set_focus()
            return False

        if pw and len(pw) < _MIN_PW_LEN:
            self._show_error(
                f"Password must be at least {_MIN_PW_LEN} characters, "
                "or leave both fields empty to skip encryption."
            )
            self._pw_field.set_focus()
            return False

        return True

    # ------------------------------------------------------------------
    # QDialog overrides
    # ------------------------------------------------------------------

    def accept(self) -> None:
        """
        Validate before closing. In create mode a failed validation clears
        the error-prone field, shows the reason, and keeps the dialog open.
        """
        self._clear_error()

        if self._mode == "create" and not self._validate_create():
            return

        super().accept()

    def showEvent(self, event) -> None:
        """Place cursor in the primary password field on first display."""
        super().showEvent(event)
        self._pw_field.set_focus()


# ---------------------------------------------------------------------------
# Public convenience wrapper
# ---------------------------------------------------------------------------

def ask_password(
    parent: Optional[QWidget],
    mode: str = "open",
) -> Optional[str]:
    """
    Show a password dialog and return the result, or None if cancelled.

    This is the preferred entry point — callers should not instantiate
    PasswordDialog directly.

    Args:
        parent: Parent widget for the dialog (may be None).
        mode:   "open"   — single field for extracting a protected pack.
                "create" — two fields for setting an optional pack password.
                           Returns "" when the user chooses not to encrypt.

    Returns:
        The entered password string (non-empty for "open"; possibly empty
        for "create"), or None if the user dismissed the dialog.
    """
    dlg = PasswordDialog(parent, mode=mode)
    if dlg.exec_() == QDialog.Accepted:
        return dlg.password()
    return None