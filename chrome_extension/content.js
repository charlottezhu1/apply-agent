// Runs on every page. Does nothing until you trigger a fill.
// The only automatic thing is showing a small button when the page looks like a job form.

function labelText(el) {
  const parts = [];
  const root = el.getRootNode();
  if (el.labels) for (const l of el.labels) parts.push(l.innerText);
  const by = el.getAttribute("aria-labelledby");
  if (by) {
    for (const id of by.split(/\s+/)) {
      const n = root.getElementById ? root.getElementById(id) : document.getElementById(id);
      if (n) parts.push(n.innerText);
    }
  }
  // Fallback for forms that don't link labels properly (Ashby and others): climb up until we reach
  // the block that holds this field's label, stopping before the block gets shared with other fields.
  if (!parts.length) {
    let node = el.parentElement;
    for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
      if (node.querySelectorAll(FIELD_SEL).length > 1) break;
      const l = node.querySelector("label, legend");
      if (l && l.innerText.trim()) {
        parts.push(l.innerText);
        break;
      }
    }
  }
  return parts;
}

function signals(el) {
  return [
    el.getAttribute("autocomplete"),
    el.name,
    el.id,
    el.placeholder,
    el.getAttribute("aria-label"),
    el.getAttribute("data-automation-id"),
    ...labelText(el)
  ]
    .filter(Boolean)
    .map((s) => s.replace(/\s+/g, " ").trim());
}

const AUTOCOMPLETE = {
  email: "email",
  tel: "phone",
  "postal-code": "zip",
  "address-level2": "city",
  "address-level1": "state",
  country: "country",
  "country-name": "country",
  "street-address": "address",
  "address-line1": "address"
  // Name autocomplete values (given-name, family-name, name...) are handled by RULES,
  // so the label can still decide between legal and everyday name.
};

// True when the field sits in an education block: the nearest ancestor that holds a school/degree/discipline
// field and no job-title/company field. Keeps "Start date month" in work experience from getting school dates.
function inEducation(el) {
  const EDU = /school|university|degree|discipline|field of study/i;
  const WORK = /company|employer|job title|\btitle\b/i;
  let node = el.parentElement;
  for (let i = 0; i < 10 && node; i++, node = node.parentElement) {
    let edu = false, work = false;
    for (const f of node.querySelectorAll(FIELD_SEL)) {
      const t = signals(f).join(" ");
      if (EDU.test(t)) edu = true;
      if (WORK.test(t)) work = true;
    }
    if (work) return false;
    if (edu) return true;
  }
  return false;
}

function keyFromSignals(sigs, el) {
  for (const rule of RULES) {
    if (sigs.some((s) => rule.re.test(s))) {
      if (rule.edu && !(el && inEducation(el))) continue;
      return rule.key === "SKIP" ? null : rule.key;
    }
  }
  return null;
}

function pickKey(el) {
  const ac = (el.getAttribute("autocomplete") || "").toLowerCase();
  if (AUTOCOMPLETE[ac]) return AUTOCOMPLETE[ac];
  return keyFromSignals(signals(el), el);
}

// Name keys start with "name". Legal vs everyday depends on the field's label text.
// Missing legal fields in PROFILE fall back to the everyday ones.
function nameValue(key, sigs) {
  const P = PROFILE;
  const legal = sigs.some((s) => LEGAL_RE.test(s));
  const join = (a, b) => [a, b].filter(Boolean).join(" ");
  const first = legal ? P.legalFirstName || P.firstName : P.firstName;
  const last = legal ? P.legalLastName || P.lastName : P.lastName;
  switch (key) {
    case "nameFirst":
      return first;
    case "nameLast":
      return last;
    case "nameMiddle": {
      const m = legal ? P.legalMiddleName || P.middleName : P.middleName;
      if (!m) return "";
      return sigs.some((s) => /initial|\bmi\b/i.test(s)) ? m[0] : m;
    }
    case "nameFull":
      return legal ? P.fullLegalName || join(first, last) : P.fullName || join(first, last);
    case "nameLastFirst":
      return first && last ? `${last}, ${first}` : "";
    case "namePreferred":
      return sigs.some((s) => /full|complete|first.*last/i.test(s))
        ? P.fullPreferredName || P.fullName || join(P.firstName, P.lastName)
        : P.preferredFirstName || P.firstName;
  }
  return "";
}

