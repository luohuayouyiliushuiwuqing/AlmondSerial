"""后台抓取调度：独立线程持续消费待抓取链接，前端无需保活。

优先级：未抓取（fetched=0）优先，抓取失败（fetched=2）次之。
首次抓取失败的链接会以低优先级重试一次；重试仍失败则保持 fetched=2，不再自动重试。
同一域名连续失败达到 MAX_CONSECUTIVE_FAILURES 次 → 自动冻结该域名（暂停抓取，避免无用功）。
"""

import queue
import threading

from loguru import logger

from . import entries
from .fetcher import fetch_page

WORKERS = 3
PRIORITY_PENDING = 0   # 未抓取（优先）
PRIORITY_RETRY = 1     # 失败重试（次之）
MAX_CONSECUTIVE_FAILURES = 5   # 同域名连续失败达到该值即自动冻结

_queue: "queue.PriorityQueue[tuple[int, str]]" = queue.PriorityQueue()
_stop = threading.Event()
_threads: list[threading.Thread] = []

_lock = threading.Lock()
_fail_counts: dict[str, int] = {}   # 域名 → 连续失败次数（成功即清零）
_frozen: set[str] = set()           # 本进程内已冻结域名（快速跳过已入队链接）


def enqueue(url: str, priority: int = PRIORITY_PENDING) -> None:
    if url:
        _queue.put((priority, url))


def status() -> dict:
    return {"queue": _queue.qsize(), "workers": WORKERS}


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
    while not _stop.is_set():
        try:
            priority, url = _queue.get(timeout=5)
        except queue.Empty:
            continue
        domain = entries._root_domain(url)
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
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"后台抓取异常：{url}（{type(exc).__name__}: {exc}）")
            _note_failure(domain)
        finally:
            _queue.task_done()


def start() -> None:
    """启动后台抓取（幂等）。未抓取优先、失败重试其次入队。"""
    if _threads:
        return
    with _lock:
        _frozen.update(entries.frozen_domains())
    for url in entries.list_pending_links():
        _queue.put((PRIORITY_PENDING, url))
    for url in entries.list_failed_links():
        _queue.put((PRIORITY_RETRY, url))
    for _ in range(WORKERS):
        t = threading.Thread(target=_worker, daemon=True, name="fetch-worker")
        t.start()
        _threads.append(t)
