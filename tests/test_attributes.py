"""attributes：详情行 → 类型化字段（纯逻辑、无网络）。"""

import unittest

from app.attributes import classify, link_kind


def _field(label, value="", **kw):
    row = {"label": label, "value": value, "links": [], "datetime": ""}
    row.update(kw)
    return row


class TestClassify(unittest.TestCase):
    def test_typed_fields(self):
        details = [
            _field("发行日期：", "2020-01-01"),
            _field("番号：", "ABP-123"),
            _field("演员：", "苍井空, 三上悠亜"),
            _field("类型：", "剧情 / 恋爱"),
            _field("片商：", "某片商"),
            _field("系列：", "某系列"),
            _field("时长：", "120分钟"),
            _field("评分：", "4.5"),
        ]
        out = classify(details)
        self.assertEqual(out["release_date"], "2020-01-01")
        self.assertEqual(out["code"], "ABP-123")
        self.assertEqual(out["actors"], ["苍井空", "三上悠亜"])
        self.assertEqual(out["genres"], ["剧情", "恋爱"])
        self.assertEqual(out["maker"], "某片商")
        self.assertEqual(out["series"], "某系列")
        self.assertEqual(out["duration"], "120分钟")
        self.assertEqual(out["rating"], "4.5")

    def test_date_from_datetime_attr(self):
        out = classify([_field("发行日期", datetime="2020-01-01T00:00:00")])
        self.assertEqual(out["release_date"], "2020-01-01")

    def test_date_variants(self):
        for v in ("2020.01.02", "2020/01/02", "2020年1月2日"):
            self.assertEqual(classify([_field("发行日期", v)])["release_date"], "2020-01-02")

    def test_unrecognized_labels_ignored(self):
        out = classify([_field("简介", "一段文字")])
        self.assertEqual(out["release_date"], "")
        self.assertEqual(out["actors"], [])
        self.assertEqual(out["code"], "")

    def test_first_hit_wins(self):
        out = classify([_field("片商", "甲"), _field("片商", "乙")])
        self.assertEqual(out["maker"], "甲")

    def test_tag_label_plain_text_is_genres(self):
        # 部分站点没有「类型」，只有「标签/標籤」纯文本 → 归为类型
        out = classify([_field("标签", "剧情 / 恋爱")])
        self.assertEqual(out["genres"], ["剧情", "恋爱"])
        self.assertEqual(out["label"], "")

    def test_traditional_tag_label_plain_text_is_genres(self):
        out = classify([_field("標籤", "巨乳, 単体")])
        self.assertEqual(out["genres"], ["巨乳", "単体"])
        self.assertEqual(out["label"], "")

    def test_tag_label_with_label_link_is_label(self):
        # 「标签」但链接指向 /cn/labels/ → 仍是厂牌，不误判为类型
        out = classify([_field("标签", "", links=[{"text": "某厂牌", "href": "https://x.com/cn/labels/1"}])])
        self.assertEqual(out["label"], "某厂牌")
        self.assertEqual(out["genres"], [])


class TestLinkValues(unittest.TestCase):
    def test_real_structure_link_values(self):
        # 模拟真实站点结构：演员/类型/发行商/导演/标籤 的值都在 <a> 链接文字里
        details = [
            {"label": "发行日期", "value": "", "datetime": "2026-10-02T00:00:00+08:00", "links": []},
            {"label": "番号", "value": "XXXX-123", "datetime": "", "links": []},
            {"label": "演员", "value": "", "datetime": "",
             "links": [{"text": "演员甲", "href": "https://x.com/dm72/cn/actresses/1"}]},
            {"label": "类型", "value": "", "datetime": "",
             "links": [
                 {"text": "剧情", "href": "https://x.com/dm206/cn/genres/1"},
                 {"text": "恋爱", "href": "https://x.com/dm127/cn/genres/2"},
             ]},
            {"label": "发行商", "value": "", "datetime": "",
             "links": [{"text": "某片商", "href": "https://x.com/dm825/cn/makers/1"}]},
            {"label": "导演", "value": "", "datetime": "",
             "links": [{"text": "某导演", "href": "https://x.com/cn/directors/1"}]},
            {"label": "标籤", "value": "", "datetime": "",
             "links": [{"text": "某厂牌", "href": "https://x.com/dm26/cn/labels/1"}]},
        ]
        out = classify(details)
        self.assertEqual(out["release_date"], "2026-10-02")
        self.assertEqual(out["code"], "XXXX-123")
        self.assertEqual(out["actors"], ["演员甲"])
        self.assertEqual(out["genres"], ["剧情", "恋爱"])
        self.assertEqual(out["maker"], "某片商")
        self.assertEqual(out["director"], "某导演")
        self.assertEqual(out["label"], "某厂牌")


class TestLinkKind(unittest.TestCase):
    def test_dm_prefix_wildcard_same_kind(self):
        # dm549 / dm206 等装饰段不同，但 /cn/genres/ 相同 → 认定为同一作用
        self.assertEqual(link_kind("https://x.com/dm549/cn/genres/1"), "genre")
        self.assertEqual(link_kind("https://x.com/dm206/cn/genres/2"), "genre")
        self.assertEqual(link_kind("https://x.com/dm72/cn/actresses/1"), "actor")
        self.assertEqual(link_kind("https://x.com/dm825/cn/makers/1"), "maker")
        self.assertEqual(link_kind("https://x.com/cn/directors/1"), "director")
        self.assertEqual(link_kind("https://x.com/dm26/cn/labels/1"), "label")

    def test_unknown_kind(self):
        self.assertIsNone(link_kind("https://x.com/other/path"))
        self.assertIsNone(link_kind(""))


if __name__ == "__main__":
    unittest.main()
