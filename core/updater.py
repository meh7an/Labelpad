"""
core/updater.py
Silent self-update engine: version comparison, GitHub release feed, asset
selection, payload download and extraction, and detached swap-helper launch.

Update flow
-----------
1. check_for_update() asks the GitHub `releases/latest` feed (drafts and
   prereleases excluded by the API) whether a newer version exists and which
   asset fits this machine. Every network failure degrades to None — the app
   never nags about connectivity.
2. download_asset() streams the payload zip into the staging directory.
3. extract_payload() unpacks it (ditto on macOS to preserve symlinks and the
   ad-hoc code signature; zipfile elsewhere).
4. launch_swap() writes a platform helper script (PowerShell / sh) into the
   staging directory and spawns it detached. The caller must quit
   immediately: the helper waits for the process to exit, renames the
   install root to a backup, moves the payload in, relaunches the app, and
   restores the backup if anything fails.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.request import Request, urlopen

from core.version import __version__

log = logging.getLogger(__name__)

FEED_URL         = "https://api.github.com/repos/meh7an/Labelpad/releases/latest"
RELEASE_PAGE_URL = "https://github.com/meh7an/Labelpad/releases/latest"
_USER_AGENT      = f"Labelpad/{__version__}"
_TIMEOUT_S       = 10
_CHUNK_BYTES     = 65536

# Matches the repository's tag history: X.Y.Z with an optional bN prerelease
# suffix, with or without the leading "v".
_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:b(\d+))?")


class UpdateError(Exception):
    """Base class for update failures the UI should surface."""


class UpdateCancelledError(UpdateError):
    """Raised when the user cancels a download in progress."""


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

def parse_version(text: str) -> Optional[tuple[int, int, int, int, int]]:
    """
    Parse "1.2.3", "v1.2.3", or "1.2.3b4" into a sortable tuple.

    Final releases sort above their own prereleases: the fourth element is 1
    for finals and 0 for betas, so (1, 2, 3, 1, 0) > (1, 2, 3, 0, 9).
    Returns None for anything that does not look like a version.
    """
    m = _VERSION_RE.fullmatch(text.strip())
    if m is None:
        return None
    major, minor, patch, beta = m.groups()
    if beta is None:
        return (int(major), int(minor), int(patch), 1, 0)
    return (int(major), int(minor), int(patch), 0, int(beta))


def is_newer(candidate: str, current: str) -> bool:
    """True when candidate is a valid version strictly newer than current."""
    a = parse_version(candidate)
    b = parse_version(current)
    return a is not None and b is not None and a > b


# ---------------------------------------------------------------------------
# Install context
# ---------------------------------------------------------------------------

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Optional[Path]:
    """
    The directory the swap helper replaces: the PyInstaller onedir folder on
    Windows, the .app bundle on macOS. None when running from source — dev
    runs are never self-updated.
    """
    if not is_frozen():
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        return None
    return exe.parent


def platform_asset_suffix() -> Optional[str]:
    """
    Release-asset filename suffix for this machine. The Windows portable zip
    doubles as the update payload for both installed and portable builds;
    macOS gets a dedicated ditto-zipped .app per arch (CI arch names are
    "arm64" and "intel").
    """
    if sys.platform == "win32":
        return "_portable_win.zip"
    if sys.platform == "darwin":
        arch = "arm64" if platform.machine() == "arm64" else "intel"
        return f"_mac_{arch}_update.zip"
    return None


def is_root_writable(root: Path) -> bool:
    """Probe whether the install root accepts writes (no elevation needed)."""
    probe = root / ".update-probe"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Release feed
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UpdateInfo:
    """Everything the UI needs to offer and perform one update."""
    current:       str
    latest:        str
    asset_name:    str
    asset_url:     str
    asset_size:    int
    release_name:  str
    release_notes: str


def _fetch_feed() -> Optional[dict]:
    try:
        req = Request(FEED_URL, headers={
            "User-Agent": _USER_AGENT,
            "Accept":     "application/vnd.github+json",
        })
        with urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.info("Update check skipped: %s", exc)
        return None


def select_asset(assets: list[dict], suffix: Optional[str]) -> Optional[dict]:
    """First asset whose name ends with the platform suffix, else None."""
    if not suffix:
        return None
    for asset in assets:
        if str(asset.get("name", "")).endswith(suffix):
            return asset
    return None


def check_for_update(
    feed:   Optional[dict] = None,
    suffix: Optional[str]  = None,
) -> Optional[UpdateInfo]:
    """
    Return an UpdateInfo when a newer release with a matching asset exists,
    None otherwise (including on any network or parsing failure).

    feed and suffix are injectable for tests; production callers use the
    defaults (live feed, this machine's suffix).
    """
    if feed is None:
        feed = _fetch_feed()
    if not feed:
        return None

    tag = str(feed.get("tag_name", ""))
    if not is_newer(tag, __version__):
        return None

    asset = select_asset(feed.get("assets") or [], suffix or platform_asset_suffix())
    if asset is None:
        log.info("Release %s has no asset for this platform.", tag)
        return None

    url = str(asset.get("browser_download_url", ""))
    if not url:
        return None

    return UpdateInfo(
        current=__version__,
        latest=tag.lstrip("v"),
        asset_name=str(asset.get("name", "update.zip")),
        asset_url=url,
        asset_size=int(asset.get("size") or 0),
        release_name=str(feed.get("name") or tag),
        release_notes=str(feed.get("body") or ""),
    )


# ---------------------------------------------------------------------------
# Download and staging
# ---------------------------------------------------------------------------

def staging_dir() -> Path:
    return Path(tempfile.gettempdir()) / "labelpad-update"


def clean_stale_staging() -> None:
    """
    Remove leftovers from earlier update attempts. Call once at app start —
    never mid-update, and a helper script still finishing its relaunch keeps
    running even if its file disappears.
    """
    shutil.rmtree(staging_dir(), ignore_errors=True)


def download_asset(
    url:      str,
    dest:     Path,
    progress: Optional[Callable[[int, int], None]] = None,
    cancel:   Optional[threading.Event]            = None,
) -> None:
    """Stream url into dest, reporting (bytes_done, bytes_total) as it goes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(req, timeout=_TIMEOUT_S) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done  = 0
        with open(dest, "wb") as f:
            while chunk := resp.read(_CHUNK_BYTES):
                if cancel is not None and cancel.is_set():
                    raise UpdateCancelledError("Download cancelled.")
                f.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)


