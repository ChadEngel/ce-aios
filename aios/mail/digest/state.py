"""Durable, write-ahead record of what AIOS has SEEN and DONE (0.4.0).

Chad's hard requirement (2026-10-21): nothing may be missed, ever. The
mailbox cannot be the record of what was seen: it is mutable, paginated,
and the MCP tool times out at 30s. So the invariant is

    NEVER trust the mailbox as the record. Write intent to durable storage
    BEFORE any mutation. Then the mailbox can lie; the log cannot.

Format: append-only JSONL, one row per state transition, fsync'd. JSONL over
pickle because it is append-only (pickle rewrites are not crash-safe), greppable
in logs/ops, and readable by `jq` when something goes wrong at 3am.

Two row kinds:

  {"t":"intent", ...}  written BEFORE a move. If we die here, the next run
                       finds the intent with no outcome and reconciles it.
  {"t":"outcome", ...} written AFTER the move succeeded (or failed). Every
                       intent MUST reach an outcome row; retention is only
                       allowed to delete items that have one.

Reconciliation (the crash-gap fix): an intent with no matching outcome means
we crashed between "decided to move" and "confirmed moved". The filer cannot
know which side of the move we died on, so it RE-LISTS the destination folder;
finding the id there means the move landed, otherwise it retries from the
inbox. Either way the row is resolved, so no item can sit permanently
unreported.

Safety rule encoded here (F5): retention only ever considers items that have a
confirmed `filed:` outcome. An item whose log we lost is never deleted.
"""

import json
import os
import tempfile
import time

STATE_DIR = os.environ.get("DIGEST_STATE_DIR", "/state")
LEDGER = os.path.join(STATE_DIR, "ledger.jsonl")
# How far back the ledger is scanned for reconciliation / resume. Retention
# is 10 days, so 30 days of history strictly dominates the delete window.
RETENTION_SCAN_DAYS = int(os.environ.get("DIGEST_LEDGER_DAYS", "30"))


def _ensure_dir():
    if not os.path.isdir(STATE_DIR):
        os.makedirs(STATE_DIR, exist_ok=True)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append(row):
    """Append one row and fsync it. fsync is the whole point: a row that is
    not on disk is a row we cannot rely on, and "cannot rely on" is how mail
    goes missing. Cheap enough at ~1 row per filed item."""
    _ensure_dir()
    row = dict(row)
    row.setdefault("ts", _now())
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def read_all(since_days=None):
    """Read the ledger. Tolerates a torn final line (power loss mid-write) by
    skipping unparseable rows rather than dying — a half-written last line is
    expected on a crash, and the reconciler exists precisely to repair that."""
    _ensure_dir()
    if not os.path.exists(LEDGER):
        return []
    cutoff = None
    if since_days is not None:
        cutoff = time.time() - since_days * 86400
    rows, torn = [], 0
    with open(LEDGER, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                torn += 1
                continue
            if cutoff is not None:
                try:
                    if time.mktime(time.strptime(row.get("ts", ""),
                                                 "%Y-%m-%dT%H:%M:%SZ")) < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
            rows.append(row)
    if torn:
        # Surfaced loudly: a torn line means we crashed mid-append, which is
        # exactly the condition reconciliation must repair.
        print(f"[state] WARNING: {torn} torn ledger row(s) skipped", flush=True)
    return rows


def latest_by_id(rows=None, ids=None):
    """Collapse intents+outcomes+reported into one merged row per message id.

    Field-merge, NOT last-row-wins, for the same reason as compact(): a
    `reported` row carries only {t,id,status}, so last-row-wins would erase the
    folder/subject from the intent and make the row look like it was never
    filed. The filer uses this to decide what is already settled, so a bug here
    means re-filing settled mail on every single fire.
    """
    out = {}
    for row in (rows if rows is not None else read_all()):
        mid = row.get("id")
        if not mid:
            continue
        if ids is not None and mid not in ids:
            continue
        if mid in out:
            out[mid].update({k: v for k, v in row.items() if k != "ts"})
        else:
            out[mid] = dict(row)
    return out


def is_settled(merged_row):
    """True when an item has a CONFIRMED terminal outcome (filed or keep).

    This is the gate retention (F5) and the filer's 'already done' check both
    rely on. An intent-only or error row is NOT settled: the item either
    crashed mid-move or failed to move, and must stay visible for retry. Never
    let an unconfirmed row count as done — that is how mail disappears.
    """
    return merged_row.get("status") in ("filed", "keep")


def unresolved_intents(rows=None):
    """Intents with no outcome = a run died mid-flight. These MUST be
    reconciled before any new filing, otherwise a crashed item could be
    filed twice or reported twice."""
    rows = rows if rows is not None else read_all()
    intents, done = {}, set()
    for row in rows:
        if row.get("t") == "intent":
            intents[row.get("id")] = row
        elif row.get("t") == "outcome":
            done.add(row.get("id"))
    return [i for mid, i in intents.items() if mid not in done]


def compact(min_days=None):
    """Compact the ledger to one row per id, oldest-first by first-seen.

    Called once a day from the filer. The ledger grows by ~1 row per filed
    item; JSONL is cheap, but unbounded growth on a shared PVC is not. After
    compaction the file is rewritten atomically (temp file + os.replace) so a
    crash mid-compaction cannot corrupt it.

    FIELD MERGE, not last-row-wins: the row kinds carry different facts. An
    `outcome` row only has {t,id,folder,status}; a `reported` row only has
    {t,id,status}. Taking the last row wholesale silently DROPS the reported
    flag, and the digest then re-reports the same mail on every fire forever.
    (The self-test caught exactly this.) So merge fields forward: later rows
    overwrite the keys they actually carry, and leave the rest intact.
    """
    min_days = RETENTION_SCAN_DAYS if min_days is None else min_days
    rows = read_all(since_days=min_days)
    first_ts, merged = {}, {}
    for row in rows:
        mid = row.get("id")
        if not mid:
            continue
        first_ts.setdefault(mid, row.get("ts"))
        if mid in merged:
            merged[mid].update({k: v for k, v in row.items() if k != "ts"})
            merged[mid]["ts"] = row.get("ts", merged[mid].get("ts"))
        else:
            merged[mid] = dict(row)
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".ledger-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for mid in sorted(merged, key=lambda m: first_ts.get(m) or ""):
                fh.write(json.dumps(merged[mid], ensure_ascii=False,
                                    sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, LEDGER)  # atomic
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print(f"[state] compacted {len(rows)} rows -> {len(merged)} ids", flush=True)
    return len(merged)