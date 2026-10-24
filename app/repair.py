"""失败恢复：把「图片」和「页面」当成两个独立的最小恢复单元。

关键事实：标题、封面 URL、详情都来自**同一次页面抓取**；只有图片字节是对封面
URL 的**第二次独立请求**，且落在本地缓存。因此：

- 缺标题 / 缺发行日期（partial）、缺封面 URL（cover_missing）→ 只重抓**页面**；
  而且第二次要用**渲染层**（`prefer_render=True`），因为同样的静态请求再发一遍
  大概率还是空。
- 图失效（cover_failed，有 URL 但图片没缓存下来）→ 只重试**图片**。页面当初抓成功、
  标题/详情都齐，重抓页面拿回的多半还是同一个 URL，属于纯冗余，默认不做。
- 页面本身抓不到（网络/反爬）→ 交给 worker 的 `fetched=2` 重试与域名冻结。

两个单元互不牵连：图片失败只写 `.fail` 标记，页面失败只改 `fetched`。各自有冷却和
次数上限，避免死链/本身就没有该字段的页面被无限重试。

页面重试次数目前用**内存计数**（重启重置，不动 schema）；图片的重试时钟由
``.fail`` 文件的 mtime 提供（落盘、跨重启有效）。
"""

import threading
import time

from loguru import logger

from . import db, entries, imgcache, proxy
from .fetcher import fetch_image_bytes, fetch_page

FAIL_COOLDOWN = 3600.0             # 同一张图两次自动修复的最小间隔（秒）
COVER_REPAIR_BATCH = 30            # 单轮最多尝试多少张图
MAX_COVER_ATTEMPTS = 2             # 同一张图本进程内最多自动尝试几次，之后转手动
COVER_FAIL_BREAKER = 5             # 连续这么多张图失败 → 疑似网络/代理异常，熔断

PAGE_REPAIR_BATCH = 10             # 单轮最多重抓多少个页面（渲染层较慢）
MAX_PAGE_ATTEMPTS = 2              # 同一页面本进程内最多自动重抓几次（很多页本来就没有该字段）
PAGE_FAIL_BREAKER = 3              # 连续这么多次页面抓取失败 → 熔断

# 本进程内的尝试计数（页面重试次数不落库，重启重置）
_cover_attempts: dict[str, int] = {}
_page_attempts: dict[str, int] = {}

# 当前正在跑的修复动作（供底部导航展示「到底在做什么」；队列空闲时也能看见）
_activity_lock = threading.Lock()
_activity: dict | None = None

# 本轮修复的完成度（供进度条显示确定百分比，而不是一直转圈）
_progress_lock = threading.Lock()
_round = {"active": False, "kind": "", "done": 0, "total": 0}


def _begin(kind: str, url: str) -> None:
    global _activity
    with _activity_lock:
        _activity = {"kind": kind, "url": url,
                     "domain": entries._root_domain(url), "since": time.time()}


def _end() -> None:
    global _activity
    with _activity_lock:
        _activity = None


def _round_begin(total: int) -> None:
    global _round
    with _progress_lock:
        _round = {"active": True, "kind": "cover", "done": 0, "total": total}


def _round_kind(kind: str) -> None:
    with _progress_lock:
        _round["kind"] = kind


def _round_step() -> None:
    with _progress_lock:
        if _round["active"]:
            _round["done"] += 1


def _round_end() -> None:
    with _progress_lock:
        _round["active"] = False
        _round["kind"] = ""


def status() -> dict | None:
    """当前修复动作 + 本轮完成度；空闲返回 None。

    kind：cover（重试封面）/ page（补全详情）。两个抓取动作之间也有短暂间隙，
    此时 ``url`` 为空但 ``active`` 仍为 True，进度条据此保持显示本轮进度。
    """
    with _activity_lock:
        a = _activity
        act = None
        if a is not None:
            act = {"kind": a["kind"], "url": a["url"], "domain": a["domain"],
                   "elapsed": round(time.time() - a["since"], 1)}
    with _progress_lock:
        done, total = _round["done"], _round["total"]
        round_active, kind = _round["active"], _round["kind"]
    if act is not None:
        act.update({"done": done, "total": total})
        return act
    if round_active:
        return {"kind": kind or "cover", "url": "", "domain": "", "elapsed": 0.0,
                "done": done, "total": total}
    return None


