"""Versioned YAML config store for the mail engine (roadmap M1 step 3).

Precedence (deliberately "merge", not "wholesale replace"):
  config.yaml on the state PVC overrides ENV per-key; absent keys fall back to
  the env-derived defaults. A typo'd or minimal file therefore cannot silently
  SHRINK scan_top back to a default that the k8s manifest had raised — the
  manifest keeps its force. save() always writes the COMPLETE dict, so an
  export is a full snapshot and can round-trip losslessly.

The file is opt-in: if config.yaml does not exist, behaviour is byte-identical
to the env-only deployment (that is how this refactor ships with zero
behavioural change). The journal (D5) records every save: actor, timestamp,
config hash, full config — append-only JSONL, same durability rules as the
ledger (fsync before returning, atomic rewrites, torn tail tolerated).

The `version` field is a short hash of the canonical config; every ledger
intent row records it, so "why was this filed?" stays answerable after any
rule change.
"""
import copy
import dataclasses
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field, fields


def _csv_list(env, default_csv):
    return tuple(s.strip().lower() for s in
                 os.environ.get(env, default_csv).split(",") if s.strip())


def _csv_int(env, default):
    return int(os.environ.get(env, str(default)))


# ---------------------------------------------------------------------------
# The rule schema. Field names mirror the env names they shadow (minus the
# DIGEST_ prefix) so the manifest ↔ config mapping is greppable.
# ---------------------------------------------------------------------------
@dataclass
class Config:
    # Destination folders (renamable per user — D4: one box, one mailbox)
    filed_folder: str = "AIOS-Filed"
    receipts_folder: str = "Receipts"
    orders_folder: str = "Orders"

    # Retention + scan sizing
    filed_days: int = 10          # 0 = keep AIOS-Filed forever (never clear)
    scan_top: int = 50            # digest listing window (manifest raises it)
    sent_top: int = 250           # sent-folder lookback for the contacts tier
    preview_chars: int = 400

    # Priority tier: sent-contacts + keyword hits (subject/sender only)
    priority_keywords: tuple = ()
    protected_domains: tuple = ()   # never filed, never deleted (active vendors)
    contact_excludes: tuple = ()    # sent-contacts opt-out (e.g. ui.com)

    # Digest self-notification exemption (kept in inbox, never filed, silent)
    notice_senders: tuple = ()
    notice_subjects: tuple = ()

    # Typed routing: regex FRAGMENTS, case-insensitive (fragments, not regexes
    # a user wrote — validated at load; unparseable config never applies)
    receipt_patterns: tuple = ()
    order_patterns: tuple = ()

    # Privacy in-flight tier. Layer 1 deterministic; layer 2 (LLM) live in the
    # app, gated by these flags. The model only ever KEEPS mail (D3).
    privacy_on: bool = True
    privacy_llm_on: bool = True
    privacy_domains: tuple = ()
    privacy_subjects: tuple = ()

    # LLM model ids (OpenAI-compatible id, e.g. "ollama/qwen3.8:latest") for
    # the two model touchpoints. prose_model composes the digest prose;
    # privacy_model runs the privacy keep-pass ("" = same as prose_model).
    # The model NEVER classifies and can only write prose / suggest keeps
    # (D3); fail-safe is structural (privacy pass failure holds candidates,
    # it cannot file or delete). UI: per-user model picker, console M2.
    prose_model: str = "ollama/muse-glimmer:latest"
    privacy_model: str = ""

    # Digest cadence (R1, user-scheduled — never hard-coded). These ship WITH
    # the reconciler that consumes them (digest/schedule_reconciler.py, run on
    # the filer heartbeat): a config key with no consumer would be a silent
    # no-op, so the pair landed together or not at all. Defaults mirror the
    # historic manifest constant so the env-only deployment is unchanged.
    # validate() parses the cron expression + timezone AND enforces the
    # 1-fire/hour floor via engine.schedule.
    digest_schedule: str = "0 5,12,17 * * *"
    digest_tz: str = "America/Chicago"

    # Runtime metadata (not user-editable, not part of the YAML schema)
    source: str = field(default="env", repr=False)  # "env" | "file"

    @property
    def version(self) -> str:
        d = asdict(self)
        d.pop("source", None)
        blob = json.dumps(d, sort_keys=True, ensure_ascii=False)
        return "v" + hashlib.sha256(blob.encode()).hexdigest()[:10]

    def validate(self):
        """Refuse configs that could be destructive (D3): no negatives, no
        empty folder names, patterns must compile as regex fragments."""
        for f in fields(self):
            v = getattr(self, f.name)
            # annotations may arrive as real type objects OR strings
            # depending on the runtime — handle both (the ("int", float)
            # form silently matched NEITHER in 0.5.x; caught by the console
            # selftest 2026-10-21).
            if f.type in (int, float, "int", "float") and isinstance(v, int):
                if v < 0:
                    raise ValueError(f"{f.name} cannot be negative (destructive)")
        for k in ("filed_folder", "receipts_folder", "orders_folder"):
            if not str(getattr(self, k)).strip():
                raise ValueError(f"{k} must be a non-empty folder name")
        for pk in ("receipt_patterns", "order_patterns", "privacy_subjects"):
            for pat in getattr(self, pk):
                re.compile(pat)  # raises ValueError on garbage
        # Schedule (R1): parse cron + timezone + the 1-fire/hour floor. A
        # schedule that cannot fire, or fires 60x/hour, is refused HERE — so
        # it can never be saved and can never reach the CronJob reconciler.
        # ScheduleUnavailable (croniter missing) is NOT an invalid schedule:
        # the digest/console do not consume the cadence, so a missing test
        # dependency must never stop mail from being filed.
        from engine import schedule as _sch
        try:
            _sch.validate_schedule(self.digest_schedule, self.digest_tz)
        except _sch.ScheduleUnavailable:
            pass
        # Models (R2): a REMOTE model id is refused for BOTH legs — mail
        # content must never leave the cluster, and the privacy keep-pass
        # sends sender/subject text to the model. Locality is structural (a
        # provider prefix), not a UI hint, so it holds however the id was set.
        from engine import models as _models
        _models.validate_choice(self.prose_model, "prose")
        _models.validate_choice(self.privacy_model, "privacy")


