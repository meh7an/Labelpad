"""
tests/test_updater.py
Unit tests for core/updater.py  (M2.1 + M5.1)
Run with:  python -m pytest tests/ -v
"""

import shlex
import sys
import zipfile
from pathlib import Path

import pytest

import core.updater as updater
from core.updater import (
    check_for_update,
    is_newer,
    parse_version,
    select_asset,
    verify_zip,
)
from core.version import __version__

WIN_SUFFIX   = "_portable_win.zip"
ARM_SUFFIX   = "_mac_arm64_update.zip"
INTEL_SUFFIX = "_mac_intel_update.zip"


def make_feed(tag="v9.9.9", assets=None, name="Big Release", body="Notes here."):
    if assets is None:
        assets = [
            {"name": f"Labelpad_{tag}_setup.exe",
             "browser_download_url": "https://x/setup", "size": 11},
            {"name": f"Labelpad_{tag}_portable_win.zip",
             "browser_download_url": "https://x/win", "size": 22},
            {"name": f"Labelpad_{tag}_mac_arm64_update.zip",
             "browser_download_url": "https://x/arm", "size": 33},
            {"name": f"Labelpad_{tag}_mac_intel_update.zip",
             "browser_download_url": "https://x/intel", "size": 44},
        ]
    return {"tag_name": tag, "name": name, "body": body, "assets": assets}


# ---------------------------------------------------------------------------
# Version parsing and comparison
# ---------------------------------------------------------------------------

class TestParseVersion:
    def test_plain_triple(self):
        assert parse_version("1.2.3") == (1, 2, 3, 1, 0)

    def test_v_prefix(self):
        assert parse_version("v10.0.7") == (10, 0, 7, 1, 0)

    def test_beta_suffix(self):
        assert parse_version("1.2.3b4") == (1, 2, 3, 0, 4)

    def test_v_prefixed_beta(self):
        assert parse_version("v1.0.3b3") == (1, 0, 3, 0, 3)

    @pytest.mark.parametrize("bad", ["", "v", "1.2", "abc", "1.2.3.4", "1.2.3-rc1"])
    def test_invalid_returns_none(self, bad):
        assert parse_version(bad) is None


class TestIsNewer:
    def test_newer_patch(self):
        assert is_newer("1.2.4", "1.2.3")

    def test_newer_minor_and_major(self):
        assert is_newer("1.3.0", "1.2.9")
        assert is_newer("2.0.0", "1.9.9")

    def test_equal_is_not_newer(self):
        assert not is_newer("1.2.3", "1.2.3")

    def test_older_is_not_newer(self):
        assert not is_newer("1.2.2", "1.2.3")

    def test_v_prefix_ignored(self):
        assert is_newer("v1.2.4", "1.2.3")

    def test_final_beats_its_own_beta(self):
        assert is_newer("1.2.3", "1.2.3b9")
        assert not is_newer("1.2.3b9", "1.2.3")

    def test_beta_of_next_version_beats_current_final(self):
        assert is_newer("1.2.4b1", "1.2.3")

    def test_beta_ordering(self):
        assert is_newer("1.2.3b2", "1.2.3b1")

    def test_malformed_either_side_is_false(self):
        assert not is_newer("garbage", "1.2.3")
        assert not is_newer("1.2.4", "garbage")


# ---------------------------------------------------------------------------
# Platform context
# ---------------------------------------------------------------------------

