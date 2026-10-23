"""图片本地缓存：抓到的图片字节落盘，命中后直接读缓存，避免重复抓取同一张图。

缓存位置 `data/cache/img/`（与数据库同目录，一起随存储目录走；可被环境变量
`IMG_CACHE_DIR` 覆盖）。缓存键 = 图片 URL 的 SHA1 哈希；Content-Type 存同名 `.meta`。
"""

import hashlib
import os
import threading
from datetime import datetime
from pathlib import Path

from loguru import logger

CACHE_DIR = Path(
    os.environ.get("IMG_CACHE_DIR")
    or (Path(__file__).resolve().parent.parent / "data" / "cache" / "img")
)

_LOCK = threading.Lock()


def _key(src: str) -> str:
    return hashlib.sha1(src.encode("utf-8")).hexdigest()


def cache_path(src: str) -> Path:
    return CACHE_DIR / _key(src)


def _fail_path(src: str) -> Path:
    return Path(str(cache_path(src)) + ".fail")


def has(src: str) -> bool:
    """缓存是否存在（只查存在性，不读字节）。"""
    return cache_path(src).exists()


def is_failed(src: str) -> bool:
    """该图像链接是否已被标记为「抓取失败」（暂时不可行，不再自动重试）。"""
    return _fail_path(src).exists()


def mark_failed(src: str) -> None:
    """标记图像链接抓取失败：后续 /api/img 直接 404，不再重复抓取。"""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _fail_path(src).write_text(
            datetime.now().isoformat(timespec="seconds"), encoding="utf-8"
        )
    except OSError as exc:  # noqa: BLE001
        logger.warning(f"图片失败标记写入失败：{exc}")


def clear_failed(src: str) -> None:
    """清除失败标记（成功抓到图或用户手动重试时）。"""
    try:
        p = _fail_path(src)
        if p.exists():
            p.unlink()
    except OSError as exc:  # noqa: BLE001
        logger.warning(f"图片失败标记清除失败：{exc}")


def get(src: str) -> tuple[bytes, str] | None:
    """读缓存：命中返回 (bytes, content_type)，未命中返回 None。"""
    p = cache_path(src)
    if not p.exists():
        return None
    try:
        data = p.read_bytes()
        meta = Path(str(p) + ".meta")
        ctype = meta.read_text(encoding="utf-8").strip() if meta.exists() else "application/octet-stream"
        return data, ctype
    except OSError:
        return None


def put(src: str, data: bytes, ctype: str) -> None:
    """写缓存：图片字节 + Content-Type。"""
    if not data:
        return
    with _LOCK:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            p = cache_path(src)
            p.write_bytes(data)
            Path(str(p) + ".meta").write_text(ctype or "application/octet-stream", encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            logger.warning(f"图片缓存写入失败：{exc}")
            return
    clear_failed(src)
