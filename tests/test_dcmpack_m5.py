"""
tests/test_dcmpack_m5.py
M5 tests — PackFolder, folder-aware manifest I/O, create_pack with folders,
extract_pack folder merging, FolderStore.upsert_folder, and round-trips.
Run with:  python -m pytest tests/ -v
"""

import json
from pathlib import Path

import pyzipper
import pytest

import core.dcmpack as dcmpack
from core.dcmpack import (
    DcmPackItem,
    DcmPackManifest,
    PackFolder,
    _apply_manifest_folders,
    create_pack,
    extract_pack,
    open_pack,
    read_manifest,
)
from core.folder_store import Folder, FolderStore

# ---------------------------------------------------------------------------
# Byte fixtures (mirrors test_dcmpack.py)
# ---------------------------------------------------------------------------

_FAKE_DCM = b"DICM_FAKE_PIXEL_DATA"


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _plain_pack(path: Path, manifest: bytes, members: dict | None = None) -> Path:
    with pyzipper.AESZipFile(str(path), "w", compression=pyzipper.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        for arc_name, data in (members or {}).items():
            zf.writestr(arc_name, data)
    return path


def _manifest_bytes(items=None, folders=None) -> bytes:
    return json.dumps({
        "schema_version":     1,
        "pack_name":          "test_pack",
        "created_at":         "2026-04-23T00:00:00+00:00",
        "password_protected": False,
        "items":              items or [],
        "folders":            folders or [],
    }).encode("utf-8")


# ---------------------------------------------------------------------------
# Shared fixture: redirect data dirs + folder store into tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    ul = tmp_path / "Unlabeled"
    rs = tmp_path / "Raster"
    dt = tmp_path / "Data"
    lb = tmp_path / "Labeled"
    fj = tmp_path / "folders.json"
    for d in (ul, rs, dt, lb):
        d.mkdir()
    monkeypatch.setattr(dcmpack, "_UNLABELED_DIR", ul)
    monkeypatch.setattr(dcmpack, "_RASTER_DIR",    rs)
    monkeypatch.setattr(dcmpack, "_DATA_DIR",      dt)
    monkeypatch.setattr(dcmpack, "_LABELED_DIR",   lb)
    monkeypatch.setattr(dcmpack, "_FOLDERS_JSON",  fj)
    return ul, rs, dt, lb


@pytest.fixture()
def store(tmp_path: Path) -> FolderStore:
    return FolderStore(json_path=tmp_path / "folders.json")


# ---------------------------------------------------------------------------
# PackFolder dataclass
# ---------------------------------------------------------------------------

class TestPackFolder:
    def test_fields_accessible(self):
        pf = PackFolder("abc", "Brain", ("tumor",), ("s1", "s2"))
        assert pf.id               == "abc"
        assert pf.name             == "Brain"
        assert pf.mandatory_labels == ("tumor",)
        assert pf.stems            == ("s1", "s2")

    def test_is_frozen(self):
        pf = PackFolder("abc", "Brain", (), ())
        with pytest.raises(Exception):
            pf.name = "Other"  # type: ignore[misc]

    def test_empty_fields(self):
        pf = PackFolder("x", "X", (), ())
        assert pf.mandatory_labels == ()
        assert pf.stems            == ()


# ---------------------------------------------------------------------------
# Manifest with folders — parse
# ---------------------------------------------------------------------------

class TestManifestFoldersParse:
    def test_manifest_without_folders_key_defaults_to_empty_tuple(self, tmp_path):
        raw  = json.dumps({
            "schema_version": 1, "pack_name": "p",
            "created_at": "", "password_protected": False, "items": [],
        }).encode()
        pack = _plain_pack(tmp_path / "p.dcmpack", raw)
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert m.folders == ()

    def test_manifest_with_empty_folders_list_gives_empty_tuple(self, tmp_path):
        pack = _plain_pack(tmp_path / "p.dcmpack", _manifest_bytes(folders=[]))
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert m.folders == ()

    def test_manifest_with_one_folder_parses_correctly(self, tmp_path):
        pack = _plain_pack(
            tmp_path / "p.dcmpack",
            _manifest_bytes(
                items=[{"stem": "scan", "labeled": False}],
                folders=[{
                    "id": "abc123", "name": "Brain",
                    "mandatory_labels": ["tumor", "edema"],
                    "stems": ["scan"],
                }],
            ),
        )
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert len(m.folders)               == 1
        assert m.folders[0].id             == "abc123"
        assert m.folders[0].name           == "Brain"
        assert m.folders[0].mandatory_labels == ("tumor", "edema")
        assert m.folders[0].stems          == ("scan",)

    def test_multiple_folders_parsed(self, tmp_path):
        pack = _plain_pack(
            tmp_path / "p.dcmpack",
            _manifest_bytes(folders=[
                {"id": "a", "name": "A", "mandatory_labels": [], "stems": ["s1"]},
                {"id": "b", "name": "B", "mandatory_labels": ["x"], "stems": ["s2"]},
            ]),
        )
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert len(m.folders) == 2
        assert {f.id for f in m.folders} == {"a", "b"}

    def test_folder_without_mandatory_labels_key_defaults_empty(self, tmp_path):
        pack = _plain_pack(
            tmp_path / "p.dcmpack",
            _manifest_bytes(folders=[{"id": "x", "name": "X", "stems": ["s1"]}]),
        )
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert m.folders[0].mandatory_labels == ()

    def test_folders_field_is_tuple(self, tmp_path):
        pack = _plain_pack(tmp_path / "p.dcmpack", _manifest_bytes())
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert isinstance(m.folders, tuple)


# ---------------------------------------------------------------------------
# create_pack with folders
# ---------------------------------------------------------------------------

class TestCreatePackWithFolders:
    def test_pack_folders_written_to_manifest(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)
        pf   = PackFolder("abc", "Brain", ("tumor",), ("scan",))
        pack = create_pack(["scan"], tmp_path / "out.dcmpack", pack_folders=[pf])
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert len(m.folders)               == 1
        assert m.folders[0].id             == "abc"
        assert m.folders[0].name           == "Brain"
        assert m.folders[0].mandatory_labels == ("tumor",)
        assert m.folders[0].stems          == ("scan",)

    def test_folder_stems_filtered_to_packed_stems(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan_a.dcm", _FAKE_DCM)
        _write(ul / "scan_b.dcm", _FAKE_DCM)
        # pack_folder includes scan_c which is NOT being packed
        pf = PackFolder("abc", "Brain", (), ("scan_a", "scan_b", "scan_c"))
        pack = create_pack(
            ["scan_a", "scan_b"], tmp_path / "out.dcmpack", pack_folders=[pf]
        )
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert set(m.folders[0].stems) == {"scan_a", "scan_b"}
        assert "scan_c" not in m.folders[0].stems

    def test_folder_with_no_matching_stems_excluded(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)
        # Folder references a stem not being packed — folder should be dropped.
        pf   = PackFolder("abc", "Brain", (), ("other_stem",))
        pack = create_pack(["scan"], tmp_path / "out.dcmpack", pack_folders=[pf])
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert m.folders == ()

    def test_no_pack_folders_arg_gives_empty_tuple(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)
        pack = create_pack(["scan"], tmp_path / "out.dcmpack")
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert m.folders == ()

    def test_multiple_folders_written(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "a.dcm", _FAKE_DCM)
        _write(ul / "b.dcm", _FAKE_DCM)
        pfs = [
            PackFolder("x1", "Alpha", (), ("a",)),
            PackFolder("x2", "Beta",  (), ("b",)),
        ]
        pack = create_pack(["a", "b"], tmp_path / "out.dcmpack", pack_folders=pfs)
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert len(m.folders) == 2

    def test_disk_file_not_modified_by_folder_filtering(self, tmp_path, dirs):
        """create_pack must not write anything to disk related to folders."""
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)
        before = set(tmp_path.rglob("*.json"))
        create_pack(
            ["scan"], tmp_path / "out.dcmpack",
            pack_folders=[PackFolder("x", "F", (), ("scan",))],
        )
        after = set(tmp_path.rglob("*.json"))
        # Only the archive itself is new — no sidecar JSONs created.
        new_files = after - before
        assert all(f.suffix == ".dcmpack" or "out" in f.name for f in new_files)


# ---------------------------------------------------------------------------
# _apply_manifest_folders
# ---------------------------------------------------------------------------

class TestApplyManifestFolders:
    def _make_manifest(self, folders) -> DcmPackManifest:
        return DcmPackManifest(
            schema_version=1,
            pack_name="test",
            created_at="",
            password_protected=False,
            items=(),
            folders=tuple(folders),
        )

    def test_creates_new_folder_in_store(self, store):
        manifest = self._make_manifest([
            PackFolder("abc", "Brain", ("tumor",), ("scan_01",)),
        ])
        _apply_manifest_folders(manifest, {"scan_01"}, store=store)
        f = store.folder_for_stem("scan_01")
        assert f is not None
        assert f.id               == "abc"
        assert f.name             == "Brain"
        assert f.mandatory_labels == ("tumor",)

    def test_only_imported_stems_added(self, store):
        manifest = self._make_manifest([
            PackFolder("abc", "Brain", (), ("s1", "s2", "s3")),
        ])
        # s3 was not imported (e.g. skipped due to conflict)
        _apply_manifest_folders(manifest, {"s1", "s2"}, store=store)
        f = store.get_folder("abc")
        assert "s1" in f.stems
        assert "s2" in f.stems
        assert "s3" not in f.stems

    def test_updates_existing_folder_by_id(self, store):
        # Pre-create folder with old name / labels
        store.upsert_folder("abc", "Old Name", ["old_label"], ["existing_stem"])
        manifest = self._make_manifest([
            PackFolder("abc", "New Name", ("new_label",), ("new_stem",)),
        ])
        _apply_manifest_folders(manifest, {"new_stem"}, store=store)
        f = store.get_folder("abc")
        assert f.name             == "New Name"
        assert f.mandatory_labels == ("new_label",)
        assert "existing_stem"    in f.stems   # preserved
        assert "new_stem"         in f.stems   # added

    def test_empty_manifest_folders_is_noop(self, store):
        store.create_folder("Pre-existing")
        manifest = self._make_manifest([])
        _apply_manifest_folders(manifest, {"scan"}, store=store)
        assert len(store.all_folders()) == 1  # unchanged

    def test_stem_not_imported_not_added(self, store):
        manifest = self._make_manifest([
            PackFolder("abc", "Brain", (), ("scan",)),
        ])
        _apply_manifest_folders(manifest, set(), store=store)  # nothing imported
        with pytest.raises(Exception):
            store.get_folder("abc")   # folder should not exist

    def test_multiple_folders_applied(self, store):
        manifest = self._make_manifest([
            PackFolder("f1", "Alpha", (),     ("s1",)),
            PackFolder("f2", "Beta",  ("x",), ("s2",)),
        ])
        _apply_manifest_folders(manifest, {"s1", "s2"}, store=store)
        assert store.folder_for_stem("s1").id == "f1"
        assert store.folder_for_stem("s2").id == "f2"

    def test_skipped_stem_assigned_when_unassigned_locally(self, store):
        """A stem that was skipped (DCM existed) gets the folder if it has none."""
        manifest = self._make_manifest([
            PackFolder("abc", "Brain", (), ("scan",)),
        ])
        _apply_manifest_folders(
            manifest,
            imported_stems=set(),
            skipped_stems={"scan"},
            store=store,
        )
        assert store.folder_for_stem("scan") is not None
        assert store.folder_for_stem("scan").id == "abc"

    def test_skipped_stem_not_reassigned_when_already_in_folder(self, store):
        """A stem that was skipped keeps its existing folder assignment."""
        existing = store.create_folder("Local Folder")
        store.add_stems(existing.id, ["scan"])
        manifest = self._make_manifest([
            PackFolder("abc", "Pack Folder", (), ("scan",)),
        ])
        _apply_manifest_folders(
            manifest,
            imported_stems=set(),
            skipped_stems={"scan"},
            store=store,
        )
        # Still in the original local folder
        assert store.folder_for_stem("scan").id == existing.id


# ---------------------------------------------------------------------------
# extract_pack with folder merging
# ---------------------------------------------------------------------------

class TestExtractPackFolderMerge:
    def test_folder_created_in_store_after_extraction(self, tmp_path, dirs):
        ul, *_ = dirs
        pack = _plain_pack(
            tmp_path / "pack.dcmpack",
            _manifest_bytes(
                items=[{"stem": "brain_01", "labeled": False}],
                folders=[{
                    "id": "abc123", "name": "Brain CT",
                    "mandatory_labels": ["tumor"],
                    "stems": ["brain_01"],
                }],
            ),
            {"items/brain_01/brain_01.dcm": _FAKE_DCM},
        )
        extract_pack(pack)
        store = FolderStore(json_path=tmp_path / "folders.json")
        f     = store.folder_for_stem("brain_01")
        assert f is not None
        assert f.name             == "Brain CT"
        assert f.mandatory_labels == ("tumor",)

    def test_skipped_stem_gets_folder_when_unassigned(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "brain_01.dcm", b"EXISTING")
        pack = _plain_pack(
            tmp_path / "pack.dcmpack",
            _manifest_bytes(
                items=[{"stem": "brain_01", "labeled": False}],
                folders=[{
                    "id": "abc", "name": "Brain",
                    "mandatory_labels": [], "stems": ["brain_01"],
                }],
            ),
            {"items/brain_01/brain_01.dcm": _FAKE_DCM},
        )
        result = extract_pack(pack, on_conflict="skip")
        assert result.skipped == ["brain_01"]
        store = FolderStore(json_path=tmp_path / "folders.json")
        # Stem had no local folder → pack folder should be applied.
        f = store.folder_for_stem("brain_01")
        assert f is not None
        assert f.id == "abc"

    def test_skipped_stem_keeps_existing_local_folder(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "brain_01.dcm", b"EXISTING")
        # Pre-assign the stem to a local folder before import.
        local_store = FolderStore(json_path=tmp_path / "folders.json")
        local_f = local_store.create_folder("Local Folder")
        local_store.add_stems(local_f.id, ["brain_01"])

        pack = _plain_pack(
            tmp_path / "pack.dcmpack",
            _manifest_bytes(
                items=[{"stem": "brain_01", "labeled": False}],
                folders=[{
                    "id": "abc", "name": "Brain",
                    "mandatory_labels": [], "stems": ["brain_01"],
                }],
            ),
            {"items/brain_01/brain_01.dcm": _FAKE_DCM},
        )
        extract_pack(pack, on_conflict="skip")
        store = FolderStore(json_path=tmp_path / "folders.json")
        # Stem was already in a folder → local assignment must be preserved.
        assert store.folder_for_stem("brain_01").id == local_f.id

    def test_pack_without_folders_does_not_touch_store(self, tmp_path, dirs):
        ul, *_ = dirs
        pack = _plain_pack(
            tmp_path / "pack.dcmpack",
            _manifest_bytes(items=[{"stem": "scan", "labeled": False}]),
            {"items/scan/scan.dcm": _FAKE_DCM},
        )
        extract_pack(pack)
        store = FolderStore(json_path=tmp_path / "folders.json")
        assert store.all_folders() == []

    def test_re_import_is_idempotent(self, tmp_path, dirs):
        """Importing the same pack twice must not duplicate folders or stems."""
        ul, *_ = dirs
        members = {"items/scan/scan.dcm": _FAKE_DCM}
        manifest = _manifest_bytes(
            items=[{"stem": "scan", "labeled": False}],
            folders=[{"id": "abc", "name": "Brain", "mandatory_labels": [], "stems": ["scan"]}],
        )
        pack = _plain_pack(tmp_path / "pack.dcmpack", manifest, members)

        extract_pack(pack, on_conflict="overwrite")
        (ul / "scan.dcm").unlink()
        extract_pack(pack, on_conflict="overwrite")

        store   = FolderStore(json_path=tmp_path / "folders.json")
        folders = store.all_folders()
        assert len(folders)              == 1
        assert folders[0].stems.count("scan") == 1  # not duplicated


# ---------------------------------------------------------------------------
# FolderStore.upsert_folder
# ---------------------------------------------------------------------------

class TestUpsertFolder:
    def test_creates_new_folder_with_given_id(self, store):
        f = store.upsert_folder("custom_id", "Brain", ["tumor"], ["s1"])
        assert f.id               == "custom_id"
        assert f.name             == "Brain"
        assert f.mandatory_labels == ("tumor",)
        assert "s1"               in f.stems

    def test_updates_existing_folder_name_and_labels(self, store):
        store.upsert_folder("x", "Old", ["old"], [])
        updated = store.upsert_folder("x", "New", ["new_a", "new_b"], [])
        assert updated.name             == "New"
        assert updated.mandatory_labels == ("new_a", "new_b")

    def test_existing_stems_preserved_on_update(self, store):
        store.upsert_folder("x", "Brain", [], ["s1", "s2"])
        updated = store.upsert_folder("x", "Brain Renamed", [], ["s3"])
        assert "s1" in updated.stems
        assert "s2" in updated.stems
        assert "s3" in updated.stems

    def test_empty_stems_list_does_not_fail(self, store):
        f = store.upsert_folder("x", "Brain", [], [])
        assert f.stems == ()

    def test_blank_name_raises_folder_name_error(self, store):
        from core.folder_store import FolderNameError
        with pytest.raises(FolderNameError):
            store.upsert_folder("x", "  ", [], [])

    def test_persists_to_disk(self, tmp_path):
        path   = tmp_path / "folders.json"
        store1 = FolderStore(json_path=path)
        store1.upsert_folder("abc", "Brain", ["tumor"], ["s1"])

        store2 = FolderStore(json_path=path)
        f      = store2.get_folder("abc")
        assert f.name             == "Brain"
        assert f.mandatory_labels == ("tumor",)
        assert "s1"               in f.stems

    def test_upsert_twice_is_idempotent_for_stems(self, store):
        store.upsert_folder("x", "Brain", [], ["s1"])
        store.upsert_folder("x", "Brain", [], ["s1"])
        f = store.get_folder("x")
        assert f.stems.count("s1") == 1


# ---------------------------------------------------------------------------
# Full round-trip: create_pack → extract_pack → FolderStore
# ---------------------------------------------------------------------------

class TestRoundTripFolders:
    def test_folder_survives_create_extract_cycle(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)

        pf   = PackFolder("round_id", "Round Trip Folder", ("tumor",), ("scan",))
        pack = create_pack(["scan"], tmp_path / "out.dcmpack", pack_folders=[pf])

        (ul / "scan.dcm").unlink()
        extract_pack(pack)

        store = FolderStore(json_path=tmp_path / "folders.json")
        f     = store.folder_for_stem("scan")
        assert f is not None
        assert f.id               == "round_id"
        assert f.name             == "Round Trip Folder"
        assert f.mandatory_labels == ("tumor",)

    def test_multiple_folders_survive_round_trip(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "a.dcm", _FAKE_DCM)
        _write(ul / "b.dcm", _FAKE_DCM)

        pfs = [
            PackFolder("id_a", "Alpha", ("label_x",), ("a",)),
            PackFolder("id_b", "Beta",  (),            ("b",)),
        ]
        pack = create_pack(["a", "b"], tmp_path / "out.dcmpack", pack_folders=pfs)
        for f in ul.iterdir():
            f.unlink()

        extract_pack(pack)

        store = FolderStore(json_path=tmp_path / "folders.json")
        fa    = store.folder_for_stem("a")
        fb    = store.folder_for_stem("b")
        assert fa.id               == "id_a"
        assert fa.mandatory_labels == ("label_x",)
        assert fb.id               == "id_b"
        assert fb.mandatory_labels == ()

    def test_pack_without_folders_leaves_store_untouched(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)

        # Create a local folder before import
        store = FolderStore(json_path=tmp_path / "folders.json")
        store.create_folder("Local Folder")

        pack = create_pack(["scan"], tmp_path / "out.dcmpack")
        (ul / "scan.dcm").unlink()
        extract_pack(pack)

        store2 = FolderStore(json_path=tmp_path / "folders.json")
        assert len(store2.all_folders()) == 1   # untouched
        assert store2.all_folders()[0].name == "Local Folder"