"""番号（条目唯一关键字）的提取与规范化。

番号是条目在库里的唯一关键字（如 `ABP-123` / `SSIS-001` / `FC2-PPV-1234567`）。
链接只是「导入番号的入口」：同一番号可挂多个链接（不同站点/镜像），入库时以番号去重。

提取分两层：
1. 站点规则（SITE_RULES）：按 host 子串命中专用正则（命名组 code），最准。
2. 通用回退：扫 URL 的路径段与查询值，找「字母 + 数字」形状、且不在 denylist 里的 token。
"""

import re
from urllib.parse import parse_qs, unquote, urlparse

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*")

# 非番号词（命中即弃）：URL 里常见的资源/分页/动作词
_PREFIX_DENYLIST = {
    "page", "video", "watch", "item", "detail", "article", "show",
    "list", "tag", "actor", "actress", "genre", "search", "category",
    "chapter", "episode", "index", "sample", "preview", "thumb",
    "image", "photo", "content", "entry", "post", "news", "title",
    "rank", "user", "channel", "play", "movie", "adult", "porn",
}

# 站点专用规则：host 子串 → 正则（命名组 code）
SITE_RULES: dict[str, re.Pattern] = {}


def normalize_code(raw: str) -> str:
    """规范番号：大写、统一分隔符为 `-`、去首尾空白；无分隔符的「字母+数字」补 `-`。"""
    text = (raw or "").strip()
    if not text:
        return ""
    text = text.replace("－", "-").replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    text = text.upper().strip("-")
    m = re.fullmatch(r"([A-Z]+)(\d+)", text)
    if m:
        text = f"{m.group(1)}-{m.group(2)}"
    return text


def _looks_like_code(token: str) -> bool:
    if not (3 <= len(token) <= 30):
        return False
    if not re.search(r"[A-Za-z]", token):
        return False
    if not re.search(r"\d", token):
        return False
    return True


def _prefix_of(code: str) -> str:
    m = re.match(r"([A-Za-z]+)", code)
    return (m.group(1) if m else "").lower()


def _first_code_token(candidate: str) -> str | None:
    for tok in _TOKEN_RE.findall(candidate or ""):
        if not _looks_like_code(tok):
            continue
        norm = normalize_code(tok)
        if _prefix_of(norm) in _PREFIX_DENYLIST:
            continue
        return norm
    return None


def _candidate_tokens(url: str) -> list[str]:
    try:
        p = urlparse(url)
    except Exception:
        return []
    segs = [s for s in (unquote(p.path).split("/")) if s.strip()]
    path_tokens = [re.sub(r"\.[A-Za-z0-9]{1,6}$", "", s) for s in segs]
    path_tokens.reverse()
    try:
        query_tokens = [unquote(v) for vs in parse_qs(p.query).values() for v in vs]
    except Exception:
        query_tokens = []
    return path_tokens + query_tokens


def extract_code(url: str) -> str | None:
    """从链接提取番号；识别不出返回 None。入参可为完整 URL 或裸番号字符串。"""
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        host = (urlparse(raw).hostname or "").lower()
    except Exception:
        host = ""
    for host_sub, rx in SITE_RULES.items():
        if host_sub in host:
            m = rx.search(raw)
            if m and m.group("code"):
                return normalize_code(m.group("code"))
    for candidate in _candidate_tokens(raw):
        code = _first_code_token(candidate)
        if code:
            return code
    return None
