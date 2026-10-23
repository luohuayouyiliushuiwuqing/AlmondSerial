"""抓取：scrapling 静态 TLS 指纹抓取 + 元数据/详情字段提取。"""

from urllib.parse import urljoin

from loguru import logger
from scrapling.fetchers import Fetcher

from . import proxy as proxyctl
from .fields import extract_fields

# 详情区块选择器：抓取成功后顺带解析「标签: 值」字段，无匹配时 details 为空列表
DETAIL_SELECTOR = ".space-y-2 > *"


def extract_metadata(response, base_url: str) -> dict:
    """在 Response 上用 CSS 选择器按优先级提取标题/缩略图/favicon。"""

    def attr(selector: str) -> str:
        return response.css(selector).extract_first() or ""

    title = (
        attr('meta[property="og:title"]::attr(content)')
        or attr('meta[name="twitter:title"]::attr(content)')
    )
    if not title:
        t = response.css("title")
        if t:
            title = t[0].text.strip()

    thumbnail = attr('meta[property="og:image"]::attr(content)')
    favicon = attr('link[rel*="icon"]::attr(href)')

    return {
        "title": title.strip() if title else "",
        "thumbnail": urljoin(base_url, thumbnail) if thumbnail else "",
        "favicon": urljoin(base_url, favicon) if favicon else "",
    }


def fetch_page(url: str, proxy: str | None = None,
               selector: str | None = None, timeout: int = 20) -> dict:
    """抓取一个 URL 的元数据与详情字段。全部失败时仅保留 URL，标题留空供手动补充。

    proxy 缺省（None）时用**全局代理**（`proxyctl.resolve()`，含 TTL 缓存与自动切换），
    避免每条链接单独提取代理；显式传值仍可覆盖（用于测试/特殊场景）。
    """
    if proxy is None:
        proxy = proxyctl.resolve() or None  # 空串 → 直连（None）
    meta = {"url": url, "title": "", "thumbnail": "", "favicon": "", "success": False, "details": []}
    try:
        response = Fetcher.get(url, timeout=timeout, proxy=proxy)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"抓取失败：{url}（{type(exc).__name__}: {exc}）")
        return meta

    status = getattr(response, "status", None)
    if response is None or (status is not None and status >= 400):
        logger.warning(f"抓取返回 HTTP {status}：{url}")
        return meta

    base_url = getattr(response, "url", None) or url
    meta["success"] = True
    meta.update(extract_metadata(response, base_url))
    try:
        meta["details"] = extract_fields(response.css(selector or DETAIL_SELECTOR))
    except Exception:  # noqa: BLE001  个别页面/非法选择器出错不影响主元数据
        meta["details"] = []
    return meta
