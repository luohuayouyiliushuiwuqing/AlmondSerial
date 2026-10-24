// 在页面上下文里抽取标题 / 封面图 / 关键信息（结构与后端 fields.extract_fields 对齐）
function extractFn() {
  const collapse = (s) => (s || "").replace(/\s+/g, " ").trim();
  const abs = (u, base) => { try { return new URL(u, base).href; } catch (e) { return ""; } };

  const q = (sel) => {
    const el = document.querySelector(sel);
    return el ? (el.getAttribute("content") || el.getAttribute("href") || "") : "";
  };

  let thumbnail = q('meta[property="og:image"]') || q('meta[property="og:image:url"]')
    || q('meta[property="og:image:secure_url"]') || q('meta[name="twitter:image"]')
    || q('meta[name="twitter:image:src"]') || q('link[rel="image_src"]');
  thumbnail = thumbnail ? abs(thumbnail, location.href) : "";

  let title = q('meta[property="og:title"]') || q('meta[name="twitter:title"]');
  if (!title) title = (document.title || "").trim();

  const details = [];
  const container = document.querySelector('.space-y-2, [class*="space-y-2"]');
  const children = container ? Array.from(container.children) : [];
  for (const el of children.slice(0, 30)) {
    const first = el.children && el.children[0];
    if (!first || first.tagName.toLowerCase() !== "span") continue;
    const labelFull = collapse(first.textContent);
    const label = labelFull.replace(/[：:]\s*$/, "").trim();
    const full = collapse(el.textContent);
    let value = full.startsWith(labelFull) ? full.slice(labelFull.length) : full.replace(labelFull, "");
    value = value.replace(/^[,，]\s*/, "").trim();
    const timeEl = el.querySelector("time");
    const datetime = timeEl ? (timeEl.getAttribute("datetime") || "") : "";
    const links = Array.from(el.querySelectorAll("a")).map((a) => ({ text: collapse(a.textContent), href: a.href || "" }));
    for (const l of links) if (l.text) value = value.replace(l.text, "");
    value = collapse(value).replace(/^[,，、;； ]+/, "");
    if (!label || (!value && !links.length && !datetime)) continue;
    details.push({ label: label, value: value, links: links, datetime: datetime });
  }

  return { url: location.href, title: title, thumbnail: thumbnail, details: details };
}

const $ = (sel) => document.querySelector(sel);
const baseInput = $("#base");
const closeAfterEl = $("#closeAfter");
const captureBtn = $("#capture");
const captureAllBtn = $("#captureAll");
const statusEl = $("#status");
const resultEl = $("#result");
const listEl = $("#list");
const skippedEl = $("#skipped");
const skippedSummaryEl = $("#skippedSummary");
const skippedListEl = $("#skippedList");

const DEFAULT_BASE = "http://127.0.0.1:8000";
const CONCURRENCY = 4;          // 同时注入的标签页数，避免一次开几十个页面把浏览器拖住
const MAX_BATCH_BYTES = 6 << 20; // 单次提交上限；超了自动分批

function setStatus(text, kind) {
  statusEl.hidden = !text;
  statusEl.textContent = text || "";
  statusEl.className = "status" + (kind ? " " + kind : "");
}

// 关页开关默认关：只有用户勾上，抓取成功后才会关掉抓到的标签页
function closeAfterCapture() {
  return !!(closeAfterEl && closeAfterEl.checked);
}

async function loadPrefs() {
  let base = DEFAULT_BASE;
  let closeAfter = false;
  try {
    const got = await chrome.storage.local.get(["baseUrl", "closeAfter"]);
    if (got && got.baseUrl) base = got.baseUrl;
    if (got && got.closeAfter) closeAfter = true;
  } catch (e) {}
  baseInput.value = base;
  closeAfterEl.checked = closeAfter;
}

baseInput.addEventListener("change", () => {
  const v = (baseInput.value || "").trim().replace(/\/+$/, "");
  chrome.storage.local.set({ baseUrl: v || DEFAULT_BASE });
});

closeAfterEl.addEventListener("change", () => {
  chrome.storage.local.set({ closeAfter: closeAfterEl.checked });
});

function currentBase() {
  return (baseInput.value || "").trim().replace(/\/+$/, "") || DEFAULT_BASE;
}

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

async function fetchImage(src, referer) {
  try {
    const resp = await fetch(src, {
      headers: { Referer: referer || src },
      credentials: "omit",
      cache: "no-store",
    });
    if (!resp.ok) return null;
    const ct = (resp.headers.get("content-type") || "").toLowerCase();
    if (!ct.includes("image/")) return null;
    const blob = await resp.blob();
    if (blob.size > 8 * 1024 * 1024) return null; // 过大不传字节，交给后端 /api/img 兜底
    return { base64: toBase64(await blob.arrayBuffer()), type: ct.split(";")[0].trim() || "image/jpeg" };
  } catch (e) { return null; }
}

function renderResult(entry, data) {
  resultEl.hidden = false;
  $("#code").textContent = (entry && entry.code) || "";
  $("#title").textContent = (entry && entry.title) || (data && data.title) || "";
  const thumb = $("#thumb");
  if (data && data.thumbnail) thumb.src = data.thumbnail;
  else thumb.removeAttribute("src");
}

