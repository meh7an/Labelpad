"""
core/folder_store.py
Persists folder definitions (name, mandatory labels, member stems) to
DATA_ROOT/folders.json.

A stem belongs to at most one folder at a time.  add_stems() enforces
this invariant atomically by evicting the stem from any prior owner
before inserting it into the target folder.

Thread safety
-------------
A threading.Lock serialises every read-modify-write cycle.  _save() uses
os.replace() so a crash mid-write never leaves a corrupt file on disk.

JSON layout
-----------
{
    "schema_version": 1,
    "folders": [
        {
            "id":               "a1b2c3d4",
            "name":             "Brain CT April",
            "mandatory_labels": ["tumor", "edema"],
            "stems":            ["brain_001", "brain_002"]
        }
    ]
}
"""

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.paths import DATA_ROOT

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_DEFAULT_PATH   = DATA_ROOT / "folders.json"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class FolderStoreError(Exception):
    """Base exception for all FolderStore operations."""


class FolderNotFoundError(FolderStoreError):
    """Raised when a folder_id is not present in the store."""


class FolderNameError(FolderStoreError):
    """Raised when a folder name is empty or whitespace-only."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Folder:
    """Immutable snapshot of a single folder and its members."""
    id:               str
    name:             str
    mandatory_labels: tuple[str, ...]
    stems:            tuple[str, ...]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class FolderStore:
    """
    Manages the complete set of application folders.

    Loads from disk on construction and writes atomically after every
    mutation.  Callers should keep a single instance per session rather
    than constructing one per call-site.

    Args:
        json_path: Override the default storage path.  Used in tests to
                   redirect I/O to a temporary directory.
    """

    def __init__(self, json_path: Optional[Path] = None) -> None:
        self._path: Path             = json_path or _DEFAULT_PATH
        self._lock: threading.Lock   = threading.Lock()
        self._folders: dict[str, Folder] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read folders.json.  Resets to an empty store on any parse error."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            parsed: dict[str, Folder] = {}
            for raw in data.get("folders", []):
                folder = Folder(
                    id=str(raw["id"]),
                    name=str(raw["name"]),
                    mandatory_labels=tuple(str(l) for l in raw.get("mandatory_labels", [])),
                    stems=tuple(str(s) for s in raw.get("stems", [])),
                )
                parsed[folder.id] = folder
            self._folders = parsed
            log.info("FolderStore: loaded %d folder(s) from %s.", len(parsed), self._path)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            log.warning(
                "FolderStore: cannot parse '%s' (%s) — starting empty.", self._path, exc
            )
            self._folders = {}

    def reload(self) -> None:
        """Re-read from disk.  Call when another process may have modified the file."""
        with self._lock:
            self._load()

    def _save(self) -> None:
        """Atomically persist current state.  Must be called while holding self._lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "folders": [
                {
                    "id":               f.id,
                    "name":             f.name,
                    "mandatory_labels": list(f.mandatory_labels),
                    "stems":            list(f.stems),
                }
                for f in self._folders.values()
            ],
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        log.debug("FolderStore: saved %d folder(s).", len(self._folders))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex[:8]

    def _require(self, folder_id: str) -> Folder:
        """Return the folder or raise FolderNotFoundError."""
        try:
            return self._folders[folder_id]
        except KeyError:
            raise FolderNotFoundError(f"No folder with id={folder_id!r}.")

    @staticmethod
    def _validate_name(name: str) -> str:
        stripped = name.strip()
        if not stripped:
            raise FolderNameError("Folder name must not be empty.")
        return stripped

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def all_folders(self) -> list[Folder]:
        """Return all folders in insertion order."""
        with self._lock:
            return list(self._folders.values())

    def get_folder(self, folder_id: str) -> Folder:
        """
        Return the folder with the given id.

        Raises:
            FolderNotFoundError: If the id is absent.
        """
        with self._lock:
            return self._require(folder_id)

    def folder_for_stem(self, stem: str) -> Optional[Folder]:
        """Return the folder that owns stem, or None when unassigned."""
        with self._lock:
            for folder in self._folders.values():
                if stem in folder.stems:
                    return folder
            return None

    def mandatory_labels_for_stem(self, stem: str) -> tuple[str, ...]:
        """Shortcut that returns an empty tuple when the stem is unassigned."""
        folder = self.folder_for_stem(stem)
        return folder.mandatory_labels if folder else ()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def create_folder(
        self,
        name: str,
        mandatory_labels: list[str] | None = None,
    ) -> Folder:
        """
        Create a new empty folder with an auto-generated id.

        Raises:
            FolderNameError: If name is blank.
        """
        name   = self._validate_name(name)
        folder = Folder(
            id=self._new_id(),
            name=name,
            mandatory_labels=tuple(mandatory_labels or []),
            stems=(),
        )
        with self._lock:
            self._folders[folder.id] = folder
            self._save()
        log.info("FolderStore: created folder '%s' (id=%s).", folder.name, folder.id)
        return folder

    def rename_folder(self, folder_id: str, new_name: str) -> Folder:
        """
        Rename an existing folder.

        Raises:
            FolderNotFoundError: If folder_id is absent.
            FolderNameError:     If new_name is blank.
        """
        new_name = self._validate_name(new_name)
        with self._lock:
            old     = self._require(folder_id)
            updated = Folder(
                id=old.id,
                name=new_name,
                mandatory_labels=old.mandatory_labels,
                stems=old.stems,
            )
            self._folders[folder_id] = updated
            self._save()
        log.info("FolderStore: renamed folder %s → '%s'.", folder_id, new_name)
        return updated

    def delete_folder(self, folder_id: str) -> None:
        """
        Delete a folder.  Member stems become unassigned; files are not touched.

        Raises:
            FolderNotFoundError: If folder_id is absent.
        """
        with self._lock:
            self._require(folder_id)
            del self._folders[folder_id]
            self._save()
        log.info("FolderStore: deleted folder id=%s.", folder_id)

    def set_mandatory_labels(self, folder_id: str, labels: list[str]) -> Folder:
        """
        Replace the mandatory labels for a folder.

        Raises:
            FolderNotFoundError: If folder_id is absent.
        """
        with self._lock:
            old     = self._require(folder_id)
            updated = Folder(
                id=old.id,
                name=old.name,
                mandatory_labels=tuple(labels),
                stems=old.stems,
            )
            self._folders[folder_id] = updated
            self._save()
        log.info("FolderStore: updated mandatory labels for folder %s.", folder_id)
        return updated

    def add_stems(self, folder_id: str, stems: list[str]) -> Folder:
        """
        Add stems to a folder, evicting each stem from any prior owner first.

        The stem-uniqueness invariant is enforced atomically: all evictions
        and the final insertion share a single lock acquisition.

        Raises:
            FolderNotFoundError: If folder_id is absent.
        """
        with self._lock:
            self._require(folder_id)
            stems_set = set(stems)

            for fid, folder in list(self._folders.items()):
                if fid == folder_id:
                    continue
                overlap = stems_set & set(folder.stems)
                if overlap:
                    self._folders[fid] = Folder(
                        id=folder.id,
                        name=folder.name,
                        mandatory_labels=folder.mandatory_labels,
                        stems=tuple(s for s in folder.stems if s not in overlap),
                    )
                    log.debug(
                        "FolderStore: evicted %s from '%s' → '%s'.",
                        overlap, folder.name, self._folders[folder_id].name,
                    )

            target   = self._folders[folder_id]
            existing = set(target.stems)
            result   = Folder(
                id=target.id,
                name=target.name,
                mandatory_labels=target.mandatory_labels,
                stems=target.stems + tuple(s for s in stems if s not in existing),
            )
            self._folders[folder_id] = result
            self._save()

        log.info("FolderStore: added %d stem(s) to folder %s.", len(stems), folder_id)
        return result

    def remove_stems(self, folder_id: str, stems: list[str]) -> Folder:
        """
        Remove stems from a folder.  Stems not present are silently ignored.

        Raises:
            FolderNotFoundError: If folder_id is absent.
        """
        remove_set = set(stems)
        with self._lock:
            target = self._require(folder_id)
            result = Folder(
                id=target.id,
                name=target.name,
                mandatory_labels=target.mandatory_labels,
                stems=tuple(s for s in target.stems if s not in remove_set),
            )
            self._folders[folder_id] = result
            self._save()
        log.info("FolderStore: removed %d stem(s) from folder %s.", len(stems), folder_id)
        return result

    def upsert_folder(
        self,
        folder_id:        str,
        name:             str,
        mandatory_labels: list[str],
        stems:            list[str],
    ) -> Folder:
        """
        Create or merge a folder identified by folder_id.

        If folder_id already exists locally: update its name and mandatory_labels,
        then add stems (evicting them from any other folder as needed).
        If folder_id is new: create the folder preserving the exact id — this
        maintains cross-machine identity when importing .dcmpack archives.

        Re-importing the same pack twice is idempotent: the same id is found,
        the name/labels are refreshed, and duplicate stems are deduplicated by
        add_stems().

        Args:
            folder_id:        Stable id from a pack manifest.
            name:             Display name (must be non-empty after stripping).
            mandatory_labels: Labels required for Labeled status.
            stems:            DICOM stems to ensure are in this folder.

        Returns:
            The resulting Folder after all mutations.

        Raises:
            FolderNameError: If name is blank.
        """
        name = self._validate_name(name)
        with self._lock:
            if folder_id in self._folders:
                old = self._folders[folder_id]
                self._folders[folder_id] = Folder(
                    id=old.id,
                    name=name,
                    mandatory_labels=tuple(mandatory_labels),
                    stems=old.stems,
                )
            else:
                self._folders[folder_id] = Folder(
                    id=folder_id,
                    name=name,
                    mandatory_labels=tuple(mandatory_labels),
                    stems=(),
                )
            self._save()
        log.info("FolderStore: upserted folder '%s' (id=%s).", name, folder_id)
        return self.add_stems(folder_id, stems) if stems else self.get_folder(folder_id)