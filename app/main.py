"""动漫管理器后端：FastAPI 应用 + REST 路由 + 静态托管。

运行：
    cd AlmondSerial
    /home/igs/miniconda3/envs/xingren/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

import base64
import logging
import threading

import anyio.to_thread
from loguru import logger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cookies as cookie_ctl
from . import actresses, db, entries, imgcache, importer, proxy, worker
from .fetcher import check_domain, fetch_page

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# 关闭 scrapling 的请求日志（如 "Fetched (200) <GET ...>"），只保留 WARNING 及以上
logging.getLogger("scrapling").setLevel(logging.WARNING)

app = FastAPI(title="动漫管理器（番号库）")

# 本地个人工具：允许跨域调用（浏览器插件 / 预览页直连后端），不涉及鉴权
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    # 沿用上次切换的数据库（路径记在项目根的 .db_state.json）
    if db.restore_state() is not None:
        logger.info(f"沿用上次的数据库：{db.current_path()}")
    logger.info(f"数据库：{db.current_path()}｜图像缓存：{imgcache.cache_root()}")
    # 增大线程池容量，避免后台抓取占满后页面操作排队
    try:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 200
    except Exception:  # noqa: BLE001
        pass
    # 启动后台持续验证代理可用性（每 60s 强制探测一次；未配置代理时为空转）
    proxy.start_background_check()
    # 后台线程立即探测一次代理，不阻塞应用启动
    threading.Thread(target=proxy.check_now, daemon=True).start()
    # 启动后台抓取 worker（前端无需保活，抓取在服务端持续进行）
    worker.start()


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


class DbRequest(BaseModel):
    path: str


class DomainCheckRequest(BaseModel):
    domain: str


class DomainFreezeRequest(BaseModel):
    domain: str
    frozen: bool


class DomainReplaceRequest(BaseModel):
    from_domain: str
    to_domain: str


class CaptureRequest(BaseModel):
    url: str
    title: str = ""
    thumbnail: str = ""
    details: list[dict] = []
    image_base64: str = ""
    image_type: str = ""


# ──────────────────────── 条目 ────────────────────────

@app.get("/api/entries")
def api_list_entries(q: str = "", limit: int = 0, offset: int = 0):
    items = entries.list_entries(limit or None, max(0, offset), q)
    return {"entries": items, "total": entries.count_entries()}


@app.get("/api/entries/pending")
def api_pending():
    return {"urls": entries.list_pending_links()}


@app.get("/api/queue/status")
def api_queue_status():
    """后台抓取队列状态（队列剩余 / worker 数 / 未抓取数 / 失败数）。"""
    return {**worker.status(), **entries.queue_stats()}


@app.post("/api/entries/import")
def api_import(req: ImportRequest):
    if not req.urls:
        raise HTTPException(400, "缺少 urls")
    result = entries.import_links(req.urls)
    for url in result.get("to_fetch", []):
        worker.enqueue(url)
    return {"ok": True, **result}


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


# ──────────────────────── 演员 ────────────────────────

@app.get("/api/actresses")
def api_actresses(q: str = "", limit: int = 200, offset: int = 0):
    """演员名单，带各自作品数；ready=False 表示演员数据还没初始化。"""
    return actresses.list_actresses(q, limit, offset)


@app.get("/api/actress/{actress_id}")
def api_actress(actress_id: int):
    """单个演员 + 他/她的全部作品（关联自动推导，只读）。"""
    a = actresses.get_actress(actress_id)
    if a is None:
        raise HTTPException(404, "演员不存在或数据未初始化")
    return a


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
    # 手动重新抓取详情后，给封面图像一次重新抓取的机会（清除失败标记）
    e2 = entries.get_entry(code)
    if e2 and e2["cover"]:
        imgcache.clear_failed(e2["cover"])
    return entries.enrich_entry(code)


@app.post("/api/entries/capture")
def api_capture(req: CaptureRequest):
    """浏览器插件直存：插件已在页面 DOM 里抽好标题/封面/关键信息，直接回填保存。

    - 复用 ingest_fetched 的番号识别 + 字段归类 + 合并（不覆盖已有非空字段）。
    - 可选带 image_base64（插件已抓到的封面字节），直接落 imgcache，省去后端再抓。
    """
    url = (req.url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "缺少页面地址")
    thumbnail = (req.thumbnail or "").strip()
    if not thumbnail.startswith(("http://", "https://")):
        thumbnail = ""
    meta = {"url": url, "title": (req.title or "").strip(),
            "thumbnail": thumbnail, "details": req.details or [], "success": True}
    e = entries.ingest_fetched(meta)
    if e is None:
        return {"ok": True, "unrecognized": True}
    if req.image_base64 and e.get("cover"):
        try:
            data = base64.b64decode(req.image_base64)
            if data:
                imgcache.put(e["cover"], data, (req.image_type or "image/jpeg").strip())
                e = entries.get_entry(e["code"]) or e
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"插件封面字节落缓存失败：{exc}")
    return {"ok": True, "entry": e}


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


# ──────────────────────── 数据库 ────────────────────────

def _db_info(path: Path, listed_directory: str) -> dict:
    """数据库状态（含图像缓存目录与用过的库历史）。"""
    return {
        "path": str(path),
        "directory": str(path.parent),
        "name": path.name,
        "files": db.list_dbs(listed_directory),
        "listed_directory": listed_directory,
        "cache_dir": str(imgcache.cache_root()),
        "history": [
            {
                "path": p,
                "name": Path(p).name,
                "directory": str(Path(p).parent),
                "exists": Path(p).is_file(),
                "current": p == str(path),
            }
            for p in db.history()
        ],
    }


@app.get("/api/db")
def api_db(directory: str = ""):
    """当前数据库状态；`directory` 给定时返回该目录下的 .db 文件列表（不切换）。"""
    cur = db.current_path()
    return _db_info(cur, directory.strip() or str(cur.parent))


@app.post("/api/db")
def api_db_set(req: DbRequest):
    """切换数据库：path 为目录 → 该目录/library.db；为 .db 文件 → 直接使用。

    图像缓存固定在该库所在目录的 `cache/img`（无则新建），切换后立刻按新位置命中。
    """
    if not req.path.strip():
        raise HTTPException(400, "缺少数据库路径")
    path = db.set_db(req.path.strip())
    logger.info(f"切换数据库：{path}｜图像缓存：{imgcache.cache_root()}")
    return _db_info(path, str(path.parent))


# ──────────────────────── 域名管理 ────────────────────────

@app.get("/api/domains")
def api_domains():
    """库中所有域名及其状态（链接数/待抓数/冻结/最近检测）。"""
    return {"domains": entries.list_domains()}


@app.post("/api/domains/check")
def api_domain_check(req: DomainCheckRequest):
    """探测单个域名可达性，并记录最近检测结果。"""
    if not req.domain.strip():
        raise HTTPException(400, "缺少域名")
    result = check_domain(req.domain.strip())
    entries.record_domain_check(result["domain"], result["ok"])
    return result


@app.post("/api/domains/freeze")
def api_domain_freeze(req: DomainFreezeRequest):
    """冻结/解冻某域名：冻结后该域名下所有链接不参与待抓取列表。"""
    if not req.domain.strip():
        raise HTTPException(400, "缺少域名")
    if not entries.set_domain_frozen(req.domain.strip(), req.frozen):
        raise HTTPException(400, "域名无效")
    worker.mark_frozen(req.domain.strip(), req.frozen)
    return {"ok": True, "domain": req.domain.strip().lower(), "frozen": req.frozen}


@app.post("/api/domains/replace")
def api_domain_replace(req: DomainReplaceRequest):
    """批量把某域名下所有链接的域名替换为新域名。"""
    result = entries.replace_domain(req.from_domain, req.to_domain)
    if result["replaced"] == 0 and not result["from_domain"]:
        raise HTTPException(400, "缺少原域名或新域名")
    return {"ok": True, **result, "domains": entries.list_domains()}


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

def _fetch_image_bytes(src: str, proxy_url: str | None, timeout: int = 15,
                       referer: str = "") -> tuple[bytes, str] | None:
    """抓取图片字节（带 Cookie 注入 + 防盗链 Referer）；失败返回 None。

    优先用 curl_cffi 的 TLS 指纹抓取（绕过 Cloudflare 等反爬），失败回退 urllib。
    """
    # 防盗链通常校验 Referer 是否来自站点页面（而非图片自身），缺省用图片同源兜底
    if not referer:
        p = urlparse(src)
        referer = f"{p.scheme}://{p.netloc}/"
    headers = {"Referer": referer}
    cookie = cookie_ctl.get_cookie_header(src)
    if cookie:
        headers["Cookie"] = cookie

    try:
        from curl_cffi import requests as cf
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        resp = cf.get(src, headers=headers, proxies=proxies, timeout=timeout,
                      impersonate="chrome", allow_redirects=True)
        if resp.status_code < 400:
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.content, ctype
        logger.warning(
            f"curl_cffi 抓图 HTTP {resp.status_code}：{src}"
            f"（server={resp.headers.get('server')} cf-ray={resp.headers.get('cf-ray')}），回退 urllib"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"curl_cffi 抓图异常：{src}（{type(exc).__name__}: {exc}），回退 urllib")

    try:
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0", **headers})
        opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url})) if proxy_url else build_opener()
        with opener.open(req, timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type", "application/octet-stream")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"urllib 抓图失败：{src}（{type(exc).__name__}: {exc}）")
        return None


@app.get("/api/img")
def api_img(src: str, referer: str = "", refresh: int = 0):
    """服务端代抓图片（封面跨域/防盗链兜底），走**全局代理**，并**落盘缓存**。

    - `referer`：来源页面地址，用于防盗链校验（缺省回退图片同源）。
    - 命中本地缓存 → 直接返回（避免重复抓取同一张图）。
    - 缓存未命中 → 抓取 → 写缓存 → 返回。
    - 抓图失败 → 写入失败标记；此后同一链接直接 404，不再重复抓取。
    - `refresh=1`：清除失败标记并强制重新抓取（绕过缓存）。
    - 代理端口漂移导致连接失败时，强制重新探测代理再重试一次。
    """
    if not src.startswith(("http://", "https://")):
        raise HTTPException(400, "src 必须是 http(s) 地址")

    # ① 已知失败且未要求强制重试 → 直接 404，不重复抓取
    if imgcache.is_failed(src) and not refresh:
        raise HTTPException(404, "图片抓取失败（该链接此前已失败，暂不重试）")
    if refresh:
        imgcache.clear_failed(src)

    # ② 命中本地缓存 → 直接返回（refresh 强制重抓时绕过）
    if not refresh:
        cached = imgcache.get(src)
        if cached is not None:
            data, ctype = cached
            return Response(content=data, media_type=ctype)

    # ③ 缓存未命中 → 抓取
    proxy_url = proxy.resolve() or None
    got = _fetch_image_bytes(src, proxy_url, referer=referer)
    if got is None and proxy_url:
        # 代理可能失效（端口漂移/连接拒绝），强制重新探测代理再试一次
        proxy_url = proxy.resolve(force=True) or None
        got = _fetch_image_bytes(src, proxy_url, referer=referer)
    if got is None:
        imgcache.mark_failed(src)
        raise HTTPException(404, "图片抓取失败（源站反爬或代理不可用）")

    # ④ 落盘缓存，下次直接命中（同时清除失败标记）
    data, ctype = got
    imgcache.put(src, data, ctype)
    return Response(content=data, media_type=ctype)


# ──────────────────────── 静态托管（放最后） ────────────────────────


class NoCacheStatic(StaticFiles):
    """每次都回源校验（未变则 304）：改完 CSS/JS 刷新即生效，不会拿到旧缓存。"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["cache-control"] = "no-cache"
        return response


app.mount("/", NoCacheStatic(directory=str(STATIC_DIR), html=True), name="static")
