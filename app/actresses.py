"""演员与类型标签：名单（带作品数）、单个演员的作品列表、类型标签维护。

演员↔作品的对应关系由详情自动推导（entry_actress）：条目一抓完就同步进去，
所以新导入 / 新抓取的作品会立刻出现在演员列表里，不用手动跑脚本。
演员类型（标签）由用户自己点选，系统只按作品给出频次参考，绝不代填。
"""

import json
import sqlite3

from . import db, entries
from .attributes import actress_names
from .codes import normalize_code

# 参考标签一次最多返回这么多条，避免长尾把页面堆满
_SUGGEST_LIMIT = 60
# IN (...) 的变量个数：分块查询，避开 SQLite 的参数上限
_CODE_CHUNK = 400

# 已经确认过「存量条目已回填」的库，避免每次请求都去数一遍
_POPULATED: set[str] = set()


def _has(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


# ──────────────────────── 关联同步 ────────────────────────

def sync_entry(conn: sqlite3.Connection, code: str) -> list[str]:
    """把一个条目的演员关联同步进 actresses / entry_actress。

    复用调用方的连接，跟条目本身的写入待在同一个事务里。做法是「先清空该条目的
    旧关联、再按当前数据重建」，所以重抓、手改演员、删条目都能如实反映，也能反复调用。
    """
    code = normalize_code(code)
    if not code or not _has(conn, "actresses"):
        return []
    row = conn.execute(
        "SELECT details, actors FROM entries WHERE code = ?", (code,)).fetchone()
    conn.execute("DELETE FROM entry_actress WHERE code = ?", (code,))
    if row is None:
        return []
    try:
        details = json.loads(row["details"] or "[]")
    except (json.JSONDecodeError, TypeError):
        details = []
    names = actress_names(details if isinstance(details, list) else [])
    if not names:
        # 详情里没有演员行时，退回用手工编辑过的 actors 字段（走同一套清洗规则）
        try:
            stored = json.loads(row["actors"] or "[]")
        except (json.JSONDecodeError, TypeError):
            stored = []
        if isinstance(stored, list) and stored:
            names = actress_names([{
                "label": "演员",
                "value": ", ".join(str(s) for s in stored),
                "links": [],
            }])
    for name in names:
        conn.execute("INSERT OR IGNORE INTO actresses (name) VALUES (?)", (name,))
        aid = conn.execute(
            "SELECT id FROM actresses WHERE name = ?", (name,)).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO entry_actress (code, actress_id) VALUES (?, ?)",
            (code, aid))
    return names


def sync_all() -> dict:
    """全量重建关联：没同步过的老库第一次打开演员视图时补一次，之后靠 sync_entry 增量维护。"""
    with db.conn() as conn:
        if not _has(conn, "actresses"):
            return {"entries": 0, "links": 0}
        codes = [r[0] for r in conn.execute("SELECT code FROM entries")]
        for code in codes:
            sync_entry(conn, code)
        # 条目已经删掉、关联却还留着的行一起清掉
        conn.execute(
            "DELETE FROM entry_actress WHERE code NOT IN (SELECT code FROM entries)")
        links = conn.execute("SELECT count(*) FROM entry_actress").fetchone()[0]
    return {"entries": len(codes), "links": links}


def _ensure_populated() -> None:
    """演员表还是空的、库里却已经有条目 → 说明是没同步过的老库，补一次全量。

    补完之后演员表非空，就不会再来；库里确实没有演员时也只多两次 count。
    """
    key = str(db.DB_PATH)
    if key in _POPULATED:
        return
    with db.conn() as conn:
        if not _has(conn, "actresses"):
            _POPULATED.add(key)
            return
        if conn.execute("SELECT count(*) FROM actresses").fetchone()[0]:
            _POPULATED.add(key)
            return
        if not conn.execute("SELECT count(*) FROM entries").fetchone()[0]:
            _POPULATED.add(key)
            return
    sync_all()
    _POPULATED.add(key)


# ──────────────────────── 查询 ────────────────────────

def list_actresses(q: str = "", limit: int = 200, offset: int = 0) -> dict:
    """按名字搜索演员，带各自作品数；演员表还没建时 ready=False。

    只列有作品的演员：演员名单是从条目详情推导出来的，重抓后作品全没了的名字
    会留在 actresses 表里（可能已经打过类型，不能删），但不该占着列表位置。
    """
    q = (q or "").strip()
    _ensure_populated()
    with db.conn() as conn:
        if not _has(conn, "actresses"):
            return {"actresses": [], "total": 0, "ready": False}
        where, params = "", []
        if q:
            where = "WHERE a.name LIKE ?"
            params = [f"%{q}%"]
        joined = ("FROM actresses a JOIN entry_actress ea ON ea.actress_id = a.id "
                  f"{where}")
        total = conn.execute(
            f"SELECT count(*) FROM (SELECT a.id {joined} GROUP BY a.id)",
            params).fetchone()[0]
        rows = conn.execute(
            f"SELECT a.id, a.name, count(ea.code) AS entries {joined} GROUP BY a.id "
            "ORDER BY entries DESC, a.name LIMIT ? OFFSET ?",
            params + [max(1, limit), max(0, offset)]).fetchall()
        return {
            "actresses": [dict(r) for r in rows],
            "total": total,
            "ready": True,
        }


