"""
core/purge.py
Permanent removal of a DICOM file and every on-disk artifact tied to its stem.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.paths import DATA_DIR, LABELED_DIR, RASTER_DIR

log = logging.getLogger(__name__)


def artifact_paths_for_stem(stem: str) -> list[Path]:
    """
    Return every existing sidecar artifact for a stem: the raster JPG, the
    metadata sidecar, and LabelMe annotation JSONs (including labelme's
    optional timestamp-prefixed '<ts>_<stem>.json' variants).

    The .dcm itself is not included.
    """
    sidecars = [
        RASTER_DIR / f"{stem}.jpg",
        DATA_DIR / f"{stem}.json",
    ]
    try:
        annotations = [
            p for p in LABELED_DIR.glob("*.json")
            if p.stem == stem or p.stem.endswith(f"_{stem}")
        ]
    except OSError:
        annotations = []
    return [p for p in sidecars if p.exists()] + annotations


def purge_dicom(dcm_path: Path) -> None:
    """
    Permanently delete a DICOM file and all artifacts tied to its stem.

    The .dcm is removed first — if that fails, nothing else is touched, so a
    locked file never loses its annotations or metadata. Sidecar failures
    after that point are logged and skipped.

    Raises:
        OSError: If the .dcm itself cannot be removed.
    """
    dcm_path.unlink(missing_ok=True)
    for artifact in artifact_paths_for_stem(dcm_path.stem):
        try:
            artifact.unlink()
        except OSError as exc:
            log.warning("Could not delete artifact %s: %s", artifact, exc)
    log.info("Purged %s and its artifacts.", dcm_path.name)
