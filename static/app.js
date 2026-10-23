// 动漫管理器前端（无构建 SPA）
const state = { entries: [], total: 0, q: "", tags: [], pending: [], queue: { pending: 0, failed: 0, queue: 0 } };

const $ = (sel) => document.querySelector(sel);
const grid = $("#grid");
const empty = $("#empty");
const stats = $("#stats-text");
const modal = $("#modal");
const modalBody = $("#modal-body");
const toastEl = $("#toast");

// ──────────────────────── 基础 ────────────────────────

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

let toastTimer = null;
function toast(msg) {
  toastEl.textContent = msg;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.hidden = true; }, 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.detail || j.error || msg; } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

// ──────────────────────── 列表 / 渲染 ────────────────────────

async function loadEntries() {
  const q = encodeURIComponent(state.q);
  const data = await api(`/api/entries?q=${q}`);
  state.entries = data.entries;
  state.total = data.total;
  renderGrid();
}

function coverUrl(e, refresh) {
  if (!e.cover) return "";
  const q = `src=${encodeURIComponent(e.cover)}`;
  const ref = e.links && e.links.length ? `&referer=${encodeURIComponent(e.links[0])}` : "";
  const rf = refresh ? "&refresh=1" : "";
  return `/api/img?${q}${ref}${rf}`;
}

function coverStateOf(e) {
  return Number.isInteger(e.cover_state) ? e.cover_state : (e.cover ? 1 : 0);
}

function imgBadge(text, cls) {
  const b = document.createElement("span");
  b.className = "img-badge " + cls;
  b.textContent = text;
  return b;
}

function buildRetryBtn(container, e) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-mini cover-retry";
  b.textContent = "重试封面";
  b.addEventListener("click", () => {
    container.classList.remove("cover-fallback");
    container.textContent = "";
    const img = document.createElement("img");
    img.src = coverUrl(e, true);
    img.alt = e.title || "";
    img.onerror = () => {
      img.remove();
      container.classList.add("cover-fallback");
      container.textContent = e.code;
      container.appendChild(buildRetryBtn(container, e));
    };
    container.appendChild(img);
  });
  return b;
}

function renderGrid() {
  grid.innerHTML = "";
  stats.textContent = `共 ${state.total} 条`;
  empty.hidden = state.entries.length > 0;
  for (const e of state.entries) {
    grid.appendChild(renderCard(e));
  }
}

function renderCard(e) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.code = e.code;

  const cover = document.createElement("div");
  cover.className = "card-cover";
  const coverState = coverStateOf(e);
  if (e.cover && coverState !== 3) {
    const img = document.createElement("img");
    img.src = coverUrl(e);
    img.loading = "lazy";
    img.alt = e.title;
    img.onerror = () => { img.remove(); cover.classList.add("cover-fallback"); cover.textContent = e.code; };
    cover.appendChild(img);
  } else {
    cover.classList.add("cover-fallback");
    cover.textContent = e.code;
  }
  if (coverState === 3) {
    cover.appendChild(imgBadge("图失败", "failed"));
  } else if (coverState === 0 && e.fetched === 1) {
    cover.appendChild(imgBadge("缺图", "missing"));
  }

  // 打开来源链接按钮（hover 显示，新标签打开第一条链接，不触发进详情）
  if (e.links && e.links.length) {
    const open = document.createElement("button");
    open.className = "card-open";
    open.title = "打开来源链接";
    open.textContent = "↗";
    open.addEventListener("click", (ev) => {
      ev.stopPropagation();
      window.open(e.links[0], "_blank", "noopener");
    });
    cover.appendChild(open);
  }

  const body = document.createElement("div");
  body.className = "card-body";
  const code = document.createElement("div");
  code.className = "card-code";
  code.textContent = e.code;
  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = e.title || "（未抓取标题）";
  const meta = document.createElement("div");
  meta.className = "card-meta";
  meta.textContent = e.release_date || "";
  const time = document.createElement("div");
  time.className = "card-time";
  time.title = "录入时间";
  time.textContent = e.created_at ? `录入 ${e.created_at}` : "";
  body.append(code, title, meta, time);

  if (e.genres && e.genres.length) {
    const tags = document.createElement("div");
    tags.className = "card-genres";
    for (const g of e.genres.slice(0, 3)) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = g;
      tags.appendChild(chip);
    }
    body.appendChild(tags);
  }

  if (e.fetched === 0) {
    const badge = document.createElement("span");
    badge.className = "pending-dot";
    badge.title = "待抓取";
    card.appendChild(badge);
  } else if (e.fetched === 2) {
    const badge = document.createElement("span");
    badge.className = "failed-dot";
    badge.title = "抓取失败";
    card.appendChild(badge);
  }

  card.append(cover, body);
  card.addEventListener("click", () => openDetail(e.code));
  return card;
}

