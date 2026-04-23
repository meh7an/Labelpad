"""
tests/test_folder_store.py
Unit tests for core/folder_store.py
Run with:  python -m pytest tests/ -v
"""

import json
from pathlib import Path

import pytest

from core.folder_store import (
    Folder,
    FolderNameError,
    FolderNotFoundError,
    FolderStore,
    FolderStoreError,
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path: Path) -> FolderStore:
    """Fresh FolderStore backed by a temp file for each test."""
    return FolderStore(json_path=tmp_path / "folders.json")


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    """Return a reusable path for constructing multiple FolderStore instances."""
    return tmp_path / "folders.json"


# ---------------------------------------------------------------------------
# Folder dataclass
# ---------------------------------------------------------------------------

class TestFolder:
    def test_fields_accessible(self):
        f = Folder(id="abc", name="Head CT", mandatory_labels=("tumor",), stems=("s1",))
        assert f.id               == "abc"
        assert f.name             == "Head CT"
        assert f.mandatory_labels == ("tumor",)
        assert f.stems            == ("s1",)

    def test_is_frozen(self):
        f = Folder(id="abc", name="X", mandatory_labels=(), stems=())
        with pytest.raises(Exception):
            f.name = "Y"  # type: ignore[misc]

    def test_mandatory_labels_is_tuple(self):
        f = Folder(id="a", name="X", mandatory_labels=("a", "b"), stems=())
        assert isinstance(f.mandatory_labels, tuple)

    def test_stems_is_tuple(self):
        f = Folder(id="a", name="X", mandatory_labels=(), stems=("s1", "s2"))
        assert isinstance(f.stems, tuple)

    def test_empty_mandatory_labels_and_stems(self):
        f = Folder(id="a", name="X", mandatory_labels=(), stems=())
        assert f.mandatory_labels == ()
        assert f.stems            == ()


# ---------------------------------------------------------------------------
# FolderStore construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_starts_empty_when_file_absent(self, store: FolderStore):
        assert store.all_folders() == []

    def test_loads_existing_file(self, store_path: Path):
        data = {
            "schema_version": 1,
            "folders": [
                {
                    "id": "aa11bb22",
                    "name": "Brain",
                    "mandatory_labels": ["tumor"],
                    "stems": ["s1", "s2"],
                }
            ],
        }
        store_path.write_text(json.dumps(data), encoding="utf-8")
        store = FolderStore(json_path=store_path)
        folders = store.all_folders()
        assert len(folders)                  == 1
        assert folders[0].id                 == "aa11bb22"
        assert folders[0].name               == "Brain"
        assert folders[0].mandatory_labels   == ("tumor",)
        assert folders[0].stems              == ("s1", "s2")

    def test_corrupt_json_starts_empty(self, store_path: Path):
        store_path.write_text("{ not valid json }", encoding="utf-8")
        store = FolderStore(json_path=store_path)
        assert store.all_folders() == []

    def test_missing_required_key_starts_empty(self, store_path: Path):
        store_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        store = FolderStore(json_path=store_path)
        assert store.all_folders() == []

    def test_empty_folders_list_starts_empty(self, store_path: Path):
        store_path.write_text(
            json.dumps({"schema_version": 1, "folders": []}), encoding="utf-8"
        )
        store = FolderStore(json_path=store_path)
        assert store.all_folders() == []


# ---------------------------------------------------------------------------
# create_folder
# ---------------------------------------------------------------------------

