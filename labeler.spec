# labeler.spec
# PyInstaller spec -- produces dist/Labelpad/Labelpad.exe

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

block_cipher = None

# Collect everything from these packages -- data, binaries, and submodules
pydicom_datas,  pydicom_bins,  pydicom_hiddens  = collect_all("pydicom")
labelme_datas,  labelme_bins,  labelme_hiddens  = collect_all("labelme")
numpy_datas,    numpy_bins,    numpy_hiddens    = collect_all("numpy")
PIL_datas,      PIL_bins,      PIL_hiddens      = collect_all("PIL")
pyqt5_datas,    pyqt5_bins,    pyqt5_hiddens    = collect_all("PyQt5")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[
        *pydicom_bins,
        *labelme_bins,
        *numpy_bins,
        *PIL_bins,
        *pyqt5_bins,
    ],
    datas=[
        ("assets/style.qss", "assets"),
        *pydicom_datas,
        *labelme_datas,
        *numpy_datas,
        *PIL_datas,
        *pyqt5_datas,
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
    upx=True,
    console=False,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Labelpad",
)