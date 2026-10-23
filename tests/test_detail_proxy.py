"""detail_proxy：代理查找的正常 / 异常 / 超时 / 格式错误处理（mock，无网络）。"""

import json
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from app import detail_proxy


class FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestLookup(unittest.TestCase):
    def setUp(self):
        detail_proxy.BASE_URL = "http://127.0.0.1:9000"

    def tearDown(self):
        detail_proxy.BASE_URL = ""

    def test_lookup_ok(self):
        data = {"title": "标题", "actors": ["甲", "乙"], "genres": ["剧情"],
                "release_date": "2020-01-01"}
        body = json.dumps({"ok": True, "data": data}).encode("utf-8")
        with mock.patch.object(detail_proxy, "urlopen", return_value=FakeResp(body)):
            out = detail_proxy.lookup("ABP-123")
        self.assertEqual(out["title"], "标题")
        self.assertEqual(out["actors"], ["甲", "乙"])
        self.assertEqual(out["release_date"], "2020-01-01")

    def test_lookup_disabled(self):
        detail_proxy.BASE_URL = ""
        self.assertIsNone(detail_proxy.lookup("ABP-123"))

    def test_lookup_http_error(self):
        exc = HTTPError("http://x", 500, "Internal Server Error", {}, None)
        with mock.patch.object(detail_proxy, "urlopen", side_effect=exc):
            self.assertIsNone(detail_proxy.lookup("ABP-123"))

    def test_lookup_unreachable(self):
        with mock.patch.object(detail_proxy, "urlopen", side_effect=URLError("connection refused")):
            self.assertIsNone(detail_proxy.lookup("ABP-123"))

    def test_lookup_timeout(self):
        with mock.patch.object(detail_proxy, "urlopen", side_effect=TimeoutError()):
            self.assertIsNone(detail_proxy.lookup("ABP-123"))

    def test_lookup_invalid_json(self):
        with mock.patch.object(detail_proxy, "urlopen", return_value=FakeResp(b"not json")):
            self.assertIsNone(detail_proxy.lookup("ABP-123"))

    def test_lookup_bad_structure(self):
        body = json.dumps({"ok": True, "data": "not-a-dict"}).encode()
        with mock.patch.object(detail_proxy, "urlopen", return_value=FakeResp(body)):
            self.assertIsNone(detail_proxy.lookup("ABP-123"))

    def test_normalize_filters_bad_fields(self):
        out = detail_proxy._normalize({
            "actors": ["a", "", 123], "genres": "not-a-list",
            "title": "  T  ", "details": [{"label": "x"}, "bad"],
        })
        self.assertEqual(out["actors"], ["a"])
        self.assertEqual(out["genres"], [])
        self.assertEqual(out["title"], "T")
        self.assertEqual(out["details"], [{"label": "x"}])


if __name__ == "__main__":
    unittest.main()
