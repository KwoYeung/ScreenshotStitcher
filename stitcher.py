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
    max_scale_delta: float = 0.025


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
    positions: list[tuple[int, int] | None]
    matches: list[MosaicMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _MosaicFeatures:
    gray: np.ndarray
    keypoints: list[cv2.KeyPoint]
    descriptors: np.ndarray | None


def read_image(path: str | Path) -> np.ndarray:
    """Read an image from a path, including paths containing non-ASCII text."""
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取图片：{path}")
    return image


def crop_image(image: np.ndarray, bounds: tuple[int, int, int, int]) -> np.ndarray:
    """Return a rectangular pixel crop while preserving all image channels."""
    left, top, right, bottom = bounds
    height, width = image.shape[:2]
    left = max(0, min(width, int(left)))
    right = max(0, min(width, int(right)))
    top = max(0, min(height, int(top)))
    bottom = max(0, min(height, int(bottom)))
    if right <= left or bottom <= top:
        raise ValueError("裁剪范围不能为空")
    return np.ascontiguousarray(image[top:bottom, left:right])


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


def _mosaic_features(image: np.ndarray, options: StitchOptions) -> _MosaicFeatures:
    gray = _mosaic_gray(image)
    orb = cv2.ORB_create(nfeatures=options.feature_count, fastThreshold=8, edgeThreshold=12)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    return _MosaicFeatures(gray, keypoints, descriptors)


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
    _first_features: _MosaicFeatures | None = None,
    _second_features: _MosaicFeatures | None = None,
) -> MosaicMatch:
    """Estimate unrestricted X/Y placement between two overlapping viewports."""
    options = options or StitchOptions()
    features_a = _first_features or _mosaic_features(first, options)
    features_b = _second_features or _mosaic_features(second, options)
    a, key_a, desc_a = features_a.gray, features_a.keypoints, features_a.descriptors
    b, key_b, desc_b = features_b.gray, features_b.keypoints, features_b.descriptors
    if desc_a is None or desc_b is None or len(key_a) < 8 or len(key_b) < 8:
        return MosaicMatch(first_index, second_index, None, None, 0.0, reason="可用图像特征太少")

    raw = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_a, desc_b, k=2)
    ratio_matches: list[cv2.DMatch] = []
    for pair in raw:
        if len(pair) != 2:
            continue
        best, other = pair
        if best.distance < 0.76 * other.distance:
            ratio_matches.append(best)

    # Estimate A -> B scale before enforcing a translation-only model. This
    # catches screenshots taken after the viewer/browser zoom changed; merging
    # those without resampling would bend text and geometry at the seam.
    if len(ratio_matches) >= 8:
        points_a = np.float32([key_a[item.queryIdx].pt for item in ratio_matches])
        points_b = np.float32([key_b[item.trainIdx].pt for item in ratio_matches])
        affine, affine_mask = cv2.estimateAffinePartial2D(
            points_a,
            points_b,
            method=cv2.RANSAC,
            ransacReprojThreshold=3.0,
            maxIters=3000,
            confidence=0.995,
            refineIters=10,
        )
        if affine is not None and affine_mask is not None:
            inlier_mask = affine_mask.ravel().astype(bool)
            inlier_count = int(inlier_mask.sum())
            inlier_points_a = points_a[inlier_mask]
            inlier_points_b = points_b[inlier_mask]
            span_a = float(np.linalg.norm(np.ptp(inlier_points_a, axis=0))) if inlier_count else 0.0
            span_b = float(np.linalg.norm(np.ptp(inlier_points_b, axis=0))) if inlier_count else 0.0
            estimated_scale = float(np.hypot(affine[0, 0], affine[1, 0]))
            enough_coverage = (
                inlier_count >= 6
                and span_a >= max(80.0, min(first.shape[:2]) * 0.18)
                and span_b >= max(80.0, min(second.shape[:2]) * 0.18)
            )
            if enough_coverage and (not np.isfinite(estimated_scale) or not 0.25 <= estimated_scale <= 4.0):
                return MosaicMatch(
                    first_index,
                    second_index,
                    None,
                    None,
                    0.0,
                    inlier_count,
                    reason=(
                        "检测到画布/内容比例异常，无法可靠估算缩放比；"
                        "请统一缩放比后重试"
                    ),
                )
            if enough_coverage and abs(estimated_scale - 1.0) > options.max_scale_delta:
                return MosaicMatch(
                    first_index,
                    second_index,
                    None,
                    None,
                    0.0,
                    inlier_count,
                    reason=(
                        "检测到画布/内容缩放比不一致："
                        f"第 {second_index + 1} 张约为第 {first_index + 1} 张的 {estimated_scale:.0%}；"
                        "请统一缩放比后重试"
                    ),
                )

    candidates: list[tuple[float, float, float, float, float, float, float]] = []
    min_side = max(36, options.min_overlap_px // 2)
    # Different crop sizes are valid as long as the images share enough real
    # content. Base the threshold on the smaller image so a large screenshot
    # does not unfairly reject an overlapping smaller one.
    min_area = min(first.shape[0] * first.shape[1], second.shape[0] * second.shape[1]) * 0.035
    for best in ratio_matches:
        pa = key_a[best.queryIdx].pt
        pb = key_b[best.trainIdx].pt
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        overlap = _overlap_rect(first.shape, second.shape, round(dx), round(dy))
        if overlap is None:
            continue
        width, height = overlap[4], overlap[5]
        if width >= min_side and height >= min_side and width * height >= min_area:
            candidates.append((dx, dy, best.distance, pa[0], pa[1], pb[0], pb[1]))
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
    inlier_points_a = np.asarray([(item[3], item[4]) for item in candidates], dtype=np.float32)[best_mask]
    inlier_points_b = np.asarray([(item[5], item[6]) for item in candidates], dtype=np.float32)[best_mask]
    coverage_a = float(np.linalg.norm(np.ptp(inlier_points_a, axis=0)))
    coverage_b = float(np.linalg.norm(np.ptp(inlier_points_b, axis=0)))
    min_coverage_a = max(72.0, min(first.shape[:2]) * 0.15)
    min_coverage_b = max(72.0, min(second.shape[:2]) * 0.15)
    if coverage_a < min_coverage_a or coverage_b < min_coverage_b:
        return MosaicMatch(
            first_index,
            second_index,
            None,
            None,
            0.0,
            len(inlier_vectors),
            reason="匹配点过于集中，无法确认画布比例和位移一致",
        )
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
    strict_order: bool = False,
) -> MosaicResult:
    """Arrange freely panned screenshots on a two-dimensional output canvas.

    Automatic mode builds a confidence graph and stitches the largest reliable
    connected group, skipping explicit outliers. Strict mode requires every
    image to overlap its immediate predecessor and stops at the first failure.
    """
    options = options or StitchOptions(min_confidence=0.34)
    if len(images) < 2:
        raise ValueError("请至少选择两张图片")
    prepared = [image.copy() for image in images]
    notify = progress or (lambda _: None)
    notify("正在提取图片特征…")
    features = [_mosaic_features(image, options) for image in prepared]
    positions: list[tuple[int, int] | None] = [None] * len(prepared)
    matches: list[MosaicMatch] = []
    warnings: list[str] = []

    if strict_order:
        positions[0] = (0, 0)
        for current in range(1, len(prepared)):
            notify(f"正在定位第 {current + 1}/{len(prepared)} 张…")
            previous = current - 1
            match = match_pair_2d(
                prepared[previous],
                prepared[current],
                previous,
                current,
                options,
                features[previous],
                features[current],
            )
            if not match.succeeded:
                raise ValueError(
                    f"自由平移拼接已在第 {previous + 1} → {current + 1} 张停止：{match.reason}。"
                    "请检查这两张的重叠内容和缩放比。"
                )
            anchor_x, anchor_y = positions[previous]  # type: ignore[misc]
            positions[current] = (anchor_x + match.offset_x, anchor_y + match.offset_y)  # type: ignore[operator]
            matches.append(match)
    else:
        pair_matches: dict[tuple[int, int], MosaicMatch] = {}
        total_pairs = len(prepared) * (len(prepared) - 1) // 2
        pair_number = 0
        for first_index in range(len(prepared) - 1):
            for second_index in range(first_index + 1, len(prepared)):
                pair_number += 1
                notify(f"正在分析图片关系 {pair_number}/{total_pairs}…")
                pair_matches[(first_index, second_index)] = match_pair_2d(
                    prepared[first_index],
                    prepared[second_index],
                    first_index,
                    second_index,
                    options,
                    features[first_index],
                    features[second_index],
                )

        successful_edges = [match for match in pair_matches.values() if match.succeeded]
        parent = list(range(len(prepared)))

        def find(node: int) -> int:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(first_node: int, second_node: int) -> None:
            first_root, second_root = find(first_node), find(second_node)
            if first_root != second_root:
                parent[second_root] = first_root

        for match in successful_edges:
            union(match.first, match.second)

        components: dict[int, set[int]] = {}
        for index in range(len(prepared)):
            components.setdefault(find(index), set()).add(index)
        largest_size = max(len(component) for component in components.values())
        if largest_size < 2:
            strongest_failure = max(pair_matches.values(), key=lambda match: match.confidence)
            raise ValueError(
                "所有图片都无法形成可靠的重叠关系："
                f"{strongest_failure.reason}。请核实重叠区域和缩放比。"
            )
        largest_components = [component for component in components.values() if len(component) == largest_size]
        if len(largest_components) > 1:
            groups = "；".join(
                "、".join(str(index + 1) for index in sorted(component))
                for component in largest_components
            )
            raise ValueError(
                f"检测到多个规模相同的独立图片组（{groups}），无法可靠判断主画布。"
                "请补充过渡截图或移除比例不一致的图片。"
            )

        main_component = largest_components[0]
        root = min(main_component)
        positions[root] = (0, 0)
        placed = {root}
        component_edges = [
            match
            for match in successful_edges
            if match.first in main_component and match.second in main_component
        ]
        while len(placed) < len(main_component):
            connecting = [
                match
                for match in component_edges
                if (match.first in placed) != (match.second in placed)
            ]
            if not connecting:
                raise ValueError("主图片组的连接关系不完整，无法生成可靠画布")
            best = max(connecting, key=lambda match: match.confidence)
            if best.first in placed:
                anchor, current = best.first, best.second
                offset_x, offset_y = best.offset_x, best.offset_y
            else:
                anchor, current = best.second, best.first
                offset_x, offset_y = -best.offset_x, -best.offset_y  # type: ignore[operator]
            anchor_x, anchor_y = positions[anchor]  # type: ignore[misc]
            positions[current] = (anchor_x + offset_x, anchor_y + offset_y)  # type: ignore[operator]
            placed.add(current)
            matches.append(best)

        for skipped in sorted(set(range(len(prepared))) - main_component):
            comparisons = [
                pair_matches[(min(skipped, placed_index), max(skipped, placed_index))]
                for placed_index in main_component
            ]
            scale_failure = next(
                (
                    match
                    for match in comparisons
                    if "缩放比" in match.reason or "比例异常" in match.reason
                ),
                None,
            )
            if scale_failure is not None:
                reason = scale_failure.reason
            else:
                reason = max(comparisons, key=lambda match: match.confidence).reason
            warnings.append(f"第 {skipped + 1} 张已跳过：{reason}")

    placed_items = [
        (index, position)
        for index, position in enumerate(positions)
        if position is not None
    ]
    min_x = min(position[0] for _index, position in placed_items)
    min_y = min(position[1] for _index, position in placed_items)
    max_x = max(position[0] + prepared[index].shape[1] for index, position in placed_items)
    max_y = max(position[1] + prepared[index].shape[0] for index, position in placed_items)
    # Keep areas not covered by any screenshot transparent in the PNG result.
    canvas = np.zeros((max_y - min_y, max_x - min_x, 4), dtype=np.uint8)
    occupied = np.zeros(canvas.shape[:2], dtype=bool)
    for index, position in placed_items:
        image = prepared[index]
        x, y = position
        image_h, image_w = image.shape[:2]
        left, top = x - min_x, y - min_y
        roi = canvas[top : top + image_h, left : left + image_w]
        mask = occupied[top : top + image_h, left : left + image_w]
        roi[..., :3][~mask] = image[~mask]
        roi[..., 3][~mask] = 255
        mask[:] = True

    notify("正在生成二维画布预览…")
    shifted_positions = [
        (position[0] - min_x, position[1] - min_y) if position is not None else None
        for position in positions
    ]
    return MosaicResult(canvas, shifted_positions, matches, warnings)
