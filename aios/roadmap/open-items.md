# Open items (priority order) — refreshed 2026-10-06

> **Mail stack FINISHED (2026-10-27)** — do not reopen without a new
> requirement. Digest + filer (`0.6.4`) and console (`0.6.6`) are deployed and
> verified; repo and cluster agree. Build-order steps 1→5 complete (editing UI
> shipped 0.6.3). Console M3/M4 remain PARKED (M4 gated on the unanswered
> second-person question) — see `decisions.md` 2026-10-27.
> **#63 CLOSED as stale** — live re-verification shows VK scoping IS enforced
> (unauth 401; read VK 12 read-only tools; file VK 5 mutating tools).
> Remaining mail-stack item: **apply the console auth Infisical sync**
> (`console/k8s/infisical-secret-sync.yaml`) AFTER the `CONSOLE_PASSWORD` key
> exists — the console fails OPEN on an empty password. Value rotation still
> pending (Chad: "rotate later").
>
> **Mail-digest pickup (2026-10-21):** session context + runbook for the
> digest/console arc lives in `handoff-mail-digest-2026-10-21.md` — start
> THERE for anything under `aios/mail/`.

> **Live queue moved to GitHub Issues** (`ChadEngel/ce-aios`) — see `manager-agent.md`. The tables below are the human-readable mirror; if they disagree with the issue tracker, **the issues win**.

Status key: OPEN · BLOCKED · NEXT · PARKED · DONE?

## NEW — T4/T5 execution findings (2026-10-06, session 5; details in `t4-t5-execution-state.md`)
| ID | Item | Status |
|---|---|---|
| N1 | `/api` locked by upstream Bifrost v2.2.6 setup lock | **RESOLVED (A)** — `BIFROST_SETUP_TOKEN` env from Infisical → `bifrost-secrets` → pod env; verified 401-without/200-with-header (+403 wrong token); #33 comment. B (dashboard auth) = T6/#32 remains |
| N2 | Pin Bifrost image (tag/digest) instead of `latest`+Always — silent v2.2.3→v2.2.6 drift with behavior change mid-deploy; contradicts repo pinning convention | **DONE 2026-10-07** — `maximhq/bifrost:v2.2.6@sha256:d0b4708…` + `IfNotPresent` (tag list digest == running imageID, verified vs Hub); lab `daa7036` |
| N3 | T5 completion: one read-only tool call through `/mcp/grafana-noc` (initialize + tools/list already proven → 20 tools) | **DONE 2026-10-06** — VK-authed `list_datasources` + `list_loki_label_names` → 200, real data; #31 closed |
| N6 | `/mcp/*` gateway accepts unauthenticated calls (LAN-only exposure today; bypasses VK scoping) | **RESOLVED 2026-10-07** — `client_config.enforce_auth_on_inference=true` via setup-token `PUT /api/config` in #33; live matrix: unauthed /mcp+​/v1 → 401, aios-noc VK → 200/20 tools, ce-key → 200 /v1 + 403 /mcp; survives restart (DB-backed); CM header documents the no-`client`-section caveat |
| N7 | Commit `udm-syslog.json` to git — lives only in cluster CM + script carry-forward; no restore source if lost (same class of loss as mac-system-monitor) | **DONE** — extracted byte-for-byte from live CM, committed `4a8c067` (lab repo) |
| N8 | Grafana pods OOMKilled (both, exit 137, 2026-10-03; limit 768Mi) — state is Postgres-backed so restarts are harmless now, but churn = noise; raise memory limit (768Mi → 1.5–2Gi, request 512Mi) | **DONE 2026-10-07** — 512Mi req / 1536Mi lim (below the 2Gi upper bound: node allocatable ~4.8Gi, 2× worst case must stay sane); applied live after lab PR #10 merge (rule-6 workflow), 2/2 running, 0 restarts, 8 dashboards intact; #34 closed |

