"""Small dependency-free global hotkey service for macOS and Windows."""

from __future__ import annotations

import ctypes
import sys
import threading
import tkinter as tk
from collections.abc import Callable


MAC_HOTKEYS = {
    "Control+Option+S": (1, (1 << 12) | (1 << 11)),
    "Control+Shift+S": (1, (1 << 12) | (1 << 9)),
    "F8": (100, 0),
}

WINDOWS_HOTKEYS = {
    "Ctrl+Alt+S": (0x0002 | 0x0001, 0x53),
    "Ctrl+Shift+S": (0x0002 | 0x0004, 0x53),
    "F8": (0, 0x77),
}


def hotkey_choices() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return tuple(MAC_HOTKEYS)
    if sys.platform == "win32":
        return tuple(WINDOWS_HOTKEYS)
    return ("F8",)


def default_hotkey() -> str:
    if sys.platform == "darwin":
        return "Control+Option+S"
    if sys.platform == "win32":
        return "Ctrl+Alt+S"
    return "F8"


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("event_class", ctypes.c_uint32), ("event_kind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("identifier", ctypes.c_uint32)]


class GlobalHotkey:
    def __init__(self, root: tk.Misc, callback: Callable[[], None]) -> None:
        self.root = root
        self.callback = callback
        self._carbon = None
        self._mac_hotkey = ctypes.c_void_p()
        self._mac_handler = ctypes.c_void_p()
        self._mac_callback = None
        self._win_thread: threading.Thread | None = None
        self._win_thread_id = 0
        self._win_ready: threading.Event | None = None
        self._win_result = False

    def register(self, label: str) -> tuple[bool, str]:
        self.unregister()
        try:
            if sys.platform == "darwin":
                return self._register_mac(label)
            if sys.platform == "win32":
                return self._register_windows(label)
        except (AttributeError, OSError) as exc:
            return False, f"系统无法注册快捷键：{exc}"
        return False, "当前系统暂不支持全局快捷键"

    def _dispatch(self) -> None:
        try:
            # The Windows hotkey loop runs on a worker thread. The callback is
            # expected to enqueue work for the UI thread rather than touching
            # Tk directly.
            self.callback()
        except (RuntimeError, tk.TclError):
            pass

    def _register_mac(self, label: str) -> tuple[bool, str]:
        if label not in MAC_HOTKEYS:
            return False, "未知快捷键"
        carbon = ctypes.CDLL(
            "/System/Library/Frameworks/Carbon.framework/Frameworks/"
            "HIToolbox.framework/HIToolbox"
        )
        self._carbon = carbon
        callback_type = ctypes.CFUNCTYPE(
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        def handler(_next_handler, _event, _user_data):
            self._dispatch()
            return 0

        self._mac_callback = callback_type(handler)
        event = _EventTypeSpec(0x6B657962, 6)  # 'keyb', kEventHotKeyPressed
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p,
            callback_type,
            ctypes.c_uint32,
            ctypes.POINTER(_EventTypeSpec),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.InstallEventHandler.restype = ctypes.c_int32
        target = carbon.GetApplicationEventTarget()
        status = carbon.InstallEventHandler(
            target,
            self._mac_callback,
            1,
            ctypes.byref(event),
            None,
            ctypes.byref(self._mac_handler),
        )
        if status != 0:
            return False, f"无法安装快捷键监听（错误 {status}）"
        carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            _EventHotKeyID,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = ctypes.c_int32
        key_code, modifiers = MAC_HOTKEYS[label]
        status = carbon.RegisterEventHotKey(
            key_code,
            modifiers,
            _EventHotKeyID(0x53544954, 1),  # 'STIT'
            target,
            0,
            ctypes.byref(self._mac_hotkey),
        )
        if status != 0:
            self._remove_mac_handler()
            return False, f"快捷键可能已被占用（错误 {status}）"
        return True, f"全局快捷键：{label}"

    def _register_windows(self, label: str) -> tuple[bool, str]:
        if label not in WINDOWS_HOTKEYS:
            return False, "未知快捷键"
        modifiers, key_code = WINDOWS_HOTKEYS[label]
        self._win_ready = threading.Event()
        self._win_result = False

        def message_loop() -> None:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self._win_thread_id = kernel32.GetCurrentThreadId()
            self._win_result = bool(user32.RegisterHotKey(None, 1, modifiers, key_code))
            self._win_ready.set()
            if not self._win_result:
                return
            message = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312 and message.wParam == 1:  # WM_HOTKEY
                    self._dispatch()
            user32.UnregisterHotKey(None, 1)

        # wintypes is lazily attached on some Python distributions.
        import ctypes.wintypes  # noqa: F401

        self._win_thread = threading.Thread(target=message_loop, daemon=True)
        self._win_thread.start()
        self._win_ready.wait(timeout=2)
        if not self._win_result:
            return False, "快捷键可能已被占用"
        return True, f"全局快捷键：{label}"

    def _remove_mac_handler(self) -> None:
        if self._mac_handler.value and self._carbon is not None:
            self._carbon.RemoveEventHandler(self._mac_handler)
        self._mac_handler = ctypes.c_void_p()

    def unregister(self) -> None:
        if sys.platform == "darwin":
            if self._carbon is not None and self._mac_hotkey.value:
                self._carbon.UnregisterEventHotKey(self._mac_hotkey)
            self._mac_hotkey = ctypes.c_void_p()
            self._remove_mac_handler()
            self._carbon = None
            self._mac_callback = None
        elif sys.platform == "win32" and self._win_thread_id:
            thread = self._win_thread
            try:
                ctypes.windll.user32.PostThreadMessageW(self._win_thread_id, 0x0012, 0, 0)  # WM_QUIT
            except (AttributeError, OSError):
                pass
            if thread is not None and thread.is_alive():
                thread.join(timeout=1)
            self._win_thread_id = 0
            self._win_thread = None