class TestPlatformAssetSuffix:
    def test_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        assert updater.platform_asset_suffix() == WIN_SUFFIX

    def test_mac_arm64(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(updater.platform, "machine", lambda: "arm64")
        assert updater.platform_asset_suffix() == ARM_SUFFIX

    def test_mac_x86_64_maps_to_intel(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(updater.platform, "machine", lambda: "x86_64")
        assert updater.platform_asset_suffix() == INTEL_SUFFIX

    def test_unsupported_platform_returns_none(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert updater.platform_asset_suffix() is None


class TestInstallContext:
    def test_install_root_none_when_not_frozen(self):
        assert updater.install_root() is None

    def test_writable_probe_on_tmp_dir(self, tmp_path):
        assert updater.is_root_writable(tmp_path)
        assert not (tmp_path / ".update-probe").exists()


# ---------------------------------------------------------------------------
# Asset selection and feed interpretation
# ---------------------------------------------------------------------------

class TestSelectAsset:
    def test_picks_windows_payload(self):
        asset = select_asset(make_feed()["assets"], WIN_SUFFIX)
        assert asset["browser_download_url"] == "https://x/win"

    def test_picks_each_mac_arch(self):
        assets = make_feed()["assets"]
        assert select_asset(assets, ARM_SUFFIX)["size"] == 33
        assert select_asset(assets, INTEL_SUFFIX)["size"] == 44

    def test_none_when_no_match(self):
        assets = [{"name": "Labelpad_v9.9.9_setup.exe", "browser_download_url": "u"}]
        assert select_asset(assets, WIN_SUFFIX) is None

    def test_none_for_empty_assets_or_suffix(self):
        assert select_asset([], WIN_SUFFIX) is None
        assert select_asset(make_feed()["assets"], None) is None


class TestCheckForUpdate:
    def test_newer_release_returns_info(self):
        info = check_for_update(feed=make_feed(), suffix=WIN_SUFFIX)
        assert info is not None
        assert info.current == __version__
        assert info.latest == "9.9.9"
        assert info.asset_url == "https://x/win"
        assert info.asset_size == 22
        assert info.release_name == "Big Release"
        assert info.release_notes == "Notes here."

    def test_same_version_returns_none(self):
        assert check_for_update(feed=make_feed(tag=f"v{__version__}"), suffix=WIN_SUFFIX) is None

    def test_older_version_returns_none(self):
        assert check_for_update(feed=make_feed(tag="v0.0.1"), suffix=WIN_SUFFIX) is None

    def test_malformed_tag_returns_none(self):
        assert check_for_update(feed=make_feed(tag="nightly"), suffix=WIN_SUFFIX) is None

    def test_missing_platform_asset_returns_none(self):
        feed = make_feed(assets=[
            {"name": "Labelpad_v9.9.9_setup.exe", "browser_download_url": "u", "size": 1},
        ])
        assert check_for_update(feed=feed, suffix=WIN_SUFFIX) is None

    def test_empty_feed_returns_none(self):
        assert check_for_update(feed={}, suffix=WIN_SUFFIX) is None

    def test_missing_download_url_returns_none(self):
        feed = make_feed(assets=[{"name": f"x{WIN_SUFFIX}", "size": 1}])
        assert check_for_update(feed=feed, suffix=WIN_SUFFIX) is None

    def test_release_name_falls_back_to_tag(self):
        feed = make_feed()
        feed["name"] = None
        info = check_for_update(feed=feed, suffix=WIN_SUFFIX)
        assert info.release_name == "v9.9.9"


# ---------------------------------------------------------------------------
# Payload verification and extraction
# ---------------------------------------------------------------------------

def _write_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


class TestVerifyZip:
    def test_valid_zip(self, tmp_path):
        assert verify_zip(_write_zip(tmp_path / "ok.zip", {"a.txt": b"hello"}))

    def test_non_zip_file(self, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"this is not a zip")
        assert not verify_zip(bad)

    def test_truncated_zip(self, tmp_path):
        good = _write_zip(tmp_path / "good.zip", {"a.txt": b"x" * 4096})
        truncated = tmp_path / "cut.zip"
        truncated.write_bytes(good.read_bytes()[:100])
        assert not verify_zip(truncated)

    def test_missing_file(self, tmp_path):
        assert not verify_zip(tmp_path / "absent.zip")


@pytest.mark.skipif(sys.platform == "darwin", reason="darwin uses the ditto branch")
class TestExtractPayload:
    def test_contents_land_at_payload_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "staging_dir", lambda: tmp_path / "staging")
        zip_path = _write_zip(
            tmp_path / "u.zip",
            {"Labelpad.exe": b"exe", "_internal/data.bin": b"d"},
        )
        payload = updater.extract_payload(zip_path)
        assert (payload / "Labelpad.exe").read_bytes() == b"exe"
        assert (payload / "_internal" / "data.bin").read_bytes() == b"d"

    def test_repeated_extraction_resets_payload_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "staging_dir", lambda: tmp_path / "staging")
        updater.extract_payload(_write_zip(tmp_path / "one.zip", {"old.txt": b"1"}))
        payload = updater.extract_payload(_write_zip(tmp_path / "two.zip", {"new.txt": b"2"}))
        assert (payload / "new.txt").exists()
        assert not (payload / "old.txt").exists()

    def test_corrupt_zip_raises_update_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "staging_dir", lambda: tmp_path / "staging")
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"nope")
        with pytest.raises(updater.UpdateError):
            updater.extract_payload(bad)


