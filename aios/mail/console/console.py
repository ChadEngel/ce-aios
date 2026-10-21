"""AIOS Inbox Console — READ-ONLY dashboard + dry-run preview (0.6.0).

Roadmap inbox-console: build-order step 3 (dry-run preview) and step 4
(read-only UI) of 5. Editing ships later (step 5), ONLY once this console is
proven true; there is deliberately no write path in this app yet.

The console shares the SAME state PVC as the digest/filer legs — mounted
READ-ONLY (k8s readOnly:true, so a bug here cannot corrupt the ledger) — and
speaks the ledger through state.py and classification through
engine.classify: the same code the fires run, one source of truth (that IS
the M1 gate "console reflects what the live digest actually did").

HARD RULES (decisions.md D3/D7 boundaries, enforced in this file):
  - The console has ZERO mutation endpoints. No move/delete/file of mail,
    ever; no Mail.Send; no writes to /state (read-only mount, and nothing
    in this code opens /state for writing).
  - POST /api/dryrun is pure compute: a HYPOTHETICAL config is validated and
    classified against recent ledger items in memory; nothing is saved.
  - The console never talks to GitHub, never calls the LLM gateway.
  - Mail content shown is limited to what the ledger already carries:
    subject/sender/preview snippets — never bodies, never credentials.
"""
import base64
import datetime
import hashlib
import hmac
import importlib
import json
import os
import ssl
import sys
import urllib.request

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(APP_DIR)          # aios/mail/ — for engine + state
for _p in (APP_DIR, PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import state as ST                          # noqa: E402  (ledger contract)
from engine import config as ECFG           # noqa: E402
from engine.classify import Classifier      # noqa: E402

STATE_DIR = os.environ.get("DIGEST_STATE_DIR", "/state")
PASSWORD = os.environ.get("CONSOLE_PASSWORD", "")
K8S_NS = os.environ.get("CONSOLE_K8S_NS", "ai")
STATIC_DIR = os.path.join(APP_DIR, "static")
DRYRUN_CAP = 500       # classify at most the newest N ledger items
SAMPLE_CAP = 12        # per-transition sample list length
JOURNAL_CAP = 12


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


# --------------------------------------------------------------------------
# auth — HTTP Basic over the LAN (single-user homelab default). Unset
# password = open console on LAN only; the k8s manifest always injects one.
# --------------------------------------------------------------------------
def require_auth(request: Request):
    if not PASSWORD:
        return
    hdr = request.headers.get("authorization", "")
    if not hdr.startswith("Basic "):
        raise HTTPException(401, "auth required",
                            headers={"WWW-Authenticate": "Basic realm=aios"})
    try:
        userpass = base64.b64decode(hdr[6:]).decode()
    except Exception:
        raise HTTPException(401, "bad auth header",
                            headers={"WWW-Authenticate": "Basic realm=aios"})
    pw = userpass.partition(":")[2]
    if not hmac.compare_digest(pw, PASSWORD):
        raise HTTPException(401, "bad credentials",
                            headers={"WWW-Authenticate": "Basic realm=aios"})


# --------------------------------------------------------------------------
# ledger views
# --------------------------------------------------------------------------
def _merged():
    rows = ST.read_all()
    return ST.latest_by_id(rows), rows


def _dest_label(c, cfg):
    """Human label of where a classified item would end up. Mirrors 0.4.x
    semantics: priority stays; privacy/notice kept; typed folders; junk."""
    if c.get("_self_notice"):
        return "kept in inbox (digest notice)"
    if c.get("type") == "priority":
        return "stays in inbox (priority)"
    if c.get("_privacy"):
        return "kept in inbox (privacy)"
    f = c.get("_file")
    if f == "receipts":
        return f"{cfg.receipts_folder}"
    if f == "orders":
        return f"{cfg.orders_folder}"
    if f == "filed":
        return f"{cfg.filed_folder} (junk)"
    return "kept in inbox"


def _row_to_message(r):
    """Ledger intent row -> a Graph-shaped message the classifier consumes.
    Exact inverse of the filer's _row() capture (the ledger was deliberately
    designed to carry the content needed for EXACTLY this)."""
    ea = {"name": r.get("sender_name") or "", "address": r.get("sender_addr")
          or ""}
    return {"id": r["id"],
            "from": {"emailAddress": ea},
            "subject": r.get("subject") or "",
            "bodyPreview": r.get("preview") or "",
            "isRead": not r.get("unread", False),
            "receivedDateTime": r.get("received"), }


# --------------------------------------------------------------------------
# config views + provenance
# --------------------------------------------------------------------------
def _display(v):
    if isinstance(v, tuple):
        return ", ".join(v) if v else "(none)"
    if isinstance(v, bool):
        return "on" if v else "off"
    return v


def _config_view():
    current = ECFG.load(STATE_DIR, log=lambda *a: None)
    env_cfg = ECFG.from_env()
    dflt = ECFG.Config()
    file_values = {}
    path = ECFG.config_path(STATE_DIR)
    if os.path.exists(path):
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            if isinstance(raw, dict):
                file_values = {k: v for k, v in raw.items()
                               if k not in ("version", "source")}
        except Exception:
            pass

    def _norm(v):
        # compare raw yaml scalars/lists against the env-config's values
        if isinstance(v, (list, tuple)):
            return tuple(sorted(str(x).strip().lower() for x in v))
        return str(v).strip().lower() if v is not None else None

    keys = []
    for k, v in ECFG.asdict(current).items():
        if k == "source":
            continue
        if k in file_values and _norm(file_values[k]) != _norm(
                getattr(env_cfg, k)):
            # "file" only where the file value actually CHANGES the env-
            # derived value; save() writes full snapshots, so membership
            # alone would stamp every key "file" forever after first save
            origin = "file"
        elif str(getattr(env_cfg, k)) != str(getattr(dflt, k)):
            origin = "env"
        else:
            origin = "default"
        keys.append({"key": k, "value": _display(v), "origin": origin})
    journal = [{"ts": e.get("ts"), "actor": e.get("actor"),
                "version": e.get("version")}
               for e in ECFG.history(STATE_DIR)[-JOURNAL_CAP:]][::-1]
    return {"version": current.version, "source": current.source,
            "prose_model": current.prose_model,
            "privacy_model": current.privacy_model, "keys": keys,
            "file_keys": sorted(file_values), "journal": journal}


# --------------------------------------------------------------------------
# k8s status (in-cluster, read-only SA; failures render as null, not 500)
# --------------------------------------------------------------------------
def _k8s_status():
    # k3s: svc/kubernetes (10.43.0.1:443) answers refused from pods on some
    # nodes — talk to the API server's real endpoint instead (CONSOLE_K8S_URL
    # pinned in the deployment manifest; RBAC is namespace-scoped read-only).
    base = os.environ.get("CONSOLE_K8S_URL") or (
        f"https://{os.environ.get('KUBERNETES_SERVICE_HOST')}:443"
        if os.environ.get("KUBERNETES_SERVICE_HOST") else None)
    if not base:
        return None
    tok_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    try:
        with open(tok_path, "r") as fh:
            tok = fh.read().strip()
        ctx = ssl.create_default_context(cafile=ca_path)
        out = {}
        for kind in ("cronjobs", "jobs"):
            url = f"{base}/apis/batch/v1/namespaces/{K8S_NS}/{kind}?limit=20"
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                data = json.loads(resp.read())
            out[kind] = [
                {"name": i["metadata"]["name"],
                 "schedule": (i.get("spec", {}).get("schedule")
                              if kind == "cronjobs" else None),
                 "suspend": i.get("spec", {}).get("suspend"),
                 "last_schedule": (i.get("status", {}).get("lastScheduleTime")
                                   if kind == "cronjobs" else None),
                 "failed": i.get("status", {}).get("failed", 0),
                 "succeeded": i.get("status", {}).get("succeeded", 0),
                 "age": i["metadata"].get("creationTimestamp")}
                for i in data.get("items", [])]
        return out
    except Exception:
        return None


# --------------------------------------------------------------------------
# dashboard payload
# --------------------------------------------------------------------------
def _state_payload():
    merged, rows = _merged()
    filed = {}
    inbox_est = 0
    pending_report = 0
    keeps, filed_items = [], []
    unresolved = ST.unresolved_intents(rows)
    for r in merged.values():
        # merged rows carry the OUTCOME's "t" by design — key off status,
        # never off the type marker
        if not (r.get("status") in ("keep", "filed") or r.get("folder")):
            continue
        if not ST.is_settled(r):
            continue  # intent-only / pending_retry rows counted via unresolved
        st = r.get("status")
        if st == "keep":
            inbox_est += 1
            if not r.get("reported"):
                pending_report += 1
            keeps.append(r)
        else:
            folder = r.get("folder") or "?"
            filed[folder] = filed.get(folder, 0) + 1
            if not r.get("reported"):
                pending_report += 1
            filed_items.append(r)
    keeps.sort(key=lambda r: r.get("ts") or "", reverse=True)
    filed_items.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return {
        "now": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "config": _config_view(),
        "ledger": {
            "total_rows": len(rows),
            "items": len(merged),
            "in_inbox_estimate": inbox_est,
            "pending_digest_report": pending_report,
            "unresolved": len(unresolved),
            "unresolved_samples": [{"id": u.get("id"),
                                    "folder": u.get("folder"),
                                    "subject": (u.get("subject") or "")[:120],
                                    "ts": u.get("ts")} for u in
                                   list(unresolved)[:15]],
            "filed_by_folder": dict(sorted(
                filed.items(), key=lambda kv: -kv[1])),
            "recent_keeps": [{"ts": r.get("ts"),
                              "subject": (r.get("subject") or "")[:120],
                              "sender": (r.get("sender_name")
                                         or r.get("sender_addr") or "")[:60],
                              "reason": r.get("reason") or
                              (r.get("privacy") or "—"),
                              "reported": bool(r.get("reported"))}
                             for r in keeps[:10]],
            "recent_filed": [{"ts": r.get("ts"),
                              "folder": r.get("folder"),
                              "subject": (r.get("subject") or "")[:120],
                              "sender": (r.get("sender_name")
                                         or r.get("sender_addr") or "")[:60],
                              "reported": bool(r.get("reported"))}
                             for r in filed_items[:10]],
            "last_write_ts": rows[-1].get("ts") if rows else None,
        },
        "k8s": _k8s_status(),
        "boundaries": "read-only: no mutation endpoints; /state mounted "
                      "read-only; mail is only ever touched by the digest/"
                      "filer legs (D3)",
    }


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/state")
def api_state():
    require_auth(_current_request())
    return _state_payload()


_current_request_store = {"req": None}   # simple sync bridge (single-threaded
# uvicorn worker keeps this correct; require_auth reads the in-flight request)


def _current_request():
    return _current_request_store.get("req")


@app.middleware("http")
async def _auth_mw(request: Request, call_next):
    _current_request_store["req"] = request
    try:
        require_auth(request)   # every path incl. / — browser prompts at load
    except HTTPException as exc:
        _current_request_store["req"] = None
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.detail},
                            headers=exc.headers or {})
    resp = await call_next(request)
    _current_request_store["req"] = None
    return resp


