// 动漫管理器前端（无构建 SPA）
const state = { entries: [], total: 0, q: "", tags: [], pending: [] };

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

function coverUrl(e) {
  return e.cover ? `/api/img?src=${encodeURIComponent(e.cover)}` : "";
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
  if (e.cover) {
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
  body.append(code, title, meta);

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

  if (!e.fetched) {
    const badge = document.createElement("span");
    badge.className = "pending-dot";
    badge.title = "待抓取";
    card.appendChild(badge);
  }

  card.append(cover, body);
  card.addEventListener("click", () => openDetail(e.code));
  return card;
}

// ──────────────────────── 详情弹层 ────────────────────────

function fieldRow(label, value) {
  const row = document.createElement("div");
  row.className = "field-row";
  const lab = document.createElement("span");
  lab.className = "field-label";
  lab.textContent = label;
  const val = document.createElement("span");
  val.className = "field-value";
  val.textContent = value || "—";
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
  if (e.cover) {
    const img = document.createElement("img");
    img.src = coverUrl(e);
    img.alt = e.title;
    img.onerror = () => { img.remove(); cover.textContent = e.code; cover.classList.add("cover-fallback"); };
    cover.appendChild(img);
  } else {
    cover.classList.add("cover-fallback");
    cover.textContent = e.code;
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
  props.append(
    fieldRow("发行日期", e.release_date),
    fieldRow("片商", e.maker),
    fieldRow("导演", e.director),
    fieldRow("标籤", e.label),
    fieldRow("系列", e.series),
    fieldRow("时长", e.duration),
    fieldRow("评分", e.rating),
  );
  info.appendChild(props);

  head.append(cover, info);

  const actors = document.createElement("div");
  actors.className = "detail-section";
  actors.innerHTML = "<h3>演员</h3>";
  actors.appendChild(chipList(e.actors.length ? e.actors : ["—"]));

  const genres = document.createElement("div");
  genres.className = "detail-section";
  genres.innerHTML = "<h3>类型</h3>";
  genres.appendChild(chipList(e.genres.length ? e.genres : ["—"]));

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
    td2.textContent = d.value || d.datetime || (d.links && d.links[0] ? d.links[0].href : "");
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
    ["label", "标籤", e.label],
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
  backfill();
}

async function backfill() {
  const { urls } = await api("/api/entries/pending");
  if (!urls.length) return;
  let done = 0;
  const worker = async () => {
    while (urls.length) {
      const url = urls.shift();
      try { await api("/api/fetch", { method: "POST", body: { url } }); } catch (e) {}
      done += 1;
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  toast(`补抓完成 ${done} 条`);
  loadEntries();
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
      backfill();
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

(async function init() {
  try {
    const [tags] = await Promise.all([
      api("/api/tags"),
      loadEntries(),
    ]);
    state.tags = tags.tags;
    backfill();
  } catch (e) {
    stats.textContent = "加载失败：" + e.message;
    empty.hidden = false;
  }
})();
