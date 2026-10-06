# T4 — NOC MCP client + per-agent Virtual Keys (design for review)

**Status:** R1 proposal. **Nothing applied.** Two findings below change the sequence — one is a hard blocker.
**Related:** spike #14 (`bifrost-mcp-spike.md`), issue #30, milestone *Tool plane / agent runtime*.

---

## Finding 1 (BLOCKER) — Bifrost refuses MCP clients pointing at private addresses without dashboard auth

**Reproduced against the live instance (read-safe probe, nothing created):**
```
POST /api/mcp/client
{"name":"zz-probe","connection_type":"http",
 "connection_string":"http://127.0.0.1:9/mcp","auth_type":"none"}
→ 403 unauthenticated callers cannot register MCP clients that connect to
      loopback, private-network, or link-local addresses; set an admin password
```
Every lab service (Grafana, Loki, Influx, `mcp-grafana`) is on an RFC1918 address. So **no MCP client can be registered until Bifrost dashboard auth (admin password) is enabled.** This is by design (a security guard in Bifrost), not a bug.

Corroborating state:
- `/api/config` → `onboarding_skipped: ["cors","dashboard-auth","enforce-inference-auth"]`, `enforce_auth_on_inference: false`.
- Setting the admin password requires a **setup token** on Bifrost ≥ 2.0.0-prerelease3 (`BIFROST_SETUP_TOKEN` env or `setup_token` in `config.json`). We are on **v2.2.3**, so this applies.

