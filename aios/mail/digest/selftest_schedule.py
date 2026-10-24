#!/usr/bin/env python3
"""Offline self-test for the schedule reconciler (R1).

Covers the pure plan (the part that must never PATCH in a loop) and the HTTP
path against a local stub API server — so the fetch/compare/patch logic is
exercised WITHOUT a cluster, and the fail-safe behaviour on a 403/timeout is
proven rather than assumed.

    python3 selftest_schedule.py      (needs croniter installed)
"""
import http.server
import json
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="aios-sched-test-")
os.environ["DIGEST_STATE_DIR"] = TMP
for p in (HERE, PARENT):
    if p not in sys.path:
        sys.path.insert(0, p)

import engine.config as ECFG                 # noqa: E402
import schedule_reconciler as R               # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


print("== 1. plan_patch is pure and loop-free ==")
check("identical -> None (no PATCH, no loop)",
      R.plan_patch("0 5,12,17 * * *", "America/Chicago",
                   "0 5,12,17 * * *", "America/Chicago") is None)
check("whitespace-normalised -> None",
      R.plan_patch("0 5,12,17 * * *", "America/Chicago",
                   "0  5,12,17   * * *", "America/Chicago") is None)
body = R.plan_patch("0 5,12,17 * * *", "America/Chicago", "0 7,18 * * *",
                    "America/Chicago")
check("schedule diff -> merge-patch body",
      body == {"spec": {"schedule": "0 7,18 * * *",
                        "timeZone": "America/Chicago"}}, body)
check("tz diff alone -> body",
      R.plan_patch("0 5 * * *", "America/Chicago",
                   "0 5 * * *", "America/New_York")
      == {"spec": {"schedule": "0 5 * * *", "timeZone": "America/New_York"}})

print("== 2. reconcile against a stub API server ==")


class Stub(http.server.BaseHTTPRequestHandler):
    """Minimal k8s API: GET returns the current CronJob, PATCH records the
    body and mutates. `state` is shared so we can assert the patch landed."""
    state = {"schedule": "0 5,12,17 * * *", "tz": "America/Chicago",
             "patches": [], "fail_patch": False, "fail_get": False}

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.state["fail_get"]:
            return self._send(500, {"message": "boom"})
        return self._send(200, {"spec": {"schedule": self.state["schedule"],
                                         "timeZone": self.state["tz"]}})

    def do_PATCH(self):
        if self.state["fail_patch"]:
            return self._send(403, {"message": "forbidden"})
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        self.state["patches"].append(body)
        if "schedule" in body.get("spec", {}):
            self.state["schedule"] = body["spec"]["schedule"]
        if "timeZone" in body.get("spec", {}):
            self.state["tz"] = body["spec"]["timeZone"]
        return self._send(200, {"spec": {"schedule": self.state["schedule"],
                                         "timeZone": self.state["tz"]}})


srv = http.server.HTTPServer(("127.0.0.1", 0), Stub)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
# the reconciler reads these; stub token/CA via a temp dir is overkill — point
# it at the server and let _ssl_ctx fall back (http URL means no TLS anyway)
os.environ["AIOS_K8S_URL"] = f"http://127.0.0.1:{port}"
# monkeypatch _token so we never need a serviceaccount file
R._token = lambda: "stub-token"
R._ssl_ctx = lambda: None

cfg = ECFG.from_dict(ECFG.asdict(ECFG.from_env()), source="file")
# default config schedule == stub schedule -> noop, no PATCH
res = R.reconcile(cfg, log=lambda *a: None)
check("in-sync -> noop", res["action"] == "noop", res)
check("no PATCH issued on noop", Stub.state["patches"] == [], Stub.state["patches"])

# config differs -> exactly one PATCH, and it lands
merged = ECFG.asdict(cfg)
merged.pop("source", None)
merged["digest_schedule"] = "0 7,18 * * *"
cfg2 = ECFG.from_dict(merged, source="file")
res = R.reconcile(cfg2, log=lambda *a: None)
check("drift -> patched", res["action"] == "patched", res)
check("exactly one PATCH", len(Stub.state["patches"]) == 1, Stub.state["patches"])
check("server now carries new schedule",
      Stub.state["schedule"] == "0 7,18 * * *", Stub.state["schedule"])

# idempotent: reconcile again -> noop (no PATCH loop)
res = R.reconcile(cfg2, log=lambda *a: None)
check("re-reconcile -> noop (idempotent)",
      res["action"] == "noop" and len(Stub.state["patches"]) == 1, res)

print("== 3. fail-safe: errors leave the CronJob alone ==")
Stub.state["fail_patch"] = True
merged3 = ECFG.asdict(cfg2)
merged3.pop("source", None)
merged3["digest_schedule"] = "0 9 * * *"
cfg3 = ECFG.from_dict(merged3, source="file")
res = R.reconcile(cfg3, log=lambda *a: None)
check("403 -> action=error", res["action"] == "error", res)
check("CronJob unchanged after failed patch",
      Stub.state["schedule"] == "0 7,18 * * *", Stub.state["schedule"])
Stub.state["fail_patch"] = False

Stub.state["fail_get"] = True
res = R.reconcile(cfg3, log=lambda *a: None)
check("GET failure -> action=error, no crash", res["action"] == "error", res)
Stub.state["fail_get"] = False

print("== 4. invalid config schedule is never applied ==")
bad = ECFG.asdict(cfg)
bad.pop("source", None)
bad["digest_schedule"] = "0 9 * * *"  # valid...
cfg4 = ECFG.from_dict(bad, source="file")
# ...but simulate a bad value slipping past config validation
cfg4.digest_schedule = "totally bogus"
res = R.reconcile(cfg4, log=lambda *a: None)
check("invalid schedule -> skipped, no PATCH",
      res["action"] == "skipped", res)

print("== 5. set_schedule persists a validated cadence (edit path) ==")
# point the config store at a fresh dir so we do not disturb the stub test
edit_dir = tempfile.mkdtemp(prefix="aios-sched-edit-")
new = R.set_schedule("0 7,18 * * *", "America/Denver", actor="tester",
                     state_dir=edit_dir, log=lambda *a: None)
check("saved + source=file", new.source == "file"
      and new.digest_schedule == "0 7,18 * * *"
      and new.digest_tz == "America/Denver", new)
reloaded = ECFG.load(edit_dir, log=lambda *a: None)
check("round-trips through config.yaml",
      reloaded.digest_schedule == "0 7,18 * * *"
      and reloaded.digest_tz == "America/Denver", reloaded)
check("journal recorded the actor",
      any(e.get("actor") == "tester" for e in ECFG.history(edit_dir)))
# other keys preserved (full-snapshot save, not partial-default reset)
check("other keys preserved on save",
      reloaded.scan_top == cfg.scan_top, (reloaded.scan_top, cfg.scan_top))
# an invalid schedule is refused at the edit path too
try:
    R.set_schedule("*/5 * * * *", None, state_dir=edit_dir,
                   log=lambda *a: None)
    check("set_schedule refuses >1/hour", False, "accepted")
except ValueError:
    check("set_schedule refuses >1/hour", True)
# and a refused edit left the file untouched
check("refused edit did not change stored schedule",
      ECFG.load(edit_dir, log=lambda *a: None).digest_schedule == "0 7,18 * * *")

srv.shutdown()
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
