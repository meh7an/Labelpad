import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

pydicom_datas,  pydicom_bins,  pydicom_hiddens  = collect_all("pydicom")
labelme_datas,  labelme_bins,  labelme_hiddens  = collect_all("labelme")
numpy_datas,    numpy_bins,    numpy_hiddens    = collect_all("numpy")
PIL_datas,      PIL_bins,      PIL_hiddens      = collect_all("PIL")

# Do NOT use collect_all for PyQt5 — PyInstaller's built-in hooks already
# collect all PyQt5 binaries and datas automatically. Calling collect_all
# on top of that causes every Qt5 framework to be registered twice, which
# produces duplicate symlink conflicts during COLLECT on macOS.
# collect_submodules is enough to surface any dynamically-imported modules.
pyqt5_hiddens = collect_submodules("PyQt5")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[
        *pydicom_bins,
        *labelme_bins,
        *numpy_bins,
        *PIL_bins,
    ],
    datas=[
        ("assets/style.qss", "assets"),
        *pydicom_datas,
        *labelme_datas,
        *numpy_datas,
        *PIL_datas,
    ],
    hiddenimports=[
        *pydicom_hiddens,
        *labelme_hiddens,
        *numpy_hiddens,
        *PIL_hiddens,
        *pyqt5_hiddens,
        "pydicom.pixel_data_handlers.pillow_handler",
        "pydicom.pixel_data_handlers.numpy_handler",
        "pydicom.pixel_data_handlers.rle_handler",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "jupyter"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Labelpad",
    debug=False,
    strip=False,
    upx=IS_WIN,          # UPX is unreliable on macOS arm64
    console=False,
    icon="assets/icon.ico" if IS_WIN else "assets/icon.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=IS_WIN,
    upx_exclude=[],
    name="Labelpad",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Labelpad.app",
        icon="assets/icon.icns",
        bundle_identifier="com.labelpad.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "1.0.0",
        },
    )