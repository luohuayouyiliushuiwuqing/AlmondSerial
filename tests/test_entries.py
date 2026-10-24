"""entries：链接导入去重 / 抓取回填合并 / 删除级联 / 条目标签（纯逻辑、无网络）。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import db, entries, imgcache


def _meta(url, **kw):
    base = {"url": url, "title": kw.pop("title", "T"), "thumbnail": kw.pop("thumbnail", ""),
            "success": kw.pop("success", True), "details": kw.pop("details", [])}
    base.update(kw)
    return base


def _detail(label, value):
    return {"label": label, "value": value, "links": [], "datetime": ""}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        db.DB_PATH = self.dir / "test.db"
        # 图片缓存也指到临时目录并还原，避免各测试类之间互相串图
        self._cache_dir = imgcache.CACHE_DIR
        imgcache.CACHE_DIR = self.dir / "img"

    def tearDown(self):
        imgcache.CACHE_DIR = self._cache_dir
        shutil.rmtree(self.dir, ignore_errors=True)


class TestImportLinks(Base):
    def test_import_and_dedup_by_code(self):
        res = entries.import_links(["https://a.com/ABP-123", "https://b.com/ABP-123"])
        self.assertEqual(res["inserted"], 1)
        self.assertEqual(res["existing"], 1)
        self.assertEqual(res["unrecognized"], [])
        e = entries.get_entry("ABP-123")
        self.assertEqual(sorted(e["links"]), ["https://a.com/ABP-123", "https://b.com/ABP-123"])
        self.assertFalse(e["fetched"])

    def test_unrecognized(self):
        res = entries.import_links(["https://a.com/about"])
        self.assertEqual(res["inserted"], 0)
        self.assertEqual(res["unrecognized"], ["https://a.com/about"])

    def test_reimport_same_link_is_idempotent(self):
        entries.import_links(["https://a.com/ABP-123"])
        res = entries.import_links(["https://a.com/ABP-123"])
        self.assertEqual(res["inserted"], 0)
        self.assertEqual(res["existing"], 1)
        self.assertEqual(len(entries.get_entry("ABP-123")["links"]), 1)


class TestIngestFetched(Base):
    def test_fetch_enriches_entry(self):
        entries.import_links(["https://a.com/ABP-123"])
        meta = _meta("https://a.com/ABP-123", title="标题",
                     details=[_detail("发行日期", "2020-01-01"), _detail("演员", "甲, 乙")])
        e = entries.ingest_fetched(meta)
        self.assertEqual(e["title"], "标题")
        self.assertEqual(e["release_date"], "2020-01-01")
        self.assertEqual(e["actors"], ["甲", "乙"])
        self.assertTrue(e["fetched"])

    def test_code_from_details_when_url_has_none(self):
        meta = _meta("https://a.com/watch?v=1", title="T", details=[_detail("番号", "MIDE-999")])
        e = entries.ingest_fetched(meta)
        self.assertIsNotNone(e)
        self.assertEqual(e["code"], "MIDE-999")

    def test_failed_fetch_does_not_overwrite(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", title="旧", success=True))
        e = entries.ingest_fetched(_meta("https://a.com/ABP-123", title="新", success=False))
        self.assertEqual(e["title"], "旧")
        self.assertTrue(e["fetched"])

    def test_empty_title_preserves_old(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", title="旧"))
        e = entries.ingest_fetched(_meta("https://a.com/ABP-123", title=""))
        self.assertEqual(e["title"], "旧")

    def test_successful_fetch_keeps_existing_cover(self):
        """再次抓取（如插件从镜像站抓同一番号）不能把已有封面换成别的地址。"""
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", thumbnail="https://img.example/a.jpg"))
        e = entries.ingest_fetched(_meta("https://a.com/ABP-123", thumbnail="https://mirror.example/b.jpg"))
        self.assertEqual(e["cover"], "https://img.example/a.jpg")

    def test_cached_cover_is_not_replaced(self):
        """原封面的图已经缓存下来，就更没有理由换 URL。"""
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", thumbnail="https://img.example/a.jpg"))
        imgcache.put("https://img.example/a.jpg", b"OLD", "image/jpeg")
        e = entries.ingest_fetched(_meta("https://a.com/ABP-123", thumbnail="https://mirror.example/b.jpg"))
        self.assertEqual(e["cover"], "https://img.example/a.jpg")
        self.assertEqual(imgcache.get("https://img.example/a.jpg")[0], b"OLD")

    def test_failed_cover_can_be_replaced(self):
        """原封面确认抓不到时，新地址是修复机会，应当采纳。"""
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", thumbnail="https://img.example/a.jpg"))
        imgcache.mark_failed("https://img.example/a.jpg")
        e = entries.ingest_fetched(_meta("https://a.com/ABP-123", thumbnail="https://mirror.example/b.jpg"))
        self.assertEqual(e["cover"], "https://mirror.example/b.jpg")

    def test_no_code_returns_none(self):
        self.assertIsNone(entries.ingest_fetched(_meta("https://a.com/about")))


class TestUpdate(Base):
    def test_update_fields(self):
        entries.import_links(["https://a.com/ABP-123"])
        e = entries.update_entry("ABP-123", {"title": "新标题", "release_date": "2020-01-01",
                                             "actors": ["甲", "乙"], "genres": ["剧情"]})
        self.assertEqual(e["title"], "新标题")
        self.assertEqual(e["release_date"], "2020-01-01")
        self.assertEqual(e["actors"], ["甲", "乙"])
        self.assertEqual(e["genres"], ["剧情"])

    def test_update_missing_returns_none(self):
        self.assertIsNone(entries.update_entry("NO-CODE", {"title": "x"}))


class TestDelete(Base):
    def test_delete_cleans_links_and_tags(self):
        entries.import_links(["https://a.com/ABP-123"])
        tag = entries.create_tag("收藏")
        entries.add_tag_to_entry("ABP-123", tag["id"])
        self.assertTrue(entries.delete_entry("ABP-123"))
        self.assertIsNone(entries.get_entry("ABP-123"))
        self.assertEqual(entries.count_entries(), 0)
        self.assertEqual(entries.list_pending_links(), [])


class TestEntryTags(Base):
    def test_tag_lifecycle(self):
        entries.import_links(["https://a.com/ABP-123"])
        tag = entries.create_tag("收藏")
        self.assertTrue(entries.add_tag_to_entry("ABP-123", tag["id"]))
        e = entries.get_entry("ABP-123")
        self.assertEqual([t["name"] for t in e["tags"]], ["收藏"])
        entries.remove_tag_from_entry("ABP-123", tag["id"])
        self.assertEqual(entries.get_entry("ABP-123")["tags"], [])


class TestSearch(Base):
    def test_search_hits_fields(self):
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", title="某标题",
                                     details=[_detail("片商", "某片商"), _detail("演员", "苍井空")]))
        self.assertEqual(len(entries.list_entries(q="某片商")), 1)
        self.assertEqual(len(entries.list_entries(q="苍井空")), 1)
        self.assertEqual(len(entries.list_entries(q="不存在")), 0)


class TestProxyEnrich(Base):
    def test_enrich_fills_missing_only(self):
        # 网页抓取已有标题/片商，代理只补缺失的发行日期/演员/类型，不覆盖已有
        entries.import_links(["https://a.com/ABP-123"])
        entries.ingest_fetched(_meta("https://a.com/ABP-123", title="网页标题",
                                     details=[_detail("片商", "网页片商")]))
        data = {"title": "代理标题", "maker": "代理片商", "release_date": "2020-01-01",
                "actors": ["甲"], "genres": ["剧情"]}
        e = entries.enrich_from_proxy("ABP-123", data)
        self.assertEqual(e["title"], "网页标题")
        self.assertEqual(e["maker"], "网页片商")
        self.assertEqual(e["release_date"], "2020-01-01")
        self.assertEqual(e["actors"], ["甲"])
        self.assertEqual(e["genres"], ["剧情"])

    def test_enrich_missing_entry_returns_none(self):
        self.assertIsNone(entries.enrich_from_proxy("NO-CODE", {"title": "x"}))


class TestLinkIntegrity(Base):
    def test_url_saved_verbatim_with_query_and_fragment(self):
        url = "https://a.com/detail/ABP-123?lang=zh&ref=search#top"
        entries.import_links([url])
        e = entries.get_entry("ABP-123")
        self.assertEqual(e["links"], [url])  # 查询参数/锚点原样保留，不截断

    def test_ingest_saves_link_when_code_from_details(self):
        # URL 本身识别不出番号，但详情里有「番号」→ 原始 URL 仍作为来源链接保存
        url = "https://a.com/watch?v=999"
        meta = _meta(url, title="T", details=[_detail("番号", "MIDE-999")])
        e = entries.ingest_fetched(meta)
        self.assertEqual(e["links"], [url])

    def test_multiple_links_all_preserved(self):
        urls = ["https://a.com/ABP-123", "https://b.com/ABP-123", "https://c.com/ABP-123"]
        entries.import_links(urls)
        e = entries.get_entry("ABP-123")
        self.assertEqual(sorted(e["links"]), sorted(urls))  # 全部镜像链接都保留


class TestOrdering(Base):
    def test_list_newest_first(self):
        entries.import_links(["https://a.com/AAA-111"])
        entries.import_links(["https://a.com/BBB-222"])
        with db.conn() as conn:
            conn.execute("UPDATE entries SET created_at='2020-01-01 00:00:00' WHERE code='AAA-111'")
            conn.execute("UPDATE entries SET created_at='2021-01-01 00:00:00' WHERE code='BBB-222'")
        codes = [e["code"] for e in entries.list_entries()]
        self.assertEqual(codes, ["BBB-222", "AAA-111"])  # 录入时间新的在前


class TestCoverState(Base):
    """封面（图像）抓取状态：0 无链接 / 1 有链接未抓图 / 2 已抓图 / 3 抓图失败。"""

    def _entry(self, cover):
        entries.import_links(["https://a.com/ABP-123"])
        entries.update_entry("ABP-123", {"cover": cover})
        return entries.get_entry("ABP-123")

    def test_no_cover_is_state_0(self):
        self.assertEqual(self._entry("")["cover_state"], 0)

    def test_cover_link_pending_is_state_1(self):
        self.assertEqual(self._entry("https://img.example/a.jpg")["cover_state"], 1)

    def test_cover_cached_is_state_2(self):
        imgcache.put("https://img.example/a.jpg", b"IMG", "image/jpeg")
        self.assertEqual(self._entry("https://img.example/a.jpg")["cover_state"], 2)

    def test_cover_failed_is_state_3(self):
        imgcache.mark_failed("https://img.example/a.jpg")
        self.assertEqual(self._entry("https://img.example/a.jpg")["cover_state"], 3)


if __name__ == "__main__":
    unittest.main()
