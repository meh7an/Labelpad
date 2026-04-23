"""
tests/test_pack_info_dialog.py
Unit tests for ui/pack_info_dialog.py

Pure-Python helpers (_format_datetime, _format_tags) need no Qt.
PackInfoDialog smoke tests require a QApplication — provided by the
session-scoped qt_app fixture which uses the offscreen platform so the
suite runs headlessly in CI without a display server.

Run with:  python -m pytest tests/ -v
"""

import os
import sys
from pathlib import Path

# Must be set before QApplication is imported anywhere in this process.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.dcmpack import DcmPackItem, DcmPackManifest
from ui.pack_info_dialog import (
    PackInfoDialog,
    _format_datetime,
    _format_tags,
)


# ---------------------------------------------------------------------------
# Session-scoped QApplication fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qt_app():
    """
    Create (or reuse) a QApplication for the entire test session.

    scope="session" is intentional: Qt forbids creating more than one
    QApplication per process, so a module-scoped fixture would fail on
    the second test module that imports it.
    """
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


# ---------------------------------------------------------------------------
# Manifest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def full_manifest() -> DcmPackManifest:
    """Manifest with two items, all metadata fields populated."""
    return DcmPackManifest(
        schema_version=1,
        pack_name="april_cohort",
        created_at="2026-04-23T10:00:00+00:00",
        password_protected=False,
        items=(
            DcmPackItem("brain_001", labeled=False),
            DcmPackItem("brain_002", labeled=True),
        ),
        author="Dr. Mehran",
        description="April 2026 CT head cohort.",
        tags=("CT", "head", "cohort-A"),
    )


@pytest.fixture
def protected_manifest() -> DcmPackManifest:
    """Manifest flagged as password-protected."""
    return DcmPackManifest(
        schema_version=1,
        pack_name="secure_pack",
        created_at="2026-04-23T00:00:00+00:00",
        password_protected=True,
        items=(DcmPackItem("scan_001", labeled=True),),
    )


@pytest.fixture
def empty_manifest() -> DcmPackManifest:
    """Manifest with no items."""
    return DcmPackManifest(1, "empty_pack", "", False, ())


# ---------------------------------------------------------------------------
# _format_datetime
# ---------------------------------------------------------------------------

class TestFormatDatetime:
    def test_valid_utc_iso_contains_year(self):
        result = _format_datetime("2026-04-23T10:00:00+00:00")
        assert "2026" in result

    def test_valid_utc_iso_contains_utc_label(self):
        result = _format_datetime("2026-04-23T10:00:00+00:00")
        assert "UTC" in result

    def test_valid_utc_iso_contains_time(self):
        result = _format_datetime("2026-04-23T10:00:00+00:00")
        assert "10:00" in result

    def test_offset_aware_string_normalised_to_utc(self):
        # +03:00 offset → 07:00 UTC
        result = _format_datetime("2026-04-23T10:00:00+03:00")
        assert "07:00" in result

    def test_empty_string_returns_unknown(self):
        assert _format_datetime("") == "Unknown"

    def test_invalid_string_returned_as_is(self):
        assert _format_datetime("not a date") == "not a date"

    def test_returns_string(self):
        assert isinstance(_format_datetime("2026-04-23T10:00:00+00:00"), str)

    def test_non_empty_result_for_valid_input(self):
        assert _format_datetime("2026-04-23T10:00:00+00:00") != ""


# ---------------------------------------------------------------------------
# _format_tags
# ---------------------------------------------------------------------------

class TestFormatTags:
    def test_non_empty_tuple_contains_all_tags(self):
        result = _format_tags(("CT", "head", "cohort-A"))
        assert "CT"       in result
        assert "head"     in result
        assert "cohort-A" in result

    def test_single_tag(self):
        result = _format_tags(("CT",))
        assert "CT" in result

    def test_empty_tuple_returns_em_dash(self):
        # Em-dash (\u2014) used as the empty-state placeholder.
        assert _format_tags(()) == "\u2014"

    def test_returns_string(self):
        assert isinstance(_format_tags(("CT",)), str)