function valueFor(key, el) {
  if (key.startsWith("name")) return nameValue(key, signals(el));
  if (key === "location") return PROFILE.location || [PROFILE.city, PROFILE.state].filter(Boolean).join(", ");
  // No personal site in PROFILE: put GitHub in the website box, unless the page has its own GitHub box.
  if (key === "website" && !PROFILE.website) {
    return candidates().some((f) => pickKey(f) === "github") ? "" : PROFILE.github;
  }
  if (key === "degree") return PROFILE.degreeOptions || PROFILE.degree;
  if (key === "eduEndYear") return PROFILE.eduEndYear || PROFILE.gradYear;
  return PROFILE[key];
}

// Setting .value directly breaks React and similar frameworks, so use the native setter.
function setNative(el, value) {
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
}

// Fires the events a real keystroke would, without leaving the field.
function typeValue(el, value) {
  setNative(el, value);
  el.dispatchEvent(new InputEvent("input", { data: String(value), inputType: "insertText", bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

// Many forms (Ashby, for example) only save a field's value when it loses focus, so a plain
// "set the value" can look filled on screen but still fail validation with "Missing entry".
// Focus the field, set the value, then really blur it, like a person tabbing away.
function setValue(el, value) {
  try {
    el.focus({ preventScroll: true });
  } catch (e) {}
  el.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
  typeValue(el, value);
  el.blur();
  el.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
}

// "No" should pick "No, I have not been employed by PwC", not "I do not know".
const startsWord = (text, want) => text.startsWith(want) && !/\w/.test(text[want.length] || "");

function setSelect(el, value) {
  const want = String(value).toLowerCase();
  const opts = [...el.options];
  const hit =
    opts.find((o) => o.text.trim().toLowerCase() === want) ||
    opts.find((o) => startsWord(o.text.trim().toLowerCase(), want)) ||
    opts.find((o) => o.text.toLowerCase().includes(want)) ||
    opts.find((o) => want.includes(o.text.trim().toLowerCase()) && o.text.trim());
  if (!hit) return false;
  el.value = hit.value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

const FIELD_SEL =
  "input:not([type=hidden]):not([type=file]):not([type=submit]):not([type=button]):not([type=checkbox]):not([type=radio]), textarea, select";

// Some sites (SmartRecruiters, for example) hide their fields inside shadow DOM, so walk into open shadow roots too.
function collect(root, out) {
  out.push(...root.querySelectorAll(FIELD_SEL));
  for (const host of root.querySelectorAll("*")) {
    if (host.shadowRoot) collect(host.shadowRoot, out);
  }
  return out;
}

function candidates() {
  return collect(document, []).filter((el) => !el.disabled && !el.readOnly && el.offsetParent !== null);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function bestOption(opts, value) {
  const want = String(value).toLowerCase();
  const txt = (o) => o.textContent.trim().toLowerCase();
  return (
    opts.find((o) => txt(o) === want) ||
    opts.find((o) => startsWord(txt(o), want)) ||
    opts.find((o) => txt(o).includes(want)) ||
    opts.find((o) => txt(o) && want.includes(txt(o)))
  );
}

// Custom dropdowns (Greenhouse and others): type to filter, then click the matching option.
function isCombobox(el) {
  return (
    el.getAttribute("role") === "combobox" ||
    el.getAttribute("aria-haspopup") === "listbox" ||
    el.getAttribute("aria-autocomplete") === "list"
  );
}

function alreadyChosen(el) {
  const box = el.closest("[class*='select']");
  return !!(box && box.querySelector("[class*='singleValue'], [class*='single-value']"));
}

const isVisible = (n) => n.offsetParent !== null || n.getClientRects().length > 0;

// Suggestion lists (Google Places, Lever, react-select...) appear a moment after typing, so poll for them.
// Only options sitting just below the input count, so unrelated menus on the page are ignored.
async function waitForOptions(el, timeout = 2500) {
  const listId = el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
  const SEL =
    "[role=option], .pac-item, [role=listbox] li, ul[class*='dropdown'] li, [class*='suggest'] li, [class*='autocomplete'] li, [class*='results'] li";
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    await sleep(150);
    let opts = [];
    const root = el.getRootNode();
    const lb = listId && root.getElementById ? root.getElementById(listId) : null;
    if (lb) opts = [...lb.querySelectorAll("[role=option], li")];
    if (!opts.length) {
      const r = el.getBoundingClientRect();
      opts = [...document.querySelectorAll(SEL)].filter((o) => {
        if (!isVisible(o) || !o.textContent.trim()) return false;
        const b = o.getBoundingClientRect();
        return b.top >= r.bottom - 12 && b.top <= r.bottom + 600 && b.left < r.right && b.right > r.left;
      });
    }
    opts = opts.filter((o) => o.textContent.trim());
    if (opts.length) return opts;
  }
  return [];
}

function clickOption(o) {
  for (const t of ["mouseover", "mousedown", "mouseup", "click"]) o.dispatchEvent(new MouseEvent(t, { bubbles: true }));
}

// Type to filter, wait for suggestions, click the matching one.
// loose=true (used for locations) accepts the first suggestion, since "Stanford, California"
// will come back as something like "Stanford, CA, USA".
async function setTypeahead(el, value, loose) {
  el.focus();
  typeValue(el, String(value));
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true }));
  el.dispatchEvent(new KeyboardEvent("keyup", { key: "a", bubbles: true }));
  const opts = await waitForOptions(el);
  const hit = bestOption(opts, value) || (loose ? opts[0] : null);
  if (hit) {
    clickOption(hit);
    await sleep(150);
    return true;
  }
  if (!loose) typeValue(el, ""); // strict dropdowns: don't leave stray text behind
  return !!el.value;
}

// ---- Dates ----
// Picks the format the field asks for (placeholder, input type), starting from PROFILE.gradDate (MM/DD/YYYY).
function formatDate(el, raw) {
  let y, m, d;
  let r = String(raw).match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{4})$/);
  if (r) [, m, d, y] = r;
  else if ((r = String(raw).match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) [, y, m, d] = r;
  else return String(raw);
  const mm = m.padStart(2, "0"), dd = d.padStart(2, "0");
  if (el.type === "date") return `${y}-${mm}-${dd}`;
  if (el.type === "month") return `${y}-${mm}`;
  const hint = [el.placeholder, el.getAttribute("aria-label"), el.getAttribute("data-format")].filter(Boolean).join(" ");
  const sep = (hint.match(/[\/.-]/) || ["/"])[0];
  if (/y{4}\W*m{2}\W*d{2}/i.test(hint)) return [y, mm, dd].join(sep);
  if (/d{2}\W*m{2}\W*y{4}/i.test(hint)) return [dd, mm, y].join(sep);
  if (/m{2,3}\W*y{4}/i.test(hint) && !/d{2}/i.test(hint)) return [mm, y].join(sep);
  return `${mm}/${dd}/${y}`;
}

// Fallback for masked inputs that ignore pasted values: type one character at a time.
async function typeInto(el, text) {
  el.focus();
  setNative(el, "");
  el.dispatchEvent(new Event("input", { bubbles: true }));
  for (const ch of text) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true }));
    setNative(el, el.value + ch);
    el.dispatchEvent(new InputEvent("input", { data: ch, inputType: "insertText", bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));
    await sleep(15);
  }
}

