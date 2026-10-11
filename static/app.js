"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage may be blocked */ } },
};

let cfg = {languages: {}, live: false}, strings = {};
const state = {type: "", level: "", closing: false, cards: {}, audio: new Map(), playing: null, answerText: ""};
const TYPES = ["", "scheme", "exam", "scholarship", "job"];
const LEVELS = ["", "national", "state"];

const t = (k, fallback) => strings[k] || fallback;

/* ---------- accessibility controls ---------- */
let fs = parseInt(store.get("fs", "16"), 10) || 16;
const applyFs = () => { document.documentElement.style.setProperty("--fs", fs + "px"); store.set("fs", fs); };
$("fs-plus").onclick = () => { fs = Math.min(24, fs + 2); applyFs(); };
$("fs-minus").onclick = () => { fs = Math.max(12, fs - 2); applyFs(); };
$("fs-reset").onclick = () => { fs = 16; applyFs(); };
const setContrast = on => { document.body.classList.toggle("hc", on); $("contrast").setAttribute("aria-pressed", on); store.set("hc", on ? "1" : "0"); };
$("contrast").onclick = () => setContrast(!document.body.classList.contains("hc"));
applyFs(); setContrast(store.get("hc", "0") === "1");

/* ---------- setup ---------- */
async function init() {
  cfg = await fetch("/api/config").then(r => r.json());
  $("lang").innerHTML = Object.keys(cfg.languages).map(l => `<option>${l}</option>`).join("");
  $("lang").value = store.get("lang", "English");
  $("state").innerHTML = '<option value="">All states</option>' + cfg.states.map(s => `<option>${s}</option>`).join("");
  $("sample-banner").hidden = !cfg.sample_data;
  $("today-line").textContent = `Today's date: ${cfg.today}. ${cfg.records} records in the knowledge base.`;
  if (!cfg.live) $("mic").disabled = true, $("mic-hint").textContent = "Voice is available when a Sarvam API key is configured.";
  buildChips();
  await loadStrings();
  runSearch();
}

function buildChips() {
  const mk = (box, items, key, labelOf) => {
    box.innerHTML = "";
    items.forEach(v => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "chip"; b.textContent = labelOf(v);
      b.setAttribute("aria-pressed", state[key] === v);
      b.onclick = () => { state[key] = v; box.querySelectorAll(".chip").forEach(x => x.setAttribute("aria-pressed", x === b)); runSearch(); };
      box.appendChild(b);
    });
  };
  mk($("type-chips"), TYPES, "type", v => v ? t(v, v[0].toUpperCase() + v.slice(1)) : t("all", "All"));
  mk($("level-chips"), LEVELS, "level", v => v ? t(v, v[0].toUpperCase() + v.slice(1)) : t("all", "All") + " levels");
}

async function loadStrings() {
  strings = await fetch("/api/ui-strings?language=" + encodeURIComponent($("lang").value)).then(r => r.json()).catch(() => ({}));
  document.querySelectorAll("[data-i18n]").forEach(el => { if (strings[el.dataset.i18n]) el.textContent = strings[el.dataset.i18n]; });
  if (strings.placeholder) $("q").placeholder = strings.placeholder;
  buildChips();
}

$("lang").onchange = async () => { store.set("lang", $("lang").value); await loadStrings(); runSearch(); };
$("state").onchange = () => runSearch();
$("closing").onclick = () => { state.closing = !state.closing; $("closing").setAttribute("aria-pressed", state.closing); runSearch(); };
$("apply-profile").onclick = () => runSearch();
$("search-form").onsubmit = e => { e.preventDefault(); runSearch(); };

const profile = () => {
  const n = id => $(id).value === "" ? null : Number($(id).value);
  const s = id => $(id).value || null;
  return {age: n("p-age"), gender: s("p-gender"), category: s("p-category"), education: s("p-education"),
          occupation: s("p-occupation"), income: n("p-income"), state: s("state")};
};
const chips = () => ({type: state.type || null, level: state.level || null, state: $("state").value || null, closing_soon: state.closing});