// ──────────────────────── 详情弹层 ────────────────────────

function fieldRow(label, value, href) {
  const row = document.createElement("div");
  row.className = "field-row";
  const lab = document.createElement("span");
  lab.className = "field-label";
  lab.textContent = label;
  const val = document.createElement("span");
  val.className = "field-value";
  if (href && value) {
    const a = document.createElement("a");
    a.className = "field-link";
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = value;
    val.appendChild(a);
  } else {
    val.textContent = value || "—";
  }
  row.append(lab, val);
  return row;
}

function chipList(items, cls = "chip") {
  const wrap = document.createElement("div");
  wrap.className = "chip-list";
  for (const it of items) {
    const c = document.createElement("span");
    c.className = cls;
    c.textContent = it;
    wrap.appendChild(c);
  }
  return wrap;
}

function linkChipList(links, cls = "chip") {
  const wrap = document.createElement("div");
  wrap.className = "chip-list";
  for (const l of links) {
    const a = document.createElement("a");
    a.className = cls + " chip-link";
    a.href = l.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = l.text || l.href;
    a.title = l.href;
    wrap.appendChild(a);
  }
  return wrap;
}

function firstLink(e, field) {
  const ls = e.field_links && e.field_links[field];
  return ls && ls.length ? ls[0].href : "";
}

async function openDetail(code) {
  const e = await api(`/api/entry/${encodeURIComponent(code)}`);
  renderDetail(e);
  modal.hidden = false;
}

