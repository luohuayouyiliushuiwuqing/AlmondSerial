"""动漫管理器后端：FastAPI 应用 + REST 路由 + 静态托管。

运行：
    cd AlmondSerial
    /home/igs/miniconda3/envs/xingren/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import urllib.request
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import base64

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cookies as cookie_ctl
from . import entries, imgcache, importer, proxy
from .fetcher import fetch_page

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="动漫管理器（番号库）")


@app.on_event("startup")
def _startup():
    # 启动后台持续验证代理可用性（每 60s 强制探测一次；未配置代理时为空转）
    proxy.start_background_check()


# ──────────────────────── 请求体模型 ────────────────────────

class ImportRequest(BaseModel):
    urls: list[str]


class FetchRequest(BaseModel):
    url: str


class ProxyRequest(BaseModel):
    url: str  # 全局代理地址；空串 = 禁用（直连）


class UpdateRequest(BaseModel):
    title: str | None = None
    cover: str | None = None
    release_date: str | None = None
    maker: str | None = None
    director: str | None = None
    label: str | None = None
    series: str | None = None
    duration: str | None = None
    rating: str | None = None
    actors: list[str] | None = None
    genres: list[str] | None = None


class TagRequest(BaseModel):
    name: str


class TagEntryRequest(BaseModel):
    tag_id: int


class ImportFileRequest(BaseModel):
    filename: str
    content: str  # base64 编码的文件字节


# ──────────────────────── 条目 ────────────────────────

@app.get("/api/entries")
def api_list_entries(q: str = "", limit: int = 0, offset: int = 0):
    items = entries.list_entries(limit or None, max(0, offset), q)
    return {"entries": items, "total": entries.count_entries()}


@app.get("/api/entries/pending")
def api_pending():
    return {"urls": entries.list_pending_links()}


@app.post("/api/entries/import")
def api_import(req: ImportRequest):
    if not req.urls:
        raise HTTPException(400, "缺少 urls")
    return {"ok": True, **entries.import_links(req.urls)}


@app.get("/api/entry/{code}")
def api_get_entry(code: str):
    e = entries.get_entry(code)
    if e is None:
        raise HTTPException(404, "条目不存在")
    return e


@app.patch("/api/entry/{code}")
def api_update_entry(code: str, req: UpdateRequest):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    e = entries.update_entry(code, fields)
    if e is None:
        raise HTTPException(404, "条目不存在")
    return e


@app.delete("/api/entry/{code}")
def api_delete_entry(code: str):
    if not entries.delete_entry(code):
        raise HTTPException(404, "条目不存在")
    return {"ok": True}


# ──────────────────────── 抓取 ────────────────────────

@app.post("/api/fetch")
def api_fetch(req: FetchRequest):
    """抓取单个链接并回填番号条目，随后代理补全缺失字段（失败静默降级）。

    代理由全局代理控制模块统一解析（fetch_page 内部调用 proxy.resolve()）。
    """
    meta = fetch_page(req.url)
    e = entries.ingest_fetched(meta)
    if e is None:
        return {"ok": True, "unrecognized": True, "meta": meta}
    e = entries.enrich_entry(e["code"])
    return {"ok": True, "entry": e}


@app.post("/api/entry/{code}/fetch")
def api_fetch_entry(code: str):
    """抓取该番号的所有链接，回填结构化属性，随后代理补全。"""
    e = entries.get_entry(code)
    if e is None:
        raise HTTPException(404, "条目不存在")
    for url in e["links"]:
        meta = fetch_page(url)
        entries.ingest_fetched(meta)
    return entries.enrich_entry(code)


@app.post("/api/entry/{code}/enrich")
def api_enrich(code: str):
    """手动触发：通过详情代理补全该番号的缺失字段。"""
    e = entries.enrich_entry(code)
    if e is None:
        raise HTTPException(404, "条目不存在")
    return e


# ──────────────────────── 标签 ────────────────────────

@app.get("/api/tags")
def api_list_tags():
    return {"tags": entries.list_tags()}


@app.post("/api/tags")
def api_create_tag(req: TagRequest):
    if not req.name.strip():
        raise HTTPException(400, "缺少标签名")
    return entries.create_tag(req.name)


@app.delete("/api/tags/{tag_id}")
def api_delete_tag(tag_id: int):
    if not entries.delete_tag(tag_id):
        raise HTTPException(404, "标签不存在")
    return {"ok": True}


@app.post("/api/entry/{code}/tags")
def api_add_tag(code: str, req: TagEntryRequest):
    entries.add_tag_to_entry(code, req.tag_id)
    return {"ok": True}


@app.delete("/api/entry/{code}/tags/{tag_id}")
def api_remove_tag(code: str, tag_id: int):
    entries.remove_tag_from_entry(code, tag_id)
    return {"ok": True}


# ──────────────────────── 文件导入 ────────────────────────

@app.post("/api/import/parse")
def api_parse_file(req: ImportFileRequest):
    """解析上传的 txt/csv/html 文件，返回去重后的链接（含域名与番号标注），不入库。"""
    try:
        content = base64.b64decode(req.content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "文件内容不是合法 base64") from exc
    links = importer.parse(content, req.filename or "")
    return {"links": links, "total": len(links)}


# ──────────────────────── 导出 ────────────────────────

@app.get("/api/export")
def api_export(fmt: str = "txt"):
    """导出：txt（每行链接）/ json（完整条目）。"""
    if fmt == "json":
        return entries.list_entries()
    lines: list[str] = []
    for e in entries.list_entries():
        for url in e["links"]:
            lines.append(url)
    return Response(content="\n".join(lines) + ("\n" if lines else ""),
                    media_type="text/plain; charset=utf-8")


# ──────────────────────── 全局代理 ────────────────────────

@app.get("/api/proxy")
def api_proxy_status():
    """全局代理当前状态（配置 / 当前地址 / 可用性），供前端明确提示。"""
    return proxy.status()


@app.post("/api/proxy")
def api_proxy_set(req: ProxyRequest):
    """设置全局代理地址（空串=禁用直连），设置后立即检查并返回状态。"""
    proxy.configure(req.url)
    return proxy.check_now()


@app.post("/api/proxy/check")
def api_proxy_check():
    """手动触发代理检查（强制探测端口与转发）。"""
    return proxy.check_now()


# ──────────────────────── 图片代理 ────────────────────────

def _fetch_image_bytes(src: str, proxy_url: str | None, timeout: int = 15) -> tuple[bytes, str] | None:
    """用 urllib 抓取图片字节（带 Cookie 注入）；失败返回 None。"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": src,  # 防盗链兜底
        }
        cookie = cookie_ctl.get_cookie_header(src)
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(src, headers=headers)
        opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url})) if proxy_url else build_opener()
        with opener.open(req, timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type", "application/octet-stream")
    except Exception:
        return None


@app.get("/api/img")
def api_img(src: str):
    """服务端代抓图片（封面跨域/防盗链兜底），走**全局代理**，并**落盘缓存**。

    - 命中本地缓存 → 直接返回（避免重复抓取同一张图）。
    - 缓存未命中 → 抓取 → 写缓存 → 返回。
    - 代理端口漂移导致连接失败时，强制重新探测代理再重试一次。
    """
    if not src.startswith(("http://", "https://")):
        raise HTTPException(400, "src 必须是 http(s) 地址")

    # ① 先查本地缓存
    cached = imgcache.get(src)
    if cached is not None:
        data, ctype = cached
        return Response(content=data, media_type=ctype)

    # ② 缓存未命中 → 抓取
    proxy_url = proxy.resolve() or None
    got = _fetch_image_bytes(src, proxy_url)
    if got is None and proxy_url:
        # 代理可能失效（端口漂移/连接拒绝），强制重新探测后再试一次
        proxy_url = proxy.resolve(force=True) or None
        got = _fetch_image_bytes(src, proxy_url)
    if got is None:
        raise HTTPException(404, "图片抓取失败（源站反爬或代理不可用）")

    # ③ 落盘缓存，下次直接命中
    data, ctype = got
    imgcache.put(src, data, ctype)
    return Response(content=data, media_type=ctype)


# ──────────────────────── 静态托管（放最后） ────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