def asdict(cfg: Config) -> dict:
    d = dataclasses.asdict(cfg)
    return d


def from_dict(d: dict, source="file") -> Config:
    """File/YAML dict → Config. Unknown keys are REJECTED (a config that
    silently ignores a typo'd key is a config that silently does nothing).
    csv strings anywhere a tuple is expected are accepted (user ergonomics)."""
    known = {f.name: f for f in fields(Config) if f.name != "source"}
    # 'source' and 'version' are metadata (runtime / round-trip marker), not
    # schema — ignored here so a saved file can be re-loaded losslessly.
    bad = set(d) - set(known) - {"source", "version"}
    if bad:
        raise ValueError(f"unknown config key(s): {sorted(bad)}")
    clean = {}
    for name, f in known.items():
        v = d.get(name)
        if v is None:
            continue
        if f.type == "tuple" or f.type is tuple:
            if isinstance(v, str):
                v = [s.strip().lower() for s in str(v).split(",") if s.strip()]
            v = tuple(str(s).strip().lower() for s in v) if v else ()
        elif f.type == "bool" or f.type is bool:
            if isinstance(v, str):
                v = v.strip().lower() not in ("0", "false", "no", "off")
            else:
                v = bool(v)
        else:
            v = f.type(v)
        clean[name] = v
    cfg = Config(**clean)
    cfg.source = source
    cfg.validate()
    return cfg


