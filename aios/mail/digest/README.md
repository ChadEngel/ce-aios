# aios-mail-digest — R0 mailbox digest + filing tier (issue #50)

Fires at **05:00, 12:00, 17:00 America/Chicago** (`timeZone`-pinned). Each run:

1. Reads inbox (top `DIGEST_SCAN_TOP` = 50, read-or-unread) via Bifrost →
  `graph-list-mail-messages` (read compartment VK).
2. Classifies **deterministically in code** — never by the model:
   - **Priority** = mail from people Chad has sent email to (sent-items
     tier), senders on `DIGEST_PROTECTED_DOMAINS` (engelmn.com, alohamn.com,
     crookedgate.com — family/personal domains, never filed or deleted), or
     keyword hits (`DIGEST_PRIORITY_KEYWORDS`: bluff creek, ccha, crossbar,
     bearpath, flag football, cc united). Stays in the inbox; reported ONCE
     (see NEW-ONLY reporting below) rather than re-alerting every fire.
   - **Receipts** (subject/sender matches `DIGEST_RECEIPT_PATTERNS`) → moved
     to the **`Receipts`** folder.
   - **Orders** (matches `DIGEST_ORDER_PATTERNS`) → moved to the **`Orders`**
     folder. Reads and unread alike — these are pattern-locked, so they file
     at the next fire regardless of read state, no grace period.
   - **Everything else non-priority** → moved to **`AIOS-Filed`**, but only
     AFTER it has been reported in an earlier fire (grace >= 1 fire, ~5–12h
     inbox residency). Chad's directive 2026-10-17: production digest with
     discarded mail left in a folder, priority kept in the inbox.
3. Summarizes the prose tiers with LOCAL `ollama/muse-glimmer`
   (`reasoning_effort: none`; the model never classifies files or moves mail).

   Scanning is INBOX-FOLDER-SCOPED (`list-mail-folder-messages` with the Inbox
   folder id). The generic `list-mail-messages` tool hits `/me/messages` = the
   ENTIRE mailbox newest-first and silently ignores any `mailbox` argument —
   using it caused whole-mailbox scans that re-processed already-filed folder
   contents (fixed 2026-10-17 after a live bulk-cleanup run surfaced it).
