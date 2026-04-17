"""
core/metadata_store.py
Persists per-file windowing parameters to the Data/ directory as JSON.
Each DICOM file gets its own sidecar: Data/<stem>.json
"""

import json
import logging
from pathlib import Path
from typing import Optional

from core.dicom_handler import WindowingParams
from core.paths import DATA_DIR

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


def _json_path(dcm_path: Path) -> Path:
    return DATA_DIR / (dcm_path.stem + ".json")


def save_windowing(dcm_path: Path, params: WindowingParams) -> Path:
    record = {
        "schema_version": _SCHEMA_VERSION,
        "source_file":    dcm_path.name,
        "window_center":  params.center,
        "window_width":   params.width,
    }
    out_path = _json_path(dcm_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    log.info("Saved windowing -> %s (WC=%.1f, WW=%.1f)", out_path, params.center, params.width)
    return out_path


def load_windowing(dcm_path: Path) -> Optional[WindowingParams]:
    json_path = _json_path(dcm_path)
    if not json_path.exists():
        return None
    try:
        record = json.loads(json_path.read_text(encoding="utf-8"))
        center = float(record["window_center"])
        width  = float(record["window_width"])
        if width <= 0:
            raise ValueError("window_width must be positive.")
        log.info("Loaded windowing <- %s (WC=%.1f, WW=%.1f)", json_path, center, width)
        return WindowingParams(center=center, width=width)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Could not parse metadata at '%s': %s -- ignoring.", json_path, exc)
        return None


def has_saved_windowing(dcm_path: Path) -> bool:
    return _json_path(dcm_path).exists()