function renderDetail(e) {
  modalBody.innerHTML = "";

  const head = document.createElement("div");
  head.className = "detail-head";

  const cover = document.createElement("div");
  cover.className = "detail-cover";
  const coverState = coverStateOf(e);
  if (e.cover && coverState !== 3) {
    const img = document.createElement("img");
    img.src = coverUrl(e);
    img.alt = e.title;
    img.onerror = () => { img.remove(); cover.textContent = e.code; cover.classList.add("cover-fallback"); };
    cover.appendChild(img);
  } else {
    cover.classList.add("cover-fallback");
    cover.textContent = e.code;
    if (coverState === 3) cover.appendChild(buildRetryBtn(cover, e));
  }

  const info = document.createElement("div");
  info.className = "detail-info";
  const code = document.createElement("h2");
  code.textContent = e.code;
  const title = document.createElement("div");
  title.className = "detail-title";
  title.textContent = e.title || "（未抓取标题）";
  info.append(code, title);

  const props = document.createElement("div");
  props.className = "detail-props";
  const propFields = [
    ["发行日期", e.release_date, ""],
    ["片商", e.maker, firstLink(e, "maker")],
    ["导演", e.director, firstLink(e, "director")],
    ["厂牌", e.label, firstLink(e, "label")],
    ["系列", e.series, firstLink(e, "series")],
    ["时长", e.duration, ""],
    ["评分", e.rating, ""],
  ];
  for (const [label, value, href] of propFields) {
    if (value) props.appendChild(fieldRow(label, value, href));
  }
  info.appendChild(props);

  head.append(cover, info);

  const actors = document.createElement("div");
  actors.className = "detail-section";
  actors.innerHTML = "<h3>演员</h3>";
  const actorLinks = (e.field_links && e.field_links.actors) || [];
  actors.appendChild(actorLinks.length ? linkChipList(actorLinks) : chipList(e.actors.length ? e.actors : ["—"]));

  const genres = document.createElement("div");
  genres.className = "detail-section";
  genres.innerHTML = "<h3>类型</h3>";
  const genreLinks = (e.field_links && e.field_links.genres) || [];
  genres.appendChild(genreLinks.length ? linkChipList(genreLinks) : chipList(e.genres.length ? e.genres : ["—"]));

  const links = document.createElement("div");
  links.className = "detail-section";
  links.innerHTML = "<h3>来源链接</h3>";
  const linkList = document.createElement("div");
  linkList.className = "link-list";
  if (!e.links.length) {
    const none = document.createElement("p");
    none.className = "hint";
    none.textContent = "（无）";
    linkList.appendChild(none);
  }
  for (const u of e.links) {
    const row = document.createElement("div");
    row.className = "link-row";
    const a = document.createElement("a");
    a.href = u;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = u;
    const copy = document.createElement("button");
    copy.className = "btn btn-mini";
    copy.textContent = "复制";
    copy.title = "复制链接";
    copy.addEventListener("click", () => {
      navigator.clipboard.writeText(u).then(() => toast("已复制链接"));
    });
    row.append(a, copy);
    linkList.appendChild(row);
  }
  links.appendChild(linkList);

  const tags = document.createElement("div");
  tags.className = "detail-section";
  tags.innerHTML = "<h3>标签</h3>";
  const tagWrap = document.createElement("div");
  tagWrap.className = "tag-manage";
  const tagChips = chipList(e.tags.length ? e.tags.map((t) => t.name) : ["（无）"]);
  tagWrap.appendChild(tagChips);
  const tagSelect = document.createElement("select");
  tagSelect.className = "tag-select";
  tagSelect.innerHTML = '<option value="">添加标签…</option>';
  for (const t of state.tags) {
    if (e.tags.some((x) => x.id === t.id)) continue;
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = t.name;
    tagSelect.appendChild(opt);
  }
  tagSelect.addEventListener("change", async () => {
    if (!tagSelect.value) return;
    await api(`/api/entry/${encodeURIComponent(e.code)}/tags`, { method: "POST", body: { tag_id: Number(tagSelect.value) } });
    toast("已添加标签");
    openDetail(e.code);
  });
  tagWrap.appendChild(tagSelect);
  tags.appendChild(tagWrap);

  const raw = document.createElement("div");
  raw.className = "detail-section";
  raw.innerHTML = "<h3>原始详情</h3>";
  const rawTable = document.createElement("table");
  rawTable.className = "raw-table";
  for (const d of e.details || []) {
    const tr = document.createElement("tr");
    const td1 = document.createElement("td");
    td1.textContent = d.label;
    const td2 = document.createElement("td");
    const dlinks = (d.links || []).filter((l) => l && l.href);
    if (dlinks.length) {
      dlinks.forEach((l, i) => {
        const a = document.createElement("a");
        a.className = "raw-link";
        a.href = l.href;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = l.text || l.href;
        td2.appendChild(a);
        if (i < dlinks.length - 1) td2.appendChild(document.createTextNode("、"));
      });
    } else {
      td2.textContent = d.value || d.datetime || "";
    }
    tr.append(td1, td2);
    rawTable.appendChild(tr);
  }
  if (!(e.details || []).length) raw.innerHTML = "<h3>原始详情</h3><p class='hint'>无</p>";
  else raw.appendChild(rawTable);

  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const fetchBtn = document.createElement("button");
  fetchBtn.className = "btn btn-primary";
  fetchBtn.textContent = "重新抓取";
  fetchBtn.addEventListener("click", async () => {
    fetchBtn.disabled = true;
    fetchBtn.textContent = "抓取中…";
    try {
      await api(`/api/entry/${encodeURIComponent(e.code)}/fetch`, { method: "POST" });
      toast("抓取完成");
      openDetail(e.code);
      loadEntries();
    } finally {
      fetchBtn.disabled = false;
      fetchBtn.textContent = "重新抓取";
    }
  });
  const enrichBtn = document.createElement("button");
  enrichBtn.className = "btn";
  enrichBtn.textContent = "代理补全";
  enrichBtn.title = "通过详情代理补全缺失字段";
  enrichBtn.addEventListener("click", async () => {
    enrichBtn.disabled = true;
    enrichBtn.textContent = "补全中…";
    try {
      await api(`/api/entry/${encodeURIComponent(e.code)}/enrich`, { method: "POST" });
      toast("补全完成");
      openDetail(e.code);
      loadEntries();
    } catch (err) {
      toast("补全失败：" + err.message);
    } finally {
      enrichBtn.disabled = false;
      enrichBtn.textContent = "代理补全";
    }
  });
  const editBtn = document.createElement("button");
  editBtn.className = "btn";
  editBtn.textContent = "编辑";
  editBtn.addEventListener("click", () => renderEdit(e));
  const delBtn = document.createElement("button");
  delBtn.className = "btn btn-danger";
  delBtn.textContent = "删除";
  delBtn.addEventListener("click", async () => {
    if (!confirm(`确定删除条目 ${e.code} 吗？`)) return;
    await api(`/api/entry/${encodeURIComponent(e.code)}`, { method: "DELETE" });
    toast("已删除");
    closeModal();
    loadEntries();
  });
  actions.append(fetchBtn, enrichBtn, editBtn, delBtn);

  modalBody.append(head, actors, genres, links, tags, raw, actions);
}