4. Posts ONE comment to [issue #50](https://github.com/ChadEngel/ce-aios/issues/50)
   via the AIOS GitHub App: model prose + a deterministic **Filed** block
   (counts + senders/subjects per folder) + the hidden
   `<!--aios-tracker|YYYY-MM-DD|ids:…-->` ledger (dedupe today; grace across days).
5. Retention: every fire, `AIOS-Filed` items older than `DIGEST_FILED_DAYS`
   (10) are **soft-deleted** — Graph DELETE moves them to Deleted Items, where
   they remain recoverable. Deletes are constrained to the folder-scoped
   listing of `AIOS-Filed` and age-filtered inside the Graph query.

Wrongly-filed mail can be dragged back in Outlook manually. The pattern lists
are env-tunable — as of 0.5.3 the rule values live in ONE object:
`k8s/rules-configmap.yaml` (ConfigMap `aios-mail-rules`), which the digest,
filer, and console all `envFrom` — a rule is edited in one file (no rebuild)
and all three legs derive the identical config version byte-for-byte.

## NEW-ONLY reporting (0.5.4, 2026-10-22)

**Bug:** every fire re-listed the whole inbox instead of the new mail. Cause: 
priority mail stays in the inbox by design, and the `fresh` predicate in
`main()` had NO "already reported" gate for the priority tier — so each
priority item re-appeared on every fire, forever. That grew the comment past
the self-imposed 4000-char cap, and because the hidden dedupe tracker line was
appended LAST, truncation erased it — so the NEXT fire could not tell what had
already been reported and the loop compounded.

**Fixes (all in `digest.py`):**
1. `select_fresh()` gates EVERY tier on "not yet reported" (priority, privacy,
   junk). Priority now reports **once**. Read state is no longer consulted:
   the old "re-alert while unread" rule was the re-index itself, and it also
   meant junk read in Outlook before the fire was never reported — and junk
   only files after being reported, so it would have sat in the inbox forever.
2. `comment_body()` is TRACKER-FIRST: the hidden `<!--aios-tracker|…-->` line
   sits right after the header and only the model PROSE is ever trimmed, so
   the dedupe ledger and the Filed block always survive truncation.
3. `MAX_COMMENT_CHARS` raised 4000 -> 60000 (GitHub allows 65536); bytes were
   never the model's fault. Env-tunable via `DIGEST_MAX_COMMENT_CHARS`.
4. Dedupe now has a DURABLE second source: `ledger_mark_reported()` flags every
   reported id in the ledger (not just filer-originated rows), and
   `ledger_reported_ids()` feeds the dedupe set. The ledger survives a
   truncated comment AND a state-volume rebuild; the tracker survives a
   ledger wipe. Union of both = "only new mail" holds when either is damaged.
5. `tracker_ids()` pages to the END of the thread (~1000 comments, bounded):
   the GitHub comments API returns OLDEST-first and **ignores** `sort`/`direction`
   (verified live 2026-10-22: `desc` and `asc` return the same order), so the
   old `per_page=60` was reading a frozen old window that would have silently
   re-reported everything once the thread passed 60 comments (~3 weeks at
   3 fires/day).

Self-test: `selftest_digest.py` (32 checks) — reported items do not re-list,
new items report exactly once, duplicates collapse, the tracker and Filed block
survive truncation, and the ledger `reported` flag round-trips.

## Filer hand-off: quiet windows are not silent (0.5.5–0.5.6, 2026-10-22)

**Bug (pre-existing, surfaced by the 0.5.4 verification):** mail the FILER
handled was never summarised if it arrived in a window where the 3x/day fire
had nothing else to say. The filer (every 5 min) MOVES mail out of the inbox,
so the digest can only learn about it from the ledger — but the post-skip
condition in `main()` consulted only the inbox-derived tiers (`fresh`/`plan`/
`privacy_new`). A window with 19 filer-filed items therefore logged
`nothing new since last fire — skipping post` and posted nothing, leaving those
rows unreported (and, because the skip returns before the cursor advance, the
backlog re-accumulated).

**Fixes (all in `digest.py`):**
1. New filer-filed ledger rows are a **post trigger**. A filer-only fire posts
   just the deterministic Filed block — the prose model is not called at all
   (`summary` falls back to "No prose this fire …" because `fresh` and
   `privacy_new` are both empty), so a genuinely idle window stays quiet while
   the 5-min leg's work is never silently dropped. The all-clear branch is
   gated the same way, so "inbox is clear" cannot mask a filer-filed backlog.
2. `ledger_new_since()` now returns the **field-merged** row per id, not the
   bare outcome row. The filer writes an INTENT row (sender/subject/preview)
   write-ahead and a bare `{t,id,folder,status}` OUTCOME row on success; the
   Filed block rendered the outcome row, so every filer-handled line read
   `unknown — ;`. The content was on disk the whole time, under a different key.
3. The `reported` check moved to the merged row. `ledger_mark_reported()`
   appends its OWN row, so the outcome row never carries the flag — checking it
   in isolation made the inclusive (`>=`) cursor re-admit rows sitting on the
   cursor second on every fire.
4. `comment_body()` resolves the sender through `_sender_name()`, which reads
   the classified shape (`from.emailAddress.name`) and falls back to the flat
   ledger keys (`sender_name`/`sender_addr`) — so neither shape can render
   `unknown` when the sender is known.
5. A SKIPPED fire still advances the report cursor (`_advance_cursor()`). Some
   ledger rows can never be reported — the digest's own notification mail is
   `_self_notice` and excluded from every tier — so without this the cursor
   stayed pinned behind them and every later fire re-scanned a growing
   backlog. Safe because `reported`, not the cursor, is the dedupe gate.

## User-scheduled cadence (R1, 0.6.0, 2026-10-23)

The digest cadence used to be a constant in `cronjob.yaml`. R1 moves it into
the versioned config store as `digest_schedule` + `digest_tz`, and adds a
reconciler that keeps the CronJob in sync — so "I want my digest at 7am and
6pm in my timezone" is a config change the system self-applies, not a hand
edit of a manifest.

**How it fits together:**
- `engine/schedule.py` — the ONE definition of a valid cadence: croniter
  parse + timezone resolve + a **1-fire/hour floor**. Called from
  `Config.validate()`, so an invalid or hammering schedule is refused at SAVE
  time (the console's save path) and can never be persisted or applied.
- `digest/schedule_reconciler.py` — runs on the **filer's 5-min heartbeat**
  (called from `filer.py`, before paging). GET the digest CronJob, compare
  schedule+tz, PATCH only on a diff (idempotent; a matching schedule is
  silent). Standalone for debugging: `python3 schedule_reconciler.py
  [--dry-run]`.
- **Fail-safe:** every error is caught. A failed read/patch logs loudly and
  leaves the CronJob on its PREVIOUS schedule; the digest may pause, but
  filing and retention (the filer's job) are never affected. An invalid
  config schedule is refused before any API call.
- **RBAC** (`k8s/schedule-rbac.yaml`): a ServiceAccount `aios-mail-filer`
  bound to a Role granting `get`+`patch` on **exactly** `batch/cronjobs:
  aios-mail-digest` (`resourceNames`). No create/delete, no other object,
  namespace-scoped. The filer also gets its own NetworkPolicy egress on 6443
  for the API server (it had none before).
- **DST correctness:** fires are computed in the schedule's wall-clock and
  localized, because croniter's tz-aware mode drifts (a "5am" daily fire
  becomes 6am on the US fall-back day). k8s `timeZone` does NOT drift, so a
  naive preview would show times the CronJob never fires. Self-tested across
  the Nov 2026 transition.
- **Console:** the cadence + next-5-fires render read-only on the dashboard
  (the console still has NO write path — it reports what the reconciler will
  enforce; the schedule is editable only in the config store / a future UI).

Self-test: `selftest_schedule.py` (14 checks) — pure plan is loop-free,
idempotent, patches once on drift, and every error path leaves the CronJob
alone (stub API server). Engine selftest adds the schedule-validation block
(invalid cron/tz, >1/hour refused, hourly allowed, DST pinned).

## Model catalogue + local-only picker (R2, 0.6.1, 2026-10-24)

Two model touchpoints already existed (`prose_model`, `privacy_model` in the
config store since 0.5.1); R2 makes the CHOICE safe and visible.

**The invariant this enforces:** mail content must never leave the cluster.
The privacy keep-pass sends sender/subject text to the model, so a remote model
is an exfiltration path — R2 states flatly that BOTH legs are local-only.
Locality is enforced **structurally** (a provider prefix, `ollama/` by default),
not by hoping a user picks well: `engine/models.py` `validate_choice()` is called
from `Config.validate()`, so a remote id is **refused at save time** exactly
like a negative retention day. An empty `privacy_model` still means "inherit
prose_model"; an empty `prose_model` is refused.

**Suitability, not just a list:** `describe_choice()` classifies a model for a
role — `error` for remote, or for prose with context < 8000 tokens (a big inbox
would truncate instead of being summarised); `warn` when the context is unknown
or the id is not in the current catalogue; `ok` otherwise. The console shows the
same verdicts the save path enforces, so the UI cannot offer a choice the engine
would reject.

**Who fetches what (boundary preserved):** the console's rule is "holds no
Bifrost VK, never calls the LLM gateway". So the **filer** (which already holds
the VK) fetches `/v1/models` on its heartbeat and caches it to
`/state/model-catalog.json`; the console reads that cache read-only. A fetch
failure or empty response leaves the previous cache (a stale catalogue beats
none and never blocks a fire); the cache is skipped entirely when < 24h old, so
the 5-minute heartbeat does not hammer the gateway. Ops can extend the local
prefix set via `DIGEST_LOCAL_MODEL_PREFIXES` — deliberately env/infra, not a
config-store key: a user must not be able to widen where their mail may be sent.

Self-test: `selftest_models.py` (14 checks) — cache round-trip, atomic write,
degraded/garbage tolerance, freshness skip (no network on a fresh cache), and
fetch-failure/empty-response keeping the prior cache. Engine selftest adds the
locality + catalogue-parsing block; console selftest adds the models view and
remote-model refusal.

## Privacy in-flight tier (0.3.3, 2026-10-18)

While Chad is actively clearing personal data from broker sites, opt-out /
removal confirmations come from unpredictable senders — and a filed-away
confirmation = a silently failed removal. Two layers protect them:

1. **Deterministic**: `PRIVACY_DOMAINS` (known broker/people-search sender
   domains) plus high-precision subject patterns (`PRIVACY_SUBJECTS`:
   opt-out, do-not-sell, removal/deletion request|confirmation, etc.).
   Both env-tunable (`DIGEST_PRIVACY_DOMAINS`, `DIGEST_PRIVACY_SUBJECTS`).
2. **Model-suggested keeps**: every fire, the model (muse-glimmer) is asked
   which of the would-be-filed mail looks like data-removal correspondence.
   **Asymmetry rule: the model can only KEEP mail — its output is mapped to
   "never file, never delete" and nothing else.** Worst case of a false
   positive = the mail stays in the inbox (the pre-AIOS safe default); the
   model can never widen filing. Kept mail is reported in its own
   "**Privacy (kept in inbox)**" digest section while present.

On model failure the digest **fails safe**: would-be-filed candidates are
held out of this fire's filing plan and re-checked next fire (they are still
reported). Toggles: `DIGEST_PRIVACY_KEEP=0` disables the tier entirely;
`DIGEST_PRIVACY_LLM=0` keeps only the deterministic layer. This is the first
LLM-influenced classification in the engine and is deliberately one-sided.

