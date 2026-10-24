"""网页快照：插件批量抓标签页的留档层（能识别番号的照旧建条目）。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db, snapshots
from app.main import app


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        db.DB_PATH = self.dir / "test.db"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestSaveBatch(Base):
    def test_recognized_page_makes_entry_and_snapshot(self):
        res = snapshots.save_batch([{
            "url": "https://a.com/detail/ABP-123", "title": "标题",
            "thumbnail": "https://img.example/a.jpg",
            "details": [{"label": "发行日期", "value": "2020-01-01", "links": [], "datetime": ""}],
        }])
        self.assertEqual(res["saved"], 1)
        self.assertEqual(res["entries"], 1)
        self.assertEqual(res["unrecognized"], 0)
        self.assertEqual(res["items"][0]["code"], "ABP-123")

        item = snapshots.list_snapshots()[0]
        self.assertEqual(item["url"], "https://a.com/detail/ABP-123")
        self.assertEqual(item["code"], "ABP-123")
        self.assertEqual(item["domain"], "a.com")
        self.assertEqual(item["details"][0]["label"], "发行日期")

    def test_unrecognized_page_still_archived(self):
        res = snapshots.save_batch([{"url": "https://a.com/about", "title": "关于"}])
        self.assertEqual(res["saved"], 1)
        self.assertEqual(res["entries"], 0)
        self.assertEqual(res["unrecognized"], 1)
        self.assertEqual(snapshots.count_snapshots(), 1)
        self.assertEqual(snapshots.list_snapshots()[0]["code"], "")

    def test_invalid_url_skipped(self):
        """非 http(s) 地址一律跳过（本机/系统页的排除在插件侧，后端照收）。"""
        res = snapshots.save_batch([
            {"url": "chrome://history", "title": "历史记录"},
            {"url": "", "title": "空"},
        ])
        self.assertEqual(res["skipped"], 2)
        self.assertEqual(res["saved"], 0)
        self.assertEqual(snapshots.count_snapshots(), 0)

    def test_same_url_overwrites_snapshot(self):
        snapshots.save_batch([{"url": "https://a.com/x", "title": "旧"}])
        snapshots.save_batch([{"url": "https://a.com/x", "title": "新"}])
        self.assertEqual(snapshots.count_snapshots(), 1)
        self.assertEqual(snapshots.list_snapshots()[0]["title"], "新")

    def test_bad_thumbnail_dropped(self):
        snapshots.save_batch([{"url": "https://a.com/x", "thumbnail": "javascript:alert(1)"}])
        self.assertEqual(snapshots.list_snapshots()[0]["thumbnail"], "")


class TestQuery(Base):
    def setUp(self):
        super().setUp()
        snapshots.save_batch([
            {"url": "https://a.com/detail/ABP-123", "title": "一号作品"},
            {"url": "https://b.com/post/1", "title": "随笔"},
        ])

    def test_search_by_code_and_title(self):
        self.assertEqual(len(snapshots.list_snapshots(q="ABP-123")), 1)
        self.assertEqual(len(snapshots.list_snapshots(q="随笔")), 1)
        self.assertEqual(len(snapshots.list_snapshots(q="b.com")), 1)
        self.assertEqual(snapshots.count_snapshots(q="com"), 2)

    def test_paging(self):
        self.assertEqual(len(snapshots.list_snapshots(limit=1)), 1)
        self.assertEqual(len(snapshots.list_snapshots(limit=10, offset=1)), 1)

    def test_delete(self):
        self.assertTrue(snapshots.delete_snapshot("https://a.com/detail/ABP-123"))
        self.assertFalse(snapshots.delete_snapshot("https://a.com/detail/ABP-123"))
        self.assertEqual(snapshots.count_snapshots(), 1)


class TestEndpoints(Base):
    """走真实 HTTP 层（不进 context manager，避开 startup 里的后台线程与真实库）。"""

    def setUp(self):
        super().setUp()
        self.client = TestClient(app)

    def test_post_batch_then_query(self):
        resp = self.client.post("/api/snapshots", json={"pages": [
            {"url": "https://a.com/detail/ABP-123", "title": "有番号",
             "thumbnail": "https://img.example/a.jpg", "details": []},
            {"url": "https://b.com/post/1", "title": "没番号"},
        ]})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual((body["saved"], body["entries"], body["unrecognized"]), (2, 1, 1))
        self.assertEqual(body["items"][0]["code"], "ABP-123")
        self.assertEqual(body["items"][1]["code"], "")

        listed = self.client.get("/api/snapshots", params={"q": "ABP-123"}).json()
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["snapshots"][0]["title"], "有番号")

        self.assertEqual(self.client.delete("/api/snapshots",
                                            params={"url": "https://b.com/post/1"}).status_code, 200)
        self.assertEqual(self.client.delete("/api/snapshots",
                                            params={"url": "https://b.com/post/1"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