N1/N2/N6 consolidated under **#33** — CLOSED 2026-10-07 (A + image pin + enforcement); B (dashboard auth) = T6/#32 remains.
| N4 | Commit ce-ai-home-lab working tree | **DONE** (Chad's yes, 2026-10-06): pushed 4 commits to `origin/headlamp-addition` — T4 MCP artifacts `952b21d`, user's PVC migration `5b12b38`, `grafana_noc` rename `576e269`, `udm-syslog.json` tracking `4a8c067`. **Merged to main via PR #9 (2026-10-07, 10 commits); lab repo now `main`-only** — all branches deleted; subsequent lab work shipped the same way (Bifrost pin `daa7036`, grafana limits lab PR #10 → `8a22654`) |
| N5 | Delete orphan `GRAFANA_API_TOKEN` from Infisical (Q-T4-6) | **DONE 2026-10-07** — Chad deleted via UI (his confirmation). Machine-side verification unavailable on this build (secret-read REST routes 404 for identity tokens; CLI unauthenticated); cluster-side blast radius verified zero: never synced into any InfisicalSecret, zero manifest references, operator logs clean, `mcp-grafana-secrets` = only `GRAFANA_SERVICE_ACCOUNT_TOKEN`, grafana healthy (8 dashboards) |

## P0 — Level 2: secrets + Influx (prerequisite for every agent)
| ID | Item | Status | Notes |
|---|---|---|---|
| S1 | Rotate Grafana admin password (`admin`/`admin` is LIVE) | PARKED — end of project | **Not a blocker, not tracked in the current push** (Chad 2026-10-04). Handled at the very end. Issue #4 closed with this note. |
| S2 | Rotate Infisical AUTH_SECRET | OPEN | logs everyone out |
| S3 | Rotate Infisical Postgres password (update POSTGRES_PASSWORD **and** DB_CONNECTION_URI) | OPEN | brief downtime |
| S4 | Rotate Infisical ENCRYPTION_KEY | OPEN | **destructive**: export all secrets first, verify backup, human-executed |
| S5 | Rotate Cloudflare API token at Cloudflare + Infisical | OPEN | leaked in old git history |
| S6 | Confirm rotation of HA Infisical client secret, HA long-lived token, HA SSH deploy key | UNCONFIRMED | INSTRUCTIONS.md says exposed in chat |
| S7 | Redact + scrub `psk` and `x_iapp_key` in home-network-config `udm-pro/exports/*` (git history too); add sanitize script + pre-commit hook; rotate WiFi PSK if it was the exposed value | OPEN | repo is private but rule is zero secrets |
| S8 | Revoke bootstrap Infisical service token once CLI admin no longer needed | OPEN | optional |
| S9 | Delete old `influx-metrics-pusher` Job (plaintext write token in spec) and rotate INFLUXDB_TOKEN after cutover | OPEN | |
| S10 | Canonicalize Infisical project names across docs | OPEN | |
| I1 | InfluxDB migration aiserver→cluster | DEFERRED | Chad 2026-10-04: **not cut over**; working as-is; **not a blocker** — don't treat as an open priority. |

## P1 — AI IT team
| ID | Item | Status |
|---|---|---|
| A1 | Decide runtime placement (Compose on Studio vs cluster) | **DECIDED 2026-10-07** — in-cluster, ns `ai` (T2, #15 closed; `decisions.md`). Studio = inference provider (Ollama native/Metal) + interactive pi sessions only. Scope-shrink note stood: only the background agent loop needed a home; Bifrost executes tools |
| A2 | Tool plane: configure Bifrost MCP clients + per-agent Virtual Keys; then prove one read-only tool end-to-end | NEXT (T4/T5). **mcpo is dead — Bifrost IS the gateway** (#14). **T4 unblocked — use `config.json`** (private-IP API path 403s without auth; config.json path does not) |
| A6 | Harden: enable Bifrost dashboard auth + `enforce_auth_on_inference` | **Optional hardening (T6), not a prerequisite.** Closes the tool-execution auth hole. Downgraded from blocker 2026-10-04 |
| A3 | Orchestrator model bake-off (multi-tool reliability) | OPEN |
| A4 | HA scope exception + firewall allow | BLOCKED on Chad |
| A5 | Tenant decision (personal vs work) → Entra app registration design | RESOLVED 2026-10-04: personal `engelmn.com`, M365 Business Premium. Design in ai-it-team.md; creation waits for hires #1–#3 |

## P2 — network (home-network-config)
Segmentation plan phases 0–6 not started; firewall is allow-all. Pending decisions: HA placement, Sonos VLAN, Droplet-7B40 → VLAN 30, printer, Netgear walkthrough, NAS eth0 → VLAN 50, rename VLAN 40/50, retire VLAN 2/DVR. Planned rules must carve out Lab→HA, Lab→UDM API for the agents/unpoller/udm-thermal.

## P2 — personal agents (added 2026-10-04; gated on Level 2, see `personal-agents.md`)
| ID | Item | Status |
|---|---|---|
| PA1 | Scheduler agent — bundle with helpdesk Graph/Entra work; scope calendar(s), R0 vs R1 | OPEN |
| PA2 | Travel agent — first real job = MSP→OGG Mar 2027; pick award-search tool | PARKED until a trip is live |
| PA3 | Financial planner — data-source decision (manual/aggregator/brokerage) gates everything | PARKED (highest risk, build last) |

## P2 — documentation drift
UDM_REPLACEMENT.md missing · duplicate syslog-receiver · DEPLOYMENT_STATUS stale header + services table lacks postgres/redis/pushover-bridge/synthetic-monitor/udm-thermal/influxdb · stale LiteLLM leftovers to delete · broken openwebui production overlay · SETUP.md TODOs (Ollama/AIbeast install doc, util-server VM doc) · reconcile `aiserver.home`/AIbeast naming · stale `.60` in firewall group.

## PARKED (do not start without Chad re-prioritizing)
Lauren AI-literacy workspace (build for one kid first; not a business yet) · West Maui cruise business (planning only) · Level 3 formal "Project" (this folder is the portable equivalent).

## Pending questions for Chad
1. Approve HA scope exception (192.168.250.168:8123/:8300, read-only first)? 
2. ~~Which M365 tenant~~ ANSWERED: personal `engelmn.com` (M365 Business Premium + Teams).
3. Were the HA token / deploy key / Infisical client secret / UDM psk rotated?
4. ~~Confirm agent runtime placement~~ **ANSWERED 2026-10-07** — in-cluster ns `ai` (T2, #15 closed, `decisions.md`).
5. What is "Azure Plan 2" — an Azure subscription (needed for Azure Bot), or Entra ID P2? Is there an active subscription?
6. Which mailbox should the helpdesk agent work on — your primary, or a dedicated one (safer)?
7. Scheduler: which calendar(s) — personal only, or family/shared too? Propose changes (R1) from day one or read-only trial?
8. Finance: intended data source — manual/spreadsheet vs aggregator (Plaid/SimpleFIN) vs brokerage API?
