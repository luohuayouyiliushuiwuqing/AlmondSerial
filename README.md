# AlmondSerial — 动漫管理器（番号库）

以**番号（code）为唯一关键字**的动漫/影片元数据管理器。粘贴链接即可导入：系统从链接自动
提取番号，抓取页面元数据（标题/封面）与「标签: 值」详情字段，归类成结构化属性（发行日期 /
演员 / 类型 / 片商 / 系列 / 时长 / 评分）后，以卡片网格展示。

## 技术栈

- **后端**：FastAPI + uvicorn + SQLite（标准库 `sqlite3`）+ scrapling（TLS 指纹抓取）+ loguru。
- **前端**：无构建原生 SPA（`static/index.html` + `app.js` + `styles.css`），由后端直接托管。

## 运行

```bash
cd AlmondSerial
# 使用已装好依赖的 conda 环境（xingren）；run.py 会自动处理端口占用：
#   被本程序占用 → 杀掉旧进程同端口重启；被其他程序占用 → 自动跳端口（+1 递增）
/home/igs/miniconda3/envs/xingren/bin/python run.py
# 可选参数：--host 0.0.0.0 --port 8000
```

浏览器打开 <http://127.0.0.1:8000/>。数据库自动生成在 `data/library.db`。

## 反爬站点 Cookie 注入

Cloudflare 等反爬站（如 fourhoi.com）对自动化抓取返回 403。可在真实浏览器里通过验证后，
把 Cookie 通过环境变量 `SITE_COOKIES` 注入（JSON：域名 → Cookie 串），抓取/抓图时按域名自动带上：

```bash
SITE_COOKIES='{"fourhoi.com": "cf_clearance=xxx; foo=bar"}' \
  /home/igs/miniconda3/envs/xingren/bin/python run.py
```

按域名匹配（含子域名：`a.fourhoi.com` 命中 `fourhoi.com`）。抓取采用**三级降级**：
静态 TLS 指纹（`Fetcher`）→ JS 渲染（`DynamicFetcher`）→ 反 Cloudflare（`StealthyFetcher`，
需 chromium 内核，用 `patchright install chromium` 安装）。

## 目录结构

```
AlmondSerial/
├── app/
│   ├── main.py         # FastAPI 应用 + REST 路由 + 静态托管 + 图片代理
│   ├── db.py           # SQLite 连接 + schema
│   ├── codes.py        # 番号提取与规范化
│   ├── fields.py       # 「标签: 值」行解析
│   ├── attributes.py   # 详情行 → 类型化字段归类
│   ├── entries.py      # 条目 CRUD + 多链接归并 + 标签
│   └── fetcher.py      # scrapling 抓取 + 元数据/详情提取
├── static/             # 前端 SPA（无构建）
├── tests/              # 单元测试
└── requirements.txt
```

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/entries?q=&limit=&offset=` | 条目列表（`q` 搜索 code/标题/演员/片商等） |
| GET | `/api/entries/pending` | 未抓取条目的链接（自动补抓轮询源） |
| POST | `/api/entries/import` | `{urls}` 链接导入 → `{inserted, existing, unrecognized}` |
| GET | `/api/entry/{code}` | 单条目（含 links/tags/属性） |
| PATCH | `/api/entry/{code}` | 编辑标题/属性 |
| DELETE | `/api/entry/{code}` | 删除条目 |
| POST | `/api/fetch` | `{url}` 抓取并回填番号条目 |
| POST | `/api/entry/{code}/fetch` | 抓取该番号所有链接 |
| GET/POST/DELETE | `/api/tags` | 标签 CRUD |
| POST/DELETE | `/api/entry/{code}/tags` | 条目标签关联 |
| GET | `/api/export?fmt=txt\|json` | 导出 |
| GET | `/api/img?src=` | 服务端图片代理（封面跨域/防盗链兜底） |

## 测试

```bash
cd AlmondSerial
/home/igs/miniconda3/envs/xingren/bin/python -m unittest discover -s tests
```
