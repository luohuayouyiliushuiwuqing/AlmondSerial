"""db：切换后的库路径被记住并在重启时沿用，失效则回退默认库。"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app import db


class TestDbState(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._state_file = db.STATE_FILE
        self._db_path = db.DB_PATH
        db.STATE_FILE = self.dir / ".db_state.json"
        db.DB_PATH = db.DEFAULT_DB_PATH

    def tearDown(self):
        db.STATE_FILE = self._state_file
        db.DB_PATH = self._db_path
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_set_db_is_remembered_and_restored(self):
        moved = self.dir / "moved"
        moved.mkdir()
        (moved / "library.db").write_bytes(b"")
        db.set_db(moved)
        target = (moved / "library.db").resolve()
        self.assertEqual(db.current_path(), target)

        db.DB_PATH = db.DEFAULT_DB_PATH  # 模拟重启：内存里的指向丢掉
        self.assertEqual(db.restore_state(), target)
        self.assertEqual(db.current_path(), target)

    def test_restore_skips_missing_file(self):
        db.STATE_FILE.write_text(
            json.dumps({"db": str(self.dir / "gone" / "library.db")}), encoding="utf-8"
        )
        self.assertIsNone(db.restore_state())
        self.assertEqual(db.current_path(), db.DEFAULT_DB_PATH)

    def test_restore_without_state_file(self):
        self.assertIsNone(db.restore_state())
        self.assertEqual(db.current_path(), db.DEFAULT_DB_PATH)

    def _make_db(self, name):
        d = self.dir / name
        d.mkdir()
        f = d / "library.db"
        f.write_bytes(b"")
        return f

    def test_history_keeps_recent_dirs(self):
        a = self._make_db("a")
        b = self._make_db("b")
        db.set_db(a)
        db.set_db(b)
        self.assertEqual(db.history()[:2], [str(b.resolve()), str(a.resolve())])

        db.DB_PATH = a.resolve()  # 切回旧库，历史只重排不重复
        self.assertEqual(db.history()[:2], [str(a.resolve()), str(b.resolve())])
        self.assertEqual(len(db.history()), 2)

    def test_history_survives_restart(self):
        a = self._make_db("a")
        db.set_db(a)
        db.DB_PATH = db.DEFAULT_DB_PATH  # 模拟重启
        self.assertIn(str(a.resolve()), db.history())

    def test_history_is_capped(self):
        for i in range(db.HISTORY_MAX + 3):
            db.set_db(self._make_db(f"d{i}"))
        self.assertEqual(len(db.history()), db.HISTORY_MAX)


if __name__ == "__main__":
    unittest.main()
