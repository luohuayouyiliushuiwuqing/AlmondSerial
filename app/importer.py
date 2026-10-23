"""文件导入解析：txt / csv / html → 链接列表（去重 + 域名 + 番号）。

导入文件里的链接含目标链接与脏链接，本模块只负责**抽取 + 标注**，不做任何入库：
产出 `[{url, domain, code}]` 供前端筛选器按域名过滤、选择性确认后再真正导入。
"""

import csv
import io
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from .codes import extract_code

_URL_RE = re.compile(r'https?://[^\s"\'<>()\[\]{}]+')
_TRAIL = re.compile(r'[.,;:!?、，。；：）】]+$')


def _root_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _clean(url: str) -> str:
    return _TRAIL.sub("", (url or "").strip())


def extract_urls(text: str) -> list[str]:
    """从任意文本里抽出 http(s) 链接，去末尾标点。"""
    out: list[str] = []
    for m in _URL_RE.findall(text or ""):
        u = _clean(m)
        if u:
            out.append(u)
    return out


class _HrefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            for k, v in attrs:
                if k.lower() == "href":
                    self.hrefs.append(v)


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def parse(content: bytes, filename: str) -> list[dict]:
    """解析上传文件，返回去重后的 [{url, domain, code}]（code 为 None 表示识别不出番号）。"""
    name = (filename or "").lower()
    urls: list[str] = []

    if name.endswith((".html", ".htm")):
        text = _decode(content)
        p = _HrefParser()
        p.feed(text)
        for h in p.hrefs:
            if h.startswith(("http://", "https://")):
                urls.append(h)
        urls += extract_urls(text)  # 兜底：正文里直接出现的完整链接
    elif name.endswith(".csv"):
        text = _decode(content)
        for row in csv.reader(io.StringIO(text)):
            for cell in row:
                urls += extract_urls(cell)
    else:  # txt / 其他：按纯文本扫
        urls = extract_urls(_decode(content))

    seen: set[str] = set()
    out: list[dict] = []
    for u in urls:
        u = u.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "domain": _root_domain(u), "code": extract_code(u)})
    return out
