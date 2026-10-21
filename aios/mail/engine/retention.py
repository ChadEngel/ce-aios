"""Retention — the bounded soft-delete clear, extracted from digest.py.

Behaviour identical to 0.4.x: deletes constrained to the destination folder's
own listing, age-filtered in the Graph query, soft delete only (Deleted
Items), circuit-breakers everywhere. The one behavioural door: clear is
disabled entirely when filed_days == 0 ("keep forever").

F5 LEDGER GATE: `confirmed` is the set of msg_keys the ledger CONFIRMS we
filed, computed by the app (it owns the ledger). None = gate unavailable ->
pre-0.4.0 folder-age-only behaviour. Never delete what we cannot prove we
filed: unreported AND deleted is the one unrecoverable failure.
"""


def clear_filed(g, cfg, confirmed, log, msg_key, now):
    """Returns (cleared_count, abort_reason|None). Bounded: 40 pages x 25."""
    if cfg.filed_days <= 0:
        log("retention: filed_days=0 (keep forever) — nothing to clear")
        return 0, None
    fm = g.list_folders()
    filed_id = fm.get(cfg.filed_folder.lower())
    if not filed_id:
        log(f"retention: {cfg.filed_folder} does not exist yet — nothing to clear")
        return 0, None
    from datetime import timedelta
    cutoff = (now() - timedelta(days=cfg.filed_days)).strftime(
        "%Y-%m-%dT%H:%M:00Z")
    n_total = 0
    abort = None
    for _ in range(40):  # cap 40x25 = up to 1000/fire so fires stay bounded
        try:
            items = g.list_folder(filed_id, 25,
                                  filter=f"receivedDateTime lt {cutoff}")
        except Exception as exc:
            abort = f"batch-list failed after {n_total} clears: {str(exc)[:90]}"
            break
        if not isinstance(items, list):
            abort = f"unexpected listing shape after {n_total} clears"
            break
        batch = [m for m in items if m.get("id")]
        if confirmed is not None:
            gated = [m for m in batch if msg_key(m.get("id")) in confirmed]
            if len(gated) != len(batch):
                log(f"retention: skipping {len(batch) - len(gated)} item(s) "
                    f"with no confirmed ledger outcome")
            batch = gated
        if not batch:
            break
        fails = 0
        for m in batch:
            try:
                g.delete(m["id"])
                n_total += 1
                fails = 0
            except Exception as exc:
                msg = str(exc)
                log(f"retention: delete failed (skipped this fire) "
                    f"err={msg[:90]}")
                fails += 1
                if fails >= 3:
                    abort = (f"3 consecutive delete failures after "
                             f"{n_total} clears: {msg[:90]}")
                    break
            import time
            time.sleep(1.0)  # stay gentle under any rate limiter
        if abort:
            break
    if abort:
        log(f"retention: ABORTED clear this fire — {abort}")
    log(f"retention: cleared {n_total} items older than {cfg.filed_days}d "
        f"from {cfg.filed_folder}")
    return n_total, abort