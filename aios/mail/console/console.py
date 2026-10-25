"""AIOS Inbox Console — dashboard + dry-run + config editing (0.6.3).

Roadmap inbox-console: build-order step 3 (dry-run preview) → step 4 (read-only
UI) → step 5 (editing, THIS version). Editing exists only after the read-only
console proved true over a live window.

The console shares the SAME state PVC as the digest/filer legs — but with TWO
mounts, and the split IS the safety argument:
  /state   READ-ONLY  — the ledger. state.py is used for reads only, and the
                        mount makes ledger.jsonl UNWRITABLE to this app.
  /config  READ-WRITE — the config store SUBDIRECTORY (config.yaml + journal),
                        the console's one and only write path.
A console bug can therefore edit config but cannot corrupt the ledger, no
matter what path it constructs.

HARD RULES (decisions.md D3/D7 boundaries):
  - The console NEVER touches mail: no move/delete/file/send, no Graph, no
    LLM, no GitHub. Its only mutation is the config store.
  - POST /api/dryrun and POST /api/config/diff are pure compute: nothing is
    persisted.
  - POST /api/config/save validates -> journals the actor (D5) -> writes the
    COMPLETE snapshot. It refuses destructive values (remote models, negative
    retention, invalid schedules, unknown keys) before anything is written.
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
import model_catalog as MODCAT              # noqa: E402  (R2: cached catalog)
from engine import config as ECFG           # noqa: E402
from engine import schedule as SCHED        # noqa: E402
from engine import models as MODELS         # noqa: E402
from engine.classify import Classifier      # noqa: E402

STATE_DIR = os.environ.get("DIGEST_STATE_DIR", "/state")
# Step 5: the config store is a SUBDIRECTORY, mounted separately from the
# read-only ledger. The console mounts it read-write here (DIGEST_CONFIG_DIR);
# /state itself stays READ-ONLY, so a console bug cannot open ledger.jsonl for
# writing no matter what path it constructs. Writes are further confined to the
# config store by the code below (config.save / set_schedule only).
CONFIG_DIR = os.environ.get("DIGEST_CONFIG_DIR") or ECFG.default_dir()
# Actor recorded in the D5 journal for console-originated saves. The k8s
# deployment sets CONSOLE_ACTOR (e.g. "console:<UPN>"); a bare name is the
# LAN default.
CONSOLE_ACTOR = os.environ.get("CONSOLE_ACTOR", "console")
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
    current = ECFG.load(CONFIG_DIR, log=lambda *a: None)
    env_cfg = ECFG.from_env()
    dflt = ECFG.Config()
    file_values = {}
    path = ECFG.config_path(CONFIG_DIR)
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
               for e in ECFG.history(CONFIG_DIR)[-JOURNAL_CAP:]][::-1]
    return {"version": current.version, "source": current.source,
            "prose_model": current.prose_model,
            "privacy_model": current.privacy_model, "keys": keys,
            "file_keys": sorted(file_values), "journal": journal,
            "schedule": _schedule_view(current),
            "models": _models_view(current)}


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
# --------------------------------------------------------------------------
# schedule view (R1) — read-only here; the reconciler applies it
# --------------------------------------------------------------------------
def _schedule_view(current):
    """The digest cadence from the config store plus the next fires, so the
    dashboard reflects what the reconciler is enforcing. Pure: this only
    FORMATS; the reconciler on the filer heartbeat owns apply (the console
    still has no mutation path)."""
    d = SCHED.describe(current.digest_schedule, current.digest_tz, n=5)
    return {"schedule": current.digest_schedule, "tz": current.digest_tz,
            "valid": d.get("valid"), "error": d.get("error"),
            "next": d.get("next", []), "advisories": d.get("advisories", []),
            "note": "applied by the filer-heartbeat reconciler; the console "
                    "only reports it (no write path)"}


def _models_view(current):
    """R2 model picker data. The catalogue is the CACHE the filer legs wrote
    to /state — this console holds no Bifrost VK and never calls the gateway,
    so it only ever reads what a leg already fetched (see model_catalog.py).
    Suitability is computed with the same engine.models rules that gate a
    save, so the UI cannot show a choice the engine would refuse."""
    meta = MODCAT.read_cache_meta(STATE_DIR)
    catalog = meta.get("models") or []
    out = MODELS.catalog_summary(catalog, current.prose_model,
                                 current.privacy_model)
    out["catalog_ts"] = meta.get("ts")
    out["catalog_url"] = meta.get("url")
    out["stale"] = not catalog
    out["note"] = ("catalogue cached by the filer heartbeat (this console "
                    "holds no gateway key and makes no LLM calls)")
    return out


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
        current = ECFG.load(CONFIG_DIR, log=lambda *a: None)
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


# --------------------------------------------------------------------------
# STEP 5 — the config write path (the console's FIRST and ONLY mutation).
#
# Scope, stated plainly because it is the whole safety argument:
#   - These endpoints write the CONFIG STORE ONLY (config.yaml + its journal),
#     which lives in a subdirectory mounted read-write (DIGEST_CONFIG_DIR).
#     The ledger's directory (/state) stays mounted READ-ONLY, so no bug here
#     can open ledger.jsonl for writing no matter what path it builds.
#   - They NEVER touch mail: no move/delete/send, no Graph, no LLM, no GitHub.
#   - Every save is VALIDATED first (engine.config.apply_overrides ->
#     validate()), then journaled with the actor (D5). An invalid or
#     destructive config is refused with 400 and writes nothing.
# --------------------------------------------------------------------------
def _config_write_available():
    """Guard: refuse to pretend to save when the config dir is not actually
    writable (e.g. a deployment that forgot the RW mount). Better a clear 503
    than a save that silently does nothing or 500s on the ledger's RO mount."""
    d = CONFIG_DIR
    try:
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write-probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.unlink(probe)
        return True
    except Exception:
        return False


