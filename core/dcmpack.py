"""
core/dcmpack.py
DCMPACK archive format — create, open, and extract .dcmpack files.

Internal ZIP layout
-------------------
    manifest.json
    items/
        <stem>/
            <stem>.dcm                  # always present
            <stem>.jpg                  # labeled items only
            <stem>_windowing.json       # labeled items only
            <stem>.json                 # labeled items only (LabelMe annotation)

Encryption
----------
When a password is supplied, pyzipper applies AES-256 (WZ_AES) encryption
to every member in the archive, including manifest.json.

imagePath handling (M2)
-----------------------
LabelMe bakes an absolute imagePath into its annotation JSON at save time.
create_pack() rewrites imagePath to the relative form ./stem.jpg in-memory
before bundling. extract_pack() rewrites it back to the absolute raster path
on the receiving machine.

Folder structure (M5)
---------------------
An optional "folders" array in manifest.json carries folder name, mandatory
labels, and member stems. extract_pack() merges this into the local
FolderStore via upsert_folder(), preserving the folder id for idempotency
across re-imports. Old packs without this key are handled transparently.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Callable

import pyzipper

from core.paths import DATA_DIR, LABELED_DIR, RASTER_DIR, UNLABELED_DIR

log = logging.getLogger(__name__)

_SCHEMA_VERSION   = 1
_MANIFEST_ARCNAME = "manifest.json"

# Module-level path references — may be overridden in tests via monkeypatch.
_UNLABELED_DIR: Path       = UNLABELED_DIR
_RASTER_DIR:    Path       = RASTER_DIR
_DATA_DIR:      Path       = DATA_DIR
_LABELED_DIR:   Path       = LABELED_DIR
_FOLDERS_JSON:  Path | None = None   # None → FolderStore uses DATA_ROOT default


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DcmPackError(Exception):
    """Base exception for all DCMPACK operations."""


class DcmPackPasswordError(DcmPackError):
    """Archive password is missing, incorrect, or the archive is encrypted."""


class DcmPackCorruptError(DcmPackError):
    """Archive is structurally invalid or a required member is absent."""


class DcmPackVersionError(DcmPackError):
    """Manifest schema_version is not supported by this build."""

class DcmPackCancelledError(DcmPackError):
    """Operation was cancelled by the user before completion."""


class LabelPatchError(DcmPackError):
    """Raised when a LabelMe annotation JSON cannot be parsed or written."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DcmPackItem:
    """Represents a single DICOM entry within a pack manifest."""
    stem:    str
    labeled: bool


@dataclass(frozen=True)
class PackFolder:
    """
    Folder metadata bundled inside a .dcmpack manifest.

    Preserves folder identity (id) so that repeated imports of the same pack
    are idempotent — the local FolderStore merges rather than duplicates.
    """
    id:               str
    name:             str
    mandatory_labels: tuple[str, ...]
    stems:            tuple[str, ...]


@dataclass(frozen=True)
class DcmPackManifest:
    """Parsed representation of a pack's manifest.json."""
    schema_version:     int
    pack_name:          str
    created_at:         str
    password_protected: bool
    items:              tuple[DcmPackItem, ...]
    author:      str                    = ""
    description: str                    = ""
    tags:        tuple[str, ...]        = field(default_factory=tuple)
    folders:     tuple[PackFolder, ...] = field(default_factory=tuple)


@dataclass
class ImportResult:
    """Summary returned by extract_pack()."""
    imported: list[str]             = field(default_factory=list)
    skipped:  list[str]             = field(default_factory=list)
    failed:   list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.imported) + len(self.skipped) + len(self.failed)

    @property
    def summary(self) -> str:
        """Human-readable one-liner suitable for a status bar message."""
        parts = []
        if self.imported: parts.append(f"{len(self.imported)} imported")
        if self.skipped:  parts.append(f"{len(self.skipped)} skipped")
        if self.failed:   parts.append(f"{len(self.failed)} failed")
        return "  |  ".join(parts) if parts else "Nothing to import"


# ---------------------------------------------------------------------------
# Archive path helpers
# ---------------------------------------------------------------------------

