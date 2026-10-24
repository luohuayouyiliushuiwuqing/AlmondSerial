"""图片本地缓存：抓到的图片字节落盘，命中后直接读缓存，避免重复抓取同一张图。

缓存位置**永远是当前数据库所在目录下的 ``cache/img``**（不与项目目录、也不与其它
布局扯上关系：库搬到哪，缓存就在哪；目录不存在时写入会新建）。缓存键是图片 URL 的
SHA1，与路径无关，所以整个目录连库带缓存一起搬家不会丢命中。环境变量
``IMG_CACHE_DIR`` 可指定固定目录（测试也直接改 ``CACHE_DIR``）。Content-Type 存同名
``.meta``。
"""

import hashlib
import os
import threading
from datetime import datetime
from pathlib import Path

from loguru import logger

from . import db

# 显式覆盖：环境变量或运行时赋值（测试用）。为 None 时随数据库目录走
_ENV_DIR = (os.environ.get("IMG_CACHE_DIR") or "").strip()
CACHE_DIR: Path | None = Path(_ENV_DIR) if _ENV_DIR else None

# 库目录下的缓存位置（唯一约定）
CACHE_SUBDIR = ("cache", "img")

_LOCK = threading.Lock()


def cache_root() -> Path:
    """缓存根目录 = ``<当前数据库目录>/cache/img``（不存在则写入时新建）。"""
    if CACHE_DIR is not None:
        return CACHE_DIR
    return db.current_path().parent.joinpath(*CACHE_SUBDIR)


def _key(src: str) -> str:
    return hashlib.sha1(src.encode("utf-8")).hexdigest()


def cache_path(src: str) -> Path:
    return cache_root() / _key(src)


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
        p = _fail_path(src)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
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
            p = cache_path(src)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            Path(str(p) + ".meta").write_text(ctype or "application/octet-stream", encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            logger.warning(f"图片缓存写入失败：{exc}")
            return
    clear_failed(src)
