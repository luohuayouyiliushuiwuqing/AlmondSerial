"""站点 Cookie 注入：按域名给抓取请求注入 Cookie。

Cloudflare 等反爬站在真实浏览器里通过验证后，把 Cookie 复制给程序、抓取时带上即可绕过。
配置：环境变量 `SITE_COOKIES`，JSON 格式，域名 → Cookie 串：

    SITE_COOKIES='{"fourhoi.com": "cf_clearance=xxx; foo=bar"}'

按域名匹配（含子域名：`a.fourhoi.com` 命中 `fourhoi.com`）。
"""

import json
import os
from urllib.parse import urlparse

_COOKIES: dict[str, str] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    raw = os.environ.get("SITE_COOKIES", "")
    if not raw:
        return
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return
    if isinstance(data, dict):
        for domain, val in data.items():
            if isinstance(val, str) and val.strip():
                _COOKIES[domain.lower()] = val.strip()


def parse_cookie_header(header: str) -> dict:
    """把 `k=v; k2=v2` 解析成 `{k: v}`。"""
    out: dict = {}
    for part in (header or "").split(";"):
        if "=" not in part:
            continue
        k, _, v = part.strip().partition("=")
        if k:
            out[k.strip()] = v.strip()
    return out


def get_cookie(url: str) -> dict | None:
    """按域名（含子域名）匹配 Cookie，返回 `{k: v}` 或 None。"""
    _load()
    if not _COOKIES:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    for domain, header in _COOKIES.items():
        if host == domain or host.endswith("." + domain):
            return parse_cookie_header(header)
    return None


def get_cookie_header(url: str) -> str | None:
    """返回可直接放进 `Cookie:` 请求头的原始串（用于 urllib），无则 None。"""
    _load()
    if not _COOKIES:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    for domain, header in _COOKIES.items():
        if host == domain or host.endswith("." + domain):
            return header
    return None
