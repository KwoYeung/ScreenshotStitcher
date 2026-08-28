import unittest
from unittest.mock import patch

import cv2
import numpy as np

from stitcher import StitchOptions, match_pair, match_pair_2d, stitch_images, stitch_mosaic


def make_page(width=620, height=1800):
    rng = np.random.default_rng(7)
    image = np.full((height, width, 3), 245, np.uint8)
    for y in range(30, height, 90):
        color = tuple(int(x) for x in rng.integers(30, 210, 3))
        cv2.rectangle(image, (30, y), (width - 35, y + 48), color, 2)
        cv2.putText(image, f"Section {y // 90:02d} unique content", (48, y + 31), cv2.FONT_HERSHEY_SIMPLEX, .72, color, 2)
        for x in range(80, width - 40, 95):
            cv2.circle(image, (x, y + 65), 7 + (y // 90) % 8, color, -1)
    return image


def shot(page, offset, height=620, header=64):
    nav = np.full((header, page.shape[1], 3), (35, 46, 58), np.uint8)
    cv2.putText(nav, "FIXED NAVIGATION", (24, 42), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
    return np.vstack((nav, page[offset : offset + height - header]))


def make_canvas(width=1250, height=980):
    rng = np.random.default_rng(19)
    image = np.full((height, width, 3), 232, np.uint8)
    for index in range(80):
        x = int(rng.integers(15, width - 100))
        y = int(rng.integers(15, height - 70))
        color = tuple(int(value) for value in rng.integers(20, 220, 3))
        cv2.rectangle(image, (x, y), (x + 60, y + 38), color, -1)
        cv2.putText(image, str(index), (x + 5, y + 27), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 2)
    return image


class StitcherTests(unittest.TestCase):
    def setUp(self):
        self.page = make_page()
        self.options = StitchOptions(min_overlap_px=80, min_confidence=.30)

    def test_pair_with_fixed_header(self):
        first = shot(self.page, 0)
        second = shot(self.page, 390)
        match = match_pair(first, second, options=self.options)
        self.assertTrue(match.succeeded, match.reason)
        self.assertGreater(match.confidence, .30)
        # 64 header + (556 - 390) pixels of repeated page content.
        self.assertAlmostEqual(match.cut_y, 230, delta=5)

    def test_auto_sort_and_stitch(self):
        images = [shot(self.page, 760), shot(self.page, 0), shot(self.page, 390)]
        result = stitch_images(images, auto_sort=True, options=self.options)
        self.assertEqual(result.order, [1, 2, 0])
        self.assertFalse(result.warnings)
        self.assertLess(result.image.shape[0], sum(image.shape[0] for image in images))

    def test_failure_keeps_entire_image(self):
        blank = np.full((620, 620, 3), 255, np.uint8)
        result = stitch_images([shot(self.page, 0), blank], auto_sort=False, options=self.options)
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(result.image.shape[0], 620 + 12 + 620)

    def test_unrestricted_2d_match(self):
        canvas = make_canvas()
        first = canvas[0:500, 0:600]
        second = canvas[260:760, 390:990]
        match = match_pair_2d(first, second, options=self.options)
        self.assertTrue(match.succeeded, match.reason)
        self.assertAlmostEqual(match.offset_x, 390, delta=3)
        self.assertAlmostEqual(match.offset_y, 260, delta=3)

    def test_unrestricted_2d_match_supports_different_sizes(self):
        canvas = make_canvas(1450, 1100)
        first = canvas[0:520, 0:640]
        second = canvas[220:800, 380:1100]
        match = match_pair_2d(first, second, options=self.options)
        self.assertTrue(match.succeeded, match.reason)
        self.assertAlmostEqual(match.offset_x, 380, delta=3)
        self.assertAlmostEqual(match.offset_y, 220, delta=3)

    def test_unrestricted_2d_match_rejects_changed_content_scale(self):
        canvas = make_canvas(1450, 1100)
        first = canvas[0:520, 0:640]
        second_source = canvas[180:760, 330:1030]
        second = cv2.resize(second_source, None, fx=1.15, fy=1.15, interpolation=cv2.INTER_LINEAR)
        match = match_pair_2d(first, second, options=self.options)
        self.assertFalse(match.succeeded)
        self.assertIn("缩放比不一致", match.reason)
        self.assertIn("115%", match.reason)

    def test_invalid_scale_estimate_never_reports_zero_percent(self):
        canvas = make_canvas()
        first = canvas[0:500, 0:600]
        second = canvas[240:740, 360:960]

        def degenerate_affine(points_a, _points_b, **_kwargs):
            matrix = np.zeros((2, 3), dtype=np.float64)
            mask = np.ones((len(points_a), 1), dtype=np.uint8)
            return matrix, mask

        with patch("stitcher.cv2.estimateAffinePartial2D", side_effect=degenerate_affine):
            match = match_pair_2d(first, second, options=self.options)
        self.assertFalse(match.succeeded)
        self.assertIn("比例异常", match.reason)
        self.assertNotIn("0%", match.reason)

    def test_mosaic_supports_serpentine_capture(self):
        canvas = make_canvas()
        origins = [(0, 0), (390, 0), (390, 260), (0, 260)]
        images = [canvas[y : y + 500, x : x + 600] for x, y in origins]
        result = stitch_mosaic(images, options=self.options)
        self.assertFalse(result.warnings)
        self.assertEqual(result.positions[0], (0, 0))
        for actual, expected in zip(result.positions, origins):
            self.assertAlmostEqual(actual[0], expected[0], delta=3)
            self.assertAlmostEqual(actual[1], expected[1], delta=3)
        self.assertAlmostEqual(result.image.shape[1], 990, delta=3)
        self.assertAlmostEqual(result.image.shape[0], 760, delta=3)

    def test_mosaic_supports_different_image_sizes(self):
        canvas = make_canvas(1450, 1100)
        origins_and_sizes = [
            ((0, 0), (640, 520)),
            ((380, 0), (700, 560)),
            ((380, 280), (650, 600)),
            ((0, 280), (600, 550)),
        ]
        images = [
            canvas[y : y + height, x : x + width]
            for ((x, y), (width, height)) in origins_and_sizes
        ]
        result = stitch_mosaic(images, options=self.options)
        self.assertFalse(result.warnings)
        expected_origins = [item[0] for item in origins_and_sizes]
        for actual, expected in zip(result.positions, expected_origins):
            self.assertAlmostEqual(actual[0], expected[0], delta=3)
            self.assertAlmostEqual(actual[1], expected[1], delta=3)
        self.assertAlmostEqual(result.image.shape[1], 1080, delta=3)
        self.assertAlmostEqual(result.image.shape[0], 880, delta=3)

    def test_mosaic_stops_at_first_scale_mismatch(self):
        canvas = make_canvas(1450, 1100)
        first = canvas[0:520, 0:640]
        second = canvas[0:560, 380:1080]
        third_source = canvas[280:880, 380:1030]
        third = cv2.resize(third_source, None, fx=1.12, fy=1.12, interpolation=cv2.INTER_LINEAR)
        with self.assertRaisesRegex(ValueError, "第 2 → 3 张停止.*缩放比不一致"):
            stitch_mosaic([first, second, third], options=self.options)

    def test_mosaic_stops_instead_of_placing_failure_on_right(self):
        canvas = make_canvas()
        first = canvas[0:500, 0:600]
        blank = np.full((430, 710, 3), 255, np.uint8)
        with self.assertRaisesRegex(ValueError, "第 1 → 2 张停止"):
            stitch_mosaic([first, blank], options=self.options)


if __name__ == "__main__":
    unittest.main()
