import io
import unittest
from unittest.mock import patch
from urllib.error import URLError

from update_checker import check_latest_release, is_newer_version


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class UpdateCheckerTests(unittest.TestCase):
    def test_version_comparison(self):
        self.assertTrue(is_newer_version("1.2.0", "1.1.0"))
        self.assertTrue(is_newer_version("2.0", "1.9.9"))
        self.assertFalse(is_newer_version("1.1", "1.1.0"))
        self.assertFalse(is_newer_version("not-a-version", "1.1.0"))

    @patch("update_checker.urlopen")
    def test_gitee_release_is_used_first(self, mock_urlopen):
        mock_urlopen.return_value = _Response(b'{"tag_name":"v1.2.0"}')
        release = check_latest_release()
        self.assertIsNotNone(release)
        self.assertEqual(release.version, "1.2.0")
        self.assertIn("gitee.com", release.url)
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("update_checker.urlopen")
    def test_github_is_fallback(self, mock_urlopen):
        mock_urlopen.side_effect = [URLError("offline"), _Response(b'{"tag_name":"v1.3.0"}')]
        release = check_latest_release()
        self.assertIsNotNone(release)
        self.assertEqual(release.version, "1.3.0")
        self.assertIn("github.com", release.url)

    @patch("update_checker.urlopen", side_effect=URLError("offline"))
    def test_offline_returns_none(self, _mock_urlopen):
        self.assertIsNone(check_latest_release())


if __name__ == "__main__":
    unittest.main()
