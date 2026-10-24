"""条目状态分级：空白 / 详情不全 / 缺图 / 图失效 / 完整，以及各自建议的重试动作。

「详情是否拿到」只看标题 + 发行日期：发行日期只可能来自详情页，拿到它就说明详情页上
能拿的都拿到了；详情页没给的项（片商、演员、类型等）不算异常。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import db, entries, imgcache

COVER = "https://img.example/a.jpg"
# 详情页给的一整套信息（实际站点常常只给其中几项）
FULL_DETAILS = [
    {"label": "发行日期", "value": "2020-01-01", "links": [], "datetime": ""},
    {"label": "片商", "value": "片商A", "links": [], "datetime": ""},
    {"label": "演员", "value": "甲, 乙", "links": [], "datetime": ""},
    {"label": "类型", "value": "剧情, 恋爱", "links": [], "datetime": ""},
]
DATE_ONLY = [d for d in FULL_DETAILS if d["label"] == "发行日期"]


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
        self.assertEqual(e["status_level"], 3)

    def test_empty_after_failed_fetch(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched({"url": "https://a.com/ABP-123", "success": False, "details": []})
        e = entries.get_entry("ABP-123")
        self.assertEqual((e["status"], e["retry"], e["fetched"]), ("empty", "both", 2))

    def test_no_date_is_partial_even_with_cover(self):
        """只有标题（可能来自 OG）没有日期 → 详情不全，且不能只报缺图。"""
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", details=[]))
        status, missing, retry, cover_state = self._status()
        self.assertEqual(status, "partial")
        self.assertEqual(missing, ["发行日期"])
        self.assertEqual(retry, "detail")
        self.assertEqual(cover_state, 1)  # 封面链接有、图未抓
        self.assertEqual(entries.get_entry("ABP-123")["status_level"], 2)

    def test_no_title_is_partial(self):
        """有日期没标题也算详情不全（缺的是标题这一项）。"""
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", title="", details=DATE_ONLY))
        status, missing, retry, _ = self._status()
        self.assertEqual((status, missing, retry), ("partial", ["标题"], "detail"))

    def test_both_title_and_date_missing_is_partial(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", title="", details=[]))
        status, missing, _, _ = self._status()
        self.assertEqual(status, "partial")
        self.assertEqual(missing, ["标题", "发行日期"])

    def test_sparse_detail_page_is_ok(self):
        """有标题 + 有发行日期就是拿到了详情：详情页没给片商/类型不算缺。"""
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", details=DATE_ONLY))
        status, missing, retry, _ = self._status()
        self.assertEqual((status, missing, retry), ("ok", [], ""))
        self.assertEqual(entries.get_entry("ABP-123")["status_level"], 0)

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

    def test_ok_while_cover_not_yet_cached(self):
        """有链接但图还没抓（懒加载）属于正常，不算异常。"""
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123"))
        status, _, retry, cover_state = self._status()
        self.assertEqual((status, cover_state, retry), ("ok", 1, ""))


class TestStatusStats(Base):
    def test_counts(self):
        entries.import_links(["https://a.com/ABP-1", "https://a.com/ABP-2",
                              "https://a.com/ABP-3", "https://a.com/ABP-4"])
        # 详情不全：有标题没日期
        entries.ingest_fetched(_meta("https://a.com/ABP-1", details=[]))
        # 缺图：标题和日期都有，只是没封面
        entries.ingest_fetched(_meta("https://a.com/ABP-2", thumbnail=""))
        # 完整
        entries.ingest_fetched(_meta("https://a.com/ABP-3"))
        # ABP-4 从未抓过（空白），不该重复算进详情不全
        st = entries.queue_stats()
        self.assertEqual(st["partial"], 1)
        self.assertEqual(st["cover_missing"], 1)
        self.assertEqual(st["pending"], 1)
        self.assertEqual(st["cover_failed"], 0)
        self.assertEqual(st["frozen_domains"], 0)

    def test_cover_failed_count(self):
        """详情齐了、有封面链接，但图片抓取失败 → 图失效。"""
        entries.import_links(["https://a.com/ABP-5"])
        entries.ingest_fetched(_meta("https://a.com/ABP-5"))
        imgcache.mark_failed(COVER)

        st = entries.queue_stats()

        self.assertEqual(st["cover_failed"], 1)
        self.assertEqual(st["cover_missing"], 0)

    def test_failed_marker_ignored_when_image_cached(self):
        """残留的 .fail 标记不能把已缓存的封面算成图失效。"""
        entries.import_links(["https://a.com/ABP-6"])
        entries.ingest_fetched(_meta("https://a.com/ABP-6"))
        imgcache.put(COVER, b"IMG", "image/png")
        imgcache.mark_failed(COVER)

        self.assertEqual(entries.queue_stats()["cover_failed"], 0)

    def test_list_entries_carries_status(self):
        entries.import_links(["https://a.com/ABP-123"])
        rows = entries.list_entries()
        self.assertEqual(len(rows), 1)
        for key in ("status", "status_level", "missing", "retry"):
            self.assertIn(key, rows[0])
        self.assertEqual(rows[0]["status"], "empty")

    def test_partial_counter_matches_badge_for_title_only_entry(self):
        """只有标题（OG 拿到的），没有日期/封面/详情：徽章是详情不全，计数也必须是。

        这里曾出现过两边判「空白」的字段清单不一致：计数那套漏了标题，于是这类
        条目在卡片上标「详情不全」却不进底部计数。
        """
        entries.import_links(["https://a.com/ABP-9"])
        entries.ingest_fetched(_meta("https://a.com/ABP-9", thumbnail="", details=[]))

        e = entries.get_entry("ABP-9")
        self.assertEqual(e["status"], "partial")
        self.assertEqual(entries.queue_stats()["partial"], 1)

    def test_library_completeness_counts(self):
        """空闲时进度条显示整库完整度：标题 + 发行日期都拿到才算完整。"""
        entries.import_links(["https://a.com/ABP-7", "https://a.com/ABP-8"])
        entries.ingest_fetched(_meta("https://a.com/ABP-7"))

        st = entries.queue_stats()

        self.assertEqual(st["entries_total"], 2)
        self.assertEqual(st["entries_complete"], 1)


if __name__ == "__main__":
    unittest.main()
