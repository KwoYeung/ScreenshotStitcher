"""Small, dependency-free release checker used by the desktop UI."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


GITEE_API = "https://gitee.com/api/v5/repos/KwoYeung/ScreenshotStitcher/releases/latest"
GITHUB_API = "https://api.github.com/repos/KwoYeung/ScreenshotStitcher/releases/latest"
GITEE_RELEASE = "https://gitee.com/KwoYeung/ScreenshotStitcher/releases/tag/{tag}"
GITHUB_RELEASE = "https://github.com/KwoYeung/ScreenshotStitcher/releases/tag/{tag}"


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    url: str


def _version_parts(version: str) -> tuple[int, ...] | None:
    cleaned = version.strip().lstrip("vV")
    if not re.fullmatch(r"\d+(?:\.\d+)*", cleaned):
        return None
    return tuple(int(part) for part in cleaned.split("."))


def is_newer_version(latest: str, current: str) -> bool:
    latest_parts = _version_parts(latest)
    current_parts = _version_parts(current)
    if latest_parts is None or current_parts is None:
        return False
    width = max(len(latest_parts), len(current_parts))
    return latest_parts + (0,) * (width - len(latest_parts)) > current_parts + (0,) * (width - len(current_parts))


def _read_release(api_url: str, release_url: str, timeout: float) -> ReleaseInfo | None:
    request = Request(
        api_url,
        headers={"Accept": "application/json", "User-Agent": "ScreenshotStitcher-Update-Checker"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    tag = str(payload.get("tag_name", "")).strip()
    version = tag.lstrip("vV")
    if _version_parts(version) is None:
        return None
    return ReleaseInfo(version, release_url.format(tag=quote(tag, safe="")))


def check_latest_release(timeout: float = 2.5) -> ReleaseInfo | None:
    """Try Gitee first and GitHub second; network failures stay silent."""
    sources = (
        (GITEE_API, GITEE_RELEASE),
        (GITHUB_API, GITHUB_RELEASE),
    )
    for api_url, release_url in sources:
        try:
            release = _read_release(api_url, release_url, timeout)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            continue
        if release is not None:
            return release
    return None