function renderEdit(e) {
  modalBody.innerHTML = "";
  const h = document.createElement("h2");
  h.textContent = `编辑 ${e.code}`;

  const fields = [
    ["title", "标题", e.title],
    ["release_date", "发行日期", e.release_date],
    ["maker", "片商", e.maker],
    ["director", "导演", e.director],
    ["label", "厂牌", e.label],
    ["series", "系列", e.series],
    ["duration", "时长", e.duration],
    ["rating", "评分", e.rating],
    ["actors", "演员（逗号分隔）", e.actors.join(", ")],
    ["genres", "类型（逗号分隔）", e.genres.join(", ")],
  ];
  const form = document.createElement("form");
  form.className = "edit-form";
  const inputs = {};
  for (const [key, label, val] of fields) {
    const lab = document.createElement("label");
    lab.textContent = label;
    const input = document.createElement("input");
    input.value = val;
    inputs[key] = input;
    lab.appendChild(input);
    form.appendChild(lab);
  }

  const save = document.createElement("button");
  save.type = "submit";
  save.className = "btn btn-primary";
  save.textContent = "保存";
  form.appendChild(save);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {};
    for (const key of ["title", "release_date", "maker", "director", "label", "series", "duration", "rating"]) {
      body[key] = inputs[key].value;
    }
    body.actors = inputs.actors.value.split(",").map((s) => s.trim()).filter(Boolean);
    body.genres = inputs.genres.value.split(",").map((s) => s.trim()).filter(Boolean);
    await api(`/api/entry/${encodeURIComponent(e.code)}`, { method: "PATCH", body });
    toast("已保存");
    openDetail(e.code);
  });

  modalBody.append(h, form);
}

function closeModal() {
  modal.hidden = true;
  modalBody.innerHTML = "";
}

// ──────────────────────── 导入 / 补抓 ────────────────────────

async function importLinks(text) {
  const urls = text.split(/[\n,，]+/).map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return;
  const res = await api("/api/entries/import", { method: "POST", body: { urls } });
  const parts = [`新增 ${res.inserted}`, `已存在 ${res.existing}`];
  if (res.unrecognized.length) parts.push(`识别不出 ${res.unrecognized.length}`);
  toast("导入完成：" + parts.join(" · "));
  await loadEntries();
  checkImportedDomains(urls);
}

// ──────────────────────── 导出 ────────────────────────