class TestCreateFolder:
    def test_returns_folder_with_correct_name(self, store: FolderStore):
        f = store.create_folder("Chest CT")
        assert f.name == "Chest CT"

    def test_id_is_8_hex_chars(self, store: FolderStore):
        f = store.create_folder("X")
        assert len(f.id) == 8
        assert all(c in "0123456789abcdef" for c in f.id)

    def test_default_mandatory_labels_is_empty(self, store: FolderStore):
        f = store.create_folder("X")
        assert f.mandatory_labels == ()

    def test_mandatory_labels_stored_as_tuple(self, store: FolderStore):
        f = store.create_folder("X", mandatory_labels=["tumor", "edema"])
        assert f.mandatory_labels == ("tumor", "edema")

    def test_stems_is_empty_on_creation(self, store: FolderStore):
        f = store.create_folder("X")
        assert f.stems == ()

    def test_persists_to_disk(self, store_path: Path):
        store = FolderStore(json_path=store_path)
        store.create_folder("Brain", mandatory_labels=["tumor"])
        data = json.loads(store_path.read_text(encoding="utf-8"))
        assert len(data["folders"]) == 1
        assert data["folders"][0]["name"] == "Brain"

    def test_multiple_folders_have_unique_ids(self, store: FolderStore):
        ids = {store.create_folder(f"Folder {i}").id for i in range(20)}
        assert len(ids) == 20

    def test_empty_name_raises_folder_name_error(self, store: FolderStore):
        with pytest.raises(FolderNameError):
            store.create_folder("")

    def test_whitespace_name_raises_folder_name_error(self, store: FolderStore):
        with pytest.raises(FolderNameError):
            store.create_folder("   ")

    def test_name_is_stripped(self, store: FolderStore):
        f = store.create_folder("  Brain CT  ")
        assert f.name == "Brain CT"

    def test_folder_name_error_is_folder_store_error(self):
        assert issubclass(FolderNameError, FolderStoreError)

    def test_folder_not_found_error_is_folder_store_error(self):
        assert issubclass(FolderNotFoundError, FolderStoreError)


# ---------------------------------------------------------------------------
# all_folders / get_folder
# ---------------------------------------------------------------------------

class TestAllFoldersAndGet:
    def test_empty_when_no_folders_created(self, store: FolderStore):
        assert store.all_folders() == []

    def test_returns_all_created_folders(self, store: FolderStore):
        a = store.create_folder("A")
        b = store.create_folder("B")
        ids = {f.id for f in store.all_folders()}
        assert {a.id, b.id} <= ids

    def test_insertion_order_preserved(self, store: FolderStore):
        names = ["Alpha", "Beta", "Gamma", "Delta"]
        for n in names:
            store.create_folder(n)
        assert [f.name for f in store.all_folders()] == names

    def test_get_folder_returns_correct_folder(self, store: FolderStore):
        f = store.create_folder("Brain")
        retrieved = store.get_folder(f.id)
        assert retrieved.name == "Brain"
        assert retrieved.id   == f.id

    def test_get_folder_invalid_id_raises(self, store: FolderStore):
        with pytest.raises(FolderNotFoundError):
            store.get_folder("deadbeef")


# ---------------------------------------------------------------------------
# rename_folder
# ---------------------------------------------------------------------------

class TestRenameFolder:
    def test_name_is_updated(self, store: FolderStore):
        f       = store.create_folder("Old Name")
        updated = store.rename_folder(f.id, "New Name")
        assert updated.name == "New Name"

    def test_id_is_preserved(self, store: FolderStore):
        f       = store.create_folder("X")
        updated = store.rename_folder(f.id, "Y")
        assert updated.id == f.id

    def test_other_fields_preserved(self, store: FolderStore):
        f = store.create_folder("X", mandatory_labels=["tumor"])
        f = store.add_stems(f.id, ["s1"])
        updated = store.rename_folder(f.id, "Y")
        assert updated.mandatory_labels == ("tumor",)
        assert updated.stems            == ("s1",)

    def test_rename_persists(self, store_path: Path):
        store = FolderStore(json_path=store_path)
        f     = store.create_folder("Old")
        store.rename_folder(f.id, "New")
        store2 = FolderStore(json_path=store_path)
        assert store2.get_folder(f.id).name == "New"

    def test_invalid_id_raises(self, store: FolderStore):
        with pytest.raises(FolderNotFoundError):
            store.rename_folder("deadbeef", "X")

    def test_empty_new_name_raises(self, store: FolderStore):
        f = store.create_folder("X")
        with pytest.raises(FolderNameError):
            store.rename_folder(f.id, "")


