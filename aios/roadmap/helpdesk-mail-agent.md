# H5 / hire #4 — Helpdesk mail agent (design, spike-resolved)

**Status:** T-M1 spike **PASS** (2026-10-16) — architecture revised; mailbox decision **resolved** (primary, Chad 2026-10-16).
**Related:** #21 (was the Entra-registration gate — now shrunk to an optional R2 hardening item), `ai-it-team.md` (roster), `decisions.md` 2026-10-16 entries (mailbox=primary; Softeria adopted).

Chad's 2026-10-16 direction: **promote to primary now, get it set up, start slow.** Concretely: v1 = one daily digest + on-demand reads, nothing else; tier gates unchanged.

## Spike result (T-M1, evaluated 2026-10-16 against Softeria/ms-365-mcp-server @ main)

Central question was app-only daemon auth. **Answer: it doesn't exist** — no client-credentials flow in the codebase (only interactive / device-code / silent / OBO). BUT the spike found a daemon-viable mode that *removes Chad's Entra work entirely*:

| Spike finding | Verdict |
|---|---|
| `--http` streamable HTTP transport, default `:3000` | PASS — Bifrost-reachable |
| `--trust-proxy-auth` (HTTP): skips bearer check; **all callers share the server's cached MSAL login** | PASS — daemon-viable; auth boundary moves to NetworkPolicy + Bifrost VK |
| `--read-only`: blocks non-GET Graph calls at the call layer + filters tools lacking `readOnlyHint` | PASS — R0 enforced in-server, not just at VK |
| `--enabled-tools` + `--allowed-scopes` + `--expected-username` | PASS — surface/identity pinning available |
| App-only (client credentials + cert) support | **FAIL** — absent; revisit only if a write-tier ever needs it (see R2 note) |
| Token cache at rest | Encrypted envelope; key supplied via env (`MS365_MCP_SECRET_KEY`) — becomes an Infisical secret. Exact env name to confirm at deploy |

## Architecture (revised)

Identical topology to the original design; the identity model changed:

```
[Bifrost /mcp/graph] --(cluster-internal, auth:none)--> [ms-365-mcp-server, ns ai]
        |                                                      |
        |--- VK aios-helpdesk (read-only execute list)         |--- one-time device-code login by Chad
        |--- NetworkPolicy: only bifrost:8000 may call         |--- encrypted token cache on PVC
```

- **Server:** `Softeria/ms-365-mcp-server` (npm `@softeria/ms-365-mcp-server`, active, ★1049) packaged in a minimal container, ns `ai`, port 3000, NetworkPolicy = only Bifrost ingress, no egress beyond Graph (`graph.microsoft.com`, `login.microsoftonline.com`).
- **Identity:** Chad logs in **once** via device code (`--login`) as his primary account; `--expected-username <upn>` pins it. Refresh-token cache persists on a small PVC (encrypted with an Infisical-held key).
- **R0 hardening flags at boot:** `--http :3000 --trust-proxy-auth --read-only --expected-username <chad-upn> --enabled-tools <mail-read-tool-list>` (exact tool names confirmed from `tools/list` at deploy; list to include only unread-list/search/get-metadata/get-body/count-style reads).
- **Bifrost:** MCP client `graph` (`auth: none`, internal endpoint), VK `aios-helpdesk`, `tools_to_execute` = same read list, `tools_to_auto_execute: []`. Client name has **no hyphens** (session-5 trap); endpoint slug re-hyphenates.
- **Digest loop (v1):** k8s CronJob calling the VK (same pattern as the NOC digest), routed through **local Ollama models via Bifrost** — mail content must not reach a frontier provider by default (context.md §6 routing rule). Later moves under `aios-manager` when hired. Digest lands on a dedicated issue thread in `ce-aios`.

## What the spike removed (vs original design)

1. **No Entra app registration for v1.** #21's `aios-helpdesk-mail` registration deferred — becomes optional hardening.
2. **No cert/PEM, no `GRAPH_*` secrets, no `ApplicationAccessPolicy`.** Delegated scope means the token IS Chad's identity reading his own mailbox — the blast radius is narrower by construction than tenant-wide app perms + RBAC fence.
3. **Infisical layout shrinks to one secret:** `MS365_MCP_SECRET_KEY` (32 bytes, cache encryption) in **jarvis-jatg** (agent-creds boundary rule, 2026-10-11) + non-secret `MS365_MCP_*` config vars. Synced via the existing `jarvis-jatg-universal-auth` credential from PR #44's deploy — no second credential.

