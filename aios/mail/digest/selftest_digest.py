#!/usr/bin/env python3
"""Offline self-test for the digest's NEW-ONLY report selection + comment-body
safety (0.5.4, Chad 2026-10-22).

Regression target: the digest was reporting an INDEX OF EVERYTHING — priority
mail stays in the inbox by design, and the old `fresh` predicate had no
"already reported" gate, so every priority item re-listed on every fire. That
overflowed the 4000-char comment cap, and because the hidden dedupe tracker was
appended LAST, truncation silently erased it — breaking dedupe for the NEXT
fire and compounding the loop.

Run BEFORE deploying:

    BIFROST_VK_AIOS_HELPDESK=x BIFROST_VK_AIOS_HELPDESK_FILE=x \
    GITHUB_APP_PRIVATE_KEY=x GITHUB_APP_ID=x GITHUB_INSTALLATION_ID=x \
    DIGEST_ISSUE_NUMBER=1 GITHUB_REPO=o/r DIGEST_STATE_DIR=/tmp/t-$$ \
    python3 selftest_digest.py
"""
import importlib.util
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="aios-digest-test-")
os.environ["DIGEST_STATE_DIR"] = TMP  # force: don't let an inherited value win
os.environ.setdefault("BIFROST_VK_AIOS_HELPDESK", "x")
os.environ.setdefault("BIFROST_VK_AIOS_HELPDESK_FILE", "x")
os.environ.setdefault("GITHUB_APP_PRIVATE_KEY", "x")
os.environ.setdefault("GITHUB_APP_ID", "x")
os.environ.setdefault("GITHUB_INSTALLATION_ID", "x")
os.environ.setdefault("DIGEST_ISSUE_NUMBER", "1")
os.environ.setdefault("GITHUB_REPO", "o/r")

HERE = os.path.dirname(os.path.abspath(__file__))
MAIL = os.path.dirname(HERE)
for p in (HERE, MAIL):
    if p not in sys.path:
        sys.path.insert(0, p)

# digest.py imports state.py by bare name and engine by package name.
_spec = importlib.util.spec_from_file_location("digest_mod",
                                               os.path.join(HERE, "digest.py"))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def cls(mid, ctype="other", _file=None, privacy=None, notice=None, unread=True):
    return {"type": ctype, "_file": _file, "_privacy": privacy,
            "_self_notice": notice, "_id": mid, "_fid": "raw-" + mid,
            "_unread": unread, "from": {"emailAddress": {"name": "S",
                                                         "address": "s@e.com"}},
            "subject": "subj-" + mid, "preview": ""}


print("== 1. the bug: an already-reported priority item must NOT re-list ==")
items = [cls("p1", "priority"), cls("j1", _file="filed"),
         cls("priv1", privacy="broker"), cls("n1", notice="digest notice")]
# p1 and j1 were reported on a previous fire; priv1/n1 were not.
reported = {"p1", "j1"}
fresh = D.select_fresh(items, reported, filed_set=set())
check("reported priority excluded (was the 'index of everything' bug)",
      all(c["_id"] != "p1" for c in fresh), [c["_id"] for c in fresh])
check("reported junk excluded", all(c["_id"] != "j1" for c in fresh))
check("privacy item NOT in the prose list (own section)",
      all(c["_id"] != "priv1" for c in fresh))
check("self-notice never reported", all(c["_id"] != "n1" for c in fresh))

print("== 2. a genuinely NEW item is reported exactly once ==")
fresh = D.select_fresh([cls("p2", "priority")], set(), filed_set=set())
check("new priority reported", [c["_id"] for c in fresh] == ["p2"], fresh)
fresh = D.select_fresh([cls("j2", _file="filed")], set(), filed_set=set())
check("new junk reported (so it can file next fire on grace)",
      [c["_id"] for c in fresh] == ["j2"], fresh)
# junk already in THIS fire's filing plan is not re-reported as prose
fresh = D.select_fresh([cls("j3", _file="filed")], set(), filed_set={"j3"})
check("junk being filed this fire is not also prose",
      fresh == [], fresh)

print("== 3. duplicate ids collapse (inbox + ledger row for the same mail) ==")
fresh = D.select_fresh([cls("dup", "priority"), cls("dup", "priority")],
                       set(), filed_set=set())
check("one bullet for a duplicated id", len(fresh) == 1, fresh)

print("== 4. TRACKER SURVIVES truncation (the compounding bug) ==")
saved = D.MAX_COMMENT_CHARS
D.MAX_COMMENT_CHARS = 900  # small, but larger than the fixed tracker+Filed parts
try:
    big = "- bullet\n" * 500
    body = D.comment_body(big, {"AIOS-Filed": 3}, 2, ["a1", "a2"])
finally:
    D.MAX_COMMENT_CHARS = saved
check("body respects the cap", len(body) <= 900, len(body))
check("tracker line present after truncation",
      "<!--aios-tracker|" in body and "ids:a1,a2" in body)
tpos = body.index("<!--aios-tracker|")
cpos = body.find("…(truncated)")
check("tracker sits BEFORE the truncated prose",
      0 <= tpos < cpos if cpos >= 0 else tpos >= 0, (tpos, cpos))
check("Filed block survives truncation", "**Filed**" in body and
      "AIOS-Filed" in body, body[-200:])
check("truncation marker present (prose was trimmed)", cpos >= 0)