## Two-compartment VK design (R2 promotion, deliberate write grant)

- **Read compartment** (`BIFROST_VK_AIOS_HELPDESK`): prose/listing reads;
  unchanged since T-M2.
- **File compartment** (`BIFROST_VK_AIOS_HELPDESK_FILE`): execute list is
  EXACTLY create-mail-folder, move-mail-message, delete-mail-message,
  list-mail-folders, list-mail-folder-messages. **No send** (mailbox
  guardrails: never send, never hard-delete — delete is the soft Graph delete
  to Deleted Items, folder-scoped and aged). If a message id ever reaches
  this VK from outside the AIOS-Filed listing, it can only be moved or softly
  deleted — the blast radius is one mailbox's folder structure.

On the daemon side the surface is pinned by `ENABLED_TOOLS` (regex in
aios/mail/k8s/deployment.yaml): 12 mail reads + the 3 write verbs.
Send/draft/reply/forward/rule/folder-delete/attachment verbs and `graph-batch`
are unreachable even at the daemon level.

## Deploy checklist (ordered)

0. **State volume rule — the PVC that backs `/state` is never deleted or
   recomputed.** Incident 2026-10-21: in the 0.5.x rollout the state PVC was
   deleted and recreated (new uid 14:23:24Z); the `nfs-client` StorageClass
   has `archiveOnDelete: "false"`, so its historic ledger, config journal,
   and cursor were erased beyond recovery. Mail itself was untouched and
   every safety gate degraded the SAFE way (items not provably ledger-
   confirmed are skipped by retention forever), but the audit trail predating
   Oct 21 14:23Z is gone. Compensations now shipped: `state.compact()` keeps
   a rolling `ledger-prev.jsonl` copy before every rewrite (self-tested),
   and volume lifecycle edits are off-limits for this PVC.