class DryRunRequest(BaseModel):
    overrides: dict


@app.post("/api/dryrun")
def api_dryrun(req: DryRunRequest):
    require_auth(_current_request())
    # NOTE: pure compute — nothing is persisted, nothing touches mail.
    try:
        if not isinstance(req.overrides, dict) or not req.overrides:
            raise ValueError("overrides must be a non-empty JSON object")
        current = ECFG.load(STATE_DIR, log=lambda *a: None)
        merged_cfg = ECFG.asdict(current)
        merged_cfg.pop("source", None)
        merged_cfg.update(req.overrides)
        proposed = ECFG.from_dict(merged_cfg, source="dryrun")  # validates
    except ValueError as exc:
        raise HTTPException(400, f"invalid config: {str(exc)[:200]}")
    merged, _rows = _merged()
    cands = []
    for r in merged.values():
        # merged rows carry the OUTCOME's "t" — key off status/folder
        if not (r.get("status") in ("keep", "filed") or r.get("folder")):
            continue
        if not ST.is_settled(r):
            continue
        cands.append(r)
    # newest first; keeps (current inbox) weigh in before historic filed rows
    cands.sort(key=lambda r: (r.get("status") == "keep",
                              r.get("ts") or ""), reverse=True)
    cands = cands[:DRYRUN_CAP]
    msgs = [_row_to_message(r) for r in cands]
    cur = Classifier(current).classify(msgs, ())   # contacts tier needs the
    pro = Classifier(proposed).classify(msgs, ())   # live sent-folder: n/a in
    # preview (documented in the UI)
    cur_by, pro_by = {}, {}
    for c in cur:
        cur_by[c["_fid"]] = c   # _fid = raw message id (ledger row id)
    for c in pro:
        pro_by[c["_fid"]] = c
    groups = {}
    for r in cands:
        a, b = cur_by.get(r["id"]), pro_by.get(r["id"])
        if not a or not b:
            continue
        la, lb = _dest_label(a, current), _dest_label(b, proposed)
        if la == lb:
            continue
        g = groups.setdefault((la, lb), {"from": la, "to": lb,
                                         "count": 0, "sample": []})
        g["count"] += 1
        if len(g["sample"]) < SAMPLE_CAP:
            g["sample"].append({"id": r["id"],
                                "subject": (r.get("subject") or "")[:120],
                                "sender": ((r.get("sender_name") or "")
                                           + " <"
                                           + (r.get("sender_addr") or "")
                                           + ">")[:100]})
    changes = sorted(groups.values(), key=lambda g: -g["count"])
    return {"current_version": current.version,
            "proposed_version": proposed.version,
            "items_scanned": len(cands),
            "changed": sum(g["count"] for g in changes),
            "changes": changes,
            "note": "PREVIEW ONLY — nothing was saved, nothing was moved"}


if __name__ == "__main__":
    # local/dev: uvicorn console:app --port 8080
    pass