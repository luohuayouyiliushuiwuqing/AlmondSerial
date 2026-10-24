"""worker：同域名连续失败自动冻结 + 队列按 URL 去重（纯逻辑，无网络）。"""

import queue
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


class TestQueueDedupe(Base):
    def setUp(self):
        super().setUp()
        self._drain()

    def tearDown(self):
        self._drain()
        super().tearDown()

    def _drain(self):
        with worker._lock:
            worker._queued.clear()
        while True:
            try:
                worker._queue.get_nowait()
            except queue.Empty:
                break
            else:
                worker._queue.task_done()

    def test_same_url_enqueued_once(self):
        worker.enqueue("https://a.com/ABP-100")
        worker.enqueue("https://a.com/ABP-100")  # 重复导入/重启重试不重复抓
        self.assertEqual(worker._queue.qsize(), 1)
        self.assertEqual(worker._queued, {"https://a.com/ABP-100"})

    def test_forget_allows_reenqueue(self):
        url = "https://a.com/ABP-200"
        worker.enqueue(url)
        worker._queue.get_nowait()
        worker._queue.task_done()
        worker._forget(url)  # 模拟处理完成
        worker.enqueue(url)
        self.assertEqual(worker._queue.qsize(), 1)


class TestProgressStatus(Base):
    """底部进度条用的实时状态：本批总数 / 已完成 / 正在抓什么。"""

    def setUp(self):
        super().setUp()
        self._drain()

    def tearDown(self):
        self._drain()
        super().tearDown()

    def _drain(self):
        with worker._lock:
            worker._queued.clear()
            worker._active.clear()
            worker._burst_total = 0
            worker._burst_done = 0
        while True:
            try:
                worker._queue.get_nowait()
            except queue.Empty:
                break
            else:
                worker._queue.task_done()

    def test_enqueue_counts_progress(self):
        worker.enqueue("https://a.com/1")
        worker.enqueue("https://a.com/2")

        st = worker.status()

        self.assertEqual(st["total"], 2)
        self.assertEqual(st["done"], 0)
        self.assertEqual(st["remaining"], 2)
        self.assertEqual(st["percent"], 0.0)
        self.assertEqual(st["active"], [])

    def test_duplicate_enqueue_not_counted_twice(self):
        worker.enqueue("https://a.com/1")
        worker.enqueue("https://a.com/1")

        st = worker.status()

        self.assertEqual(st["total"], 1)
        self.assertEqual(st["remaining"], 1)

    def test_new_batch_resets_progress(self):
        worker.enqueue("https://a.com/1")
        worker._queue.get_nowait()
        worker._queue.task_done()
        with worker._lock:
            worker._burst_done = 1

        self.assertEqual(worker.status()["percent"], 100.0)

        worker.enqueue("https://a.com/2")  # 队列空闲后再次入队 → 新批次
        st = worker.status()
        self.assertEqual(st["total"], 1)
        self.assertEqual(st["done"], 0)
        self.assertEqual(st["percent"], 0.0)

    def test_status_includes_repair_activity(self):
        from app import repair
        repair._begin("page", "https://a.com/ABP-100")
        try:
            active = worker.status()["active"]
        finally:
            repair._end()

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["kind"], "page")
        self.assertEqual(active[0]["domain"], "a.com")
        self.assertIsNone(repair.status())


if __name__ == "__main__":
    unittest.main()