async function exportTxt() {
  const res = await fetch("/api/export?fmt=txt");
  const text = await res.text();
  const blob = new Blob([text], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "links.txt";
  a.click();
  URL.revokeObjectURL(a.href);
}

// ──────────────────────── 文件导入 + 链接筛选 ────────────────────────

function groupByDomain(links) {
  const map = new Map();
  for (const l of links) {
    const d = l.domain || "（无域名）";
    if (!map.has(d)) map.set(d, []);
    map.get(d).push(l);
  }
  return map;
}

function domainState(items) {
  const sel = items.filter((l) => l.selected).length;
  return sel === 0 ? 0 : sel === items.length ? 2 : 1; // 0 无 / 1 部分 / 2 全
}

function renderImportModal() {
  const links = state.pending;
  const groups = groupByDomain(links);
  const domains = [...groups.keys()].sort((a, b) => groups.get(b).length - groups.get(a).length);
  const domainCheckboxes = {};
  const linkCheckboxes = [];

  modalBody.innerHTML = "";

  const head = document.createElement("div");
  head.className = "import-head";
  const h2 = document.createElement("h2");
  h2.textContent = "链接筛选";
  const total = document.createElement("span");
  total.className = "import-total";
  total.textContent = `共 ${links.length} 个链接`;
  head.append(h2, total);
  modalBody.appendChild(head);

  const bar = document.createElement("div");
  bar.className = "import-bar";
  const selAll = document.createElement("button");
  selAll.className = "btn";
  selAll.textContent = "全选";
  const selNone = document.createElement("button");
  selNone.className = "btn";
  selNone.textContent = "全不选";
  const countEl = document.createElement("span");
  countEl.className = "import-count";
  bar.append(selAll, selNone, countEl);
  modalBody.appendChild(bar);

  // 域名筛选器
  const filter = document.createElement("div");
  filter.className = "domain-filter";
  filter.innerHTML = "<div class='filter-title'>按域名过滤</div>";
  const domainList = document.createElement("div");
  domainList.className = "domain-list";
  for (const d of domains) {
    const item = document.createElement("label");
    item.className = "domain-item";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    const span = document.createElement("span");
    span.textContent = `${d} (${groups.get(d).length})`;
    item.append(cb, span);
    domainList.appendChild(item);
    domainCheckboxes[d] = cb;
  }
  filter.appendChild(domainList);
  modalBody.appendChild(filter);

  // 链接列表
  const list = document.createElement("div");
  list.className = "import-list";
  const indexMap = {};
  links.forEach((l, i) => {
    const d = l.domain || "（无域名）";
    (indexMap[d] ||= []).push(i);
    const row = document.createElement("label");
    row.className = "import-row";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = l.selected;
    const url = document.createElement("span");
    url.className = "import-url";
    url.textContent = l.url;
    const dom = document.createElement("span");
    dom.className = "import-domain";
    dom.textContent = l.domain || "—";
    const code = document.createElement("span");
    code.className = "import-code " + (l.code ? "ok" : "bad");
    code.textContent = l.code || "未识别";
    row.append(cb, url, dom, code);
    list.appendChild(row);
    linkCheckboxes.push(cb);
  });
  modalBody.appendChild(list);

  // 底部操作
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const cancel = document.createElement("button");
  cancel.className = "btn";
  cancel.textContent = "取消";
  const confirmBtn = document.createElement("button");
  confirmBtn.className = "btn btn-primary";
  confirmBtn.textContent = "确认导入";
  actions.append(cancel, confirmBtn);
  modalBody.appendChild(actions);

  const update = () => {
    const selected = links.filter((l) => l.selected).length;
    countEl.textContent = `已选 ${selected} / ${links.length}`;
    for (const d of domains) {
      const st = domainState(groups.get(d));
      domainCheckboxes[d].checked = st === 2;
      domainCheckboxes[d].indeterminate = st === 1;
    }
    confirmBtn.disabled = selected === 0;
  };

  selAll.addEventListener("click", () => {
    links.forEach((l) => (l.selected = true));
    linkCheckboxes.forEach((cb) => (cb.checked = true));
    update();
  });
  selNone.addEventListener("click", () => {
    links.forEach((l) => (l.selected = false));
    linkCheckboxes.forEach((cb) => (cb.checked = false));
    update();
  });

  for (const d of domains) {
    domainCheckboxes[d].addEventListener("change", () => {
      const checked = domainCheckboxes[d].checked;
      for (const i of indexMap[d]) {
        links[i].selected = checked;
        linkCheckboxes[i].checked = checked;
      }
      update();
    });
  }

  linkCheckboxes.forEach((cb, i) => {
    cb.addEventListener("change", () => {
      links[i].selected = cb.checked;
      update();
    });
  });

  cancel.addEventListener("click", closeModal);
  confirmBtn.addEventListener("click", async () => {
    const urls = links.filter((l) => l.selected).map((l) => l.url);
    if (!urls.length) { toast("未选择任何链接"); return; }
    confirmBtn.disabled = true;
    confirmBtn.textContent = "导入中…";
    try {
      const res = await api("/api/entries/import", { method: "POST", body: { urls } });
      const parts = [`新增 ${res.inserted}`, `已存在 ${res.existing}`];
      if (res.unrecognized.length) parts.push(`识别不出 ${res.unrecognized.length}`);
      toast("导入完成：" + parts.join(" · "));
      closeModal();
      await loadEntries();
      checkImportedDomains(urls);
    } finally {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "确认导入";
    }
  });

  update();
}

async function importFile(file) {
  // 读取为原始字节 → base64（保留原始编码，后端按 utf-8/gb18030 兜底解码）
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let b64 = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    b64 += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  b64 = btoa(b64);
  const res = await fetch("/api/import/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, content: b64 }),
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  const data = await res.json();
  if (!data.links.length) { toast("文件中没有找到任何链接"); return; }
  state.pending = data.links.map((l) => ({ ...l, selected: true })); // 默认全选
  renderImportModal();
  modal.hidden = false;
}

// ──────────────────────── 全局代理面板 ────────────────────────

async function openProxyPanel() {
  const st = await api("/api/proxy");
  modalBody.innerHTML = "";

  const h = document.createElement("h2");
  h.textContent = "代理设置";

  const statusBox = document.createElement("div");
  statusBox.className = "proxy-status";
  const rows = [
    ["模式", st.auto ? "自动检测" : "手动指定"],
    ["手动配置", st.configured || "（未指定，自动探测本机端口）"],
    ["当前代理", st.current || "直连（无可用代理）"],
    ["状态", st.available ? "✓ 可用" : "✗ 不可用（已降级直连）"],
  ];
  for (const [k, v] of rows) {
    const row = document.createElement("div");
    row.className = "field-row";
    const lab = document.createElement("span");
    lab.className = "field-label";
    lab.textContent = k;
    const val = document.createElement("span");
    val.className = "field-value";
    val.textContent = v;
    row.append(lab, val);
    statusBox.appendChild(row);
  }

  const form = document.createElement("form");
  form.className = "edit-form";
  const label = document.createElement("label");
  label.textContent = "手动指定代理（留空 = 自动检测本机代理端口，如 http://127.0.0.1:7897）";
  const input = document.createElement("input");
  input.value = st.configured || "";
  input.placeholder = "http://127.0.0.1:7897";
  label.appendChild(input);
  form.appendChild(label);

  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const checkBtn = document.createElement("button");
  checkBtn.type = "button";
  checkBtn.className = "btn";
  checkBtn.textContent = "检查";
  checkBtn.addEventListener("click", async () => {
    checkBtn.disabled = true;
    checkBtn.textContent = "检查中…";
    try {
      const res = await api("/api/proxy/check", { method: "POST" });
      toast(res.available ? `代理可用：${res.current}` : "代理不可用，已降级直连");
      openProxyPanel();
    } finally {
      checkBtn.disabled = false;
      checkBtn.textContent = "检查";
    }
  });
  const saveBtn = document.createElement("button");
  saveBtn.type = "submit";
  saveBtn.className = "btn btn-primary";
  saveBtn.textContent = "保存";
  actions.append(checkBtn, saveBtn);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    saveBtn.disabled = true;
    saveBtn.textContent = "保存中…";
    try {
      const res = await api("/api/proxy", { method: "POST", body: { url: input.value } });
      toast(res.available ? `代理可用：${res.current}` : "代理不可用，已降级直连");
      openProxyPanel();
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "保存";
    }
  });

  modalBody.append(h, statusBox, form, actions);
  modal.hidden = false;
}

