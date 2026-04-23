"""
tests/test_label_overlay.py
Unit tests for ui/label_overlay.py — _resolve_image_path, LabelOverlay,
and load_label_overlay (including M2 imagePath handling).
Run with:  python -m pytest tests/ -v
"""

import json
from pathlib import Path

import numpy as np
import pytest

import ui.label_overlay as lo_mod
from ui.label_overlay import (
    LabelOverlay,
    _resolve_image_path,
    load_label_overlay,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _annotation(
    image_path: str = "/abs/foreign/path/scan.jpg",
    shapes: list | None = None,
    w: int = 512,
    h: int = 512,
) -> bytes:
    """Return minimal LabelMe JSON bytes suitable for writing to disk."""
    return json.dumps({
        "imagePath":   image_path,
        "imageWidth":  w,
        "imageHeight": h,
        "shapes":      shapes or [],
    }).encode("utf-8")


def _polygon(label: str = "tumor") -> dict:
    """Return a minimal valid polygon shape dict."""
    return {
        "label":      label,
        "shape_type": "polygon",
        "points":     [[10, 10], [30, 10], [30, 30], [10, 30]],
    }


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def labeled_dir(tmp_path, monkeypatch):
    """
    Redirect _LABELED_DIR in the label_overlay module to a fresh temp
    directory for each test — mirrors the pattern used in test_dcmpack.py.
    """
    lb = tmp_path / "Labeled"
    lb.mkdir()
    monkeypatch.setattr(lo_mod, "_LABELED_DIR", lb)
    return lb


# ---------------------------------------------------------------------------
# _resolve_image_path
# ---------------------------------------------------------------------------

class TestResolveImagePath:
    """
    _resolve_image_path(raw_path, json_path) must handle every state that
    imagePath can be in after a DCMPACK round-trip:
      1. Valid absolute path on the same machine (post-extraction).
      2. Relative ./stem.jpg (portable form written by create_pack).
      3. Stale absolute path from a foreign machine (sibling fallback).
      4. Completely unresolvable path.
      5. Empty or absent field.
    """

    def test_empty_string_returns_none(self, tmp_path):
        result = _resolve_image_path("", tmp_path / "ann.json")
        assert result is None

    def test_existing_absolute_path_returned_directly(self, tmp_path):
        raster = _write(tmp_path / "scan.jpg", b"data")
        result = _resolve_image_path(str(raster), tmp_path / "other" / "ann.json")
        assert result == raster

    def test_nonexistent_absolute_path_no_sibling_returns_none(self, tmp_path):
        result = _resolve_image_path("/does/not/exist/scan.jpg", tmp_path / "ann.json")
        assert result is None

    def test_relative_dot_slash_resolves_to_sibling(self, tmp_path):
        json_path = tmp_path / "ann.json"
        sibling   = _write(tmp_path / "scan.jpg", b"data")
        result    = _resolve_image_path("./scan.jpg", json_path)
        assert result == sibling.resolve()

    def test_bare_filename_resolves_to_sibling(self, tmp_path):
        json_path = tmp_path / "ann.json"
        sibling   = _write(tmp_path / "scan.jpg", b"data")
        result    = _resolve_image_path("scan.jpg", json_path)
        assert result == sibling.resolve()

    def test_stale_absolute_path_resolves_via_sibling_basename(self, tmp_path):
        """
        The common post-import case: imagePath was an absolute path on the
        machine that created the pack, but the basename still exists next
        to the annotation JSON on the receiving machine.
        """
        json_path = tmp_path / "Labeled" / "scan.json"
        json_path.parent.mkdir()
        sibling   = _write(tmp_path / "Labeled" / "scan.jpg", b"data")
        stale     = "/home/other_user/Documents/Labelpad/Raster/scan.jpg"
        result    = _resolve_image_path(stale, json_path)
        assert result == sibling.resolve()

    def test_completely_unresolvable_returns_none(self, tmp_path):
        json_path = tmp_path / "Labeled" / "scan.json"
        json_path.parent.mkdir()
        result    = _resolve_image_path("./missing.jpg", json_path)
        assert result is None


# ---------------------------------------------------------------------------
# LabelOverlay
# ---------------------------------------------------------------------------

class TestLabelOverlay:

    def test_label_count_includes_all_shapes(self):
        shapes  = [_polygon("a"), _polygon("b"), _polygon("a")]
        overlay = LabelOverlay(shapes, 256, 256)
        assert overlay.label_count == 3

    def test_label_names_are_sorted_and_deduplicated(self):
        shapes  = [_polygon("zebra"), _polygon("ant"), _polygon("ant")]
        overlay = LabelOverlay(shapes, 256, 256)
        assert overlay.label_names == ["ant", "zebra"]

    def test_image_path_property_returns_stored_value(self, tmp_path):
        p       = tmp_path / "scan.jpg"
        overlay = LabelOverlay([_polygon()], 256, 256, image_path=p)
        assert overlay.image_path == p

    def test_image_path_defaults_to_none(self):
        overlay = LabelOverlay([_polygon()], 256, 256)
        assert overlay.image_path is None

    def test_draw_returns_correct_shape_and_dtype(self):
        rgb     = np.zeros((64, 64, 3), dtype=np.uint8)
        overlay = LabelOverlay([_polygon()], 64, 64)
        result  = overlay.draw(rgb)
        assert result.shape == (64, 64, 3)
        assert result.dtype == np.uint8

    def test_draw_does_not_mutate_input_array(self):
        rgb      = np.zeros((64, 64, 3), dtype=np.uint8)
        original = rgb.copy()
        overlay  = LabelOverlay([_polygon()], 64, 64)
        overlay.draw(rgb)
        np.testing.assert_array_equal(rgb, original)

    def test_draw_skips_non_polygon_shapes(self):
        """draw() must not raise when all shapes are non-polygon types."""
        shapes  = [{"label": "x", "shape_type": "rectangle", "points": [[0,0],[10,10]]}]
        overlay = LabelOverlay(shapes, 64, 64)
        rgb     = np.zeros((64, 64, 3), dtype=np.uint8)
        result  = overlay.draw(rgb)
        assert result.shape == (64, 64, 3)

    def test_draw_with_empty_shapes_list(self):
        overlay = LabelOverlay([], 64, 64)
        rgb     = np.zeros((64, 64, 3), dtype=np.uint8)
        result  = overlay.draw(rgb)
        assert result.shape == (64, 64, 3)


# ---------------------------------------------------------------------------
# load_label_overlay
# ---------------------------------------------------------------------------

class TestLoadLabelOverlay:

    def test_returns_none_when_no_json_in_labeled_dir(self, labeled_dir):
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is None

    def test_returns_none_when_shapes_list_is_empty(self, labeled_dir):
        _write(labeled_dir / "scan.json", _annotation(shapes=[]))
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is None

    def test_returns_none_when_only_non_polygon_shapes(self, labeled_dir):
        shapes = [{"label": "x", "shape_type": "rectangle", "points": [[0,0],[10,10]]}]
        _write(labeled_dir / "scan.json", _annotation(shapes=shapes))
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is None

    def test_returns_overlay_with_correct_label_count(self, labeled_dir):
        shapes = [_polygon("a"), _polygon("b")]
        _write(labeled_dir / "scan.json", _annotation(shapes=shapes))
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is not None
        assert result.label_count == 2

    def test_returns_overlay_with_correct_label_names(self, labeled_dir):
        shapes = [_polygon("tumor"), _polygon("lesion"), _polygon("tumor")]
        _write(labeled_dir / "scan.json", _annotation(shapes=shapes))
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result.label_names == ["lesion", "tumor"]

    def test_image_path_resolved_when_raster_sibling_exists(self, labeled_dir):
        """Covers the ./stem.jpg portable path written by create_pack."""
        sibling = _write(labeled_dir / "scan.jpg", b"data")
        _write(labeled_dir / "scan.json", _annotation(
            image_path="./scan.jpg", shapes=[_polygon()]
        ))
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is not None
        assert result.image_path == sibling.resolve()

    def test_image_path_resolved_from_valid_absolute_path(self, labeled_dir, tmp_path):
        """Covers the absolute-path case after extraction on the same machine."""
        raster = _write(tmp_path / "Raster" / "scan.jpg", b"data")
        _write(labeled_dir / "scan.json", _annotation(
            image_path=str(raster), shapes=[_polygon()]
        ))
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is not None
        assert result.image_path == raster

    def test_image_path_is_none_when_raster_not_found(self, labeled_dir):
        """Stale path from a foreign machine with no sibling → None, not an error."""
        _write(labeled_dir / "scan.json", _annotation(
            image_path="/foreign/machine/path/scan.jpg", shapes=[_polygon()]
        ))
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is not None       # overlay still loads
        assert result.image_path is None  # path just can't be resolved

    def test_corrupt_json_returns_none_without_raising(self, labeled_dir):
        _write(labeled_dir / "scan.json", b"{ this is not valid json }")
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is None

    def test_timestamp_prefixed_filename_is_matched(self, labeled_dir):
        """LabelMe sometimes saves as <timestamp>_<stem>.json."""
        shapes = [_polygon()]
        _write(labeled_dir / "1776400910652_scan.json", _annotation(shapes=shapes))
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is not None

    def test_unrelated_stem_is_not_matched(self, labeled_dir):
        _write(labeled_dir / "unrelated.json", _annotation(shapes=[_polygon()]))
        result = load_label_overlay(Path("Unlabeled/scan.dcm"))
        assert result is None