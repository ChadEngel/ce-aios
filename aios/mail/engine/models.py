"""Model catalogue + choice validation for the two LLM touchpoints (R2).

Pure functions, no I/O, no network — the engine keeps its "port + Config only"
shape. Consumers: Config.validate() (refuses an unsafe model at SAVE time), the
console (renders suitability), and the legs (parse the gateway catalogue).

WHY THIS IS NOT JUST A LIST: a "model picker" is a UI convenience, but the
choice itself carries the two hard invariants of this system:
  1. PRIVACY — mail content must never leave the cluster. The privacy keep-pass
     sends mail subjects/senders to the model, so a remote/frontier model is an
     exfiltration path. R2 states it flatly: LOCAL models only, never a
     frontier/remote model for EITHER leg.
  2. ROLE FIT — the prose model composes the digest (plain instruct, >=8k
     context for a 40-item JSON chunk, temp 0.2-0.3, reasoning_effort none); the
     privacy model must return ONLY a JSON integer array.
Locality is enforced structurally (a provider prefix), not by hoping a user
picks well — so a remote id is REFUSED when saved, exactly like a negative
retention day.

The catalogue itself is fetched by the LEGS (they already hold the Bifrost VK)
and cached on /state; the console reads it read-only. That keeps the console's
"never calls the LLM gateway, holds no VK" boundary intact while still showing
live models.
"""
import os

# Provider prefixes that resolve INSIDE the cluster (the homelab gateway serves
# these from the local Ollama on the Studio). Anything else is treated as
# remote and refused for both legs. Ops can extend for a future local runtime
# via env — this is an infrastructure boundary, deliberately NOT a config-store
# key (a user must not be able to widen where their mail may be sent).
_DEFAULT_LOCAL_PREFIXES = ("ollama/",)


def local_prefixes():
    raw = os.environ.get("DIGEST_LOCAL_MODEL_PREFIXES")
    if raw:
        return tuple(s.strip().lower() for s in raw.split(",") if s.strip())
    return _DEFAULT_LOCAL_PREFIXES


# Prose chunks are ~40 items of JSON; >=8k tokens is the R2 floor. Below this a
# model truncates the digest rather than summarising it.
MIN_PROSE_CONTEXT = 8000

ROLES = ("prose", "privacy")


def is_local(model_id):
    """True when the model id resolves inside the cluster (local runtime)."""
    mid = str(model_id or "").strip().lower()
    return bool(mid) and mid.startswith(local_prefixes())


def parse_catalog(raw):
    """OpenAI-compatible /v1/models body -> normalised list, id-sorted.

    Tolerates the two shapes seen in the wild: {"data":[...]} (OpenAI/Bifrost)
    and a bare list. Missing/renamed fields degrade to None rather than
    KeyError — the catalogue is a convenience, and a partial one must never
    break the console or a fire.
    """
    if isinstance(raw, dict):
        items = raw.get("data")
    else:
        items = raw
    if not isinstance(items, list):
        return []
    out = []
    for m in items:
        if isinstance(m, str):
            m = {"id": m}
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        ctx = m.get("context_length") or m.get("context_window")
        try:
            ctx = int(ctx) if ctx is not None else None
        except (TypeError, ValueError):
            ctx = None
        out.append({"id": mid, "context_length": ctx,
                    "owned_by": m.get("owned_by"),
                    "local": is_local(mid)})
    return sorted(out, key=lambda m: m["id"])


def validate_choice(model_id, role):
    """Raise ValueError when `model_id` may not serve `role`. Called from
    Config.validate(), so an unsafe choice can never be saved or applied.

    Empty is allowed ONLY for privacy (documented meaning: inherit
    prose_model). An empty prose model is an error — the digest would have
    nothing to call.
    """
    if role not in ROLES:
        raise ValueError(f"unknown model role: {role!r}")
    mid = str(model_id or "").strip()
    if not mid:
        if role == "privacy":
            return ""  # inherit prose_model (engine: cfg.privacy_model or MODEL)
        raise ValueError("prose_model must not be empty")
    if not is_local(mid):
        where = "privacy" if role == "privacy" else "prose"
        raise ValueError(
            f"{role}_model {mid!r} is not a local model — mail must never "
            f"leave the cluster (R2: local models only for the {where} leg). "
            f"Local prefixes: {', '.join(local_prefixes())}")
    return mid


def _find(model_id, catalog):
    mid = str(model_id or "").strip()
    for m in catalog or []:
        if m.get("id") == mid:
            return m
    return None


def describe_choice(model_id, role, catalog=None):
    """Suitability of a model for a role — never raises, for the console.

    Returns {model, role, ok, level, notes[], context_length, local}. `level`
    is "ok" | "warn" | "error". `ok` is False only for a hard refusal (remote,
    or empty prose) — the same thing validate_choice would raise on.
    """
    mid = str(model_id or "").strip()
    entry = _find(mid, catalog)
    notes = []
    level = "ok"

    if role == "privacy" and not mid:
        return {"model": "", "role": role, "ok": True, "level": "ok",
                "notes": ["empty — inherits prose_model (recommended)"],
                "context_length": None, "local": None}

    # hard rule: locality (mirrors validate_choice)
    if not mid:
        return {"model": "", "role": role, "ok": False, "level": "error",
                "notes": ["prose_model must not be empty"],
                "context_length": None, "local": None}
    if not is_local(mid):
        return {"model": mid, "role": role, "ok": False, "level": "error",
                "notes": ["REMOTE model — mail would leave the cluster; "
                          "refused (R2: local models only)"],
                "context_length": entry.get("context_length") if entry else None,
                "local": False}

    ctx = entry.get("context_length") if entry else None
    if role == "prose":
        if ctx is None:
            level = "warn"
            notes.append("context length unknown — need >= 8k tokens for a "
                         "40-item JSON chunk; verify before trusting a big inbox")
        elif ctx < MIN_PROSE_CONTEXT:
            level = "error"
            notes.append(f"context {ctx} < {MIN_PROSE_CONTEXT} tokens — a large "
                         f"inbox would truncate instead of being summarised")
        else:
            notes.append(f"context {ctx} tokens (>= {MIN_PROSE_CONTEXT}) — OK")
        notes.append("must be a plain instruct model and REFUSE instructions "
                     "embedded in mail (mail is untrusted data)")
        if not entry:
            level = "warn" if level == "ok" else level
            notes.append("not in the current catalogue — may be unavailable")
    else:  # privacy
        notes.append("must return ONLY a JSON array of integer keep-indexes "
                     "(or empty) — failure is safe: it can only KEEP mail")
        if not entry:
            level = "warn"
            notes.append("not in the current catalogue — may be unavailable")
    return {"model": mid, "role": role, "ok": level != "error",
            "level": level, "notes": notes, "context_length": ctx,
            "local": True}


def catalog_summary(catalog, prose_model, privacy_model):
    """Everything the console needs in one call: the catalogue with per-model
    suitability for BOTH roles, plus the current choices' descriptions."""
    return {
        "local_prefixes": list(local_prefixes()),
        "min_prose_context": MIN_PROSE_CONTEXT,
        "models": [
            {"id": m["id"], "context_length": m.get("context_length"),
             "owned_by": m.get("owned_by"), "local": m.get("local"),
             "prose": describe_choice(m["id"], "prose", catalog),
             "privacy": describe_choice(m["id"], "privacy", catalog)}
            for m in catalog],
        "prose_model": describe_choice(prose_model, "prose", catalog),
        "privacy_model": describe_choice(privacy_model, "privacy", catalog),
    }