1. **Build the image (amd64 — workers run it):**
   `docker buildx build --platform linux/amd64 --load -t caehomelab/aios-mail-digest:0.6.0 --file aios/mail/digest/Dockerfile aios/mail/`
2. **Import on BOTH amd64 workers** (k3s import is node-local, and
   `nodeSelector: kubernetes.io/arch: amd64` pins both legs to those nodes):
   ```bash
   docker save caehomelab/aios-mail-digest:0.6.0 | \
     ssh cengel@192.168.30.60  'sudo -n k3s ctr -n k8s.io images import -'
   docker save caehomelab/aios-mail-digest:0.6.0 | \
     ssh cengel@192.168.30.251 'sudo -n k3s ctr -n k8s.io images import -'
   ```
3. **Human prerequisites (Chad, once):**
   - Re-run the device-code login WITHOUT `--read-only` (scope match with the
     new boot flags — see aios/mail/README.md step 4) and rollout-restart the
     mail daemon.
   - Create the Bifrost virtual key `aios-helpdesk-file` (execute list = the
     five file-compartment tools above), put its key into Infisical
     `jarvis-jatg` (prod) as `BIFROST_VK_AIOS_HELPDESK_FILE`. (Same visit:
     optionally narrow the read VK `aios-helpdesk`'s execute list to the 12
     mail reads.)
