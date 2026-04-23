"""
core/status.py
Shared file-status resolution used by the main window and pack export dialog.

Status definitions
------------------
Unlabeled   — No LabelMe annotation JSON exists for this stem.
In Progress — Annotation exists but not all folder-mandatory labels are present.
Labeled     — Annotation exists and every mandatory label is satisfied
              (or the stem is unassigned / in a folder with no requirements).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from core.paths import LABELED_DIR

if TYPE_CHECKING:
    from core.folder_store import FolderStore

log = logging.getLogger(__name__)

# Module-level path reference — may be overridden in tests via monkeypatch.
_LABELED_DIR: Path = LABELED_DIR


# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

class FileStatus:
    UNLABELED   = "Unlabeled"
    IN_PROGRESS = "In Progress"
    LABELED     = "Labeled"


STATUS_COLORS: dict[str, str] = {
    FileStatus.UNLABELED:   "#5A7FA8",
    FileStatus.IN_PROGRESS: "#C8922A",
    FileStatus.LABELED:     "#3E8E41",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_annotation_label_names(stem: str) -> set[str]:
    """
    Return the set of label names used in a stem's LabelMe annotation JSON.

    Returns an empty set when the file is absent, empty, or unparseable.
    Only the exact <stem>.json path is checked — timestamp-prefixed variants
    (labelme's <ts>_<stem>.json) are handled separately by label_overlay.py.
    """
    json_path = _LABELED_DIR / f"{stem}.json"
    if not json_path.exists():
        return set()
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return {s["label"] for s in data.get("shapes", []) if "label" in s}
    except (json.JSONDecodeError, KeyError, TypeError):
        return set()


def resolve_status(
    dcm_path: Path,
    store: FolderStore | None = None,
) -> str:
    """
    Determine the labeling status of a DICOM file.

    Resolution order:
      1. No annotation JSON → Unlabeled.
      2. Annotation exists + store supplied + stem is in a folder whose
         mandatory labels are not all present in the annotation → In Progress.
      3. Otherwise → Labeled.

    Args:
        dcm_path: Path to the .dcm file (only the stem is used).
        store:    FolderStore for mandatory-label lookup.  When None the
                  function returns Unlabeled or Labeled only.

    Returns:
        One of FileStatus.UNLABELED, FileStatus.IN_PROGRESS, FileStatus.LABELED.
    """
    if not (_LABELED_DIR / f"{dcm_path.stem}.json").exists():
        return FileStatus.UNLABELED

    if store is not None:
        mandatory = store.mandatory_labels_for_stem(dcm_path.stem)
        if mandatory:
            present = get_annotation_label_names(dcm_path.stem)
            if not set(mandatory).issubset(present):
                return FileStatus.IN_PROGRESS

    return FileStatus.LABELED