// ──────────────────────── 数据库切换面板 ────────────────────────

async function reloadAfterSwitch() {
  state.q = "";
  const search = $("#search-input");
  if (search) search.value = "";
  const [tags] = await Promise.all([api("/api/tags"), loadEntries()]);
  state.tags = tags.tags;
}

async function openDbPanel(directory = "") {
  const q = directory ? `?directory=${encodeURIComponent(directory)}` : "";
  const st = await api(`/api/db${q}`);
  modalBody.innerHTML = "";

  const h = document.createElement("h2");
  h.textContent = "数据库";

  const current = document.createElement("div");
  current.className = "db-current";
  current.textContent = `当前数据库：${st.path}`;

  const form = document.createElement("form");
  form.className = "db-form";
  const input = document.createElement("input");
  input.type = "text";
  input.value = st.listed_directory;
  input.placeholder = "数据库目录（如 D:\\data）或完整 .db 文件路径";
  const browseBtn = document.createElement("button");
  browseBtn.type = "button";
  browseBtn.className = "btn";
  browseBtn.textContent = "浏览目录";
  const switchBtn = document.createElement("button");
  switchBtn.type = "submit";
  switchBtn.className = "btn btn-primary";
  switchBtn.textContent = "切换";
  form.append(input, browseBtn, switchBtn);

  const listWrap = document.createElement("div");
  listWrap.className = "db-list-wrap";
  const listTitle = document.createElement("div");
  listTitle.className = "db-list-title";
  listTitle.textContent = `目录：${st.listed_directory}`;
  const list = document.createElement("div");
  list.className = "db-list";
  if (!st.files.length) {
    const none = document.createElement("p");
    none.className = "hint";
    none.textContent = "（该目录下暂无 .db 文件）";
    list.appendChild(none);
  }
  for (const f of st.files) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "db-item" + (f === st.name ? " active" : "");
    row.textContent = f + (f === st.name ? "（当前）" : "");
    row.addEventListener("click", async () => {
      row.disabled = true;
      try {
        await api("/api/db", { method: "POST", body: { path: `${st.listed_directory}/${f}` } });
        toast("已切换数据库：" + f);
        closeModal();
        await reloadAfterSwitch();
      } catch (err) {
        toast("切换失败：" + err.message);
        row.disabled = false;
      }
    });
    list.appendChild(row);
  }
  listWrap.append(listTitle, list);

  const useDefault = document.createElement("button");
  useDefault.type = "button";
  useDefault.className = "btn";
  useDefault.textContent = "使用此目录的 library.db（无则新建）";
  useDefault.addEventListener("click", async () => {
    useDefault.disabled = true;
    try {
      await api("/api/db", { method: "POST", body: { path: st.listed_directory } });
      toast("已切换到 library.db");
      closeModal();
      await reloadAfterSwitch();
    } catch (err) {
      toast("切换失败：" + err.message);
      useDefault.disabled = false;
    }
  });

  browseBtn.addEventListener("click", () => openDbPanel(input.value.trim()));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    switchBtn.disabled = true;
    switchBtn.textContent = "切换中…";
    try {
      await api("/api/db", { method: "POST", body: { path: input.value.trim() } });
      toast("已切换数据库");
      closeModal();
      await reloadAfterSwitch();
    } catch (err) {
      toast("切换失败：" + err.message);
      switchBtn.disabled = false;
      switchBtn.textContent = "切换";
    }
  });

  modalBody.append(h, current, form, listWrap, useDefault);
  modal.hidden = false;
}

