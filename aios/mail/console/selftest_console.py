#!/usr/bin/env python3
"""Offline self-test for the console: state payload, provenance view, dry-run
diff/preview (D3: the preview must neither write nor touch the state), auth
refusal. Run BEFORE deploying:

    python3 selftest_console.py      (needs fastapi + pyyaml installed)
"""
import importlib
import hashlib
import json
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="aios-console-test-")
os.environ["DIGEST_STATE_DIR"] = TMP            # the LEDGER (read-only to the
# console live; here a plain dir). The config store is a SUBDIRECTORY, exactly
# as in the deployment, mounted read-write while /state is read-only.
CFG = os.path.join(TMP, "config")
os.environ["DIGEST_CONFIG_DIR"] = CFG
os.makedirs(CFG, exist_ok=True)
os.environ["CONSOLE_PASSWORD"] = ""   # routes are auth-gated; direct calls
os.environ["CONSOLE_ACTOR"] = "console:test"
os.environ.setdefault("DIGEST_PRIORITY_KEYWORDS", "bluff creek")

PARENT = os.path.dirname(os.path.abspath(__file__))
MAIL = os.path.dirname(PARENT)
DIGEST = os.path.join(MAIL, "digest")
for p in (PARENT, MAIL, DIGEST):
    if p not in sys.path:
        sys.path.insert(0, p)

import state as ST                        # noqa: E402
from engine import config as ECFG         # noqa: E402

PASS = FAIL = 0

# Node harness used by section 13: executes the REAL served <script> against a
# real state payload with a minimal DOM shim, and prints OK only if load()
# renders every section without throwing. %s = html, %s = payload JSON.
_JS_SIM = r'''
import fs from 'fs';
const html = %s;
const s = %s;
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const mk = () => ({innerHTML:'', textContent:'', style:{}, value:'{}', disabled:false, tBodies:[{innerHTML:''}]});
const els = {};
global.document = {getElementById:(id)=>{ if(!els[id]) els[id]=mk(); return els[id]; }, body:{innerHTML:''}};
global.fetch = async (url)=>({status:200, json:async()=>{
  const u = String(url);
  if (u.includes('/api/config/diff')) return {current_version:'vA', proposed_version:'vB', changes:[{key:'filed_days', from:'10', to:'14'}], note:'x'};
  if (u.includes('/api/config/save')) return {saved:false, version:'vA', changes:[], note:'no change'};
  if (u.includes('/api/dryrun')) return {items_scanned:1, proposed_version:'vB', changed:0, changes:[], note:'x'};
  return s;
}});
const AF = (async function(){}).constructor;
const code = script.replace(/document\.getElementById\('drybtn'\)\.onclick = dryrun;\s*load\(\);\s*$/, '');
const mod = await new AF(code + '\nreturn {load, diff, dryrun, save};')();
await mod.load();
for (const id of ['cards','fires','cadence','models','journal']) {
  if (!els[id] || !els[id].innerHTML.length) throw new Error('section not rendered: '+id);
}
if (!els.rules.tBodies[0].innerHTML.length) throw new Error('rules table not rendered');
if (!els.recent.tBodies[0].innerHTML.length) throw new Error('recent table not rendered');
if (els.fires.innerHTML.includes('loading')) throw new Error('fires still loading');
await mod.diff(); await mod.dryrun();
console.log('OK');
'''