def from_env() -> Config:
    """ENV → Config. Defaults mirror digest.py's historic env defaults
    EXACTLY — this is the byte-for-byte backward-compatibility path."""
    return Config(
        filed_folder=os.environ.get("DIGEST_FILED_FOLDER", "AIOS-Filed"),
        receipts_folder=os.environ.get("DIGEST_RECEIPTS_FOLDER", "Receipts"),
        orders_folder=os.environ.get("DIGEST_ORDERS_FOLDER", "Orders"),
        filed_days=_csv_int("DIGEST_FILED_DAYS", 10),
        scan_top=_csv_int("DIGEST_SCAN_TOP", 50),
        sent_top=_csv_int("DIGEST_SENT_TOP", 250),
        preview_chars=_csv_int("DIGEST_PREVIEW_CHARS", 400),
        priority_keywords=_csv_list(
            "DIGEST_PRIORITY_KEYWORDS",
            "bluff creek,ccha,crossbar,bearpath,flag football,cc united"),
        protected_domains=_csv_list("DIGEST_PROTECTED_DOMAINS", ""),
        contact_excludes=_csv_list("DIGEST_CONTACT_EXCLUDES", ""),
        notice_senders=_csv_list("DIGEST_NOTICE_SENDERS",
                                 "notifications@github.com"),
        notice_subjects=_csv_list("DIGEST_NOTICE_SUBJECTS",
                                  "AIOS daily mail digest"),
        receipt_patterns=_csv_list(
            "DIGEST_RECEIPT_PATTERNS",
            "^receipt,^invoice,^you paid,^payment confirmation"),
        order_patterns=_csv_list(
            "DIGEST_ORDER_PATTERNS",
            "order confirmation,your order,^order #,^order placed,"
            "thanks for your order,thanks for your purchase,order shipped,"
            "shipped:,out for delivery,track your package,package delivered,"
            "has shipped"),
        privacy_on=os.environ.get("DIGEST_PRIVACY_KEEP", "1") != "0",
        privacy_llm_on=os.environ.get("DIGEST_PRIVACY_LLM", "1") != "0",
        prose_model=os.environ.get("DIGEST_MODEL", "ollama/muse-glimmer:latest"),
        privacy_model=os.environ.get("DIGEST_MODEL", "ollama/muse-glimmer:latest"),
        privacy_domains=_csv_list(
            "DIGEST_PRIVACY_DOMAINS",
            "whitepages.com,spokeo.com,beenverified.com,radaris.com,"
            "intelius.com,truepeoplesearch.com,truepeoplesearch.net,"
            "fastpeoplesearch.com,fastpeoplesearch.net,mylife.com,"
            "peoplefinders.com,usphonebook.com,nuwber.com,"
            "instantcheckmate.com,truthfinder.com,peoplelooker.com,"
            "anywho.com,incogni.com,deleteme.com,privacybee.com,kanary.com,"
            "onerep.com,optery.com,easyoptouts.com,reputationdefender.com"),
        privacy_subjects=_csv_list(
            "DIGEST_PRIVACY_SUBJECTS",
            "opt[- ]?out,do[- ]?not[- ]?sell,"
            "(remove|removal|deletion|suppress\\w*) "
            "(request|confirm\\w*|complete\\w*|update|status|has been|was "
            "successful|processed),"
            "(complete|start|begin|process\\w*|confirm) (your |the )?(removal|"
            "deletion|opt|request),"
            "data (removal|deletion|suppression|privacy),"
            "privacy (request|confirm\\w*)"),
        digest_schedule=os.environ.get("DIGEST_SCHEDULE", "0 5,12,17 * * *"),
        digest_tz=os.environ.get("DIGEST_TZ", "America/Chicago"),
        source="env",
    )
    cfg.validate()
    return cfg


CONFIG_FILE = "config.yaml"
JOURNAL_FILE = "config-journal.jsonl"


def default_dir():
    """The DIRECTORY the config store lives in — deliberately SEPARATE from
    the ledger's directory (step 5).

    Why a subdirectory and not just /state: from step 5 the console gets its
    first WRITE path (editing rules in the UI). The console's safety story has
    always been "a bug here cannot corrupt the ledger", enforced by mounting
    /state READ-ONLY. To keep that true while allowing config writes, the
    config store moves into <state>/config and the console mounts THAT
    subdirectory read-write while the ledger's parent stays read-only — so a
    console bug can name /config/... but is structurally unable to open
    /state/ledger.jsonl for writing (proved live: root mount RO denies the
    ledger, the subPath mount allows only the subdir).

    Overridable with DIGEST_CONFIG_DIR (the container sets it to the mount
    path it chose, which may differ per workload — e.g. the console mounts the
    same subdir at /config while the legs reach it at /state/config)."""
    env = os.environ.get("DIGEST_CONFIG_DIR")
    if env:
        return env
    return os.path.join(os.environ.get("DIGEST_STATE_DIR", "/state"),
                        "config")


def config_path(state_dir=None):
    return os.path.join(state_dir or default_dir(), CONFIG_FILE)


def journal_path(state_dir=None):
    return os.path.join(state_dir or default_dir(), JOURNAL_FILE)