**Consequence:** T4 cannot execute before this is done. Chad does it (it's an admin action, R2/human). It also happens to close the "no auth gate on tool execution" hole flagged in the spike — the two fixes are the same change.

---

## Finding 2 — one MCP server covers Loki + Grafana + InfluxDB (no bespoke integration)

**`grafana/mcp-grafana`** (official Grafana, Go, 3.5k★, active) is the right server:
| Need | How it's met |
|---|---|
| Loki queries | `query_loki_logs` / LogQL + label + patterns tools |
| InfluxDB queries | `influxdb` tool category (disabled by default → `--enabled-tools influxdb`) via the Grafana datasource |
| Grafana dashboards/alerts | dashboard + alerting tools |
| **Read-only** | **`--disable-write`** flag — disables all create/update tools |
| **HTTP transport** | `-t streamable-http` + `--address` / `--endpoint-path /mcp` (avoids the STDIO-in-container problem) |
| Auth | `GRAFANA_SERVICE_ACCOUNT_TOKEN` (scope it read-only) |

This collapses the "build Grafana/Loki/Influx MCP servers" work (old H2) into **deploy one container**. No `mcpo`, no custom code.

**Consequence:** H2 should be closed or rewritten — it's now "deploy `mcp-grafana` read-only," not "build MCP servers."

---

## Proposed architecture

```
Open WebUI ──► Bifrost (v2.2.3) ──► MCP client "grafana-noc" ──► mcp-grafana (k8s, HTTP, --disable-write)
                 │  Virtual Key "aios-noc" (tool allow-list, R0)          │
                 │                                                        ├─► Grafana API (read-only SA token)
                 └─ per-agent scoping, native                             ├─► Loki (via Grafana)
                                                                          └─► InfluxDB (via Grafana datasource)
```
- **`mcp-grafana` runs as its own in-cluster Deployment/Service** (no Ingress — internal only). Keeps STDIO out of the Bifrost container and gives the server its own scoped credential.
- **Bifrost connects over `streamable-http`** to `http://mcp-grafana.ai.svc.cluster.local:8000/mcp`.
- **Grafana service-account token lives in Infisical**, injected as an env var; Bifrost config references it (never plaintext).

### Draft MCP client config (Bifrost)
```json
{
  "name": "grafana-noc",
  "connection_type": "http",
  "connection_string": "http://mcp-grafana.ai.svc.cluster.local:8000/mcp",
  "auth_type": "none",
  "tools_to_execute": ["*"],
  "tools_to_auto_execute": [],
  "allow_on_all_virtual_keys": false
}
```
> `tools_to_auto_execute: []` = **R0**: the model can see/read but nothing executes without an explicit `tool/execute` call. `allow_on_all_virtual_keys: false` = deny-by-default, so this server is reachable *only* by keys we explicitly grant.

### Draft Virtual Key (`aios-noc`, R0)
```json
{
  "name": "aios-noc",
  "mcp_configs": [
    { "mcp_client_name": "grafana-noc", "tools_to_execute": ["*"] }
  ]
}
```
R0 today. Promotion path (per `ai-it-team.md` gates): R1 = no change (already sees everything, executes nothing). R2 = add named action tools to the client's `tools_to_auto_execute`, one tool at a time, after the 2-week R0 track record.

### The one decision inside T4 — where the config lives
| Option | Pros | Cons |
|---|---|---|
| **A. UI/API (config.db on PVC)** | Matches how Bifrost is run today | **Not declarative; lost on PVC rebuild** (spike caveat); violates the "reproducible from git" rule |
| **B. `config.json` via git-tracked ConfigMap** (recommended) | Declarative, diffable, survives PVC loss, matches Chad's hard rule; supports `env.VAR_NAME` so secrets stay in Infisical | Must verify precedence vs the existing `config.db` state (the two live VKs `ce-key` / `ce-pi-macbook`) before cutting over |

**Recommendation: B**, but confirm precedence against the live `config.db` first — turning on `config.json` must not silently drop the existing virtual keys. (Open question Q-T4-1.)

---

## Ordered execution plan
| # | Step | Owner | Tier |
|---|---|---|---|
| 0 | **Enable Bifrost dashboard auth** (set `BIFROST_SETUP_TOKEN`, create admin; turn on `enforce_auth_on_inference`) | **Chad** | human/R2 |
| 1 | Deploy `mcp-grafana` read-only (`--disable-write`, `-t streamable-http`, `--enabled-tools` incl. `influxdb`) as an in-cluster Deployment/Service | Chad/AI assist | R1→R2 |
| 2 | Create a **read-only Grafana service account**, token → Infisical; inject as env | Chad | human |
| 3 | Decide config source (A vs B); if B, write the git-tracked ConfigMap | review | R1 |
| 4 | Register the `grafana-noc` MCP client in Bifrost | Chad/assist | R1→R2 |
| 5 | Create the `aios-noc` Virtual Key | Chad/assist | R1→R2 |
| 6 | **Prove end-to-end** (that's T5): one LogQL query through OWUI/API | — | — |

## Failure modes to write down (NOC agent)
- Read-only token scoped correctly? (`--disable-write` + a Viewer-scope SA token — belt and braces.)
- Loki/Influx timeouts on large ranges → cap range/limit in the tool wrapper.
- MCP client marked reachable by other keys by mistake (`allow_on_all_virtual_keys` must stay `false`).
- Token expiry/rotation breaks the client silently → alert on MCP client health.
- Agent Mode vs Open WebUI streaming (from spike #14) — verify before relying on auto-execute.

## Open questions
1. **Q-T4-1:** Bifrost config precedence — does adding a `config.json` supersede/merge with the existing `config.db` virtual keys? Verify before choosing option B.
2. **Q-T4-2:** Runtime confirmation — deploy `mcp-grafana` in-cluster (recommended) or alongside the future agent loop on the Studio?
3. **Q-T4-3:** Scope the Grafana service account: Viewer role sufficient for the Loki+Influx+Grafana read tools we want? Confirm the exact tool list on first successful connect.
4. **Q-T4-4:** Does `mcp-grafana`'s `influxdb` category query via the Grafana datasource proxy (reusing Grafana's existing Influx token) or need its own InfluxDB creds? Confirm at T5.

## What was NOT done
No MCP client, virtual key, service account, or k8s workload was created. All checks were read-only. The one `POST` was an intentionally-rejected probe; verified afterwards that `GET /api/mcp/clients` still returns `count: 0`.