def _tags_of(conn: sqlite3.Connection, actress_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT t.id, t.name FROM actress_tag_map m "
        "JOIN actress_tags t ON t.id = m.tag_id "
        "WHERE m.actress_id = ? ORDER BY t.id", (actress_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def _codes_of(conn: sqlite3.Connection, actress_id: int) -> list[str]:
    if not _has(conn, "entry_actress"):
        return []
    return [r[0] for r in conn.execute(
        "SELECT code FROM entry_actress WHERE actress_id = ?", (actress_id,))]


def _suggest(conn: sqlite3.Connection, actress_id: int) -> dict:
    """该演员各部作品的「类型」出现频次，给用户打标签时做参考。

    只推荐**至少占全部作品 2/3** 的类型：占比更低的多半是某几部片的偶发题材，
    不足以代表这个演员，宁可不推荐。分母是该演员的全部作品数（没抓到类型的
    作品也算在内，它们同样说明「这个类型不是每部都有」）。
    `9/12` 就是 12 部作品里有 9 部带这个类型。
    """
    codes = _codes_of(conn, actress_id)
    total = len(codes)
    counts: dict[str, int] = {}
    usable = 0
    for i in range(0, len(codes), _CODE_CHUNK):
        chunk = codes[i:i + _CODE_CHUNK]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
                f"SELECT genres FROM entries WHERE code IN ({marks})", chunk):
            try:
                genres = json.loads(row[0] or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(genres, list) or not genres:
                continue
            usable += 1
            for g in {str(x).strip() for x in genres if str(x).strip()}:
                counts[g] = counts.get(g, 0) + 1
    # count / total >= 2/3 的整数写法，避免浮点误差
    keep = [(n, c) for n, c in counts.items() if total and c * 3 >= total * 2]
    keep.sort(key=lambda kv: (-kv[1], kv[0]))
    return {"total": total, "usable": usable,
            "items": [{"name": n, "count": c} for n, c in keep[:_SUGGEST_LIMIT]]}


def suggest_tags(actress_id: int) -> dict:
    with db.conn() as conn:
        return _suggest(conn, actress_id)


def get_actress(actress_id: int) -> dict | None:
    """演员本身 + 他/她的全部作品（按录入时间从近到远）+ 已选类型 + 类型参考。"""
    _ensure_populated()
    with db.conn() as conn:
        if not _has(conn, "actresses"):
            return None
        row = conn.execute(
            "SELECT id, name FROM actresses WHERE id = ?", (actress_id,)).fetchone()
        if row is None:
            return None
        codes = _codes_of(conn, actress_id)
        tags = _tags_of(conn, actress_id)
        suggest = _suggest(conn, actress_id)
    items = entries.list_by_codes(codes)
    items.sort(key=lambda e: (e.get("created_at") or ""), reverse=True)
    return {"id": row["id"], "name": row["name"],
            "total": len(items), "entries": items,
            "tags": tags, "suggestions": suggest}


# ──────────────────────── 类型标签（用户点选） ────────────────────────

def _tag_payload(conn: sqlite3.Connection, actress_id: int) -> dict:
    return {"id": actress_id,
            "tags": _tags_of(conn, actress_id),
            "suggestions": _suggest(conn, actress_id)}


def add_tag(actress_id: int, name: str) -> dict | None:
    """给演员打一个类型；名字第一次出现时自动进字典表。演员不存在返回 None。"""
    name = (name or "").strip()
    if not name:
        return None
    with db.conn() as conn:
        if conn.execute(
                "SELECT 1 FROM actresses WHERE id = ?", (actress_id,)).fetchone() is None:
            return None
        conn.execute("INSERT OR IGNORE INTO actress_tags (name) VALUES (?)", (name,))
        tag_id = conn.execute(
            "SELECT id FROM actress_tags WHERE name = ?", (name,)).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO actress_tag_map (actress_id, tag_id) VALUES (?, ?)",
            (actress_id, tag_id))
        return _tag_payload(conn, actress_id)


def remove_tag(actress_id: int, tag_id: int) -> dict | None:
    with db.conn() as conn:
        if conn.execute(
                "SELECT 1 FROM actresses WHERE id = ?", (actress_id,)).fetchone() is None:
            return None
        conn.execute(
            "DELETE FROM actress_tag_map WHERE actress_id = ? AND tag_id = ?",
            (actress_id, tag_id))
        return _tag_payload(conn, actress_id)
