"""
core/dicom_handler.py
Responsible for all DICOM I/O: reading pixel data, applying DICOM windowing
(Window Center / Window Width), and exporting the result as a JPG.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pydicom
from PIL import Image

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowingParams:
    """Immutable container for a DICOM windowing state."""
    center: float
    width: float

    @property
    def lower(self) -> float:
        return self.center - self.width / 2.0

    @property
    def upper(self) -> float:
        return self.center + self.width / 2.0


@dataclass
class DicomImage:
    """
    Parsed representation of a single DICOM file.

    Attributes:
        path:             Absolute path to the source .dcm file.
        pixel_array:      Raw, unscaled pixel data as a 2-D float32 array.
        default_windowing: WC/WW extracted from the DICOM tags (may be None
                          if the tags are absent).
        patient_id:       Optional patient identifier from DICOM metadata.
        modality:         e.g. "CT", "MR", "CR" — from DICOM tags.
        pixel_min:        Minimum value in the raw pixel array.
        pixel_max:        Maximum value in the raw pixel array.
    """
    path: Path
    pixel_array: np.ndarray
    default_windowing: Optional[WindowingParams]
    patient_id: str
    modality: str
    pixel_min: float
    pixel_max: float


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

class DicomReadError(Exception):
    """Raised when a DICOM file cannot be read or is missing required data."""


def load_dicom(path: Path) -> DicomImage:
    """
    Read a DICOM file and return a DicomImage.

    Applies pydicom's rescale slope/intercept so the pixel array is in
    Hounsfield Units (for CT) or manufacturer-calibrated units for other
    modalities.

    Args:
        path: Path to the .dcm file.

    Returns:
        DicomImage with raw pixel data and metadata populated.

    Raises:
        DicomReadError: If the file cannot be opened or has no pixel data.
    """
    if not path.exists():
        raise DicomReadError(f"File not found: {path}")

    try:
        ds = pydicom.dcmread(str(path))
    except Exception as exc:
        raise DicomReadError(f"pydicom could not read '{path.name}': {exc}") from exc

    if not hasattr(ds, "PixelData"):
        raise DicomReadError(f"'{path.name}' contains no pixel data.")

    try:
        raw = ds.pixel_array.astype(np.float32)
    except Exception as exc:
        raise DicomReadError(f"Could not decode pixel array in '{path.name}': {exc}") from exc

    # Apply DICOM rescale tags if present (slope / intercept)
    slope = float(getattr(ds, "RescaleSlope", 1.0))
    intercept = float(getattr(ds, "RescaleIntercept", 0.0))
    pixel_array = raw * slope + intercept

    # Extract default windowing from DICOM tags
    default_windowing = _extract_default_windowing(ds, pixel_array)

    patient_id = str(getattr(ds, "PatientID", "Unknown"))
    modality = str(getattr(ds, "Modality", "Unknown"))

    log.info(
        "Loaded DICOM '%s' | modality=%s | shape=%s | WC/WW=%s",
        path.name, modality, pixel_array.shape, default_windowing,
    )

    return DicomImage(
        path=path,
        pixel_array=pixel_array,
        default_windowing=default_windowing,
        patient_id=patient_id,
        modality=modality,
        pixel_min=float(pixel_array.min()),
        pixel_max=float(pixel_array.max()),
    )


def _extract_default_windowing(
    ds: pydicom.Dataset,
    pixel_array: np.ndarray,
) -> Optional[WindowingParams]:
    """
    Attempt to read WindowCenter and WindowWidth from the dataset.

    DICOM allows these tags to hold a list of presets; we take the first.
    Falls back to a full-range windowing derived from the pixel data itself
    so sliders always have a sensible starting position.
    """
    try:
        wc_tag = ds.WindowCenter
        ww_tag = ds.WindowWidth

        # Tags may be pydicom DSfloat, a list, or a MultiValue — normalise.
        center = float(wc_tag[0]) if hasattr(wc_tag, "__iter__") and not isinstance(wc_tag, str) else float(wc_tag)
        width  = float(ww_tag[0]) if hasattr(ww_tag, "__iter__") and not isinstance(ww_tag, str) else float(ww_tag)

        if width <= 0:
            raise ValueError("WindowWidth must be positive.")

        return WindowingParams(center=center, width=width)

    except (AttributeError, IndexError, ValueError, TypeError) as exc:
        log.warning("Could not read DICOM windowing tags (%s) — using pixel range fallback.", exc)

    # Fallback: centre the window over the full pixel value range
    p_min = float(pixel_array.min())
    p_max = float(pixel_array.max())
    center = (p_min + p_max) / 2.0
    width  = max(p_max - p_min, 1.0)
    return WindowingParams(center=center, width=width)


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------

def apply_windowing(pixel_array: np.ndarray, params: WindowingParams) -> np.ndarray:
    """
    Apply linear DICOM windowing to a raw pixel array and return a uint8 image.

    The standard DICOM linear windowing formula maps the interval
    [center - width/2, center + width/2] onto [0, 255], clamping values
    outside this range to 0 or 255 respectively.

    Args:
        pixel_array: 2-D float32 array of raw (rescaled) pixel values.
        params:      WindowingParams describing the desired WC/WW.

    Returns:
        2-D uint8 numpy array suitable for display or export.
    """
    lo = params.lower
    hi = params.upper
    span = hi - lo if hi != lo else 1.0

    windowed = (pixel_array - lo) / span * 255.0
    windowed = np.clip(windowed, 0.0, 255.0)
    return windowed.astype(np.uint8)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_jpg(
    pixel_array: np.ndarray,
    params: WindowingParams,
    output_path: Path,
    quality: int = 95,
) -> Path:
    """
    Apply windowing and write the result as a JPEG file.

    Args:
        pixel_array:  Raw float32 pixel array from a DicomImage.
        params:       The WC/WW to bake into the export.
        output_path:  Destination file path (should end in .jpg / .jpeg).
        quality:      JPEG quality factor (1-95). Default 95 preserves detail.

    Returns:
        The resolved output_path after successful write.

    Raises:
        OSError: If the file cannot be written.
    """
    uint8_array = apply_windowing(pixel_array, params)
    img = Image.fromarray(uint8_array, mode="L")  # L = 8-bit greyscale

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), format="JPEG", quality=quality)

    log.info("Exported JPG → %s (WC=%.1f, WW=%.1f)", output_path, params.center, params.width)
    return output_path


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def raster_path_for(dcm_path: Path, raster_dir: Path) -> Path:
    """Return the expected JPG path in Raster/ for a given DICOM file."""
    return raster_dir / (dcm_path.stem + ".jpg")


def suggest_slider_range(dicom: DicomImage) -> tuple[float, float, float, float]:
    """
    Compute reasonable slider min/max values for WC and WW controls.

    Returns:
        (wc_min, wc_max, ww_min, ww_max)
    """
    p_range = dicom.pixel_max - dicom.pixel_min
    padding = p_range * 0.1 or 50.0  # at least ±50 padding

    wc_min = dicom.pixel_min - padding
    wc_max = dicom.pixel_max + padding

    ww_min = 1.0
    ww_max = p_range * 2.0 or 2000.0

    return wc_min, wc_max, ww_min, ww_max