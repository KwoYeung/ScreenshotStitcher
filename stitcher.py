"""Core algorithms for vertical screenshots and freely panned 2D canvases."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np


@dataclass(slots=True)
class StitchOptions:
    min_overlap_px: int = 60
    min_confidence: float = 0.36
    max_overlap_ratio: float = 0.92
    side_margin_ratio: float = 0.035
    scrollbar_width_px: int = 18
    feature_count: int = 5000


@dataclass(slots=True)
class PairMatch:
    first: int
    second: int
    cut_y: int | None
    confidence: float
    feature_inliers: int = 0
    similarity: float = 0.0
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.cut_y is not None


@dataclass(slots=True)
class StitchResult:
    image: np.ndarray
    order: list[int]
    matches: list[PairMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MosaicMatch:
    """Placement of ``second`` relative to ``first`` on the output canvas."""

    first: int
    second: int
    offset_x: int | None
    offset_y: int | None
    confidence: float
    feature_inliers: int = 0
    similarity: float = 0.0
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.offset_x is not None and self.offset_y is not None


@dataclass(slots=True)
class MosaicResult:
    image: np.ndarray
    positions: list[tuple[int, int]]
    matches: list[MosaicMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read_image(path: str | Path) -> np.ndarray:
    """Read an image from a path, including paths containing non-ASCII text."""
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取图片：{path}")
    return image


def write_image(path: str | Path, image: np.ndarray) -> None:
    suffix = Path(path).suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        suffix = ".png"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise ValueError(f"无法编码输出图片：{path}")
    encoded.tofile(str(path))


def _normalise_widths(images: Sequence[np.ndarray]) -> list[np.ndarray]:
    if not images:
        raise ValueError("请至少选择两张图片")
    widths = np.asarray([im.shape[1] for im in images])
    target = int(np.median(widths))
    if np.max(np.abs(widths - target) / target) > 0.08:
        raise ValueError("图片宽度差异超过 8%，请确认截图来自同一页面和缩放比例")
    result = []
    for image in images:
        if image.shape[1] == target:
            result.append(image.copy())
        else:
            height = round(image.shape[0] * target / image.shape[1])
            result.append(cv2.resize(image, (target, height), interpolation=cv2.INTER_AREA))
    return result


def _content_gray(image: np.ndarray, options: StitchOptions) -> tuple[np.ndarray, int]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    margin = max(2, round(gray.shape[1] * options.side_margin_ratio))
    right = max(margin + 20, gray.shape[1] - margin - options.scrollbar_width_px)
    cropped = gray[:, margin:right]
    # CLAHE makes low-contrast screenshots produce more stable keypoints.
    return cv2.createCLAHE(2.0, (8, 8)).apply(cropped), margin


def _mosaic_gray(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.createCLAHE(2.0, (8, 8)).apply(gray)


def _overlap_rect(
    shape_a: tuple[int, ...],
    shape_b: tuple[int, ...],
    dx: int,
    dy: int,
) -> tuple[int, int, int, int, int, int] | None:
    """Return aligned A/B starts and overlap size for p_b = p_a + (dx, dy)."""
    ah, aw = shape_a[:2]
    bh, bw = shape_b[:2]
    ax = max(0, -dx)
    ay = max(0, -dy)
    bx = max(0, dx)
    by = max(0, dy)
    width = min(aw - ax, bw - bx)
    height = min(ah - ay, bh - by)
    if width <= 0 or height <= 0:
        return None
    return ax, ay, bx, by, width, height


def _mosaic_similarity(a: np.ndarray, b: np.ndarray, dx: int, dy: int) -> float:
    overlap = _overlap_rect(a.shape, b.shape, dx, dy)
    if overlap is None:
        return 0.0
    ax, ay, bx, by, width, height = overlap
    aa = a[ay : ay + height, ax : ax + width]
    bb = b[by : by + height, bx : bx + width]
    scale = min(1.0, 520 / max(width, height))
    size = (max(24, round(width * scale)), max(24, round(height * scale)))
    aa = cv2.resize(aa, size, interpolation=cv2.INTER_AREA)
    bb = cv2.resize(bb, size, interpolation=cv2.INTER_AREA)
    edge_a = cv2.Laplacian(aa, cv2.CV_32F)
    edge_b = cv2.Laplacian(bb, cv2.CV_32F)
    corr = float(cv2.matchTemplate(edge_a, edge_b, cv2.TM_CCOEFF_NORMED)[0, 0])
    mad = float(np.mean(np.abs(aa.astype(np.float32) - bb.astype(np.float32))) / 255.0)
    return float(np.clip(0.72 * max(0.0, corr) + 0.28 * (1.0 - 2.2 * mad), 0, 1))


def match_pair_2d(
    first: np.ndarray,
    second: np.ndarray,
    first_index: int = 0,
    second_index: int = 1,
    options: StitchOptions | None = None,
) -> MosaicMatch:
    """Estimate unrestricted X/Y placement between two overlapping viewports."""
    options = options or StitchOptions()
    a = _mosaic_gray(first)
    b = _mosaic_gray(second)
    orb = cv2.ORB_create(nfeatures=options.feature_count, fastThreshold=8, edgeThreshold=12)
    key_a, desc_a = orb.detectAndCompute(a, None)
    key_b, desc_b = orb.detectAndCompute(b, None)
    if desc_a is None or desc_b is None or len(key_a) < 8 or len(key_b) < 8:
        return MosaicMatch(first_index, second_index, None, None, 0.0, reason="可用图像特征太少")

    raw = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_a, desc_b, k=2)
    candidates: list[tuple[float, float, float]] = []
    min_side = max(36, options.min_overlap_px // 2)
    # Different crop sizes are valid as long as the images share enough real
    # content. Base the threshold on the smaller image so a large screenshot
    # does not unfairly reject an overlapping smaller one.
    min_area = min(first.shape[0] * first.shape[1], second.shape[0] * second.shape[1]) * 0.035
    for pair in raw:
        if len(pair) != 2:
            continue
        best, other = pair
        if best.distance >= 0.76 * other.distance:
            continue
        pa = key_a[best.queryIdx].pt
        pb = key_b[best.trainIdx].pt
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        overlap = _overlap_rect(first.shape, second.shape, round(dx), round(dy))
        if overlap is None:
            continue
        width, height = overlap[4], overlap[5]
        if width >= min_side and height >= min_side and width * height >= min_area:
            candidates.append((dx, dy, best.distance))
    if len(candidates) < 5:
        return MosaicMatch(first_index, second_index, None, None, 0.0, reason="未找到可靠的二维重叠")

    vectors = np.asarray([(item[0], item[1]) for item in candidates], dtype=np.float32)
    tolerance = max(3.5, min(first.shape[:2]) * 0.007)
    best_mask: np.ndarray | None = None
    best_quality = -1.0
    for center in vectors:
        # A zero displacement is commonly caused by a viewer toolbar that stays
        # fixed while the underlying large image is panned.
        if np.linalg.norm(center) < 8:
            continue
        distances = np.linalg.norm(vectors - center, axis=1)
        mask = distances <= tolerance
        count = int(mask.sum())
        if count < 4:
            continue
        spread = float(np.mean(distances[mask]))
        quality = count - spread * 0.25
        if quality > best_quality:
            best_quality = quality
            best_mask = mask
    if best_mask is None:
        return MosaicMatch(first_index, second_index, None, None, 0.0, reason="匹配点的二维位移不一致")

    inlier_vectors = vectors[best_mask]
    dx, dy = np.median(inlier_vectors, axis=0)
    dx_i, dy_i = int(round(float(dx))), int(round(float(dy)))
    similarity = _mosaic_similarity(a, b, dx_i, dy_i)
    support = min(1.0, len(inlier_vectors) / 26.0)
    precision = max(0.0, 1.0 - float(np.mean(np.linalg.norm(inlier_vectors - (dx, dy), axis=1))) / tolerance)
    confidence = float(np.clip(0.48 * support + 0.17 * precision + 0.35 * similarity, 0, 1))
    if confidence < options.min_confidence or similarity < 0.20:
        return MosaicMatch(
            first_index,
            second_index,
            None,
            None,
            confidence,
            len(inlier_vectors),
            similarity,
            f"二维候选匹配可信度不足（{confidence:.0%}）",
        )
    # p_b = p_a + delta, therefore the B image origin is -delta from A.
    return MosaicMatch(
        first_index,
        second_index,
        -dx_i,
        -dy_i,
        confidence,
        len(inlier_vectors),
        similarity,
        "二维匹配成功",
    )


def _aligned_similarity(a: np.ndarray, b: np.ndarray, dy: float, options: StitchOptions) -> float:
    """Measure visual agreement for translation y_b = y_a + dy."""
    shift = int(round(dy))
    a_start = max(0, -shift)
    b_start = max(0, shift)
    length = min(a.shape[0] - a_start, b.shape[0] - b_start)
    if length < options.min_overlap_px:
        return 0.0

    # A fixed header in B does not move with the page. Ignore up to the first
    # 16% of the aligned region, then compare several content bands.
    header_skip = min(round(b.shape[0] * 0.16), max(0, length - options.min_overlap_px))
    a_start += header_skip
    b_start += header_skip
    length -= header_skip
    if length < options.min_overlap_px:
        return 0.0

    sample_h = min(length, 520)
    if length > sample_h:
        positions = np.linspace(0, length - sample_h, 3, dtype=int)
    else:
        positions = np.array([0])
    scores: list[float] = []
    for offset in positions:
        aa = a[a_start + offset : a_start + offset + sample_h]
        bb = b[b_start + offset : b_start + offset + sample_h]
        scale = min(1.0, 420 / aa.shape[1])
        size = (max(24, round(aa.shape[1] * scale)), max(24, round(aa.shape[0] * scale)))
        aa = cv2.resize(aa, size, interpolation=cv2.INTER_AREA)
        bb = cv2.resize(bb, size, interpolation=cv2.INTER_AREA)
        # Gradient agreement is less sensitive to theme brightness changes.
        ga = cv2.Sobel(aa, cv2.CV_32F, 0, 1, ksize=3)
        gb = cv2.Sobel(bb, cv2.CV_32F, 0, 1, ksize=3)
        corr = cv2.matchTemplate(ga, gb, cv2.TM_CCOEFF_NORMED)[0, 0]
        mad = np.mean(np.abs(aa.astype(np.float32) - bb.astype(np.float32))) / 255.0
        scores.append(float(np.clip(0.68 * max(0.0, corr) + 0.32 * (1.0 - 2.2 * mad), 0, 1)))
    return float(np.median(scores))


def match_pair(
    first: np.ndarray,
    second: np.ndarray,
    first_index: int = 0,
    second_index: int = 1,
    options: StitchOptions | None = None,
) -> PairMatch:
    """Find where the second screenshot continues after the first."""
    options = options or StitchOptions()
    a, _ = _content_gray(first, options)
    b, _ = _content_gray(second, options)
    orb = cv2.ORB_create(nfeatures=options.feature_count, fastThreshold=10, edgeThreshold=12)
    key_a, desc_a = orb.detectAndCompute(a, None)
    key_b, desc_b = orb.detectAndCompute(b, None)
    if desc_a is None or desc_b is None or len(key_a) < 8 or len(key_b) < 8:
        return PairMatch(first_index, second_index, None, 0.0, reason="可用图像特征太少")

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw = matcher.knnMatch(desc_a, desc_b, k=2)
    candidates: list[tuple[float, float, float]] = []
    max_dx = max(8, first.shape[1] * 0.025)
    for pair in raw:
        if len(pair) != 2:
            continue
        best, other = pair
        if best.distance >= 0.76 * other.distance:
            continue
        pa, pb = key_a[best.queryIdx].pt, key_b[best.trainIdx].pt
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        cut = first.shape[0] + dy
        if abs(dx) <= max_dx and options.min_overlap_px <= cut <= second.shape[0] * options.max_overlap_ratio:
            candidates.append((dy, dx, best.distance))
    if len(candidates) < 5:
        return PairMatch(first_index, second_index, None, 0.0, reason="未找到可靠的纵向对应关系")

    dys = np.asarray([item[0] for item in candidates])
    # Densest translation cluster; clustering rejects repeated text and fixed bars.
    best_mask: np.ndarray | None = None
    best_quality = -1.0
    tolerance = max(3.0, first.shape[0] * 0.006)
    for center in dys:
        mask = np.abs(dys - center) <= tolerance
        count = int(mask.sum())
        if count < 4:
            continue
        spread = float(np.std(dys[mask]))
        quality = count - spread * 0.35
        if quality > best_quality:
            best_quality, best_mask = quality, mask
    if best_mask is None:
        return PairMatch(first_index, second_index, None, 0.0, reason="匹配点的位移不一致")

    inliers = [candidates[i] for i, yes in enumerate(best_mask) if yes]
    dy = float(np.median([item[0] for item in inliers]))
    cut_y = int(round(first.shape[0] + dy))
    similarity = _aligned_similarity(a, b, dy, options)
    support = min(1.0, len(inliers) / 24.0)
    precision = max(0.0, 1.0 - float(np.std([x[0] for x in inliers])) / (tolerance + 1e-6))
    confidence = float(np.clip(0.48 * support + 0.17 * precision + 0.35 * similarity, 0, 1))
    if confidence < options.min_confidence or similarity < 0.22:
        return PairMatch(
            first_index, second_index, None, confidence, len(inliers), similarity,
            f"候选匹配可信度不足（{confidence:.0%}）",
        )
    return PairMatch(first_index, second_index, cut_y, confidence, len(inliers), similarity, "匹配成功")


def _best_order(images: Sequence[np.ndarray], options: StitchOptions) -> tuple[list[int], dict[tuple[int, int], PairMatch]]:
    count = len(images)
    pairs = {
        (i, j): match_pair(images[i], images[j], i, j, options)
        for i in range(count) for j in range(count) if i != j
    }
    score = lambda i, j: pairs[i, j].confidence if pairs[i, j].succeeded else -0.18
    if count <= 9:
        # Exhaustive ordering is predictable for the small batches used by this test tool.
        order = max(permutations(range(count)), key=lambda p: sum(score(p[k], p[k + 1]) for k in range(count - 1)))
        return list(order), pairs
    remaining = set(range(count))
    starts = {i: max(score(i, j) for j in remaining if j != i) for i in remaining}
    current = max(starts, key=starts.get)
    order = [current]
    remaining.remove(current)
    while remaining:
        current = max(remaining, key=lambda j: score(order[-1], j))
        order.append(current)
        remaining.remove(current)
    return order, pairs


def stitch_images(
    images: Sequence[np.ndarray],
    auto_sort: bool = True,
    options: StitchOptions | None = None,
    progress: Callable[[str], None] | None = None,
) -> StitchResult:
    options = options or StitchOptions()
    if len(images) < 2:
        raise ValueError("请至少选择两张图片")
    images = _normalise_widths(images)
    notify = progress or (lambda _: None)
    notify("正在分析相邻截图…")
    if auto_sort:
        order, pair_cache = _best_order(images, options)
    else:
        order = list(range(len(images)))
        pair_cache = {}

    chunks = [images[order[0]]]
    matches: list[PairMatch] = []
    warnings: list[str] = []
    for position, (i, j) in enumerate(zip(order, order[1:]), 1):
        notify(f"正在拼接第 {position + 1}/{len(order)} 张…")
        match = pair_cache.get((i, j)) or match_pair(images[i], images[j], i, j, options)
        matches.append(match)
        if match.succeeded:
            chunks.append(images[j][match.cut_y :])
        else:
            # Never delete uncertain content. A small separator makes the failure visible.
            separator = np.full((12, images[j].shape[1], 3), (40, 165, 235), dtype=np.uint8)
            chunks.extend((separator, images[j]))
            warnings.append(f"第 {i + 1} → {j + 1} 张：{match.reason}，已保留完整图片并插入橙色分隔线")
    notify("正在生成预览…")
    return StitchResult(np.vstack(chunks), order, matches, warnings)


def stitch_mosaic(
    images: Sequence[np.ndarray],
    options: StitchOptions | None = None,
    progress: Callable[[str], None] | None = None,
) -> MosaicResult:
    """Arrange freely panned screenshots on a two-dimensional output canvas.

    Capture order is retained. Each new image may attach to any earlier image,
    so a serpentine scan or a return to a previous row is supported.
    """
    options = options or StitchOptions(min_confidence=0.34)
    if len(images) < 2:
        raise ValueError("请至少选择两张图片")
    prepared = [image.copy() for image in images]
    notify = progress or (lambda _: None)
    positions: list[tuple[int, int]] = [(0, 0)]
    matches: list[MosaicMatch] = []
    warnings: list[str] = []

    for current in range(1, len(prepared)):
        notify(f"正在定位第 {current + 1}/{len(prepared)} 张…")
        candidates = [
            match_pair_2d(prepared[anchor], prepared[current], anchor, current, options)
            for anchor in range(current)
        ]
        successful = [match for match in candidates if match.succeeded]
        if successful:
            best = max(successful, key=lambda match: match.confidence)
            anchor_x, anchor_y = positions[best.first]
            positions.append((anchor_x + best.offset_x, anchor_y + best.offset_y))  # type: ignore[operator]
            matches.append(best)
        else:
            reason = max(candidates, key=lambda match: match.confidence).reason if candidates else "没有候选图片"
            next_x = max(
                x + prepared[index].shape[1]
                for index, (x, _y) in enumerate(positions)
            ) + 12
            positions.append((next_x, 0))
            failed = MosaicMatch(current - 1, current, None, None, 0.0, reason=reason)
            matches.append(failed)
            warnings.append(f"第 {current + 1} 张无法与已有画布可靠匹配，已完整放到画布右侧：{reason}")

    min_x = min(x for x, _ in positions)
    min_y = min(y for _, y in positions)
    max_x = max(x + image.shape[1] for image, (x, _y) in zip(prepared, positions))
    max_y = max(y + image.shape[0] for image, (_x, y) in zip(prepared, positions))
    canvas = np.full((max_y - min_y, max_x - min_x, 3), 238, dtype=np.uint8)
    occupied = np.zeros(canvas.shape[:2], dtype=bool)
    for image, (x, y) in zip(prepared, positions):
        image_h, image_w = image.shape[:2]
        left, top = x - min_x, y - min_y
        roi = canvas[top : top + image_h, left : left + image_w]
        mask = occupied[top : top + image_h, left : left + image_w]
        roi[~mask] = image[~mask]
        mask[:] = True

    notify("正在生成二维画布预览…")
    shifted_positions = [(x - min_x, y - min_y) for x, y in positions]
    return MosaicResult(canvas, shifted_positions, matches, warnings)
