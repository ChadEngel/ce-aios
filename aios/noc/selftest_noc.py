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
    "query_influxdb": BUCKETS,
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
    "query_influxdb": BUCKETS,
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
    "query_influxdb": BUCKETS,
})
body = noc.build_report(NOW)
check("absent source is 🔴 not 🟠", "🔴" in body and "no entries in the last 7 days" in body, body)

# 5. unhealthy datasource is reported and forces attention
fake_mcp({
    "check_datasources_health": HEALTH_BAD,
    "list_loki_label_values": ["udm-syslog"],
    "query_loki_logs": loki_logs(newest_hours=0.2, error_rows=0),
    "query_loki_stats": loki_stats(10),
    "query_influxdb": BUCKETS,
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
    "query_influxdb": BUCKETS,
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

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} — {', '.join(FAILS)}")
    sys.exit(1)
print("all NOC selftests passed")