# ---------------------------------------------------------------------------
# delete_folder
# ---------------------------------------------------------------------------

class TestDeleteFolder:
    def test_folder_is_removed(self, store: FolderStore):
        f = store.create_folder("Brain")
        store.delete_folder(f.id)
        assert not any(x.id == f.id for x in store.all_folders())

    def test_invalid_id_raises(self, store: FolderStore):
        with pytest.raises(FolderNotFoundError):
            store.delete_folder("deadbeef")

    def test_stems_become_unassigned(self, store: FolderStore):
        f = store.create_folder("Brain")
        store.add_stems(f.id, ["s1", "s2"])
        store.delete_folder(f.id)
        assert store.folder_for_stem("s1") is None
        assert store.folder_for_stem("s2") is None

    def test_delete_persists(self, store_path: Path):
        store = FolderStore(json_path=store_path)
        f     = store.create_folder("X")
        store.delete_folder(f.id)
        store2 = FolderStore(json_path=store_path)
        assert store2.all_folders() == []

    def test_other_folders_unaffected(self, store: FolderStore):
        a = store.create_folder("A")
        b = store.create_folder("B")
        store.delete_folder(a.id)
        assert store.get_folder(b.id).name == "B"


# ---------------------------------------------------------------------------
# set_mandatory_labels
# ---------------------------------------------------------------------------

class TestSetMandatoryLabels:
    def test_replaces_labels(self, store: FolderStore):
        f       = store.create_folder("X", mandatory_labels=["old"])
        updated = store.set_mandatory_labels(f.id, ["tumor", "edema"])
        assert updated.mandatory_labels == ("tumor", "edema")

    def test_empty_list_clears_labels(self, store: FolderStore):
        f       = store.create_folder("X", mandatory_labels=["tumor"])
        updated = store.set_mandatory_labels(f.id, [])
        assert updated.mandatory_labels == ()

    def test_other_fields_preserved(self, store: FolderStore):
        f = store.create_folder("Brain")
        f = store.add_stems(f.id, ["s1"])
        updated = store.set_mandatory_labels(f.id, ["lesion"])
        assert updated.name   == "Brain"
        assert updated.stems  == ("s1",)

    def test_persists(self, store_path: Path):
        store   = FolderStore(json_path=store_path)
        f       = store.create_folder("X")
        store.set_mandatory_labels(f.id, ["a", "b"])
        store2  = FolderStore(json_path=store_path)
        assert store2.get_folder(f.id).mandatory_labels == ("a", "b")

    def test_invalid_id_raises(self, store: FolderStore):
        with pytest.raises(FolderNotFoundError):
            store.set_mandatory_labels("deadbeef", ["x"])


# ---------------------------------------------------------------------------
# add_stems
# ---------------------------------------------------------------------------

