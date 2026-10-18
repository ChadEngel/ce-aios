# aios-mail-digest — R0 mailbox digest + filing tier (issue #50)

Fires at **05:00, 12:00, 17:00 America/Chicago** (`timeZone`-pinned). Each run:

1. Reads inbox (top `DIGEST_SCAN_TOP` = 50, read-or-unread) via Bifrost →
  `graph-list-mail-messages` (read compartment VK).
2. Classifies **deterministically in code** — never by the model:
   - **Priority** = mail from people Chad has sent email to (sent-items
     tier), senders on `DIGEST_PROTECTED_DOMAINS` (engelmn.com, alohamn.com,
     crookedgate.com — family/personal domains, never filed or deleted), or
     keyword hits (`DIGEST_PRIORITY_KEYWORDS`: bluff creek, ccha, crossbar,
     bearpath, flag football, cc united). Stays in the inbox; re-alerts every
     fire while unread.
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

Wrongly-filed mail can be dragged back in Outlook manually, and the pattern
lists are env-tunable in the CronJob without a rebuild.

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

1. **Build the image (amd64 — workers run it):**
   `docker buildx build --platform linux/amd64 --load -t caehomelab/aios-mail-digest:0.3.4 aios/mail/digest/`
2. **Import on BOTH amd64 workers** (k3s import is node-local; the job has no
   PVC and `nodeSelector: kubernetes.io/arch: amd64`):
   ```bash
   docker save caehomelab/aios-mail-digest:0.3.4 | \
     ssh cengel@192.168.30.60  'sudo -n k3s ctr -n k8s.io images import -'
   docker save caehomelab/aios-mail-digest:0.3.4 | \
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