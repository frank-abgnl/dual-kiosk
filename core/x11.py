#!/usr/bin/env python3
"""
Central X11 / XInput 2 / XFixes ctypes wrapper and error handling.
"""
import ctypes
import ctypes.util
import logging
import threading
from typing import Optional, Tuple

logger = logging.getLogger("xinput_seating.x11")


# ---------------------------------------------------------------------------
# XInput 2 Ctypes Structures
# ---------------------------------------------------------------------------
class _XIEventMask(ctypes.Structure):
    _fields_ = [
        ("deviceid", ctypes.c_int),
        ("mask_len", ctypes.c_int),
        ("mask", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _XGenericEventCookie(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("extension", ctypes.c_int),
        ("evtype", ctypes.c_int),
        ("cookie", ctypes.c_uint),
        ("data", ctypes.c_void_p),
    ]


class _XIRawEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("extension", ctypes.c_int),
        ("evtype", ctypes.c_int),
        ("time", ctypes.c_ulong),
        ("deviceid", ctypes.c_int),
        ("sourceid", ctypes.c_int),
        ("detail", ctypes.c_int),  # Keycode
        ("flags", ctypes.c_int),
    ]


class _XIModifierState(ctypes.Structure):
    _fields_ = [
        ("base", ctypes.c_int),
        ("latched", ctypes.c_int),
        ("locked", ctypes.c_int),
        ("effective", ctypes.c_int),
    ]


class _XIGroupState(ctypes.Structure):
    _fields_ = [
        ("base", ctypes.c_int),
        ("latched", ctypes.c_int),
        ("locked", ctypes.c_int),
        ("effective", ctypes.c_int),
    ]


class _XIButtonState(ctypes.Structure):
    _fields_ = [
        ("mask_len", ctypes.c_int),
        ("mask", ctypes.c_void_p),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_int),
        ("xcookie", _XGenericEventCookie),
        ("pad", ctypes.c_long * 24),
    ]


_XERROR_HANDLER_PROTO = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


def _x_error_handler(display_ptr, error_event_ptr):
    logger.debug(f"X11 error caught: display={display_ptr}, event={error_event_ptr}")
    return 0


# Module-level reference so it is never garbage collected
_X_ERROR_HANDLER_REF = _XERROR_HANDLER_PROTO(_x_error_handler)
_error_handler_installed = False
_init_lock = threading.Lock()
_loaded_libs: Optional[Tuple[ctypes.CDLL, ctypes.CDLL, ctypes.CDLL]] = None


def load_x11() -> Tuple[ctypes.CDLL, ctypes.CDLL, ctypes.CDLL]:
    """
    Loads libX11, libXi, and libXfixes with proper signatures.
    Installs a non-fatal XSetErrorHandler once per process.
    """
    global _loaded_libs, _error_handler_installed
    with _init_lock:
        if _loaded_libs is not None:
            return _loaded_libs

        x11_path = ctypes.util.find_library("X11") or "libX11.so.6"
        xi_path = ctypes.util.find_library("Xi") or "libXi.so.6"
        xfixes_path = ctypes.util.find_library("Xfixes") or "libXfixes.so.3"

        x11 = ctypes.CDLL(x11_path)
        xi = ctypes.CDLL(xi_path)
        xfixes = ctypes.CDLL(xfixes_path)

        # Configure X11
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XFlush.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XSync.restype = ctypes.c_int
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XConnectionNumber.restype = ctypes.c_int
        x11.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x11.XPending.restype = ctypes.c_int
        x11.XPending.argtypes = [ctypes.c_void_p]
        x11.XNextEvent.restype = ctypes.c_int
        x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XEvent)]
        x11.XGetEventData.restype = ctypes.c_int
        x11.XGetEventData.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XGenericEventCookie)]
        x11.XFreeEventData.restype = ctypes.c_void_p
        x11.XFreeEventData.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XGenericEventCookie)]
        x11.XQueryExtension.restype = ctypes.c_int
        x11.XQueryExtension.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        x11.XStringToKeysym.restype = ctypes.c_ulong
        x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeycodeToKeysym.restype = ctypes.c_ulong
        x11.XKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int]
        x11.XFree.restype = ctypes.c_int
        x11.XFree.argtypes = [ctypes.c_void_p]

        x11.XSetErrorHandler.restype = ctypes.c_void_p
        x11.XSetErrorHandler.argtypes = [_XERROR_HANDLER_PROTO]

        # Configure Xi
        xi.XISelectEvents.restype = ctypes.c_int
        xi.XISelectEvents.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_XIEventMask),
            ctypes.c_int,
        ]
        xi.XIQueryPointer.restype = ctypes.c_int
        xi.XIQueryPointer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(_XIButtonState),
            ctypes.POINTER(_XIModifierState),
            ctypes.POINTER(_XIGroupState),
        ]
        xi.XIWarpPointer.restype = ctypes.c_int
        xi.XIWarpPointer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_double,
            ctypes.c_double,
        ]
        xi.XISetClientPointer.restype = ctypes.c_int
        xi.XISetClientPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
        xi.XISetFocus.restype = ctypes.c_int
        xi.XISetFocus.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong]

        # Configure XFixes
        xfixes.XFixesQueryVersion.restype = ctypes.c_int
        xfixes.XFixesQueryVersion.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        xfixes.XFixesCreatePointerBarrier.restype = ctypes.c_ulong
        xfixes.XFixesCreatePointerBarrier.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        xfixes.XFixesDestroyPointerBarrier.restype = ctypes.c_int
        xfixes.XFixesDestroyPointerBarrier.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

        if not _error_handler_installed:
            x11.XSetErrorHandler(_X_ERROR_HANDLER_REF)
            _error_handler_installed = True

        _loaded_libs = (x11, xi, xfixes)
        return _loaded_libs


def open_display(name: Optional[str] = None):
    """Opens and returns an X11 Display pointer. Each thread should open its own."""
    x11, _, _ = load_x11()
    c_name = name.encode("utf-8") if name else None
    return x11.XOpenDisplay(c_name)
