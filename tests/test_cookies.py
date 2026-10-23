"""cookies：站点 Cookie 注入（域名匹配 + header 解析）。"""

import unittest
from unittest import mock

from app import cookies


def _reset():
    cookies._COOKIES.clear()
    cookies._loaded = False


class TestParseCookieHeader(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(cookies.parse_cookie_header("a=1; b=2"), {"a": "1", "b": "2"})

    def test_parse_skip_invalid(self):
        self.assertEqual(cookies.parse_cookie_header("a=1; nokv; c=3"), {"a": "1", "c": "3"})


class TestGetCookie(unittest.TestCase):
    def tearDown(self):
        _reset()

    def test_domain_match(self):
        with mock.patch.dict("os.environ", {"SITE_COOKIES": '{"fourhoi.com": "cf=1; foo=2"}'}):
            _reset()
            self.assertEqual(cookies.get_cookie("https://fourhoi.com/x"), {"cf": "1", "foo": "2"})

    def test_subdomain_match(self):
        with mock.patch.dict("os.environ", {"SITE_COOKIES": '{"fourhoi.com": "cf=1"}'}):
            _reset()
            self.assertEqual(cookies.get_cookie("https://a.fourhoi.com/x"), {"cf": "1"})

    def test_no_match(self):
        with mock.patch.dict("os.environ", {"SITE_COOKIES": '{"other.com": "cf=1"}'}):
            _reset()
            self.assertIsNone(cookies.get_cookie("https://fourhoi.com/x"))

    def test_header(self):
        with mock.patch.dict("os.environ", {"SITE_COOKIES": '{"fourhoi.com": "cf=1; foo=2"}'}):
            _reset()
            self.assertEqual(cookies.get_cookie_header("https://fourhoi.com/x"), "cf=1; foo=2")

    def test_no_config_returns_none(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            _reset()
            self.assertIsNone(cookies.get_cookie("https://fourhoi.com/x"))


if __name__ == "__main__":
    unittest.main()