class ConfigWrite(BaseModel):
    overrides: dict
    confirm: bool = False      # save requires an explicit confirm from the UI


def _propose(current, overrides):
    """current + overrides -> validated proposed Config. Raises ValueError
    (-> 400) on an unknown key or a value validate() refuses."""
    return ECFG.apply_overrides(current, overrides)


def _printable(v):
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return v


@app.post("/api/config/diff")
def api_config_diff(req: ConfigWrite):
    """Preview a config edit: the field-level diff (old -> new) and the
    resulting version. Writes NOTHING (same guarantee as /api/dryrun)."""
    require_auth(_current_request())
    try:
        current = ECFG.load(CONFIG_DIR, log=lambda *a: None)
        proposed = _propose(current, req.overrides)
    except ValueError as exc:
        raise HTTPException(400, f"invalid config: {str(exc)[:200]}")
    changes = [{"key": c["key"], "from": _printable(c["from"]),
                "to": _printable(c["to"])}
               for c in ECFG.diff(current, proposed)]
    return {"current_version": current.version,
            "proposed_version": proposed.version,
            "changes": changes,
            "note": "PREVIEW ONLY — nothing was saved"}


@app.post("/api/config/save")
def api_config_save(req: ConfigWrite):
    """THE write path. Validates, then persists the COMPLETE config snapshot
    + a D5 journal row with the actor. Writes only inside CONFIG_DIR.

    Requires confirm=true so a stray POST cannot mutate config by accident;
    the UI renders the diff first.
    """
    require_auth(_current_request())
    if not req.confirm:
        raise HTTPException(400, "confirm must be true to save")
    if not _config_write_available():
        raise HTTPException(503, f"config store not writable ({CONFIG_DIR}); "
                                 f"check the read-write mount")
    try:
        current = ECFG.load(CONFIG_DIR, log=lambda *a: None)
        proposed = _propose(current, req.overrides)
    except ValueError as exc:
        raise HTTPException(400, f"invalid config: {str(exc)[:200]}")
    changes = ECFG.diff(current, proposed)
    if not changes:
        return {"saved": False, "version": current.version,
                "changes": [], "note": "no change — nothing written"}
    try:
        ver = ECFG.save(proposed, actor=CONSOLE_ACTOR, state_dir=CONFIG_DIR)
    except Exception as exc:
        raise HTTPException(500, f"save failed: {str(exc)[:200]}")
    return {"saved": True, "version": ver, "actor": CONSOLE_ACTOR,
            "changes": [{"key": c["key"], "from": _printable(c["from"]),
                         "to": _printable(c["to"])} for c in changes],
            "note": "saved; takes effect at the NEXT fire (every ledger item "
                    "records the version that decided it)"}


if __name__ == "__main__":
    # local/dev: uvicorn console:app --port 8080
    pass