/* ---------- search / ask ---------- */
let runId = 0;
async function runSearch() {
  const id = ++runId, q = $("q").value.trim();
  stopAudio(); $("answer-box").hidden = true; state.answerText = "";
  const body = {q, language: $("lang").value, profile: profile(), chips: chips()};
  $("status").textContent = q ? "Searching…" : "";
  if (!q) {
    const d = await post("/api/search", body);
    if (id === runId && d.cards) renderCards(d.cards);
    $("status").textContent = d.error || "";
    return;
  }
  const resp = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!resp.ok) { $("status").textContent = (await resp.json().catch(() => ({}))).error || "Request failed."; return; }
  const typer = makeTypewriter($("answer"));
  const reader = resp.body.getReader(), dec = new TextDecoder();
  let buf = "";
  const handle = ev => {
    if (id !== runId) return;
    if (ev.event === "results") {
      renderCards(ev.cards); $("status").textContent = ev.cards.length ? t("writing", "Writing a short summary…") : "";
      $("answer-box").hidden = false; $("answer").textContent = ""; $("answer").classList.add("cursor");
      $("answer-listen").hidden = true; $("answer-en").hidden = true; $("answer-english").hidden = true;
      if (ev.language && ev.language !== "en-IN") $("answer-en").hidden = false;
    } else if (ev.event === "english") {
      $("answer-english").textContent = ev.text;
    } else if (ev.event === "text") {
      state.answerText += ev.text; $("status").textContent = ""; typer.push(ev.text);
    } else if (ev.event === "done") {
      typer.finish(() => { $("answer").classList.remove("cursor"); if (cfg.live) $("answer-listen").hidden = false; });
    } else if (ev.event === "error") {
      $("status").textContent = ev.error; typer.finish(() => $("answer").classList.remove("cursor"));
    }
  };
  for (;;) {
    const {done, value} = await reader.read(); if (done) break;
    buf += dec.decode(value, {stream: true});
    let i; while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (line) handle(JSON.parse(line)); }
  }
}

async function post(url, body) {
  try {
    const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    return await r.json();
  } catch { return {error: "Network error. Please try again."}; }
}

/* Reveals streamed text a few characters at a time so it reads like live typing. */
function makeTypewriter(el) {
  let queue = "", timer = null, finished = null;
  const tick = () => {
    if (!queue) { clearInterval(timer); timer = null; if (finished) finished(); return; }
    const n = Math.max(1, Math.ceil(queue.length / 40));
    el.textContent += queue.slice(0, n); queue = queue.slice(n);
  };
  return {
    push(s) { queue += s; if (!timer) timer = setInterval(tick, 25); },
    finish(cb) { finished = cb; if (!timer) cb(); },
  };
}

$("answer-en").onclick = () => { const e = $("answer-english"); e.hidden = !e.hidden; $("answer-en").textContent = e.hidden ? t("english", "Show English original") : t("hide_english", "Hide English"); };
$("answer-listen").onclick = () => toggleAudio("answer", state.answerText);

/* ---------- cards ---------- */
function daysBadge(c) {
  if (c.days_left === null || c.days_left === undefined) return `<span class="badge">${esc(t("not_announced", "Last date not announced"))}</span>`;
  if (c.days_left < 0) return `<span class="badge urgent">${esc(t("closed", "Closed"))}</span>`;
  return `<span class="badge ${c.days_left <= 14 ? "urgent" : ""}">${c.days_left} ${esc(t("days_left", "days left"))}</span>`;
}

function renderCards(cards) {
  state.cards = {};
  $("results").innerHTML = cards.length ? "" : `<p class="status">${esc(t("no_results", "No matching entries found."))}</p>`;
  cards.forEach(c => { state.cards[c.id] = c; $("results").appendChild(cardEl(c)); });
}

