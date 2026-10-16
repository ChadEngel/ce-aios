# H5 / hire #4 — Helpdesk mail agent (design for review)

**Status:** R1 proposal — nothing applied, nothing registered, no secrets written.
**Related:** #21 (Entra registration gate, `status:waiting-on-chad`), `ai-it-team.md` (roster + M365 guidance), `manager-agent.md` (digest pattern), `decisions.md` 2026-10-07 (T2 in-cluster runtime), 2026-10-11 (jarvis-jatg agent-creds boundary).

Chad reprioritized 2026-10-11: mailbox agent **next** (ahead of patch/facilities in the original order). Honest note: this does not block or depend on NOC/patch — its prerequisites (Entra app + mailbox choice + Infisical layout) are independent, so the reorder is defensible without leaving a half-finished hire behind. NOC's tool plane is live; NOC-as-standing-agent still owes its 2-week R0 window.

## Scope (v1)

Mail triage, **read-only (R0)**.

- **In v1:** unread/needs-action digest, inbox counts, search/retrieval (metadata + body on demand), category inventory. One recurring digest.
- **Out of scope v1:** Mail.Send (never for v1, decide later entirely), delete (never), Teams two-way (that's the other half of hire #4 — later, needs Azure Bot + tunnel decision), auto-categorize (R2-class, later).
- **Rides along:** scheduler agent (PA1, #27) shares the Entra/graph infrastructure — if Chad does the registration session once, `aios-calendar` can be created in the same sitting (separate registration, separate identity, per the one-app-per-agent rule).

## Architecture

**Chosen:** Graph MCP **server** in-cluster (ns `ai`), registered as a Bifrost MCP client, VK `aios-helpdesk` scoped to read tools only. The agent loop dispatches through Bifrost like every other hire.

Why this shape (consistency with five recorded decisions rather than novelty):
1. **Bifrost is the tool plane** (spike #14) — tools belong on `/mcp/*`
behind per-agent Virtual Keys, not inside bespoke service code.
2. **Per-agent least privilege is native** — `aios-helpdesk` VK +
`tools_to_execute` read-only list = R0 enforced at the gateway.
3. **One secret path** (operator sync, `jarvis-jatg` source) and **one
observability path** (tool calls → Loki).
4. Reusable: once `graph` exists as a client, the scheduler (calendar
read) and later Teams work reuse it — a second integration rides the
same endpoint.

Rejected alternatives:
- **Graph calls inside `aios-manager` code** (like its GitHub logic) —
couples mail to the manager binary, bypasses VK-scoping/audit,
duplicates Graph auth handling. Not reusable by the scheduler.
- **GitHub Actions / external cron** — violates Infisical-only rule
(LLM key needed) and Bifrost is LAN-only.
- **Personal PAT-style delegation by reusing Chad's own mailbox token** —
violates least-privilege/revocable-identity guardrail outright.

## Identity & auth (Chad does this — one sitting)

Per the app-registration register in `ai-it-team.md`:

1. **App:** single-tenant Entra registration `aios-helpdesk-mail`.
2. **Credential:** certificate (self-signed, upload `.cer`). No client
secret unless cert flow fails — secret only if needed, 90-day expiry,
Infisical-held. Record choice + expiry in #21 when done.
3. **API permission:** `Mail.Read` (application) for R0. **Not**
`Mail.ReadWrite` yet — add it only when a write action is promoted (R2
gate). App-only mail permissions are tenant-wide *by default* — the RBAC
step below is not optional.
4. **Tenancy scope (mandatory):** Exchange Online `ApplicationAccessPolicy`
restricting the app to a mail-enabled security group containing exactly
the target mailbox. PowerShell:
   `New-ApplicationAccessPolicy -AppId <appId> -PolicyScopeGroupId <grp> -AccessRight RestrictAccess` +
   `Test-ApplicationAccessPolicy` on a mailbox outside the group (expect
   failure) and inside (expect success). **Verify current Microsoft
   guidance at build time** (UNCONFIRMED from this doc — flags per rule 4).
5. **Admin consent:** Chad grants once. Sign-in/audit logs reviewed at the
   2-week R0 gate.

**Mailbox choice (gating question, #21):** recommendation = a **dedicated
mailbox** (e.g. `aios-mailbox@engelmn.com`). Blast-radius: policy group
contains one mailbox, not Chad's primary; nothing personal leaks into
agent context; kill = delete registration. Tradeoff: mail must be
*deliberately routed* to it (rules/forwarding), so the agent's value is
bounded by what the mailbox receives. Operating on the primary inbox is
possible but exposes personal PII to model context from day one.

## Infisical layout (jarvis-jatg — per the 2026-10-11 boundary rule)

Secrets at the **root path** of `jarvis-jatg/prod` (hyphen-free paths are
an operator-template requirement — issue #43 trap):

| Key | Content | Source |
|---|---|---|
| `GRAPH_TENANT_ID` | tenant UUID | Chad (Entra portal) |
| `GRAPH_CLIENT_ID` | app UUID | Chad |
| `GRAPH_PRIVATE_KEY_PEM` | cert private key (PEM) | generated at creation |
| `GRAPH_CERT_THUMBPRINT` | hex thumbprint | Entra portal |
| `GRAPH_MAILBOX_UPN` | target mailbox | Chad's mailbox decision |
| `GRAPH_BASE_URL` | `https://graph.microsoft.com/v1.0` (test override possible) | this doc |

K8s: `InfisicalSecret` `aios-helpdesk-secrets-sync` → `aios-helpdesk-secrets`
in ns `ai`, referencing a `jarvis-jatg-universal-auth` credential — **the
same one** being created for `aios-manager` (PR #44 step 1) — no second
credential needed. Rotation: cert at 12 months, renewal runbook note in
this file at build time.

## MCP server — spike first (T-M1, follows the #14 pattern)

Community candidates (checked 2026-10-11; stars + last push from GitHub):

| Candidate | Stars | Last push | Notes |
|---|---|---|---|
| `Softeria/ms-365-mcp-server` | 1049 | 2026-10-15 (active) | **HTTP/streamable transport + Open WebUI integration documented.** Auth model is user-delegation OAuth (BYO app registration via `MS365_MCP_CLIENT_ID/SECRET`, device-code/OBO flows). **App-only client-credentials support UNCONFIRMED — this is the spike's central question.** Also verify the tool surface can be filtered to read-only mail. |
| `ryaker/outlook-mcp` | 445 | 2026-03-30 (stale) | Mail-focused; stdio; stale 6 months — pass unless spike #1 fails |
| `tecnologicachile/mail-mcp` | 103 | active | Rust, multi-protocol IMAP/SMTP/EWS/Graph; SMTP surface = unwanted send capability for v1 |
| Fallback: **build minimal** | — | — | FastMCP (Python) ~150 LOC: `mail_list_unread`, `mail_search`, `mail_get_metadata`, `mail_get_body`, `mail_count_by_category`; app-only cert auth exactly as designed above |

**Spike pass criteria (all four):** (a) unattended app-only auth (cert) —
no interactive login anywhere in the boot path; (b) HTTP/streamable
transport reachable from Bifrost's container; (c) tool surface restricts
to reads; (d) no plaintext secret beyond what Infisical injects. If (a)
fails on every candidate → build minimal (it's small and exactly-scoped;
that's not the mcpo mistake — mcpo duplicated an existing capability,
this fills a verified hole).

Deployment: ns `ai`, container, NetworkPolicy allowing only bifrost:8000
inbound-style traffic (mirror `mcp-grafana`'s policy), VK `aios-helpdesk`
with `tools_to_execute` = the read list, `tools_to_auto_execute: []`.
**MCP client name: `graph`** — client names forbid hyphens (session-5
trap: `grafana-noc` failed); endpoint slug re-hyphenates to
`/mcp/graph`.

## Tier plan (mirrors the roster's gate)

| Tier | What | Config |
|---|---|---|
| R0 (start) | digest + read/search only | `Mail.Read`, VK execute-list = 5 read tools |
| R1 | propose rules: categories, flags, folder targets (as diffs in the digest) | no permission change needed — still read-only compute |
| R2 | apply ONE action type at a time: first `move to folder`, then `categorize` | add `Mail.ReadWrite` to the app + RBAC re-check + explicit per-action confirm |
| Never | send, delete, Teams outbound as "mail" | out of scope / separate design |

**"Hired" bar (from `ai-it-team.md`):** 2 weeks R0 with logged calls
reviewed by Chad + written failure-mode list, then one action at a time.

## The v1 deliverable: digest

Recurring (cadence TBD — recommendation: daily at a fixed time):
unread count, messages where the last thread message is from another
party (needs-reply heuristic), today's categories breakdown, top-5 aged
unread. Delivery: comment on a dedicated digest issue in `ce-aios`
(auditable, matches the manager pattern). Pushover/Teams delivery = later
(reuse `pushover-bridge` if Chad prefers push immediately).

## Failure modes (seed the R0 gate's list)

1. **Prompt injection via mail body** — email content is untrusted data,
   never instructions (operating-rules guardrail). Body fetched on demand
   only; never auto-fed to the model in bulk.
2. **Scope creep of app perms** — tenant-wide by default; the RBAC policy
   is the real control. `Test-ApplicationAccessPolicy` results recorded
   in #21 at creation.
3. **Log PII** — Loki logs carry ids/subjects-truncated, **never bodies**
   or recipient addresses.
4. **Graph throttling** — 429 backoff; digest is a single bounded batch,
   not a stream.
5. **Stale digest read as current state** — timestamps in every digest.
6. **Cert expiry (12mo) = silent outage** — renewal note + Loki probe
   (start-up failure event) makes it loud, not silent.

## Open questions (Chad — #21)

1. **Mailbox:** dedicated `aios-mailbox@engelmn.com` (recommended) vs
   operating on the primary personal inbox?
2. Digest cadence + surface: daily-to-issue-comment vs push via
   pushover-bridge vs both?
3. Do the scheduler's `aios-calendar` registration in the same Entra
   sitting? (#27) — recommended, one consent session.

## Rollout

| Step | Who | Gate |
|---|---|---|
| T-M1 spike: pick/build MCP server | assistant (branch PR) | pass criteria above |
| Entra registration + cert + RBAC + consent | **Chad** (#21) | mailbox choice answered |
| Cert + secret keys into `jarvis-jatg` | Chad (UI, clipboard, never chat) | registration done |
| Deploy server (ns ai) + Bifrost client + VK | assistant | PR #44's `jarvis-jatg-universal-auth` exists first (shared prereq) |
| T-M2: one read tool call end-to-end (T5 pattern) | assistant | proof logged to issue |
| R0 digest, 2 weeks | agent | Chad reviews logs → "hired" decision |