## What the spike added (new failure modes / notes)

1. **Acts-as-Chad identity.** Graph calls carry Chad's delegated identity, not a service identity. Acceptable for **read-only** (and honest: it can only read what Chad can read). **Trigger point:** if any write tier (R2: move/categorize) is ever promoted, acting as-user is scarier than acting as-scoped-app — at that gate, build the app-only path (own registration, `Mail.ReadWrite` application, RBAC fence, cert) per the original design, kept in this file as a rollback target, or reject the write tier.
2. **Refresh-token staleness.** Delegated refresh tokens die on inactivity (~90d), CA policies, or password change — a silent-outage class. Mitigations: daily digest keeps the token warm; `--verify-login` in the CronJob's health step; a failed-refresh event lands in Loki as the loud signal. Cert-expiry silent outage from the original design is gone; this replaces it as the watch item.
3. **The token cache is a credential at rest.** Encrypted, but treat the PVC like a secret store: no arbitrary pods in ns `ai` get read access (default-deny ingress/egress for the server pod per NetworkPolicy; PVC limited to the server).
4. **Scope breadth.** Softeria's default app asks for its fixed, broader-than-mail scopes (`--list-permissions` to enumerate at deploy). Least-privilege hardening = BYO minimal-scope delegated app (`Mail.Read`, `User.Read`) later — a registration task Chad can batch with the scheduler's `aios-calendar` sitting (#27). Not required for R0.

## Tier plan (unchanged gates)

| Tier | What | Config |
|---|---|---|
| R0 (start — "slow", per Chad) | Daily digest + 4-5 read tools on demand | `--read-only` + VK execute-list, `Mail.Read`-equivalent delegated scope |
| R1 | Propose triage rules as diffs in the digest | no config change — read-only compute |
| R2 (decision gate, not scheduled) | one action at a time (move/categorize) | **re-evaluate identity first** (see failure-mode 1) |
| Never | send, delete | out of scope |

**Digest spec (v1 deliverable, daily):** unread count, needs-reply heuristic (last thread message from another party), category breakdown, top-5 aged unread, timestamps on everything. Log-PII rule holds: ids + truncated subjects in Loki, **never bodies or recipient addresses**; bodies reach model context only on explicit single-message reads.

## Failure modes (seed the R0 two-week gate)

1. Prompt injection via mail body — content is untrusted data, never instructions (operating-rules guardrail); bulk body ingestion avoided.
2. Refresh-token staleness (watch item above).
3. VK scope creep — execute-list changes require an issue + decision note.
4. Graph 429s — bounded single-batch digest, backoff; not a stream.
5. Stale digest read as current state — timestamps mandatory.
6. Bifrost/auth drift — the `--trust-proxy-auth` mode trusts the network boundary; if NetworkPolicy ever opens beyond Bifrost, the mailbox rides the opening. Review the policy in the R0 gate.

## Rollout

| Step | Who | Gate / note |
|---|---|---|
| Container build + deploy manifests (deployment/pvc/networkpolicy/service, ns ai) | assistant (PR) | after PR #44's `jarvis-jatg-universal-auth` exists (shared prereq) |
| Image import to k3s + apply | assistant (SSH path verified session 7) | manifests merged |
| Bifrost client `graph` + VK `aios-helpdesk` | assistant (lab repo, branch+PR) | server reachable |
| One-time device-code login (`--login`, `--expected-username`) | **Chad** (~5 min, from laptop/lounge; URL+code) | only human step in the mail chain |
| T-M2: one tool call end-to-end (T5 pattern) | assistant | proof logged |
| Daily digest, 2-week R0 | agent | Chad reviews logs → "hired" decision |

**Still pending before ANY in-cluster deploy (both manager and mail):** Chad's universal-auth credential step from PR #44 (`jarvis-jatg` identity → UI → clientId/secret → `kubectl -n ai create secret generic jarvis-jatg-universal-auth ...`).