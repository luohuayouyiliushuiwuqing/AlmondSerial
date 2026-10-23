"""imgcache：图片缓存读写（临时目录）。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import imgcache


class TestImgCache(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        imgcache.CACHE_DIR = self.dir

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_put_get(self):
        imgcache.put("https://x.com/a.jpg", b"IMG", "image/jpeg")
        self.assertEqual(imgcache.get("https://x.com/a.jpg"), (b"IMG", "image/jpeg"))

    def test_miss_returns_none(self):
        self.assertIsNone(imgcache.get("https://x.com/nope.jpg"))

    def test_put_empty_skipped(self):
        imgcache.put("https://x.com/a.jpg", b"", "image/jpeg")
        self.assertIsNone(imgcache.get("https://x.com/a.jpg"))

    def test_same_url_same_key(self):
        self.assertEqual(imgcache.cache_path("https://x.com/a.jpg"),
                         imgcache.cache_path("https://x.com/a.jpg"))

    def test_default_ctype(self):
        imgcache.put("https://x.com/b.jpg", b"X", "")
        self.assertEqual(imgcache.get("https://x.com/b.jpg")[1], "application/octet-stream")

    def test_has_and_failed_markers(self):
        self.assertFalse(imgcache.has("https://x.com/c.jpg"))
        self.assertFalse(imgcache.is_failed("https://x.com/c.jpg"))
        imgcache.mark_failed("https://x.com/c.jpg")
        self.assertTrue(imgcache.is_failed("https://x.com/c.jpg"))
        self.assertFalse(imgcache.has("https://x.com/c.jpg"))
        imgcache.clear_failed("https://x.com/c.jpg")
        self.assertFalse(imgcache.is_failed("https://x.com/c.jpg"))

    def test_put_clears_failed_mark(self):
        imgcache.mark_failed("https://x.com/d.jpg")
        imgcache.put("https://x.com/d.jpg", b"IMG", "image/jpeg")
        self.assertTrue(imgcache.has("https://x.com/d.jpg"))
        self.assertFalse(imgcache.is_failed("https://x.com/d.jpg"))


if __name__ == "__main__":
    unittest.main()
