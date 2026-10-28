// Runs the real config.js + content.js as two classic scripts in one context (like Chrome content scripts) with
// stubbed browser APIs, against the profile.json at argv[2]. Prints one JSON line. Used by test_extension.py.
const vm = require("vm"), fs = require("fs"), path = require("path");
const dir = path.join(__dirname, "..", "chrome_extension") + path.sep;
function boot(response) {
  const doc = { querySelectorAll: () => [], querySelector: () => null, getElementById: () => null,
                body: { innerText: "" }, documentElement: { appendChild() {} }, referrer: "" };
  const ctx = { document: doc, location: { href: "https://example.com/apply" },
    console: { log() {}, warn() {}, error() {} }, setInterval: () => 1, clearInterval() {}, setTimeout,
    chrome: { runtime: { getURL: (p) => p } },
    fetch: async (u) => (u.includes("profile.json") ? response : { ok: false }),
    getComputedStyle: () => ({}), CSS: { escape: (s) => s } };
  ctx.window = ctx; ctx.window.top = ctx;
  vm.createContext(ctx);
  for (const f of ["config.js", "content.js"]) vm.runInContext(fs.readFileSync(dir + f, "utf8"), ctx, { filename: f });
  return { ctx, ev: (s) => vm.runInContext(s, ctx) };
}
(async () => {
  const profile = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const a = boot({ ok: true, json: async () => profile });
  const out = { initialKeys: a.ev("Object.keys(PROFILE).length") };
  out.fillOnEmptyPage = await a.ctx.__autofillRun();
  out.loadedKeys = a.ev("Object.keys(PROFILE).length");
  out.email = a.ev('valueFor("email", null)');
  out.website = a.ev('valueFor("website", null)');
  out.location = a.ev('valueFor("location", null)');
  out.degree = a.ev('valueFor("degree", null)');
  out.eduEndYear = a.ev('valueFor("eduEndYear", null)');
  out.legalFirst = a.ev('nameValue("nameFirst", ["Legal first name"])');
  out.everydayFirst = a.ev('nameValue("nameFirst", ["First name"])');
  const b = boot({ ok: false, status: 404 });
  out.missingFill = await b.ctx.__autofillRun();
  out.missingFlag = b.ev("profileMissing");
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
