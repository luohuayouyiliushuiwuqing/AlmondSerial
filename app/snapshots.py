"""网页快照：浏览器插件「抓取全部标签页」时的留档层。

与番号库的关系：能识别出番号的页面照旧走 ``entries.ingest_fetched`` 生成/合并条目；
识别不出的页面也不丢，一律按 url 落一份快照（同一 url 只留最近一次，重抓即覆盖）。
"""

import json
from urllib.parse import urlparse

from . import db, entries


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _norm_page(page: dict) -> dict | None:
    """规整插件传来的一页：非法 url 返回 None（计入跳过）。"""
    url = (page.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    thumbnail = (page.get("thumbnail") or "").strip()
    if not thumbnail.startswith(("http://", "https://")):
        thumbnail = ""
    return {
        "url": url,
        "title": (page.get("title") or "").strip(),
        "thumbnail": thumbnail,
        "details": page.get("details") or [],
    }


def save_batch(pages: list[dict]) -> dict:
    """批量写入快照，返回 ``{total, saved, entries, unrecognized, skipped, items}``。

    ``items`` 与传入顺序一致，供插件逐条回显（哪页识别成了哪个番号）。
    """
    rows: list[tuple] = []
    items: list[dict] = []
    skipped = 0

    for page in pages or []:
        norm = _norm_page(page if isinstance(page, dict) else {})
        if norm is None:
            skipped += 1
            continue
        # 先入库/合并番号条目（内部自带连接），再统一收集快照行，避免长事务互相等锁
        e = entries.ingest_fetched({
            "url": norm["url"], "title": norm["title"],
            "thumbnail": norm["thumbnail"], "details": norm["details"], "success": True,
        })
        code = (e or {}).get("code", "")
        rows.append((
            norm["url"], norm["title"], norm["thumbnail"], _domain(norm["url"]), code,
            json.dumps(norm["details"], ensure_ascii=False),
        ))
        items.append({"url": norm["url"], "title": norm["title"], "code": code})

    if rows:
        with db.conn() as conn:
            conn.executemany(
                "INSERT INTO page_snapshots (url, title, thumbnail, domain, code, details, captured_at) "
                "VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime')) "
                "ON CONFLICT(url) DO UPDATE SET title=excluded.title, thumbnail=excluded.thumbnail, "
                "domain=excluded.domain, code=excluded.code, details=excluded.details, "
                "captured_at=excluded.captured_at",
                rows,
            )

    return {
        "total": len(pages or []),
        "saved": len(rows),
        "entries": sum(1 for i in items if i["code"]),
        "unrecognized": sum(1 for i in items if not i["code"]),
        "skipped": skipped,
        "items": items,
    }


def _row_to_snapshot(row) -> dict:
    try:
        details = json.loads(row["details"] or "[]")
    except ValueError:
        details = []
    return {
        "url": row["url"],
        "title": row["title"],
        "thumbnail": row["thumbnail"],
        "domain": row["domain"],
        "code": row["code"],
        "details": details,
        "captured_at": row["captured_at"],
    }


def list_snapshots(limit: int = 200, offset: int = 0, q: str = "") -> list[dict]:
    """快照列表，按最近抓取倒序；q 匹配 url / 标题 / 番号 / 域名。"""
    sql = "SELECT * FROM page_snapshots"
    args: list = []
    if q.strip():
        like = f"%{q.strip()}%"
        sql += " WHERE url LIKE ? OR title LIKE ? OR code LIKE ? OR domain LIKE ?"
        args = [like] * 4
    sql += " ORDER BY captured_at DESC, rowid DESC LIMIT ? OFFSET ?"
    args += [max(0, limit), max(0, offset)]
    with db.conn() as conn:
        return [_row_to_snapshot(r) for r in conn.execute(sql, args).fetchall()]


def count_snapshots(q: str = "") -> int:
    sql = "SELECT COUNT(*) AS n FROM page_snapshots"
    args: list = []
    if q.strip():
        like = f"%{q.strip()}%"
        sql += " WHERE url LIKE ? OR title LIKE ? OR code LIKE ? OR domain LIKE ?"
        args = [like] * 4
    with db.conn() as conn:
        return int(conn.execute(sql, args).fetchone()["n"])


def delete_snapshot(url: str) -> bool:
    with db.conn() as conn:
        cur = conn.execute("DELETE FROM page_snapshots WHERE url = ?", ((url or "").strip(),))
        return cur.rowcount > 0
