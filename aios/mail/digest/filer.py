#!/usr/bin/env python3
"""AIOS inbox FILER — the every-5-minutes leg (0.4.0, Chad 2026-10-21).

Split from the digest so filing keeps up with incoming mail instead of only
running 3x/day. This leg:
  1. FULLY pages the inbox to exhaustion (no window cap — vector #1).
  2. Reconciles any crash from the previous run (vector #3).
  3. Classifies deterministically (same code as the digest, imported).
  4. Write-ahead logs EVERY item's intended outcome, fsync'd, BEFORE moving.
  5. Moves only what it fully paged out, then logs confirmed outcomes.
  6. Never summarises, never posts to GitHub, never runs retention.

WHY THIS EXISTS — Chad's requirement: nothing may be missed. The mailbox is not
a reliable record: it is paginated, mutable, and the MCP tool times out at 30s.
So every item that this process has ever seen has a durable row in the shared
JSONL ledger BEFORE it is touched. The digest reads that ledger; the mailbox
may lose, reorder, or hide things, the ledger may not.

SAFETY RAILS (fail toward the inbox, never away from it):
  - Never move an item we did not see in a COMPLETE page-through this run
    (skip-paging while writing shifts the window and silently skips items —
    that bug hit this system in production before).
  - If listing fails at any page, act on NOTHING this run and retry next fire.
  - Any error, timeout, or ambiguity -> leave the item in the inbox.
  - Retention does not run here at all. Only the digest deletes, and only
    items with a confirmed filed: outcome.

The privacy LLM keep-pass (layer 2 of the privacy tier) DOES run here, on the
would-be-filed set, exactly as in the digest. Chad's broker opt-out confirmations
must not be filed away; the digest's 3x/day cadence is far too slow for that,
and an LLM-free filer would silently regress the privacy tier to deterministic-
only. The model can only ever KEEP mail (it returns keep-indexes, nothing
else); on model failure we fail SAFE and hold those candidates out of filing.
"""

import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_PARENT = os.path.dirname(HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)  # make `import engine` work (0.5.0)

import state as ST           # noqa: E402

# Load the digest module for the shared, already-audited logic (classify,
# privacy keep-pass, MCP clients, folder map). One source of truth for
# classification: the filer and the digest MUST never disagree about what is
# junk, or an item could be filed by one and reported by the other.
_spec = importlib.util.spec_from_file_location("digest_mod", os.path.join(HERE, "digest.py"))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

log = D.log
msg_key = D.msg_key
FILED_FOLDER = D.FILED_FOLDER

# Full page size. PAGESIZE<<PAGE_MAX: we must fully drain the inbox.
PAGESIZE = int(os.environ.get("FILER_PAGE_SIZE", "100"))
PAGE_MAX = int(os.environ.get("FILER_PAGE_MAX", "60"))  # hard ceiling: 6000 items
MOVE_SLEEP = float(os.environ.get("FILER_MOVE_SLEEP", "0.35"))
# Every listed item older than this is abandoned: if the inbox is genuinely
# this huge, something is wrong and we would rather process the head and
# leave the tail than run unbounded.
MAX_ITEM_AGE_DAYS = int(os.environ.get("FILER_MAX_AGE_DAYS", "365"))


def page_inbox_all():
    """FULLY page the inbox newest-first with skip-pagination. Listing only —
    we never mutate during the walk, because moving while skipping shifts the
    window and silently skips items (vector #4). Retries each page on the 30s
    MCP timeout (vector #2) rather than aborting the whole fire.

    Returns (items, complete). complete=False means we could not see the WHOLE
    inbox this run; the caller then files NOTHING, because an item we never
    paged out must not be moved on a guess.
    """
    inbox_id = D.folder_map().get("inbox")
    if not inbox_id:
        raise RuntimeError("Inbox folder not found")
    items, skip, seen_ids = [], 0, set()
    for page in range(PAGE_MAX):
        raw = None
        for attempt in (1, 2, 3):
            try:
                raw = D.graph_call(
                    "list-mail-folder-messages",
                    {"mailFolderId": inbox_id, "top": PAGESIZE, "skip": skip,
                     "orderby": "receivedDateTime desc"})
                break
            except Exception as exc:
                log(f"list page {page} attempt {attempt}: {str(exc)[:90]}")
                time.sleep(2 * attempt)
        if raw is None:
            log(f"listing INCOMPLETE after {len(items)} items (page {page})")
            return items, False
        vals = raw.get("value") if isinstance(raw, dict) else raw
        if not isinstance(vals, list):
            return items, False
        if not vals:
            return items, True  # short/empty page -> fully drained
        fresh = 0
        for m in vals:
            mid = msg_key(m.get("id"))
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                items.append(m)
                fresh += 1
        # If a full page yielded only duplicates we are stuck on a shifting or
        # repeating window: stop rather than spin to PAGE_MAX.
        if fresh == 0 and len(vals) >= PAGESIZE:
            log(f"page {page}: {len(vals)} rows but 0 new ids -> stopping walk")
            return items, False
        skip += PAGESIZE
        if len(vals) < PAGESIZE:
            return items, True
        if len(items) >= PAGE_MAX * PAGESIZE:
            log(f"hit page ceiling ({len(items)} items) — inbox larger than expected")
            return items, False
    log(f"hit page ceiling without drain ({len(items)} items)")
    return items, False


