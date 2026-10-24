"""actresses：演员名单、自动同步、作品列表、类型标签（纯逻辑、无网络）。"""

import json
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


class TestAutoSync(Base):
    """条目一落库就自动进演员层，不用再手动跑 build_actresses.py。"""

    def test_ingest_creates_links(self):
        entries.ingest_fetched({"url": "https://a.com/ABP-100", "title": "甲",
                                "success": True,
                                "details": [_detail("演员", "苍井空, 三上悠亜")]})
        res = actresses.list_actresses()
        self.assertTrue(res["ready"])
        self.assertEqual(res["total"], 2)
        ids = {a["name"]: a["id"] for a in res["actresses"]}
        data = actresses.get_actress(ids["苍井空"])
        self.assertEqual([e["code"] for e in data["entries"]], ["ABP-100"])

    def test_ingest_is_idempotent(self):
        meta = {"url": "https://a.com/ABP-100", "title": "甲", "success": True,
                "details": [_detail("演员", "苍井空")]}
        entries.ingest_fetched(meta)
        entries.ingest_fetched(meta)
        with db.conn() as conn:
            n = conn.execute("SELECT count(*) FROM entry_actress").fetchone()[0]
        self.assertEqual(n, 1)

    def test_refetch_drops_dropped_actress(self):
        entries.ingest_fetched({"url": "https://a.com/ABP-100", "success": True,
                                "details": [_detail("演员", "苍井空, 三上悠亜")]})
        entries.ingest_fetched({"url": "https://a.com/ABP-100", "success": True,
                                "details": [_detail("演员", "苍井空")]})
        res = actresses.list_actresses()
        self.assertEqual([a["name"] for a in res["actresses"]], ["苍井空"])

    def test_delete_entry_clears_links(self):
        entries.ingest_fetched({"url": "https://a.com/ABP-100", "success": True,
                                "details": [_detail("演员", "苍井空")]})
        entries.delete_entry("ABP-100")
        with db.conn() as conn:
            n = conn.execute("SELECT count(*) FROM entry_actress").fetchone()[0]
        self.assertEqual(n, 0)

    def test_manual_actors_used_when_details_have_none(self):
        entries.ingest_fetched({"url": "https://a.com/ABP-100", "title": "甲",
                                "success": True, "details": []})
        entries.update_entry("ABP-100", {"actors": ["苍井空"]})
        res = actresses.list_actresses()
        self.assertEqual([a["name"] for a in res["actresses"]], ["苍井空"])

    def test_backfill_old_db_on_first_visit(self):
        """老库：条目已经在、演员表还是空的 → 第一次访问演员视图时补一次全量。"""
        entries.import_links(["https://a.com/ABP-100"])
        with db.conn() as conn:
            conn.execute(
                "UPDATE entries SET details = ? WHERE code = 'ABP-100'",
                (json.dumps([_detail("演员", "苍井空")], ensure_ascii=False),))
            conn.execute("DELETE FROM entry_actress")
            conn.execute("DELETE FROM actresses")
        res = actresses.list_actresses()
        self.assertEqual([a["name"] for a in res["actresses"]], ["苍井空"])


class TestTags(Base):
    """演员类型：系统只按作品算频次参考（且要占到 2/3），写入全靠用户点选。"""

    def setUp(self):
        super().setUp()
        for code, genres in [("ABP-100", "巨乳, 单体"), ("SSIS-200", "巨乳, 单体"),
                             ("SSIS-300", "巨乳, 企画")]:
            entries.ingest_fetched({"url": f"https://a.com/{code}", "success": True,
                                    "details": [_detail("演员", "苍井空"),
                                                _detail("类型", genres)]})
        self.aid = actresses.list_actresses()["actresses"][0]["id"]

    def test_suggestions_sorted_by_frequency(self):
        s = actresses.suggest_tags(self.aid)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["usable"], 3)
        self.assertEqual([i["name"] for i in s["items"]], ["巨乳", "单体"])
        self.assertEqual(s["items"][0], {"name": "巨乳", "count": 3})

    def test_low_share_filtered_out(self):
        """只占 1/3 的类型算偶发题材，不足以代表这个演员，不进参考。"""
        self.assertNotIn("企画", [i["name"] for i in actresses.suggest_tags(self.aid)["items"]])

    def test_two_thirds_boundary(self):
        """15 部作品：占 10 部（正好 2/3）的留下，占 9 部的砍掉。"""
        for i in range(15):
            genres = []
            if i < 10:
                genres.append("甲")
            if i < 9:
                genres.append("乙")
            entries.ingest_fetched({"url": f"https://a.com/BND-{i:03d}", "success": True,
                                    "details": [_detail("演员", "边界"),
                                                _detail("类型", ", ".join(genres))]})
        aid = [a["id"] for a in actresses.list_actresses()["actresses"]
               if a["name"] == "边界"][0]
        s = actresses.suggest_tags(aid)
        self.assertEqual(s["total"], 15)
        self.assertEqual([i["name"] for i in s["items"]], ["甲"])
        self.assertEqual(s["items"][0]["count"], 10)

    def test_usable_zero_without_genres(self):
        """有作品但一个类型都没抓到：分母仍是作品数，但没有可推荐的。"""
        entries.ingest_fetched({"url": "https://a.com/NONE-001", "success": True,
                                "details": [_detail("演员", "无类型")]})
        aid = [a["id"] for a in actresses.list_actresses()["actresses"]
               if a["name"] == "无类型"][0]
        self.assertEqual(actresses.suggest_tags(aid),
                         {"total": 1, "usable": 0, "items": []})

    def test_suggestions_empty_for_actress_without_entries(self):
        with db.conn() as conn:
            conn.execute("INSERT INTO actresses (name) VALUES ('孤零零')")
            new_id = conn.execute(
                "SELECT id FROM actresses WHERE name='孤零零'").fetchone()[0]
        self.assertEqual(actresses.suggest_tags(new_id),
                         {"total": 0, "usable": 0, "items": []})

    def test_nothing_picked_by_default(self):
        self.assertEqual(actresses.get_actress(self.aid)["tags"], [])

    def test_add_and_remove(self):
        res = actresses.add_tag(self.aid, "巨乳")
        self.assertEqual([t["name"] for t in res["tags"]], ["巨乳"])
        self.assertEqual([t["name"] for t in actresses.get_actress(self.aid)["tags"]],
                         ["巨乳"])
        tag_id = res["tags"][0]["id"]
        self.assertEqual(actresses.remove_tag(self.aid, tag_id)["tags"], [])

    def test_add_twice_keeps_one_row(self):
        actresses.add_tag(self.aid, "巨乳")
        actresses.add_tag(self.aid, "巨乳")
        with db.conn() as conn:
            n = conn.execute("SELECT count(*) FROM actress_tag_map").fetchone()[0]
        self.assertEqual(n, 1)

    def test_add_rejects_blank_and_unknown_actress(self):
        self.assertIsNone(actresses.add_tag(self.aid, "   "))
        self.assertIsNone(actresses.add_tag(999999, "巨乳"))

    def test_suggestions_still_list_picked_name(self):
        """参考列表本身不过滤已选（口径一致），由前端把已选的从参考里去掉。"""
        actresses.add_tag(self.aid, "巨乳")
        self.assertIn("巨乳", [i["name"] for i in actresses.suggest_tags(self.aid)["items"]])


if __name__ == "__main__":
    unittest.main()
