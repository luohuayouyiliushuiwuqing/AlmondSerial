"""actresses：演员名单、搜索、单个演员的作品列表（纯逻辑、无网络）。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import actresses, db, entries


def _detail(label, value):
    return {"label": label, "value": value, "links": [], "datetime": ""}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        db.DB_PATH = self.dir / "test.db"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def seed(self):
        entries.import_links(["https://a.com/ABP-100", "https://a.com/SSIS-200"])
        entries.ingest_fetched({"url": "https://a.com/ABP-100", "title": "甲",
                                "success": True,
                                "details": [_detail("演员", "苍井空, 三上悠亜")]})
        entries.ingest_fetched({"url": "https://a.com/SSIS-200", "title": "乙",
                                "success": True,
                                "details": [_detail("演员", "苍井空")]})
        with db.conn() as conn:
            for name in ("苍井空", "三上悠亜"):
                conn.execute("INSERT OR IGNORE INTO actresses (name) VALUES (?)", (name,))
            ids = {n: i for i, n in conn.execute("SELECT id, name FROM actresses")}
            for code, name in [("ABP-100", "苍井空"), ("ABP-100", "三上悠亜"),
                               ("SSIS-200", "苍井空")]:
                conn.execute(
                    "INSERT OR IGNORE INTO entry_actress (code, actress_id) VALUES (?,?)",
                    (code, ids[name]))
        return ids


class TestList(Base):
    def test_list_with_counts(self):
        self.seed()
        res = actresses.list_actresses()
        self.assertTrue(res["ready"])
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["actresses"][0]["name"], "苍井空")   # 作品多的排前面
        self.assertEqual(res["actresses"][0]["entries"], 2)
        self.assertEqual(res["actresses"][1]["entries"], 1)

    def test_search(self):
        self.seed()
        res = actresses.list_actresses("苍井")
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["actresses"][0]["name"], "苍井空")

    def test_search_no_hit(self):
        self.seed()
        res = actresses.list_actresses("不存在的人")
        self.assertEqual(res["total"], 0)
        self.assertEqual(res["actresses"], [])

    def test_ready_false_without_table(self):
        with db.conn() as conn:
            conn.execute("DROP TABLE IF EXISTS actresses")
        res = actresses.list_actresses()
        self.assertFalse(res["ready"])
        self.assertEqual(res["actresses"], [])


class TestGet(Base):
    def test_entries_of_actress(self):
        ids = self.seed()
        data = actresses.get_actress(ids["苍井空"])
        self.assertEqual(data["name"], "苍井空")
        self.assertEqual(data["total"], 2)
        self.assertEqual(sorted(e["code"] for e in data["entries"]),
                         ["ABP-100", "SSIS-200"])
        # 返回的是完整条目结构，前端可直接复用卡片渲染
        first = data["entries"][0]
        for key in ("code", "title", "cover", "status", "links", "tags"):
            self.assertIn(key, first)

    def test_single_entry_actress(self):
        ids = self.seed()
        data = actresses.get_actress(ids["三上悠亜"])
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["entries"][0]["code"], "ABP-100")

    def test_unknown_id(self):
        self.seed()
        self.assertIsNone(actresses.get_actress(999999))

    def test_actress_without_entries(self):
        ids = self.seed()
        with db.conn() as conn:
            conn.execute("INSERT INTO actresses (name) VALUES ('孤零零')")
            new_id = conn.execute(
                "SELECT id FROM actresses WHERE name='孤零零'").fetchone()[0]
        data = actresses.get_actress(new_id)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["entries"], [])


if __name__ == "__main__":
    unittest.main()
