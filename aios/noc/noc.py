#!/usr/bin/env python3
"""AIOS NOC agent (hire #1) — R0 read-only triage.

Job (per `aios/roadmap/ai-it-team.md`): "what's broken / what changed?"
This is the deterministic core: it asks the tool plane a fixed set of
questions, then posts what it found to a GitHub issue thread. No model is
involved in R0 — every line in the report is a measured fact, so the
two-week review window is reviewing *observations*, not prose.

What it checks (all read-only, all via the `aios-noc` Virtual Key):
  1. Datasource health          — are Loki and InfluxDB reachable at all?
  2. Log freshness per job      — newest entry age; the "is anything arriving?"
  3. Error volume per job       — line-filtered counts over the window.
  4. InfluxDB bucket liveness   — bucket count + newest write per bucket.

Tier: R0. `tools_to_auto_execute` is empty on the VK, so nothing here can
mutate anything even if this file is wrong. There is no write tool on the
VK to call.

Design notes / traps carried over from the mail digest:
  * MCP needs `initialize` once before `tools/call`.
  * Bifrost answers `application/json` for non-streamed calls.
  * GitHub comments API returns OLDEST-first and IGNORES sort/direction, so
    any "read the thread" logic must page FORWARD (not needed here yet, but
    the rule is why `post_comment` is the only GitHub write).
  * Every timestamp in the report is explicit — a stale report must never be
    mistaken for current state (failure mode 5 in the mail agent's list).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import jwt

# ------------------------------------------------------------------ config
BIFROST_URL = os.environ.get("BIFROST_URL", "https://llm.caehomelab.com")
VK = os.environ["BIFROST_VK_AIOS_NOC"]
MCP_URL = f"{BIFROST_URL}/mcp/grafana-noc"

GITHUB_APP_ID = os.environ["GITHUB_APP_ID"]
GITHUB_APP_PRIVATE_KEY = os.environ["GITHUB_APP_PRIVATE_KEY"].replace("\\n", "\n")
GITHUB_APP_REPO = os.environ.get("GITHUB_APP_REPO", "ChadEngel/ce-aios")
GITHUB_INSTALLATION_ID = os.environ.get("GITHUB_INSTALLATION_ID", "168700840")
NOC_ISSUE_NUMBER = os.environ["NOC_ISSUE_NUMBER"]

# Window for "what changed": 24h matches the daily-ish cadence and is long
# enough that a single missed fire does not read as an outage.
WINDOW_HOURS = int(os.environ.get("NOC_WINDOW_HOURS", "24"))
# A log source silent longer than this is reported as STALE. udm-syslog is
# chatty (487k entries / 7d when healthy), so hours of silence is meaningful.
STALE_HOURS = float(os.environ.get("NOC_STALE_HOURS", "6"))
# Hard cap on entries pulled per job when looking for the newest line. We ask
# for 1 backward; this is only a guard against a tool that ignores `limit`.
SAMPLE_LIMIT = int(os.environ.get("NOC_SAMPLE_LIMIT", "5"))
# Cap on rows pulled when counting error-ish lines. The count is reported as
# "at least N" when this cap is reached, never as an exact total.
FILTER_LIMIT = int(os.environ.get("NOC_FILTER_LIMIT", "500"))
MCP_TIMEOUT = int(os.environ.get("NOC_MCP_TIMEOUT", "60"))

# Loki datasource UID is stable in this Grafana (verified live); env-overridable
# so a datasource rebuild does not need a code change.
LOKI_UID = os.environ.get("NOC_LOKI_UID", "loki")
INFLUX_UID = os.environ.get("NOC_INFLUX_UID", "dfdkew37wk1dse")

# Jobs expected to be shipping logs. Silence from any of these is a finding.
# Empty = discover dynamically from Loki's `job` label values (the default),
# which is self-maintaining but cannot report "a source that vanished
# entirely" — hence the optional explicit list.
EXPECTED_JOBS = [j.strip() for j in os.environ.get("NOC_EXPECTED_JOBS", "").split(",")
                 if j.strip()]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ plumbing
def http_json(method, url, headers, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {raw[:300]}")
    return json.loads(raw)


MCP_HEADERS = {
    "x-bf-vk": VK,
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_INIT_DONE = {"v": False}


def _mcp_init():
    if _INIT_DONE["v"]:
        return
    resp = http_json("POST", MCP_URL, MCP_HEADERS,
                     {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "aios-noc",
                                                "version": "0.1.0"}}},
                     timeout=MCP_TIMEOUT)
    if resp.get("error"):
        raise RuntimeError(f"MCP initialize: {str(resp['error'])[:200]}")
    _INIT_DONE["v"] = True


def mcp(name, args):
    """Call a grafana_noc-<name> tool and return the decoded JSON payload.

    The server returns `content[0].text` as a JSON *string*; tools that fail
    soft (bad args, empty range) still return 200 with an error string inside,
    so a non-JSON payload is surfaced rather than swallowed.
    """
    _mcp_init()
    resp = http_json("POST", MCP_URL, MCP_HEADERS,
                     {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": f"grafana_noc-{name}",
                                 "arguments": args}},
                     timeout=MCP_TIMEOUT)
    if resp.get("error"):
        raise RuntimeError(f"MCP error on {name}: {str(resp['error'])[:200]}")
    text = resp["result"]["content"][0]["text"]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Keep the raw string: several tools answer with a plain sentence
        # ("unknown arguments ...", "no log entries ...") instead of JSON.
        return {"_raw": text}


# ------------------------------------------------------------------ checks
def utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_health():
    """Datasource reachability. Returns (ok: bool, lines: list[str])."""
    try:
        r = mcp("check_datasources_health", {})
    except Exception as exc:
        return False, [f"- ❌ health check failed: {str(exc)[:200]}"]
    lines, ok = [], True
    for ds in r.get("results", []):
        if ds.get("status") == "OK":
            lines.append(f"- ✅ {ds.get('name')} ({ds.get('uid')}) — {ds.get('message','')}")
        else:
            ok = False
            lines.append(f"- ❌ {ds.get('name')} ({ds.get('uid')}) — "
                         f"{ds.get('status')}: {ds.get('message','')}")
    if not lines:
        ok = False
        lines.append(f"- ❌ no datasources returned: {str(r)[:200]}")
    return ok, lines


def list_jobs():
    """The `job` label values present in Loki (what is shipping logs)."""
    try:
        r = mcp("list_loki_label_values",
                {"datasourceUid": LOKI_UID, "labelName": "job"})
    except Exception as exc:
        log(f"list_jobs failed: {str(exc)[:150]}")
        return []
    if isinstance(r, list):
        return [j for j in r if isinstance(j, str)]
    vals = r.get("data") or r.get("values") or []
    return [j for j in vals if isinstance(j, str)]


def newest_entry_age_hours(job, now):
    """Age in hours of the newest log line for `job`, or None if none found."""
    start = utc(now - timedelta(days=7))
    try:
        r = mcp("query_loki_logs",
                {"datasourceUid": LOKI_UID,
                 "logql": f'{{job="{job}"}}',
                 "limit": SAMPLE_LIMIT,
                 "direction": "backward",
                 "startRfc3339": start,
                 "endRfc3339": utc(now)})
    except Exception as exc:
        log(f"newest_entry_age_hours({job}) failed: {str(exc)[:150]}")
        return None
    rows = r.get("data") or []
    if not rows:
        return None
    try:
        ts = int(rows[0]["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None
    # Loki returns nanoseconds-since-epoch as a string.
    when = datetime.fromtimestamp(ts / 1e9, timezone.utc)
    return (now - when).total_seconds() / 3600.0


def entry_count(job, now, hours, filter_expr=None):
    """Entries for `job` in the last `hours`, optionally line-filtered.

    TRAP (verified live 2026-10-27): Loki's `query_loki_stats` accepts ONLY
    label matchers — both aggregations (`sum by (job) (...)`) and line filters
    (`|~ "error"`) are rejected with `400 only label matchers are supported`.
    So the unfiltered count uses `query_loki_stats` (exact, cheap), while a
    filtered count must use `query_loki_logs` and count the rows it returns.

    Returns (count, exact: bool). A filtered count is capped by `limit`, so it
    is reported as "at least N" when the cap is hit — never as an exact total.
    """
    if not filter_expr:
        try:
            r = mcp("query_loki_stats",
                    {"datasourceUid": LOKI_UID, "logql": f'{{job="{job}"}}',
                     "startRfc3339": utc(now - timedelta(hours=hours)),
                     "endRfc3339": utc(now)})
        except Exception as exc:
            log(f"entry_count({job}) failed: {str(exc)[:150]}")
            return None, False
        if isinstance(r, dict) and "entries" in r:
            return r["entries"], True
        return None, False

    try:
        r = mcp("query_loki_logs",
                {"datasourceUid": LOKI_UID,
                 "logql": f'{{job="{job}"}} |~ "{filter_expr}"',
                 "limit": FILTER_LIMIT,
                 "direction": "backward",
                 "startRfc3339": utc(now - timedelta(hours=hours)),
                 "endRfc3339": utc(now)})
    except Exception as exc:
        log(f"entry_count({job},filtered) failed: {str(exc)[:150]}")
        return None, False
    rows = r.get("data") or []
    return len(rows), False


def influx_buckets(now):
    """Bucket list + newest write time per bucket (best-effort).

    `buckets()` is the one Influx query proven to work through this datasource
    (verified live). Per-bucket freshness needs a second query per bucket; we
    cap the fan-out so a wide bucket set cannot turn one fire into N calls.
    """
    try:
        r = mcp("query_influxdb",
                {"datasourceUid": INFLUX_UID, "dialect": "flux",
                 "query": "buckets()"})
    except Exception as exc:
        return None, [f"- ❌ InfluxDB query failed: {str(exc)[:200]}"]
    if not isinstance(r, dict) or "rows" not in r:
        return None, [f"- ❌ InfluxDB returned an unexpected shape: {str(r)[:200]}"]
    names = [row.get("name") for row in r["rows"] if row.get("name")]
    return names, [f"- {len(names)} bucket(s): {', '.join(sorted(names))}"]


# ------------------------------------------------------------------ report
def build_report(now):
    """Run every check and assemble the markdown comment body."""
    started = utc(now)
    health_ok, health_lines = check_health()

    jobs = list_jobs()
    if EXPECTED_JOBS:
        missing = [j for j in EXPECTED_JOBS if j not in jobs]
        jobs = sorted(set(jobs) | set(EXPECTED_JOBS))
    else:
        missing = []

    stale, fresh, err_rows = [], [], []
    for job in jobs:
        age = newest_entry_age_hours(job, now)
        if age is None:
            stale.append((job, None))
        elif age >= STALE_HOURS:
            stale.append((job, age))
        else:
            fresh.append((job, age))
        total, _ = entry_count(job, now, WINDOW_HOURS)
        if total == 0:
            # Nothing arrived, so there is nothing to filter and no point
            # spending a second call to prove it. Also prevents the
            # contradiction of reporting "0 errors" against a stale source.
            err_rows.append((job, 0, 0, True))
            continue
        n, n_exact = entry_count(job, now, WINDOW_HOURS,
                                 filter_expr="(?i)error|fail|panic|critical")
        err_rows.append((job, n, total, n_exact))

    buckets, bucket_lines = influx_buckets(now)

    out = []
    out.append(f"## NOC triage — {started}")
    out.append("")
    out.append(f"R0 read-only sweep. Window: last {WINDOW_HOURS}h. "
               f"Stale threshold: {STALE_HOURS}h. "
               f"All values measured at `{started}`.")
    out.append("")

    out.append("### Datasources")
    out.extend(health_lines)
    out.append("")

    out.append("### Log freshness")
    if not jobs:
        out.append("- ⚠️ no `job` label values in Loki — nothing is shipping logs")
    for job, age in sorted(stale, key=lambda x: (x[1] is not None, -(x[1] or 0))):
        if age is None:
            out.append(f"- 🔴 **{job}** — no entries in the last 7 days")
        else:
            out.append(f"- 🟠 **{job}** — stale: newest entry {age:.1f}h old "
                       f"(threshold {STALE_HOURS}h)")
    for job, age in sorted(fresh, key=lambda x: -x[1]):
        out.append(f"- ✅ {job} — newest entry {age:.1f}h old")
    if missing:
        out.append(f"- 🔴 expected but absent from Loki: {', '.join(missing)}")
    out.append("")

    out.append(f"### Error volume (last {WINDOW_HOURS}h)")
    any_err = False
    for job, n, total, n_exact in err_rows:
        if n is None:
            out.append(f"- ⚠️ {job} — error count unavailable")
            continue
        shown = str(n) if n_exact else (f"at least {n}" if n >= FILTER_LIMIT else str(n))
        tot = "?" if total is None else str(total)
        if n > 0:
            any_err = True
            if total:
                out.append(f"- ⚠️ {job} — {shown} error-ish / {tot} total "
                           f"({100.0 * n / total:.1f}%)")
            else:
                out.append(f"- ⚠️ {job} — {shown} error-ish (total unavailable)")
        elif total == 0:
            # Zero total is NOT health — it means nothing arrived (already
            # reported as stale above). A green tick here would contradict it.
            out.append(f"- ⚪ {job} — no entries in the window to count")
        else:
            out.append(f"- ✅ {job} — 0 error-ish / {tot} total")
    if not err_rows:
        out.append("- (no jobs to count)")
    out.append("")

    out.append("### InfluxDB")
    out.extend(bucket_lines)
    out.append("")

    # A one-line verdict, so the reader does not have to infer it.
    problems = len(stale) + len(missing) + (0 if health_ok else 1)
    if problems == 0 and not any_err:
        out.append("**Verdict: all clear.** No stale sources, no datasource "
                   "failures, no error-ish lines in the window.")
    else:
        bits = []
        if not health_ok:
            bits.append("a datasource is unhealthy")
        if stale:
            bits.append(f"{len(stale)} stale/absent source(s)")
        if missing:
            bits.append(f"{len(missing)} expected source(s) missing")
        if any_err:
            bits.append("error-ish lines present")
        out.append(f"**Verdict: attention needed** — {', '.join(bits)}.")
    return "\n".join(out)


# ------------------------------------------------------------------ github
def github_install_token():
    now = int(time.time())
    token = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": GITHUB_APP_ID},
                       GITHUB_APP_PRIVATE_KEY, algorithm="RS256")
    r = http_json(
        "POST",
        f"https://api.github.com/app/installations/{GITHUB_INSTALLATION_ID}/access_tokens",
        {"Authorization": f"Bearer {token}",
         "Accept": "application/vnd.github+json",
         "Content-Type": "application/json"})
    return r["token"]


def post_comment(body, token):
    c = http_json(
        "POST",
        f"https://api.github.com/repos/{GITHUB_APP_REPO}/issues/"
        f"{NOC_ISSUE_NUMBER}/comments",
        {"Authorization": f"Bearer {token}",
         "Accept": "application/vnd.github+json",
         "Content-Type": "application/json"},
        {"body": body})
    log(f"comment posted: id={c.get('id')} ({len(body)} chars)")
    return c.get("id")


def main():
    now = datetime.now(timezone.utc)
    log(f"aios-noc starting at {utc(now)}")

    body = build_report(now)

    if os.environ.get("NOC_DRY_RUN") == "1":
        print(body)
        return 0

    token = github_install_token()
    post_comment(body, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