class TestAddStems:
    def test_stems_appear_in_folder(self, store: FolderStore):
        f = store.create_folder("X")
        store.add_stems(f.id, ["s1", "s2"])
        assert "s1" in store.get_folder(f.id).stems
        assert "s2" in store.get_folder(f.id).stems

    def test_insertion_order_preserved(self, store: FolderStore):
        f = store.create_folder("X")
        store.add_stems(f.id, ["c", "a", "b"])
        assert store.get_folder(f.id).stems == ("c", "a", "b")

    def test_duplicate_stems_not_added_twice(self, store: FolderStore):
        f = store.create_folder("X")
        store.add_stems(f.id, ["s1"])
        store.add_stems(f.id, ["s1", "s2"])
        stems = store.get_folder(f.id).stems
        assert stems.count("s1") == 1
        assert "s2" in stems

    def test_invalid_id_raises(self, store: FolderStore):
        with pytest.raises(FolderNotFoundError):
            store.add_stems("deadbeef", ["s1"])

    def test_persists(self, store_path: Path):
        store = FolderStore(json_path=store_path)
        f     = store.create_folder("X")
        store.add_stems(f.id, ["s1"])
        store2 = FolderStore(json_path=store_path)
        assert "s1" in store2.get_folder(f.id).stems

    def test_stem_uniqueness_invariant_evicts_from_other_folder(self, store: FolderStore):
        """A stem added to folder B must be removed from folder A."""
        a = store.create_folder("A")
        b = store.create_folder("B")
        store.add_stems(a.id, ["shared"])
        store.add_stems(b.id, ["shared"])
        assert "shared" not in store.get_folder(a.id).stems
        assert "shared"     in store.get_folder(b.id).stems

    def test_stem_uniqueness_partial_overlap(self, store: FolderStore):
        """Only the overlapping stem is evicted; the other stays in folder A."""
        a = store.create_folder("A")
        b = store.create_folder("B")
        store.add_stems(a.id, ["s1", "s2"])
        store.add_stems(b.id, ["s2", "s3"])
        assert "s1"     in store.get_folder(a.id).stems
        assert "s2" not in store.get_folder(a.id).stems
        assert "s2"     in store.get_folder(b.id).stems
        assert "s3"     in store.get_folder(b.id).stems

    def test_folder_for_stem_updated_after_eviction(self, store: FolderStore):
        a = store.create_folder("A")
        b = store.create_folder("B")
        store.add_stems(a.id, ["stem"])
        store.add_stems(b.id, ["stem"])
        owner = store.folder_for_stem("stem")
        assert owner is not None
        assert owner.id == b.id


# ---------------------------------------------------------------------------
# remove_stems
# ---------------------------------------------------------------------------

class TestRemoveStems:
    def test_stem_is_removed(self, store: FolderStore):
        f = store.create_folder("X")
        store.add_stems(f.id, ["s1", "s2"])
        store.remove_stems(f.id, ["s1"])
        assert "s1" not in store.get_folder(f.id).stems
        assert "s2"     in store.get_folder(f.id).stems

    def test_absent_stems_silently_ignored(self, store: FolderStore):
        f = store.create_folder("X")
        store.add_stems(f.id, ["s1"])
        store.remove_stems(f.id, ["ghost"])  # must not raise
        assert "s1" in store.get_folder(f.id).stems

    def test_remove_all_stems_leaves_empty_tuple(self, store: FolderStore):
        f = store.create_folder("X")
        store.add_stems(f.id, ["s1", "s2"])
        store.remove_stems(f.id, ["s1", "s2"])
        assert store.get_folder(f.id).stems == ()

    def test_invalid_id_raises(self, store: FolderStore):
        with pytest.raises(FolderNotFoundError):
            store.remove_stems("deadbeef", ["s1"])

    def test_persists(self, store_path: Path):
        store = FolderStore(json_path=store_path)
        f     = store.create_folder("X")
        store.add_stems(f.id, ["s1", "s2"])
        store.remove_stems(f.id, ["s1"])
        store2 = FolderStore(json_path=store_path)
        assert "s1" not in store2.get_folder(f.id).stems
        assert "s2"     in store2.get_folder(f.id).stems

    def test_other_fields_preserved(self, store: FolderStore):
        f = store.create_folder("Brain", mandatory_labels=["tumor"])
        store.add_stems(f.id, ["s1", "s2"])
        updated = store.remove_stems(f.id, ["s1"])
        assert updated.name             == "Brain"
        assert updated.mandatory_labels == ("tumor",)


# ---------------------------------------------------------------------------
# folder_for_stem / mandatory_labels_for_stem
# ---------------------------------------------------------------------------

