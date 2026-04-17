"""
core/paths.py
Single source of truth for all runtime directory paths.

App data lives in:
    Windows : C:/Users/<user>/Documents/Labelpad/
    macOS   : /Users/<user>/Documents/Labelpad/
    Linux   : ~/Documents/Labelpad/
"""

from pathlib import Path


def _data_root() -> Path:
    """Resolve the user-facing data root regardless of OS."""
    documents = Path.home() / "Documents"
    root = documents / "Labelpad"
    return root


DATA_ROOT   = _data_root()

UNLABELED_DIR = DATA_ROOT / "Unlabeled"
RASTER_DIR    = DATA_ROOT / "Raster"
DATA_DIR      = DATA_ROOT / "Data"
LABELED_DIR   = DATA_ROOT / "Labeled"

ALL_DIRS = [UNLABELED_DIR, RASTER_DIR, DATA_DIR, LABELED_DIR]


def bootstrap() -> None:
    """Create all data directories if they don't exist yet."""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for d in ALL_DIRS:
        d.mkdir(exist_ok=True)