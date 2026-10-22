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

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