def _dcm_arc_path(stem: str)       -> str: return f"items/{stem}/{stem}.dcm"
def _jpg_arc_path(stem: str)       -> str: return f"items/{stem}/{stem}.jpg"
def _windowing_arc_path(stem: str) -> str: return f"items/{stem}/{stem}_windowing.json"
def _label_arc_path(stem: str)     -> str: return f"items/{stem}/{stem}.json"


# ---------------------------------------------------------------------------
# Low-level ZIP helpers
# ---------------------------------------------------------------------------

def _pw_bytes(password: Optional[str]) -> Optional[bytes]:
    return password.encode("utf-8") if password else None


def _open_zip(path: Path, mode: str, password: Optional[str]) -> pyzipper.AESZipFile:
    """
    Open a pyzipper AESZipFile in the requested mode.

    Write mode enables DEFLATE compression and, when a password is provided,
    AES-256 encryption. Read mode auto-detects both attributes from the file.
    """
    kwargs: dict = {}
    if mode == "w":
        kwargs["compression"] = pyzipper.ZIP_DEFLATED
        if password:
            kwargs["encryption"] = pyzipper.WZ_AES

    try:
        zf = pyzipper.AESZipFile(str(path), mode, **kwargs)
    except (OSError, pyzipper.BadZipFile) as exc:
        raise DcmPackCorruptError(f"Cannot open '{path.name}': {exc}") from exc

    pw = _pw_bytes(password)
    if pw:
        zf.setpassword(pw)
    return zf


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

