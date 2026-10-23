"""详情代理：通过可配置的远程服务获取番号结构化详细信息。

代理是**增强通道（非必需）**：网页抓取拿不到的字段，尝试从代理补全。代理未配置 /
不可用 / 超时 / 返回异常时一律静默降级（返回 None），绝不阻塞主流程。

接口约定（HTTP JSON，POST {DETAIL_PROXY_URL}/lookup）：

    请求  { "code": "ABP-123", "url": "https://...", "fields": ["title", ...] }
    响应  { "ok": true, "data": { "title", "cover", "release_date", "maker",
                                   "series", "duration", "rating",
                                   "actors": [], "genres": [], "details": [] } }

地址由环境变量 `DETAIL_PROXY_URL` 指定；未设置则整条通道关闭（enabled() 为 False）。
"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from loguru import logger

# 代理 base URL（例如 http://127.0.0.1:9000）；未配置则禁用该通道
BASE_URL = os.environ.get("DETAIL_PROXY_URL", "").strip().rstrip("/")

# 代理返回的字段清单（与 entries 表 / attributes.classify 对齐）
PROXY_FIELDS = (
    "title", "cover", "release_date", "maker", "director", "label", "series",
    "duration", "rating", "actors", "genres", "details",
)

_TIMEOUT = 10


def enabled() -> bool:
    """代理通道是否启用（未配置地址即关闭）。"""
    return bool(BASE_URL)


def lookup(code: str, url: str | None = None, timeout: int = _TIMEOUT) -> dict | None:
    """调用代理查番号详情，返回规整后的字段 dict；任何失败返回 None。"""
    if not enabled():
        return None

    payload = json.dumps({
        "code": code,
        "url": url or "",
        "fields": list(PROXY_FIELDS),
    }).encode("utf-8")

    req = Request(
        f"{BASE_URL}/lookup",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as exc:
        logger.warning(f"详情代理返回 HTTP {exc.code}：{code}")
        return None
    except URLError as exc:
        logger.warning(f"详情代理不可达：{code}（{exc.reason}）")
        return None
    except Exception as exc:  # noqa: BLE001 —— 超时 / 连接重置等
        logger.warning(f"详情代理请求失败：{code}（{type(exc).__name__}: {exc}）")
        return None

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"详情代理返回非 JSON：{code}")
        return None
    if not isinstance(body, dict):
        logger.warning(f"详情代理响应结构异常：{code}")
        return None

    return _normalize(body.get("data"))


def _normalize(data) -> dict | None:
    """把代理返回的数据规整成与 entries 字段一致的 dict；无效返回 None。"""
    if not isinstance(data, dict):
        return None
    out: dict = {}
    for f in PROXY_FIELDS:
        v = data.get(f)
        if f in ("actors", "genres"):
            if isinstance(v, list):
                out[f] = [x.strip() for x in v if isinstance(x, str) and x.strip()]
            else:
                out[f] = []
        elif f == "details":
            out[f] = [d for d in v if isinstance(d, dict)] if isinstance(v, list) else []
        else:
            out[f] = v.strip() if isinstance(v, str) else ""
    return out
