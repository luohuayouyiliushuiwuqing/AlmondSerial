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

# 运行时切换数据库时保护 DB_PATH 的读写（切换与每次建连之间串行化）
_PATH_LOCK = threading.Lock()


def set_db(path) -> Path:
    """切换到指定数据库，返回实际使用的库文件路径。

    - path 是**目录**（存在且为目录）→ 使用该目录下的 ``library.db``；
    - 否则把 path 当作 ``.db`` **文件路径**直接使用（父目录不存在会自动创建）。
    """
    global DB_PATH
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / "library.db"
    with _PATH_LOCK:
        DB_PATH = p
    return p


def list_dbs(directory) -> list[str]:
    """列出目录下所有 ``.db`` 文件名（按名称排序）；目录不存在返回空列表。"""
    d = Path(directory).expanduser()
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.glob("*.db") if f.is_file())

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
    with _PATH_LOCK:
        path = DB_PATH
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
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
