"""
tests/test_dicom_engine.py
Unit tests for core/dicom_handler.py and core/metadata_store.py.
Run with:  python -m pytest tests/ -v
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from core.dicom_handler import (
    WindowingParams,
    apply_windowing,
    export_jpg,
    suggest_slider_range,
    DicomImage,
)
from core import metadata_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_dicom_image(pixel_min: float = -1000.0, pixel_max: float = 3000.0) -> DicomImage:
    """Construct a synthetic DicomImage for testing — no real .dcm file needed."""
    arr = np.linspace(pixel_min, pixel_max, num=256 * 256, dtype=np.float32).reshape(256, 256)
    return DicomImage(
        path=Path("Unlabeled/synthetic.dcm"),
        pixel_array=arr,
        default_windowing=WindowingParams(center=500.0, width=1500.0),
        patient_id="TEST-001",
        modality="CT",
        pixel_min=pixel_min,
        pixel_max=pixel_max,
    )


# ---------------------------------------------------------------------------
# WindowingParams tests
# ---------------------------------------------------------------------------

class TestWindowingParams:
    def test_lower_upper(self):
        p = WindowingParams(center=100.0, width=200.0)
        assert p.lower == pytest.approx(0.0)
        assert p.upper == pytest.approx(200.0)

    def test_asymmetric_center(self):
        p = WindowingParams(center=-500.0, width=1000.0)
        assert p.lower == pytest.approx(-1000.0)
        assert p.upper == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# apply_windowing tests
# ---------------------------------------------------------------------------

class TestApplyWindowing:
    def test_output_dtype_is_uint8(self):
        arr = np.array([[0.0, 500.0, 1000.0]], dtype=np.float32)
        result = apply_windowing(arr, WindowingParams(center=500.0, width=1000.0))
        assert result.dtype == np.uint8

    def test_values_clamped_to_0_255(self):
        arr = np.array([[-9999.0, 0.0, 9999.0]], dtype=np.float32)
        result = apply_windowing(arr, WindowingParams(center=0.0, width=100.0))
        assert result.min() == 0
        assert result.max() == 255

    def test_full_range_maps_correctly(self):
        # When window spans exactly [0, 1000], pixel=0 → 0, pixel=1000 → 255
        arr = np.array([[0.0, 1000.0]], dtype=np.float32)
        result = apply_windowing(arr, WindowingParams(center=500.0, width=1000.0))
        assert result[0, 0] == 0
        assert result[0, 1] == 255

    def test_mid_point_is_128(self):
        # Center pixel should map to ~128
        arr = np.array([[500.0]], dtype=np.float32)
        result = apply_windowing(arr, WindowingParams(center=500.0, width=1000.0))
        assert 127 <= result[0, 0] <= 128


# ---------------------------------------------------------------------------
# export_jpg tests
# ---------------------------------------------------------------------------

class TestExportJpg:
    def test_file_is_created(self, tmp_path: Path):
        dicom = _make_dicom_image()
        out = tmp_path / "output.jpg"
        returned = export_jpg(dicom.pixel_array, dicom.default_windowing, out)
        assert returned == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_output_is_valid_jpeg(self, tmp_path: Path):
        from PIL import Image
        dicom = _make_dicom_image()
        out = tmp_path / "output.jpg"
        export_jpg(dicom.pixel_array, dicom.default_windowing, out)
        img = Image.open(out)
        assert img.format == "JPEG"
        assert img.mode == "L"  # greyscale

    def test_creates_parent_dirs(self, tmp_path: Path):
        dicom = _make_dicom_image()
        out = tmp_path / "nested" / "deep" / "output.jpg"
        export_jpg(dicom.pixel_array, dicom.default_windowing, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# suggest_slider_range tests
# ---------------------------------------------------------------------------

class TestSuggestSliderRange:
    def test_ww_min_is_positive(self):
        dicom = _make_dicom_image()
        _, _, ww_min, _ = suggest_slider_range(dicom)
        assert ww_min >= 1.0

    def test_wc_range_covers_pixel_range(self):
        dicom = _make_dicom_image(pixel_min=-1000.0, pixel_max=3000.0)
        wc_min, wc_max, _, _ = suggest_slider_range(dicom)
        assert wc_min < -1000.0
        assert wc_max > 3000.0


# ---------------------------------------------------------------------------
# metadata_store tests
# ---------------------------------------------------------------------------

class TestMetadataStore:
    def test_round_trip(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(metadata_store, "DATA_DIR", tmp_path)

        dcm_path = Path("Unlabeled/brain_scan.dcm")
        params = WindowingParams(center=40.0, width=80.0)

        metadata_store.save_windowing(dcm_path, params)
        loaded = metadata_store.load_windowing(dcm_path)

        assert loaded is not None
        assert loaded.center == pytest.approx(40.0)
        assert loaded.width  == pytest.approx(80.0)

    def test_load_missing_returns_none(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(metadata_store, "DATA_DIR", tmp_path)
        result = metadata_store.load_windowing(Path("Unlabeled/nonexistent.dcm"))
        assert result is None

    def test_has_saved_windowing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(metadata_store, "DATA_DIR", tmp_path)
        dcm_path = Path("Unlabeled/test.dcm")
        assert not metadata_store.has_saved_windowing(dcm_path)
        metadata_store.save_windowing(dcm_path, WindowingParams(center=0.0, width=400.0))
        assert metadata_store.has_saved_windowing(dcm_path)

    def test_corrupted_json_returns_none(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(metadata_store, "DATA_DIR", tmp_path)
        dcm_path = Path("Unlabeled/bad.dcm")
        (tmp_path / "bad.json").write_text("{ not valid json }", encoding="utf-8")
        result = metadata_store.load_windowing(dcm_path)
        assert result is None