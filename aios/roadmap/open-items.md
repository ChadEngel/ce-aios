# Open items (priority order) — refreshed 2026-10-06

> **Live queue moved to GitHub Issues** (`ChadEngel/ce-aios`) — see `manager-agent.md`. The tables below are the human-readable mirror; if they disagree with the issue tracker, **the issues win**.

Status key: OPEN · BLOCKED · NEXT · PARKED · DONE?

## NEW — T4/T5 execution findings (2026-10-06, session 5; details in `t4-t5-execution-state.md`)
| ID | Item | Status |
|---|---|---|
| N1 | `/api` locked by upstream Bifrost v2.2.6 "setup lock" (pulled via `latest`+Always mid-deploy). Decisions: A = `setup_token` from Infisical (recommended), B = enable dashboard auth (== T6) | **BLOCKED on Chad** |
| N2 | Pin Bifrost image (tag/digest) instead of `latest`+Always — silent v2.2.3→v2.2.6 drift with behavior change mid-deploy; contradicts repo pinning convention | OPEN (Chad's earlier deliberate choice, needs his yes) |
| N3 | T5 completion: one read-only tool call through `/mcp/grafana-noc` (initialize + tools/list already proven → 20 tools) | **DONE 2026-10-06** — VK-authed `list_datasources` + `list_loki_label_names` → 200, real data; #31 closed |
| N6 | `/mcp/*` gateway accepts unauthenticated calls (LAN-only exposure today; bypasses VK scoping) | OPEN — **#33** |
| N7 | Commit `udm-syslog.json` to git — lives only in cluster CM + script carry-forward; no restore source if lost (same class of loss as mac-system-monitor) | OPEN — fold into N4 commit |
| N8 | Grafana pods OOMKilled (both, exit 137, 2026-10-03; limit 768Mi) — state is Postgres-backed so restarts are harmless now, but churn = noise; consider raising memory limit | OPEN — optional hardening |

N1/N2 (setup_token vs dashboard auth; image pinning) consolidated into **#33** (`status:waiting-on-chad`).
| N4 | Commit ce-ai-home-lab working tree (T4 artifacts + hyphen fix; user's unstaged PVC migration stays out) | BLOCKED on explicit yes |
| N5 | Delete orphan `GRAFANA_API_TOKEN` from Infisical (Q-T4-6) | BLOCKED on explicit yes |

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
| A1 | Decide runtime placement (Compose on Studio vs cluster) | OPEN — confirm. **Scope shrunk by #14:** only the background agent loop needs a home; Bifrost executes tools |
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
4. Confirm agent runtime placement: Docker Compose on the Mac Studio (leaning) vs in-cluster.
5. What is "Azure Plan 2" — an Azure subscription (needed for Azure Bot), or Entra ID P2? Is there an active subscription?
6. Which mailbox should the helpdesk agent work on — your primary, or a dedicated one (safer)?
7. Scheduler: which calendar(s) — personal only, or family/shared too? Propose changes (R1) from day one or read-only trial?
8. Finance: intended data source — manual/spreadsheet vs aggregator (Plaid/SimpleFIN) vs brokerage API?
