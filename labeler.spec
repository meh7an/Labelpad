import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# ---------------------------------------------------------------------------
# Package collection
# ---------------------------------------------------------------------------

pydicom_datas,  pydicom_bins,  pydicom_hiddens  = collect_all("pydicom")
labelme_datas,  labelme_bins,  labelme_hiddens  = collect_all("labelme")
osam_datas,     osam_bins,     osam_hiddens     = collect_all("osam")
onnx_datas,     _,             onnx_hiddens     = collect_all("onnxruntime")
numpy_datas,    numpy_bins,    numpy_hiddens    = collect_all("numpy")
PIL_datas,      PIL_bins,      PIL_hiddens      = collect_all("PIL")

# PyQt5 — platform-split:
#   Windows : collect_all is required for Qt5 binaries.
#   macOS   : collect_all must NOT be used — PyInstaller's built-in hooks
#             already handle Qt frameworks; running collect_all on top
#             registers every framework twice, causing duplicate symlink
#             conflicts during COLLECT.
if IS_WIN:
    pyqt5_datas, pyqt5_bins, pyqt5_hiddens = collect_all("PyQt5")
else:
    pyqt5_datas, pyqt5_bins = [], []
    pyqt5_hiddens = collect_submodules("PyQt5")

# ---------------------------------------------------------------------------
# Windows: VC++ runtime DLLs pinned into onnxruntime/capi/
#
# collect_all("PyQt5") adds PyQt5\Qt5\bin first in PATH, which ships its own
# older VC++ runtime DLLs (msvcp140.dll 14.26). onnxruntime.dll is compiled
# against VS2022 (14.50+) and fails DllMain when it gets Qt's older version.
#
# By collecting the current System32 copies directly into onnxruntime/capi/,
# LOAD_WITH_ALTERED_SEARCH_PATH finds the correct version in the DLL's own
# directory before searching PATH, regardless of PATH order.
# ---------------------------------------------------------------------------

if IS_WIN:
    _sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    _vc_dlls = [
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ]
    onnx_vc_bins = [
        (str(_sys32 / dll), "onnxruntime/capi")
        for dll in _vc_dlls
        if (_sys32 / dll).exists()
    ]
else:
    onnx_vc_bins = []

# ---------------------------------------------------------------------------
# macOS only: resolve labelme's __main__ entry point for the co-bundled exe.
# On macOS labelme must be a separate process so the OS registers it as an
# independent foreground application (dock tile + keyboard focus).
# On Windows labelme runs inside the main process via a subprocess sentinel.
# ---------------------------------------------------------------------------

if IS_MAC:
    import labelme as _lm
    _labelme_main = str(Path(_lm.__file__).parent / "__main__.py")

# ---------------------------------------------------------------------------
# Shared dependency lists (reused by both Analysis objects on macOS)
# ---------------------------------------------------------------------------

_shared_binaries = [
    *labelme_bins,
    *osam_bins,
    *numpy_bins,
    *PIL_bins,
]

_shared_datas = [
    *labelme_datas,
    *osam_datas,
    *onnx_datas,
    *numpy_datas,
    *PIL_datas,
]

_shared_hiddenimports = [
    *labelme_hiddens,
    *osam_hiddens,
    *onnx_hiddens,
    *numpy_hiddens,
    *PIL_hiddens,
    *pyqt5_hiddens,
]

_shared_excludes = [
    "tkinter",
    "matplotlib",
    "scipy",
    "IPython",
    "jupyter",
    "onnxruntime.quantization",
]

# ---------------------------------------------------------------------------
# Analysis #1 — Main application
# ---------------------------------------------------------------------------

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[
        *pydicom_bins,
        *pyqt5_bins,
        *onnx_vc_bins,      # correct VC++ runtime versions pinned into onnxruntime/capi/
        *_shared_binaries,
    ],
    datas=[
        ("assets/style.qss", "assets"),
        *pydicom_datas,
        *pyqt5_datas,
        *_shared_datas,
    ],
    hiddenimports=[
        *pydicom_hiddens,
        *_shared_hiddenimports,
        "pydicom.pixel_data_handlers.pillow_handler",
        "pydicom.pixel_data_handlers.numpy_handler",
        "pydicom.pixel_data_handlers.rle_handler",
    ],
    hookspath=[],
    runtime_hooks=(
        ["runtime_hooks/pyi_rth_onnxruntime_win.py"] if IS_WIN else []
    ),
    excludes=_shared_excludes,
    cipher=block_cipher,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# Analysis #2 — macOS only: co-bundled labelme executable
# ---------------------------------------------------------------------------

if IS_MAC:
    labelme_a = Analysis(
        [_labelme_main],
        pathex=["."],
        binaries=_shared_binaries,
        datas=_shared_datas,
        hiddenimports=_shared_hiddenimports,
        hookspath=[],
        runtime_hooks=["runtime_hooks/pyi_rth_labelme_macos.py"],
        excludes=_shared_excludes,
        cipher=block_cipher,
        noarchive=False,
    )

# ---------------------------------------------------------------------------
# PYZ archives
# ---------------------------------------------------------------------------

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if IS_MAC:
    labelme_pyz = PYZ(labelme_a.pure, labelme_a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------------
# EXE — Main application
# ---------------------------------------------------------------------------

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Labelpad",
    debug=False,
    strip=False,
    upx=IS_WIN,
    console=False,
    icon="assets/icon.ico" if IS_WIN else "assets/icon.icns",
)

# ---------------------------------------------------------------------------
# EXE — macOS only: co-bundled labelme helper
# console=True because labelme writes status to stdout/stderr.
# ---------------------------------------------------------------------------

if IS_MAC:
    labelme_exe = EXE(
        labelme_pyz,
        labelme_a.scripts,
        [],
        exclude_binaries=True,
        name="labelme",
        debug=False,
        strip=False,
        upx=False,
        console=True,
        icon="assets/icon.icns",
    )

# ---------------------------------------------------------------------------
# COLLECT
# ---------------------------------------------------------------------------

if IS_MAC:
    coll = COLLECT(
        exe,
        labelme_exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        labelme_a.binaries,
        labelme_a.zipfiles,
        labelme_a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="Labelpad",
    )
else:
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
            "NSHighResolutionCapable":    True,
            "CFBundleShortVersionString": "1.0.0",

            # ---------------------------------------------------------------------------
            # File association: declare that Labelpad opens .dcmpack files.
            # LSHandlerRank "Owner" gives Labelpad priority over any other
            # app that might claim the same extension.
            # ---------------------------------------------------------------------------
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName":       "Labelpad DICOM Pack",
                    "CFBundleTypeRole":       "Editor",
                    "CFBundleTypeExtensions": ["dcmpack"],
                    "CFBundleTypeIconFile":   "icon.icns",
                    "LSItemContentTypes":     ["com.labelpad.dcmpack"],
                    "LSHandlerRank":          "Owner",
                }
            ],

            # ---------------------------------------------------------------------------
            # UTI export: register com.labelpad.dcmpack as a known type so
            # Spotlight, Quick Look, and other apps can identify the format.
            # Conforms to public.data (arbitrary bytes) and public.archive
            # (container of other files — accurate for a ZIP-based format).
            # ---------------------------------------------------------------------------
            "UTExportedTypeDeclarations": [
                {
                    "UTTypeIdentifier":  "com.labelpad.dcmpack",
                    "UTTypeDescription": "Labelpad DICOM Pack",
                    "UTTypeConformsTo":  ["public.data", "public.archive"],
                    "UTTypeTagSpecification": {
                        "public.filename-extension": ["dcmpack"],
                    },
                }
            ],
        },
    )