# ──────────────────────── 图片：只修图，不碰页面 ────────────────────────

def _try_cover(src: str, referer: str) -> bool:
    """重抓一张封面；成功写缓存返回 True，失败重新标记返回 False。

    注意：**不在请求前清除失败标记**。标记留着的这段时间里，/api/img 会直接 404，
    不会和修复线程并发重复抓同一张图；成功时 ``put`` 会自动清掉标记。
    """
    if imgcache.has(src):
        imgcache.clear_failed(src)
        return True
    proxy_url = proxy.resolve() or None
    _begin("cover", src)
    try:
        got = fetch_image_bytes(src, proxy_url, referer=referer)
        if got is None and proxy_url:
            # 代理可能端口漂移，强制重新探测后再试一次
            proxy_url = proxy.resolve(force=True) or None
            got = fetch_image_bytes(src, proxy_url, referer=referer)
    finally:
        _end()
    if got is None:
        imgcache.mark_failed(src)  # 刷新失败时间，进入下一轮冷却
        return False
    data, ctype = got
    imgcache.put(src, data, ctype)  # put 内部会清掉失败标记
    return True


def _referer_for(e: dict, src: str) -> str:
    """挑一个与图片同站（根域名相同）的链接当 Referer，避免用错镜像触发防盗链。"""
    links = e.get("links") or []
    if not links:
        return ""
    src_root = entries._root_domain(src)
    for url in links:
        if entries._root_domain(url) == src_root:
            return url
    return links[0]


def _cover_candidates(limit: int) -> list[tuple[str, str]]:
    """待修的 ``(code, cover)``：有封面链接、未缓存、失败标记已过冷却期。

    先用 ``failed_keys()`` 扫一遍 ``*.fail`` 文件，只有确实失败过且过冷却的才纳入，
    避免对全库每条封面都做存在性检查。**按图片 URL 去重**：多个条目共用同一张
    封面（如站点 logo）时，一轮只尝试一次——成功一次就全部命中缓存。
    """
    failed = imgcache.failed_keys()
    if not failed:
        return []
    with db.conn() as conn:
        rows = conn.execute(
            "SELECT code, cover FROM entries WHERE cover <> '' "
            "ORDER BY updated_at ASC"
        ).fetchall()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for r in rows:
        src = r["cover"]
        if src in seen:
            continue
        seen.add(src)
        if imgcache.key_for(src) not in failed:
            continue
        if imgcache.has(src):
            continue  # 已有缓存（失败标记是残留），跳过
        if not imgcache.failed_older_than(src, FAIL_COOLDOWN):
            continue
        out.append((r["code"], src))
        if len(out) >= limit:
            break
    return out


def repair_cover_failures(limit: int = COVER_REPAIR_BATCH,
                          cands: list[tuple[str, str]] | None = None) -> dict:
    """修复一轮图失效（只重试图片字节，默认不重抓页面）。"""
    if cands is None:
        if not imgcache.failed_keys():
            return {"checked": 0, "repaired": 0, "failed": 0, "skipped": 0}
        cands = _cover_candidates(limit)

    repaired = failed = skipped = 0
    streak = 0
    attempted: set[str] = set()
    for code, src in cands:
        _round_step()  # 无论尝试还是跳过，都算「处理完一项」
        if src in attempted:  # 双保险：同一 URL 一轮只试一次
            skipped += 1
            continue
        n = _cover_attempts.get(src, 0)
        if n >= MAX_COVER_ATTEMPTS:
            skipped += 1
            continue
        attempted.add(src)
        e = entries.get_entry(code)
        if e is None:
            continue
        referer = _referer_for(e, src)
        if _try_cover(src, referer):
            repaired += 1
            _cover_attempts.pop(src, None)
            streak = 0
        else:
            failed += 1
            _cover_attempts[src] = n + 1
            streak += 1
            if streak >= COVER_FAIL_BREAKER:
                logger.warning("连续多张封面抓取失败，疑似网络/代理异常，暂停本轮图片修复")
                break

    if repaired or failed:
        logger.info(f"图失效修复：修复 {repaired}，仍失败 {failed}，达上限跳过 {skipped}")
    return {"checked": len(cands), "repaired": repaired, "failed": failed, "skipped": skipped}


