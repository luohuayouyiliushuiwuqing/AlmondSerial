"""抓取：scrapling 静态 TLS 指纹抓取 + 元数据/详情字段提取。"""

from urllib.parse import urljoin, urlparse
from urllib.request import ProxyHandler, Request, build_opener

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


def _failure(url: str) -> dict:
    return {"url": url, "title": "", "thumbnail": "", "favicon": "",
            "success": False, "details": []}


def fetch_page(url: str, proxy: str | None = None,
               selector: str | None = None, timeout: int = 20,
               prefer_render: bool = False) -> dict:
    """三级降级抓取：Fetcher（静态 TLS 指纹）→ DynamicFetcher（JS 渲染）→ StealthyFetcher（反 Cloudflare）。

    - proxy 缺省（None）用**全局代理**（`proxyctl.resolve()`，含 TTL 缓存与自动切换）。
    - Cookie 按域名从 `SITE_COOKIES` 环境变量注入（绕过 Cloudflare 等反爬）。
    - 默认：静态成功即返回；失败再降级浏览器层。
    - ``prefer_render=True``：**先走渲染层**（dynamic → stealth），再回退静态。用于
      「详情抓不到」的重抓——同样的静态请求再发一次大概率还是空，换 JS 渲染才有意义。
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

    def static_layers() -> dict | None:
        """静态层：代理优先，网络错误时回退直连。"""
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
        return None

    def render_layers() -> dict | None:
        """浏览器层：dynamic → stealth。"""
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
        return None

    if prefer_render:
        return render_layers() or static_layers() or _failure(url)
    return static_layers() or render_layers() or _failure(url)


_IMAGE_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
_ALT_IMPERSONATE = "safari17_0"   # 403 时换一套 TLS 指纹再试


def _origin(url: str) -> str:
    try:
        p = urlparse(url)
    except Exception:  # noqa: BLE001
        return ""
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


def _site_of(host: str) -> str:
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "")


def _sec_fetch_site(src: str, referer: str) -> str:
    if not referer:
        return "cross-site"
    try:
        a, b = urlparse(src), urlparse(referer)
    except Exception:  # noqa: BLE001
        return "cross-site"
    if (a.scheme, a.netloc) == (b.scheme, b.netloc):
        return "same-origin"
    if _site_of(a.hostname or "") == _site_of(b.hostname or ""):
        return "same-site"
    return "cross-site"


def _cookie_header(src: str, referer: str) -> str:
    """合并「图片域名 + 来路页域名」两边的 Cookie（图片域名优先）。

    有些站点的云防护 Cookie（如 cf_clearance）只对页面域名下发，图片却在另一台
    CDN/子域上；两边合在一起带上，能救回一部分 403。
    """
    seen: set[str] = set()
    parts: list[str] = []
    for url in (src, referer):
        if not url:
            continue
        header = cookie_ctl.get_cookie_header(url)
        if not header:
            continue
        for part in header.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            key = part.split("=", 1)[0].strip()
            if key in seen:
                continue
            seen.add(key)
            parts.append(part)
    return "; ".join(parts)


def _image_headers(src: str, referer: str, cookie: str) -> dict:
    """按「浏览器加载 <img>」的样子构造请求头（CF 会校验 Sec-Fetch-*）。"""
    headers = {
        "Accept": _IMAGE_ACCEPT,
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": _sec_fetch_site(src, referer),
    }
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _referer_candidates(src: str, referer: str) -> list[str]:
    """Referer 尝试顺序：来路页 → 图片自身 origin → 不带 Referer。"""
    out: list[str] = []
    for ref in (referer, _origin(src), ""):
        if ref not in out:
            out.append(ref)
    return out


def _cf_image_get(src: str, proxy_url: str | None, headers: dict, timeout: int,
                  impersonate: str) -> tuple[int | None, bytes | None, str, str]:
    """curl_cffi 取图；返回 (status, body, ctype, server)，网络异常 status=None。"""
    try:
        from curl_cffi import requests as cf
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        resp = cf.get(src, headers=headers, proxies=proxies, timeout=timeout,
                      impersonate=impersonate, allow_redirects=True)
        server = resp.headers.get("server", "") or ""
        if resp.status_code < 400 and resp.content:
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.status_code, resp.content, ctype, server
        return resp.status_code, None, "", server
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"curl_cffi 抓图异常（{impersonate}）：{src}（{type(exc).__name__}: {exc}）")
        return None, None, "", ""


def _urllib_image_get(src: str, proxy_url: str | None, headers: dict,
                      timeout: int) -> tuple[bytes, str] | None:
    try:
        req = Request(src, headers={"User-Agent": "Mozilla/5.0", **headers})
        opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url})) if proxy_url else build_opener()
        with opener.open(req, timeout=timeout) as r:
            data = r.read()
            if not data:
                return None
            return data, r.headers.get("Content-Type", "application/octet-stream")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"urllib 抓图失败：{src}（{type(exc).__name__}: {exc}）")
        return None


def fetch_image_bytes(src: str, proxy_url: str | None, timeout: int = 15,
                      referer: str = "") -> tuple[bytes, str] | None:
    """抓取图片字节（Cookie + 防盗链 Referer）；失败返回 None。

    针对 Cloudflare 403，按代价从低到高尝试：
    1. 默认 TLS 指纹（chrome）+ 多种 Referer（来路页 → 图片自身 origin → 不带），
       并合并「图片域名 + 来路页域名」两边的 Cookie；
    2. 仍 403 → 换一套 TLS 指纹（safari）再试；若响应头仍是 Cloudflare，
       urllib 必然也过不了，直接放弃（少一次无用请求）；
    3. 最后才用 urllib 兜底（无 TLS 指纹，只对普通防盗链有效）。
    """
    cookie = _cookie_header(src, referer)
    referers = _referer_candidates(src, referer)

    status: int | None = None
    server = ""
    for ref in referers:
        status, body, ctype, srv = _cf_image_get(
            src, proxy_url, _image_headers(src, ref, cookie), timeout, "chrome")
        server = server or srv
        if body:
            return body, ctype
        if status is None:
            break  # 网络异常：换 Referer 没有意义，交给 urllib 兜底
        if status != 403:
            logger.warning(f"抓图 HTTP {status}：{src}")
            return None  # 404/410 等明确失败，换策略也一样

    if status == 403:
        # 换 TLS 指纹（用原 Referer）再试一次
        _, body, ctype, alt_server = _cf_image_get(
            src, proxy_url, _image_headers(src, referer, cookie), timeout, _ALT_IMPERSONATE)
        if body:
            return body, ctype
        if "cloudflare" in (server + alt_server).lower():
            logger.warning(f"抓图被 Cloudflare 拦截（可刷新 SITE_COOKIES 或稍后再试）：{src}")
            return None

    got = _urllib_image_get(src, proxy_url, _image_headers(src, referer, cookie), timeout)
    if got is None:
        logger.warning(f"抓图失败（所有策略均未成功）：{src}")
    return got


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
