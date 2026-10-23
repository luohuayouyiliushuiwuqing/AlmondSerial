"""「标签: 值」行结构的字段解析（如 div.space-y-2 里的 发行日期/番号/演员…）。

给定选择器匹配到的元素列表（scrapling SelectorElement），规则为行的第一个直接子元素是
<span>（标签），其后是纯文本 / <time> / <a>。产物统一 `{label, value, links, datetime}`。
"""

MAX_FIELDS = 30


def _collapse(text: str) -> str:
    return " ".join((text or "").split())


def extract_fields(elements) -> list[dict]:
    fields: list[dict] = []
    for el in list(elements)[:MAX_FIELDS]:
        children = list(getattr(el, "children", None) or [])
        if not children or getattr(children[0], "tag", None) != "span":
            continue
        label_full = _collapse(children[0].get_all_text())
        label = label_full.rstrip("：: ").strip()
        full = _collapse(el.get_all_text())
        value = full[len(label_full):] if full.startswith(label_full) else full.replace(label_full, "", 1)
        value = value.strip().lstrip(",，").strip()

        times = el.css("time")
        datetime_val = times[0].attrib.get("datetime", "") if times else ""
        links = [{"text": _collapse(a.get_all_text()), "href": a.attrib.get("href", "")}
                 for a in el.css("a")]
        for l in links:
            if l["text"]:
                value = value.replace(l["text"], "", 1)
        value = _collapse(value).strip(" ,，、;；")
        if not label or (not value and not links and not datetime_val):
            continue
        fields.append({"label": label, "value": value, "links": links, "datetime": datetime_val})
    return fields