# ──────────────────────── 页面：补标题 / 封面 URL / 详情 ────────────────────────

def _refetch(code: str, links: list[str], prefer_render: bool) -> bool:
    """重抓给定的链接并回填；返回是否至少有一次页面抓取成功。"""
    ok = False
    for url in links:
        _begin("page", url)
        try:
            meta = fetch_page(url, prefer_render=prefer_render)
        finally:
            _end()
        entries.ingest_fetched(meta)  # 失败会置 fetched=2，交给 worker
        ok = ok or bool(meta.get("success"))
    return ok


def _page_candidates(limit: int) -> tuple[list[tuple[str, list[str]]], int]:
    """本轮可重抓的 ``(code, links)`` 及被跳过的数量（次数上限 / 链接全冻结）。"""
    # 多取一些候选：其中一部分可能已达尝试上限而被跳过
    codes = entries.list_page_repair_codes(limit * 3)
    if not codes:
        return [], 0

    frozen = entries.frozen_domains()
    out: list[tuple[str, list[str]]] = []
    skipped = 0
    for code in codes:
        if len(out) >= limit:
            break
        n = _page_attempts.get(code, 0)
        if n >= MAX_PAGE_ATTEMPTS:
            skipped += 1
            continue
        e = entries.get_entry(code)
        if e is None:
            continue
        links = [u for u in e["links"] if entries._root_domain(u) not in frozen]
        if not links:
            skipped += 1  # 全部链接都在冻结域名下，先不动
            continue
        out.append((code, links))
    return out, skipped


def repair_missing_details(limit: int = PAGE_REPAIR_BATCH,
                           cands: list[tuple[str, list[str]]] | None = None) -> dict:
    """重抓 partial / cover_missing 条目的页面（**优先渲染层**），有次数上限。"""
    if cands is None:
        cands, skipped = _page_candidates(limit)
        if not cands:
            return {"checked": 0, "repaired": 0, "failed": 0, "skipped": skipped}
    else:
        skipped = 0
        if not cands:
            return {"checked": 0, "repaired": 0, "failed": 0, "skipped": 0}

    checked = repaired = failed = 0
    streak = 0
    for code, links in cands:
        n = _page_attempts.get(code, 0)
        if n >= MAX_PAGE_ATTEMPTS:  # 双保险：候选阶段已过滤
            skipped += 1
            continue

        checked += 1
        _round_step()
        _page_attempts[code] = n + 1
        ok = _refetch(code, links, prefer_render=True)

        after = entries.get_entry(code)
        if after and after["status"] == "ok":
            repaired += 1
            _page_attempts.pop(code, None)
        else:
            failed += 1

        if not ok:
            streak += 1
            if streak >= PAGE_FAIL_BREAKER:
                logger.warning("连续多次详情抓取失败，疑似网络/代理异常，暂停本轮页面修复")
                break
        else:
            streak = 0

    if checked:
        logger.info(f"页面补全：修复 {repaired}，仍不全 {failed}，达上限跳过 {skipped}")
    return {"checked": checked, "repaired": repaired, "failed": failed, "skipped": skipped}


def run_once() -> dict:
    """跑一轮失败恢复：先修图片（便宜），再补页面（可能启动浏览器渲染）。

    开始前先数清本轮要做的事，进度条据此给出确定的完成百分比；没有可做的事时
    总数为 0，前端转而显示整库完整度。
    """
    cover_cands = _cover_candidates(COVER_REPAIR_BATCH)
    page_cands, _ = _page_candidates(PAGE_REPAIR_BATCH)
    _round_begin(len(cover_cands) + len(page_cands))
    try:
        _round_kind("cover")
        cover = repair_cover_failures(cands=cover_cands)
        _round_kind("page")
        page = repair_missing_details(cands=page_cands)
    finally:
        _round_end()
    return {"cover": cover, "page": page}