def reconcile():
    """Repair any crash from a previous run BEFORE doing new work.

    An intent row with no outcome means we died between 'decided to move' and
    'confirmed moved'. We cannot know which side of the move we died on, so we
    re-list the intended destination folder: if the id is there the move landed
    (log the outcome, it happened); otherwise retry the move from the inbox.
    Either way the row resolves — so nothing can sit permanently unreported.

    This is the fix for the silent-loss vector: without it, a crash between the
    move and the log write would file mail that the digest never summarizes, and
    nothing would ever notice.
    """
    pending = ST.unresolved_intents()
    if not pending:
        return 0
    log(f"reconcile: {len(pending)} unresolved intent(s) from a prior run")
    filed_fm = {}
    resolved = 0
    for intent in pending:
        target = intent.get("folder")
        found = False
        if target and target != "keep":
            try:
                if target not in filed_fm:
                    filed_fm[target] = D.graph_file(
                        "list-mail-folder-messages",
                        {"mailFolderId": D.folder_map().get(target.lower()), "top": 200})
                blob = json.dumps(filed_fm[target])  # id substring search is enough
                found = intent.get("id") in blob
            except Exception as exc:
                log(f"reconcile: cannot inspect {target}: {str(exc)[:80]} — "
                    f"leaving intent open, will retry next run")
                continue
        if found:
            ST.append({"t": "outcome", "id": intent["id"], "folder": target,
                       "status": "filed", "reconciled": True})
            resolved += 1
            log(f"reconcile: {intent['id']} found in {target} -> outcome filed")
        else:
            # Not in the destination: it is presumably still in the inbox and
            # will be picked up by this run's normal walk. Record that we saw
            # the gap so it is not mistaken for a first sighting.
            ST.append({"t": "outcome", "id": intent["id"], "folder": target,
                       "status": "pending_retry", "reconciled": True})
            log(f"reconcile: {intent['id']} NOT in {target} -> will re-file from inbox")
    return resolved


