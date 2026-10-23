"""全局代理控制模块。

以**全局代理**为核心：代理配置一处生效，抓取流程统一经 `resolve()` 取当前可用地址，
结果按 TTL 缓存——避免在每条链接处理流程中重复探测/提取代理。

能力：
- 配置：环境变量 `PROXY_URL`，或运行时 `configure()`（API 设置）。
- 检查：`resolve()` 按需验证（端口 + 转发双重），TTL 内复用缓存；
  `start_background_check()` 后台持续验证。
- 自动处理：配置的代理失效 → 自动探测本机常见端口切换 → 仍无可用 → 降级直连。
- 状态：`status()` 暴露配置 / 当前地址 / 可用性，供前端明确提示。
"""

import os
import socket
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

from loguru import logger

_LOCK = threading.Lock()

# 全局配置：环境变量 PROXY_URL 为初始值，运行时 configure() 可覆盖
_configured = os.environ.get("PROXY_URL", "").strip()
# 当前实际使用的代理（探测/验证后的结果；空串 = 直连）
_current = _configured
# 探测结果缓存：None=未验证，str=可用地址，""=不可用（直连）
_last_check_time = 0.0
_last_check_result: str | None = None

CHECK_TTL = 30.0       # 探测结果缓存有效期（秒）—— 避免每条链接重复探测
CHECK_TIMEOUT = 8.0    # 代理转发验证超时
PORT_RANGE = range(7889, 7900)   # 本机常见代理端口探测区间（Clash 默认 7897）
PROBE_HOST = "127.0.0.1"
PROBE_URL = os.environ.get("PROXY_PROBE_URL", "https://www.gstatic.com/generate_204")

_bg_thread: threading.Thread | None = None
_bg_stop = threading.Event()
_BG_INTERVAL = 60.0


def configure(proxy: str) -> None:
    """设置全局代理地址（空串 = 禁用/直连）。"""
    global _configured, _current, _last_check_time, _last_check_result
    with _LOCK:
        _configured = (proxy or "").strip()
        _current = _configured
        _last_check_time = 0.0
        _last_check_result = None
    if _configured:
        logger.info(f"全局代理已配置：{_configured}")
    else:
        logger.info("全局代理已禁用（直连）")


def configured() -> str:
    with _LOCK:
        return _configured


def status() -> dict:
    """代理当前状态，供前端展示 / 明确错误提示。"""
    with _LOCK:
        return {
            "configured": _configured,
            "current": _current,
            "auto": not bool(_configured),   # 是否自动检测模式（未手动指定）
            "enabled": bool(_configured),
            "available": bool(_last_check_result),
            "last_checked": _last_check_time or None,
        }


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """TCP 探测端口是否开放。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def proxy_works(proxy: str, timeout: float = CHECK_TIMEOUT) -> bool:
    """验证代理转发真实可用（仅端口开放但转发不通的不算）。"""
    if not proxy:
        return False
    try:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        req = Request(PROBE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=timeout) as resp:
            return getattr(resp, "status", 200) < 400
    except Exception:
        return False


def detect_local_ports(host: str = PROBE_HOST) -> list[str]:
    """探测本机常见代理端口，返回开放且转发可用的地址列表（按端口升序）。"""
    found: list[str] = []
    for port in PORT_RANGE:
        if not _port_open(host, port):
            continue
        candidate = f"http://{host}:{port}"
        if proxy_works(candidate):
            found.append(candidate)
    return found


def resolve(force: bool = False) -> str:
    """返回当前应使用的全局代理地址（空串 = 直连）。

    **自动检测 / 自动更新**：TTL 内复用缓存（避免每条链接重复探测）；过期或 force 时：
    - 手动配置了代理 → 优先验证配置，失效则转自动探测本机端口切换；
    - 未手动配置 → 直接自动探测本机常见代理端口；
    - 仍无可用 → 降级直连。
    因此无需手动维护代理地址，代理客户端启动 / 换端口后会自动跟上。
    """
    global _current, _last_check_time, _last_check_result
    with _LOCK:
        now = time.time()
        if not force and _last_check_result is not None and (now - _last_check_time) < CHECK_TTL:
            return _current

        # 手动配置的代理优先
        if _configured:
            if proxy_works(_configured):
                _current = _configured
                _last_check_result = _configured
                _last_check_time = now
                return _current
            logger.warning(f"手动配置的代理不可用：{_configured}，转自动探测本机端口…")

        # 自动探测本机常见代理端口（自动检测）
        candidates = detect_local_ports()
        if candidates:
            _current = candidates[0]
            _last_check_result = _current
            _last_check_time = now
            logger.info(f"{'已自动切换到' if _configured else '自动检测到'}可用代理：{_current}")
            return _current

        _current = ""
        _last_check_result = ""
        _last_check_time = now
        logger.warning("无可用代理，降级直连")
        return ""


def check_now() -> dict:
    """手动触发一次检查（强制），返回最新状态。"""
    resolve(force=True)
    return status()


def start_background_check(interval: float = _BG_INTERVAL) -> None:
    """后台线程持续验证代理可用性（幂等，已在运行则不重复启动）。"""
    global _bg_thread
    if _bg_thread is not None and _bg_thread.is_alive():
        return
    _bg_stop.clear()

    def _loop():
        while not _bg_stop.is_set():
            _bg_stop.wait(interval)
            if _bg_stop.is_set():
                break
            # 无论是否手动配置都自动探测（自动检测 + 自动更新）
            resolve(force=True)

    _bg_thread = threading.Thread(target=_loop, daemon=True, name="proxy-bg-check")
    _bg_thread.start()
    logger.info(f"代理后台检查已启动：每 {interval}s 验证一次")


def stop_background_check() -> None:
    _bg_stop.set()
