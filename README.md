# Labelpad

A medical-grade desktop application for DICOM image viewing, windowing, and polygon annotation. Built for clinical and research workflows where precise control over image rendering is required before labeling.

---

## Overview

Labelpad bridges the gap between raw DICOM files and annotation tools. It provides a purpose-built viewer with Window Center / Window Width controls, exports a windowed JPEG, and launches [LabelMe](https://github.com/wkentaro/labelme) with the output directory pre-configured. Annotation results are stored separately from source files and can be reviewed directly in the viewer with polygon overlays.

---

## Features

- DICOM file browser with per-file status tracking (`Unlabeled`, `Raster Ready`, `Labeled`)
- Live DICOM viewer with Window Center and Window Width sliders
- Automatic fallback to pixel-range windowing when DICOM tags are absent
- Per-file windowing state persisted across sessions
- One-click JPEG export and LabelMe launch
- Polygon annotation overlay rendered directly on the DICOM viewer
- Batch DICOM import via file dialog
- All user data stored in `Documents/Labelpad/` — separate from the install directory
- Dark medical-grade UI with Windows 11 dark title bar support

---

## Requirements

| Package | Version |
| ------- | ------- |
| Python  | 3.12    |
| pydicom | 2.4.5   |
| numpy   | 2.4.4   |
| Pillow  | 12.2.0  |
| PyQt5   | 5.15.11 |
| labelme | 6.1.0   |
| pytest  | 9.0.3   |

---

## Installation

```bash
# Clone the repository
git clone https://github.com/yourname/labelpad.git
cd labelpad

# Create and activate a virtual environment
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## Usage

```bash
python main.py
```

On first launch, Labelpad creates the following directory structure in your Documents folder:

```
~/Documents/Labelpad/
├── Unlabeled/    ← place source DICOM files here, or use Import
├── Raster/       ← windowed JPEG exports (auto-generated)
├── Data/         ← per-file windowing metadata (auto-generated)
└── Labeled/      ← LabelMe annotation output
```

### Workflow

1. Click **Import DICOMs** (or press `Ctrl+I`) to import `.dcm` / `.dicom` files
2. Select a file from the list — status is shown as `Unlabeled`, `Raster Ready`, or `Labeled`
3. Click **Open in DICOM Viewer**
4. Adjust **Window Center** and **Window Width** sliders until the anatomy is clearly visible
5. Click **Confirm & Open in LabelMe** — settings are saved, a JPEG is exported, and LabelMe opens
6. Annotate in LabelMe and save — the JSON annotation appears in `Labeled/` automatically
7. Re-open the viewer at any time to see polygon overlays rendered on the DICOM image

### Keyboard Shortcuts

| Key       | Action                       |
| --------- | ---------------------------- |
| `Ctrl+I`  | Import DICOM files           |
| `↑` / `↓` | Navigate file list           |
| `Enter`   | Open selected file in viewer |
| `F5`      | Refresh file list            |

---

## Project Structure

```
labelpad/
├── main.py                  # Entry point
├── requirements.txt
├── labeler.spec             # PyInstaller build spec
├── assets/
│   ├── style.qss            # Application stylesheet
│   └── icon.ico             # Application icon
├── core/
│   ├── dicom_handler.py     # DICOM reading, windowing, JPEG export
│   ├── labelme_bridge.py    # LabelMe subprocess management
│   ├── metadata_store.py    # Windowing state persistence
│   └── paths.py             # Centralised directory path resolution
├── ui/
│   ├── main_window.py       # File browser and application shell
│   ├── dicom_viewer.py      # DICOM viewer with slider controls
│   ├── error_dialog.py      # Branded error and warning dialogs
│   └── label_overlay.py     # Polygon annotation renderer
├── installer/
│   └── labelpad.nsi         # NSIS Windows installer script
├── .github/
│   └── workflows/
│       └── build.yml        # GitHub Actions build pipeline
└── tests/
    └── test_dicom_engine.py
```

---

## Running Tests

```bash
pytest tests/ -v
```

Tests use synthetic pixel arrays and do not require real DICOM files.

---

## Building the Windows Installer

Releases are built automatically via GitHub Actions on every version tag push. The pipeline produces two artifacts:

- `Labelpad_v<version>_setup.exe` — standard Windows installer (Start Menu shortcut, Add/Remove Programs entry, uninstaller)
- `Labelpad_v<version>_portable.zip` — standalone portable build, no installation required

To trigger a release:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Both artifacts are attached to the GitHub Release automatically.

---

## License

MIT
