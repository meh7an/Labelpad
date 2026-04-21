"""
runtime_hooks/pyi_rth_labelme_macos.py

PyInstaller runtime hook executed at the very start of the co-bundled
labelme process on macOS, before any frozen Python code runs.

Problem
-------
The labelme executable lives inside Labelpad.app/Contents/MacOS/, so macOS
associates it with the parent bundle's identity. Without intervention, the
OS never registers it as an independent foreground application, which means:
  - No dock tile appears for labelme
  - All keyboard events remain routed to Labelpad's menu bar

Fix
---
Call into the Objective-C runtime to obtain NSApplication.sharedApplication
and set its activation policy to NSApplicationActivationPolicyRegular (0)
before Qt creates its own QApplication. Qt calls sharedApplication
internally during QApplication.__init__ and will find the already-existing
instance with the correct policy already set, so this is safe to do here.
"""

import sys

if sys.platform == "darwin":
    try:
        import ctypes
        import ctypes.util

        # AppKit must be loaded before NSApplication is accessible via the
        # ObjC runtime. The framework is always present on macOS.
        ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/AppKit.framework/AppKit"
        )
        _objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))

        _objc.objc_getClass.restype    = ctypes.c_void_p
        _objc.objc_getClass.argtypes   = [ctypes.c_char_p]
        _objc.sel_registerName.restype  = ctypes.c_void_p
        _objc.sel_registerName.argtypes = [ctypes.c_char_p]

        # Obtain (or create) the shared NSApplication instance.
        _objc.objc_msgSend.restype  = ctypes.c_void_p
        _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _app = _objc.objc_msgSend(
            _objc.objc_getClass(b"NSApplication"),
            _objc.sel_registerName(b"sharedApplication"),
        )

        # setActivationPolicy_(NSApplicationActivationPolicyRegular = 0)
        # Promotes the process to a regular foreground app with a dock tile.
        _objc.objc_msgSend.restype  = ctypes.c_bool
        _objc.objc_msgSend.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
        ]
        _objc.objc_msgSend(
            _app,
            _objc.sel_registerName(b"setActivationPolicy:"),
            0,  # NSApplicationActivationPolicyRegular
        )

        # activateIgnoringOtherApps_(True)
        # Brings this process to the foreground so it receives keyboard focus.
        _objc.objc_msgSend.restype  = ctypes.c_void_p
        _objc.objc_msgSend.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool,
        ]
        _objc.objc_msgSend(
            _app,
            _objc.sel_registerName(b"activateIgnoringOtherApps:"),
            True,
        )

    except Exception:
        pass  # never crash the app over an activation hint