class TestFolderForStem:
    def test_returns_owning_folder(self, store: FolderStore):
        f = store.create_folder("Brain")
        store.add_stems(f.id, ["s1"])
        owner = store.folder_for_stem("s1")
        assert owner is not None
        assert owner.id == f.id

    def test_returns_none_for_unassigned_stem(self, store: FolderStore):
        assert store.folder_for_stem("ghost") is None

    def test_returns_none_after_removal(self, store: FolderStore):
        f = store.create_folder("X")
        store.add_stems(f.id, ["s1"])
        store.remove_stems(f.id, ["s1"])
        assert store.folder_for_stem("s1") is None

    def test_returns_none_after_folder_deleted(self, store: FolderStore):
        f = store.create_folder("X")
        store.add_stems(f.id, ["s1"])
        store.delete_folder(f.id)
        assert store.folder_for_stem("s1") is None


class TestMandatoryLabelsForStem:
    def test_returns_labels_for_assigned_stem(self, store: FolderStore):
        f = store.create_folder("X", mandatory_labels=["tumor", "edema"])
        store.add_stems(f.id, ["s1"])
        assert store.mandatory_labels_for_stem("s1") == ("tumor", "edema")

    def test_returns_empty_tuple_for_unassigned_stem(self, store: FolderStore):
        assert store.mandatory_labels_for_stem("ghost") == ()

    def test_returns_empty_tuple_for_folder_with_no_requirements(self, store: FolderStore):
        f = store.create_folder("X")
        store.add_stems(f.id, ["s1"])
        assert store.mandatory_labels_for_stem("s1") == ()


# ---------------------------------------------------------------------------
# Round-trip persistence
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_full_round_trip(self, store_path: Path):
        store1 = FolderStore(json_path=store_path)
        f      = store1.create_folder("Brain CT", mandatory_labels=["tumor", "edema"])
        store1.add_stems(f.id, ["scan_01", "scan_02"])

        store2   = FolderStore(json_path=store_path)
        folders2 = store2.all_folders()
        assert len(folders2)                   == 1
        assert folders2[0].name                == "Brain CT"
        assert folders2[0].mandatory_labels    == ("tumor", "edema")
        assert folders2[0].stems               == ("scan_01", "scan_02")

    def test_multiple_folders_round_trip(self, store_path: Path):
        store1 = FolderStore(json_path=store_path)
        a      = store1.create_folder("A")
        b      = store1.create_folder("B")
        store1.add_stems(a.id, ["s1"])
        store1.add_stems(b.id, ["s2"])

        store2 = FolderStore(json_path=store_path)
        assert len(store2.all_folders())         == 2
        assert "s1" in store2.get_folder(a.id).stems
        assert "s2" in store2.get_folder(b.id).stems

    def test_reload_reflects_disk_changes(self, store_path: Path):
        store = FolderStore(json_path=store_path)
        f     = store.create_folder("X")
        store.add_stems(f.id, ["s1"])

        # Simulate an external write by creating a second store instance.
        store2 = FolderStore(json_path=store_path)
        store2.rename_folder(f.id, "Y")

        store.reload()
        assert store.get_folder(f.id).name == "Y"

    def test_uniqueness_invariant_survives_round_trip(self, store_path: Path):
        store1 = FolderStore(json_path=store_path)
        a      = store1.create_folder("A")
        b      = store1.create_folder("B")
        store1.add_stems(a.id, ["shared"])
        store1.add_stems(b.id, ["shared"])   # evicts from A

        store2 = FolderStore(json_path=store_path)
        assert "shared" not in store2.get_folder(a.id).stems
        assert "shared"     in store2.get_folder(b.id).stems


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_no_tmp_file_remains_after_save(self, store_path: Path):
        store = FolderStore(json_path=store_path)
        store.create_folder("X")
        tmp = store_path.with_suffix(".tmp")
        assert not tmp.exists()

    def test_json_file_is_valid_after_save(self, store_path: Path):
        store = FolderStore(json_path=store_path)
        store.create_folder("Brain", mandatory_labels=["tumor"])
        data = json.loads(store_path.read_text(encoding="utf-8"))
        assert data["schema_version"]         == 1
        assert len(data["folders"])           == 1
        assert data["folders"][0]["name"]     == "Brain"
        assert data["folders"][0]["mandatory_labels"] == ["tumor"]