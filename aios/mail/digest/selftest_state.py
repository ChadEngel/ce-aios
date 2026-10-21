#!/usr/bin/env python3
"""Offline self-test for the write-ahead ledger + reconciliation (0.4.0).

This is the safety-critical path: if reconciliation is wrong, the system either
loses mail or files it twice, and both are invisible from the mailbox. Run it
BEFORE deploying the filer:

    python3 selftest_state.py
"""
import importlib.util
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="aios-ledger-test-")
os.environ["DIGEST_STATE_DIR"] = TMP

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("st", os.path.join(HERE, "state.py"))
ST = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ST)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def reset():
    if os.path.exists(ST.LEDGER):
        os.unlink(ST.LEDGER)


print("== 1. append + read round-trip ==")
reset()
ST.append({"t": "intent", "id": "a1", "folder": "AIOS-Filed", "subject": "hi"})
ST.append({"t": "outcome", "id": "a1", "folder": "AIOS-Filed", "status": "filed"})
rows = ST.read_all()
check("2 rows written", len(rows) == 2, rows)
check("rows have ts", all("ts" in r for r in rows))

print("== 2. unresolved intents (the crash case) ==")
reset()
ST.append({"t": "intent", "id": "b1", "folder": "AIOS-Filed"})
ST.append({"t": "intent", "id": "b2", "folder": "Orders"})
ST.append({"t": "outcome", "id": "b1", "status": "filed"})
un = ST.unresolved_intents()
check("exactly the crashed intent is unresolved",
      [i["id"] for i in un] == ["b2"], [i["id"] for i in un])

print("== 3. torn final line is survivable ==")
reset()
ST.append({"t": "intent", "id": "c1", "folder": "AIOS-Filed"})
ST.append({"t": "outcome", "id": "c1", "status": "filed"})
with open(ST.LEDGER, "a") as fh:
    fh.write('{"t":"intent","id":"c2","fold')  # power-loss mid-append
rows = ST.read_all()
check("good rows survive a torn tail", len(rows) == 2, rows)
check("torn id not resurrected",
      [i["id"] for i in ST.unresolved_intents()] == [])

print("== 4. latest_by_id: outcome supersedes intent ==")
reset()
ST.append({"t": "intent", "id": "d1", "folder": "AIOS-Filed"})
ST.append({"t": "outcome", "id": "d1", "folder": "AIOS-Filed", "status": "filed"})
latest = ST.latest_by_id()
check("latest row for d1 is the outcome",
      latest["d1"]["status"] == "filed", latest)

print("== 5. compaction keeps the winning row per id ==")
reset()
for i in range(50):
    ST.append({"t": "intent", "id": f"e{i}", "folder": "AIOS-Filed"})
    ST.append({"t": "outcome", "id": f"e{i}", "status": "filed"})
before = len(ST.read_all())
ST.compact()
after = ST.read_all()
check("100 rows compact to 50", before == 100 and len(after) == 50, (before, len(after)))
check("all rows are settled outcomes",
      all(ST.is_settled(r) for r in after), after[:3])
check("compaction is idempotent", len(ST.read_all()) == 50)

print("== 6. reported flag survives compaction (no re-report loop) ==")
reset()
ST.append({"t": "intent", "id": "f1", "folder": "AIOS-Filed", "subject": "s"})
ST.append({"t": "outcome", "id": "f1", "folder": "AIOS-Filed", "status": "filed"})
ST.append({"t": "outcome", "id": "f1", "folder": "AIOS-Filed", "status": "filed",
           "reported": True})
ST.compact()
latest = ST.latest_by_id()
check("f1 still marked reported", latest["f1"].get("reported"), latest["f1"])
check("f1 row type stays 'outcome' (digest filters on t==outcome)",
      latest["f1"].get("t") == "outcome", latest["f1"])
check("f1 still knows its folder (not clobbered by the sparse reported row)",
      latest["f1"].get("folder") == "AIOS-Filed", latest["f1"])
check("f1 counts as settled", ST.is_settled(latest["f1"]), latest["f1"])

print("== 6b. an unconfirmed row is NOT settled (F5: never delete un-logged) ==")
reset()
ST.append({"t": "intent", "id": "f2", "folder": "AIOS-Filed"})
check("intent-only row is not settled", not ST.is_settled(ST.latest_by_id()["f2"]))
ST.append({"t": "outcome", "id": "f2", "status": "error", "err": "timeout"})
check("error row is not settled", not ST.is_settled(ST.latest_by_id()["f2"]))
ST.append({"t": "outcome", "id": "f2", "status": "filed"})
check("filed row is settled", ST.is_settled(ST.latest_by_id()["f2"]))

print("== 7. crash between intent and outcome is DETECTED ==")
reset()
ST.append({"t": "intent", "id": "g1", "folder": "AIOS-Filed", "subject": "important"})
# simulate: process died here. Nothing else.
check("g1 unresolved (would be reconciled)",
      [i["id"] for i in ST.unresolved_intents()] == ["g1"])

print("== 8. durability: file readable by an independent reader ==")
reset()
ST.append({"t": "intent", "id": "h1", "folder": "AIOS-Filed", "subject": "x"})
with open(ST.LEDGER) as fh:
    line = json.loads(fh.readline())
check("row is valid JSON on disk, greppable",
      line["id"] == "h1" and line["t"] == "intent", line)

print("== 9. rolling backup before compaction (incident 2026-10-21) ==")
reset()
for i in range(3):
    ST.append({"t": "intent", "id": f"b{i}", "folder": "keep"})
ST.append({"t": "outcome", "id": "b0", "status": "keep", "folder": "keep"})
with open(ST.LEDGER) as fh:
    before = fh.read()
n = ST.compact()
bk = os.path.join(ST.STATE_DIR, "ledger-prev.jsonl")
with open(bk) as fh:
    backed = fh.read()
check("backup captures pre-compaction ledger", backed == before
      and len(backed.strip().splitlines()) == 4)
check("compaction merged same ids", n == 3, n)
reset()
ST.compact()   # compact on an empty ledger must still succeed
check("empty-ledger compaction ok",
      not os.path.exists(ST.LEDGER) or
      open(ST.LEDGER).read().strip() == "")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)