# Inbox Console — a per-user management UI + appliance for the mail digest

**Status:** M1 DONE (engine extracted + shipped 0.5.0→0.5.2, 2026-10-21 —
see `helpdesk-mail-agent.md` M1 notes). Read-only console + dry-run preview
SHIPPED 0.6.0, 2026-10-22 (steps 3–4 of the build order: config store ✅ →
cfg-version stamps ✅ → dry-run preview ✅ → read-only UI ✅ → editing LAST).
**INCIDENT 2026-10-21 14:23Z** recorded in §11 (state PVC deleted/recreated
in the 0.5.x rollout; historic ledger unrecoverable; mail untouched).
**Related:** `helpdesk-mail-agent.md` (the live digest agent this manages), `ai-it-team.md` (hire #4), #50 (digest report thread), PR #57 (protected senders + folder-scoped scan), PR #58 (session 9 cont. 6 — the whole-mailbox listing find).

## 1. Why this exists

Every knob of the live digest lives in a k8s manifest + env vars, and every state check is either an ssh script or a comment on an issue. That's survivable for one engineer (Chad) and unreadable for everyone else. Chad's stated end goal: **a packaged appliance — a micro VM any user can run** — that manages their own inbox with the same deterministic tiers, logged in with **Entra ID so the setup knows exactly which user/mailbox it serves.**

The console is the missing management surface. The engine (deterministic classify → file → retain) is already proven; what's missing is: who it's for (identity), rule visibility (see/edit rules safely), and a self-serve package.

## 2. Decisions (agreed up front; `decisions.md` entry to follow at M0)

- **D1 — Entra OIDC replaces the device-code daemon for the appliance.** Delegated MSAL auth (`auth code + PKCE`, localhost + LAN redirect, device-code fallback for headless VMs), direct Microsoft Graph calls. No ms-365-mcp-server dependency. Scopes requested, nothing more: `User.Read`, `Mail.Read`, `Mail.ReadWrite`, **`Contacts.Read`** — and no `Mail.Send` exists at the app-registration level, so the appliance cannot send by construction. `Contacts.Read` also FIXES the contact-proxy gap (today the agent infers contacts from the Sent folder; delegated scope gives the real address book).
- **D2 — One codebase, two deployment targets.** Chad's k3s (instance #1) and the per-user micro VM run the SAME container. Bifrost/VK/GitHub-App remain *adapters*, not dependencies: the engine core speaks plain Graph + config.
- **D3 — Deterministic mutations only.** The LLM never files, moves, or deletes mail (the 0.3.1 boundary). All mailbox mutations = the deterministic engine: moves, and retention = **soft delete only** (to Deleted Items, recoverable). The UI cannot express a hard delete or a send, and refuses configs that approximate it (e.g., retention days cannot be negative/blank-destructive).
- **D4 — One box = one mailbox.** First login binds the config to that UPN; a second user on the same box is refused (use a second box). This is the product boundary and keeps "which user is this setup for" unambiguous.
- **D5 — Config is versioned YAML + an append-only audit journal** on persistent storage. Every UI edit renders a diff, requires Save, and lands in the journal with actor (UPN), timestamp, and config-hash. Export/import buttons for backup/migration.
- **D6 — Self-contained secrets.** Per-user boxes hold only model credentials in a local encrypted store (`systemd-creds`/age on the VM), no third-party secret service required. Chad's lab instance may keep Infisical — that's a lab convenience, not a product dependency. (Deviation from the all-secrets-in-Infisical rule is deliberate and product-scoped; the rule protects the homelab, an appliance per outside user can't ship Infisical.)
- **D7 — Report adapters, pluggable.** webhook (product default), Pushover (home-lab default via `pushover-bridge`), GitHub-issue (today's pattern; optional). The tracker/ledger (`<!--aios-tracker-->` comment tags) generalizes to a local journal.

## 3. Console surface (screens)

0. **Setup wizard (first boot):** Entra tenant/client IDs, redirect URI, test login → UPN binding; prose-model endpoint + key; folder names (`AIOS-Filed`/`Receipts`/`Orders` defaults, renamed per user); seed rules (empty by default; Chad preset ships with his three protected domains + keywords if he chooses the preset); cadence. Device-code fallback when headless.
1. **Dashboard:** identity card ("this setup manages `<UPN>` `<displayName>`"), protection state badge, last/next run, tier counts (priority/protected/receipts/orders/filed), last-fire filing list with **per-item Undo** (move back to inbox — the recovery pass, productized), retention forecast (what the next fire would clear).
2. **Rules:** priority keywords (chips), protected domains (chips), **contacts**: address-book search → explicit keep-list + optional sent-lookback slider (0–500) + Entra-address-book groups; **per-sender overrides** (3-state: keep / file / typed) with inbox-sender search; typed patterns (receipts/orders) with a **live tester** (paste a subject → shows what it matches, editable as plain fragments with safe validation).
3. **Routing & cadence:** destination folder picker (Graph folder list), scan top, grace period, fire schedule (cron-ish with human labels).
4. **Retention:** days (0 = keep forever), per-fire cap, **dry-run preview** ("what the next fire would clear, itemized") + confirm. The full ledger of clears is browsable.
5. **Prompts:** the prose summarizer prompt as an editable, **versioned** config item with documented variables (`{{counts}}`, `{{priority_items}}`, …), a **dry render** against the last run's data (no posting, no model side effects — prose-only boundary stated in the UI), and the model picker (any OpenAI-compatible endpoint; muse-glimmer/Bifrost for Chad).
6. **Reports:** adapter config + test-send button.
7. **Recovery/browser:** paged, searchable `AIOS-Filed` and typed folders; restore-to-inbox per item or batch (soft, reversible; nothing hard-deletes outside the retention engine).
8. **Audit:** the journal — every config diff and mailbox mutation, filterable by date/actor/action.
9. **Assistant panel (v1.5, explicitly gated):** read-only chat over the audit + current stats ("why did X get filed?", "tighten my rules"); responses carry a how-trace; **the model still cannot mutate anything** — it proposes, the user applies. Mirrors the R1 propose-triage-diffs pattern from the manager.

## 4. Architecture

```
[Browser] --https--> [Inbox Console (one container)]
                         |  FastAPI + Jinja2 + htmx (no SPA build)
                         |  APScheduler worker (the digest loop)
                         |  MSAL (delegated tokens, in-memory + encrypted cache)
                         |  sqlite (audit/ledger/versions) + config.yaml (versioned)
                         |  engine = digest.py logic extracted to aios_mail_engine:
                         |    classify() / folder_map() / move / bounded clear_filed()
                         |--- graph.microsoft.com (direct, delegated)
                         |--- prose model: user-configured OpenAI-compatible endpoint
                         |    (Bifrost muse-glimmer for Chad; per-user model choice)
```

- Reuse the proven engine — `digest.py`'s deterministic parts become a small library; the console wraps them with config + audit. The GitHub/ledger coupling stays behind adapters (D7).
- The whole-mailbox listing trap is fixed at the engine layer for good (`list-mail-folder-messages` + real folder ids; enumerate-then-move pattern).
- Container ≈ python-slim, ~180 MB, amd64+arm64.

## 5. Packaging (the micro VM)

- **Level 1 — container** `ghcr.io/caehomelab/aios-inbox-console:x.y`: the deployment target for instance #1 (Chad's cluster) and any container host.
- **Level 2 — appliance** (the per-user product): minimal OS (Fedora CoreOS or Debian 12 + podman) + **Quadlet** `.container` unit + persistent volume dir + ignition/cloud-init that sets: LAN-only firewall (:8080), unattended security updates, RTC, and pulls the pinned container. Deliverable = Packer template in `appliance/` producing a ~2 GB qcow2/ova per hypervisor. 1 vCPU / 512 MB–1 GB RAM is sufficient (no local model in the box).
- **Entra onboarding is scripted**, not manual per user: `appliance/entra-app.sh` (az CLI) creates the app registration with the exact permission set + redirect URIs and prints the wizard inputs. Chad's tenant first; multitenant flag is a later, deliberate step.

## 6. Phases (each with a gate)

- **M0 — proof (1 session):** spec PR (this doc) + tracking issue; `entra-app.sh` scaffold (Chad runs it — he can create app registrations, confirmed 2026-10-04); OAuth PoC: login → UPN card → folder list via delegated Graph. **Gate:** delegated path demonstrably replaces the daemon login.
- **M1 — engine + read-only console (2 sessions):** extract `aios_mail_engine` from digest.py (classify / folder_map / bounded clear / moves), config store + journal scaffold, dashboard read-only against Chad's real mailbox, identity binding. **Gate:** dashboard tier counts match the live digest's last #50 comment.
- **M2 — rules + prompts (2 sessions):** screens 2/3/4/5 with diff-save + live tester + dry-run retention preview. **Gate:** a rule change made in the console changes the next real fire's behavior; prompt dry-render matches what a fire would post.
- **M3 — actions + audit (2 sessions):** scheduler-driven filing, per-item Undo, recovery browser, adapters (webhook + Pushover first; GitHub last). **Gate:** a full fire runs console-driven with a complete audit trail, mailbox end-state identical to a cronjob fire.
- **M4 — appliance (2 sessions):** container publish + Packer/ignition + docs + wizard polish; **pilot instance #2 on a second user's tenant** (family member). **Gate:** a user other than Chad completes setup → first digest → one rule change, without touching the homelab.
- **R0→R1 pattern applies to the product too:** two-week live window for the pilot, failure-mode review, then generalize (multitenant Entra flag, docs site).

## 7. Risks / watch items

- **Graph surface:** `Mail.ReadWrite` delegated = inherent read/write over that mailbox — that IS the product's promise; guards are D3 (no send scope, no hard delete, deterministic-only mutations) and D4 (one mailbox per box). No multi-mailbox or application-level permissions ever.
- **Token cache on the VM** = crown jewel: encrypted at rest (`systemd-creds`/age), disk-only on the appliance volume, documented in the security notes; loss = re-login, not exposure.
- **Conditional Access / tenant policies** on a user's tenant may add consent steps — the wizard surfaces them; failures fail closed (no fallback auth paths that bypass Entra).
- **Engine drift:** console must keep parity tests with the live digest (M1 gate + a shared golden test set of classified subjects).
- **Prompt editing = prose only:** the UI must never let a prompt influence classification/filing (D3); show that boundary where prompts are edited.

## 8. Open questions (Chad)

1. Product name (`aios-inbox-console` placeholder).
2. Public open-source the appliance eventually, or private until proven on instance #2?
3. Who is pilot instance #2?
4. Is LAN-only access right for the appliance, or should it also sit behind Tailscale for remote management?
5. Keep GitHub-issue reports as an adapter only, or retire them once webhook/Pushover cover the home lab?

## 9. UI requirements intake (2026-10-21, Chad)

Two requirements stated directly for the console; both feed the editing
milestones (M2+) as first-class screens.

**R1 — user-scheduled digests (never hard-coded).** ✅ **SHIPPED 0.6.0,
2026-10-23** — built exactly to this spec. `digest_schedule` + `digest_tz`
live in the config store; `engine/schedule.py` validates them at save/load
(croniter + timezone + 1-fire/hour floor); `digest/schedule_reconciler.py`
runs on the filer heartbeat and PATCHes the digest CronJob only on drift
(idempotent, fail-safe: errors leave the previous schedule, never touch
filing/retention). RBAC = get+patch on exactly `batch/cronjobs:
aios-mail-digest` (k8s/schedule-rbac.yaml); filer gets its own netpol with
6443 egress. Console renders the cadence + next-5-fires read-only (still no
write path). DST-corrected (croniter's aware mode drifts across fall-back —
fire times are computed in wall-clock and localized; self-tested). Tests:
selftest_schedule 14, engine +16 (49 total), console +8 (28 total).

Original design notes (still accurate):
- The reconciler runs on the filer's heartbeat (every 5 min): load config,
  compare CronJob spec vs config, PATCH only on diff. Needs an RBAC Role
  scoped to exactly `patch`/`update` on `batch/cronjobs: aios-mail-digest`.
- Fail-safe: patch failure = digest keeps its previous schedule + loud log —
  a schedule edit can pause a digest, never mail filing/retention.
- UI: cron expression field + timezone; validation via croniter at save;
  show the next 5 fires as a preview before saving; guardrails: no more than
  1 fire/hour and no duplicate minute offsets against the filer heartbeart;
  D5 journal records the actor for every schedule change.
- Config-schema note: the fields ship WITH the reconciler only — never inert
  keys (engine/config.py documents this).

**R2 — per-user LLM connection ("more individualized").** ✅ **SHIPPED
0.6.1/0.6.2, 2026-10-24** — `engine/models.py` validates every model choice
at save time: **local-only is structural** (provider prefix, `ollama/` by
default; a remote id is REFUSED for both legs like a negative retention day), empty
privacy inherits prose, empty prose is refused. `describe_choice()` grades
suitability (`error` for remote or prose context <8k; `warn` for unknown
context/absent from catalogue) so the UI shows exactly what the engine
enforces. The catalogue is fetched by the FILER (which holds the gateway key)
on its heartbeat and cached to `/state/model-catalog.json` — the console reads
it read-only, preserving its "no VK, never calls the LLM gateway" boundary.
Console shows current choices + the catalogue with per-model prose/privacy
verdicts. Local prefixes extend via `DIGEST_LOCAL_MODEL_PREFIXES` (env/infra,
not a config key — a user must not widen where mail may go). Tests:
selftest_models 14 (new), engine +27 (76), console +13 (41).

Original design notes (implemented):
- List models from the gateway's `/v1/models` (OpenAI-compatible); show a
  "what this model does here" panel per field (guidance below).
- What the model needs to be able to do:
  * **prose_model** — plain instruct model, OpenAI-compatible
    `/v1/chat/completions`, context >= 8k tokens (a chunk is 40 items JSON),
    temperature 0.2-0.3, `reasoning_effort: none`; must REFUSE instructions
    embedded in mail content (system prompt enforces; mail is untrusted
    data); no tools/multimodal needed. Output = plain text ~1-4k chars/chunk.
  * **privacy_model** — same transport; must return ONLY a JSON array of
    integer indexes (keep list) or an empty list; LOCAL models only (privacy
    invariant: mail never leaves the cluster); misbehaving/failing models
    are structurally safe — keep-pass output can only PROTECT mail; failure
    holds candidates for a later fire (asymmetry rule, D3).
  * Never a frontier/remote model for either leg; never a model with agentic
    tool access (the privacy call already denies MCP tools per request).
- Model choice is per-user: each user's digest gets their own voice/model;
  the ledger's `cfg` version stamp ties every filing decision to the exact
  rules+model set in force when it ran.

## 10. Read-only console shipped (0.6.0, 2026-10-22) + incident

`aios/mail/console/` — `caehomelab/aios-mail-console:0.6.0`, NodePort 30080
in ns `ai`, HTTP Basic (secret `aios-console-auth`), one deployment:
- **Boundaries (D3/D7, enforced in code + manifest):** ZERO mutation
  endpoints; `/state` mounted readOnly; console touches ONLY the ledger via
  the same `state.py` the legs run, and ONLY the engine classifier
  (`engine.classify`) — one source of truth with the fires. Never GitHub,
  never the LLM, never Graph.
- **`GET /api/state`** (auth): ledger truth (items / in-inbox estimate /
  filed-by-folder / unresolved / awaiting-report), effective rules with
  PROVENANCE per key (`file` = config.yaml actually changes the env value,
  `env` = manifest, `default` = engine default), models in use, D5 journal,
  fire status via in-cluster read-only ServiceAccount (Role = get/list
  batch only; API endpoint pinned to the k3s server via `CONSOLE_K8S_URL`).
- **`POST /api/dryrun`** (auth, pure compute — writes NOTHING): a
  hypothetical config (validated; unknown keys/destructive values → 400)
  classified against the newest ledger items and diffed vs CURRENT rules;
  grouped "from → to" with sample subjects. The contacts tier is excluded
  (needs live sent-folder data the ledger doesn't carry) — shown in the UI.
- **Editing is deliberately ABSENT** (build-order step 5, ships after this
  console is proven true over a live window).

### §10b Step 5 — editing UI SHIPPED (console 0.6.3, 2026-10-24)

The console now has **one** write path: the config store. The design keeps the
ledger-untouchable guarantee by **structural isolation**: the config store is a
subdirectory (`<state>/config/`) mounted **separately read-write** at `/config`,
while `/state` stays **read-only**. So a console bug can edit config but cannot
open `ledger.jsonl` for writing, whatever path it builds (proved live: root RO
mount denies the ledger, the subPath mount allows only the subdir).

Endpoints: `POST /api/config/diff` (pure preview, nothing written) and
`POST /api/config/save` (requires `confirm:true`; validates → journals the actor
(D5) → writes the complete snapshot; a no-op writes nothing). `/api/dryrun`
stays the "what would this do to your mail" preview. The UI renders the config
diff and needs an explicit Save; the actor (`CONSOLE_ACTOR`) lands in the
journal. Destructive edits (negative retention, remote models, bad schedules,
unknown keys) are refused with 400 **before** any write. tests: console 65
(was 41).
- Self-tests offline: `selftest_console.py` (20 checks; includes a proof the
  state dir is BYTE-IDENTICAL around every preview), engine 33 (incl. new
  negative-int regression), state 21 (incl. new rolling-backup check).

**Rule identity across legs is now STRUCTURAL:** ConfigMap
`aios-mail-rules` (digest/k8s/rules-configmap.yaml) is envFrom'd by the
digest CronJob, the filer, AND the console — the legs' duplicated literal
rule env blocks were removed (values moved over VERBATIM; live config
version v6635667ca6 unchanged). A fourth consumer without the envFrom
visibly diverges on the console's version chip.

### §11 Incident: state PVC deleted/recreated, 2026-10-21 14:23Z

**What:** during the 0.5.x rollout the `aios-mail-state` PVC was deleted and
recreated (new uid 14:23:24Z; no apply in repo recreates it — the deletion
was a rollout-process failure, unrecorded at the time). The `nfs-client`
StorageClass has `archiveOnDelete: "false"`, so the provisioner ERASED the
volume's historic contents (ledger.jsonl, config-journal.jsonl, cursor).
Earliest surviving ledger row: 2026-10-21T14:26:38Z.
**Blast radius (never-miss invariant):** MAIL AND FOLDERS WERE NEVER TOUCHED
— filing state lives in the mailbox, not the ledger. Every safety gate
failed SAFE: unconfirmed items are skipped by retention clearing forever
(F5 gate), lost outcome-rows made old filed items LESS deletable, not more.
Lost: the pre-Oct-21 audit/explain history; keeps re-announced in the first
post-wipe digest (cursor re-seeded, marked reported by that fire).
**Compensations shipped:** (1) `state.compact()` now preserves a rolling
`ledger-prev.jsonl` copy of the ledger BEFORE every rewrite (self-tested;
a future wipe loses at most one compaction interval, not the trail —
compact() is the ledger's only rewriter); (2) volume lifecycle edits on
this PVC are off-limits; (3) the ledger-check/inspection procedure includes
checking the PVC uid is UNCHANGED.
**Not done (deliberate):** mass-rebuilding confirmations for the pre-wipe
filed items (e.g. the ~20 Receipts) — that would ARM retention clearing on
mail whose rules-era provenance we can no longer prove; decided against.
A read-only reconciler could RESTORE audit rows (intent+outcome marked
`cfg: rebuilt`) WITHOUT the reported/confirm semantics if ever wanted.