def _parse_manifest(raw: bytes) -> DcmPackManifest:
    """Deserialise raw manifest bytes into a DcmPackManifest."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DcmPackCorruptError(f"manifest.json is not valid JSON: {exc}") from exc

    version = data.get("schema_version")
    if version != _SCHEMA_VERSION:
        raise DcmPackVersionError(
            f"Unsupported manifest schema_version {version!r}. "
            f"This build supports version {_SCHEMA_VERSION}."
        )

    try:
        items = tuple(
            DcmPackItem(stem=str(i["stem"]), labeled=bool(i["labeled"]))
            for i in data.get("items", [])
        )
        tags = tuple(str(t) for t in data.get("tags", []))
        folders = tuple(
            PackFolder(
                id=str(f["id"]),
                name=str(f["name"]),
                mandatory_labels=tuple(str(l) for l in f.get("mandatory_labels", [])),
                stems=tuple(str(s) for s in f.get("stems", [])),
            )
            for f in data.get("folders", [])
        )
        return DcmPackManifest(
            schema_version=version,
            pack_name=str(data["pack_name"]),
            created_at=str(data.get("created_at", "")),
            password_protected=bool(data.get("password_protected", False)),
            items=items,
            author=str(data.get("author", "")),
            description=str(data.get("description", "")),
            tags=tags,
            folders=folders,
        )
    except (KeyError, TypeError) as exc:
        raise DcmPackCorruptError(
            f"manifest.json has an unexpected structure: {exc}"
        ) from exc


def read_manifest(zf: pyzipper.AESZipFile) -> DcmPackManifest:
    """
    Read and validate manifest.json from an already-open AESZipFile.

    Args:
        zf: An open pyzipper.AESZipFile (password already set if needed).

    Returns:
        Parsed DcmPackManifest.

    Raises:
        DcmPackPasswordError: Archive requires a password that was not supplied,
                              or the supplied password is incorrect.
        DcmPackCorruptError:  manifest.json is absent or structurally invalid.
        DcmPackVersionError:  Manifest schema version is not supported.
    """
    try:
        raw = zf.read(_MANIFEST_ARCNAME)
    except RuntimeError as exc:
        raise DcmPackPasswordError(
            "Archive is password-protected — provide the correct password."
        ) from exc
    except KeyError:
        raise DcmPackCorruptError("Archive is missing manifest.json.")

    return _parse_manifest(raw)


def _write_manifest(zf: pyzipper.AESZipFile, manifest: DcmPackManifest) -> None:
    record = {
        "schema_version":     manifest.schema_version,
        "pack_name":          manifest.pack_name,
        "created_at":         manifest.created_at,
        "password_protected": manifest.password_protected,
        "author":             manifest.author,
        "description":        manifest.description,
        "tags":               list(manifest.tags),
        "items": [
            {"stem": item.stem, "labeled": item.labeled}
            for item in manifest.items
        ],
        "folders": [
            {
                "id":               f.id,
                "name":             f.name,
                "mandatory_labels": list(f.mandatory_labels),
                "stems":            list(f.stems),
            }
            for f in manifest.folders
        ],
    }
    zf.writestr(_MANIFEST_ARCNAME, json.dumps(record, indent=2))


# ---------------------------------------------------------------------------
# Label imagePath patch utilities
# ---------------------------------------------------------------------------

def _patch_label_bytes(raw: bytes, new_image_path: str) -> bytes:
    """
    Return a copy of raw LabelMe JSON bytes with imagePath replaced.

    This is a pure in-memory transform — it does not touch the filesystem.
    Called by _add_labeled_patched during archive creation to normalise the
    path to the portable relative form ./stem.jpg before bundling.

    Args:
        raw:            UTF-8-encoded LabelMe annotation JSON.
        new_image_path: Replacement value for the imagePath field.

    Returns:
        UTF-8-encoded JSON bytes with imagePath updated.

    Raises:
        LabelPatchError: If raw is not valid JSON or not valid UTF-8.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LabelPatchError(f"Cannot parse label annotation: {exc}") from exc

    data["imagePath"] = new_image_path
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def patch_label_imagepath(json_path: Path, new_image_path: Path | str) -> None:
    """
    Rewrite the imagePath field in a LabelMe annotation JSON file in-place.

    Called by _patch_extracted_label after each labeled item is extracted, to
    update the stale path baked in by labelme (or the portable ./stem.jpg from
    a well-formed pack) to the absolute raster location on this machine.

    Args:
        json_path:      Path to the LabelMe .json annotation file on disk.
        new_image_path: Replacement value for imagePath. Pass an absolute Path
                        for machine-specific storage, or "./stem.jpg" for
                        portable archive use.

    Raises:
        LabelPatchError: If the file cannot be read, is not valid JSON, or
                         cannot be written back to disk.
    """
    try:
        raw = json_path.read_bytes()
    except OSError as exc:
        raise LabelPatchError(
            f"Cannot read label annotation '{json_path.name}': {exc}"
        ) from exc
    path_str = new_image_path.as_posix() if isinstance(new_image_path, Path) else str(new_image_path)
    patched  = _patch_label_bytes(raw, path_str)
    try:
        json_path.write_bytes(patched)
    except OSError as exc:
        raise LabelPatchError(
            f"Cannot write patched annotation '{json_path.name}': {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def peek_is_password_protected(path: Path) -> bool:
    """
    Return True if the pack is password-protected without raising.

    Attempts to read manifest.json without a password. pyzipper raises
    RuntimeError when it encounters AES-encrypted content and no key is set.
    All other errors (corrupt file, missing file) return False so that the
    caller receives the proper exception from open_pack() instead.

    Args:
        path: Path to the .dcmpack file.

    Returns:
        True if AES encryption is detected, False otherwise.
    """
    try:
        with pyzipper.AESZipFile(str(path), "r") as zf:
            zf.read(_MANIFEST_ARCNAME)
        return False
    except RuntimeError:
        return True
    except Exception:
        return False


def open_pack(path: Path, password: Optional[str] = None) -> pyzipper.AESZipFile:
    """
    Open a .dcmpack archive for reading.

    The returned AESZipFile is not yet validated. Call read_manifest(zf)
    immediately after to confirm the archive is structurally sound.

    Usage:
        with open_pack(path, password) as zf:
            manifest = read_manifest(zf)
            ...

    Args:
        path:     Path to the .dcmpack file.
        password: Decryption password, or None for unprotected packs.

    Returns:
        An open AESZipFile (context manager — caller must close it).

    Raises:
        DcmPackCorruptError:  File not found or not a valid ZIP archive.
        DcmPackPasswordError: Wrong or missing password (deferred to first read).
    """
    if not path.exists():
        raise DcmPackCorruptError(f"Pack file not found: {path}")
    return _open_zip(path, "r", password)

_ProgressCB = Callable[[int, int], None]

def extract_pack(
    path:              Path,
    password:          Optional[str]          = None,
    on_conflict:       Literal["skip", "overwrite"] = "skip",
    progress_callback: _ProgressCB | None     = None,
) -> ImportResult:
    """
    Extract a .dcmpack archive into the application's data directories.

    Args:
        path:              Path to the .dcmpack file.
        password:          Decryption password, or None for unprotected packs.
        on_conflict:       "skip" or "overwrite" for existing DCM files.
        progress_callback: Optional callable(current_index, total_items)
                           invoked just before each item is processed.

    Returns:
        ImportResult summarising imported / skipped / failed stems.

    Raises:
        DcmPackPasswordError: Wrong or missing password.
        DcmPackCorruptError:  Archive is structurally invalid.
        DcmPackVersionError:  Unsupported manifest schema version.
    """
    result   = ImportResult()
    manifest = None

    with open_pack(path, password) as zf:
        manifest = read_manifest(zf)
        total    = len(manifest.items)
        log.info(
            "Extracting pack '%s' (%d item(s), conflict=%s).",
            manifest.pack_name, total, on_conflict,
        )
        for idx, item in enumerate(manifest.items):
            if progress_callback:
                progress_callback(idx, total)
            try:
                _extract_item(zf, item, on_conflict, result)
            except Exception as exc:
                log.error("Failed to extract '%s': %s", item.stem, exc)
                result.failed.append((item.stem, str(exc)))

    if manifest and manifest.folders and (result.imported or result.skipped):
        _apply_manifest_folders(
            manifest,
            imported_stems=set(result.imported),
            skipped_stems=set(result.skipped),
        )

    return result


def _apply_manifest_folders(
    manifest:       DcmPackManifest,
    imported_stems: set[str],
    skipped_stems:  set[str]            = frozenset(),
    store:          "FolderStore | None" = None,
) -> None:
    """
    Merge folder structure from the manifest into the local FolderStore.

    Folder assignment rules per stem:
    - Imported stems are always assigned to the manifest folder.
    - Skipped stems are only assigned if they currently belong to no folder
      locally, so existing local organisation is never overwritten.

    Folders are matched by id for idempotency — re-importing the same pack
    a second time neither duplicates folders nor reassigns stems.

    Args:
        manifest:       Parsed manifest containing folder metadata.
        imported_stems: Stems that were freshly extracted this run.
        skipped_stems:  Stems whose DCM already existed and was left in place.
        store:          Optional FolderStore for testing (None = default path).
    """
    if not manifest.folders:
        return

    from core.folder_store import FolderStore
    _store = store or FolderStore(json_path=_FOLDERS_JSON)

    for pack_folder in manifest.folders:
        stems_to_add = [
            s for s in pack_folder.stems
            if s in imported_stems
            or (s in skipped_stems and _store.folder_for_stem(s) is None)
        ]
        if not stems_to_add:
            continue
        _store.upsert_folder(
            folder_id=pack_folder.id,
            name=pack_folder.name,
            mandatory_labels=list(pack_folder.mandatory_labels),
            stems=stems_to_add,
        )
        log.info(
            "Merged folder '%s' (%d stem(s)) from pack.",
            pack_folder.name, len(stems_to_add),
        )


def _extract_item(
    zf:          pyzipper.AESZipFile,
    item:        DcmPackItem,
    on_conflict: str,
    result:      ImportResult,
) -> None:
    dest_dcm = _UNLABELED_DIR / f"{item.stem}.dcm"

    if dest_dcm.exists() and on_conflict == "skip":
        log.debug("Skipping DCM for '%s' — destination already exists.", item.stem)
        if item.labeled:
            _import_labels_if_missing(zf, item)
        result.skipped.append(item.stem)
        return

    _write_member(zf, _dcm_arc_path(item.stem), dest_dcm)

    if item.labeled:
        _write_member(zf, _jpg_arc_path(item.stem),       _RASTER_DIR  / f"{item.stem}.jpg")
        _write_member(zf, _windowing_arc_path(item.stem), _DATA_DIR    / f"{item.stem}.json")
        _write_member(zf, _label_arc_path(item.stem),     _LABELED_DIR / f"{item.stem}.json")
        _patch_extracted_label(item.stem)

    log.info("Extracted '%s' (labeled=%s).", item.stem, item.labeled)
    result.imported.append(item.stem)


def _import_labels_if_missing(zf: pyzipper.AESZipFile, item: DcmPackItem) -> None:
    """
    Import labeled assets for a stem whose DCM already exists locally.

    Writes raster, windowing, and annotation when:
    - The local annotation JSON is absent, OR
    - The pack annotation contains label names not present in the local one
      (pack has broader coverage — local should be updated).

    The local annotation is left untouched when it already covers everything
    the pack provides, preserving work done locally.
    """
    local_label = _LABELED_DIR / f"{item.stem}.json"

    if local_label.exists():
        try:
            local_names = {
                s["label"]
                for s in json.loads(local_label.read_text(encoding="utf-8")).get("shapes", [])
                if "label" in s
            }
            try:
                pack_raw   = zf.read(_label_arc_path(item.stem))
                pack_names = {
                    s["label"]
                    for s in json.loads(pack_raw.decode("utf-8")).get("shapes", [])
                    if "label" in s
                }
            except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
                return  # cannot read pack label — keep local
            if not (pack_names - local_names):
                return  # local already covers all pack labels
        except Exception:
            return  # cannot parse local label — keep it (conservative)

    try:
        _write_member(zf, _jpg_arc_path(item.stem),       _RASTER_DIR  / f"{item.stem}.jpg")
        _write_member(zf, _windowing_arc_path(item.stem), _DATA_DIR    / f"{item.stem}.json")
        _write_member(zf, _label_arc_path(item.stem),     _LABELED_DIR / f"{item.stem}.json")
        _patch_extracted_label(item.stem)
        log.info("Imported labels for existing DICOM '%s'.", item.stem)
    except (DcmPackCorruptError, DcmPackPasswordError) as exc:
        log.warning("Could not import labels for existing '%s': %s", item.stem, exc)


def _write_member(zf: pyzipper.AESZipFile, arc_name: str, dest: Path) -> None:
    """Extract a single archive member to dest, creating parent directories."""
    try:
        data = zf.read(arc_name)
    except KeyError:
        raise DcmPackCorruptError(f"Required archive member '{arc_name}' is missing.")
    except RuntimeError as exc:
        raise DcmPackPasswordError(
            f"Cannot decrypt '{arc_name}' — verify the password."
        ) from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _patch_extracted_label(stem: str) -> None:
    """
    After extraction, rewrite imagePath in the annotation JSON to the absolute
    raster path on this machine.

    Failures are logged as warnings rather than propagated — a stale imagePath
    affects only labelme's image-loading behaviour, not Labelpad's own overlay
    renderer which uses the DICOM pixel data directly.
    """
    label_path  = _LABELED_DIR / f"{stem}.json"
    raster_path = _RASTER_DIR  / f"{stem}.jpg"
    try:
        patch_label_imagepath(label_path, raster_path)
        log.debug("Patched imagePath for '%s' → %s", stem, raster_path)
    except LabelPatchError as exc:
        log.warning(
            "Could not patch imagePath for '%s' after extraction: %s", stem, exc
        )


def _find_dicom_source(stem: str) -> Path | None:
    """
    Return the first existing DICOM file for stem in UNLABELED_DIR,
    trying .dcm then .dicom. Returns None if neither is found.

    Both extensions are equally valid in the application; the source
    extension is normalised to .dcm inside the archive on packing.
    """
    for ext in (".dcm", ".dicom"):
        candidate = _UNLABELED_DIR / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def create_pack(
    stems:             list[str],
    dest_path:         Path,
    password:          Optional[str]             = None,
    author:            str                       = "",
    description:       str                       = "",
    tags:              list[str] | None          = None,
    pack_folders:      list[PackFolder] | None   = None,
    progress_callback: _ProgressCB | None        = None,
) -> Path:
    """
    Bundle a list of DICOM stems into a new .dcmpack archive.

    Args:
        stems:             File stems to include.
        dest_path:         Destination .dcmpack path.
        password:          Optional AES-256 encryption password.
        author:            Optional author string for the manifest.
        description:       Optional description string for the manifest.
        tags:              Optional list of tag strings.
        pack_folders:      Optional folder metadata to embed in the manifest.
        progress_callback: Optional callable(current_index, total_items)
                           invoked just before each item is written.

    Returns:
        Resolved absolute path of the created archive.

    Raises:
        DcmPackError: A source DICOM is missing for one or more stems.
        OSError:      The destination path is not writable.
    """
    items:   list[DcmPackItem] = []
    sources: list[Path]        = []
    for stem in stems:
        dcm_src = _find_dicom_source(stem)
        if dcm_src is None:
            raise DcmPackError(
                f"Source DICOM not found for stem '{stem}' "
                f"(tried .dcm and .dicom in {_UNLABELED_DIR})"
            )
        labeled = (_LABELED_DIR / f"{stem}.json").exists()
        items.append(DcmPackItem(stem=stem, labeled=labeled))
        sources.append(dcm_src)

    stems_set = set(stems)
    filtered_folders: tuple[PackFolder, ...] = ()
    if pack_folders:
        filtered = []
        for pf in pack_folders:
            present = tuple(s for s in pf.stems if s in stems_set)
            if present:
                filtered.append(PackFolder(
                    id=pf.id,
                    name=pf.name,
                    mandatory_labels=pf.mandatory_labels,
                    stems=present,
                ))
        filtered_folders = tuple(filtered)

    manifest = DcmPackManifest(
        schema_version=_SCHEMA_VERSION,
        pack_name=dest_path.stem,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        password_protected=bool(password),
        items=tuple(items),
        author=author,
        description=description,
        tags=tuple(tags or []),
        folders=filtered_folders,
    )

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(items)

    with _open_zip(dest_path, "w", password) as zf:
        _write_manifest(zf, manifest)
        for idx, (item, dcm_src) in enumerate(zip(items, sources)):
            if progress_callback:
                progress_callback(idx, total)
            _add_member(zf, dcm_src, _dcm_arc_path(item.stem))
            if item.labeled:
                _add_member(zf, _RASTER_DIR / f"{item.stem}.jpg",  _jpg_arc_path(item.stem))
                _add_member(zf, _DATA_DIR   / f"{item.stem}.json",  _windowing_arc_path(item.stem))
                _add_labeled_patched(zf, item.stem)

    log.info(
        "Created pack '%s' — %d item(s), %d folder(s), encrypted=%s.",
        dest_path.name, len(items), len(filtered_folders), bool(password),
    )
    return dest_path.resolve()


def _add_member(zf: pyzipper.AESZipFile, src: Path, arc_name: str) -> None:
    """Write src into the archive as arc_name. Logs a warning if src is absent."""
    if not src.exists():
        log.warning("Packing: source not found, skipping — %s", src)
        return
    zf.write(str(src), arcname=arc_name)


def _add_labeled_patched(zf: pyzipper.AESZipFile, stem: str) -> None:
    """
    Bundle the label annotation JSON with imagePath normalised to ./stem.jpg.

    The source file is read from disk, patched in memory, and written directly
    into the archive. The on-disk annotation in Labeled/ is never touched.
    If patching fails due to malformed JSON, the original bytes are bundled
    as-is and a warning is emitted so packing can continue.
    """
    src = _LABELED_DIR / f"{stem}.json"
    if not src.exists():
        log.warning("Packing: label JSON not found, skipping — %s", src)
        return

    raw = src.read_bytes()
    try:
        patched = _patch_label_bytes(raw, f"./{stem}.jpg")
    except LabelPatchError as exc:
        log.warning(
            "Could not patch imagePath for '%s' — bundling annotation as-is: %s",
            stem, exc,
        )
        patched = raw

    zf.writestr(_label_arc_path(stem), patched)