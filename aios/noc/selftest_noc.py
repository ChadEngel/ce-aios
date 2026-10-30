#!/usr/bin/env python3
"""Offline self-test for the NOC agent (aios/noc/noc.py).

The agent's job is to turn a fixed set of MCP answers into one honest markdown
report. The part that must never be wrong is the REPORT LOGIC: a stale source
reported as fresh, a zero-count shown as a green tick against a silent job, or
a filtered count claimed as exact would all make the NOC lie. So this stubs the
single `mcp()` seam and asserts what build_report() says.

No network, no cluster, no VK needed — run before any deploy:

    python3 selftest_noc.py
"""
import importlib.util
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# noc.py reads these at import time. Dummy values keep the import honest
# (real values come from the aios-noc-secrets K8s Secret).
for k in ("BIFROST_VK_AIOS_NOC", "GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY",
          "NOC_ISSUE_NUMBER"):
    os.environ.setdefault(k, "dummy")

_spec = importlib.util.spec_from_file_location("noc_mod",
                                               os.path.join(HERE, "noc.py"))
N = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(N)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# A fake MCP layer: a dict of tool-name -> callable(args) -> payload. Anything
# not supplied raises, so a report that queries something unexpected fails loud
# rather than silently dropping a check.
class FakeMCP:
    def __init__(self, **tools):
        self.tools = tools
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, args))
        if name not in self.tools:
            raise AssertionError(f"unexpected MCP tool call: {name} {args}")
        return self.tools[name](args)


def now():
    return datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)


def ns(dt):
    return str(int(dt.timestamp() * 1e9))


def run(report_now=None, **tools):
    """Install a fake mcp() and build a report; restore the real one after."""
    real = N.mcp
    N.mcp = FakeMCP(**tools)
    try:
        return N.build_report(report_now or now())
    finally:
        N.mcp = real


print("== 1. healthy estate -> all clear verdict ==")
healthy = run(
    check_datasources_health=lambda a: {"results": [
        {"name": "Loki", "uid": "loki", "status": "OK", "message": "connected"},
        {"name": "InfluxDB", "uid": "dfdk", "status": "OK", "message": "working"}]},
    list_loki_label_values=lambda a: ["aios-manager", "udm-syslog"],
    query_loki_logs=lambda a: ({"data": [{"timestamp": ns(now())}]}
                               if a.get("limit") == N.SAMPLE_LIMIT
                               else {"data": []}),
    query_loki_stats=lambda a: {"entries": 100},
    query_influxdb=lambda a: {"rows": [{"name": "host_metrics"},
                                       {"name": "network_metrics"}]},
)
check("reports datasources OK", "- ✅ Loki (loki)" in healthy, healthy[:200])
check("all-clear verdict", "Verdict: all clear" in healthy, healthy[-300:])
check("no STALE finding when fresh",
      "🟠" not in healthy and "🔴" not in healthy, healthy)

print("== 2. a silent source is STALE, never a green zero ==")
# Force it precisely: newest-entry query returns nothing (-> no entries 7d),
# and stats says 0 in the window.
old = run(
    check_datasources_health=lambda a: {"results": [
        {"name": "Loki", "uid": "loki", "status": "OK", "message": "c"}]},
    list_loki_label_values=lambda a: ["udm-syslog"],
    query_loki_logs=lambda a: {"data": []},
    query_loki_stats=lambda a: {"entries": 0},
    query_influxdb=lambda a: {"rows": [{"name": "b1"}]},
)
check("silent source flagged (no entries 7d)", "🔴 **udm-syslog**" in old, old)
check("zero total shown as ⚪, not ✅",
      "⚪ udm-syslog" in old and "✅ udm-syslog — 0 error-ish" not in old, old)
check("attention verdict", "Verdict: attention needed" in old, old)

print("== 3. stale-by-age (entries exist but old) ==")
import datetime as _dt  # noqa: E402
stale_ts = ns(now() - _dt.timedelta(hours=50))
by_age = run(
    check_datasources_health=lambda a: {"results": [
        {"name": "Loki", "uid": "loki", "status": "OK", "message": "c"}]},
    list_loki_label_values=lambda a: ["noc"],
    query_loki_logs=lambda a: ({"data": [{"timestamp": stale_ts}]}
                               if a.get("limit") == N.SAMPLE_LIMIT
                               else {"data": []}),
    query_loki_stats=lambda a: {"entries": 0},
    query_influxdb=lambda a: {"rows": [{"name": "b1"}]},
)
check("stale age reported with hours", "🟠 **noc**" in by_age and "50." in by_age,
      by_age)

print("== 4. filtered counts are 'at least N', never exact ==")
# 500 error-ish rows == FILTER_LIMIT -> must read "at least 500", not "500"
n_filter = N.FILTER_LIMIT
capped = run(
    check_datasources_health=lambda a: {"results": [
        {"name": "Loki", "uid": "loki", "status": "OK", "message": "c"}]},
    list_loki_label_values=lambda a: ["noisy"],
    query_loki_logs=lambda a: (
        {"data": [{"timestamp": ns(now())}]} if a.get("limit") == N.SAMPLE_LIMIT
        else {"data": [{"timestamp": ns(now())}] * n_filter}),
    query_loki_stats=lambda a: {"entries": 1000},
    query_influxdb=lambda a: {"rows": [{"name": "b1"}]},
)
check("capped filtered count says 'at least'", f"at least {n_filter}" in capped,
      capped)
check("error line present", "⚠️ noisy" in capped, capped)

print("== 5. datasource failure is a hard finding ==")
down = run(
    check_datasources_health=lambda a: {"results": [
        {"name": "InfluxDB", "uid": "d", "status": "ERROR", "message": "boom"}]},
    list_loki_label_values=lambda a: [],
    query_loki_stats=lambda a: {"entries": 0},
    query_influxdb=lambda a: {"rows": []},
)
check("failed datasource shown ❌", "❌ InfluxDB" in down, down)
check("no jobs -> warns nothing is shipping",
      "no `job` label values" in down, down)
check("attention verdict on failure", "Verdict: attention needed" in down, down)

print("== 6. expected-jobs set flags a VANISHED source ==")
N.EXPECTED_JOBS = ["udm-syslog", "unpoller"]
vanished = run(
    check_datasources_health=lambda a: {"results": [
        {"name": "Loki", "uid": "loki", "status": "OK", "message": "c"}]},
    list_loki_label_values=lambda a: ["unpoller"],   # udm-syslog gone entirely
    query_loki_logs=lambda a: {"data": [{"timestamp": ns(now())}]},
    query_loki_stats=lambda a: {"entries": 10},
    query_influxdb=lambda a: {"rows": [{"name": "b1"}]},
)
N.EXPECTED_JOBS = []
check("vanished source flagged", "expected but absent" in vanished, vanished)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