def check(name, cond, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" +
          ("" if cond else f"  {detail}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def _ts(i):
    return "2026-10-21T{:02d}:00:00Z".format(i)


def intent(i, folder, subj, sname, saddr, prev=""):
    return {"t": "intent", "id": f"it{i}", "folder": folder, "cfg": "vTEST",
            "subject": subj, "sender_name": sname, "sender_addr": saddr,
            "preview": prev, "tier": "other", "reason": None,
            "privacy": None, "self_notice": False, "unread": True,
            "received": _ts(i), "ts": _ts(i)}


rows = [
    # K3 keep items currently in the inbox
    intent(1, "keep", "Magic-receipt from WidgetCo", "WidgetCo",
           "store@widgetco.com"),
    intent(2, "keep", "weekly newsletter", "Shop", "news@shop.example"),
    intent(3, "keep", "crossbar league signups", "Parks", "parks@city.example"),
    # filed items
    intent(4, "AIOS-Filed", "old promo", "Sales", "sales@promo.example"),
    intent(5, "Receipts", "Invoice from Acme", "Acme", "billing@acme.example"),
]
outcomes = [
    {"t": "outcome", "id": f"it{i}",
     "status": "keep" if i <= 3 else "filed",
     "folder": ("keep" if i <= 3 else ("AIOS-Filed" if i == 4
               else "Receipts")), "reported": False, "ts": _ts(i)}
    for i in (1, 2, 3, 4, 5)]
# one unresolved intent (crash between intent and outcome)
rows.append(intent(9, "AIOS-Filed", "ghost item", "Ghost", "g@h.example"))

with open(os.path.join(TMP, "ledger.jsonl"), "w") as fh:
    for r in rows + outcomes:
        fh.write(json.dumps(r) + "\n")

import yaml

print("== 1. import console + state payload ==")
try:
    import fastapi  # noqa: F401
except ImportError:
    print("fastapi not installed locally — run: pip3 install 'fastapi==0.115.6'"
          " 'uvicorn==0.32.0' 'pyyaml==6.0.3' (test-only); skipping.")
    sys.exit(0)
import console as C

s = C._state_payload()
check("items counted", s["ledger"]["items"] == 6, s["ledger"]["items"])
check("inbox estimate = 3 keeps", s["ledger"]["in_inbox_estimate"] == 3)
check("filed by folder", s["ledger"]["filed_by_folder"].get("Receipts") == 1
      and s["ledger"]["filed_by_folder"].get("AIOS-Filed") == 1,
      s["ledger"]["filed_by_folder"])
check("unresolved intent surfaced", s["ledger"]["unresolved"] == 1
      and s["ledger"]["unresolved_samples"][0]["subject"] == "ghost item")
check("backlog = all unreported", s["ledger"]["pending_digest_report"] == 5)

print("== 2. provenance: file override wins on its keys only ==")
# save the way the real UI will: full effective config (env-derived) with
# the override on top — NOT from_dict(partial), which would silently reset
# every other key to its default
merged = ECFG.asdict(ECFG.from_env())
merged.pop("source", None)
merged["filed_days"] = 21
ECFG.save(ECFG.from_dict(merged, source="file"), actor="test", state_dir=CFG)
s2 = C._state_payload()
kc = {k["key"]: k for k in s2["config"]["keys"]}
check("filed_days origin=file", kc["filed_days"]["origin"] == "file")
check("filed_days value shown", str(kc["filed_days"]["value"]) == "21",
      kc["filed_days"])
check("other keys stay env/default",
      kc["priority_keywords"]["origin"] in ("env", "default"),
      kc["priority_keywords"])
check("model fields present",
      "prose_model" in kc and "privacy_model" in kc)
check("journal records the save",
      any(j["actor"] == "test" for j in s2["config"]["journal"]))

print("== 3. dry-run: preview computes diffs, writes NOTHING ==")


def _tree_hash():
    h = hashlib.sha256()
    for root, _dirs, files in os.walk(TMP):
        for f in sorted(files):
            with open(os.path.join(root, f), "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


before = _tree_hash()
Req = C.DryRunRequest
# a) receipt pattern routes the currently-kept WidgetCo mail to Receipts
r1 = C.api_dryrun(Req(overrides={"receipt_patterns": ["^magic-receipt"]}))
check("preview scanned candidates", r1["items_scanned"] == 5, r1["items_scanned"])
check("junk->Receipts found",
      any(g["to"] == "Receipts" and g["count"] == 1 for g in r1["changes"]),
      r1["changes"])
check("no save note", "nothing was saved" in r1["note"])
# b) keyword change routes crossbar newsletter to priority
r2 = C.api_dryrun(Req(overrides={"priority_keywords": ["crossbar"]}))
check("junk->priority found",
      any(g["to"] == "stays in inbox (priority)" and g["count"] == 1
          for g in r2["changes"]), r2["changes"])
# c) nothing is written by previewing
after = _tree_hash()
check("STATE DIR BYTE-IDENTICAL after previews", before == after)

print("== 4. D3: destructive/invalid previews are refused, not applied ==")
for bad in ({"filed_days": -1}, {"no_such_key": 1}, {}, {"scan_top": "x"}):
    try:
        C.api_dryrun(Req(overrides=bad))
        check(f"refused {bad}", False, "no exception")
    except Exception as exc:
        code = getattr(exc, "status_code", 0)
        check(f"refused {bad}(400)", code == 400, (code, str(exc)[:80]))
after2 = _tree_hash()
check("still byte-identical after refusals", after2 == before)

print("== 5. auth: password set -> refuse without header ==")
# Auth: exercise the REAL require_auth() helper directly (the previous version
# of this block was a no-op that could not fail). This matters because the
# console FAILS OPEN when CONSOLE_PASSWORD is empty -- the failure mode of a
# mis-rendered Infisical sync. Pin BOTH directions:
#   * empty password  -> open (documented fail-open; guards the sync ordering)
#   * set password    -> 401 without/with-wrong creds, pass with the right one
import base64 as b64


class _Req:
    """Minimal stand-in for a Starlette Request (require_auth reads .headers)."""

    def __init__(self, auth=None):
        self.headers = {} if auth is None else {"authorization": auth}


def _hdr(pw, user="chad"):
    return "Basic " + b64.b64encode(f"{user}:{pw}".encode()).decode()


def _status(req):
    """HTTP status of an auth check: 200 if it passes, else the raised code."""
    try:
        C.require_auth(req)
        return 200
    except Exception as exc:
        return getattr(exc, "status_code", 500)


C.PASSWORD = "secret123"
check("auth: no header -> 401", _status(_Req()) == 401, _status(_Req()))
check("auth: wrong password -> 401",
      _status(_Req(_hdr("wrong"))) == 401, _status(_Req(_hdr("wrong"))))
check("auth: right password -> pass",
      _status(_Req(_hdr("secret123"))) == 200, _status(_Req(_hdr("secret123"))))
C.PASSWORD = ""
check("auth: empty password FAILS OPEN (guards sync ordering)",
      _status(_Req()) == 200,
      "fail-open is expected; the Infisical key must exist before the sync")

print("== 6. schedule view (R1) = read-only cadence + next fires ==")
s3 = C._state_payload()
sch = s3["config"].get("schedule", {})
check("schedule present in config view", bool(sch.get("schedule")), sch)
check("schedule valid + 5 next fires",
      sch.get("valid") is True and len(sch.get("next", [])) == 5, sch)
check("schedule keys exposed for provenance",
      "digest_schedule" in kc and "digest_tz" in kc, list(kc)[-3:])

print("== 7. dry-run refuses an invalid schedule, writes nothing ==")
before3 = _tree_hash()
for bad_sched in ({"digest_schedule": "* * * * *"},
                  {"digest_schedule": "not cron"},
                  {"digest_tz": "Mars/Olympus"}):
    try:
        C.api_dryrun(Req(overrides=bad_sched))
        check(f"refused {bad_sched}", False, "accepted")
    except Exception as exc:
        check(f"refused {bad_sched}(400)",
              getattr(exc, "status_code", 0) == 400, str(exc)[:80])
check("state still byte-identical after schedule refusals",
      _tree_hash() == before3)
r_ok = C.api_dryrun(Req(overrides={"digest_schedule": "0 7,18 * * *"}))
check("valid schedule override accepted in preview",
      "changed" in r_ok and _tree_hash() == before3)

print("== 8. models view (R2) = cached catalogue, no gateway call ==")
s4 = C._state_payload()
mv = s4["config"].get("models", {})
check("models view present", "models" in mv and "prose_model" in mv, mv)
check("empty catalogue -> stale flag, prose still described",
      mv.get("stale") is True and mv["prose_model"].get("ok") is True, mv)
check("privacy model local + described (ok despite absent catalogue)",
      mv["privacy_model"].get("ok") is True
      and mv["privacy_model"].get("local") is True, mv["privacy_model"])
# seed a catalogue the way the filer would, then re-read
import model_catalog as MC  # noqa: E402
from engine import models as MODELS  # noqa: E402
cat = MODELS.parse_catalog({"data": [
    {"id": "ollama/qwen3.8:latest", "context_length": 32000},
    {"id": "openrouter/auto"}]})
MC.write_cache(TMP, cat, "https://llm.caehomelab.com/v1/models")
s5 = C._state_payload()
mv2 = s5["config"]["models"]
check("catalogue now non-stale", mv2["stale"] is False, mv2)
check("2 models listed", len(mv2["models"]) == 2, mv2["models"])
remote = next(m for m in mv2["models"] if m["id"] == "openrouter/auto")
check("remote model flagged error for prose",
      remote["prose"]["level"] == "error", remote)
localm = next(m for m in mv2["models"] if m["id"].startswith("ollama/"))
check("local model ok for prose", localm["prose"]["ok"], localm)
check("catalogue timestamp surfaced", bool(mv2.get("catalog_ts")), mv2)

print("== 9. dry-run refuses a remote model, writes nothing ==")
before4 = _tree_hash()
for bad_model in ({"prose_model": "openrouter/auto"},
                  {"privacy_model": "gpt-4o"},
                  {"prose_model": ""}):
    try:
        C.api_dryrun(Req(overrides=bad_model))
        check(f"refused {bad_model}", False, "accepted")
    except Exception as exc:
        check(f"refused {bad_model}(400)",
              getattr(exc, "status_code", 0) == 400, str(exc)[:80])
check("state byte-identical after model refusals", _tree_hash() == before4)
r_m = C.api_dryrun(Req(overrides={"prose_model": "ollama/qwen3.6:latest"}))
check("valid local model override accepted in preview",
      "changed" in r_m and _tree_hash() == before4)

print("== 10. STEP 5: the config write path = diff, save, journal =")
# A save needs a confirm; without it, refuse and write nothing.
def _cfg_hash():
    h = hashlib.sha256()
    for f in sorted(os.listdir(CFG)):
        with open(os.path.join(CFG, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()

before5 = _cfg_hash()
try:
    C.api_config_save(C.ConfigWrite(overrides={"filed_days": 30}, confirm=False))
    check("save without confirm refused", False, "accepted")
except Exception as exc:
    check("save without confirm refused(400)",
          getattr(exc, "status_code", 0) == 400, str(exc)[:80])
check("unconfirmed save wrote nothing", _cfg_hash() == before5)

# diff endpoint: pure compute, reports the change, writes nothing
before_diff = _cfg_hash()
d = C.api_config_diff(C.ConfigWrite(overrides={"filed_days": 30}))
check("diff lists the changed key",
      any(c["key"] == "filed_days" and str(c["to"]) == "30"
          for c in d["changes"]), d)
check("diff reports current->proposed versions",
      d["current_version"] != d["proposed_version"], d)
check("diff writes nothing", _cfg_hash() == before_diff)

# save: validates -> journals actor -> writes complete snapshot
res = C.api_config_save(C.ConfigWrite(overrides={"filed_days": 30},
                                      confirm=True))
check("save succeeded", res["saved"] is True, res)
check("save reports the actor", res["actor"] == "console:test", res)
check("save wrote config.yaml", os.path.exists(ECFG.config_path(CFG)))
reloaded = ECFG.load(CFG, log=lambda *a: None)
check("value persisted", reloaded.filed_days == 30, reloaded.filed_days)
check("other keys preserved (full-snapshot save)",
      reloaded.scan_top == ECFG.load(None, log=lambda *a: None).scan_top
      if False else reloaded.scan_top == ECFG.from_env().scan_top,
      reloaded.scan_top)
check("journal recorded console actor",
      any(e.get("actor") == "console:test" for e in ECFG.history(CFG)))

# a no-op save writes nothing
before_noop = _cfg_hash()
res2 = C.api_config_save(C.ConfigWrite(overrides={"filed_days": 30},
                                       confirm=True))
check("no-op save reports saved=False", res2["saved"] is False, res2)
check("no-op save wrote nothing", _cfg_hash() == before_noop)

print("== 11. STEP 5: destructive edits refused before any write ==")
for bad in ({"filed_days": -1}, {"no_such_key": 1},
            {"prose_model": "openrouter/auto"},
            {"digest_schedule": "*/5 * * * *"}):
    before_bad = _cfg_hash()
    try:
        C.api_config_save(C.ConfigWrite(overrides=bad, confirm=True))
        check(f"refused {bad}", False, "accepted")
    except Exception as exc:
        check(f"refused {bad}(400)",
              getattr(exc, "status_code", 0) == 400, str(exc)[:80])
    check(f"no write for {bad}", _cfg_hash() == before_bad)

print("== 12. STEP 5: the ledger is UNWRITABLE to the console's write path ==")
# The console writes ONLY inside CONFIG_DIR. Prove the code holds no write
# path to the ledger: state.py's append() targets STATE_DIR (the RO mount in
# prod), and the config write functions only ever touch CONFIG_DIR.
ledger = os.path.join(TMP, "ledger.jsonl")
ledger_before = open(ledger, "rb").read() if os.path.exists(ledger) else b""
C.api_config_save(C.ConfigWrite(overrides={"filed_days": 31}, confirm=True))
check("config saves never touch the ledger file",
      (open(ledger, "rb").read() if os.path.exists(ledger) else b"") == ledger_before)
# structural check: no config-write code references a ledger path
import inspect  # noqa: E402
src = inspect.getsource(ECFG.save) + inspect.getsource(ECFG.apply_overrides)
check("config.save never names ledger.jsonl",
      "ledger" not in src.lower(), src[:80])
check("state.LEDGER is outside the config store dir",
      os.path.dirname(ST.LEDGER) != CFG, (ST.LEDGER, CFG))

print("== 13. front-end JS sanity: the served script must run, esc() must work ==")
# This is the check that was MISSING and let a real bug ship: the console UI's
# HTML-escape helper was malformed (the `[c]` lookup sat OUTSIDE the arrow
# callback), so esc() threw on its FIRST call and every section after the cards
# stayed on "loading…". HTTP 200 never proved the JS ran. We now execute the
# actual served <script> in node (if present) against a real payload, and
# assert the key sections render. Skips cleanly when node is unavailable.
import re as _re
import shutil as _shutil
import subprocess as _sp

_html = open(os.path.join(PARENT, "static", "index.html"), encoding="utf-8").read()
_script = _re.search(r"<script>([\s\S]*?)</script>", _html)
check("index.html embeds a script", bool(_script))
_js = _script.group(1)
# static guards that need no JS runtime (always run):
# The buggy 0.6.0 form applied the [c] lookup to the CALLBACK's result:
#   /g,c=>({...}))[c]||c   -> reads `c` outside the callback -> throws on use
# Correct: the map is indexed INSIDE the callback -> ...}[c]));  So the buggy
# signature is the exact substring "))[c]||c" applied after a => callback.
# Check the esc DEFINITION LINE only (the bug's description lives in a comment).
check("esc() map lookup is inside the callback, not applied to its result",
      "))[c]||c" not in _re.sub(r"//[^\n]*", "", _js).replace(" ", ""),
      "malformed esc(): the [c] lookup is outside the callback")
check("esc() escapes the angle brackets too",
      "'<':'&lt;'" in _js and "'>':'&gt;'" in _js, "esc map incomplete")
check("every section element the loader needs exists",
      all(f'id="{i}"' in _html for i in
          ("cards", "fires", "cadence", "models", "rules", "journal",
           "recent", "dryout")), "missing section id")

_node = _shutil.which("node")
if not _node:
    print("  (node not installed — skipping the execute-the-script check)")
else:
    payload = C._state_payload()      # a real payload, same shape the browser gets
    sim = os.path.join(TMP, "sim.mjs")
    with open(sim, "w", encoding="utf-8") as fh:
        fh.write(_JS_SIM % (json.dumps(_html), json.dumps(payload)))
    out = _sp.run([_node, sim], capture_output=True, text=True, timeout=60)
    check("served script executes and load() renders all sections",
          "OK" in out.stdout, (out.stdout[-400:], out.stderr[-400:]))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)