// ──────────────────────── 标签页过滤 ────────────────────────

// 本机 / 内网地址一律不抓：回环（127.0.0.0/8、::1）、私有网段、链路本地、mDNS 本机名
function isLocalHost(host) {
  const h = (host || "").toLowerCase().replace(/^\[|\]$/g, "");
  if (!h) return true;
  if (h === "localhost" || h.endsWith(".localhost")) return true;
  if (h === "::1" || h === "0.0.0.0" || h === "::") return true;
  if (/^127\./.test(h)) return true;
  if (/^10\./.test(h)) return true;
  if (/^192\.168\./.test(h)) return true;
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(h)) return true;
  if (/^169\.254\./.test(h)) return true;
  if (/\.local$/.test(h)) return true;
  return false;
}

// 返回 null 表示可抓；否则返回跳过原因
function skipReason(url, backendOrigin) {
  if (!url) return "无地址";
  let u;
  try { u = new URL(url); } catch (e) { return "地址无效"; }
  const scheme = u.protocol.toLowerCase();
  if (scheme !== "http:" && scheme !== "https:") {
    // chrome://history、edge://settings、about:、chrome-extension://、devtools://、file: 等
    return "系统页面（" + scheme.replace(":", "") + "）";
  }
  if (isLocalHost(u.hostname)) return "本机 / 内网端口";
  if (backendOrigin && u.origin === backendOrigin) return "后端自身地址";
  return null;
}

async function collectTabs(base) {
  let backendOrigin = "";
  try { backendOrigin = new URL(base).origin; } catch (e) {}

  const tabs = await chrome.tabs.query({});
  const seen = new Set();
  const usable = [];
  const skipped = [];
  for (const tab of tabs) {
    const url = tab.url || tab.pendingUrl || "";
    const key = url.split("#")[0];
    const reason = skipReason(url, backendOrigin);
    if (reason) {
      skipped.push({ title: tab.title || "", url: url, reason: reason });
      continue;
    }
    if (seen.has(key)) {
      skipped.push({ title: tab.title || "", url: url, reason: "重复标签页" });
      continue;
    }
    seen.add(key);
    usable.push({ id: tab.id, title: tab.title || "", url: url });
  }
  return { usable: usable, skipped: skipped };
}

async function extractFromTabs(tabs, onProgress) {
  const captured = new Array(tabs.length).fill(null);
  const failed = [];
  let next = 0;
  let done = 0;

  const run = async () => {
    while (next < tabs.length) {
      const idx = next++;
      const tab = tabs[idx];
      try {
        const [inj] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractFn });
        const r = inj && inj.result;
        if (!r || !r.url) throw new Error("页面信息读取失败");
        captured[idx] = { page: r, tabId: tab.id };
      } catch (e) {
        let msg = (e && e.message ? e.message : String(e)).split("\n")[0];
        if (msg.length > 60) msg = msg.slice(0, 60) + "…";
        failed.push({ title: tab.title || "", url: tab.url || "", reason: "无法读取（" + msg + "）" });
      }
      done += 1;
      if (onProgress) onProgress(done, tabs.length, tab.title || tab.url || "");
    }
  };

  const workers = [];
  for (let i = 0; i < Math.min(CONCURRENCY, tabs.length); i += 1) workers.push(run());
  await Promise.all(workers);
  return { captured: captured.filter(Boolean), failed: failed };
}

// 关闭已经抓取成功的标签页；个别标签已被手动关掉时不让整批失败
async function closeTabs(tabIds) {
  const ids = (tabIds || []).filter((id) => typeof id === "number");
  if (!ids.length) return 0;
  try {
    await chrome.tabs.remove(ids);
    return ids.length;
  } catch (e) {
    let closed = 0;
    for (const id of ids) {
      try {
        await chrome.tabs.remove(id);
        closed += 1;
      } catch (err) {}
    }
    return closed;
  }
}

function chunkPages(pages) {
  const groups = [];
  let cur = [];
  let size = 0;
  for (const p of pages) {
    const n = JSON.stringify(p).length;
    if (cur.length && size + n > MAX_BATCH_BYTES) {
      groups.push(cur);
      cur = [];
      size = 0;
    }
    cur.push(p);
    size += n;
  }
  if (cur.length) groups.push(cur);
  return groups;
}

async function postSnapshots(base, pages) {
  const out = { saved: 0, entries: 0, unrecognized: 0, items: [] };
  for (const group of chunkPages(pages)) {
    const resp = await fetch(`${base}/api/snapshots`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pages: group }),
    });
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try { msg = (await resp.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    const res = await resp.json();
    out.saved += res.saved || 0;
    out.entries += res.entries || 0;
    out.unrecognized += res.unrecognized || 0;
    out.items = out.items.concat(res.items || []);
  }
  return out;
}

// ──────────────────────── 结果渲染 ────────────────────────

function hostOf(url) {
  try { return new URL(url).host; } catch (e) { return url; }
}

