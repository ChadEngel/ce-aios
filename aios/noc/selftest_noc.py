#!/usr/bin/env python3
"""Selftests for the NOC agent — no network, no cluster.

Run: python3 aios/noc/selftest_noc.py
Exits non-zero on the first failure. Every test here pins a behaviour that was
actually observed against the live tool plane, so a regression is caught
without needing the cluster.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

# noc.py reads these at import time; provide harmless values BEFORE importing.
os.environ.setdefault("BIFROST_VK_AIOS_NOC", "sk-bf-test")
os.environ.setdefault("GITHUB_APP_ID", "1")
os.environ.setdefault("GITHUB_APP_PRIVATE_KEY", "x")
os.environ.setdefault("NOC_ISSUE_NUMBER", "17")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import noc  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILS.append(name)


def fake_mcp(responses):
    """Replace noc.mcp with a dispatcher over {tool_name: payload|callable}."""
    calls = []

    def _mcp(name, args):
        calls.append((name, args))
        r = responses.get(name)
        if r is None:
            raise RuntimeError(f"unexpected tool call: {name}")
        return r(args) if callable(r) else r

    noc.mcp = _mcp
    return calls


# ---------------------------------------------------------------- fixtures
HEALTH_OK = {"results": [
    {"uid": "dfdkew37wk1dse", "name": "InfluxDB", "type": "influxdb",
     "status": "OK", "message": "datasource is working. 5 buckets found"},
    {"uid": "loki", "name": "Loki", "type": "loki",
     "status": "OK", "message": "Data source successfully connected."},
]}
HEALTH_BAD = {"results": [
    {"uid": "loki", "name": "Loki", "type": "loki",
     "status": "ERROR", "message": "connection refused"},
]}
BUCKETS = {"columns": ["name", "id"], "rows": [
    {"id": "a", "name": "host_metrics"}, {"id": "b", "name": "kube_metrics"},
]}

# Device-health Flux goes through the SAME query_influxdb tool as buckets(), so
# fakes must dispatch on the query text. These builders mimic the real tool's
# shapes, including the traps found live:
#   * `|> max()` / `|> last()` return one row PER SERIES (subsystem), so a
#     correct reader takes the max across ALL rows — a fake that returns a
#     single row would hide that bug.
#   * `Time` is RFC3339 with nanosecond precision.
# Timestamp is fixed just before NOW so "fresh" holds: age is measured against
# the report's `now`, so a hard-coded date would read as years stale.
_DEV_TS = "2026-10-27T11:59:30.123456789Z"


def dev_fresh(n):
    """Newest-sample max rows: one per subsystem, value n (all equal is the
    normal case — the controller repeats the same count per subsystem)."""
    return {"columns": ["Time", "num_disconnected"], "rows":
            [{"Time": _DEV_TS, "num_disconnected": n} for _ in range(5)]}


def dev_window(*per_series):
    """Windowed max rows: one per series. A test can pass a lower first value
    to prove the reader does not just take rows[0]."""
    return {"columns": ["Time", "num_disconnected"], "rows":
            [{"Time": _DEV_TS, "num_disconnected": v} for v in per_series]}


def influx_dispatch(buckets=BUCKETS, fresh=None, window=None, *_a, **_k):
    """query_influxdb fake that routes by Flux content, so one fake serves
    buckets() AND the device queries.

    Routing is by exact Flux shape, not a guessed substring:
      * the FRESHNESS query ends `|> max(column: "_time")`
      * the NOW query ends `|> max()` after a `group(columns:)`
      * the WINDOW query is a bare `|> max()`
    """
    def _r(args):
        q = args.get("query", "")
        if q.strip().startswith("buckets()"):
            return buckets
        if "max(column:" in q:                       # freshness (age) query
            return fresh if fresh is not None else dev_fresh(0)
        if "num_disconnected" in q and "|> max()" in q and "group(columns:" in q:
            return fresh if fresh is not None else dev_fresh(0)
        if "num_disconnected" in q and "max()" in q:
            return window if window is not None else dev_window(0)
        return buckets
    return _r


def loki_stats(entries):
    return {"streams": 1 if entries else 0, "chunks": entries,
            "entries": entries, "bytes": entries * 100}


def loki_logs(newest_hours=None, error_rows=0):
    """A `query_loki_logs` fake that behaves like the real tool: it returns
    the newest line for an UNFILTERED selector, and only `error_rows` rows for
    a line-FILTERED one. Conflating the two is what made the first draft of
    these tests assert nonsense (a healthy log reported as having an error)."""
    def _r(args):
        logql = args.get("logql", "")
        if "|~" in logql:
            return {"data": [{"timestamp": ns_ago(0.1), "line": "boom"}]
                    * error_rows}
        if newest_hours is None:
            return {"data": []}
        return {"data": [{"timestamp": ns_ago(newest_hours), "line": "ok"}]}
    return _r


NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)


def ns_ago(hours):
    """Loki timestamp (ns since epoch, as a string) for `hours` before NOW."""
    dt = NOW - timedelta(hours=hours)
    return str(int(dt.timestamp() * 1e9))


print("NOC agent selftests")

# 1. happy path: everything fresh, no errors
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.5, error_rows=0),
    "query_loki_stats": loki_stats(100),
    "query_influxdb": influx_dispatch(),
})
body = noc.build_report(NOW)
check("happy path says all clear", "all clear" in body.lower(), body[-160:])
check("happy path shows the fresh source", "✅ udm-syslog" in body)

# 2. stale source is reported with its age and drives the verdict
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=116.8),
    "query_loki_stats": loki_stats(0),
    "query_influxdb": influx_dispatch(),
})
body = noc.build_report(NOW)
check("stale source flagged", "🟠" in body and "udm-syslog" in body, body)
check("stale age rendered", "116.8h" in body, body)
check("stale drives 'attention needed'", "attention needed" in body.lower())

# 3. zero-total must NOT render as a green tick (it contradicts "stale")
check("zero total is not a green tick", "✅ udm-syslog — 0 error-ish" not in body, body)
check("zero total is neutral", "⚪" in body, body)

# 4. a source with NO entries in 7d is distinguished from merely stale
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["ghost"],
    "query_loki_logs": loki_logs(newest_hours=None),
    "query_loki_stats": loki_stats(0),
    "query_influxdb": influx_dispatch(),
})
body = noc.build_report(NOW)
check("absent source is 🔴 not 🟠", "🔴" in body and "no entries in the last 7 days" in body, body)

# 5. unhealthy datasource is reported and forces attention
fake_mcp({
    "check_datasources_health": HEALTH_BAD,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.2, error_rows=0),
    "query_loki_stats": loki_stats(10),
    "query_influxdb": influx_dispatch(),
})
body = noc.build_report(NOW)
check("unhealthy datasource flagged", "❌ Loki" in body, body)
check("unhealthy drives attention", "attention needed" in body.lower())

# 6. THE TRAP: query_loki_stats rejects line filters, so a FILTERED count must
#    go through query_loki_logs instead. If someone "optimises" this back to
#    stats, the live call 400s — pin the routing.
calls = fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.1, error_rows=3),
    "query_loki_stats": loki_stats(50),
    "query_influxdb": influx_dispatch(),
})
noc.build_report(NOW)
stats_logql = [a["logql"] for n, a in calls if n == "query_loki_stats"]
check("stats calls carry NO line filter",
      all("|~" not in q for q in stats_logql), str(stats_logql))
logs_logql = [a["logql"] for n, a in calls if n == "query_loki_logs"]
check("filtered counts go through query_loki_logs",
      any("|~" in q for q in logs_logql), str(logs_logql))

# 7. a soft-failing tool (plain-text error, not JSON) must not crash the run
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": {"_raw": 'unknown arguments "from", "query", "to"'},
    "query_loki_logs": loki_logs(newest_hours=None),
    "query_loki_stats": {"_raw": "only label matchers are supported"},
    "query_influxdb": {"_raw": "some plain-text failure"},
})
try:
    body = noc.build_report(NOW)
    check("non-JSON tool payloads do not crash", True)
    check("non-JSON influx surfaces as a problem", "InfluxDB query failed" in body
          or "unexpected shape" in body, body)
except Exception as exc:
    check("non-JSON tool payloads do not crash", False, str(exc))

# 8. an exception from one check must not abort the whole report
def _boom(_args):
    raise RuntimeError("connection reset")


fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.3, error_rows=0),
    "query_loki_stats": loki_stats(10),
    "query_influxdb": _boom,
})
try:
    body = noc.build_report(NOW)
    check("influx failure degrades gracefully", "InfluxDB query failed" in body, body)
    check("report still renders after a check fails", "### Datasources" in body)
except Exception as exc:
    check("influx failure degrades gracefully", False, str(exc))

# 9. timestamps are always present (failure mode: stale report read as current)
check("report is timestamped", "All values measured at" in body, body[:200])

# 10. the report never claims to have written anything (R0 posture)
check("report is read-only in wording",
      "move" not in body.lower() and "delete" not in body.lower(), body)

# --- 11. network device health (the UDM/UniFi estate) ----------------------
# This whole section exists because the NOC once watched ONLY log/datasource
# health, so the UDM's "device offline" alerts never appeared in a report.
# Pin every behaviour that makes this check trustworthy.

# 11a. healthy: fresh data, nothing disconnected -> all clear
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.5),
    "query_loki_stats": loki_stats(100),
    "query_influxdb": influx_dispatch(fresh=dev_fresh(0), window=dev_window(0)),
})
body = noc.build_report(NOW)
check("devices healthy -> all clear", "all clear" in body.lower(), body[-200:])
check("device section present", "### Network devices" in body, body)
check("0 disconnected now shown ✅",
      "✅ 0 device(s) disconnected now" in body, body)

# 11b. THE rows[0] TRAP: Flux max()/last() return one row PER SERIES. A reader
#      that takes rows[0] reports the FIRST series (here 0) while another
#      series (here 3) is the real, worse value. Live 2026-10-30 the windowed
#      max was [0,0,1,3,0,...]; rows[0] would have said "nothing happened"
#      while 3 devices were down. Pin that the MAX ACROSS ROWS is taken.
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.5),
    "query_loki_stats": loki_stats(100),
    "query_influxdb": influx_dispatch(fresh=dev_fresh(0),
                                      window=dev_window(0, 0, 1, 3, 0)),
})
body = noc.build_report(NOW)
check("rows[0]=0 but another series=3 -> catches the 3", "**3 device(s)**" in body
      or "3 device(s) disconnected at some point" in body, body)
check("rows[0] trap drives attention", "attention needed" in body.lower(), body)

# 11c. devices down RIGHT NOW -> 🔴 and attention
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.5),
    "query_loki_stats": loki_stats(100),
    "query_influxdb": influx_dispatch(fresh=dev_fresh(2), window=dev_window(2)),
})
body = noc.build_report(NOW)
check("device down now is 🔴", "🔴 **2 device(s) disconnected NOW**" in body, body)
check("device down drives attention", "attention needed" in body.lower(), body)
check("device down named in the verdict", "2 device(s) disconnected" in body, body)

# 11d. a between-sweep drop (down then recovered) is still surfaced — the
#      EXACT case that hid a real UDM event from a twice-daily sweep.
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.5),
    "query_loki_stats": loki_stats(100),
    "query_influxdb": influx_dispatch(fresh=dev_fresh(0), window=dev_window(1)),
})
body = noc.build_report(NOW)
check("recovered drop is still flagged",
      "disconnected at some point in the last" in body, body)
check("recovered drop drives attention", "attention needed" in body.lower(), body)

# 11e. stale device DATA: unpoller stopped -> counts untrustworthy, not "healthy"
_old_ts = "2026-10-27T06:00:00.000000000Z"   # ~6h before NOW
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.5),
    "query_loki_stats": loki_stats(100),
    "query_influxdb": influx_dispatch(
        fresh={"columns": ["Time", "num_disconnected"],
               "rows": [{"Time": _old_ts, "num_disconnected": 0}]},
        window=dev_window(0)),
})
body = noc.build_report(NOW)
check("stale device data flagged", "device data stale" in body, body)
check("stale device data drives attention", "attention needed" in body.lower(), body)

# 11f. NO device data at all -> never "healthy"
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.5),
    "query_loki_stats": loki_stats(100),
    "query_influxdb": influx_dispatch(
        fresh={"columns": [], "rows": []}, window={"columns": [], "rows": []}),
})
body = noc.build_report(NOW)
check("no device data is 🔴, not all-clear",
      "no UniFi device data" in body and "all clear" not in body.lower(), body)

# 11g. the check can be turned off wholesale (NOC_DEVICES=0)
_saved = noc.NOC_DEVICES_ON
noc.NOC_DEVICES_ON = False
fake_mcp({
    "check_datasources_health": HEALTH_OK,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.5),
    "query_loki_stats": loki_stats(100),
    "query_influxdb": influx_dispatch(),
})
body = noc.build_report(NOW)
noc.NOC_DEVICES_ON = _saved
check("device check can be disabled", "device check disabled" in body, body)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} — {', '.join(FAILS)}")
    sys.exit(1)
print("all NOC selftests passed")