// ──────────────────────── 域名管理 ────────────────────────

function rootDomain(url) {
  try {
    const host = new URL(url).hostname.toLowerCase();
    const parts = host.split(".");
    return parts.length >= 2 ? parts.slice(-2).join(".") : host;
  } catch (e) { return ""; }
}

function mkBtn(text, cls, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = cls;
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

function cleanDomainInput(raw) {
  return (raw || "").trim().replace(/^https?:\/\//, "").split("/")[0].toLowerCase();
}

function openReplaceForm(fromDomain) {
  modalBody.innerHTML = "";
  const h = document.createElement("h2");
  h.textContent = `替换域名：${fromDomain}`;
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = "将把该域名下所有链接（及封面图地址）的域名整体替换为新域名，路径保持不变。";

  const form = document.createElement("form");
  form.className = "edit-form";
  const label = document.createElement("label");
  label.textContent = "新域名（如 xxx.com，可带 https:// 前缀）";
  const input = document.createElement("input");
  input.placeholder = "xxx.com";
  input.autocomplete = "off";
  label.appendChild(input);
  form.appendChild(label);

  const actions = document.createElement("div");
  actions.className = "detail-actions";
  actions.append(mkBtn("取消", "btn", openDomainsPanel));
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "btn btn-primary";
  submit.textContent = "确认替换";
  actions.appendChild(submit);
  form.appendChild(actions);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const toDomain = cleanDomainInput(input.value);
    if (!toDomain) { toast("域名无效"); return; }
    submit.disabled = true;
    submit.textContent = "替换中…";
    try {
      const r = await api("/api/domains/replace", { method: "POST", body: { from_domain: fromDomain, to_domain: toDomain } });
      toast(`已替换 ${r.replaced} 条链接`);
      await loadEntries();
      openDomainsPanel();
    } catch (e) {
      toast("替换失败：" + e.message);
      submit.disabled = false;
      submit.textContent = "确认替换";
    }
  });

  modalBody.append(h, hint, form);
  modal.hidden = false;
}

async function openDomainsPanel() {
  const data = await api("/api/domains");
  const domains = data.domains || [];
  modalBody.innerHTML = "";

  const h = document.createElement("h2");
  h.textContent = "域名管理";
  modalBody.appendChild(h);

  const list = document.createElement("div");
  list.className = "domain-manage";

  if (!domains.length) {
    const none = document.createElement("p");
    none.className = "hint";
    none.textContent = "（库中还没有链接，无域名）";
    list.appendChild(none);
  }

  for (const d of domains) {
    const row = document.createElement("div");
    row.className = "domain-row";

    const info = document.createElement("div");
    info.className = "domain-info";
    const name = document.createElement("div");
    name.className = "domain-name";
    name.textContent = d.domain;
    const meta = document.createElement("div");
    meta.className = "domain-meta";
    const statusText = d.frozen ? "已冻结" : (d.checked_at ? (d.ok ? "最近检测可用" : "最近检测不可用") : "未检测");
    meta.textContent = `链接 ${d.link_count} · 待抓 ${d.pending_count} · ${statusText}`;
    info.append(name, meta);

    const actions = document.createElement("div");
    actions.className = "domain-actions";
    actions.append(
      mkBtn("检测", "btn btn-mini", async () => {
        const r = await api("/api/domains/check", { method: "POST", body: { domain: d.domain } });
        toast(r.ok ? `域名可用（HTTP ${r.status}）` : `域名不可用：${r.error || "无响应"}`);
        openDomainsPanel();
      }),
      mkBtn(d.frozen ? "解冻" : "冻结", "btn btn-mini" + (d.frozen ? "" : " btn-danger"), async () => {
        await api("/api/domains/freeze", { method: "POST", body: { domain: d.domain, frozen: !d.frozen } });
        toast(d.frozen ? "已解冻" : "已冻结");
        openDomainsPanel();
      }),
      mkBtn("替换域名", "btn btn-mini", () => openReplaceForm(d.domain)),
    );

    row.append(info, actions);
    list.appendChild(row);
  }

  modalBody.appendChild(list);
  modal.hidden = false;
}

