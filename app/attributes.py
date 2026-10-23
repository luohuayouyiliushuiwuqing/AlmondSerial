"""「标签: 值」行 → 类型化条目字段的映射（动漫条目的属性解析）。

字段行有两种取值形态：
- 纯文本：`<span>标签:</span><span>值</span>` → 值在文本里；
- 链接形态：`<span>演员:</span><a href="...">演员名</a>` → 值在链接文字里，
  此时 `fields.extract_fields` 会把链接文字从 value 里剥掉、放进 `links[].text`。

本模块：
1. 按标签别名归类（含中英日同义、繁体/简体变体）；
2. 标签未命中时，用链接 href 的「作用通配识别」兜底——忽略路径里的 `dmXXX` 等
   装饰段，只认 `/cn/genres/`、`/cn/actresses/` 这类关键段；
3. 对演员/类型等**多值字段**，value 为空时从 `links[].text` 取值。
"""

import re

from .codes import extract_code, normalize_code

# 字段 → 标签别名（比对时对标签做：去空白、去末尾冒号、小写）
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "release_date": ("发行日期", "发布日期", "发售日", "発売日", "发行时间", "上市日期",
                     "release date", "release"),
    "code": ("番号", "品番", "编号", "code", "number"),
    "title": ("标题", "片名", "title"),
    "actors": ("演员", "出演", "出演者", "主演", "女优", "女優",
               "cast", "actress", "actresses", "performer", "performers"),
    "genres": ("类型", "类别", "分类", "种类", "種別", "题材", "genre", "genres"),
    "maker": ("片商", "发行商", "製作商", "制作", "制片", "メーカー", "制造商",
              "maker", "studio", "manufacturer"),
    "director": ("导演", "監督", "director"),
    "label": ("标籤", "標籤", "标签", "標签", "厂牌", "廠牌", "label", "labels"),
    "series": ("系列", "シリーズ", "series"),
    "duration": ("时长", "片长", "片長", "収録時間", "播放时长",
                 "duration", "length", "runtime"),
    "rating": ("评分", "评价", "評分", "評判", "rating", "score"),
}

# 链接作用通配识别：只认关键路径段，路径中间的 dmXXX 等装饰段被通配忽略——
# 例如 https://x.com/dm549/cn/genres/xxx 与 dm206/cn/genres/xxx 认定同一「类型」作用。
_LINK_KIND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("actor", re.compile(r"/cn/actresses/", re.I)),
    ("genre", re.compile(r"/cn/genres/", re.I)),
    ("maker", re.compile(r"/cn/makers/", re.I)),
    ("director", re.compile(r"/cn/directors/", re.I)),
    ("label", re.compile(r"/cn/labels/", re.I)),
]
_KIND_TO_FIELD = {
    "actor": "actors", "genre": "genres", "maker": "maker",
    "director": "director", "label": "label",
}

_LIST_SPLIT_RE = re.compile(r"[,，、/／;；\n]+")
_DATE_RE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?")


def _norm_label(label: str) -> str:
    return re.sub(r"\s+", "", (label or "")).rstrip("：:").lower()


_ALIAS_MAP: dict[str, str] = {}
for _field, _aliases in FIELD_ALIASES.items():
    for _a in _aliases:
        _ALIAS_MAP.setdefault(_norm_label(_a), _field)


def link_kind(href: str) -> str | None:
    """识别链接的作用类型（actor/genre/maker/director/label）。

    通配忽略路径中间的装饰段（如 dm549）——不同装饰段认定为同一作用。
    """
    if not href:
        return None
    for kind, rx in _LINK_KIND_PATTERNS:
        if rx.search(href):
            return kind
    return None


def _split_list(value: str) -> list[str]:
    return [s.strip() for s in _LIST_SPLIT_RE.split(value or "") if s.strip()]


def _link_texts(links) -> list[str]:
    return [l["text"].strip() for l in links
            if isinstance(l, dict) and (l.get("text") or "").strip()]


def _parse_date(value: str, datetime_val: str) -> str:
    if datetime_val:
        m = _DATE_RE.search(datetime_val)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _DATE_RE.search(value or "")
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


# 「标签/標籤」跨站点语义不一：有的站点用它表示类型(genres)，有的站点（链接指向 /cn/labels/）
# 用它表示厂牌(label)。这里按链接作用消歧；无链接时按用户反馈默认归为类型(genres)。
_TAG_LABELS = {"标签", "标籤", "標籤", "標签"}


def _first_link_kind(links) -> str | None:
    for l in links or []:
        if isinstance(l, dict):
            kind = link_kind(l.get("href"))
            if kind:
                return kind
    return None


def _resolve_field(label: str, links: list) -> str | None:
    """标签文字 + 链接 href → 类型化字段（含「标签/標籤」的跨站点消歧）。"""
    field = _ALIAS_MAP.get(label)
    if field == "label" and label in _TAG_LABELS:
        kind = _first_link_kind(links)
        return _KIND_TO_FIELD.get(kind) if kind else "genres"
    if field:
        return field
    # 标签文字未命中 → 用链接 href 作用通配识别兜底
    kind = _first_link_kind(links)
    return _KIND_TO_FIELD.get(kind) if kind else None


def classify(details: list[dict]) -> dict:
    """把通用详情行归类成类型化字段（首个命中优先；值在文本或链接文字里都能取到）。"""
    out = {
        "title": "", "code": "", "release_date": "",
        "maker": "", "director": "", "label": "", "series": "", "duration": "", "rating": "",
        "actors": [], "genres": [],
    }
    for row in details or []:
        if not isinstance(row, dict):
            continue
        label = _norm_label(row.get("label") or "")
        field = _resolve_field(label, row.get("links") or [])
        if not field:
            continue

        value = (row.get("value") or "").strip()
        datetime_val = row.get("datetime") or ""
        links = row.get("links") or []

        if field == "release_date":
            d = _parse_date(value, datetime_val)
            if d and not out["release_date"]:
                out["release_date"] = d
        elif field == "code":
            code = extract_code(value) or normalize_code(value)
            if code and not out["code"]:
                out["code"] = code
        elif field in ("actors", "genres"):
            # 值可能在文本，也可能在链接文字里（演员/类型常是 <a> 链接）
            items = _split_list(value) or _link_texts(links)
            if items and not out[field]:
                out[field] = items
        elif field == "title":
            if value and not out["title"]:
                out["title"] = value
        else:  # maker / director / label / series / duration / rating
            if not out[field]:
                texts = _link_texts(links)
                out[field] = value or (texts[0] if texts else "")
    return out


def field_links(details: list[dict]) -> dict[str, list[dict]]:
    """从详情行提取「类型化字段名 → 链接列表」，供前端把文字渲染成可跳转链接。

    链接保留 text（显示）+ href（跳转）；标签文字未命中时用链接 href 作用通配兜底。
    """
    out: dict[str, list[dict]] = {}
    for row in details or []:
        if not isinstance(row, dict):
            continue
        label = _norm_label(row.get("label") or "")
        field = _resolve_field(label, row.get("links") or [])
        if not field:
            continue
        links = [
            {"text": (l.get("text") or "").strip(), "href": l.get("href") or ""}
            for l in row.get("links") or []
            if isinstance(l, dict) and l.get("href")
        ]
        if links and field not in out:
            out[field] = links
    return out