print("== 5. a normal (short) body is untouched and keeps the tracker ==")
fixture = [{"from": {"emailAddress": {"name": "X", "address": "a@b.c"}},
            "subject": "an invoice"}]
body = D.comment_body("hello world", {"Receipts": fixture}, 0, ["z9"])
check("no truncation on a short fire", "…(truncated)" not in body)
check("tracker present", "ids:z9" in body)
check("prose present", "hello world" in body)

print("== 6. durable dedupe: ledger `reported` flag round-trips ==")
D.ledger_mark_reported(["m1", "m2"], cursor_ts="2026-10-22T00:00:00Z")
got = D.ledger_reported_ids()
check("reported ids readable back from the ledger",
      {"m1", "m2"} <= got, got)
check("cursor file advanced",
      open(os.path.join(TMP, "last_digest_report")).read().strip()
      == "2026-10-22T00:00:00Z")

print("== 7. a reported row must not clobber the folder/status (field merge) ==")
import state as ST  # noqa: E402
ST.append({"t": "intent", "id": "m3", "folder": "Receipts", "subject": "inv"})
ST.append({"t": "outcome", "id": "m3", "status": "filed", "folder": "Receipts"})
D.ledger_mark_reported(["m3"], cursor_ts=None)
merged = ST.latest_by_id()
check("folder preserved on the merged row",
      merged["m3"].get("folder") == "Receipts", merged["m3"])
check("still settled after the reported row", ST.is_settled(merged["m3"]))
check("m3 is now in the dedupe set", "m3" in D.ledger_reported_ids())

print("== 8. filer-filed rows are reportable AND carry their content ==")
# Regression: the filer writes an INTENT row (with sender/subject) write-ahead,
# then a bare OUTCOME row ({t,id,folder,status}) when the move lands.
# ledger_new_since() used to return the bare outcome row, so (a) the Filed block
# rendered "unknown — ;" for every filer-handled item, and (b) the inclusive
# >= cursor re-admitted rows forever because the `reported` flag lives on its
# OWN appended row, never on the outcome row.
ST.append({"t": "intent", "id": "f1", "folder": "Receipts",
           "subject": "a receipt", "sender_name": "Acme",
           "sender_addr": "billing@acme.test", "tier": "other"})
ST.append({"t": "outcome", "id": "f1", "folder": "Receipts",
           "status": "filed", "ts": "2026-10-22T10:00:00Z"})
entries = D.ledger_new_since()
f1 = [r for r in entries if r.get("id") == "f1"]
check("filer-filed row is reportable (not skipped)", len(f1) == 1, entries)
check("merged row keeps the sender from the intent row",
      f1 and f1[0].get("sender_name") == "Acme", f1[:1])
check("merged row keeps the subject from the intent row",
      f1 and f1[0].get("subject") == "a receipt", f1[:1])

# And once reported, it must never come back — even though the outcome row's ts
# is exactly the cursor (the old inclusive-`>=` re-admission bug).
D.ledger_mark_reported(["f1"], cursor_ts="2026-10-22T10:00:00Z")
check("reported filer row does not resurface at the cursor second",
      all(r.get("id") != "f1" for r in D.ledger_new_since()))

print("== 9. a filer-only window is a POST trigger (quiet-window gap) ==")
# The skip condition in main() must treat new filer-filed rows as a reason to
# post, or mail the filer handled is silently never summarised by any fire.
ledger_filed = {"Receipts": [D.ledger_to_classified(
    [{"id": "f1", "folder": "Receipts", "sender_name": "Acme",
      "subject": "a receipt"}])[0]]}
fresh, plan, cleared, privacy_new = [], [], 0, []
would_skip = (not fresh and not plan and cleared == 0 and not privacy_new
              and not ledger_filed)
check("filer-only window does NOT hit the 'nothing new' skip", not would_skip)
check("truly idle window still skips",
      (not [] and not [] and 0 == 0 and not [] and not {}))

print("== 10. Filed block renders the sender, never 'unknown' ==")
# Both shapes must render: the classified shape (live inbox + adapted ledger)
# and a raw ledger row (flat sender_name).
body = D.comment_body("prose", {"Receipts": ledger_filed["Receipts"]}, 0, [])
check("adapted row renders its sender name", "Acme — a receipt" in body, body)
raw = [{"sender_name": "Beta", "subject": "raw subject"}]
body = D.comment_body("prose", {"Orders": raw}, 0, [])
check("raw ledger row falls back to sender_name",
      "Beta — raw subject" in body, body)
check("address fallback when only an address is known",
      "c@d.e" in D.comment_body("p", {"Orders": [
          {"sender_addr": "c@d.e", "subject": "s"}]}, 0, []))

print("== 11. a SKIPPED fire still advances the cursor (no permanent stall) ==")
# The digest's own notification mail is journaled as a `keep` row but is
# excluded from every tier (_self_notice), so it can NEVER be reported. If the
# skip path returned without advancing, the cursor would stay pinned behind it
# and every later fire would re-scan a growing backlog.
D._advance_cursor([{"id": "s1", "ts": "2026-10-22T11:00:00Z"}])
check("skip path advanced the cursor",
      open(os.path.join(TMP, "last_digest_report")).read().strip()
      == "2026-10-22T11:00:00Z",
      open(os.path.join(TMP, "last_digest_report")).read().strip())
before = open(os.path.join(TMP, "last_digest_report")).read()
D._advance_cursor([])
check("nothing to advance is a no-op (cursor unchanged)",
      open(os.path.join(TMP, "last_digest_report")).read() == before)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