function renderDomainAlert(bad) {
  modalBody.innerHTML = "";
  const h = document.createElement("h2");
  h.textContent = "检测到不可用的域名";
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = "以下域名连接失败（可能已失效或被墙）。可批量替换为新域名，或冻结以暂停抓取。";

  const list = document.createElement("div");
  list.className = "domain-manage";
  for (const d of bad) {
    const row = document.createElement("div");
    row.className = "domain-row";
    const info = document.createElement("div");
    info.className = "domain-info";
    const name = document.createElement("div");
    name.className = "domain-name";
    name.textContent = d.domain;
    const meta = document.createElement("div");
    meta.className = "domain-meta";
    meta.textContent = d.error || "无响应";
    info.append(name, meta);
    const actions = document.createElement("div");
    actions.className = "domain-actions";
    actions.append(
      mkBtn("替换域名", "btn btn-mini", () => openReplaceForm(d.domain)),
      mkBtn("冻结", "btn btn-mini btn-danger", async () => {
        await api("/api/domains/freeze", { method: "POST", body: { domain: d.domain, frozen: true } });
        toast("已冻结 " + d.domain);
        closeModal();
      }),
    );
    row.append(info, actions);
    list.appendChild(row);
  }

  modalBody.append(h, hint, list, mkBtn("关闭", "btn", closeModal));
  modal.hidden = false;
}

async function checkImportedDomains(urls) {
  const domains = [...new Set(urls.map(rootDomain).filter(Boolean))];
  if (!domains.length) return;
  const results = await Promise.all(domains.map((d) =>
    api("/api/domains/check", { method: "POST", body: { domain: d } })
      .catch(() => ({ domain: d, ok: false, error: "检测失败" }))
  ));
  const bad = results.filter((r) => !r.ok);
  if (bad.length) renderDomainAlert(bad);
}

// ──────────────────────── 事件绑定 ────────────────────────

$("#import-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const input = $("#import-input");
  importLinks(input.value);
  input.value = "";
});

let searchTimer = null;
$("#search-input").addEventListener("input", (ev) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.q = ev.target.value.trim();
    loadEntries();
  }, 250);
});

$("#export-btn").addEventListener("click", exportTxt);
$("#db-btn").addEventListener("click", () => openDbPanel());
$("#domains-btn").addEventListener("click", openDomainsPanel);
$("#proxy-btn").addEventListener("click", openProxyPanel);
$("#file-import-btn").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  try {
    await importFile(file);
  } catch (e) {
    toast("解析失败：" + e.message);
  }
});
modal.addEventListener("click", (ev) => {
  if (ev.target.dataset.close !== undefined) closeModal();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") closeModal();
});

// ──────────────────────── 启动 ────────────────────────

async function updateQueueStatus() {
  try {
    const st = await api("/api/queue/status");
    state.queue = st;
    const el = $("#queue-text");
    if (!el) return;
    const parts = [];
    if (st.queue > 0) parts.push(`抓取中 ${st.queue} 条`);
    if (st.pending > 0) parts.push(`待抓取 ${st.pending}`);
    if (st.failed > 0) parts.push(`失败 ${st.failed}`);
    el.textContent = parts.length ? parts.join(" · ") : "";
  } catch (e) {}
}

(async function init() {
  try {
    const [tags] = await Promise.all([
      api("/api/tags"),
      loadEntries(),
    ]);
    state.tags = tags.tags;
    updateQueueStatus();
    // 后台抓取由服务端 worker 进行，前端定时刷新即可看到进度
    setInterval(() => {
      loadEntries().catch(() => {});
      updateQueueStatus();
    }, 3000);
  } catch (e) {
    stats.textContent = "加载失败：" + e.message;
    empty.hidden = false;
  }
})();
