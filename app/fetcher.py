"""抓取：scrapling 静态 TLS 指纹抓取 + 元数据/详情字段提取。"""

from urllib.parse import urljoin

from loguru import logger
from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher

from . import cookies as cookie_ctl
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

    thumbnail = (
        attr('meta[property="og:image"]::attr(content)')
        or attr('meta[property="og:image:url"]::attr(content)')
        or attr('meta[property="og:image:secure_url"]::attr(content)')
        or attr('meta[name="twitter:image"]::attr(content)')
        or attr('meta[name="twitter:image:src"]::attr(content)')
        or attr('link[rel="image_src"]::attr(href)')
    )
    favicon = attr('link[rel*="icon"]::attr(href)')

    return {
        "title": title.strip() if title else "",
        "thumbnail": urljoin(base_url, thumbnail) if thumbnail else "",
        "favicon": urljoin(base_url, favicon) if favicon else "",
    }


# 反爬拦截的常见状态码：拿到它们才值得降级浏览器；404 等普通失败不降级（避免白启动浏览器）
_BLOCK_STATUSES = (401, 403, 429, 503)


def _extract(url: str, response, selector: str | None) -> dict:
    """从成功响应提取元数据 + 详情字段。"""
    base_url = getattr(response, "url", None) or url
    meta = {"url": url, "success": True, "details": []}
    meta.update(extract_metadata(response, base_url))
    try:
        meta["details"] = extract_fields(response.css(selector or DETAIL_SELECTOR))
    except Exception:  # noqa: BLE001  个别页面/非法选择器出错不影响主元数据
        meta["details"] = []
    return meta


def fetch_page(url: str, proxy: str | None = None,
               selector: str | None = None, timeout: int = 20) -> dict:
    """三级降级抓取：Fetcher（静态 TLS 指纹）→ DynamicFetcher（JS 渲染）→ StealthyFetcher（反 Cloudflare）。

    - proxy 缺省（None）用**全局代理**（`proxyctl.resolve()`，含 TTL 缓存与自动切换）。
    - Cookie 按域名从 `SITE_COOKIES` 环境变量注入（绕过 Cloudflare 等反爬）。
    - 静态成功即返回；被反爬（401/403/429/503）才降级浏览器；其余失败直接放弃该级。
    """
    if proxy is None:
        proxy = proxyctl.resolve() or None  # 空串 → 直连（None）
    cookies = cookie_ctl.get_cookie(url)

    def _static(p):
        return Fetcher.get(url, timeout=timeout, proxy=p, cookies=cookies)

    def _dynamic(p):
        return DynamicFetcher.fetch(url, timeout=timeout * 1000, proxy=p, cookies=cookies)

    def _stealth(p):
        return StealthyFetcher.fetch(url, timeout=timeout * 1000, proxy=p, cookies=cookies)

    # ── 静态层：代理优先，网络错误时回退直连 ──
    routes = [proxy] if proxy else [None]
    if proxy is not None:
        routes.append(None)
    for p in routes:
        try:
            resp = _static(p)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"静态{'代理' if p else '直连'}异常：{url}（{type(exc).__name__}: {exc}）")
            continue
        st = getattr(resp, "status", None)
        if resp is not None and (st is None or st < 400):
            return _extract(url, resp, selector)
        if st in _BLOCK_STATUSES:
            logger.warning(f"静态被反爬（HTTP {st}），降级浏览器：{url}")
            break
        logger.warning(f"静态返回 HTTP {st}：{url}")

    # ── 浏览器层：dynamic → stealth（被反爬/JS 渲染时才走到这） ──
    for ename, engine in (("dynamic", _dynamic), ("stealth", _stealth)):
        try:
            resp = engine(proxy)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{ename} 异常：{url}（{type(exc).__name__}: {exc}）")
            continue
        st = getattr(resp, "status", None)
        if resp is not None and (st is None or st < 400):
            return _extract(url, resp, selector)
        logger.warning(f"{ename} 被反爬（HTTP {st}）：{url}")

    return {"url": url, "title": "", "thumbnail": "", "favicon": "", "success": False, "details": []}


def check_domain(domain: str, proxy: str | None = None, timeout: int = 10) -> dict:
    """探测域名是否可达：请求 https://domain/ 看是否有正常 HTTP 响应。

    判定「可用」只看请求能否完成（得到任意状态码），连接失败/DNS/超时才算挂掉。
    proxy 缺省用全局代理（与抓取一致）。
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return {"domain": domain, "ok": False, "status": None, "error": "空域名"}
    if proxy is None:
        proxy = proxyctl.resolve() or None
    url = f"https://{domain}/"
    try:
        from curl_cffi import requests as cf
        proxies = {"http": proxy, "https": proxy} if proxy else None
        resp = cf.get(url, timeout=timeout, proxies=proxies,
                      impersonate="chrome", allow_redirects=True)
        return {"domain": domain, "ok": True, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"domain": domain, "ok": False, "status": None,
                "error": f"{type(exc).__name__}: {exc}"}
