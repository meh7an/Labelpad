"""
tests/test_status.py
Unit tests for core/status.py — FileStatus constants, get_annotation_label_names,
and resolve_status (including FolderStore integration).
Run with:  python -m pytest tests/ -v
"""

import json
from pathlib import Path

import pytest

import core.status as status_mod
from core.folder_store import FolderStore
from core.status import (
    FileStatus,
    STATUS_COLORS,
    get_annotation_label_names,
    resolve_status,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def labeled_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect _LABELED_DIR in core.status to a fresh temp directory."""
    lb = tmp_path / "Labeled"
    lb.mkdir()
    monkeypatch.setattr(status_mod, "_LABELED_DIR", lb)
    return lb


@pytest.fixture()
def store(tmp_path: Path) -> FolderStore:
    return FolderStore(json_path=tmp_path / "folders.json")


def _write_annotation(labeled_dir: Path, stem: str, labels: list[str]) -> Path:
    """Write a minimal LabelMe annotation JSON for the given stem."""
    path = labeled_dir / f"{stem}.json"
    path.write_text(
        json.dumps({
            "shapes":      [{"label": l, "shape_type": "polygon", "points": []} for l in labels],
            "imageWidth":  512,
            "imageHeight": 512,
            "imagePath":   f"./{stem}.jpg",
        }),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# FileStatus constants
# ---------------------------------------------------------------------------

class TestFileStatusConstants:
    def test_unlabeled_value(self):
        assert FileStatus.UNLABELED == "Unlabeled"

    def test_in_progress_value(self):
        assert FileStatus.IN_PROGRESS == "In Progress"

    def test_labeled_value(self):
        assert FileStatus.LABELED == "Labeled"

    def test_raster_ready_is_gone(self):
        assert not hasattr(FileStatus, "RASTER_READY")


# ---------------------------------------------------------------------------
# STATUS_COLORS
# ---------------------------------------------------------------------------

class TestStatusColors:
    def test_all_three_statuses_have_a_color(self):
        assert FileStatus.UNLABELED   in STATUS_COLORS
        assert FileStatus.IN_PROGRESS in STATUS_COLORS
        assert FileStatus.LABELED     in STATUS_COLORS

    def test_raster_ready_color_is_gone(self):
        assert "Raster Ready" not in STATUS_COLORS

    def test_colors_are_hex_strings(self):
        for color in STATUS_COLORS.values():
            assert color.startswith("#"), f"Expected hex color, got {color!r}"
            assert len(color) == 7


# ---------------------------------------------------------------------------
# get_annotation_label_names
# ---------------------------------------------------------------------------

class TestGetAnnotationLabelNames:
    def test_returns_empty_set_when_no_json(self, labeled_dir: Path):
        assert get_annotation_label_names("ghost") == set()

    def test_returns_label_names(self, labeled_dir: Path):
        _write_annotation(labeled_dir, "scan", ["tumor", "edema"])
        assert get_annotation_label_names("scan") == {"tumor", "edema"}

    def test_deduplicates_repeated_labels(self, labeled_dir: Path):
        _write_annotation(labeled_dir, "scan", ["tumor", "tumor", "edema"])
        assert get_annotation_label_names("scan") == {"tumor", "edema"}

    def test_returns_empty_set_for_no_shapes(self, labeled_dir: Path):
        (labeled_dir / "scan.json").write_text(
            json.dumps({"shapes": []}), encoding="utf-8"
        )
        assert get_annotation_label_names("scan") == set()

    def test_returns_empty_set_on_corrupt_json(self, labeled_dir: Path):
        (labeled_dir / "scan.json").write_text("{ not valid }", encoding="utf-8")
        assert get_annotation_label_names("scan") == set()

    def test_returns_empty_set_on_missing_label_key(self, labeled_dir: Path):
        (labeled_dir / "scan.json").write_text(
            json.dumps({"shapes": [{"shape_type": "polygon"}]}), encoding="utf-8"
        )
        assert get_annotation_label_names("scan") == set()

    def test_returns_set_not_list(self, labeled_dir: Path):
        _write_annotation(labeled_dir, "scan", ["tumor"])
        assert isinstance(get_annotation_label_names("scan"), set)


# ---------------------------------------------------------------------------
# resolve_status
# ---------------------------------------------------------------------------

class TestResolveStatus:
    def test_no_annotation_returns_unlabeled(self, labeled_dir: Path, store: FolderStore):
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, store) == FileStatus.UNLABELED

    def test_no_annotation_no_store_returns_unlabeled(self, labeled_dir: Path):
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, None) == FileStatus.UNLABELED

    def test_annotation_no_store_returns_labeled(self, labeled_dir: Path):
        _write_annotation(labeled_dir, "scan", ["tumor"])
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, None) == FileStatus.LABELED

    def test_annotation_no_folder_returns_labeled(self, labeled_dir: Path, store: FolderStore):
        """Stem not in any folder → Labeled regardless of what labels are present."""
        _write_annotation(labeled_dir, "scan", ["tumor"])
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, store) == FileStatus.LABELED

    def test_annotation_folder_no_mandatory_labels_returns_labeled(
        self, labeled_dir: Path, store: FolderStore
    ):
        f = store.create_folder("Brain", mandatory_labels=[])
        store.add_stems(f.id, ["scan"])
        _write_annotation(labeled_dir, "scan", ["tumor"])
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, store) == FileStatus.LABELED

    def test_all_mandatory_labels_present_returns_labeled(
        self, labeled_dir: Path, store: FolderStore
    ):
        f = store.create_folder("Brain", mandatory_labels=["tumor", "edema"])
        store.add_stems(f.id, ["scan"])
        _write_annotation(labeled_dir, "scan", ["tumor", "edema", "extra"])
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, store) == FileStatus.LABELED

    def test_one_mandatory_label_missing_returns_in_progress(
        self, labeled_dir: Path, store: FolderStore
    ):
        f = store.create_folder("Brain", mandatory_labels=["tumor", "edema"])
        store.add_stems(f.id, ["scan"])
        _write_annotation(labeled_dir, "scan", ["tumor"])   # edema missing
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, store) == FileStatus.IN_PROGRESS

    def test_all_mandatory_labels_missing_returns_in_progress(
        self, labeled_dir: Path, store: FolderStore
    ):
        f = store.create_folder("Brain", mandatory_labels=["tumor", "edema"])
        store.add_stems(f.id, ["scan"])
        _write_annotation(labeled_dir, "scan", [])   # no labels at all
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, store) == FileStatus.IN_PROGRESS

    def test_empty_annotation_shapes_with_mandatory_labels_returns_in_progress(
        self, labeled_dir: Path, store: FolderStore
    ):
        f = store.create_folder("X", mandatory_labels=["tumor"])
        store.add_stems(f.id, ["scan"])
        (labeled_dir / "scan.json").write_text(
            json.dumps({"shapes": []}), encoding="utf-8"
        )
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, store) == FileStatus.IN_PROGRESS

    def test_corrupt_annotation_with_mandatory_labels_returns_in_progress(
        self, labeled_dir: Path, store: FolderStore
    ):
        """Unreadable annotation + mandatory labels → can't confirm complete → In Progress."""
        f = store.create_folder("X", mandatory_labels=["tumor"])
        store.add_stems(f.id, ["scan"])
        (labeled_dir / "scan.json").write_text("{ not valid json }", encoding="utf-8")
        dcm = Path("Unlabeled/scan.dcm")
        assert resolve_status(dcm, store) == FileStatus.IN_PROGRESS

    def test_stem_uses_only_base_stem_not_full_path(
        self, labeled_dir: Path, store: FolderStore
    ):
        """resolve_status must derive the stem from dcm_path.stem, not the full path."""
        _write_annotation(labeled_dir, "deep_scan", ["tumor"])
        dcm = Path("some/nested/dir/deep_scan.dcm")
        assert resolve_status(dcm, None) == FileStatus.LABELED

    def test_different_stems_resolved_independently(
        self, labeled_dir: Path, store: FolderStore
    ):
        f = store.create_folder("X", mandatory_labels=["tumor"])
        store.add_stems(f.id, ["scan_a", "scan_b"])
        _write_annotation(labeled_dir, "scan_a", ["tumor"])   # complete
        _write_annotation(labeled_dir, "scan_b", [])          # incomplete

        assert resolve_status(Path("scan_a.dcm"), store) == FileStatus.LABELED
        assert resolve_status(Path("scan_b.dcm"), store) == FileStatus.IN_PROGRESS