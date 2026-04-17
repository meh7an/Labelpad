"""
Labelpad
Entry point -- bootstraps folder structure and launches the Qt application.
"""

import sys
import logging
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QMessageBox


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def check_dependencies():
    if getattr(sys, "frozen", False):
        return []
    required = {
        "pydicom": "pydicom",
        "numpy":   "numpy",
        "PIL":     "Pillow",
        "PyQt5":   "PyQt5",
        "labelme": "labelme",
    }
    missing = []
    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return missing


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------

def _apply_dark_titlebar(window) -> None:
    """Force dark title bar on Windows 10/11 via DWM API. No-op on other OS."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(window.winId())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception as exc:
        logging.warning("Could not apply dark title bar: %s", exc)


def _apply_icon(window, icon) -> None:
    """Set window icon. Must be called after window.show() so winId() is valid."""
    window.setWindowIcon(icon)


def _resolve_icon():
    """Return a QIcon from assets/icon.ico, works in both dev and PyInstaller."""
    from PyQt5.QtGui import QIcon
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(".")
    icon_path = base / "assets" / "icon.ico"
    if icon_path.exists():
        return QIcon(str(icon_path))
    return QIcon()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    configure_logging()

    from core.paths import bootstrap, DATA_ROOT
    bootstrap()
    logging.info("Data root: %s", DATA_ROOT)

    app = QApplication(sys.argv)
    app.setApplicationName("Labelpad")
    app.setOrganizationName("Labelpad")

    app_icon = _resolve_icon()
    app.setWindowIcon(app_icon)

    qss_path = Path("assets/style.qss")
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
    else:
        logging.warning("Stylesheet not found at %s -- running unstyled.", qss_path)

    missing = check_dependencies()
    if missing:
        QMessageBox.critical(
            None,
            "Missing Dependencies",
            "The following required packages are not installed:\n\n"
            + "\n".join(f"  - {p}" for p in missing)
            + "\n\nRun:  pip install -r requirements.txt",
        )
        return 1

    from ui.main_window import MainWindow
    from ui.error_dialog import AppDialog, install_exception_hook

    try:
        window = MainWindow()
        window.show()                       # show FIRST so winId() is valid
        _apply_icon(window, app_icon)       # then apply icon
        _apply_dark_titlebar(window)
        install_exception_hook(lambda: window)
        return app.exec_()
    except Exception as exc:
        logging.critical("Fatal error during startup: %s", exc, exc_info=True)
        AppDialog.error(None, "Startup Error", f"The application could not start.\n\n{exc}", exc=exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())