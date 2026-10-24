"""capture 端点：浏览器插件直存（复用 ingest_fetched）+ 封面字节落缓存。"""

import base64
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app import db, imgcache
from app.main import CaptureRequest, api_capture


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        db.DB_PATH = self.dir / "test.db"
        self._cache_dir = imgcache.CACHE_DIR
        imgcache.CACHE_DIR = self.dir / "img"

    def tearDown(self):
        imgcache.CACHE_DIR = self._cache_dir
        shutil.rmtree(self.dir, ignore_errors=True)


class TestCapture(Base):
    def test_capture_saves_entry_from_url(self):
        res = api_capture(CaptureRequest(
            url="https://a.com/detail/ABP-123", title="标题",
            thumbnail="https://img.example/a.jpg",
            details=[{"label": "发行日期", "value": "2020-01-01", "links": [], "datetime": ""}],
        ))
        self.assertTrue(res["ok"])
        e = res["entry"]
        self.assertEqual(e["code"], "ABP-123")
        self.assertEqual(e["title"], "标题")
        self.assertEqual(e["release_date"], "2020-01-01")
        self.assertEqual(e["cover"], "https://img.example/a.jpg")
        self.assertEqual(e["cover_state"], 1)  # 有链接、未抓图

    def test_capture_with_image_bytes_caches_cover(self):
        res = api_capture(CaptureRequest(
            url="https://a.com/detail/ABP-123", title="T",
            thumbnail="https://img.example/a.jpg",
            image_base64=base64.b64encode(b"IMG").decode(), image_type="image/jpeg",
        ))
        self.assertTrue(imgcache.has("https://img.example/a.jpg"))
        self.assertEqual(res["entry"]["cover_state"], 2)  # 已抓图

    def test_recapture_does_not_overwrite_cover(self):
        """同一条目再次从插件抓取：封面 URL 和已缓存的图片字节都必须保持原样。"""
        api_capture(CaptureRequest(
            url="https://a.com/detail/ABP-123", title="T",
            thumbnail="https://img.example/a.jpg",
            image_base64=base64.b64encode(b"OLD").decode(), image_type="image/jpeg",
        ))
        res = api_capture(CaptureRequest(
            url="https://mirror.example/detail/ABP-123", title="T2",
            thumbnail="https://mirror.example/b.jpg",
            image_base64=base64.b64encode(b"NEW").decode(), image_type="image/jpeg",
        ))
        self.assertEqual(res["entry"]["cover"], "https://img.example/a.jpg")
        self.assertEqual(imgcache.get("https://img.example/a.jpg")[0], b"OLD")
        self.assertFalse(imgcache.has("https://mirror.example/b.jpg"))

    def test_recapture_does_not_rewrite_cached_bytes_for_same_url(self):
        """同一封面 URL 重抓：本地已有图片就不再让插件字节覆盖它。"""
        api_capture(CaptureRequest(
            url="https://a.com/detail/ABP-123", title="T",
            thumbnail="https://img.example/a.jpg",
            image_base64=base64.b64encode(b"OLD").decode(), image_type="image/jpeg",
        ))
        api_capture(CaptureRequest(
            url="https://a.com/detail/ABP-123", title="T",
            thumbnail="https://img.example/a.jpg",
            image_base64=base64.b64encode(b"NEW").decode(), image_type="image/jpeg",
        ))
        self.assertEqual(imgcache.get("https://img.example/a.jpg")[0], b"OLD")

    def test_recapture_fills_missing_cover_bytes(self):
        """封面链接一致但本地还没缓存时，插件字节正好省一次抓图，应当落盘。"""
        api_capture(CaptureRequest(
            url="https://a.com/detail/ABP-123", title="T",
            thumbnail="https://img.example/a.jpg",
        ))
        res = api_capture(CaptureRequest(
            url="https://a.com/detail/ABP-123", title="T",
            thumbnail="https://img.example/a.jpg",
            image_base64=base64.b64encode(b"IMG").decode(), image_type="image/jpeg",
        ))
        self.assertEqual(res["entry"]["cover_state"], 2)
        self.assertEqual(imgcache.get("https://img.example/a.jpg")[0], b"IMG")

    def test_capture_unrecognized(self):
        res = api_capture(CaptureRequest(url="https://a.com/about", title="x"))
        self.assertTrue(res.get("unrecognized"))

    def test_capture_requires_url(self):
        with self.assertRaises(HTTPException):
            api_capture(CaptureRequest(url="", title="x"))


if __name__ == "__main__":
    unittest.main()
