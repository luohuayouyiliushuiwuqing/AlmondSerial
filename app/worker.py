"""后台抓取调度：独立线程持续消费待抓取链接，前端无需保活。

优先级：未抓取（fetched=0）优先，抓取失败（fetched=2）次之。
首次抓取失败的链接会以低优先级重试一次；重试仍失败则保持 fetched=2，不再自动重试。
同一域名连续失败达到 MAX_CONSECUTIVE_FAILURES 次 → 自动冻结该域名（暂停抓取，避免无用功）。
"""

import queue
import threading
import time

from loguru import logger

from . import entries, repair
from .fetcher import fetch_page

WORKERS = 3
PRIORITY_PENDING = 0   # 未抓取（优先）
PRIORITY_RETRY = 1     # 失败重试（次之）
MAX_CONSECUTIVE_FAILURES = 5   # 同域名连续失败达到该值即自动冻结
REPAIR_IDLE_WAIT = 60.0        # 队列空闲后，每隔多久尝试一次图失效修复（秒）

_queue: "queue.PriorityQueue[tuple[int, str]]" = queue.PriorityQueue()
_stop = threading.Event()
_threads: list[threading.Thread] = []
_repair_thread: threading.Thread | None = None

_lock = threading.Lock()
_fail_counts: dict[str, int] = {}   # 域名 → 连续失败次数（成功即清零）
_frozen: set[str] = set()           # 本进程内已冻结域名（快速跳过已入队链接）
_queued: set[str] = set()           # 已在队列中的 URL：按链接去重，避免重复抓同一页

# 供前端展示的实时状态：正在抓什么 + 本批任务的完成进度
_active: dict[int, dict] = {}       # 线程 id → {url, domain, since}
_burst_total = 0                    # 本批任务总数（队列由空转非空时重新计）
_burst_done = 0                     # 本批已完成数（每个 URL 只计一次，重试不重复计）


def enqueue(url: str, priority: int = PRIORITY_PENDING) -> None:
    """入队一条链接；同一 URL 已在队列里则不重复入队。"""
    if not url:
        return
    global _burst_total, _burst_done
    with _lock:
        if url in _queued:
            return
        # 队列由空转非空：这是新的一批任务，进度从 0 重新算
        if _queue.unfinished_tasks == 0:
            _burst_total = 0
            _burst_done = 0
        _burst_total += 1
        _queued.add(url)
        _queue.put((priority, url))


def _forget(url: str) -> None:
    """链接处理结束/被重排后，从去重集合里移除。"""
    with _lock:
        _queued.discard(url)


def status() -> dict:
    """实时状态：正在抓什么 + 本批任务进度（供底部导航/进度条展示）。"""
    with _lock:
        active = [
            {"kind": "fetch", "url": a["url"], "domain": a["domain"],
             "elapsed": round(time.time() - a["since"], 1)}
            for a in _active.values()
        ]
        total, done = _burst_total, _burst_done
    # 队列空闲后的修复线程也算「正在干活」：重试封面 / 补全详情
    repairing = repair.status()
    if repairing:
        active.append(repairing)
    remaining = _queue.unfinished_tasks
    percent = None if total == 0 else min(100.0, round(done * 100 / total, 1))
    return {
        "queue": _queue.qsize(),
        "workers": WORKERS,
        "active": active,
        "total": total,
        "done": done,
        "remaining": remaining,
        "percent": percent,
    }


def mark_frozen(domain: str, frozen: bool) -> None:
    """外部（手动冻结/解冻）同步本进程的冻结集合。"""
    domain = (domain or "").strip().lower()
    if not domain:
        return
    with _lock:
        if frozen:
            _frozen.add(domain)
        else:
            _frozen.discard(domain)


def _is_frozen(domain: str) -> bool:
    with _lock:
        return domain in _frozen


def _note_success(domain: str) -> None:
    if not domain:
        return
    with _lock:
        _fail_counts[domain] = 0


def _note_failure(domain: str) -> None:
    if not domain:
        return
    freeze = False
    with _lock:
        n = _fail_counts.get(domain, 0) + 1
        _fail_counts[domain] = n
        if n >= MAX_CONSECUTIVE_FAILURES and domain not in _frozen:
            _frozen.add(domain)
            freeze = True
    if freeze:
        entries.set_domain_frozen(domain, True)
        logger.warning(
            f"域名 {domain} 连续失败 {MAX_CONSECUTIVE_FAILURES} 次，已自动冻结"
            f"（暂停抓取该域名，可在「域名」面板解冻）"
        )


def _worker() -> None:
    global _burst_done
    tid = threading.get_ident()
    while not _stop.is_set():
        try:
            priority, url = _queue.get(timeout=5)
        except queue.Empty:
            continue
        domain = entries._root_domain(url)
        requeued = False
        with _lock:
            _active[tid] = {"url": url, "domain": domain, "since": time.time()}
        try:
            if domain and _is_frozen(domain):
                continue  # 域名已冻结，跳过（保持 fetched 不变，解冻后再抓）
            meta = fetch_page(url)
            entries.ingest_fetched(meta)
            if meta.get("success"):
                _note_success(domain)
            else:
                _note_failure(domain)
                # 首次抓取失败 → 低优先级重试一次；重试再失败则保持 fetched=2
                if priority == PRIORITY_PENDING:
                    _queue.put((PRIORITY_RETRY, url))
                    requeued = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"后台抓取异常：{url}（{type(exc).__name__}: {exc}）")
            _note_failure(domain)
        finally:
            with _lock:
                _active.pop(tid, None)
                if not requeued:
                    _burst_done += 1  # 每个 URL 只在最终结束时计一次
            if not requeued:
                _forget(url)  # 已重排的仍在队列里，保留去重记录
            _queue.task_done()


def _repair_loop() -> None:
    """队列全部抓完后，周期性尝试修复图失效（封面没缓存下来，但链接可能仍有效）。

    只在没有在途抓取任务时动手，避免和正常抓取抢资源；具体分级重试与冷却/
    熔断由 ``repair.repair_cover_failures`` 负责。
    """
    while not _stop.is_set():
        _stop.wait(REPAIR_IDLE_WAIT)
        if _stop.is_set():
            break
        if _queue.unfinished_tasks:
            continue  # 还有抓取/重试任务，等全部抓完再修图
        try:
            repair.run_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"失败恢复异常：{type(exc).__name__}: {exc}")


def start() -> None:
    """启动后台抓取（幂等）。未抓取优先、失败重试其次入队。"""
    global _repair_thread, _burst_total, _burst_done
    if _threads:
        return
    with _lock:
        _frozen.update(entries.frozen_domains())
        _active.clear()
        _burst_total = 0
        _burst_done = 0
    for url in entries.list_pending_links():
        enqueue(url, PRIORITY_PENDING)
    for url in entries.list_failed_links():
        enqueue(url, PRIORITY_RETRY)
    for _ in range(WORKERS):
        t = threading.Thread(target=_worker, daemon=True, name="fetch-worker")
        t.start()
        _threads.append(t)
    # 所有抓取任务完成后，后台周期性修复「图失效」的封面
    if _repair_thread is None or not _repair_thread.is_alive():
        _repair_thread = threading.Thread(target=_repair_loop, daemon=True, name="cover-repair")
        _repair_thread.start()