function cardEl(c) {
  const loc = c.local, show = loc || c;
  const el = document.createElement("article");
  el.className = "card t-" + c.type; el.dataset.id = c.id;
  const where = c.level === "state" ? (c.state || t("state", "State")) : t("national", "National");
  el.innerHTML = `
    <div class="badges">
      <span class="badge">${esc(t(c.type, c.type))}</span><span class="badge">${esc(where)}</span>
      ${daysBadge(c)}
      ${c.needs_verification ? `<span class="badge warn">${esc(t("verify", "Verify on the official site"))}</span>` : ""}
    </div>
    <h3>${esc(show.title)}</h3>
    <p class="authority">${esc(c.authority)}</p>
    <p class="field">${esc(show.summary)}</p>
    <p class="field"><b>${esc(t("eligible", "Who can apply"))}:</b> ${esc(show.eligibility)}</p>
    <p class="field"><b>${esc(t("benefit", "Benefit"))}:</b> ${esc(show.benefit)}</p>
    <p class="field"><b>${esc(t("how", "How to apply"))}:</b> ${esc(show.how_to_apply)}</p>
    <p class="field deadline"><b>${esc(t("deadline", "Last date"))}:</b> ${esc(c.deadline || t("not_announced", "Not announced"))}</p>
    ${c.fee ? `<p class="field"><b>Fee:</b> ${esc(c.fee)}</p>` : ""}
    <div class="card-actions">
      <button type="button" class="btn small" data-act="check">${esc(t("check", "Check eligibility"))}</button>
      ${cfg.live ? `<button type="button" class="btn small" data-act="listen">&#128266; ${esc(t("listen", "Listen"))}</button>` : ""}
      <a class="btn small" href="${esc(c.official_url)}" target="_blank" rel="noopener noreferrer">${esc(t("official", "Official site"))} &#8599;</a>
      ${c.deadline ? `<button type="button" class="btn small ghost" data-act="remind">${esc(t("reminder", "Add reminder"))}</button>` : ""}
      ${loc ? `<button type="button" class="btn small ghost" data-act="english">${esc(t("english", "Show English original"))}</button>` : ""}
    </div>
    <div class="check-out" aria-live="polite"></div>
    <div class="en-out" hidden></div>`;
  el.querySelector(".card-actions").onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    ({check: () => checkEligibility(c, el), listen: () => listenCard(c, el, b), remind: () => downloadIcs(c), english: () => toggleEnglish(c, el, b)})[b.dataset.act]?.();
  };
  return el;
}

async function checkEligibility(c, el) {
  const p = profile();
  const d = await post("/api/eligibility", {record_id: c.id, profile: p});
  const out = el.querySelector(".check-out");
  if (!d.checks) { out.textContent = d.error || "Could not check."; return; }
  if (!d.checks.length) { out.innerHTML = `<p class="hint">No fixed criteria are listed. Read the official notification.</p>`; return; }
  const label = {ok: "Matches", fail: "Does not match", unknown: "Add this in the profile to check"};
  out.innerHTML = `<ul class="checklist">` + d.checks.map(k => `<li class="${k.status}"><b>${esc(k.criterion)}</b>: ${esc(k.detail)} &mdash; ${label[k.status]}</li>`).join("") + `</ul>`;
}

function toggleEnglish(c, el, btn) {
  const box = el.querySelector(".en-out");
  box.hidden = !box.hidden;
  box.innerHTML = box.hidden ? "" : `<p class="english"><b>${esc(c.title)}</b><br>${esc(c.summary)}<br><b>Who can apply:</b> ${esc(c.eligibility)}<br><b>Benefit:</b> ${esc(c.benefit)}<br><b>How to apply:</b> ${esc(c.how_to_apply)}</p>`;
  btn.textContent = box.hidden ? t("english", "Show English original") : t("hide_english", "Hide English");
}

function downloadIcs(c) {
  const d = c.deadline.replaceAll("-", "");
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Yojana Saathi//EN", "BEGIN:VEVENT", `UID:${c.id}@yojana-saathi`,
    `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}Z`, `DTSTART;VALUE=DATE:${d}`,
    `SUMMARY:Last date: ${c.title.replace(/[,;\n]/g, " ")}`, `DESCRIPTION:Confirm on ${c.official_url}`,
    "BEGIN:VALARM", "TRIGGER:-P3D", "ACTION:DISPLAY", "DESCRIPTION:Application closes in 3 days", "END:VALARM", "END:VEVENT", "END:VCALENDAR"].join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([ics], {type: "text/calendar"}));
  a.download = "deadline.ics"; a.click(); URL.revokeObjectURL(a.href);
}

/* ---------- audio (Sarvam TTS via our server) ---------- */
function stopAudio() { if (state.playing) { state.playing.audio.pause(); state.playing.btn?.classList.remove("playing"); state.playing = null; } }

async function toggleAudio(key, text, btn) {
  if (state.playing && state.playing.key === key) {
    const a = state.playing.audio;
    if (a.paused) a.play(); else a.pause();
    return;
  }
  stopAudio();
  const lang = $("lang").value;
  const cacheKey = key + "|" + lang + "|" + text.length;
  let b64 = state.audio.get(cacheKey);
  if (!b64) {
    $("status").textContent = "Preparing audio…";
    const d = await post("/api/tts", {text, language: lang});
    $("status").textContent = "";
    if (!d.audio) { $("status").textContent = d.detail || d.error || "Audio is not available."; return; }
    b64 = d.audio; state.audio.set(cacheKey, b64);
  }
  const audio = new Audio("data:audio/wav;base64," + b64);
  audio.onended = () => { state.playing = null; };
  state.playing = {key, audio, btn};
  audio.play();
}

function listenCard(c, el, btn) {
  const show = c.local || c;
  toggleAudio(c.id, [show.title, show.summary, show.eligibility, show.benefit].filter(Boolean).join(". "), btn);
}

/* ---------- microphone (Sarvam STT, code-mixed Hindi-English supported) ---------- */
let rec, chunks = [];
$("mic").onclick = async () => {
  if (rec && rec.state === "recording") { rec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    rec = new MediaRecorder(stream); chunks = [];
    rec.ondataavailable = e => chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach(tr => tr.stop());
      $("mic").classList.remove("rec"); $("mic").setAttribute("aria-label", "Speak your question");
      const fd = new FormData(); fd.append("audio", new Blob(chunks, {type: "audio/webm"}), "q.webm");
      $("status").textContent = "Listening…";
      const r = await fetch("/api/stt", {method: "POST", body: fd});
      const d = await r.json();
      if (!r.ok) { $("status").textContent = d.detail || "Speech service error."; return; }
      $("q").value = d.transcript;
      const name = Object.keys(cfg.languages).find(k => cfg.languages[k] === d.language_code);
      if (name && name !== $("lang").value) { $("lang").value = name; store.set("lang", name); await loadStrings(); }
      runSearch();
    };
    rec.start(); $("mic").classList.add("rec"); $("mic").setAttribute("aria-label", "Stop recording");
    $("status").textContent = "Recording… tap again to stop.";
  } catch { $("status").textContent = "Microphone is not available."; }
};

init();
