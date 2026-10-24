"""Schedule maths for the digest cadence (roadmap inbox-console R1).

Pure functions, no k8s, no config, no I/O — so the engine keeps its
"port + Config only" shape and every consumer (reconciler, console preview,
selftests) shares ONE definition of what a valid schedule is.

Why a module at all: the cadence used to be a manifest constant. R1 moves it
into the versioned config store, which means it can now be EDITED, which means
it can now be WRONG — a schedule that fires 60x/hour, or one with a timezone
that doesn't exist, or (worst) a config save that silently stops the digest
from ever running again. So validation is a first-class, tested artifact
rather than an incidental croniter call.

Guardrails (R1 intake, 2026-10-21):
  - the expression must parse and the timezone must resolve;
  - no more than one fire per hour — a mis-typed expression that turns a
    3x/day digest into an hourly hammer is refused at SAVE time, not
    discovered from a bill or a rate-limited mailbox;
  - the filer-heartbeat collision is reported as an ADVISORY, not a refusal
    (see _filer_minute_advisories) because the incumbent live schedule is
    itself on minute 0, and a guardrail that refuses the running config is a
    landmine, not a safety rail.

DST note: fires are computed against a tz-aware anchor so "5am" stays 5am
across a DST shift (that is the whole reason timeZone is stored alongside the
expression instead of relying on the node clock).
"""
from datetime import datetime, timedelta, timezone

try:
    from croniter import croniter
    AVAILABLE = True
except Exception:  # pragma: no cover - surfaced loudly by validate_schedule
    croniter = None
    AVAILABLE = False


def _require_available():
    if not AVAILABLE:
        raise ScheduleUnavailable(
            "croniter is not installed — cannot validate the digest schedule")


class ScheduleUnavailable(RuntimeError):
    """croniter missing. Distinct from ValueError so callers can decide: the
    digest does not CONSUME the schedule (only the reconciler does), so a
    missing validation dependency must never stop filing mail — validate()
    treats this as 'cannot check, skip' rather than 'invalid'."""

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - py<3.9 has no zoneinfo
    ZoneInfo = None


# The filer heartbeat cadence (filer-cronjob.yaml "*/5 * * * *"). Digest fires
# landing on one of these minutes share a start instant with a filer run.
FILER_HEARTBEAT_MINUTES = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)

_MAX_GAP_PROBE = 60  # fires sampled when checking the 1/hour floor


def resolve_tz(name):
    """Zone name -> tzinfo. Raises ValueError with a user-legible message."""
    if ZoneInfo is None:
        raise ValueError("zoneinfo unavailable on this interpreter")
    try:
        return ZoneInfo(str(name))
    except Exception:
        raise ValueError(f"unknown timezone: {name!r} (want e.g. America/Chicago)")


def validate_schedule(expr, tz):
    """Raise ValueError when (expr, tz) cannot be a safe digest cadence.

    Called from Config.validate(), so an invalid schedule can never be
    persisted by save() and can never be applied by load() — the failure
    happens while a human is still looking at the screen.
    """
    if not AVAILABLE:
        _require_available()
    expr = str(expr or "").strip()
    if not expr:
        raise ValueError("digest_schedule must not be empty")
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid cron expression: {expr!r}")
    resolve_tz(tz)  # raises on a bad zone

    fires = _next_anchored(expr, tz, _MAX_GAP_PROBE)
    if len(fires) >= 2:
        gaps = [(b - a).total_seconds() for a, b in zip(fires, fires[1:])]
        smallest = min(gaps)
        if smallest < 3600:
            raise ValueError(
                f"schedule fires more than once per hour (smallest gap "
                f"{int(smallest)}s) — refuse to turn the digest into a "
                f"rate-limited hammer")
    return True


def _next_anchored(expr, tz, n, now=None):
    """n upcoming fires as tz-aware datetimes, walked in the schedule's own
    wall-clock and then localized (DST-correct).

    Why not just hand croniter a tz-aware anchor: it steps in absolute time,
    so across a DST shift a "5am" daily fire drifts to 6am on the fall-back
    day (and 4am in spring). k8s CronJob timeZone does NOT have that bug, so a
    preview built on croniter's aware mode would show the user times the real
    CronJob never fires. Instead we iterate in NAIVE local wall-clock (where
    "0 5 * * *" is always 05:00) and localize each result.

    US transitions move 2am, so a 5am fire exists unambiguously; the generic
    guard below still skips a fire whose local time does not exist (spring
    forward over its hour) rather than emitting a shifted instant — matching
    how cron daemons treat the skipped hour.
    """
    zone = resolve_tz(tz)
    base_aware = (now or datetime.now(timezone.utc)).astimezone(zone)
    # naive wall-clock anchor: strip tzinfo, keep the local fields
    it = croniter(expr, base_aware.replace(tzinfo=None))
    out = []
    guard = 0
    while len(out) < n and guard < n * 10 + 400:
        guard += 1
        wall = it.get_next(datetime)          # naive, local wall-clock
        aware = wall.replace(tzinfo=zone)
        # A nonexistent local time (skipped by a forward shift) round-trips to
        # a different wall clock; skip it instead of reporting a shifted fire.
        if aware.astimezone(zone).replace(tzinfo=None) != wall:
            continue
        out.append(aware)
    return out


def next_fires(expr, tz, n=5, now=None):
    """Next n fire instants as ISO-8601 strings in the schedule's timezone."""
    try:
        return [d.isoformat(timespec="minutes")
                for d in _next_anchored(expr, tz, n, now=now)]
    except Exception as exc:
        return [f"<invalid: {str(exc)[:60]}>"]


def filer_minute_advisories(expr):
    """Advisory strings for fire minutes that coincide with the filer
    heartbeat. NOT a validation failure — see the module docstring."""
    try:
        it = croniter(expr, datetime.now(timezone.utc))
        minutes = set()
        for _ in range(24):
            minutes.add(it.get_next(datetime).minute)
    except Exception:
        return []
    hit = sorted(m for m in minutes if m in FILER_HEARTBEAT_MINUTES)
    if not hit:
        return []
    return [f"digest fires on minute(s) {hit} — same instant as a filer "
            f"heartbeat; harmless (separate CronJobs, shared ledger) but "
            f"offsetting by a minute avoids two readers starting together"]


def describe(expr, tz, n=5, now=None):
    """Everything a dashboard/preview needs in one call (never raises)."""
    try:
        _require_available()
        validate_schedule(expr, tz)
        return {"schedule": expr, "tz": tz, "valid": True,
                "next": next_fires(expr, tz, n, now=now),
                "advisories": filer_minute_advisories(expr)}
    except ScheduleUnavailable as exc:
        return {"schedule": expr, "tz": tz, "valid": None,
                "error": str(exc), "next": [], "advisories": []}
    except ValueError as exc:
        return {"schedule": expr, "tz": tz, "valid": False,
                "error": str(exc), "next": [], "advisories": []}
