"""
tests/test_app_dialog.py
AppDialog.question smoke tests require a QApplication — provided by the
session-scoped qt_app fixture which uses the offscreen platform so the
suite stays headless.
Run with:  python -m pytest tests/ -v
"""

import os

# Must be set before QApplication is imported anywhere in this process.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication, QPushButton

from ui.error_dialog import AppDialog, Severity


@pytest.fixture(scope="session")
def qt_app():
    """Create (or reuse) a QApplication for the entire test session."""
    app = QApplication.instance() or QApplication([])
    yield app


class TestQuestionDialog:
    def test_constructs_with_custom_button_texts(self, qt_app):
        dlg = AppDialog(
            None, "Update Labelpad", "Update now?",
            severity=Severity.QUESTION, buttons="yes-cancel",
            yes_text="Update", cancel_text="Not now",
        )
        texts = [b.text() for b in dlg.findChildren(QPushButton)]
        assert "Update" in texts
        assert "Not now" in texts

    def test_yes_accepts_and_cancel_rejects(self, qt_app):
        dlg = AppDialog(None, "T", "M", buttons="yes-cancel")
        buttons = {b.text(): b for b in dlg.findChildren(QPushButton)}
        results = []
        dlg.accepted.connect(lambda: results.append("accepted"))
        dlg.rejected.connect(lambda: results.append("rejected"))
        buttons["Yes"].click()
        buttons["Cancel"].click()
        assert results == ["accepted", "rejected"]

    def test_default_ok_dialog_unchanged(self, qt_app):
        dlg = AppDialog(None, "Oops", "Something broke.")
        texts = [b.text() for b in dlg.findChildren(QPushButton)]
        assert texts.count("OK") == 1
        assert "Yes" not in texts
