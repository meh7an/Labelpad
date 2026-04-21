"""
Labelpad
Entry point -- bootstraps folder structure and launches the Qt application.
"""

import sys

# ---------------------------------------------------------------------------
# Windows frozen subprocess sentinel
# Must be checked before any other imports so that labelme can initialise
# its own QApplication without conflicting with ours.
# ---------------------------------------------------------------------------

if "--_labelme_subprocess" in sys.argv:
    idx = sys.argv.index("--_labelme_subprocess")
    sys.argv = [sys.argv[0]] + sys.argv[idx + 1:]
    from labelme.__main__ import main as _labelme_main
    _labelme_main()
    sys.exit(0)

# ---------------------------------------------------------------------------
# Normal imports (only reached when not running as labelme subprocess)
# ---------------------------------------------------------------------------

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
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value),
        )
    except Exception as exc:
        logging.warning("Could not apply dark title bar: %s", exc)


def _apply_icon(window, icon) -> None:
    """Set window icon. Must be called after window.show() so winId() is valid."""
    window.setWindowIcon(icon)


def _resolve_icon():
    """Return a QIcon from assets/icon.ico, works in both dev and PyInstaller."""
    from PyQt5.QtGui import QIcon
    base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(".")
    icon_path = base / "assets" / "icon.ico"
    return QIcon(str(icon_path)) if icon_path.exists() else QIcon()


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
    from PyQt5.QtGui import QFontDatabase, QFont, QPalette, QColor
    from PyQt5.QtWidgets import QProxyStyle, QStyle

    # Load Google Sans from bundled font files.
    _fonts_dir = (Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(".")) / "assets" / "fonts"
    for _ttf in _fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(_ttf))

    _font = QFont("Google Sans")
    _font.setPixelSize(13)
    _font.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(_font)

    # Disable Qt's mnemonic (keyboard accelerator) processing globally so that
    # a single '&' in button text is displayed as-is rather than being consumed
    # as a shortcut prefix. Without this, "Confirm & Open" renders as "Confirm Open".
    class _NoMnemonicStyle(QProxyStyle):
        def styleHint(self, hint, option=None, widget=None, returnData=None):
            if hint == QStyle.SH_UnderlineShortcut:
                return 0
            return super().styleHint(hint, option, widget, returnData)

        def drawItemText(self, painter, rect, flags, pal, enabled, text, textRole=QPalette.NoRole):
            # Replace '&&' with placeholder, strip mnemonic '&', restore '&',
            # then collapse any double spaces left behind.
            text = text.replace("&&", "\x00").replace("&", "").replace("\x00", "&").replace("  ", " ")
            super().drawItemText(painter, rect, flags, pal, enabled, text, textRole)

    app.setStyle(_NoMnemonicStyle("Fusion"))

    # Force button/input text colors via palette so Windows Fusion never falls
    # back to the system ButtonText role (which defaults to black on Windows).
    palette = app.palette()
    palette.setColor(QPalette.ButtonText,                    QColor("#D4D8DE"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#3E4A5C"))
    palette.setColor(QPalette.Text,                    QColor("#D4D8DE"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#3E4A5C"))
    palette.setColor(QPalette.Base,                    QColor("#0D1118"))
    palette.setColor(QPalette.WindowText,                    QColor("#D4D8DE"))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#3E4A5C"))
    app.setPalette(palette)

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
        window.show()
        _apply_icon(window, app_icon)
        _apply_dark_titlebar(window)
        install_exception_hook(lambda: window)
        return app.exec_()
    except Exception as exc:
        logging.critical("Fatal error during startup: %s", exc, exc_info=True)
        AppDialog.error(None, "Startup Error", f"The application could not start.\n\n{exc}", exc=exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())