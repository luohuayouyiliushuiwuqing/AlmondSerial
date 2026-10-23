"""worker：同域名连续失败自动冻结（纯逻辑，无网络）。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import db, entries, worker


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        db.DB_PATH = self.dir / "test.db"
        self._reset()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        self._reset()

    def _reset(self):
        with worker._lock:
            worker._fail_counts.clear()
            worker._frozen.clear()


class TestAutoFreeze(Base):
    def test_freeze_after_consecutive_failures(self):
        for _ in range(worker.MAX_CONSECUTIVE_FAILURES - 1):
            worker._note_failure("x.com")
        self.assertNotIn("x.com", worker._frozen)
        self.assertEqual(entries.frozen_domains(), set())

        worker._note_failure("x.com")  # 第 5 次连续失败
        self.assertIn("x.com", worker._frozen)
        self.assertEqual(entries.frozen_domains(), {"x.com"})

    def test_success_resets_counter(self):
        for _ in range(worker.MAX_CONSECUTIVE_FAILURES - 1):
            worker._note_failure("x.com")
        worker._note_success("x.com")
        for _ in range(worker.MAX_CONSECUTIVE_FAILURES - 1):
            worker._note_failure("x.com")
        self.assertNotIn("x.com", worker._frozen)  # 未连续到阈值

    def test_freeze_only_logged_once(self):
        worker._note_failure("x.com")
        worker._note_failure("x.com")
        worker._note_failure("x.com")
        worker._note_failure("x.com")
        worker._note_failure("x.com")  # 冻结
        worker._note_failure("x.com")  # 已在 _frozen，不再触发
        self.assertEqual(entries.frozen_domains(), {"x.com"})


class TestFrozenDomains(Base):
    def test_persisted_roundtrip(self):
        self.assertTrue(entries.set_domain_frozen("y.com", True))
        self.assertEqual(entries.frozen_domains(), {"y.com"})
        self.assertTrue(entries.set_domain_frozen("y.com", False))
        self.assertEqual(entries.frozen_domains(), set())


if __name__ == "__main__":
    unittest.main()
