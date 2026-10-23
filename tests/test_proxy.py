"""proxy：全局代理 resolve 的缓存 / 切换 / 降级逻辑（mock，无网络）。"""

import unittest
from unittest import mock

from app import proxy


class TestResolve(unittest.TestCase):
    def setUp(self):
        proxy.configure("")  # 重置为直连

    def tearDown(self):
        proxy.configure("")

    def test_no_config_no_proxy_returns_direct(self):
        # 未手动配置 + 本机无可用代理 → 降级直连
        with mock.patch.object(proxy, "detect_local_ports", return_value=[]):
            self.assertEqual(proxy.resolve(force=True), "")

    def test_auto_detect_when_not_configured(self):
        # 未手动配置，但本机探测到可用代理 → 自动采用（自动检测）
        with mock.patch.object(proxy, "detect_local_ports", return_value=["http://127.0.0.1:7897"]):
            self.assertEqual(proxy.resolve(force=True), "http://127.0.0.1:7897")

    def test_configured_and_working(self):
        proxy.configure("http://127.0.0.1:7897")
        with mock.patch.object(proxy, "proxy_works", return_value=True):
            self.assertEqual(proxy.resolve(force=True), "http://127.0.0.1:7897")

    def test_configured_fail_then_switch_to_detected(self):
        proxy.configure("http://127.0.0.1:7897")
        with mock.patch.object(proxy, "proxy_works", return_value=False), \
             mock.patch.object(proxy, "detect_local_ports", return_value=["http://127.0.0.1:7899"]):
            self.assertEqual(proxy.resolve(force=True), "http://127.0.0.1:7899")

    def test_configured_fail_no_candidate_downgrades_to_direct(self):
        proxy.configure("http://127.0.0.1:7897")
        with mock.patch.object(proxy, "proxy_works", return_value=False), \
             mock.patch.object(proxy, "detect_local_ports", return_value=[]):
            self.assertEqual(proxy.resolve(force=True), "")

    def test_ttl_cache_avoids_repeat_check(self):
        proxy.configure("http://127.0.0.1:7897")
        with mock.patch.object(proxy, "proxy_works", return_value=True) as m:
            self.assertEqual(proxy.resolve(force=True), "http://127.0.0.1:7897")
            self.assertEqual(proxy.resolve(), "http://127.0.0.1:7897")
            self.assertEqual(proxy.resolve(), "http://127.0.0.1:7897")
            self.assertEqual(m.call_count, 1)  # 只探测一次，其余走缓存

    def test_force_bypasses_cache(self):
        proxy.configure("http://127.0.0.1:7897")
        with mock.patch.object(proxy, "proxy_works", return_value=True) as m:
            proxy.resolve(force=True)
            proxy.resolve(force=True)
            self.assertEqual(m.call_count, 2)

    def test_status_reflects_state(self):
        proxy.configure("http://127.0.0.1:7897")
        with mock.patch.object(proxy, "proxy_works", return_value=True):
            proxy.resolve(force=True)
        st = proxy.status()
        self.assertEqual(st["configured"], "http://127.0.0.1:7897")
        self.assertTrue(st["enabled"])
        self.assertTrue(st["available"])


if __name__ == "__main__":
    unittest.main()
