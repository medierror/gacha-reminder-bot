"use strict";
// Gacha Tracker UI. All data comes from Python (window.pywebview.api). Every value put into HTML is escaped.

let DATA = null;
let VIEW = "home";
const ORDER = ["genshin", "hsr", "zzz", "wuwa", "hi3"];
const KIND = { event: "Event", challenge: "Endgame", banner: "Banner" };
const $ = (sel) => document.querySelector(sel);
const api = () => window.pywebview.api;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const url = (u) => (typeof u === "string" && u.startsWith("https://")) ? esc(u.replace(/["'()\\\s<>]/g, encodeURIComponent)) : "";
const nowSec = () => Date.now() / 1000;
const G = (g) => DATA.games[g];

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.h);
  toast.h = setTimeout(() => t.classList.remove("show"), 2400);
}

function fmtLeft(sec) {
  if (sec <= 0) return "ended";
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60), s = Math.floor(sec % 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString(undefined,
  { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function tickTimers() {
  const now = nowSec();
  document.querySelectorAll(".timer[data-end]").forEach((el) => {
    const start = Number(el.dataset.start || 0), end = Number(el.dataset.end);
    if (start > now) {
      el.textContent = "starts in " + fmtLeft(start - now);
      el.className = "timer future";
      return;
    }
    const left = end - now;
    el.textContent = (el.dataset.prefix || "") + fmtLeft(left);
    el.className = "timer" + (left < 86400 ? " hot" : left < 3 * 86400 ? " soon" : "");
  });
}

function curChip(game, amount) {
  if (!amount) return "";
  const icon = G(game).currencyIcon;
  return `<span class="cur">${icon ? `<img src="${url(icon)}" alt="">` : esc(G(game).emoji)} ${amount.toLocaleString()} <span class="muted" style="font-weight:400">${esc(G(game).currency)}</span></span>`;
}

function card(i, showGame) {
  const g = G(i.game);
  let thumb;
  if (i.image && i.image_is_icon) {
    thumb = `<div class="thumb fallback mode"><img src="${url(i.image)}" alt="">`;
  } else if (i.image && (i.kind !== "banner" || i.official_image)) {
    thumb = `<div class="thumb" style="background-image:url('${url(i.image)}')">`;
  } else if (i.image) {
    thumb = `<div class="thumb portrait"><img src="${url(i.image)}" alt="">`;
  } else {
    const ic = i.icon || g.currencyIcon || g.icon;
    thumb = `<div class="thumb fallback">${ic ? `<img src="${url(ic)}" alt="">` : ""}`;
  }
  const gameTag = showGame ? `<span class="tag game">${g.icon ? `<img src="${url(g.icon)}" alt="">` : ""}${esc(g.name)}</span>` : "";
  return `<div class="card" style="--c:${esc(g.color)}">
    ${thumb}<span class="tag">${esc(KIND[i.kind] || i.kind)}</span>${gameTag}</div>
    <div class="body">
      <h4>${esc(i.name)}</h4>
      <div class="row">${curChip(i.game, i.currency) || "<span></span>"}
        <span class="timer" data-end="${Number(i.end)}" data-start="${Number(i.start) || 0}"></span></div>
      <div class="date">Ends ${esc(fmtDate(i.end))}</div>
    </div></div>`;
}

function grid(items, showGame, empty) {
  if (!items.length) return `<div class="empty">${esc(empty)}</div>`;
  return `<div class="grid">${items.map((i) => card(i, showGame)).join("")}</div>`;
}

function codeRow(game, c) {
  const g = G(game);
  const redeem = c.url ? `<button class="btn primary" data-open="${url(c.url)}">Redeem</button>`
                       : `<span class="pill">Redeem in-game</span>`;
  return `<div class="code" style="--c:${esc(g.color)}">
    <div><div class="mono">${esc(c.code)}</div>${c.rewards ? `<div class="rew">${esc(c.rewards)}</div>` : ""}</div>
    <div class="btns"><button class="btn" data-copy="${esc(c.code)}">Copy</button>${redeem}</div></div>`;
}

function meter(game) {
  const acc = DATA.account;
  if (!acc || !acc.notes) return "";
  const n = acc.notes[game], g = G(game);
  if (!n) return "";
  const head = `<div class="top">${g.icon ? `<img src="${url(g.icon)}" alt="">` : ""}${esc(g.name)}`;
  if (n.error) {
    return `<div class="meter" style="--c:${esc(g.color)}">${head}</div>
      <div class="line">Turn on <b>Real-Time Notes</b> in HoYoLAB → Battle Chronicle settings.</div></div>`;
  }
  const pct = Math.min(100, Math.round(n.stamina / n.max * 100)), full = n.stamina >= n.max;
  const ci = acc.checkin ? acc.checkin[game] : null;
  const checkin = ci === true ? `<span class="ok">✓ Checked in today</span>`
    : ci === false ? `<a href="#" data-open="${url(g.checkin)}" class="bad">Not checked in yet</a>` : "";
  return `<div class="meter" style="--c:${esc(g.color)}">${head}<span class="val">${n.stamina}/${n.max}</span></div>
    <div class="bar${full ? " full" : ""}"><i style="width:${pct}%"></i></div>
    <div class="line"><span>${esc(acc.staminaName[game])}</span>
      ${full ? `<span class="bad">Full!</span>` : `<span>full in <span class="timer" data-end="${Number(n.full_at)}"></span></span>`}</div>
    <div class="line"><span class="${n.daily_done ? "ok" : "bad"}">${n.daily_done ? "✓" : "!"} ${esc(n.daily_text)}</span>${checkin}</div>
  </div>`;
}

function energySection(games) {
  const acc = DATA.account;
  if (acc && acc.expired) return `<div class="empty">Your HoYoLAB login expired. <a href="#" data-view="settings">Connect again</a></div>`;
  if (!acc || !acc.notes) return "";
  const html = games.map(meter).join("");
  return html ? `<h2>🔋 Your energy & dailies</h2><div class="energy">${html}</div>` : "";
}

const active = (g) => DATA.items.filter((i) => i.game === g);
const totalCur = (items) => items.filter((i) => i.kind !== "banner").reduce((a, i) => a + (i.currency || 0), 0);

// ---------------------------------------------------------------- views
function viewHome() {
  const now = nowSec(), hour = new Date().getHours();
  const hello = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const soon = DATA.items.filter((i) => i.start <= now && i.end - now < 3 * 86400).sort((a, b) => a.end - b.end);
  const tiles = ORDER.map((k) => {
    const g = G(k), items = active(k), cur = totalCur(items);
    return `<button class="tile" data-view="${k}" style="--c:${esc(g.color)};background-image:url('${url(g.art)}')">
      <b>${g.icon ? `<img src="${url(g.icon)}" alt="">` : ""}${esc(g.name)}</b>
      <span>${cur ? `${cur.toLocaleString()} ${esc(g.currency)} to collect`
        : items.length ? `${items.length} active` : `${(DATA.codes[k] || []).length} redeem codes`}</span></button>`;
  }).join("");
  const codes = ORDER.flatMap((k) => (DATA.codes[k] || []).slice(0, 2).map((c) => codeRow(k, c))).slice(0, 8).join("");
  return `<h1>${hello} ✨</h1><div class="muted">Here's everything happening in your games.</div>
    ${energySection(ORDER)}
    <h2>🎮 Your games</h2><div class="tiles">${tiles}</div>
    <h2>⏰ Ending in the next 3 days <span class="sub">${soon.length} things</span></h2>${grid(soon, true, "Nothing ends in the next 3 days 🎉")}
    <h2>🎁 Redeem codes</h2><div class="codes">${codes || `<div class="empty">No active codes right now.</div>`}</div>`;
}

function viewGame(k) {
  const g = G(k), items = active(k), now = nowSec();
  const by = (kind) => items.filter((i) => i.kind === kind).sort((a, b) => (a.start > now) - (b.start > now) || a.end - b.end);
  const cur = totalCur(items);
  const codes = (DATA.codes[k] || []).map((c) => codeRow(k, c)).join("");
  const manual = k === "hi3" ? `<div class="empty">Honkai 3rd has no public event calendar, so only its redeem codes show here.</div>` : "";
  return `<div class="hero" style="--c:${esc(g.color)};background-image:url('${url(g.art)}')">
      <div class="title">${g.icon ? `<img src="${url(g.icon)}" alt="">` : ""}<div><h1>${esc(g.name)}</h1>
      <div class="muted">${esc(g.daily)}</div></div></div>
      <div class="chips">${cur ? `<span class="chip big" style="--c:${esc(g.color)}">${g.currencyIcon ? `<img src="${url(g.currencyIcon)}" alt="">` : ""}${cur.toLocaleString()} ${esc(g.currency)} up for grabs</span>` : ""}
      <span class="chip">🎉 ${by("event").length} events</span><span class="chip">🎲 ${by("banner").length} banners</span>
      ${g.checkin ? `<button class="chip" data-open="${url(g.checkin)}">🗓️ Daily check-in</button>` : ""}</div></div>
    ${energySection([k])}
    ${manual}
    ${by("event").length || k !== "hi3" ? `<h2>🎉 Events</h2>${grid(by("event"), false, "No events right now.")}` : ""}
    ${by("banner").length ? `<h2>🎲 Banners</h2>${grid(by("banner"), false, "")}` : ""}
    ${by("challenge").length ? `<h2>⚔️ Endgame</h2>${grid(by("challenge"), false, "")}` : ""}
    <h2>🎁 Redeem codes</h2><div class="codes">${codes || `<div class="empty">No active codes right now.</div>`}</div>`;
}

function viewSettings() {
  const s = DATA.settings, acc = DATA.account;
  const sw = (key) => `<label class="switch"><input type="checkbox" data-set="${key}" ${s[key] ? "checked" : ""}><span></span></label>`;
  const hours = [72, 24, 3, 1].map((h) => `<label><input type="checkbox" data-hour="${h}" ${s.notify_hours.includes(h) ? "checked" : ""}>${h >= 48 ? h / 24 + " days" : h + "h"}</label>`).join("");
  let account;
  if (acc && acc.games) {
    account = Object.entries(acc.games).map(([k, a]) => `<div class="acc-row">${G(k).icon ? `<img src="${url(G(k).icon)}" alt="">` : ""}
        <b>${esc(G(k).name)}</b><span class="muted">${esc(a.nickname)} · Lv ${Number(a.level)} · ${esc(a.server)}</span></div>`).join("")
      + `<div style="margin-top:12px"><button class="btn" id="unlink">Disconnect & delete my login</button></div>`;
  } else {
    account = `<ol class="steps">
        <li>Open <b>hoyolab.com</b> in your browser and log in.</li>
        <li>Press <b>F12</b> → <b>Application</b> tab (Storage in Firefox) → <b>Cookies</b> → hoyolab.com.</li>
        <li>Copy <b>ltuid_v2</b>, <b>ltoken_v2</b> and <b>ltmid_v2</b> into the boxes below.</li>
        <li>For energy, turn on <b>Real-Time Notes</b> in HoYoLAB → Battle Chronicle → settings.</li></ol>
      <div class="link-form">
        <label>ltuid_v2<input type="text" id="ltuid" autocomplete="off" spellcheck="false"></label>
        <label>ltoken_v2<textarea id="ltoken" rows="2" autocomplete="off" spellcheck="false"></textarea></label>
        <label>ltmid_v2<input type="text" id="ltmid" autocomplete="off" spellcheck="false"></label>
        <div><button class="btn primary" id="link">Connect</button> <span id="link-msg"></span></div></div>
      <div class="safe">🔒 Your password is never needed. These cookies can't change your password or email, log into your
        game or spend anything. They're stored encrypted on this PC only (key in Windows Credential Manager) and never
        sent anywhere except HoYoLAB. Disconnect anytime.</div>`;
  }
  return `<h1>Settings</h1><div class="muted">Changes are saved right away.</div>
  <div class="settings" style="margin-top:18px">
    <div class="box"><h3>🌍 Game server</h3>
      <div class="field"><div>Server<small>End times are shown for this server</small></div>
        <select data-set="server">${["europe", "america", "asia", "tw"].map((v) => `<option value="${v}" ${s.server === v ? "selected" : ""}>${v === "tw" ? "TW/HK/MO" : v[0].toUpperCase() + v.slice(1)}</option>`).join("")}</select></div></div>
    <div class="box"><h3>🔔 Notifications</h3>
      <div class="field"><div>New events & banners</div>${sw("notify_new")}</div>
      <div class="field"><div>New redeem codes</div>${sw("notify_codes")}</div>
      <div class="field"><div>Ending soon<small>Remind me this long before something ends</small></div><div class="checks">${hours}</div></div>
      <div class="field"><div>Energy almost full<small>Resin / Trailblaze Power / Battery (needs HoYoLAB)</small></div>
        <div><input type="number" min="5" max="600" data-set="resin_minutes" value="${Number(s.resin_minutes)}" style="width:80px"> min before ${sw("notify_resin")}</div></div>
      <div class="field"><div>Evening dailies reminder</div><div><input type="time" data-set="daily_time" value="${esc(s.daily_time)}"> ${sw("notify_daily")}</div></div>
      <div class="field"><div>Start with Windows<small>Runs quietly in the tray so reminders always arrive</small></div>${sw("start_with_windows")}</div>
    </div>
    <div class="box"><h3>🔗 HoYoLAB account</h3>${account}</div>
  </div>`;
}

// ---------------------------------------------------------------- rendering
function renderNav() {
  const now = nowSec();
  const item = (k) => {
    const g = G(k), urgent = active(k).filter((i) => i.start <= now && i.end - now < 86400).length;
    return `<button class="nav-btn ${VIEW === k ? "active" : ""}" data-view="${k}" style="--c:${esc(g.color)}">
      ${g.icon ? `<img src="${url(g.icon)}" alt="">` : `<span class="emo">${esc(g.emoji)}</span>`}${esc(g.name)}
      ${urgent ? `<span class="count" title="ending within 24h">${urgent}</span>` : ""}</button>`;
  };
  $("#nav").innerHTML = `<button class="nav-btn ${VIEW === "home" ? "active" : ""}" data-view="home"><span class="emo">✨</span>Overview</button>`
    + ORDER.map(item).join("");
  $("#nav-settings").classList.toggle("active", VIEW === "settings");
  const mins = DATA.updated ? Math.round((nowSec() - DATA.updated) / 60) : null;
  $("#updated").textContent = mins === null ? "Loading…" : mins < 1 ? "Updated just now" : `Updated ${mins} min ago`;
}

function render() {
  if (!DATA) return;
  renderNav();
  const main = $("#view");
  if (!DATA.updated && !DATA.items.length && VIEW !== "settings") {
    main.innerHTML = `<div class="loading"><div class="spinner"></div><p>Fetching the latest events…</p></div>`;
    return;
  }
  main.innerHTML = VIEW === "home" ? viewHome() : VIEW === "settings" ? viewSettings() : viewGame(VIEW);
  tickTimers();
}

async function load() {
  try {
    DATA = await api().get_data();
    if (VIEW !== "settings" || !document.querySelector(".settings")) render();
    else renderNav();
  } catch (e) {
    console.error(e);
  }
}

// ---------------------------------------------------------------- events
document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-view],[data-open],[data-copy],#refresh,#link,#unlink");
  if (!t) return;
  e.preventDefault();
  if (t.dataset.view) {
    VIEW = t.dataset.view;
    render();
    $("#view").scrollTop = 0;
  } else if (t.dataset.open) {
    await api().open_url(t.dataset.open);
  } else if (t.dataset.copy) {
    await api().copy(t.dataset.copy);
    toast(`Copied ${t.dataset.copy}`);
  } else if (t.id === "refresh") {
    t.classList.add("spin");
    await api().refresh();
    setTimeout(async () => { await load(); t.classList.remove("spin"); }, 6000);
  } else if (t.id === "link") {
    const msg = $("#link-msg");
    msg.className = "muted";
    msg.textContent = "Checking with HoYoLAB…";
    const r = await api().link($("#ltuid").value, $("#ltoken").value, $("#ltmid").value);
    if (r.ok) {
      toast("Connected! 🎉");
      DATA = await api().get_data();
      render();
      setTimeout(load, 8000);
    } else {
      msg.className = "err";
      msg.textContent = r.error;
    }
  } else if (t.id === "unlink") {
    await api().unlink();
    toast("Disconnected. Your login was deleted.");
    DATA = await api().get_data();
    render();
  }
});

document.addEventListener("change", async (e) => {
  const el = e.target;
  if (!el.closest(".settings")) return;
  const s = { ...DATA.settings };
  if (el.dataset.set) s[el.dataset.set] = el.type === "checkbox" ? el.checked : el.value;
  if (el.dataset.hour) {
    s.notify_hours = [...document.querySelectorAll("[data-hour]")].filter((x) => x.checked).map((x) => Number(x.dataset.hour));
  }
  DATA.settings = await api().save_settings(s);
  toast("Saved ✓");
});

window.addEventListener("pywebviewready", async () => {
  // while the first download runs, check every 2 s so each game appears as soon as it's ready
  let lastCount = -1;
  while (!DATA || !DATA.updated) {
    await load();
    if (DATA && DATA.items.length !== lastCount) { lastCount = DATA.items.length; render(); }
    await new Promise((r) => setTimeout(r, 2000));
  }
  render();
  setInterval(load, 30000);
  setInterval(tickTimers, 1000);
});
