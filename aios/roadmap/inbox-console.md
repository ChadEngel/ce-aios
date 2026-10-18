# Inbox Console — a per-user management UI + appliance for the mail digest

**Status:** M0 IN FLIGHT (spec approved + merged PR #59, 2026-10-17; Entra scaffold + OAuth PoC drafted, PR pending; Chad runs `appliance/entra-app.sh`, then the delegated-login PoC gate).
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