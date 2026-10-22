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

Self-test: `selftest_digest.py` (21 checks) — reported items do not re-list,
new items report exactly once, duplicates collapse, the tracker and Filed block
survive truncation, and the ledger `reported` flag round-trips.

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
   `docker buildx build --platform linux/amd64 --load -t caehomelab/aios-mail-digest:0.4.0 aios/mail/digest/`
2. **Import on BOTH amd64 workers** (k3s import is node-local, and
   `nodeSelector: kubernetes.io/arch: amd64` pins both legs to those nodes):
   ```bash
   docker save caehomelab/aios-mail-digest:0.4.0 | \
     ssh cengel@192.168.30.60  'sudo -n k3s ctr -n k8s.io images import -'
   docker save caehomelab/aios-mail-digest:0.4.0 | \
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