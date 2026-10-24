#!/usr/bin/env python3
"""Offline self-test for the model catalogue cache (R2).

The network fetch is a thin HTTP call; what must be rock-solid is the CACHE
contract, because the console renders it and a malformed/stale cache must
never break the dashboard or (worse) a fire. This exercises write/read, the
atomic replace, the degraded-shape tolerance, and the freshness skip that
keeps the 5-minute heartbeat from hammering the gateway.

    python3 selftest_models.py      (needs croniter? no; pyyaml? no)
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="aios-models-test-")
os.environ["DIGEST_STATE_DIR"] = TMP
for p in (HERE, PARENT):
    if p not in sys.path:
        sys.path.insert(0, p)

import model_catalog as MC        # noqa: E402
from engine import models as MODELS  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


print("== 1. cache write/read round-trip ==")
models = MODELS.parse_catalog({"data": [
    {"id": "ollama/qwen3.8:latest", "context_length": 32000},
    {"id": "openrouter/auto"},
]})
doc = MC.write_cache(TMP, models, "https://llm.caehomelab.com/v1/models")
check("write returns doc with models", len(doc["models"]) == 2, doc)
check("read_cached returns the models", MC.read_cached(TMP) == models)
meta = MC.read_cache_meta(TMP)
check("meta carries ts + url", meta["ts"] and meta["url"].endswith("/v1/models"), meta)

print("== 2. missing/garbage cache degrades, never raises ==")
empty = tempfile.mkdtemp(prefix="aios-models-empty-")
check("absent cache -> []", MC.read_cached(empty) == [])
check("absent cache meta -> safe shape",
      MC.read_cache_meta(empty) == {"ts": None, "url": None, "models": []})
bad = tempfile.mkdtemp(prefix="aios-models-bad-")
with open(MC.catalog_path(bad), "w") as fh:
    fh.write("{ this is not json")
check("torn/garbage cache -> []", MC.read_cached(bad) == [])
check("torn meta -> safe shape", MC.read_cache_meta(bad)["models"] == [])
# the cache is ALWAYS written as the {ts,url,models} wrapper; a bare top-level
# list is not a shape write_cache produces, so it is ignored ([] = don't guess)
with open(MC.catalog_path(bad), "w") as fh:
    json.dump([{"id": "ollama/x"}], fh)
check("bare-list cache ignored (only the wrapper shape is valid)",
      MC.read_cached(bad) == [])
check("bare-list meta -> safe shape",
      MC.read_cache_meta(bad)["models"] == [])

print("== 3. freshness skip: no fetch when the cache is fresh ==")
calls = {"n": 0}


def _boom(*a, **k):
    calls["n"] += 1
    raise AssertionError("refresh() hit the network on a fresh cache")


# Patch the lazy import path indirectly: if the cache is fresh, refresh must
# return BEFORE importing digest. We prove that by making `import digest`
# explode via sys.modules sentinel.
class _Explode:
    def __getattr__(self, name):
        raise AssertionError("digest imported despite fresh cache")


sys.modules["digest"] = _Explode()
fresh = MC.refresh(TMP, log=lambda *a: None)   # fresh (just written)
check("fresh cache -> no network/import", fresh["models"] == models)
del sys.modules["digest"]

print("== 4. stale cache -> attempts fetch, failure keeps old cache ==")
# force staleness by rewriting ts far in the past
old = MC.read_cache_meta(TMP)
old["ts"] = "2000-01-01T00:00:00Z"
with open(MC.catalog_path(TMP), "w") as fh:
    json.dump(old, fh)
# make the fetch fail: module 'digest' with a http_json that raises
class _FailDigest:
    BIFROST_URL = "http://127.0.0.1:1"
    VK = "x"

    @staticmethod
    def http_json(*a, **k):
        raise RuntimeError("simulated network failure")


sys.modules["digest"] = _FailDigest()
got = MC.refresh(TMP, log=lambda *a: None)
del sys.modules["digest"]
check("stale + fetch failure -> previous cache still returned",
      got["models"] == models, got)

print("== 5. stale cache + successful fetch -> cache updated ==")


class _OKDigest:
    BIFROST_URL = "http://gw"
    VK = "x"

    @staticmethod
    def http_json(method, url, headers, body=None, timeout=90):
        return {"data": [{"id": "ollama/new:latest", "context_length": 16000}]}


old["ts"] = "2000-01-01T00:00:00Z"
with open(MC.catalog_path(TMP), "w") as fh:
    json.dump(old, fh)
sys.modules["digest"] = _OKDigest()
got = MC.refresh(TMP, log=lambda *a: None)
del sys.modules["digest"]
check("fetch success -> new model cached",
      [m["id"] for m in got["models"]] == ["ollama/new:latest"], got)
check("cache on disk updated", MC.read_cached(TMP)[0]["id"] == "ollama/new:latest")


class _EmptyDigest:
    BIFROST_URL = "http://gw"
    VK = "x"

    @staticmethod
    def http_json(*a, **k):
        return {"data": []}


old["ts"] = "2000-01-01T00:00:00Z"
with open(MC.catalog_path(TMP), "w") as fh:
    json.dump(old, fh)
before = MC.read_cached(TMP)
sys.modules["digest"] = _EmptyDigest()
got = MC.refresh(TMP, log=lambda *a: None)
del sys.modules["digest"]
check("empty fetch -> previous cache kept (no wipe)",
      MC.read_cached(TMP) == before, got)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