# ---------------------------------------------------------------------------
# PackInfoDialog — smoke tests
# ---------------------------------------------------------------------------

class TestPackInfoDialog:
    """
    Construction-level smoke tests: verify the dialog builds without error
    and exposes the expected widget state.  No exec_() is called — these
    tests never block waiting for user input.
    """

    def test_constructs_without_error(self, qt_app, full_manifest, tmp_path):
        dlg = PackInfoDialog(None, full_manifest, tmp_path / "april_cohort.dcmpack")
        assert dlg is not None

    def test_window_title_is_pack_info(self, qt_app, full_manifest, tmp_path):
        dlg = PackInfoDialog(None, full_manifest, tmp_path / "april_cohort.dcmpack")
        assert dlg.windowTitle() == "Pack Info"

    def test_item_list_count_matches_manifest(self, qt_app, full_manifest, tmp_path):
        dlg = PackInfoDialog(None, full_manifest, tmp_path / "april_cohort.dcmpack")
        assert dlg._file_list.count() == len(full_manifest.items)

    def test_item_list_count_for_single_item(self, qt_app, protected_manifest, tmp_path):
        dlg = PackInfoDialog(None, protected_manifest, tmp_path / "secure_pack.dcmpack")
        assert dlg._file_list.count() == 1

    def test_empty_manifest_shows_placeholder_row(self, qt_app, empty_manifest, tmp_path):
        dlg = PackInfoDialog(None, empty_manifest, tmp_path / "empty.dcmpack")
        # Empty item list → one non-interactive placeholder row.
        assert dlg._file_list.count() == 1

    def test_item_list_rows_are_not_selectable(self, qt_app, full_manifest, tmp_path):
        """Content rows must be enabled-but-not-selectable (Qt.ItemIsEnabled only)."""
        from PyQt5.QtCore import Qt
        dlg  = PackInfoDialog(None, full_manifest, tmp_path / "april_cohort.dcmpack")
        item = dlg._file_list.item(0)
        assert not (item.flags() & Qt.ItemIsSelectable)
        assert     (item.flags() & Qt.ItemIsEnabled)

    def test_labeled_item_text_contains_labeled(self, qt_app, full_manifest, tmp_path):
        dlg = PackInfoDialog(None, full_manifest, tmp_path / "april_cohort.dcmpack")
        # brain_002 is labeled=True (index 1).
        assert "Labeled" in dlg._file_list.item(1).text()

    def test_unlabeled_item_text_contains_unlabeled(self, qt_app, full_manifest, tmp_path):
        dlg = PackInfoDialog(None, full_manifest, tmp_path / "april_cohort.dcmpack")
        # brain_001 is labeled=False (index 0).
        assert "Unlabeled" in dlg._file_list.item(0).text()

    def test_dialog_is_modal(self, qt_app, full_manifest, tmp_path):
        dlg = PackInfoDialog(None, full_manifest, tmp_path / "april_cohort.dcmpack")
        assert dlg.isModal()

    def test_minimum_width_enforced(self, qt_app, full_manifest, tmp_path):
        dlg = PackInfoDialog(None, full_manifest, tmp_path / "april_cohort.dcmpack")
        assert dlg.minimumWidth() >= 500

    def test_constructs_with_protected_manifest(self, qt_app, protected_manifest, tmp_path):
        dlg = PackInfoDialog(None, protected_manifest, tmp_path / "secure.dcmpack")
        assert dlg is not None

    def test_constructs_with_empty_metadata_fields(self, qt_app, tmp_path):
        """Manifest with no author/description/tags must not raise."""
        bare = DcmPackManifest(
            schema_version=1,
            pack_name="bare",
            created_at="",
            password_protected=False,
            items=(DcmPackItem("scan", labeled=False),),
        )
        dlg = PackInfoDialog(None, bare, tmp_path / "bare.dcmpack")
        assert dlg is not None