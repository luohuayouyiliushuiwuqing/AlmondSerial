"""条目存储：番号（code）为唯一关键字 + 多链接关联 + 标签。

合并规则：抓取失败不覆盖已有字段、空标题/空字段不覆盖、抓取置 fetched=1（「尝试过」）。
"""

import json
import sqlite3
from urllib.parse import urlsplit, urlunsplit, urlparse

from . import db, detail_proxy, imgcache
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


def _cover_state(cover: str) -> int:
    """封面（图像）抓取状态：0 无链接 / 1 有链接未抓图 / 2 已抓图 / 3 抓图失败。"""
    if not cover:
        return 0
    if imgcache.has(cover):
        return 2
    if imgcache.is_failed(cover):
        return 3
    return 1


# ──────────────────────── 条目状态分级 ────────────────────────
# 按严重度从高到低：空白 > 详情不全 > 图失效/缺图 > 完整。
# 「详情到底拿到没有」只认两样：标题 + 发行日期。详情页本来就不保证项项齐全（缺片商、
# 缺演员都属正常，不算异常）；但发行日期只可能来自详情页——拿到它就说明详情页上能拿的
# 都拿到了。标题可能来自 OG，光有标题不足以证明抓过详情，所以缺任一项都算详情不全。
# 「缺图」只在详情齐了之后才单独成级，不会盖住详情的问题。
STATUS_EMPTY = "empty"                  # 空白：标题、详情、封面全无（多半从未抓过，或抓取彻底失败）
STATUS_PARTIAL = "partial"              # 详情不全：缺标题或发行日期（封面有没有都归这一级）
STATUS_COVER_FAILED = "cover_failed"    # 图失效：详情齐了，但抓图失败
STATUS_COVER_MISSING = "cover_missing"  # 缺图：详情齐了，但没有封面链接
STATUS_OK = "ok"

STATUS_LEVEL = {
    STATUS_EMPTY: 3, STATUS_PARTIAL: 2,
    STATUS_COVER_FAILED: 1, STATUS_COVER_MISSING: 1, STATUS_OK: 0,
}

# 建议的重试动作：detail 重抓详情页 / cover 重抓封面图 / both 两者 / "" 无需
RETRY_ACTION = {
    STATUS_EMPTY: "both", STATUS_PARTIAL: "detail",
    STATUS_COVER_FAILED: "cover", STATUS_COVER_MISSING: "detail", STATUS_OK: "",
}

# 判定「详情是否拿到」的两个字段：(字段, 中文名)。任一为空即详情不全
DETAIL_PROOF_FIELDS = (("title", "标题"), ("release_date", "发行日期"))


def _entry_state(entry: dict) -> tuple[str, list[str], str]:
    """算出 (状态, 缺失的关键项, 建议重试动作)。"""
    missing = [label for key, label in DETAIL_PROOF_FIELDS if not entry[key]]
    cover_state = entry["cover_state"]
    blank = not any((
        entry["title"], entry["cover"], entry["details"], entry["release_date"],
        entry["maker"], entry["director"], entry["label"], entry["series"],
        entry["duration"], entry["rating"], entry["actors"], entry["genres"],
    ))
    if blank:
        status = STATUS_EMPTY
    elif missing:
        status = STATUS_PARTIAL
    elif cover_state == 3:
        status = STATUS_COVER_FAILED
    elif cover_state == 0:
        status = STATUS_COVER_MISSING
    else:
        status = STATUS_OK
    return status, missing, RETRY_ACTION[status]


