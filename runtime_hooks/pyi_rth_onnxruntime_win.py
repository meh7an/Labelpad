"""
runtime_hooks/pyi_rth_onnxruntime_win.py

PyInstaller runtime hook for onnxruntime on Windows.

Problem
-------
PyInstaller adds PyQt5\Qt5\bin to PATH at startup. Qt5\bin ships its own
MSVCP140.dll (version 14.26). onnxruntime 1.24+ is compiled against
VS2022 (MSVCP140.dll 14.50+). Windows DLL loading is name-based: once a
DLL name is loaded into a process, all subsequent requests return the
already-loaded copy. If Qt's 14.26 copy loads first, onnxruntime's
DllMain crashes with error 1114 (0xc0000005 access violation) when it
calls a function that only exists in 14.50+.

Fix
---
Pre-load the correct VC++ runtime DLLs from onnxruntime/capi/ (where we
placed System32 copies at build time) before any Qt DLL can load the
older PATH version. Once the 14.50 copies are in the process, Windows
returns those for all subsequent requests regardless of PATH order.
"""

import os
import sys

if sys.platform == "win32":
    import ctypes

    _base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    _capi = os.path.join(_base, "onnxruntime", "capi")

    # Register DLL search directories so Windows can resolve
    # onnxruntime's transitive dependencies.
    for _d in (_base, _capi):
        if os.path.isdir(_d):
            try:
                os.add_dll_directory(_d)
            except Exception:
                pass

    # Pre-load the correct VC++ runtime versions from capi/ before Qt5\bin
    # can pollute the process with its older copies. Order matters —
    # vcruntime must be loaded before msvcp.
    _vc_load_order = [
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
    ]
    for _dll in _vc_load_order:
        _path = os.path.join(_capi, _dll)
        if os.path.exists(_path):
            try:
                ctypes.WinDLL(_path)
            except Exception:
                pass