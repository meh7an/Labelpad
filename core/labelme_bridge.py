"""
core/labelme_bridge.py

Launches labelme as a managed subprocess and monitors it on a plain Python
thread. Qt signals marshal results back to the main thread safely, avoiding
QThread entirely to sidestep macOS teardown crashes.

Platform strategy
-----------------
macOS (frozen)
    A co-bundled 'labelme' executable produced by the second Analysis in
    labeler.spec sits next to Labelpad inside Contents/MacOS/. It is spawned
    as a fully independent process (start_new_session=True) with PyInstaller's
    worker-detection env vars stripped, so macOS registers it as an
    independent foreground app with its own dock tile and keyboard focus.

Windows (frozen)
    labelme is bundled inside the main executable. A second instance of
    Labelpad.exe is spawned with the sentinel --_labelme_subprocess, which
    main.py detects at startup and redirects to labelme's entry point before
    any Qt initialisation occurs in that process.

Development (unfrozen, both platforms)
    labelme is resolved from PATH, or via sys.executable -m labelme.
"""

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from core.dicom_handler import raster_path_for
from core.paths import LABELED_DIR, RASTER_DIR

log = logging.getLogger(__name__)

_SUBPROCESS_SENTINEL = "--_labelme_subprocess"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LabelmeNotFoundError(Exception):
    pass


class RasterNotFoundError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_env_for_subprocess() -> dict:
    """
    Return a sanitised copy of the current environment for the labelme process.

    macOS only: PyInstaller sets _PYI_APPLICATION_HOME_DIR (and legacy
    _MEIPASS2) in the parent process. When a child inherits these, its
    bootloader treats it as a worker subprocess and skips full NSApplication
    initialisation. The result is no dock tile and keyboard events routing to
    the parent's menu bar. Stripping these forces an independent app startup.

    Windows: these variables are not stripped. The sentinel subprocess is the
    same executable and needs them for correct PyInstaller initialisation.
    DYLD_LIBRARY_PATH is preserved on macOS for shared-library resolution.
    """
    env = os.environ.copy()
    if sys.platform == "darwin":
        for key in ("_PYI_APPLICATION_HOME_DIR", "_MEIPASS2", "PYINSTALLER_RESET_ENVIRONMENT"):
            env.pop(key, None)
    return env


def _build_command(jpg_path: Path) -> list:
    LABELED_DIR.mkdir(exist_ok=True)
    labelme_args = [
        str(jpg_path.resolve()),
        "--output", str(LABELED_DIR.resolve()),
        "--nodata",
    ]

    # Allow the caller to override the labelme executable via env var.
    # Useful for development when labelme is installed in a different
    # location or venv than the one running the app.
    # Usage: LABELME_EXECUTABLE=/path/to/labelme python main.py
    env_exe = os.environ.get("LABELME_EXECUTABLE")
    if env_exe:
        log.info("Using LABELME_EXECUTABLE override: %s", env_exe)
        return [env_exe] + labelme_args

    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            bundled = Path(sys.executable).parent / "labelme"
            if not bundled.exists():
                raise LabelmeNotFoundError(
                    f"Co-bundled labelme not found at '{bundled}'.\n"
                    "Rebuild the application bundle with the updated labeler.spec."
                )
            log.info("Frozen macOS: using co-bundled labelme at %s", bundled)
            return [str(bundled)] + labelme_args

        log.info("Frozen Windows: spawning labelme via sentinel in %s", sys.executable)
        return [sys.executable, _SUBPROCESS_SENTINEL] + labelme_args

    # In a venv, pip places scripts next to the Python executable.
    # Check there first — this works regardless of whether the venv is
    # activated or PATH is set up correctly.
    _scripts = Path(sys.executable).parent
    for _candidate in (_scripts / "labelme.exe", _scripts / "labelme"):
        if _candidate.exists():
            log.info("Dev: labelme found at %s", _candidate)
            return [str(_candidate)] + labelme_args

    system_exe = shutil.which("labelme")
    if system_exe:
        log.info("Dev: labelme found on PATH at %s", system_exe)
        return [system_exe] + labelme_args

    import importlib.util
    if importlib.util.find_spec("labelme") is not None:
        log.info("Dev: labelme found in env, using '%s -m labelme'.", sys.executable)
        return [sys.executable, "-m", "labelme"] + labelme_args

    raise LabelmeNotFoundError(
        "labelme could not be found.\nInstall it with:  pip install labelme"
    )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class LabelmeSession(QObject):
    """
    Manages a single labelme subprocess lifecycle.

    Constructed and lives on the Qt main thread. The subprocess is monitored
    on a plain Python daemon thread. Qt signals deliver events back to the
    main thread safely across the thread boundary.
    """

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
        super().__init__()
        self._dcm_path   = dcm_path
        self._on_started = on_started
        self._on_exit    = on_exit
        self._on_error   = on_error
        self._process: Optional[subprocess.Popen] = None
        self._thread:  Optional[threading.Thread] = None

        self._sig_started.connect(self._handle_started)
        self._sig_finished.connect(self._handle_finished)
        self._sig_error.connect(self._handle_error)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        jpg_path = raster_path_for(self._dcm_path, RASTER_DIR)
        if not jpg_path.exists():
            raise RasterNotFoundError(
                f"Raster image not found: {jpg_path}\n"
                "Open the DICOM Viewer and confirm export first."
            )
        command = _build_command(jpg_path)
        self._thread = threading.Thread(
            target=self._run,
            args=(command,),
            daemon=True,
            name=f"labelme-{self._dcm_path.stem}",
        )
        self._thread.start()

    def terminate(self) -> None:
        if self._process and self._process.poll() is None:
            log.info("Terminating labelme PID %d.", self._process.pid)
            self._process.terminate()

    # ------------------------------------------------------------------
    # Worker — runs on the monitor thread
    # ------------------------------------------------------------------

    def _run(self, command: list) -> None:
        log.info("Spawning labelme: %s", " ".join(command))

        if sys.platform == "win32":
            extra = {"creationflags": subprocess.CREATE_NO_WINDOW}
        else:
            extra = {"start_new_session": True}

        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_clean_env_for_subprocess(),
                **extra,
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
    # Handlers — called on the main thread via Qt signal dispatch
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
# Public entry point
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