def verify_zip(path: Path) -> bool:
    """True when path is a structurally sound zip archive."""
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def extract_payload(zip_path: Path) -> Path:
    """
    Unpack the payload into the staging directory.

    Returns the directory whose contents mirror the install root (Windows —
    the portable zip has the app at its root) or the new .app bundle
    (macOS). ditto is used on macOS because zipfile drops symlinks and
    permission bits, which would break the bundle and its ad-hoc signature.
    """
    dest = staging_dir() / "payload"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)

    if sys.platform == "darwin":
        result = subprocess.run(
            ["ditto", "-x", "-k", str(zip_path), str(dest)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise UpdateError(f"Payload extraction failed: {result.stderr.strip()}")
        apps = [p for p in dest.iterdir() if p.suffix == ".app"]
        if not apps:
            raise UpdateError("The update payload contains no .app bundle.")
        return apps[0]

    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError(f"Payload extraction failed: {exc}") from exc
    return dest


# ---------------------------------------------------------------------------
# Swap helpers
# ---------------------------------------------------------------------------

def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _windows_helper(payload: Path, root: Path, exe_name: str, pid: int) -> str:
    """
    PowerShell script: wait for the app to exit, rename the install root to a
    backup (same-volume, effectively atomic), copy the payload in (copy, not
    move — staging may live on another volume), relaunch, drop the backup.
    Any failure restores the backup so the old install keeps working.
    """
    return f"""$ErrorActionPreference = 'Stop'
$appPid  = {pid}
$payload = {_ps_quote(str(payload))}
$root    = {_ps_quote(str(root))}
$exe     = {_ps_quote(exe_name)}
$backup  = "$root.update-backup"
if ($appPid -gt 0) {{
  Wait-Process -Id $appPid -ErrorAction SilentlyContinue
}}
Start-Sleep -Milliseconds 500
try {{
  if (Test-Path -LiteralPath $backup) {{ Remove-Item -LiteralPath $backup -Recurse -Force }}
  Move-Item -LiteralPath $root -Destination $backup -Force
  New-Item -ItemType Directory -Path $root | Out-Null
  Copy-Item -Path (Join-Path $payload '*') -Destination $root -Recurse -Force
  Remove-Item -LiteralPath $backup -Recurse -Force
}} catch {{
  if ((Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath (Join-Path $root $exe))) {{
    if (Test-Path -LiteralPath $root) {{ Remove-Item -LiteralPath $root -Recurse -Force }}
    Move-Item -LiteralPath $backup -Destination $root -Force
  }}
}}
$launch = Join-Path $root $exe
if (Test-Path -LiteralPath $launch) {{ Start-Process -FilePath $launch }}
Remove-Item -LiteralPath $payload -Recurse -Force -ErrorAction SilentlyContinue
"""


def _macos_helper(payload_app: Path, root_app: Path, pid: int) -> str:
    """
    Shell script: wait for exit, mv the .app aside, ditto the new bundle into
    place (preserves signature, symlinks, permissions), relaunch via open.
    Failure restores the backup.
    """
    payload_q = shlex.quote(str(payload_app))
    root_q    = shlex.quote(str(root_app))
    return f"""#!/bin/sh
APP_PID={pid}
PAYLOAD={payload_q}
ROOT={root_q}
BACKUP="$ROOT.update-backup"
if [ "$APP_PID" -gt 0 ] 2>/dev/null; then
  while kill -0 "$APP_PID" 2>/dev/null; do sleep 0.5; done
fi
sleep 0.5
rm -rf "$BACKUP"
if mv "$ROOT" "$BACKUP"; then
  if ditto "$PAYLOAD" "$ROOT"; then
    rm -rf "$BACKUP"
  else
    rm -rf "$ROOT"
    mv "$BACKUP" "$ROOT"
  fi
fi
rm -rf "$PAYLOAD"
if [ -d "$ROOT" ]; then
  open -n "$ROOT" || true
fi
"""


def launch_swap(payload: Path, root: Path, pid: Optional[int] = None) -> None:
    """
    Write the platform helper into staging and spawn it fully detached. The
    caller must quit the application immediately afterwards — the helper
    waits on our PID before touching anything.
    """
    pid = os.getpid() if pid is None else pid
    staging = staging_dir()
    staging.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        script = staging / "apply-update.ps1"
        script.write_text(
            _windows_helper(payload, root, Path(sys.executable).name, pid),
            encoding="utf-8-sig",
        )
        detached = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", str(script)],
            creationflags=detached, close_fds=True, cwd=str(staging),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    elif sys.platform == "darwin":
        script = staging / "apply-update.sh"
        script.write_text(_macos_helper(payload, root, pid), encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(
            ["/bin/sh", str(script)],
            start_new_session=True, close_fds=True, cwd=str(staging),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        raise UpdateError(f"Self-update is not supported on {sys.platform}.")

    log.info("Swap helper launched for %s.", root)


# ---------------------------------------------------------------------------
# CLI hook (--apply-update)
# ---------------------------------------------------------------------------

def apply_update_from_zip(zip_path: Optional[Path], root: Optional[Path] = None) -> int:
    """
    Verify, extract, and hand a payload zip to the swap helper — the last leg
    of the update flow, exposed for end-to-end testing via the hidden
    `--apply-update <zip> [install_root]` flag. Returns a process exit code.
    """
    if zip_path is None or not zip_path.exists():
        print("--apply-update: payload zip not found.", file=sys.stderr)
        return 2
    root = root or install_root()
    if root is None:
        print(
            "--apply-update: no install root — pass one explicitly when "
            "running from source.",
            file=sys.stderr,
        )
        return 2
    if not verify_zip(zip_path):
        print(f"--apply-update: {zip_path} is not a valid zip.", file=sys.stderr)
        return 2
    try:
        payload = extract_payload(zip_path)
        launch_swap(payload, root)
    except UpdateError as exc:
        print(f"--apply-update: {exc}", file=sys.stderr)
        return 1
    return 0
