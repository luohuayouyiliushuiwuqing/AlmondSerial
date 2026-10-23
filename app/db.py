"""SQLite 基础设施：连接 + schema（番号库）。

番号（code）是条目的唯一关键字；链接（entry_links.url）唯一，多条链接归并到同一番号。
每次操作独立短连接，WAL + timeout=10，线程安全。
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

# 数据库默认放在项目根的 data/ 下（运行时生成，可被环境变量覆盖）
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "library.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    code         TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    cover        TEXT NOT NULL DEFAULT '',
    release_date TEXT NOT NULL DEFAULT '',
    maker        TEXT NOT NULL DEFAULT '',
    series       TEXT NOT NULL DEFAULT '',
    duration     TEXT NOT NULL DEFAULT '',
    rating       TEXT NOT NULL DEFAULT '',
    director     TEXT NOT NULL DEFAULT '',
    label        TEXT NOT NULL DEFAULT '',
    actors       TEXT NOT NULL DEFAULT '[]',
    genres       TEXT NOT NULL DEFAULT '[]',
    details      TEXT NOT NULL DEFAULT '[]',
    fetched      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS entry_links (
    url        TEXT PRIMARY KEY,
    code       TEXT NOT NULL,
    domain     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_entry_links_code ON entry_links(code);
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS entry_tags (
    code   TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (code, tag_id)
);
"""

_ENSURE_LOCK = threading.Lock()


def _migrate(connection: sqlite3.Connection) -> None:
    """老库升级：给 entries 表补 director / label 列（新库建表已含，此处幂等）。"""
    cols = [r[1] for r in connection.execute("PRAGMA table_info(entries)")]
    for col in ("director", "label"):
        if col not in cols:
            connection.execute(
                f"ALTER TABLE entries ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
            )


@contextmanager
def conn():
    if not DB_PATH.parent.is_dir():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with _ENSURE_LOCK:
            connection.executescript(_SCHEMA)
            _migrate(connection)
            connection.execute("PRAGMA journal_mode=WAL")
        with connection:
            yield connection
    finally:
        connection.close()
