import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

import screen_capture
from screen_capture import (
    MACOS_BUNDLE_IDENTIFIER,
    ScreenRect,
    capture_argument,
    capture_filename,
    capture_region_to_file,
    frame_pieces,
    open_screen_capture_settings,
    register_screen_capture_permission,
    request_screen_capture_permission,
    reset_screen_capture_permission,
    screen_capture_permission_granted,
)


class ScreenCaptureTests(unittest.TestCase):
    def test_capture_argument_supports_secondary_display_coordinates(self):
        self.assertEqual(capture_argument((-1920, 120, 800, 600)), "-R-1920,120,800,600")

    def test_screen_rect_keeps_negative_origin(self):
        rect = ScreenRect(-1920, -200, 1920, 1080)
        self.assertEqual((rect.x, rect.y, rect.width, rect.height), (-1920, -200, 1920, 1080))

    def test_frame_is_outside_captured_pixels(self):
        x, y, width, height = 100, 200, 800, 600
        for piece in frame_pieces((x, y, width, height), thickness=2):
            overlap_width = max(0, min(x + width, piece.x + piece.width) - max(x, piece.x))
            overlap_height = max(0, min(y + height, piece.y + piece.height) - max(y, piece.y))
            self.assertEqual(overlap_width * overlap_height, 0)

    def test_readable_capture_filename(self):
        self.assertEqual(capture_filename(datetime(2026, 8, 24, 14, 37), 7), "0824-1437-007.png")

    def test_windows_capture_uses_virtual_desktop_bbox(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "capture.png"
            with (
                patch.object(screen_capture.sys, "platform", "win32"),
                patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (800, 600))) as grab,
            ):
                self.assertTrue(capture_region_to_file(destination, (-1920, 120, 800, 600)))
            grab.assert_called_once_with(bbox=(-1920, 120, -1120, 720), all_screens=True)

    def test_non_macos_permission_is_already_available(self):
        with patch.object(screen_capture.sys, "platform", "win32"):
            self.assertTrue(screen_capture_permission_granted())

    def test_macos_permission_uses_core_graphics_preflight_and_request(self):
        with (
            patch.object(screen_capture.sys, "platform", "darwin"),
            patch.object(screen_capture, "_core_graphics_permission_call", side_effect=[False, True]) as call,
        ):
            self.assertFalse(screen_capture_permission_granted())
            self.assertTrue(request_screen_capture_permission())
        self.assertEqual(
            [entry.args[0] for entry in call.call_args_list],
            ["CGPreflightScreenCaptureAccess", "CGRequestScreenCaptureAccess"],
        )

    def test_reset_targets_only_this_apps_screen_capture_record(self):
        completed = screen_capture.subprocess.CompletedProcess([], 0, stdout="reset", stderr="")
        with (
            patch.object(screen_capture.sys, "platform", "darwin"),
            patch.object(screen_capture.subprocess, "run", return_value=completed) as run,
        ):
            succeeded, _detail = reset_screen_capture_permission()
        self.assertTrue(succeeded)
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            ["/usr/bin/tccutil", "reset", "ScreenCapture", MACOS_BUNDLE_IDENTIFIER],
        )

    def test_registration_uses_real_macos_screenshot_path(self):
        completed = screen_capture.subprocess.CompletedProcess([], 1)
        with (
            patch.object(screen_capture.sys, "platform", "darwin"),
            patch.object(screen_capture.subprocess, "run", return_value=completed) as run,
            patch.object(screen_capture, "screen_capture_permission_granted", return_value=False),
        ):
            self.assertFalse(register_screen_capture_permission())
        self.assertEqual(run.call_args.args[0][0], "/usr/sbin/screencapture")

    def test_open_macos_permission_settings(self):
        with (
            patch.object(screen_capture.sys, "platform", "darwin"),
            patch.object(screen_capture.subprocess, "Popen") as popen,
        ):
            self.assertTrue(open_screen_capture_settings())
        self.assertEqual(popen.call_args.args[0][0], "/usr/bin/open")


if __name__ == "__main__":
    unittest.main()
