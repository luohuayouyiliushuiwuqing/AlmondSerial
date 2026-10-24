"""imgcache：图片缓存读写（临时目录）。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import db, imgcache


class TestImgCache(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        imgcache.CACHE_DIR = self.dir

    def tearDown(self):
        imgcache.CACHE_DIR = None  # 交还给「跟随数据库目录」
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


class TestCacheFollowsDb(unittest.TestCase):
    """库被移动/重命名后，缓存跟着库目录走：命中关系不因路径变化而丢失。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._cache_dir = imgcache.CACHE_DIR
        self._db_path = db.DB_PATH
        imgcache.CACHE_DIR = None  # 不固定目录，跟随数据库

    def tearDown(self):
        imgcache.CACHE_DIR = self._cache_dir
        db.DB_PATH = self._db_path
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_cache_dir_follows_db_directory(self):
        old = self.dir / "old"
        new = self.dir / "renamed" / "library.db"
        new.parent.mkdir(parents=True)
        new.write_bytes(b"")

        db.DB_PATH = old / "library.db"
        self.assertEqual(imgcache.cache_root(), old / "cache" / "img")

        db.DB_PATH = new  # 库被移动并改名
        self.assertEqual(imgcache.cache_root(), new.parent / "cache" / "img")

    def test_cache_created_next_to_db(self):
        """库目录下没有缓存目录时不回退项目目录，而是就地新建。"""
        lib = self.dir / "lib" / "library.db"
        lib.parent.mkdir(parents=True)
        db.DB_PATH = lib
        target = lib.parent / "cache" / "img"
        self.assertFalse(target.exists())
        self.assertEqual(imgcache.cache_root(), target)

        imgcache.put("https://img.example/new.jpg", b"IMG", "image/png")
        self.assertTrue(target.is_dir())
        self.assertTrue((target / imgcache._key("https://img.example/new.jpg")).is_file())

    def test_cached_image_survives_move(self):
        src = "https://img.example/a.jpg"
        old = self.dir / "old"
        (old / "cache" / "img").mkdir(parents=True)
        db.DB_PATH = old / "library.db"
        imgcache.put(src, b"IMG", "image/png")

        renamed = self.dir / "renamed"
        old.rename(renamed)  # 整个目录搬走 + 改名
        db.DB_PATH = renamed / "library.db"
        self.assertTrue(imgcache.has(src))
        self.assertEqual(imgcache.get(src), (b"IMG", "image/png"))


if __name__ == "__main__":
    unittest.main()
