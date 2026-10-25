#!/usr/bin/env python3
"""Digest-schedule reconciler (roadmap inbox-console R1).

The digest cadence used to be a constant in cronjob.yaml. R1 moves it into the
versioned config store (`digest_schedule` + `digest_tz`), which means the
CronJob must be kept in sync with config. This module is that sync — and
nothing else.

DESIGN (from the R1 intake, 2026-10-21):
  - It runs on the FILER's existing 5-minute heartbeat (called from filer.py),
    so there is no new timer and no new pod.
  - Each run: load config -> GET the digest CronJob -> compare schedule+tz ->
    PATCH only on a diff. Idempotent; a matching schedule is silent.
  - FAIL-SAFE: any error is caught and logged; the digest simply keeps its
    previous schedule. A schedule edit can pause the DIGEST, never filing or
    retention — those are the filer's job and this module does not touch it.
  - RBAC is scoped to exactly patch/get on the single CronJob named
    `aios-mail-digest` (see k8s/schedule-rbac.yaml); the code refuses to
    target any other object.

WHY THE PATCH GOES THROUGH A VALIDATED CONFIG: engine.config.load() validates
the schedule (croniter + timezone + the 1/hour floor) BEFORE this runs, and an
invalid config falls back to env loudly rather than applying. So a bad edit is
refused at save time in the console, and even a hand-edited bad file cannot
reach the CronJob — load() hands back the env-derived (valid) schedule instead.

Pure vs I/O split: `plan_patch()` is pure and unit-tested; `reconcile()` does
the HTTP. That keeps the "what would change" answer testable offline.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(HERE)
for _p in (HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine.config as ECFG          # noqa: E402
from engine import schedule as SCHED  # noqa: E402

# The ONE CronJob this reconciler is allowed to touch. Changing this constant
# without changing the RBAC resourceNames would just produce 403s (defence in
# depth: the Role also names it).
TARGET_CRONJOB = "aios-mail-digest"
DEFAULT_NS = os.environ.get("AIOS_MAIL_NS", "ai")


def desired_from_config(cfg):
    """(schedule, tz) the CronJob should have, from the effective config."""
    return str(cfg.digest_schedule).strip(), str(cfg.digest_tz).strip()


def plan_patch(current_schedule, current_tz, desired_schedule, desired_tz):
    """Pure: the merge-patch body that brings `current` to `desired`, or None
    when already in sync. Also returns the fields that differ (for logging).

    Normalises whitespace on the cron expression: "0 5,12,17 * * *" and
    "0  5,12,17 * * *" are the same schedule and must not cause a PATCH loop.
    """
    def _norm_expr(s):
        return " ".join(str(s or "").split())

    same = (_norm_expr(current_schedule) == _norm_expr(desired_schedule)
            and str(current_tz or "").strip() == str(desired_tz).strip())
    if same:
        return None
    return {"spec": {"schedule": desired_schedule, "timeZone": desired_tz}}


# --------------------------------------------------------------------------
# k8s I/O
# --------------------------------------------------------------------------
def _api_base():
    # svc/kubernetes (10.43.0.1:443) refuses from worker pods on this k3s, so
    # the real endpoint is injected (same reason the console has
    # CONSOLE_K8S_URL). Fall back to the standard in-cluster env var.
    base = os.environ.get("AIOS_K8S_URL") or os.environ.get("CONSOLE_K8S_URL")
    if base:
        return base.rstrip("/")
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    if host:
        return f"https://{host}:443"
    return None


def _token():
    with open("/var/run/secrets/kubernetes.io/serviceaccount/token") as fh:
        return fh.read().strip()


def _ssl_ctx():
    ca = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    return ssl.create_default_context(cafile=ca) if os.path.exists(ca) \
        else ssl.create_default_context()


def _cronjob_url(base, ns):
    return f"{base}/apis/batch/v1/namespaces/{ns}/cronjobs/{TARGET_CRONJOB}"


def get_cronjob(base, ns, tok, ctx):
    req = urllib.request.Request(
        _cronjob_url(base, ns),
        headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        return json.loads(resp.read())


def patch_cronjob(base, ns, tok, ctx, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        _cronjob_url(base, ns), data=data, method="PATCH",
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/merge-patch+json"})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        return json.loads(resp.read())


def reconcile(cfg, log=print, ns=DEFAULT_NS, apply=True):
    """Bring the digest CronJob in line with `cfg`. Never raises: returns a
    result dict with `action` in {"noop", "patched", "error", "skipped"}.

    apply=False computes the plan without patching (manual --dry-run)."""
    desired_s, desired_tz = desired_from_config(cfg)
    # Guard: never apply an invalid schedule even if config.load somehow let
    # one through (belt and braces — validate() already gates this).
    try:
        SCHED.validate_schedule(desired_s, desired_tz)
    except SCHED.ScheduleUnavailable as exc:
        log(f"schedule reconcile: cannot validate ({str(exc)[:100]}) — leaving "
            f"CronJob untouched")
        return {"action": "skipped", "reason": "validation unavailable"}
    except ValueError as exc:
        log(f"schedule reconcile: config schedule invalid ({str(exc)[:120]}) "
            f"— leaving CronJob untouched")
        return {"action": "skipped", "reason": "invalid schedule"}

    base = _api_base()
    if not base:
        log("schedule reconcile: no API endpoint (AIOS_K8S_URL unset, not "
            "in-cluster) — skipping")
        return {"action": "skipped", "reason": "no api endpoint"}
    try:
        tok, ctx = _token(), _ssl_ctx()
        cj = get_cronjob(base, ns, tok, ctx)
    except Exception as exc:
        log(f"schedule reconcile: cannot read CronJob {TARGET_CRONJOB} "
            f"({str(exc)[:120]}) — digest keeps its current schedule")
        return {"action": "error", "reason": str(exc)[:160]}

    cur = cj.get("spec", {})
    cur_s, cur_tz = cur.get("schedule"), cur.get("timeZone")
    body = plan_patch(cur_s, cur_tz, desired_s, desired_tz)
    if body is None:
        log(f"schedule reconcile: {TARGET_CRONJOB} in sync "
            f"({cur_s} {cur_tz}) — no change")
        return {"action": "noop", "schedule": cur_s, "tz": cur_tz}

    if not apply:
        log(f"schedule reconcile: DRY-RUN would set {cur_s!r}/{cur_tz!r} -> "
            f"{desired_s!r}/{desired_tz!r}")
        return {"action": "would-patch", "from": [cur_s, cur_tz],
                "to": [desired_s, desired_tz]}
    try:
        patch_cronjob(base, ns, tok, ctx, body)
        log(f"schedule reconcile: {TARGET_CRONJOB} {cur_s!r}/{cur_tz!r} -> "
            f"{desired_s!r}/{desired_tz!r}")
        return {"action": "patched", "from": [cur_s, cur_tz],
                "to": [desired_s, desired_tz]}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:160]
        except Exception:
            pass
        log(f"schedule reconcile: PATCH failed ({exc.code}) {detail} — digest "
            f"keeps its current schedule; will retry next heartbeat")
        return {"action": "error", "code": exc.code, "reason": detail}
    except Exception as exc:
        log(f"schedule reconcile: PATCH failed ({str(exc)[:120]}) — digest "
            f"keeps its current schedule; will retry next heartbeat")
        return {"action": "error", "reason": str(exc)[:160]}


def set_schedule(schedule=None, tz=None, actor="cli", state_dir=None, log=print):
    """Persist a new digest cadence to the config store (the edit path that
    the console's step-5 editing UI will call; usable NOW from the CLI).

    Mirrors the console's safe-save contract: load the CURRENT effective
    config, override only the named keys, then save the COMPLETE snapshot —
    never from_dict(partial), which would silently reset every other key to
    its default. Validation (cron/tz/1-per-hour) runs inside save()."""
    state_dir = state_dir or ECFG.default_dir()
    cfg = ECFG.load(state_dir, log=log)
    merged = ECFG.asdict(cfg)
    merged.pop("source", None)
    if schedule is not None:
        merged["digest_schedule"] = schedule
    if tz is not None:
        merged["digest_tz"] = tz
    new = ECFG.from_dict(merged, source="file")   # validates (may raise)
    ver = ECFG.save(new, actor=actor, state_dir=state_dir)
    log(f"schedule saved: {new.digest_schedule} {new.digest_tz} ({ver})")
    return new


def main(argv=None):
    """Standalone entrypoint for manual runs / debugging:
        python3 schedule_reconciler.py                     # reconcile now
        python3 schedule_reconciler.py --dry-run           # show the plan only
        python3 schedule_reconciler.py --set '0 7,18 * * *' [--tz America/Denver]
        python3 schedule_reconciler.py --show              # config + next fires
    """
    argv = sys.argv[1:] if argv is None else list(argv)
    state_dir = ECFG.default_dir()

    if "--show" in argv:
        cfg = ECFG.load(state_dir)
        print(json.dumps(SCHED.describe(cfg.digest_schedule, cfg.digest_tz,
                                        n=5), indent=2))
        return 0

    if "--set" in argv:
        i = argv.index("--set")
        sched = argv[i + 1] if i + 1 < len(argv) else None
        tz = None
        if "--tz" in argv:
            tz = argv[argv.index("--tz") + 1]
        try:
            set_schedule(sched, tz, actor=os.environ.get("USER", "cli"),
                         state_dir=state_dir)
        except ValueError as exc:
            print(f"REFUSED: {exc}")
            return 2
        cfg = ECFG.load(state_dir)
        print(json.dumps(SCHED.describe(cfg.digest_schedule, cfg.digest_tz,
                                        n=5), indent=2))
        return 0

    apply = "--dry-run" not in argv
    cfg = ECFG.load(state_dir)
    res = reconcile(cfg, apply=apply)
    print(json.dumps(res, indent=2))
    # error is the only non-zero case: skipped/noop/patched are all benign
    return 0 if res["action"] != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
