"""演员查询：名单（带作品数）+ 单个演员的作品列表。

演员与作品的对应关系由详情自动推导（entry_actress），只读不写；
演员标签由用户自己指定，本模块不碰 actress_tag_map。
"""

from . import db, entries


def _has(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def list_actresses(q: str = "", limit: int = 200, offset: int = 0) -> dict:
    """按名字搜索演员，带各自作品数；演员表还没建时 ready=False。"""
    q = (q or "").strip()
    with db.conn() as conn:
        if not _has(conn, "actresses"):
            return {"actresses": [], "total": 0, "ready": False}
        where, params = "", []
        if q:
            where = "WHERE a.name LIKE ?"
            params = [f"%{q}%"]
        total = conn.execute(
            f"SELECT count(*) FROM actresses a {where}", params).fetchone()[0]
        rows = conn.execute(
            "SELECT a.id, a.name, count(ea.code) AS entries "
            "FROM actresses a LEFT JOIN entry_actress ea ON ea.actress_id = a.id "
            f"{where} GROUP BY a.id ORDER BY entries DESC, a.name LIMIT ? OFFSET ?",
            params + [max(1, limit), max(0, offset)]).fetchall()
        return {
            "actresses": [dict(r) for r in rows],
            "total": total,
            "ready": True,
        }


def get_actress(actress_id: int) -> dict | None:
    """演员本身 + 他/她的全部作品（按录入时间从近到远）。"""
    with db.conn() as conn:
        if not _has(conn, "actresses"):
            return None
        row = conn.execute(
            "SELECT id, name FROM actresses WHERE id = ?", (actress_id,)).fetchone()
        if row is None:
            return None
        codes = []
        if _has(conn, "entry_actress"):
            codes = [r[0] for r in conn.execute(
                "SELECT code FROM entry_actress WHERE actress_id = ?", (actress_id,))]
    items = entries.list_by_codes(codes)
    items.sort(key=lambda e: (e.get("created_at") or ""), reverse=True)
    return {"id": row["id"], "name": row["name"],
            "total": len(items), "entries": items}
