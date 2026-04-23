"""
tests/test_dcmpack.py
Unit tests for core/dcmpack.py  (M1 + M2)
Run with:  python -m pytest tests/ -v
"""

import json
from pathlib import Path

import pyzipper
import pytest

import core.dcmpack as dcmpack
from core.dcmpack import (
    DcmPackCorruptError,
    DcmPackError,
    DcmPackItem,
    DcmPackManifest,
    DcmPackPasswordError,
    DcmPackVersionError,
    ImportResult,
    LabelPatchError,
    create_pack,
    extract_pack,
    open_pack,
    patch_label_imagepath,
    peek_is_password_protected,
    read_manifest,
)

# ---------------------------------------------------------------------------
# Fixture byte payloads
# ---------------------------------------------------------------------------

_FAKE_DCM   = b"DICM_FAKE_PIXEL_DATA"
_FAKE_JPG   = b"\xff\xd8\xff\xe0FAKE_JPEG_DATA"
_FAKE_WND   = json.dumps(
    {"schema_version": 1, "source_file": "x.dcm", "window_center": 40.0, "window_width": 80.0}
).encode()
_FAKE_LABEL = json.dumps({
    "shapes": [],
    "imageWidth": 512,
    "imageHeight": 512,
    "imagePath": "/abs/path/from/original/machine/x.jpg",
}).encode()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _manifest_bytes(
    items=None,
    pack_name="test_pack",
    version=1,
    password_protected=False,
) -> bytes:
    return json.dumps({
        "schema_version":     version,
        "pack_name":          pack_name,
        "created_at":         "2026-04-23T00:00:00+00:00",
        "password_protected": password_protected,
        "items":              items or [],
    }).encode("utf-8")