4. **Sync + apply:** `kubectl kustomize aios/mail/digest/k8s | ssh util-server kubectl apply -f -`
   → Secret `aios-mail-digest-secrets` now carries FIVE keys
   (+`BIFROST_VK_AIOS_HELPDESK_FILE`). Wait ~60s for the operator sync.
5. **Probe folder access BEFORE a real fire** (dry-run the compartments):
   ```bash
   kubectl -n ai create job --from=cronjob/aios-mail-digest aios-mail-digest-manual
   kubectl -n ai logs job/aios-mail-digest-manual
   kubectl -n ai delete job aios-mail-digest-manual
   ```
   Expect: `file:` log lines, `Orders`/`Receipts`/`AIOS-Filed` folders
   materialized, and the **Filed** block on #50. A second manual fire right
   after should report `filing plan: fewer/no items` (dedupe + grace working).
6. **Retention dry-run:** move (or wait for) an item in `AIOS-Filed`, set
   `DIGEST_FILED_DAYS=0` temporarily via
   `kubectl -n ai set env cronjob/aios-mail-digest DIGEST_FILED_DAYS=0`,
   fire once manually, confirm the item lands in Deleted Items, then reset
   with `kubectl -n ai set env cronjob/aios-mail-digest DIGEST_FILED_DAYS-`.
7. **Verify guards:** the mail daemon pod must ONLY expose the narrowed tool
   list (tools/list through each VK, visible in the Bifrost dashboard shows
   the pin); egress netpol unchanged.

## Digest delivery exemption (0.3.7, 2026-10-21)

Chad reads his updates through GitHub's notification email for issue #50 —
which the filing tier was sweeping into `AIOS-Filed`. The system was filing
the mail that announced it had run.

`DIGEST_NOTICE_SENDERS` (default `notifications@github.com`) +
`DIGEST_NOTICE_SUBJECTS` (default `AIOS daily mail digest`) match on sender
and subject only, never `bodyPreview` — GitHub notification mail **quotes**
the digest, so a body match would over-match unrelated GitHub mail. Sender
matching is exact (or domain-wide when the env value is a bare domain like
`github.com`); a substring test would let `xnotifications@github.com` through.

Matching mail is kept **silently**, not classified priority: it arrives
unread and priority re-alerts while unread, so a plain priority rule would
make every fire narrate its own delivery mail forever — the same
self-referential loop the classifier's no-`bodyPreview` rule already guards
against. Verified live: `digest notices kept: 5`, zero self-references in
the prose, 0 filed.

⚠️ This is a **delivery-location** fix, not a delivery guarantee. Any channel
that lands in this managed mailbox is subject to the filing tier; the
exemption is scoped to this one sender+subject pair only.

## Failure modes to watch (R0 review window, ref #21)

- `Failed to acquire token` → login/daemon scope mismatch (login MUST be
  re-run without `--read-only` after the boot-flag change).
- `MCP error on move-mail-message[file]` → the file VK is missing/wrong
  execute list (check Bifrost dashboard → governance → `aios-helpdesk-file`).
- Move fails but digest posts → per-move failures log and retry next fire;
  counts in the Filed block reflect only successful moves.
- Silent no-comment → job logs on the node; Loki carries stdout, no bodies.
- `clear_filed` deleting nothing while items aged → check folder name case
  (`AIOS-Filed` exact) and that `DIGEST_FILED_DAYS` is not inflated.