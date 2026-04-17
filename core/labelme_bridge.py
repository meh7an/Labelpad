"""
core/labelme_bridge.py
Launches labelme as a managed subprocess, monitoring it on a plain
Python thread. Avoids QThread entirely to sidestep macOS teardown
crashes. UI callbacks are marshalled back to the Qt main thread via
a QTimer single-shot poll.
"""

import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from core.dicom_handler import raster_path_for
from core.paths import RASTER_DIR, LABELED_DIR

log = logging.getLogger(__name__)

_RASTER_DIR  = RASTER_DIR
_LABELED_DIR = LABELED_DIR


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LabelmeNotFoundError(Exception):
    pass

class RasterNotFoundError(Exception):
    pass


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def find_labelme_executable() -> Optional[str]:
    if shutil.which("labelme"):
        log.info("labelme found on PATH: %s", shutil.which("labelme"))
        return "labelme"
    try:
        import labelme  # noqa: F401
        log.info("labelme importable -- using '%s -m labelme'.", sys.executable)
        return None
    except ImportError:
        pass
    raise LabelmeNotFoundError(
        "labelme could not be found.\nInstall it with:  pip install labelme"
    )


def _build_command(jpg_path: Path) -> list:
    _LABELED_DIR.mkdir(exist_ok=True)
    exe = find_labelme_executable()
    base = [exe] if exe else [sys.executable, "-m", "labelme"]
    return base + [
        str(jpg_path.resolve()),
        "--output", str(_LABELED_DIR.resolve()),
        "--nodata",
    ]


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class LabelmeSession(QObject):
    """
    Runs labelme in a subprocess monitored by a plain Python thread.
    Results are delivered back to the Qt main thread via Qt signals,
    which PyQt5 dispatches safely across threads when the receiver
    lives on the main thread.
    """

    # Internal signals -- emitted from the Python thread, received on main thread
    _sig_started  = pyqtSignal(int)   # pid
    _sig_finished = pyqtSignal(int)   # return code
    _sig_error    = pyqtSignal(str)   # error message

    def __init__(
        self,
        dcm_path:   Path,
        on_started: Optional[Callable[[int], None]] = None,
        on_exit:    Optional[Callable[[], None]]    = None,
        on_error:   Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__()            # lives on the main thread
        self._dcm_path   = dcm_path
        self._on_started = on_started
        self._on_exit    = on_exit
        self._on_error   = on_error
        self._process: Optional[subprocess.Popen] = None
        self._thread:  Optional[threading.Thread] = None

        # Signals are received on the main thread (this object's thread)
        self._sig_started.connect(self._handle_started)
        self._sig_finished.connect(self._handle_finished)
        self._sig_error.connect(self._handle_error)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def start(self) -> None:
        jpg_path = raster_path_for(self._dcm_path, _RASTER_DIR)
        if not jpg_path.exists():
            raise RasterNotFoundError(
                f"Raster image not found: {jpg_path}\n"
                "Open the DICOM Viewer and confirm export first."
            )

        command = _build_command(jpg_path)

        self._thread = threading.Thread(
            target=self._run,
            args=(command,),
            daemon=True,   # dies automatically if the app exits
            name=f"labelme-{self._dcm_path.stem}",
        )
        self._thread.start()

    def terminate(self) -> None:
        if self._process and self._process.poll() is None:
            log.info("Terminating labelme PID %d.", self._process.pid)
            self._process.terminate()

    # ------------------------------------------------------------------
    # Worker (runs on the Python thread)
    # ------------------------------------------------------------------

    def _run(self, command: list) -> None:
        log.info("Spawning labelme: %s", " ".join(command))
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._sig_started.emit(self._process.pid)
            log.info("labelme PID %d started.", self._process.pid)

            _out, _err = self._process.communicate()
            rc = self._process.returncode
            log.info("labelme PID %d exited with code %d.", self._process.pid, rc)

            self._sig_finished.emit(rc)

        except FileNotFoundError as exc:
            self._sig_error.emit(f"Executable not found: {exc}")
        except OSError as exc:
            self._sig_error.emit(f"OS error launching labelme: {exc}")

    # ------------------------------------------------------------------
    # Handlers (called on the main thread via Qt signal dispatch)
    # ------------------------------------------------------------------

    def _handle_started(self, pid: int) -> None:
        log.info("labelme session started (PID %d) for '%s'.", pid, self._dcm_path.name)
        if self._on_started:
            self._on_started(pid)

    def _handle_finished(self, _rc: int) -> None:
        log.info("labelme session finished for '%s'.", self._dcm_path.name)
        if self._on_exit:
            self._on_exit()

    def _handle_error(self, message: str) -> None:
        log.error("labelme session error for '%s': %s", self._dcm_path.name, message)
        if self._on_error:
            self._on_error(message)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def launch_labelme(
    dcm_path:   Path,
    on_started: Optional[Callable[[int], None]] = None,
    on_exit:    Optional[Callable[[], None]]    = None,
    on_error:   Optional[Callable[[str], None]] = None,
) -> LabelmeSession:
    session = LabelmeSession(
        dcm_path=dcm_path,
        on_started=on_started,
        on_exit=on_exit,
        on_error=on_error,
    )
    session.start()
    return session