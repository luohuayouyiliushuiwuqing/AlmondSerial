"""SQLite 基础设施：连接 + schema（番号库）。

番号（code）是条目的唯一关键字；链接（entry_links.url）唯一，多条链接归并到同一番号。
每次操作独立短连接，WAL + timeout=10，线程安全。
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

PROJECT_DIR = Path(__file__).resolve().parent.parent

# 数据库默认放在项目根的 data/ 下（运行时生成，可被环境变量覆盖）
DEFAULT_DB_PATH = PROJECT_DIR / "data" / "library.db"
DB_PATH = DEFAULT_DB_PATH

# 记住上次切换到的库 + 用过的库历史，重启后沿用并可一键切换回来
STATE_FILE = PROJECT_DIR / ".db_state.json"
HISTORY_MAX = 8

# 运行时切换数据库时保护 DB_PATH 的读写（切换与每次建连之间串行化）
_PATH_LOCK = threading.Lock()


def current_path() -> Path:
    """当前使用的数据库文件路径（图片缓存目录等据此定位，随库走）。"""
    with _PATH_LOCK:
        return DB_PATH


def _read_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(**patch) -> None:
    data = _read_state()
    data.update(patch)
    try:
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        logger.warning(f"数据库状态记忆写入失败：{exc}")


def history() -> list[str]:
    """用过的数据库路径（当前库排最前，去重，最多 HISTORY_MAX 条）。"""
    saved = [str(x) for x in (_read_state().get("history") or []) if str(x).strip()]
    cur = str(current_path())
    items = [cur] + [p for p in saved if p != cur]
    return items[:HISTORY_MAX]


def _remember(path: Path) -> None:
    """把当前库记为「上次使用」并推到历史最前。"""
    prev = [str(x) for x in (_read_state().get("history") or []) if str(x).strip()]
    rest = [p for p in prev if p != str(path)][: HISTORY_MAX - 1]
    _write_state(db=str(path), history=[str(path)] + rest)


def restore_state() -> Path | None:
    """启动时恢复上次使用的库并记入历史；记下的路径已失效则用默认库（返回 None）。"""
    global DB_PATH
    restored = None
    saved = str(_read_state().get("db") or "").strip()
    if saved:
        p = Path(saved).expanduser()
        if p.is_file():
            with _PATH_LOCK:
                DB_PATH = p
            restored = p
        else:
            logger.warning(f"上次使用的数据库已不存在，回退到默认库 {DEFAULT_DB_PATH}：{p}")
    _remember(current_path())
    return restored


def set_db(path) -> Path:
    """切换到指定数据库，返回实际使用的库文件路径。

    - path 是**目录**（存在且为目录）→ 使用该目录下的 ``library.db``；
    - 否则把 path 当作 ``.db`` **文件路径**直接使用（父目录不存在会自动创建）。

    切换结果会记到 ``.db_state.json``（当前库 + 历史），重启后沿用。
    """
    global DB_PATH
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / "library.db"
    p = p.resolve()
    with _PATH_LOCK:
        DB_PATH = p
    _remember(p)
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
CREATE TABLE IF NOT EXISTS domain_state (
    domain      TEXT PRIMARY KEY,
    frozen      INTEGER NOT NULL DEFAULT 0,
    replacement TEXT NOT NULL DEFAULT '',
    ok          INTEGER NOT NULL DEFAULT 1,
    checked_at  TEXT NOT NULL DEFAULT ''
);
"""

# 演员相关的表：脚本 build_actresses.py 直接复用这段 DDL，保证与线上一致
ACTRESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS actresses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
-- 演员标签字典：候选池来自各条目详情的「类型」，具体给谁打由人指定
CREATE TABLE IF NOT EXISTS actress_tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
-- 演员 ↔ 标签（多对多）
CREATE TABLE IF NOT EXISTS actress_tag_map (
    actress_id INTEGER NOT NULL,
    tag_id     INTEGER NOT NULL,
    PRIMARY KEY (actress_id, tag_id)
);
-- 条目 ↔ 演员（多对多）：由详情里的演员数据自动推导，页面只读
CREATE TABLE IF NOT EXISTS entry_actress (
    code       TEXT NOT NULL,
    actress_id INTEGER NOT NULL,
    PRIMARY KEY (code, actress_id)
);
CREATE INDEX IF NOT EXISTS idx_entry_actress_actress ON entry_actress(actress_id);
"""

_SCHEMA = _SCHEMA + ACTRESS_SCHEMA

_ENSURE_LOCK = threading.Lock()
_ENSURED: set[str] = set()


def _migrate(connection: sqlite3.Connection) -> None:
    """老库升级：给 entries 表补 director / label 列（新库建表已含，此处幂等）。"""
    cols = [r[1] for r in connection.execute("PRAGMA table_info(entries)")]
    for col in ("director", "label"):
        if col not in cols:
            connection.execute(
                f"ALTER TABLE entries ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
            )


def _ensure(connection: sqlite3.Connection, path: Path) -> None:
    """每个数据库文件只初始化一次（建表 + 迁移 + WAL）。

    高并发下大量连接若每次都对已建好的库重跑建表脚本，会在全局锁里串行化；
    这里用已初始化集合跳过重复执行，锁只用于保护首次初始化。
    """
    key = str(path.resolve())
    with _ENSURE_LOCK:
        if key in _ENSURED:
            return
        connection.executescript(_SCHEMA)
        _migrate(connection)
        connection.execute("PRAGMA journal_mode=WAL")
        _ENSURED.add(key)


@contextmanager
def conn():
    with _PATH_LOCK:
        path = DB_PATH
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        _ensure(connection, path)
        with connection:
            yield connection
    finally:
        connection.close()
