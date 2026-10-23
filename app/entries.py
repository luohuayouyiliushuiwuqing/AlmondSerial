"""条目存储：番号（code）为唯一关键字 + 多链接关联 + 标签。

合并规则：抓取失败不覆盖已有字段、空标题/空字段不覆盖、抓取置 fetched=1（「尝试过」）。
"""

import json
import sqlite3
from urllib.parse import urlparse

from . import db, detail_proxy
from .attributes import classify, field_links
from .codes import extract_code, normalize_code


def _root_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _entry_tags(conn: sqlite3.Connection, code: str) -> list[dict]:
    rows = conn.execute(
        "SELECT t.id, t.name FROM tags t JOIN entry_tags et ON et.tag_id = t.id "
        "WHERE et.code = ? ORDER BY t.name", (code,)).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def _links_for(conn: sqlite3.Connection, code: str) -> list[str]:
    rows = conn.execute("SELECT url FROM entry_links WHERE code = ? ORDER BY rowid", (code,)).fetchall()
    return [r["url"] for r in rows]


def _row_to_entry(row: sqlite3.Row, links: list[str], tags: list[dict]) -> dict:
    details = json.loads(row["details"] or "[]")
    return {
        "code": row["code"], "title": row["title"], "cover": row["cover"],
        "release_date": row["release_date"], "maker": row["maker"],
        "director": row["director"], "label": row["label"],
        "series": row["series"], "duration": row["duration"], "rating": row["rating"],
        "actors": json.loads(row["actors"] or "[]"),
        "genres": json.loads(row["genres"] or "[]"),
        "details": details,
        "field_links": field_links(details),
        "fetched": bool(row["fetched"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "links": links, "tags": tags,
    }


def count_entries() -> int:
    with db.conn() as conn:
        return conn.execute("SELECT count(*) FROM entries").fetchone()[0]


def list_entries(limit: int | None = None, offset: int = 0, q: str = "") -> list[dict]:
    """全部条目（或分页片段），按创建时间从近到远；q 命中 code/标题/片商/系列/演员/类型。"""
    q = (q or "").strip()
    where = ""
    params: list = []
    if q:
        like = f"%{q}%"
        where = ("WHERE code LIKE ? OR title LIKE ? OR maker LIKE ? OR director LIKE ? "
                 "OR label LIKE ? OR series LIKE ? OR release_date LIKE ? "
                 "OR actors LIKE ? OR genres LIKE ?")
        params = [like] * 9
    order = "ORDER BY created_at DESC, code DESC"
    with db.conn() as conn:
        if limit is None:
            rows = conn.execute(f"SELECT * FROM entries {where} {order}", params).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM entries {where} {order} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [_row_to_entry(r, _links_for(conn, r["code"]), _entry_tags(conn, r["code"]))
                for r in rows]


def get_entry(code: str) -> dict | None:
    code = normalize_code(code)
    if not code:
        return None
    with db.conn() as conn:
        row = conn.execute("SELECT * FROM entries WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None
        return _row_to_entry(row, _links_for(conn, code), _entry_tags(conn, code))


def import_links(urls: list[str]) -> dict:
    """链接导入：从每个链接提取番号，建条目 + 挂链接（不联网）。"""
    urls = [u.strip() for u in (urls or []) if isinstance(u, str) and u.strip()]
    inserted = existing = 0
    unrecognized: list[str] = []
    with db.conn() as conn:
        for url in urls:
            code = extract_code(url)
            if not code:
                unrecognized.append(url)
                continue
            is_new = conn.execute("SELECT 1 FROM entries WHERE code = ?", (code,)).fetchone() is None
            conn.execute("INSERT OR IGNORE INTO entries (code, fetched) VALUES (?, 0)", (code,))
            conn.execute(
                "INSERT OR IGNORE INTO entry_links (url, code, domain) VALUES (?, ?, ?)",
                (url, code, _root_domain(url)),
            )
            if is_new:
                inserted += 1
            else:
                existing += 1
    return {"inserted": inserted, "existing": existing, "unrecognized": unrecognized}


def ingest_fetched(meta: dict) -> dict | None:
    """抓取结果回填：从 url（或详情「番号」行）取 code，建/并条目，归类结构化字段。"""
    url = (meta.get("url") or "").strip()
    details = meta.get("details") or []
    typed = classify(details)
    code = extract_code(url) or typed.get("code")
    if not code:
        return None
    code = normalize_code(code)
    success = bool(meta.get("success"))

    with db.conn() as conn:
        conn.execute("INSERT OR IGNORE INTO entries (code, fetched) VALUES (?, 0)", (code,))
        conn.execute(
            "INSERT OR IGNORE INTO entry_links (url, code, domain) VALUES (?, ?, ?)",
            (url, code, _root_domain(url)),
        )
        old = conn.execute("SELECT * FROM entries WHERE code = ?", (code,)).fetchone()
        if not success and old is not None:
            conn.execute(
                "UPDATE entries SET fetched = 1, updated_at = datetime('now','localtime') WHERE code = ?",
                (code,),
            )
        else:
            prev = old if old is not None else None

            def keep(incoming: str, col: str) -> str:
                return incoming or (prev[col] if prev is not None else "")

            title = keep((meta.get("title") or "").strip(), "title")
            cover = keep((meta.get("thumbnail") or "").strip(), "cover")
            release_date = keep(typed.get("release_date") or "", "release_date")
            maker = keep(typed.get("maker") or "", "maker")
            director = keep(typed.get("director") or "", "director")
            label = keep(typed.get("label") or "", "label")
            series = keep(typed.get("series") or "", "series")
            duration = keep(typed.get("duration") or "", "duration")
            rating = keep(typed.get("rating") or "", "rating")
            actors = typed.get("actors") or (json.loads(prev["actors"] or "[]") if prev is not None else [])
            genres = typed.get("genres") or (json.loads(prev["genres"] or "[]") if prev is not None else [])

            conn.execute(
                "UPDATE entries SET title=?, cover=?, release_date=?, maker=?, director=?, label=?, series=?, "
                "duration=?, rating=?, actors=?, genres=?, details=?, fetched=1, "
                "updated_at=datetime('now','localtime') WHERE code=?",
                (title, cover, release_date, maker, director, label, series, duration, rating,
                 json.dumps(actors, ensure_ascii=False),
                 json.dumps(genres, ensure_ascii=False),
                 json.dumps(details, ensure_ascii=False), code),
            )
    return get_entry(code)


def set_entry_title(code: str, title: str) -> dict | None:
    return update_entry(code, {"title": title})


def update_entry(code: str, fields: dict) -> dict | None:
    """手动更新条目字段（只改给到的键）。fields 支持 title/cover/release_date/maker/series/duration/rating/actors/genres。"""
    code = normalize_code(code)
    if not code:
        return None
    with db.conn() as conn:
        if conn.execute("SELECT 1 FROM entries WHERE code = ?", (code,)).fetchone() is None:
            return None
        sets: list[str] = []
        params: list = []
        for key, val in fields.items():
            if key in ("actors", "genres"):
                if isinstance(val, str):
                    val = [s.strip() for s in val.split(",") if s.strip()]
                if not isinstance(val, list):
                    continue
                sets.append(f"{key} = ?")
                params.append(json.dumps(val, ensure_ascii=False))
            elif key in ("title", "cover", "release_date", "maker", "director", "label", "series", "duration", "rating"):
                sets.append(f"{key} = ?")
                params.append((val or "").strip() if isinstance(val, str) else "")
        if not sets:
            return get_entry(code)
        sets.append("updated_at = datetime('now','localtime')")
        params.append(code)
        conn.execute(f"UPDATE entries SET {', '.join(sets)} WHERE code = ?", params)
    return get_entry(code)


def delete_entry(code: str) -> bool:
    code = normalize_code(code)
    if not code:
        return False
    with db.conn() as conn:
        conn.execute("DELETE FROM entry_links WHERE code = ?", (code,))
        conn.execute("DELETE FROM entry_tags WHERE code = ?", (code,))
        cur = conn.execute("DELETE FROM entries WHERE code = ?", (code,))
        return cur.rowcount > 0


def list_pending_links(limit: int = 5000) -> list[str]:
    with db.conn() as conn:
        rows = conn.execute(
            "SELECT el.url FROM entry_links el JOIN entries e ON e.code = el.code "
            "WHERE e.fetched = 0 ORDER BY el.rowid LIMIT ?", (limit,)).fetchall()
        return [r["url"] for r in rows]


# ──────────────────────── 详情代理补全 ────────────────────────

def enrich_from_proxy(code: str, data: dict) -> dict | None:
    """用代理返回的数据补全条目的**缺失字段**（非空不覆盖，与合并规则一致）。

    只填当前为空的字段：已有的标题/封面/属性/演员/类型/详情一律不被代理覆盖，
    保证「网页抓取到的内容优先、代理只做兜底补全」这一整体行为一致性。
    """
    code = normalize_code(code)
    if not code:
        return None
    with db.conn() as conn:
        old = conn.execute("SELECT * FROM entries WHERE code = ?", (code,)).fetchone()
        if old is None:
            return None
        sets: list[str] = []
        params: list = []
        for f in ("title", "cover", "release_date", "maker", "director", "label", "series", "duration", "rating"):
            incoming = (data.get(f) or "").strip() if isinstance(data.get(f), str) else ""
            if incoming and not (old[f] or ""):
                sets.append(f"{f} = ?")
                params.append(incoming)
        for f in ("actors", "genres"):
            incoming = data.get(f) or []
            if incoming and not json.loads(old[f] or "[]"):
                sets.append(f"{f} = ?")
                params.append(json.dumps(incoming, ensure_ascii=False))
        incoming_details = data.get("details") or []
        if incoming_details and not json.loads(old["details"] or "[]"):
            sets.append("details = ?")
            params.append(json.dumps(incoming_details, ensure_ascii=False))
        if sets:
            sets.append("updated_at = datetime('now','localtime')")
            params.append(code)
            conn.execute(f"UPDATE entries SET {', '.join(sets)} WHERE code = ?", params)
    return get_entry(code)


def enrich_entry(code: str) -> dict | None:
    """从代理补全条目缺失字段；代理未启用/失败/无缺失时返回当前条目。"""
    code = normalize_code(code)
    if not code:
        return None
    e = get_entry(code)
    if e is None:
        return None
    if not detail_proxy.enabled():
        return e
    url = e["links"][0] if e["links"] else None
    data = detail_proxy.lookup(code, url=url)
    if not data:
        return e
    return enrich_from_proxy(code, data) or e


# ──────────────────────── Tags ────────────────────────

def list_tags() -> list[dict]:
    with db.conn() as conn:
        rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]


def create_tag(name: str) -> dict:
    name = (name or "").strip()
    with db.conn() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        row = conn.execute("SELECT * FROM tags WHERE name = ?", (name,)).fetchone()
        return {"id": row["id"], "name": row["name"]}


def delete_tag(tag_id: int) -> bool:
    with db.conn() as conn:
        conn.execute("DELETE FROM entry_tags WHERE tag_id = ?", (tag_id,))
        cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        return cur.rowcount > 0


def add_tag_to_entry(code: str, tag_id: int) -> bool:
    code = normalize_code(code)
    if not code:
        return False
    with db.conn() as conn:
        conn.execute("INSERT OR IGNORE INTO entry_tags (code, tag_id) VALUES (?, ?)", (code, tag_id))
        return True


def remove_tag_from_entry(code: str, tag_id: int) -> bool:
    code = normalize_code(code)
    if not code:
        return False
    with db.conn() as conn:
        cur = conn.execute("DELETE FROM entry_tags WHERE code = ? AND tag_id = ?", (code, tag_id))
        return cur.rowcount > 0
