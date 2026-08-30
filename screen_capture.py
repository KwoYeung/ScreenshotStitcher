"""Cross-platform display, capture, and persistent-overlay helpers."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


MACOS_BUNDLE_IDENTIFIER = "com.screenshotstitcher.desktop"
MACOS_SCREEN_CAPTURE_SETTINGS = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)


@dataclass(frozen=True, slots=True)
class ScreenRect:
    x: int
    y: int
    width: int
    height: int


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


class _WinRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def configure_process_dpi_awareness() -> None:
    """Keep Tk, monitor coordinates, and captured pixels aligned on Windows."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware.
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _core_graphics_permission_call(function_name: str) -> bool:
    framework = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    function = getattr(framework, function_name)
    function.argtypes = []
    function.restype = ctypes.c_bool
    return bool(function())


def screen_capture_permission_granted() -> bool:
    """Return whether macOS currently recognizes this build as authorized."""
    if sys.platform != "darwin":
        return True
    try:
        return _core_graphics_permission_call("CGPreflightScreenCaptureAccess")
    except (AttributeError, OSError):
        return False


def request_screen_capture_permission() -> bool:
    """Ask macOS to register and authorize the current application build."""
    if sys.platform != "darwin":
        return True
    try:
        return _core_graphics_permission_call("CGRequestScreenCaptureAccess")
    except (AttributeError, OSError):
        return False


def reset_screen_capture_permission() -> tuple[bool, str]:
    """Remove stale ScreenCapture records for this app's bundle identifier."""
    if sys.platform != "darwin":
        return False, "截图权限修复仅适用于 macOS"
    try:
        completed = subprocess.run(
            ["/usr/bin/tccutil", "reset", "ScreenCapture", MACOS_BUNDLE_IDENTIFIER],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return False, str(exc)
    detail = (completed.stderr or completed.stdout).strip()
    if completed.returncode == 0:
        return True, detail
    return False, detail or f"tccutil 退出码：{completed.returncode}"


def open_screen_capture_settings() -> bool:
    """Open the macOS pane where the user must grant the final permission."""
    if sys.platform != "darwin":
        return False
    try:
        subprocess.Popen(
            ["/usr/bin/open", MACOS_SCREEN_CAPTURE_SETTINGS],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return True


def active_displays() -> list[ScreenRect]:
    """Return every active display in the desktop's global coordinates."""
    if sys.platform == "win32":
        result: list[ScreenRect] = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(_WinRect),
            ctypes.c_long,
        )

        def collect(_monitor, _dc, rect_pointer, _data):
            rect = rect_pointer.contents
            result.append(ScreenRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top))
            return 1

        callback = callback_type(collect)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, callback, 0)
        return result
    if sys.platform != "darwin":
        return []
    framework = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    framework.CGGetActiveDisplayList.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    framework.CGGetActiveDisplayList.restype = ctypes.c_int32
    framework.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    framework.CGDisplayBounds.restype = _CGRect
    count = ctypes.c_uint32()
    if framework.CGGetActiveDisplayList(0, None, ctypes.byref(count)) != 0 or count.value == 0:
        return []
    display_ids = (ctypes.c_uint32 * count.value)()
    if framework.CGGetActiveDisplayList(count.value, display_ids, ctypes.byref(count)) != 0:
        return []
    result: list[ScreenRect] = []
    for display_id in display_ids[: count.value]:
        bounds = framework.CGDisplayBounds(display_id)
        result.append(
            ScreenRect(
                round(bounds.origin.x),
                round(bounds.origin.y),
                round(bounds.size.width),
                round(bounds.size.height),
            )
        )
    return result


def capture_argument(region: tuple[int, int, int, int]) -> str:
    x, y, width, height = region
    return f"-R{x},{y},{width},{height}"


def capture_filename(moment: datetime, serial: int) -> str:
    return f"{moment:%m%d-%H%M}-{serial:03d}.png"


