"""条目状态分级：空白 / 缺详情 / 详情不全 / 缺图 / 图失效 / 完整，以及各自建议的重试动作。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import db, entries, imgcache

COVER = "https://img.example/a.jpg"
FULL_DETAILS = [
    {"label": "发行日期", "value": "2020-01-01", "links": [], "datetime": ""},
    {"label": "片商", "value": "片商A", "links": [], "datetime": ""},
    {"label": "演员", "value": "甲, 乙", "links": [], "datetime": ""},
    {"label": "类型", "value": "剧情, 恋爱", "links": [], "datetime": ""},
]


def _meta(url, **over):
    meta = {"url": url, "title": "标题", "thumbnail": COVER, "success": True,
            "details": list(FULL_DETAILS)}
    meta.update(over)
    return meta


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        db.DB_PATH = self.dir / "test.db"
        self._cache_dir = imgcache.CACHE_DIR
        imgcache.CACHE_DIR = self.dir / "img"

    def tearDown(self):
        imgcache.CACHE_DIR = self._cache_dir
        imgcache.clear_failed(COVER)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _status(self, code="ABP-123"):
        e = entries.get_entry(code)
        return e["status"], e["missing"], e["retry"], e["cover_state"]


class TestStatusLevels(Base):
    def test_empty_never_fetched(self):
        entries.import_links(["https://a.com/ABP-123"])
        e = entries.get_entry("ABP-123")
        self.assertEqual((e["status"], e["retry"], e["fetched"]), ("empty", "both", 0))
        self.assertEqual(e["status_level"], 4)

    def test_empty_after_failed_fetch(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched({"url": "https://a.com/ABP-123", "success": False, "details": []})
        e = entries.get_entry("ABP-123")
        self.assertEqual((e["status"], e["retry"], e["fetched"]), ("empty", "both", 2))

    def test_cover_but_no_detail_reports_missing_detail(self):
        """又缺图又缺详情 → 报「缺详情」，不能只报缺图。"""
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", details=[]))
        status, missing, retry, cover_state = self._status()
        self.assertEqual(status, "no_detail")
        self.assertEqual(retry, "detail")
        self.assertEqual(cover_state, 1)  # 封面链接有、图未抓
        self.assertIn("片商", missing)
        self.assertIn("类型", missing)

    def test_detail_but_no_cover_still_missing_detail(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", details=[], thumbnail=""))
        self.assertEqual(self._status()[0], "no_detail")

    def test_partial_detail(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123",
                                     details=[d for d in FULL_DETAILS if d["label"] != "类型"]))
        status, missing, retry, _ = self._status()
        self.assertEqual(status, "partial")
        self.assertEqual(missing, ["类型"])
        self.assertEqual(retry, "detail")
        self.assertEqual(entries.get_entry("ABP-123")["status_level"], 2)

    def test_cover_missing_only(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", thumbnail=""))
        status, missing, retry, cover_state = self._status()
        self.assertEqual((status, cover_state, missing), ("cover_missing", 0, []))
        self.assertEqual(retry, "detail")  # 补封面链接只能靠重抓详情页

    def test_cover_failed_only(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123"))
        imgcache.mark_failed(COVER)
        status, _, retry, cover_state = self._status()
        self.assertEqual((status, cover_state), ("cover_failed", 3))
        self.assertEqual(retry, "cover")  # 只重抓图，不动详情

    def test_ok_when_cover_cached(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123"))
        imgcache.put(COVER, b"IMG", "image/png")
        status, missing, retry, cover_state = self._status()
        self.assertEqual((status, retry, cover_state, missing), ("ok", "", 2, []))
        self.assertEqual(entries.get_entry("ABP-123")["status_level"], 0)

    def test_ok_while_cover_not_yet_cached(self):
        """有链接但图还没抓（懒加载）属于正常，不算异常。"""
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123"))
        status, _, retry, cover_state = self._status()
        self.assertEqual((status, cover_state, retry), ("ok", 1, ""))


class TestStatusStats(Base):
    def test_counts(self):
        entries.import_links(["https://a.com/ABP-1", "https://a.com/ABP-2", "https://a.com/ABP-3"])
        # 缺详情
        entries.ingest_fetched(_meta("https://a.com/ABP-1", details=[]))
        # 缺图（详情齐）
        entries.ingest_fetched(_meta("https://a.com/ABP-2", thumbnail=""))
        # 完整
        entries.ingest_fetched(_meta("https://a.com/ABP-3"))
        st = entries.queue_stats()
        self.assertEqual(st["no_detail"], 1)
        self.assertEqual(st["cover_missing"], 1)

    def test_list_entries_carries_status(self):
        entries.import_links(["https://a.com/ABP-123"])
        rows = entries.list_entries()
        self.assertEqual(len(rows), 1)
        for key in ("status", "status_level", "missing", "retry"):
            self.assertIn(key, rows[0])
        self.assertEqual(rows[0]["status"], "empty")


if __name__ == "__main__":
    unittest.main()