function renderBatchList(items) {
  listEl.innerHTML = "";
  if (!items.length) {
    listEl.hidden = true;
    return;
  }
  listEl.hidden = false;
  for (const item of items) {
    const li = document.createElement("li");

    const badge = document.createElement("span");
    badge.className = "badge" + (item.code ? "" : " none");
    badge.textContent = item.code || "未识别";

    const txt = document.createElement("div");
    txt.className = "txt";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = item.title || hostOf(item.url);
    const host = document.createElement("div");
    host.className = "host";
    host.textContent = hostOf(item.url);
    txt.appendChild(name);
    txt.appendChild(host);

    li.title = item.url;
    li.appendChild(badge);
    li.appendChild(txt);
    listEl.appendChild(li);
  }
}

function renderSkipped(items) {
  if (!items.length) {
    skippedEl.hidden = true;
    skippedListEl.innerHTML = "";
    return;
  }
  const byReason = {};
  for (const it of items) byReason[it.reason] = (byReason[it.reason] || 0) + 1;
  const parts = Object.keys(byReason).map((k) => `${k} ${byReason[k]}`);
  skippedSummaryEl.textContent = `已跳过 ${items.length} 个页面（${parts.join("、")}）`;
  skippedListEl.innerHTML = "";
  for (const it of items) {
    const li = document.createElement("li");
    li.textContent = `${it.title || it.url || "(无标题)"} — ${it.reason}`;
    li.title = it.url || "";
    skippedListEl.appendChild(li);
  }
  skippedEl.hidden = false;
  skippedEl.open = false;
}

function setBusy(busy) {
  captureBtn.disabled = busy;
  captureAllBtn.disabled = busy;
}

// ──────────────────────── 抓取当前页 ────────────────────────

captureBtn.addEventListener("click", async () => {
  setBusy(true);
  setStatus("抓取中…");
  resultEl.hidden = true;
  listEl.hidden = true;
  skippedEl.hidden = true;
  try {
    const base = currentBase();

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) throw new Error("找不到当前标签页");

    const [inj] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractFn });
    const data = inj && inj.result;
    if (!data || !data.url) throw new Error("页面信息读取失败");

    // 尽量把封面图字节也一并抓下来（防盗链 Referer = 页面地址）
    let image = null;
    if (data.thumbnail) image = await fetchImage(data.thumbnail, data.url);

    const body = {
      url: data.url,
      title: data.title || "",
      thumbnail: data.thumbnail || "",
      details: data.details || [],
      image_base64: image ? image.base64 : "",
      image_type: image ? image.type : "",
    };

    const resp = await fetch(`${base}/api/entries/capture`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try { msg = (await resp.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    const res = await resp.json();
    if (res.unrecognized) {
      setStatus("未识别出番号，未保存", "err");
    } else {
      renderResult(res.entry, data);
      setStatus("已保存", "ok");
      if (closeAfterCapture()) {
        const closed = await closeTabs([tab.id]);
        setStatus(closed ? "已保存，页面已关闭" : "已保存（页面关闭失败）",
                  closed ? "ok" : "err");
      }
    }
  } catch (e) {
    setStatus("失败：" + (e && e.message ? e.message : e), "err");
  } finally {
    setBusy(false);
  }
});

// ──────────────────────── 抓取全部标签页 ────────────────────────

captureAllBtn.addEventListener("click", async () => {
  setBusy(true);
  resultEl.hidden = true;
  listEl.hidden = true;
  skippedEl.hidden = true;
  setStatus("正在清点标签页…");

  try {
    const base = currentBase();
    const { usable, skipped } = await collectTabs(base);

    if (!usable.length) {
      renderSkipped(skipped);
      setStatus("没有可抓取的页面（全部是系统页或本机地址）", "err");
      return;
    }

    const { captured, failed } = await extractFromTabs(usable, (done, total) => {
      setStatus(`抓取中 ${done}/${total}…`);
    });

    if (!captured.length) {
      renderSkipped(skipped.concat(failed));
      setStatus("所有页面都读取失败", "err");
      return;
    }

    setStatus(`正在保存 ${captured.length} 页…`);
    const payload = captured.map((c) => ({
      url: c.page.url,
      title: c.page.title || "",
      thumbnail: c.page.thumbnail || "",
      details: c.page.details || [],
    }));
    const res = await postSnapshots(base, payload);

    renderBatchList(res.items);
    renderSkipped(skipped.concat(failed));
    const summary = `已抓取 ${res.saved} 页：保存番号 ${res.entries} 个，未识别 ${res.unrecognized} 页已留档快照`;

    if (!closeAfterCapture()) {
      setStatus(summary, "ok");
      return;
    }
    setStatus("正在关闭已抓取的标签页…");
    const closed = await closeTabs(captured.map((c) => c.tabId));
    setStatus(`${summary}；已关闭 ${closed} 个标签页`, closed ? "ok" : "err");
  } catch (e) {
    setStatus("失败：" + (e && e.message ? e.message : e), "err");
  } finally {
    setBusy(false);
  }
});

loadPrefs();
