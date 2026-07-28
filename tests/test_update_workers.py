"""
tests/test_update_workers.py
Unit tests for the update workers in ui/workers.py  (M2.2)
Run with:  python -m pytest tests/ -v
"""

from pathlib import Path

import ui.workers as workers
from core.updater import UpdateCancelledError, UpdateInfo


def _info(**overrides):
    defaults = dict(
        current="1.2.0",
        latest="9.9.9",
        asset_name="Labelpad_v9.9.9_portable_win.zip",
        asset_url="https://x/win",
        asset_size=22,
        release_name="Big Release",
        release_notes="Notes.",
    )
    defaults.update(overrides)
    return UpdateInfo(**defaults)


class TestUpdateCheckWorker:
    def test_emits_update_available_when_newer(self, monkeypatch):
        info = _info()
        monkeypatch.setattr(workers, "check_for_update", lambda: info)
        received, done = [], []
        w = workers.UpdateCheckWorker()
        w.update_available.connect(received.append)
        w.finished.connect(lambda: done.append(True))
        w.run()
        assert received == [info]
        assert done == [True]

    def test_silent_when_up_to_date(self, monkeypatch):
        monkeypatch.setattr(workers, "check_for_update", lambda: None)
        received, done = [], []
        w = workers.UpdateCheckWorker()
        w.update_available.connect(received.append)
        w.finished.connect(lambda: done.append(True))
        w.run()
        assert received == []
        assert done == [True]

    def test_silent_on_exception(self, monkeypatch):
        def boom():
            raise RuntimeError("network exploded")
        monkeypatch.setattr(workers, "check_for_update", boom)
        received, done = [], []
        w = workers.UpdateCheckWorker()
        w.update_available.connect(received.append)
        w.finished.connect(lambda: done.append(True))
        w.run()
        assert received == []
        assert done == [True]


class TestUpdateDownloadWorker:
    def test_finished_with_dest_path(self, tmp_path, monkeypatch):
        dest = tmp_path / "u.zip"

        def fake_download(url, d, progress=None, cancel=None):
            d.write_bytes(b"payload")
            if progress:
                progress(7, 7)

        monkeypatch.setattr(workers, "download_asset", fake_download)
        got, prog = [], []
        w = workers.UpdateDownloadWorker("https://x", dest)
        w.finished.connect(got.append)
        w.progress.connect(lambda a, b: prog.append((a, b)))
        w.run()
        assert got == [dest]
        assert prog == [(7, 7)]
        assert dest.read_bytes() == b"payload"

    def test_cancelled_removes_partial_file(self, tmp_path, monkeypatch):
        dest = tmp_path / "u.zip"

        def fake_download(url, d, progress=None, cancel=None):
            d.write_bytes(b"partial")
            raise UpdateCancelledError("stop")

        monkeypatch.setattr(workers, "download_asset", fake_download)
        cancelled = []
        w = workers.UpdateDownloadWorker("https://x", dest)
        w.cancelled.connect(lambda: cancelled.append(True))
        w.run()
        assert cancelled == [True]
        assert not dest.exists()

    def test_failed_removes_partial_file_and_reports(self, tmp_path, monkeypatch):
        dest = tmp_path / "u.zip"

        def fake_download(url, d, progress=None, cancel=None):
            d.write_bytes(b"partial")
            raise OSError("connection reset")

        monkeypatch.setattr(workers, "download_asset", fake_download)
        failures = []
        w = workers.UpdateDownloadWorker("https://x", dest)
        w.failed.connect(failures.append)
        w.run()
        assert len(failures) == 1
        assert "connection reset" in failures[0]
        assert not dest.exists()

    def test_cancel_sets_event(self):
        w = workers.UpdateDownloadWorker("https://x", Path("nowhere.zip"))
        assert not w._cancel_event.is_set()
        w.cancel()
        assert w._cancel_event.is_set()
