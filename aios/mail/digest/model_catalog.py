#!/usr/bin/env python3
"""Model catalogue fetch + cache (R2).

Fetches the OpenAI-compatible /v1/models catalogue from the gateway and caches
it on the state volume. This runs in the LEGS (filer heartbeat, like the
schedule reconciler), because the legs already hold the Bifrost VK — NOT in the
console, whose boundary is "holds no VK, never calls the LLM gateway". The
console then renders the cached list read-only.

Cache format (state/model-catalog.json):
  {"ts": "...", "url": "...", "models": [ {id, context_length, owned_by, local} ... ]}
Written atomically; a fetch failure leaves the PREVIOUS cache in place (a stale
catalogue is far better than none, and never blocks a fire).
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(HERE)
for _p in (HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine import models as MODELS  # noqa: E402

CATALOG_FILE = "model-catalog.json"


def catalog_path(state_dir):
    return os.path.join(state_dir, CATALOG_FILE)


def read_cached(state_dir):
    """The cached catalogue (possibly stale), or [] — never raises."""
    try:
        with open(catalog_path(state_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data.get("models") or []
        return []
    except Exception:
        return []


def read_cache_meta(state_dir):
    """Full cache doc (ts/url/models) for the console, or a safe empty shape."""
    try:
        with open(catalog_path(state_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("models"), list):
            return data
    except Exception:
        pass
    return {"ts": None, "url": None, "models": []}


def write_cache(state_dir, models, url):
    os.makedirs(state_dir, exist_ok=True)
    doc = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "url": url, "models": models}
    fd, tmp = tempfile.mkstemp(dir=state_dir, prefix=".catalog-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, catalog_path(state_dir))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return doc


def refresh(state_dir=None, log=print, force=False, max_age_hours=24):
    """Fetch /v1/models and cache it. Best-effort: any failure logs and leaves
    the existing cache. Returns the cache doc.

    Skips the network when the cache is fresh (< max_age_hours) unless force —
    the catalogue changes rarely and the heartbeat runs every 5 minutes.
    """
    state_dir = state_dir or os.environ.get("DIGEST_STATE_DIR", "/state")
    meta = read_cache_meta(state_dir)
    if not force and meta.get("ts") and meta.get("models"):
        try:
            age = time.time() - time.mktime(time.strptime(
                meta["ts"], "%Y-%m-%dT%H:%M:%SZ"))
            if age < max_age_hours * 3600:
                return meta
        except Exception:
            pass
    try:
        # Import lazily: this module is also imported by the console image
        # (read-only side), where digest.py's env requirements (VKs, GitHub)
        # must NOT be triggered just to read the cache.
        import digest as D
        url = f"{D.BIFROST_URL}/v1/models"
        raw = D.http_json("GET", url, {"x-bf-vk": D.VK,
                                       "Content-Type": "application/json"},
                          timeout=30)
        models = MODELS.parse_catalog(raw)
        if not models:
            log("model catalog: empty response — keeping previous cache")
            return meta
        doc = write_cache(state_dir, models, url)
        log(f"model catalog: {len(models)} model(s) cached "
            f"({sum(1 for m in models if m['local'])} local)")
        return doc
    except Exception as exc:
        log(f"model catalog: fetch FAILED ({str(exc)[:140]}) — keeping "
            f"previous cache")
        return meta


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    state_dir = os.environ.get("DIGEST_STATE_DIR", "/state")
    doc = refresh(state_dir, force="--force" in argv)
    print(json.dumps(doc, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