def capture_region_to_file(path: str | Path, region: tuple[int, int, int, int]) -> bool:
    """Capture an exact global screen rectangle to PNG on macOS or Windows."""
    destination = str(path)
    if sys.platform == "darwin":
        completed = subprocess.run(
            ["/usr/sbin/screencapture", "-x", capture_argument(region), destination],
            check=False,
        )
        return completed.returncode == 0 and Path(destination).exists()
    if sys.platform == "win32":
        try:
            from PIL import ImageGrab
        except ModuleNotFoundError as exc:
            raise RuntimeError("Windows 截图需要 Pillow，请重新安装 requirements.txt") from exc
        x, y, width, height = region
        image = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
        image.save(destination, "PNG")
        return Path(destination).exists()
    raise RuntimeError("内置截图目前支持 macOS 和 Windows")


def place_windows_window(window_id: int, region: tuple[int, int, int, int]) -> bool:
    """Place a Tk HWND at exact virtual-desktop coordinates, including negatives."""
    if sys.platform != "win32":
        return False
    x, y, width, height = region
    hwnd_topmost = ctypes.c_void_p(-1)
    swp_no_activate = 0x0010
    return bool(
        ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(window_id),
            hwnd_topmost,
            x,
            y,
            width,
            height,
            swp_no_activate,
        )
    )


def frame_pieces(region: tuple[int, int, int, int], thickness: int = 2) -> tuple[ScreenRect, ...]:
    """Create four border strips immediately outside the captured pixels."""
    x, y, width, height = region
    return (
        ScreenRect(x - thickness, y - thickness, width + thickness * 2, thickness),
        ScreenRect(x - thickness, y + height, width + thickness * 2, thickness),
        ScreenRect(x - thickness, y, thickness, height),
        ScreenRect(x + width, y, thickness, height),
    )


def configure_persistent_overlay(window_title: str, ignore_mouse: bool = True) -> int | None:
    """Configure one of this process' NSWindows as a cross-app overlay.

    Tk's ``-topmost`` only guarantees ordering while the Python application is
    active. These native AppKit properties keep the border visible while the
    user drags content in another application. The returned pointer is valid
    only for the lifetime of the Tk window.
    """
    if sys.platform != "darwin":
        return None
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    message_address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    if message_address is None:
        return None

    send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(message_address)
    send_uint = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(message_address)
    send_cstring = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)(message_address)
    send_indexed_id = ctypes.CFUNCTYPE(
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
    )(message_address)
    send_void_bool = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(message_address)
    send_void_long = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(message_address)
    send_void_ulong = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(message_address)
    send_void = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(message_address)
    selector = lambda name: objc.sel_registerName(name.encode("ascii"))

    application = send_id(objc.objc_getClass(b"NSApplication"), selector("sharedApplication"))
    windows = send_id(application, selector("windows"))
    count = send_uint(windows, selector("count"))
    native_window: int | None = None
    for index in range(count):
        candidate = send_indexed_id(windows, selector("objectAtIndex:"), index)
        title_object = send_id(candidate, selector("title"))
        if not title_object:
            continue
        title_bytes = send_cstring(title_object, selector("UTF8String"))
        if title_bytes and title_bytes.decode("utf-8") == window_title:
            native_window = candidate
            break
    if native_window is None:
        return None

    send_void_bool(native_window, selector("setHidesOnDeactivate:"), False)
    send_void_bool(native_window, selector("setCanHide:"), False)
    send_void_bool(native_window, selector("setIgnoresMouseEvents:"), ignore_mouse)
    send_void_bool(native_window, selector("setHasShadow:"), False)
    # NSStatusWindowLevel stays above ordinary windows from other apps without
    # covering system menus. JoinAllSpaces + FullScreenAuxiliary keeps the frame
    # available when the target app occupies another Space/full-screen desktop.
    send_void_long(native_window, selector("setLevel:"), 25)
    send_void_ulong(native_window, selector("setCollectionBehavior:"), 1 | (1 << 8))
    send_void(native_window, selector("orderFrontRegardless"))
    return int(native_window)


def show_native_overlay(native_window: int | None) -> None:
    if sys.platform != "darwin" or native_window is None:
        return
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    if address is not None:
        ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(address)(
            native_window,
            objc.sel_registerName(b"orderFrontRegardless"),
        )