def load(state_dir=None, log=None):
    """load() → Config. Resolution: env defaults, overridden per-key by
    config.yaml when present. An INVALID present file falls back to env with
    a loud warning — the fire must not die over config (mail keeps flowing),
    but it must be visible that the file was ignored."""
    print_fn = log or (lambda *a: print("[config]", *a, file=os.sys.stderr,
                                        flush=True))
    state_dir = state_dir or default_dir()
    cfg = from_env()
    path = config_path(state_dir)
    # Legacy-path guard (step 5): the store moved from <state>/config.yaml into
    # the <state>/config/ subdirectory. If an OLD file is found where the new
    # one should be absent, that is a silent file->env behaviour flip — so say
    # so loudly rather than ignore it. Not auto-migrated: that would be a write,
    # and a surprising one; the operator moves it deliberately.
    if not os.path.exists(path) and os.path.basename(state_dir) == "config":
        legacy = os.path.join(os.path.dirname(state_dir), CONFIG_FILE)
        if os.path.exists(legacy):
            print_fn(f"WARNING: legacy config at {legacy} is IGNORED — the "
                     f"store now lives at {path}; move the file to keep its "
                     f"overrides (otherwise env defaults apply)")
    if os.path.exists(path):
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            if not isinstance(raw, dict):
                raise ValueError("config.yaml: top level must be a mapping")
            overrides = from_dict(raw, source="file")
            merged = asdict(cfg)
            merged.pop("source", None)
            merged.update({k: v for k, v in asdict(overrides).items()
                           if k not in ("source", "version")})
            cfg = from_dict(merged, source="file")
            print_fn(f"loaded {cfg.version} (file {path}, overrides "
                     f"{len(raw)} key(s))")
        except Exception as exc:
            print_fn(f"WARNING: {path} unreadable ({str(exc)[:120]}) — "
                     f"falling back to env defaults {cfg.version}")
    cfg.validate()  # paranoia: never hand back an unvalidated config
    return cfg


def save(cfg: Config, actor: str, state_dir=None):
    """Write the complete config.yaml atomically + append the journal row
    (D5). Validation happens in from_dict/validate BEFORE any write. The
    journal entry is fsync'd before save() returns."""
    state_dir = state_dir or default_dir()
    os.makedirs(state_dir, exist_ok=True)
    cfg.validate()
    # Round-trip through dict->Config so what lands on disk is EXACTLY the
    # validated schema (no source field, tuples as yaml lists).
    snap = asdict(cfg)
    snap.pop("source", None)
    cfg2 = from_dict(copy.deepcopy(snap), source="file")
    ver = cfg2.version
    snap["version"] = ver  # informational only; load() recomputes + ignores
    import yaml
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=state_dir, prefix=".config-",
                                   suffix=".yaml.tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(snap, fh, sort_keys=True, allow_unicode=True)
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp creates 0600 owned by the writer. The legs that CONSUME this
        # file may run as a different uid than the console that wrote it (both
        # nobody today, but the console is a separate deployment), so make it
        # world-readable: a config the reader cannot open is the silent
        # file->env flip this store exists to prevent. It holds no secrets
        # (rules/models/folders), so 0644 is correct here.
        os.chmod(tmp, 0o644)
        os.replace(tmp, config_path(state_dir))
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    entry = {"t": "config", "ts": _utcnow(), "actor": actor, "version": ver,
             "config": snap}
    with open(journal_path(state_dir), "a",
              encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return ver


def apply_overrides(current, overrides):
    """Return a validated new Config = current with `overrides` merged on top.

    THE safe-save rule (the one bug this store has repeatedly invited):
    from_dict(partial_dict) resets every ABSENT key to its DEFAULT. So a save
    must always start from the current EFFECTIVE config (which already merged
    env + file) and override only the named keys — never from_dict(overrides)
    alone, which would silently drop every env-raised knob to its default.

    Raises ValueError on an unknown key or a value validate() refuses — the
    same refusal the save path relies on, surfaced BEFORE anything is written.
    """
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be an object")
    merged = asdict(current)
    merged.pop("source", None)
    bad = set(overrides) - set(merged)
    if bad:
        raise ValueError(f"unknown config key(s): {sorted(bad)}")
    merged.update(overrides)
    return from_dict(merged, source="file")


def diff(old: Config, new: Config):
    """Field-level change list between two configs: [{key, from, to}], only
    for keys whose effective value differs. Used by the console to show what a
    Save would change (D5: every edit renders a diff before it lands).

    Boolean/tuple values are returned as-is; the caller formats for display.
    """
    a, b = asdict(old), asdict(new)
    a.pop("source", None)
    b.pop("source", None)
    out = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if va != vb:
            out.append({"key": k, "from": va, "to": vb})
    return out


def history(state_dir=None):
    state_dir = state_dir or default_dir()
    path = journal_path(state_dir)
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # torn tail — same policy as the ledger
    return out


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")