// Date pickers open a calendar on focus. Set the text, then blur and click outside so the calendar closes
// and the typed date is kept.
async function setDate(el, raw) {
  const text = formatDate(el, raw);
  el.focus();
  setNative(el, text);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(100);
  if (el.value !== text) {
    await typeInto(el, text);
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  el.blur();
  el.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  for (const t of ["mousedown", "mouseup", "click"]) document.body.dispatchEvent(new MouseEvent(t, { bubbles: true }));
  await sleep(150);
  return el.value === text;
}

// ---- Per-job answers from the pipeline (generated/packets.json, written by submission_preparer) ----
// The file is read fresh on every fill, so new answers work without reloading the extension.
async function loadPacket() {
  try {
    const r = await fetch(chrome.runtime.getURL("generated/packets.json"), { cache: "no-store" });
    if (!r.ok) return null;
    const packets = await r.json();
    let top = "";
    try { top = window.top.location.href; } catch (e) {} // cross-origin iframe: fall back to referrer
    const hay = [location.href, document.referrer, top].join(" ");
    const hits = packets.filter((p) => p.match.some((k) => hay.includes(k)));
    return hits.length === 1 ? hits[0] : null; // ambiguous → fill nothing rather than the wrong job's answers
  } catch (e) {
    return null;
  }
}

// ---- The applicant's own values (generated/profile.json, written from applicant/profile.yaml) ----
// Read fresh on every fill, like the packets. Without it nothing can be filled.
let profileMissing = false;
async function loadProfile() {
  try {
    const r = await fetch(chrome.runtime.getURL("generated/profile.json"), { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    PROFILE = await r.json();
    profileMissing = false;
  } catch (e) {
    profileMissing = true;
    console.warn("[autofill] no profile: run `python -m apply_agent.sync` in the apply_agent folder", e);
  }
  return !profileMissing;
}

const STOP = new Set(["the", "and", "you", "your", "for", "are", "this", "that", "with", "what", "why", "how", "our", "about", "would", "like", "please"]);
const words = (s) => new Set(s.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter((w) => w.length > 2 && !STOP.has(w)));

function bestAnswer(sigs, answers) {
  let best = null, bestScore = 0;
  const label = words(sigs.join(" "));
  for (const [q, a] of Object.entries(answers)) {
    const qw = words(q);
    if (!qw.size) continue;
    let hit = 0;
    for (const w of qw) if (label.has(w)) hit++;
    const score = hit / qw.size;
    if (score > bestScore) { best = a; bestScore = score; }
  }
  return bestScore >= 0.6 ? best : null;
}

// ---- Radio buttons (Yes/No groups) ----
// Radios are not in FIELD_SEL, so they are grouped here, the question text is found, and the matching option is clicked.
const RADIO_KEYS = new Set(["answerNo", "workAuthorized", "needsSponsorship", "veteran", "disability", "gender", "race"]);

function radioLabel(r) {
  const l = r.labels && r.labels[0];
  if (l && l.innerText.trim()) return l.innerText.trim();
  return (r.getAttribute("aria-label") || r.nextSibling?.textContent || r.value || "").trim();
}

function radioQuestion(group) {
  const first = group[0];
  const parts = [];
  const fs = first.closest("fieldset, [role=radiogroup], [role=group]");
  if (fs) {
    const legend = fs.querySelector("legend");
    if (legend) parts.push(legend.innerText);
    const by = fs.getAttribute("aria-labelledby");
    if (by) for (const id of by.split(/\s+/)) parts.push(document.getElementById(id)?.innerText || "");
    parts.push(fs.getAttribute("aria-label") || "");
  }
  // Fallback: the smallest ancestor holding every option, widened a little to reach the question text.
  let node = first.parentElement;
  while (node && !group.every((r) => node.contains(r))) node = node.parentElement;
  for (let i = 0; i < 3 && node; i++, node = node.parentElement) {
    const t = node.innerText.replace(group.map(radioLabel).join("|"), "");
    if (t.replace(/\W/g, "").length > 15) {
      parts.push(node.innerText);
      break;
    }
  }
  return parts.filter(Boolean).map((p) => p.replace(/\s+/g, " ").trim());
}

async function fillRadios(report) {
  const radios = [];
  const walk = (root) => {
    radios.push(...root.querySelectorAll("input[type=radio]"));
    for (const h of root.querySelectorAll("*")) if (h.shadowRoot) walk(h.shadowRoot);
  };
  walk(document);
  const groups = new Map();
  for (const r of radios) {
    if (r.disabled || !(isVisible(r) || (r.labels?.[0] && isVisible(r.labels[0])))) continue;
    const id = r.name ? `${r.form ? "f" : ""}:${r.name}` : r;
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(r);
  }
  let filled = 0;
  for (const group of groups.values()) {
    if (group.some((r) => r.checked)) continue; // never overwrite an answer
    const sigs = radioQuestion(group);
    const key = keyFromSignals(sigs, null);
    if (!key || !RADIO_KEYS.has(key) || !PROFILE[key]) continue;
    const opts = group.map((r) => ({ textContent: radioLabel(r), radio: r }));
    const hit = bestOption(opts, PROFILE[key]);
    const q = (sigs[0] || "?").slice(0, 40);
    if (!hit) {
      report.push(`FAILED (radio): ${q} (${key})`);
      continue;
    }
    hit.radio.click();
    filled++;
    report.push(`filled (radio): ${q} → ${hit.textContent} (${key})`);
  }
  return filled;
}

async function fillPage() {
  if (!(await loadProfile())) return 0;
  let filled = 0;
  const report = [];
  const packet = await loadPacket();
  if (packet) report.push(`packet: ${packet.folder}`);
  for (const el of candidates()) {
    const key = pickKey(el);
    if (!key) {
      // Essay questions: fill an empty free-text field with this job's drafted answer.
      if (packet && !el.value && (el.tagName === "TEXTAREA" || el.type === "text")) {
        const ans = bestAnswer(signals(el), packet.answers || {});
        if (ans) {
          setValue(el, ans);
          filled++;
          report.push(`filled (answer): ${(signals(el)[0] || "?").slice(0, 40)}`);
        }
      }
      continue;
    }
    const value = valueFor(key, el);
    if (!value || !value.length) continue;
    const label = (signals(el).find((s) => s.length > 2 && s.length < 60) || "?").slice(0, 40);
    let ok = false;
    const choices = [].concat(value); // some fields (degree) offer several wordings to try
    if (el.tagName === "SELECT") {
      ok = choices.some((v) => setSelect(el, v));
    } else if (el.value || alreadyChosen(el)) {
      report.push(`skipped (already has a value): ${label}`);
      continue; // Never overwrite something you already typed.
    } else if (key === "gradDate") {
      ok = await setDate(el, value);
    } else if (key === "location") {
      ok = await setTypeahead(el, value, true);
    } else if (isCombobox(el)) {
      for (const v of choices) if ((ok = await setTypeahead(el, v, false))) break;
    } else {
      setValue(el, choices[0]);
      ok = true;
    }
    if (ok) filled++;
    report.push(`${ok ? "filled" : "FAILED"}: ${label} (${key})`);
  }
  filled += await fillRadios(report);
  // Open the page console to see exactly what was matched.
  console.log("[autofill]", filled, "fields\n" + report.join("\n"));
  return filled;
}

// Exposed so the popup and shortcut can call it.
window.__autofillRun = fillPage;

// Light detection: looks like a job form if several known fields are present
// and the page mentions a resume or application.
function looksLikeJobForm() {
  const matched = candidates().filter((el) => pickKey(el)).length;
  const hasFile = !!document.querySelector("input[type=file]");
  const text = (document.body?.innerText || "").slice(0, 5000).toLowerCase();
  const jobWords = /resume|cv|apply|application|cover letter/.test(text) || hasFile;
  return matched >= 4 && jobWords;
}

function showButton() {
  if (document.getElementById("__autofill_btn")) return;
  const b = document.createElement("button");
  b.id = "__autofill_btn";
  b.textContent = "Autofill";
  b.style.cssText =
    "position:fixed;bottom:20px;right:20px;z-index:2147483647;padding:10px 16px;" +
    "background:#2563eb;color:#fff;border:0;border-radius:999px;font:600 14px system-ui;" +
    "cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25)";
  b.onclick = async () => {
    const n = await fillPage();
    b.textContent = n ? `Filled ${n}` : profileMissing ? "No profile: run sync" : "Nothing to fill";
    setTimeout(() => (b.textContent = "Autofill"), 2000);
  };
  document.documentElement.appendChild(b);
}

if (SHOW_FLOATING_BUTTON) {
  // Forms often render late, so check a few times instead of watching the page forever.
  let tries = 0;
  const timer = setInterval(() => {
    if (looksLikeJobForm()) {
      showButton();
      clearInterval(timer);
    } else if (++tries >= 6) {
      clearInterval(timer);
    }
  }, 1500);
}
