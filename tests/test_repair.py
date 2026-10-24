"""repair：失败恢复——图片只修图，缺内容才重抓页面（且走渲染层、有上限）。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import db, entries, imgcache, repair


def _detail(label, value):
    return {"label": label, "value": value, "links": [], "datetime": ""}


def _meta(url, *, title="T", date="2024-01-01", cover="", success=True):
    details = [_detail("发行日期", date)] if date else []
    return {"url": url, "title": title, "thumbnail": cover,
            "success": success, "details": details}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        db.DB_PATH = self.dir / "test.db"
        self._cache_dir = imgcache.CACHE_DIR
        imgcache.CACHE_DIR = None  # 跟随数据库目录

        self._saved = {
            "cooldown": repair.FAIL_COOLDOWN,
            "cover_attempts": repair.MAX_COVER_ATTEMPTS,
            "page_attempts": repair.MAX_PAGE_ATTEMPTS,
            "resolve": repair.proxy.resolve,
            "img": repair.fetch_image_bytes,
            "page": repair.fetch_page,
        }
        repair.FAIL_COOLDOWN = 0  # 测试里让新鲜失败标记也参与修复
        repair.proxy.resolve = lambda force=False: ""
        repair._cover_attempts.clear()
        repair._page_attempts.clear()
        repair._end()  # 清掉上一个用例可能残留的「当前动作」

    def tearDown(self):
        repair.FAIL_COOLDOWN = self._saved["cooldown"]
        repair.MAX_COVER_ATTEMPTS = self._saved["cover_attempts"]
        repair.MAX_PAGE_ATTEMPTS = self._saved["page_attempts"]
        repair.proxy.resolve = self._saved["resolve"]
        repair.fetch_image_bytes = self._saved["img"]
        repair.fetch_page = self._saved["page"]
        repair._cover_attempts.clear()
        repair._page_attempts.clear()
        imgcache.CACHE_DIR = self._cache_dir
        shutil.rmtree(self.dir, ignore_errors=True)

    def seed(self, code="ABP-100", *, title="T", date="2024-01-01",
             cover="https://img/x.jpg"):
        url = f"https://a.com/{code}"
        entries.import_links([url])
        entries.ingest_fetched(_meta(url, title=title, date=date, cover=cover))
        return url

    def seed_cover_failed(self, code="ABP-100", cover="https://img/x.jpg"):
        self.seed(code, cover=cover)
        imgcache.mark_failed(cover)
        return cover


class TestRepairActivity(Base):
    def test_status_visible_during_cover_attempt(self):
        self.seed_cover_failed()
        seen = []

        def fake_img(src, *a, **k):
            seen.append(repair.status())
            return (b"IMG", "image/jpeg")

        repair.fetch_image_bytes = fake_img
        repair.repair_cover_failures()

        self.assertEqual(seen[0]["kind"], "cover")
        self.assertEqual(seen[0]["url"], "https://img/x.jpg")
        self.assertEqual(seen[0]["domain"], "img")
        self.assertIsNone(repair.status())  # 结束后清空

    def test_status_visible_during_page_refetch(self):
        self.seed("ABP-100", date="")
        seen = []

        def fake_page(url, **kw):
            seen.append(repair.status())
            return _meta(url, date="2024-05-06", success=True)

        repair.fetch_page = fake_page
        repair.repair_missing_details()

        self.assertEqual(seen[0]["kind"], "page")
        self.assertEqual(seen[0]["url"], "https://a.com/ABP-100")
        self.assertIsNone(repair.status())

    def test_round_progress_during_cover_repair(self):
        """修复进度条要给出确定百分比：本轮 1 项，请求时已完成 1/1。"""
        self.seed_cover_failed()
        seen = []

        def fake_img(src, *a, **k):
            seen.append(repair.status())
            return (b"IMG", "image/jpeg")

        repair.fetch_image_bytes = fake_img
        repair.run_once()

        self.assertEqual((seen[0]["kind"], seen[0]["done"], seen[0]["total"]),
                         ("cover", 1, 1))
        self.assertIsNone(repair.status())

    def test_round_progress_during_page_repair(self):
        self.seed("ABP-100", date="")
        seen = []

        def fake_page(url, **kw):
            seen.append(repair.status())
            return _meta(url, date="2024-05-06", success=True)

        repair.fetch_page = fake_page
        repair.run_once()

        self.assertEqual((seen[0]["kind"], seen[0]["done"], seen[0]["total"]),
                         ("page", 1, 1))
        self.assertIsNone(repair.status())


class TestCoverRepair(Base):
    def test_cover_repaired_from_url_without_page_fetch(self):
        cover = self.seed_cover_failed()
        calls = []

        def fake_img(src, proxy_url, timeout=15, referer=""):
            calls.append(src)
            return (b"IMG", "image/jpeg")

        def boom(*a, **k):
            raise AssertionError("图失效修复不应重抓页面")

        repair.fetch_image_bytes = fake_img
        repair.fetch_page = boom

        res = repair.repair_cover_failures()

        self.assertEqual(res["repaired"], 1)
        self.assertEqual(calls, [cover])
        self.assertTrue(imgcache.has(cover))
        self.assertFalse(imgcache.is_failed(cover))

    def test_cover_failure_does_not_refetch_page(self):
        self.seed_cover_failed()
        repair.fetch_image_bytes = lambda *a, **k: None
        repair.fetch_page = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("图失效修复不应重抓页面"))

        res = repair.repair_cover_failures()

        self.assertEqual(res["failed"], 1)
        e = entries.get_entry("ABP-100")
        self.assertEqual(e["status"], "cover_failed")
        self.assertEqual(e["fetched"], 1)  # 图片失败不牵连 fetched

    def test_cover_attempt_cap(self):
        repair.MAX_COVER_ATTEMPTS = 2
        self.seed_cover_failed()
        repair.fetch_image_bytes = lambda *a, **k: None

        self.assertEqual(repair.repair_cover_failures()["failed"], 1)
        self.assertEqual(repair.repair_cover_failures()["failed"], 1)
        third = repair.repair_cover_failures()
        self.assertEqual(third["failed"], 0)
        self.assertEqual(third["skipped"], 1)

    def test_cooldown_skips_recent_failure(self):
        cover = self.seed_cover_failed()
        repair.FAIL_COOLDOWN = 3600
        repair.fetch_image_bytes = lambda *a, **k: (b"IMG", "image/jpeg")

        res = repair.repair_cover_failures()

        self.assertEqual(res["checked"], 0)
        self.assertFalse(imgcache.has(cover))

    def test_same_cover_url_fetched_once_per_sweep(self):
        cover = "https://img/shared.jpg"
        self.seed_cover_failed("ABP-100", cover=cover)
        self.seed_cover_failed("ABP-200", cover=cover)  # 两个条目共用同一张封面
        calls = []

        def fake_img(src, *a, **k):
            calls.append(src)
            return None

        repair.fetch_image_bytes = fake_img
        repair.repair_cover_failures()

        self.assertEqual(calls, [cover])  # 一轮只请求一次，不按条目重复

    def test_failed_marker_kept_during_attempt(self):
        self.seed_cover_failed()
        seen = []

        def fake_img(src, *a, **k):
            seen.append(imgcache.is_failed(src))
            return None

        repair.fetch_image_bytes = fake_img
        repair.repair_cover_failures()

        # 请求期间失败标记仍在 → /api/img 不会并发重复抓同一张图
        self.assertEqual(seen, [True])

    def test_no_failed_marks_nothing(self):
        res = repair.repair_cover_failures()
        self.assertEqual(res, {"checked": 0, "repaired": 0, "failed": 0, "skipped": 0})


class TestPageRepair(Base):
    def test_partial_repaired_via_render_layer(self):
        self.seed("ABP-100", date="")  # 缺发行日期 → partial
        calls = []

        def fake_page(url, **kw):
            calls.append(kw)
            return _meta(url, title="T", date="2024-05-06",
                         cover="https://img/new.jpg", success=True)

        repair.fetch_page = fake_page
        res = repair.repair_missing_details()

        self.assertEqual(res["repaired"], 1)
        self.assertTrue(calls and calls[0].get("prefer_render") is True)
        e = entries.get_entry("ABP-100")
        self.assertEqual(e["release_date"], "2024-05-06")
        self.assertEqual(e["status"], "ok")

    def test_cover_missing_repaired(self):
        self.seed("ABP-100", cover="")
        repair.fetch_page = lambda url, **kw: _meta(
            url, cover="https://img/new.jpg", success=True)

        res = repair.repair_missing_details()

        self.assertEqual(res["repaired"], 1)
        self.assertEqual(entries.get_entry("ABP-100")["cover"], "https://img/new.jpg")

    def test_page_attempt_cap(self):
        repair.MAX_PAGE_ATTEMPTS = 2
        self.seed("ABP-100", date="")
        calls = []

        def fake_page(url, **kw):
            calls.append(url)
            return _meta(url, date="", success=True)  # 抓成功但仍没有日期

        repair.fetch_page = fake_page
        repair.repair_missing_details()
        repair.repair_missing_details()
        third = repair.repair_missing_details()

        self.assertEqual(len(calls), 2)
        self.assertEqual(third["skipped"], 1)

    def test_frozen_domain_is_skipped(self):
        self.seed("ABP-100", date="")
        entries.set_domain_frozen("a.com", True)
        calls = []
        repair.fetch_page = lambda *a, **k: calls.append(1) or _meta(
            "https://a.com/ABP-100")

        res = repair.repair_missing_details()

        self.assertEqual(calls, [])
        self.assertEqual(res["checked"], 0)
        self.assertEqual(res["skipped"], 1)

    def test_page_failure_circuit_breaker(self):
        for i in range(5):
            self.seed(f"ABP-{i:03d}", date="")
        calls = []

        def fake_page(url, **kw):
            calls.append(url)
            return _meta(url, success=False)

        repair.fetch_page = fake_page
        repair.repair_missing_details()

        self.assertEqual(len(calls), repair.PAGE_FAIL_BREAKER)


class TestPageRepairQuery(Base):
    def test_only_partial_and_cover_missing_selected(self):
        self.seed("P-001", date="")                        # partial
        self.seed("C-001", cover="")                       # cover_missing
        self.seed("B-001", title="", date="", cover="")    # empty（空白）
        self.seed("O-001")                                 # ok

        codes = entries.list_page_repair_codes(50)

        self.assertIn("P-001", codes)
        self.assertIn("C-001", codes)
        self.assertNotIn("B-001", codes)
        self.assertNotIn("O-001", codes)

    def test_failed_entries_not_selected(self):
        url = self.seed("F-001", date="")
        # 页面抓取失败 → fetched=2，交给 worker，不进页面修复候选
        entries.ingest_fetched({"url": url, "title": "", "thumbnail": "",
                                "success": False, "details": []})
        self.assertNotIn("F-001", entries.list_page_repair_codes(50))


class TestImageFetchHelpers(unittest.TestCase):
    """图片请求的 Referer / Sec-Fetch-Site 选择（纯逻辑，无网络）。"""

    def test_referer_candidates_order(self):
        from app import fetcher
        src = "https://img.example.com/a.png"
        self.assertEqual(
            fetcher._referer_candidates(src, "https://page.example.com/p"),
            ["https://page.example.com/p", "https://img.example.com", ""],
        )

    def test_sec_fetch_site(self):
        from app import fetcher
        self.assertEqual(
            fetcher._sec_fetch_site("https://a.com/x.png", "https://a.com/p"),
            "same-origin")
        self.assertEqual(
            fetcher._sec_fetch_site("https://img.a.com/x.png", "https://www.a.com/p"),
            "same-site")
        self.assertEqual(
            fetcher._sec_fetch_site("https://cdn.b.com/x.png", "https://a.com/p"),
            "cross-site")
        self.assertEqual(
            fetcher._sec_fetch_site("https://a.com/x.png", ""),
            "cross-site")


class TestImageFetchStrategy(unittest.TestCase):
    """fetch_image_bytes 的 403 多策略顺序（不联网，替换底层请求）。"""

    def setUp(self):
        from app import fetcher
        self.fetcher = fetcher
        self._orig_cf = fetcher._cf_image_get
        self._orig_urllib = fetcher._urllib_image_get
        self.calls: list[tuple[str, str]] = []
        self.urllib_calls: list[str] = []

    def tearDown(self):
        self.fetcher._cf_image_get = self._orig_cf
        self.fetcher._urllib_image_get = self._orig_urllib

    def _fake_cf(self, results):
        def fake(src, proxy_url, headers, timeout, impersonate):
            self.calls.append((headers.get("Referer", ""), impersonate))
            return results.pop(0)
        self.fetcher._cf_image_get = fake

    def _fake_urllib(self, result=None):
        def fake(src, proxy_url, headers, timeout):
            self.urllib_calls.append(src)
            return result
        self.fetcher._urllib_image_get = fake

    def test_403_tries_referers_then_alt_impersonation(self):
        self._fake_cf([(403, None, "", "cloudflare")] * 4)
        self._fake_urllib(None)

        got = self.fetcher.fetch_image_bytes(
            "https://img.example.com/x.png", None,
            referer="https://page.example.com/p")

        self.assertIsNone(got)
        self.assertEqual([c[0] for c in self.calls[:3]],
                         ["https://page.example.com/p", "https://img.example.com", ""])
        self.assertEqual(self.calls[3][1], self.fetcher._ALT_IMPERSONATE)
        self.assertEqual(self.urllib_calls, [])  # 确认是 CF → 不再发无用的 urllib 请求

    def test_success_on_second_referer(self):
        self._fake_cf([(403, None, "", "cloudflare"),
                       (200, b"IMG", "image/png", "")])
        self._fake_urllib(None)

        got = self.fetcher.fetch_image_bytes(
            "https://img.example.com/x.png", None,
            referer="https://page.example.com/p")

        self.assertEqual(got, (b"IMG", "image/png"))
        self.assertEqual(self.calls[1][0], "https://img.example.com")

    def test_404_stops_without_urllib(self):
        self._fake_cf([(404, None, "", "")])
        self._fake_urllib(None)

        got = self.fetcher.fetch_image_bytes(
            "https://img.example.com/x.png", None,
            referer="https://page.example.com/p")

        self.assertIsNone(got)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.urllib_calls, [])

    def test_network_error_falls_back_to_urllib(self):
        self._fake_cf([(None, None, "", "")])
        self._fake_urllib((b"IMG", "image/jpeg"))

        got = self.fetcher.fetch_image_bytes(
            "https://img.example.com/x.png", None,
            referer="https://page.example.com/p")

        self.assertEqual(got, (b"IMG", "image/jpeg"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.urllib_calls), 1)


class TestImgcacheMarkers(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._db_path = db.DB_PATH
        self._cache_dir = imgcache.CACHE_DIR
        db.DB_PATH = self.dir / "test.db"
        imgcache.CACHE_DIR = None

    def tearDown(self):
        db.DB_PATH = self._db_path
        imgcache.CACHE_DIR = self._cache_dir
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_failed_keys_and_age(self):
        src = "https://img/x.jpg"
        self.assertFalse(imgcache.is_failed(src))
        imgcache.mark_failed(src)
        self.assertTrue(imgcache.is_failed(src))
        self.assertIn(imgcache.key_for(src), imgcache.failed_keys())
        self.assertTrue(imgcache.failed_older_than(src, 0))
        self.assertFalse(imgcache.failed_older_than(src, 3600))

        imgcache.clear_failed(src)
        self.assertFalse(imgcache.is_failed(src))
        self.assertEqual(imgcache.failed_keys(), set())


if __name__ == "__main__":
    unittest.main()