def _row_to_entry(row: sqlite3.Row, links: list[str], tags: list[dict]) -> dict:
    details = json.loads(row["details"] or "[]")
    cover = row["cover"] or ""
    entry = {
        "code": row["code"], "title": row["title"], "cover": cover,
        "cover_state": _cover_state(cover),
        "release_date": row["release_date"], "maker": row["maker"],
        "director": row["director"], "label": row["label"],
        "series": row["series"], "duration": row["duration"], "rating": row["rating"],
        "actors": json.loads(row["actors"] or "[]"),
        "genres": json.loads(row["genres"] or "[]"),
        "details": details,
        "field_links": field_links(details),
        "fetched": row["fetched"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "links": links, "tags": tags,
    }
    status, missing, retry = _entry_state(entry)
    entry.update(status=status, status_level=STATUS_LEVEL[status], missing=missing, retry=retry)
    return entry


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
    order = "ORDER BY created_at DESC, rowid DESC"
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


def list_by_codes(codes: list[str]) -> list[dict]:
    """按番号批量取条目（保持传入顺序，已不存在的跳过）。"""
    wanted = [c for c in (codes or []) if c]
    if not wanted:
        return []
    marks = ",".join("?" * len(wanted))
    with db.conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM entries WHERE code IN ({marks})", wanted).fetchall()
        by_code = {r["code"]: r for r in rows}
        return [_row_to_entry(by_code[c], _links_for(conn, c), _entry_tags(conn, c))
                for c in wanted if c in by_code]


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
    """链接导入：从每个链接提取番号，建条目 + 挂链接（不联网）。

    返回里带 `to_fetch`：本次导入后仍待抓取（fetched=0）的链接，供后台抓取入队。
    """
    urls = [u.strip() for u in (urls or []) if isinstance(u, str) and u.strip()]
    inserted = existing = 0
    unrecognized: list[str] = []
    to_fetch: list[str] = []
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
            row = conn.execute("SELECT fetched FROM entries WHERE code = ?", (code,)).fetchone()
            if row is not None and not row["fetched"]:
                to_fetch.append(url)
    return {"inserted": inserted, "existing": existing, "unrecognized": unrecognized,
            "to_fetch": to_fetch}


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
                "UPDATE entries SET fetched = 2, updated_at = datetime('now','localtime') WHERE code = ?",
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
        # 详情一落库就把演员关联同步进演员层，新抓的条目立刻出现在演员列表里
        from . import actresses  # 延迟导入：actresses 依赖本模块
        actresses.sync_entry(conn, code)
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
        if "actors" in fields:
            # 详情没变时光看 details 解析不到手改的演员，这里补一次同步
            from . import actresses
            actresses.sync_entry(conn, code)
    return get_entry(code)


def delete_entry(code: str) -> bool:
    code = normalize_code(code)
    if not code:
        return False
    with db.conn() as conn:
        conn.execute("DELETE FROM entry_links WHERE code = ?", (code,))
        conn.execute("DELETE FROM entry_tags WHERE code = ?", (code,))
        cur = conn.execute("DELETE FROM entries WHERE code = ?", (code,))
        from . import actresses
        actresses.sync_entry(conn, code)  # 条目已删 → 顺手清掉它留下的演员关联
        return cur.rowcount > 0


def list_pending_links(limit: int = 5000) -> list[str]:
    """待抓取链接（fetched=0）；冻结域名下的链接不参与（保持冻结、不反复抓）。"""
    with db.conn() as conn:
        rows = conn.execute(
            "SELECT el.url FROM entry_links el JOIN entries e ON e.code = el.code "
            "LEFT JOIN domain_state ds ON ds.domain = el.domain "
            "WHERE e.fetched = 0 AND (ds.frozen IS NULL OR ds.frozen = 0) "
            "ORDER BY el.rowid LIMIT ?", (limit,)).fetchall()
        return [r["url"] for r in rows]


def list_failed_links(limit: int = 5000) -> list[str]:
    """抓取失败的链接（fetched=2）；冻结域名下不参与，供低优先级重试。"""
    with db.conn() as conn:
        rows = conn.execute(
            "SELECT el.url FROM entry_links el JOIN entries e ON e.code = el.code "
            "LEFT JOIN domain_state ds ON ds.domain = el.domain "
            "WHERE e.fetched = 2 AND (ds.frozen IS NULL OR ds.frozen = 0) "
            "ORDER BY el.rowid LIMIT ?", (limit,)).fetchall()
        return [r["url"] for r in rows]


# 「空白条目」判据（与 _entry_state 的 blank 完全一致）：所有内容字段都为空
_BLANK_ENTRY_SQL = (
    "e.title = '' AND e.cover = '' AND e.details = '[]' AND e.release_date = '' "
    "AND e.maker = '' AND e.director = '' AND e.label = '' AND e.series = '' "
    "AND e.duration = '' AND e.rating = '' AND e.actors = '[]' AND e.genres = '[]'"
)


def list_page_repair_codes(limit: int = 50) -> list[str]:
    """需要**重抓页面**才能补全的条目（partial / cover_missing），按更新时间从旧到新。

    - partial：有内容但缺标题或发行日期；
    - cover_missing：标题和发行日期齐了，但没有封面链接。

    只取 ``fetched=1``（已抓过；0 待抓、2 失败由 worker 处理）。判据与
    ``_entry_state`` 保持一致，避免「状态」与「修复候选」各说各话。
    """
    sql = (
        "SELECT e.code FROM entries e "
        f"WHERE e.fetched = 1 AND NOT ({_BLANK_ENTRY_SQL}) "
        "AND (e.title = '' OR e.release_date = '' OR e.cover = '') "
        "ORDER BY e.updated_at ASC LIMIT ?"
    )
    with db.conn() as conn:
        return [r[0] for r in conn.execute(sql, (limit,)).fetchall()]


def queue_stats() -> dict:
    """抓取队列统计：未抓取 / 失败（按链接，排除冻结域名）+ 异常分布（按条目）。

    `partial`：详情不全（缺标题或发行日期）；完全空白的条目不重复计入，它们已经
    体现在上面按链接统计的未抓取 / 失败里；
    `cover_missing`：详情齐了（有标题和发行日期）但没有封面链接；
    `cover_failed`：详情齐了、有封面链接，但图片抓取失败（有 ``.fail`` 标记且无缓存）；
    `frozen_domains`：已冻结的域名数（冻结域名下的链接不计入未抓/失败，需单独可见）；
    `entries_total` / `entries_complete`：条目总数与资料完整（标题+发行日期都拿到）数，
    供进度条在空闲时显示整库完整度。
    """
    base = ("FROM entry_links el JOIN entries e ON e.code = el.code "
            "LEFT JOIN domain_state ds ON ds.domain = el.domain "
            "WHERE (ds.frozen IS NULL OR ds.frozen = 0) AND ")
    with db.conn() as conn:
        pending = conn.execute(f"SELECT count(*) {base} e.fetched = 0").fetchone()[0]
        failed = conn.execute(f"SELECT count(*) {base} e.fetched = 2").fetchone()[0]
        # partial 的判据必须与 _entry_state 完全一致：用同一份「空白条目」SQL 排除
        # 完全空的条目，而不是各自维护一份字段清单——否则只有标题的条目会在徽章上
        # 显示「详情不全」却不进计数。
        row = conn.execute(
            "SELECT "
            f" SUM(CASE WHEN (e.title = '' OR e.release_date = '') "
            f"      AND NOT ({_BLANK_ENTRY_SQL}) THEN 1 ELSE 0 END) AS partial, "
            " SUM(CASE WHEN e.cover = '' AND e.title <> '' AND e.release_date <> '' "
            "      THEN 1 ELSE 0 END) AS cover_missing, "
            " SUM(CASE WHEN e.title <> '' AND e.release_date <> '' "
            "      THEN 1 ELSE 0 END) AS complete, "
            " count(*) AS entries_total "
            "FROM entries e").fetchone()
        covers = conn.execute(
            "SELECT cover FROM entries "
            "WHERE cover <> '' AND title <> '' AND release_date <> ''"
        ).fetchall()
        frozen = conn.execute(
            "SELECT count(*) FROM domain_state WHERE frozen = 1").fetchone()[0]

    # 图失效：先用失败标记集合过滤（只对真正失败过的 URL 做存在性检查），避免全库 stat
    failed_keys = imgcache.failed_keys()
    cover_failed = 0
    if failed_keys:
        for r in covers:
            cover = r["cover"]
            if imgcache.key_for(cover) in failed_keys and not imgcache.has(cover):
                cover_failed += 1

    return {"pending": pending, "failed": failed,
            "partial": row["partial"] or 0, "cover_missing": row["cover_missing"] or 0,
            "cover_failed": cover_failed, "frozen_domains": frozen,
            "entries_total": row["entries_total"] or 0,
            "entries_complete": row["complete"] or 0}


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
            from . import actresses
            actresses.sync_entry(conn, code)
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


# ──────────────────────── 域名管理 ────────────────────────

def list_domains() -> list[dict]:
    """库中所有域名及其状态（链接数 / 待抓数 / 冻结 / 最近检测）。"""
    with db.conn() as conn:
        rows = conn.execute(
            "SELECT el.domain, COUNT(*) AS link_count, "
            "SUM(CASE WHEN e.fetched = 0 THEN 1 ELSE 0 END) AS pending_count "
            "FROM entry_links el JOIN entries e ON e.code = el.code "
            "GROUP BY el.domain ORDER BY el.domain"
        ).fetchall()
        states = {r["domain"]: r for r in conn.execute("SELECT * FROM domain_state").fetchall()}
    out: list[dict] = []
    for r in rows:
        d = r["domain"]
        st = states.get(d)
        out.append({
            "domain": d,
            "link_count": r["link_count"],
            "pending_count": r["pending_count"] or 0,
            "frozen": bool(st["frozen"]) if st else False,
            "replacement": (st["replacement"] if st else "") or "",
            "ok": bool(st["ok"]) if st else True,
            "checked_at": (st["checked_at"] if st else "") or "",
        })
    return out


def set_domain_frozen(domain: str, frozen: bool) -> bool:
    domain = (domain or "").strip().lower()
    if not domain:
        return False
    with db.conn() as conn:
        conn.execute(
            "INSERT INTO domain_state (domain, frozen) VALUES (?, ?) "
            "ON CONFLICT(domain) DO UPDATE SET frozen = excluded.frozen",
            (domain, int(bool(frozen))),
        )
    return True


def frozen_domains() -> set[str]:
    """已冻结的域名集合（供后台抓取快速跳过）。"""
    with db.conn() as conn:
        rows = conn.execute("SELECT domain FROM domain_state WHERE frozen = 1").fetchall()
        return {r["domain"] for r in rows}


def record_domain_check(domain: str, ok: bool) -> None:
    domain = (domain or "").strip().lower()
    if not domain:
        return
    with db.conn() as conn:
        conn.execute(
            "INSERT INTO domain_state (domain, ok, checked_at) VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(domain) DO UPDATE SET ok = excluded.ok, checked_at = excluded.checked_at",
            (domain, int(bool(ok))),
        )


def _replace_domain_in_url(url: str, from_domain: str, to_domain: str) -> str:
    """把 URL 的 host 中的 from_domain（含子域名前缀）替换为 to_domain，其余部分保留。"""
    try:
        parts = urlsplit(url)
    except Exception:
        return ""
    host = (parts.hostname or "").lower()
    if not host or not (host == from_domain or host.endswith("." + from_domain)):
        return ""
    if host == from_domain:
        new_host = to_domain
    else:
        new_host = host[: -(len(from_domain) + 1)] + "." + to_domain
    netloc = new_host
    if parts.port:
        netloc = f"{new_host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def replace_domain(from_domain: str, to_domain: str) -> dict:
    """批量替换某域名下所有链接的域名为新域名，并记录替换关系。"""
    from_domain = (from_domain or "").strip().lower()
    to_domain = (to_domain or "").strip().lower()
    if not from_domain or not to_domain or from_domain == to_domain:
        return {"replaced": 0, "from_domain": from_domain, "to_domain": to_domain}
    with db.conn() as conn:
        rows = conn.execute("SELECT url FROM entry_links WHERE domain = ?", (from_domain,)).fetchall()
        replaced = 0
        for r in rows:
            old = r["url"]
            new = _replace_domain_in_url(old, from_domain, to_domain)
            if not new or new == old:
                continue
            if conn.execute("SELECT 1 FROM entry_links WHERE url = ?", (new,)).fetchone():
                # 新链接已存在 → 视为合并，删除旧链接（保留已有）
                conn.execute("DELETE FROM entry_links WHERE url = ?", (old,))
            else:
                conn.execute(
                    "UPDATE entry_links SET url = ?, domain = ? WHERE url = ?",
                    (new, _root_domain(new), old),
                )
            replaced += 1
        # 同步替换封面 URL 里含旧域名的地址（整体修改，避免封面失效）
        for r in conn.execute(
            "SELECT code, cover FROM entries WHERE cover LIKE ?", (f"%{from_domain}%",)
        ).fetchall():
            new_cover = _replace_domain_in_url(r["cover"], from_domain, to_domain)
            if new_cover and new_cover != r["cover"]:
                conn.execute("UPDATE entries SET cover = ? WHERE code = ?", (new_cover, r["code"]))
        conn.execute(
            "INSERT INTO domain_state (domain, replacement) VALUES (?, ?) "
            "ON CONFLICT(domain) DO UPDATE SET replacement = excluded.replacement",
            (from_domain, to_domain),
        )
    return {"replaced": replaced, "from_domain": from_domain, "to_domain": to_domain}
