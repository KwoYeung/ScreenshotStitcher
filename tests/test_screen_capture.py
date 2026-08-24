import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

import screen_capture
from screen_capture import ScreenRect, capture_argument, capture_filename, capture_region_to_file, frame_pieces


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


if __name__ == "__main__":
    unittest.main()
