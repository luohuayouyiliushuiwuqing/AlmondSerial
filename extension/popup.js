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
const captureBtn = $("#capture");
const statusEl = $("#status");
const resultEl = $("#result");

const DEFAULT_BASE = "http://127.0.0.1:8000";

function setStatus(text, kind) {
  statusEl.hidden = !text;
  statusEl.textContent = text || "";
  statusEl.className = "status" + (kind ? " " + kind : "");
}

async function loadBase() {
  let base = DEFAULT_BASE;
  try {
    const got = await chrome.storage.local.get("baseUrl");
    if (got && got.baseUrl) base = got.baseUrl;
  } catch (e) {}
  baseInput.value = base;
}

baseInput.addEventListener("change", () => {
  const v = (baseInput.value || "").trim().replace(/\/+$/, "");
  chrome.storage.local.set({ baseUrl: v || DEFAULT_BASE });
});

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

captureBtn.addEventListener("click", async () => {
  captureBtn.disabled = true;
  setStatus("抓取中…");
  resultEl.hidden = true;
  try {
    const base = (baseInput.value || "").trim().replace(/\/+$/, "") || DEFAULT_BASE;

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
      setStatus("已保存", "ok");
      renderResult(res.entry, data);
    }
  } catch (e) {
    setStatus("失败：" + (e && e.message ? e.message : e), "err");
  } finally {
    captureBtn.disabled = false;
  }
});

loadBase();