# ---------------------------------------------------------------------------
# Swap helper scripts
# ---------------------------------------------------------------------------

class TestWindowsHelper:
    def test_embeds_quoted_paths_and_pid(self):
        payload = Path("C:/Temp/pay load")
        root    = Path("C:/Apps/My App")
        script  = updater._windows_helper(payload, root, "Labelpad.exe", 4242)
        assert f"$payload = '{payload}'" in script
        assert f"$root    = '{root}'" in script
        assert "$appPid  = 4242" in script
        assert "Wait-Process" in script

    def test_apostrophes_are_doubled(self):
        root   = Path("C:/Users/o'brien/app")
        script = updater._windows_helper(Path("C:/t/p"), root, "x.exe", 1)
        assert str(root).replace("'", "''") in script

    def test_has_backup_and_rollback(self):
        script = updater._windows_helper(Path("C:/t/p"), Path("C:/a/r"), "x.exe", 1)
        assert '$backup  = "$root.update-backup"' in script
        assert "Move-Item -LiteralPath $root -Destination $backup" in script
        assert "Move-Item -LiteralPath $backup -Destination $root" in script
        assert "Start-Process -FilePath $launch" in script


class TestMacosHelper:
    def test_embeds_quoted_paths_and_pid(self):
        payload = Path("/tmp/pay load/Labelpad.app")
        root    = Path("/Applications/Labelpad.app")
        script  = updater._macos_helper(payload, root, 777)
        assert f"PAYLOAD={shlex.quote(str(payload))}" in script
        assert f"ROOT={shlex.quote(str(root))}" in script
        assert "APP_PID=777" in script

    def test_uses_ditto_and_relaunches(self):
        script = updater._macos_helper(Path("/t/p.app"), Path("/a/r.app"), 1)
        assert 'ditto "$PAYLOAD" "$ROOT"' in script
        assert 'open -n "$ROOT"' in script

    def test_has_backup_and_rollback(self):
        script = updater._macos_helper(Path("/t/p.app"), Path("/a/r.app"), 1)
        assert 'BACKUP="$ROOT.update-backup"' in script
        assert 'mv "$ROOT" "$BACKUP"' in script
        assert 'mv "$BACKUP" "$ROOT"' in script


# ---------------------------------------------------------------------------
# Staging hygiene
# ---------------------------------------------------------------------------

class TestStaging:
    def test_clean_stale_staging_removes_dir(self, tmp_path, monkeypatch):
        staging = tmp_path / "staging"
        monkeypatch.setattr(updater, "staging_dir", lambda: staging)
        staging.mkdir()
        (staging / "leftover.zip").write_bytes(b"x")
        updater.clean_stale_staging()
        assert not staging.exists()

    def test_clean_stale_staging_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "staging_dir", lambda: tmp_path / "absent")
        updater.clean_stale_staging()
        updater.clean_stale_staging()