def _plain_pack(path: Path, manifest: bytes, members: dict | None = None) -> Path:
    """Write a non-encrypted .dcmpack archive."""
    with pyzipper.AESZipFile(str(path), "w", compression=pyzipper.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        for arc_name, data in (members or {}).items():
            zf.writestr(arc_name, data)
    return path


def _encrypted_pack(
    path: Path,
    password: str,
    manifest: bytes,
    members: dict | None = None,
) -> Path:
    """Write an AES-256-encrypted .dcmpack archive."""
    with pyzipper.AESZipFile(
        str(path), "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.writestr("manifest.json", manifest)
        for arc_name, data in (members or {}).items():
            zf.writestr(arc_name, data)
    return path


# ---------------------------------------------------------------------------
# Shared fixture: redirect all four data-dir references into tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """
    Redirect _UNLABELED_DIR, _RASTER_DIR, _DATA_DIR, _LABELED_DIR in the
    dcmpack module to isolated tmp_path subdirectories for each test.
    """
    ul = tmp_path / "Unlabeled"
    rs = tmp_path / "Raster"
    dt = tmp_path / "Data"
    lb = tmp_path / "Labeled"
    for d in (ul, rs, dt, lb):
        d.mkdir()
    monkeypatch.setattr(dcmpack, "_UNLABELED_DIR", ul)
    monkeypatch.setattr(dcmpack, "_RASTER_DIR",    rs)
    monkeypatch.setattr(dcmpack, "_DATA_DIR",      dt)
    monkeypatch.setattr(dcmpack, "_LABELED_DIR",   lb)
    return ul, rs, dt, lb


# ---------------------------------------------------------------------------
# DcmPackItem
# ---------------------------------------------------------------------------

class TestDcmPackItem:
    def test_fields_accessible(self):
        item = DcmPackItem(stem="brain_001", labeled=False)
        assert item.stem    == "brain_001"
        assert item.labeled is False

    def test_is_frozen(self):
        item = DcmPackItem(stem="brain_001", labeled=False)
        with pytest.raises(Exception):
            item.stem = "other"  # type: ignore[misc]

    def test_labeled_true(self):
        assert DcmPackItem(stem="x", labeled=True).labeled is True


# ---------------------------------------------------------------------------
# DcmPackManifest
# ---------------------------------------------------------------------------

class TestDcmPackManifest:
    def test_fields_accessible(self):
        m = DcmPackManifest(
            schema_version=1,
            pack_name="batch_01",
            created_at="2026-04-23T00:00:00+00:00",
            password_protected=False,
            items=(DcmPackItem("a", False),),
        )
        assert m.pack_name           == "batch_01"
        assert m.schema_version      == 1
        assert m.password_protected  is False
        assert len(m.items)          == 1

    def test_empty_items_tuple(self):
        assert DcmPackManifest(1, "p", "", False, ()).items == ()


# ---------------------------------------------------------------------------
# ImportResult
# ---------------------------------------------------------------------------

class TestImportResult:
    def test_total_across_all_buckets(self):
        r = ImportResult(imported=["a", "b"], skipped=["c"], failed=[("d", "err")])
        assert r.total == 4

    def test_total_empty(self):
        assert ImportResult().total == 0

    def test_summary_all_buckets(self):
        r = ImportResult(imported=["a"], skipped=["b"], failed=[("c", "x")])
        assert "1 imported" in r.summary
        assert "1 skipped"  in r.summary
        assert "1 failed"   in r.summary

    def test_summary_empty(self):
        assert ImportResult().summary == "Nothing to import"

    def test_summary_imported_only(self):
        assert ImportResult(imported=["a", "b"]).summary == "2 imported"


# ---------------------------------------------------------------------------
# read_manifest
# ---------------------------------------------------------------------------

class TestReadManifest:
    def test_valid_manifest_with_items(self, tmp_path):
        pack = _plain_pack(
            tmp_path / "p.dcmpack",
            _manifest_bytes(items=[{"stem": "scan_01", "labeled": True}]),
        )
        with open_pack(pack) as zf:
            m = read_manifest(zf)

        assert m.pack_name          == "test_pack"
        assert m.schema_version     == 1
        assert m.password_protected is False
        assert len(m.items)         == 1
        assert m.items[0].stem      == "scan_01"
        assert m.items[0].labeled   is True

    def test_valid_manifest_empty_items(self, tmp_path):
        pack = _plain_pack(tmp_path / "p.dcmpack", _manifest_bytes())
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert m.items == ()

    def test_missing_manifest_raises_corrupt(self, tmp_path):
        pack = tmp_path / "bad.dcmpack"
        with pyzipper.AESZipFile(str(pack), "w") as zf:
            zf.writestr("other.txt", b"data")
        with open_pack(pack) as zf:
            with pytest.raises(DcmPackCorruptError, match="missing manifest"):
                read_manifest(zf)

    def test_invalid_json_raises_corrupt(self, tmp_path):
        pack = tmp_path / "bad.dcmpack"
        with pyzipper.AESZipFile(str(pack), "w") as zf:
            zf.writestr("manifest.json", b"{ not valid }")
        with open_pack(pack) as zf:
            with pytest.raises(DcmPackCorruptError, match="not valid JSON"):
                read_manifest(zf)

    def test_unsupported_version_raises_version_error(self, tmp_path):
        pack = _plain_pack(tmp_path / "p.dcmpack", _manifest_bytes(version=99))
        with open_pack(pack) as zf:
            with pytest.raises(DcmPackVersionError):
                read_manifest(zf)

    def test_missing_required_key_raises_corrupt(self, tmp_path):
        bad = json.dumps({"schema_version": 1}).encode()
        pack = _plain_pack(tmp_path / "p.dcmpack", bad)
        with open_pack(pack) as zf:
            with pytest.raises(DcmPackCorruptError):
                read_manifest(zf)

    def test_password_protected_without_key_raises_password_error(self, tmp_path):
        pack = _encrypted_pack(
            tmp_path / "p.dcmpack", "secret",
            _manifest_bytes(password_protected=True),
        )
        with open_pack(pack) as zf:
            with pytest.raises(DcmPackPasswordError):
                read_manifest(zf)


# ---------------------------------------------------------------------------
# peek_is_password_protected
# ---------------------------------------------------------------------------

class TestPeekIsPasswordProtected:
    def test_unprotected_pack_returns_false(self, tmp_path):
        pack = _plain_pack(tmp_path / "p.dcmpack", _manifest_bytes())
        assert peek_is_password_protected(pack) is False

    def test_protected_pack_returns_true(self, tmp_path):
        pack = _encrypted_pack(
            tmp_path / "p.dcmpack", "secret",
            _manifest_bytes(password_protected=True),
        )
        assert peek_is_password_protected(pack) is True

    def test_nonexistent_file_returns_false(self, tmp_path):
        assert peek_is_password_protected(tmp_path / "ghost.dcmpack") is False


# ---------------------------------------------------------------------------
# open_pack
# ---------------------------------------------------------------------------

class TestOpenPack:
    def test_opens_valid_unprotected_pack(self, tmp_path):
        pack = _plain_pack(tmp_path / "p.dcmpack", _manifest_bytes())
        with open_pack(pack) as zf:
            assert zf is not None

    def test_nonexistent_file_raises_corrupt(self, tmp_path):
        with pytest.raises(DcmPackCorruptError, match="not found"):
            open_pack(tmp_path / "ghost.dcmpack")


# ---------------------------------------------------------------------------
# _patch_label_bytes  (private, tested directly — pure function)
# ---------------------------------------------------------------------------

class TestPatchLabelBytes:
    def test_replaces_image_path(self):
        raw     = _FAKE_LABEL
        patched = dcmpack._patch_label_bytes(raw, "./brain_001.jpg")
        data    = json.loads(patched)
        assert data["imagePath"] == "./brain_001.jpg"

    def test_preserves_other_fields(self):
        raw     = _FAKE_LABEL
        patched = dcmpack._patch_label_bytes(raw, "./x.jpg")
        data    = json.loads(patched)
        assert data["shapes"]      == []
        assert data["imageWidth"]  == 512
        assert data["imageHeight"] == 512

    def test_adds_image_path_when_absent(self):
        raw     = json.dumps({"shapes": [], "imageWidth": 256, "imageHeight": 256}).encode()
        patched = dcmpack._patch_label_bytes(raw, "./new.jpg")
        data    = json.loads(patched)
        assert data["imagePath"] == "./new.jpg"

    def test_invalid_json_raises_label_patch_error(self):
        with pytest.raises(LabelPatchError):
            dcmpack._patch_label_bytes(b"{ not json }", "./x.jpg")

    def test_invalid_utf8_raises_label_patch_error(self):
        with pytest.raises(LabelPatchError):
            dcmpack._patch_label_bytes(b"\xff\xfe invalid", "./x.jpg")

    def test_output_is_valid_utf8_bytes(self):
        patched = dcmpack._patch_label_bytes(_FAKE_LABEL, "./x.jpg")
        assert isinstance(patched, bytes)
        patched.decode("utf-8")  # must not raise


# ---------------------------------------------------------------------------
# patch_label_imagepath  (public, on-disk)
# ---------------------------------------------------------------------------

class TestPatchLabelImagepath:
    def test_rewrites_image_path_in_place(self, tmp_path):
        f = _write(tmp_path / "ann.json", _FAKE_LABEL)
        patch_label_imagepath(f, Path("/new/raster/path.jpg"))
        data = json.loads(f.read_bytes())
        assert data["imagePath"] == "/new/raster/path.jpg"

    def test_accepts_path_object(self, tmp_path):
        f = _write(tmp_path / "ann.json", _FAKE_LABEL)
        patch_label_imagepath(f, Path("/some/path.jpg"))
        data = json.loads(f.read_bytes())
        assert data["imagePath"] == "/some/path.jpg"

    def test_accepts_string(self, tmp_path):
        f = _write(tmp_path / "ann.json", _FAKE_LABEL)
        patch_label_imagepath(f, "./stem.jpg")
        data = json.loads(f.read_bytes())
        assert data["imagePath"] == "./stem.jpg"

    def test_preserves_shapes_and_dimensions(self, tmp_path):
        f = _write(tmp_path / "ann.json", _FAKE_LABEL)
        patch_label_imagepath(f, "./x.jpg")
        data = json.loads(f.read_bytes())
        assert data["shapes"]      == []
        assert data["imageWidth"]  == 512
        assert data["imageHeight"] == 512

    def test_missing_file_raises_label_patch_error(self, tmp_path):
        with pytest.raises(LabelPatchError, match="Cannot read"):
            patch_label_imagepath(tmp_path / "ghost.json", "./x.jpg")

    def test_invalid_json_on_disk_raises_label_patch_error(self, tmp_path):
        f = _write(tmp_path / "bad.json", b"{ not valid json }")
        with pytest.raises(LabelPatchError):
            patch_label_imagepath(f, "./x.jpg")

    def test_idempotent_on_repeated_calls(self, tmp_path):
        f = _write(tmp_path / "ann.json", _FAKE_LABEL)
        patch_label_imagepath(f, "./stem.jpg")
        patch_label_imagepath(f, "./stem.jpg")
        data = json.loads(f.read_bytes())
        assert data["imagePath"] == "./stem.jpg"


# ---------------------------------------------------------------------------
# create_pack
# ---------------------------------------------------------------------------

class TestCreatePack:
    def test_unlabeled_only_contains_dcm(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan_01.dcm", _FAKE_DCM)

        pack = create_pack(["scan_01"], tmp_path / "out.dcmpack")

        assert pack.exists()
        with open_pack(pack) as zf:
            m     = read_manifest(zf)
            names = zf.namelist()

        assert len(m.items)                == 1
        assert m.items[0].labeled          is False
        assert "items/scan_01/scan_01.dcm"  in names
        assert "items/scan_01/scan_01.jpg"  not in names

    def test_labeled_item_includes_all_four_assets(self, tmp_path, dirs):
        ul, rs, dt, lb = dirs
        _write(ul / "ct_chest.dcm",  _FAKE_DCM)
        _write(rs / "ct_chest.jpg",  _FAKE_JPG)
        _write(dt / "ct_chest.json", _FAKE_WND)
        _write(lb / "ct_chest.json", _FAKE_LABEL)

        pack = create_pack(["ct_chest"], tmp_path / "out.dcmpack")

        with open_pack(pack) as zf:
            m          = read_manifest(zf)
            names      = zf.namelist()
            label_raw  = zf.read("items/ct_chest/ct_chest.json")

        assert m.items[0].labeled                        is True
        assert "items/ct_chest/ct_chest.dcm"             in names
        assert "items/ct_chest/ct_chest.jpg"             in names
        assert "items/ct_chest/ct_chest_windowing.json"  in names
        assert "items/ct_chest/ct_chest.json"            in names

        # imagePath must be normalised to the portable relative form.
        label_data = json.loads(label_raw)
        assert label_data["imagePath"] == "./ct_chest.jpg"

    def test_label_json_on_disk_not_modified_by_packing(self, tmp_path, dirs):
        ul, rs, dt, lb = dirs
        _write(ul / "scan.dcm",  _FAKE_DCM)
        _write(rs / "scan.jpg",  _FAKE_JPG)
        _write(dt / "scan.json", _FAKE_WND)
        _write(lb / "scan.json", _FAKE_LABEL)

        original_on_disk = (lb / "scan.json").read_bytes()
        create_pack(["scan"], tmp_path / "out.dcmpack")
        assert (lb / "scan.json").read_bytes() == original_on_disk

    def test_manifest_password_protected_flag_set(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)

        pack = create_pack(["scan"], tmp_path / "out.dcmpack", password="s3cret")

        with open_pack(pack, password="s3cret") as zf:
            m = read_manifest(zf)
        assert m.password_protected is True

    def test_encrypted_pack_unreadable_without_password(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)
        pack = create_pack(["scan"], tmp_path / "out.dcmpack", password="s3cret")

        with open_pack(pack) as zf:
            with pytest.raises(DcmPackPasswordError):
                read_manifest(zf)

    def test_multiple_stems_recorded_in_manifest(self, tmp_path, dirs):
        ul, *_ = dirs
        for name in ("a", "b", "c"):
            _write(ul / f"{name}.dcm", _FAKE_DCM)

        pack = create_pack(["a", "b", "c"], tmp_path / "out.dcmpack")

        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert len(m.items)               == 3
        assert {i.stem for i in m.items}  == {"a", "b", "c"}

    def test_pack_name_derived_from_dest_filename(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)
        pack = create_pack(["scan"], tmp_path / "batch_april.dcmpack")
        with open_pack(pack) as zf:
            m = read_manifest(zf)
        assert m.pack_name == "batch_april"

    def test_missing_dcm_raises_dcmpack_error(self, tmp_path, dirs):
        with pytest.raises(DcmPackError, match="not found"):
            create_pack(["ghost"], tmp_path / "out.dcmpack")

    def test_dicom_extension_variant_is_packed(self, tmp_path, dirs):
        """A file stored as .dicom must be found and bundled without error."""
        ul, *_ = dirs
        _write(ul / "scan.dicom", _FAKE_DCM)
        pack = create_pack(["scan"], tmp_path / "out.dcmpack")
        with open_pack(pack) as zf:
            m     = read_manifest(zf)
            names = zf.namelist()
        assert len(m.items)          == 1
        assert m.items[0].stem       == "scan"
        # Source extension is normalised to .dcm inside the archive.
        assert "items/scan/scan.dcm"  in names

    def test_creates_parent_directories(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)
        dest = tmp_path / "nested" / "deep" / "out.dcmpack"
        create_pack(["scan"], dest)
        assert dest.exists()

    def test_returned_path_is_resolved(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "scan.dcm", _FAKE_DCM)
        pack = create_pack(["scan"], tmp_path / "out.dcmpack")
        assert pack.is_absolute()


# ---------------------------------------------------------------------------
# extract_pack
# ---------------------------------------------------------------------------

class TestExtractPack:
    def _build_pack(
        self,
        tmp_path: Path,
        labeled: bool = False,
        password: str | None = None,
        stem: str = "brain_01",
    ) -> Path:
        members = {f"items/{stem}/{stem}.dcm": _FAKE_DCM}
        if labeled:
            members[f"items/{stem}/{stem}.jpg"]            = _FAKE_JPG
            members[f"items/{stem}/{stem}_windowing.json"] = _FAKE_WND
            members[f"items/{stem}/{stem}.json"]           = _FAKE_LABEL

        manifest = _manifest_bytes(
            items=[{"stem": stem, "labeled": labeled}],
            password_protected=bool(password),
        )
        pack = tmp_path / "pack.dcmpack"
        if password:
            return _encrypted_pack(pack, password, manifest, members)
        return _plain_pack(pack, manifest, members)

    def test_unlabeled_extraction_creates_dcm_only(self, tmp_path, dirs):
        ul, rs, *_ = dirs
        result = extract_pack(self._build_pack(tmp_path))

        assert result.imported == ["brain_01"]
        assert (ul / "brain_01.dcm").exists()
        assert not (rs / "brain_01.jpg").exists()

    def test_labeled_extraction_creates_all_files(self, tmp_path, dirs):
        ul, rs, dt, lb = dirs
        result = extract_pack(self._build_pack(tmp_path, labeled=True))

        assert result.imported == ["brain_01"]
        assert (ul / "brain_01.dcm").exists()
        assert (rs / "brain_01.jpg").exists()
        assert (dt / "brain_01.json").exists()
        assert (lb / "brain_01.json").exists()

        # imagePath must be patched to the absolute raster path on this machine.
        label_data = json.loads((lb / "brain_01.json").read_bytes())
        assert label_data["imagePath"] == str(rs / "brain_01.jpg")

    def test_dcm_and_raster_contents_are_intact(self, tmp_path, dirs):
        ul, rs, *_ = dirs
        extract_pack(self._build_pack(tmp_path, labeled=True))

        assert (ul / "brain_01.dcm").read_bytes() == _FAKE_DCM
        assert (rs / "brain_01.jpg").read_bytes() == _FAKE_JPG

    def test_skip_conflict_preserves_original(self, tmp_path, dirs):
        ul, *_ = dirs
        existing = _write(ul / "brain_01.dcm", b"ORIGINAL")
        result   = extract_pack(self._build_pack(tmp_path), on_conflict="skip")

        assert result.skipped        == ["brain_01"]
        assert result.imported       == []
        assert existing.read_bytes() == b"ORIGINAL"

    def test_overwrite_conflict_replaces_file(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "brain_01.dcm", b"ORIGINAL")
        result = extract_pack(self._build_pack(tmp_path), on_conflict="overwrite")

        assert result.imported                    == ["brain_01"]
        assert (ul / "brain_01.dcm").read_bytes() == _FAKE_DCM

    def test_password_protected_round_trip(self, tmp_path, dirs):
        ul, *_ = dirs
        result = extract_pack(self._build_pack(tmp_path, password="hunter2"), password="hunter2")

        assert result.imported == ["brain_01"]
        assert (ul / "brain_01.dcm").exists()

    def test_wrong_password_raises_password_error(self, tmp_path, dirs):
        pack = self._build_pack(tmp_path, password="correct")
        with pytest.raises(DcmPackPasswordError):
            extract_pack(pack, password="wrong")

    def test_missing_member_recorded_as_failed(self, tmp_path, dirs):
        manifest = _manifest_bytes(items=[{"stem": "brain_01", "labeled": True}])
        pack     = _plain_pack(
            tmp_path / "bad.dcmpack",
            manifest,
            {"items/brain_01/brain_01.dcm": _FAKE_DCM},  # labeled assets absent
        )
        result = extract_pack(pack)

        assert len(result.failed)  == 1
        assert result.failed[0][0] == "brain_01"

    def test_nonexistent_pack_raises_corrupt(self, tmp_path):
        with pytest.raises(DcmPackCorruptError):
            extract_pack(tmp_path / "ghost.dcmpack")

    def test_multi_item_partial_failure_does_not_abort(self, tmp_path, dirs):
        ul, *_ = dirs
        manifest = _manifest_bytes(items=[
            {"stem": "good", "labeled": False},
            {"stem": "bad",  "labeled": True},   # labeled but assets absent
        ])
        pack = _plain_pack(
            tmp_path / "mixed.dcmpack",
            manifest,
            {
                "items/good/good.dcm": _FAKE_DCM,
                "items/bad/bad.dcm":   _FAKE_DCM,
            },
        )
        result = extract_pack(pack)

        assert "good" in result.imported
        assert len(result.failed)  == 1
        assert result.failed[0][0] == "bad"


# ---------------------------------------------------------------------------
# Round-trip: create_pack → extract_pack
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_plain_unlabeled_round_trip(self, tmp_path, dirs):
        ul, *_ = dirs
        _write(ul / "mri_001.dcm", _FAKE_DCM)
        pack = create_pack(["mri_001"], tmp_path / "trip.dcmpack")

        (ul / "mri_001.dcm").unlink()
        result = extract_pack(pack)

        assert result.imported                   == ["mri_001"]
        assert (ul / "mri_001.dcm").read_bytes() == _FAKE_DCM

    def test_encrypted_labeled_round_trip(self, tmp_path, dirs):
        ul, rs, dt, lb = dirs
        _write(ul / "ct_brain.dcm",  _FAKE_DCM)
        _write(rs / "ct_brain.jpg",  _FAKE_JPG)
        _write(dt / "ct_brain.json", _FAKE_WND)
        _write(lb / "ct_brain.json", _FAKE_LABEL)

        pack = create_pack(["ct_brain"], tmp_path / "secure.dcmpack", password="abc123")

        for d in (ul, rs, dt, lb):
            for f in d.iterdir():
                f.unlink()

        result = extract_pack(pack, password="abc123")

        assert result.imported                    == ["ct_brain"]
        assert (ul / "ct_brain.dcm").read_bytes() == _FAKE_DCM
        assert (rs / "ct_brain.jpg").read_bytes() == _FAKE_JPG
        assert (dt / "ct_brain.json").read_bytes() == _FAKE_WND

        # Label JSON bytes differ from _FAKE_LABEL because imagePath was
        # patched twice: to ./ct_brain.jpg during packing, then to the
        # absolute raster path on extraction. Verify the final state.
        label_data = json.loads((lb / "ct_brain.json").read_bytes())
        assert label_data["imagePath"]  == str(rs / "ct_brain.jpg")
        assert label_data["shapes"]     == []
        assert label_data["imageWidth"] == 512

    def test_image_path_portable_inside_pack(self, tmp_path, dirs):
        ul, rs, dt, lb = dirs
        _write(ul / "scan.dcm",  _FAKE_DCM)
        _write(rs / "scan.jpg",  _FAKE_JPG)
        _write(dt / "scan.json", _FAKE_WND)
        _write(lb / "scan.json", _FAKE_LABEL)

        pack = create_pack(["scan"], tmp_path / "out.dcmpack")

        # The bundled annotation must use a relative path — not a machine path.
        with open_pack(pack) as zf:
            bundled = json.loads(zf.read("items/scan/scan.json"))
        assert bundled["imagePath"] == "./scan.jpg"

    def test_multi_stem_round_trip(self, tmp_path, dirs):
        ul, *_ = dirs
        stems = ["alpha", "beta", "gamma"]
        for s in stems:
            _write(ul / f"{s}.dcm", _FAKE_DCM)

        pack = create_pack(stems, tmp_path / "multi.dcmpack")
        for f in ul.iterdir():
            f.unlink()

        result = extract_pack(pack)

        assert sorted(result.imported) == sorted(stems)
        for s in stems:
            assert (ul / f"{s}.dcm").exists()