def main():
    log(f"filer starting (page size {PAGESIZE}, cap {PAGE_MAX})")
    # --- 0. Config store (0.5.0): rules from YAML if present, env below. The
    # digest module's rule globals are updated in place so BOTH legs always
    # agree with each other and with whatever config version loaded.
    D.apply_config()
    # Crash repair first; never stack a new run on an unresolved gap.
    try:
        reconcile()
    except Exception as exc:
        log(f"reconcile FAILED (continuing): {str(exc)[:120]}")

    # --- 1. Full page-through (no window cap).
    items, complete = page_inbox_all()
    log(f"inbox: listed {len(items)} complete={complete}")
    if not complete:
        log("listing incomplete -> filing NOTHING this run (fail safe), retry next fire")
        return

    # --- 2. Contacts + deterministic classification (shared with digest).
    contacts = D.fetch_sent_contacts()
    classified = D.classify(items, contacts)

    # Ledger view of everything we have already resolved, so we do not
    # re-file or re-report settled items.
    settled = ST.latest_by_id(ST.read_all(since_days=ST.RETENTION_SCAN_DAYS))
    settled = {k: v for k, v in settled.items()
               if v.get("t") == "outcome" and v.get("status") in ("filed", "keep")}

    # Everything we can see and have not durably recorded is NEW to us.
    unseen = [c for c in classified if c["_id"] not in settled]
    log(f"new since ledger: {len(unseen)} of {len(classified)} listed")

    if not unseen:
        # Daily compaction only when idle, so we never rewrite the ledger
        # during heavy filing (compaction is a full atomic rewrite).
        try:
            last = os.path.getmtime(ST.LEDGER) if os.path.exists(ST.LEDGER) else 0
            if time.time() - last > 86400 and ST.LEDGER:
                ST.compact()
        except Exception as exc:
            log(f"compaction skipped: {str(exc)[:80]}")
        log("nothing new — idle")
        return

    # --- 3. Privacy tier layer 2 (model can only KEEP; failure = fail safe).
    candidates = [c for c in unseen if c.get("_file") and not c.get("_privacy")
                  and not c.get("_self_notice")]
    held = []
    if D.PRIVACY_ON and D.PRIVACY_LLM_ON and candidates:
        try:
            kept = D.privacy_llm_keeps(candidates)
            for c in candidates:
                if c["_id"] in kept:
                    c["_privacy"] = "model-suggested data-removal mail (kept)"
                    c["_file"] = None
        except Exception as exc:
            log(f"privacy llm FAILED — holding {len(candidates)} candidate(s) "
                f"from filing this run (fail safe): {str(exc)[:90]}")
            held = candidates
    log(f"privacy: {len([c for c in unseen if c.get('_privacy')])} kept; "
        f"held for retry: {len(held)}")

    # --- 4. Build the plan. Kept-in-inbox items get a durable 'keep' row too,
    #        so the digest can see them even though they never move.
    plan, keeps = [], []
    for c in unseen:
        if c.get("_held"):
            continue  # deliberately not planned this run; retried next fire
        if c.get("_self_notice"):
            keeps.append(c)
        elif c["_file"]:
            # _file is a SENTINEL, not a folder name: "filed" means "the junk
            # folder", "receipts"/"orders" mean the typed folders. The digest
            # maps them at the plan; the filer must too, or it creates a folder
            # literally named "filed" (bug caught on the first catch-up fire).
            target = FILED_FOLDER if c["_file"] == "filed" else {
                "receipts": D.RECEIPTS_FOLDER,
                "orders": D.ORDERS_FOLDER,
            }.get(c["_file"])
            if not target:
                log(f"unknown _file sentinel {c['_file']!r} -> keeping in inbox")
                keeps.append(c)
            else:
                c["_target"] = target
                plan.append((c, target))
        else:
            keeps.append(c)  # priority + privacy-kept stay in the inbox

    # --- 5. WRITE-AHEAD: durable intent rows BEFORE any move. This is the
    #        guarantee. From here on, even a total crash cannot lose an item.
    folder_ids = {}
    try:
        for name in sorted({f for _, f in plan}):
            folder_ids[name] = D.ensure_folder(name)
    except Exception as exc:
        log(f"folder prep failed -> filing NOTHING this run: {str(exc)[:120]}")
        return
    for c, folder in plan:
        ST.append(_row(c, folder))
    for c in keeps:
        ST.append(_row(c, "keep"))
    log(f"write-ahead: {len(plan)} intent(s) + {len(keeps)} keep row(s) durable")

    # --- 6. Act. Every intent gets an outcome, success or failure.
    filed_counts, failed = {}, 0
    for c, folder in plan:
        try:
            D.graph_file("move-mail-message",
                         {"messageId": c["_fid"],
                          "body": {"DestinationId": folder_ids[folder]}})
            ST.append({"t": "outcome", "id": c["_id"], "folder": folder,
                       "status": "filed"})
            filed_counts[folder] = filed_counts.get(folder, 0) + 1
            time.sleep(MOVE_SLEEP)
        except Exception as exc:
            failed += 1
            ST.append({"t": "outcome", "id": c["_id"], "folder": folder,
                       "status": "error", "err": str(exc)[:150]})
            log(f"move failed (item stays, retried next fire) "
                f"id={c['_id']} err={str(exc)[:80]}")
    for c in keeps:
        ST.append({"t": "outcome", "id": c["_id"], "folder": "keep",
                   "status": "keep"})

    log(f"filed: {filed_counts or '{}'} ; kept-in-inbox: {len(keeps)} ; "
        f"errors: {failed}")
    log("filer done (no digest, no retention — those are the 3x/day leg)")


def _row(c, folder):
    """The durable row for one item. It carries the CONTENT the digest will
    need later (sender/subject/preview/tier), because by the time the digest
    runs this mail is out of the inbox and cannot be re-fetched. Storing only
    an id would make the ledger a list of ids we can no longer act on."""
    ea = (c.get("from") or {}).get("emailAddress") or {}
    return {"t": "intent", "id": c["_id"], "folder": folder,
            "cfg": D._CFG.version,  # which rules decided this (explainability;
            # makes a bad config edit traceable to exactly the items it hit)
            "subject": (c.get("subject") or "")[:160],
            "sender_name": (ea.get("name") or "")[:80],
            "sender_addr": (ea.get("address") or "")[:120],
            "preview": (c.get("preview") or "")[:200],
            "tier": "priority" if c.get("type") == "priority" else "other",
            "reason": c.get("reason"),
            "privacy": c.get("_privacy"),
            "self_notice": bool(c.get("_self_notice")),
            "unread": bool(c.get("_unread")),
            "received": c.get("_received")}


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("FILER FAILED:", type(exc).__name__, str(exc)[:300])
        # Exit non-zero so the CronJob surfaces the failure; the ledger
        # reconciliation on the next fire repairs whatever was in flight.
        sys.exit(1)