# T4 — NOC MCP client + per-agent Virtual Keys (design for review)

**Status:** R1 proposal. **Nothing applied.** Corrected 2026-10-04 (see Finding 1).
**Related:** spike #14 (`bifrost-mcp-spike.md`), issue #30, milestone *Tool plane / agent runtime*.

---

## Finding 1 (CORRECTED) — private-network MCP clients ARE supported; my earlier "blocker" was wrong

**Correction (2026-10-04, after Chad pushed back and I read the source).** I initially called this a hard blocker. It is not. Bifrost explicitly supports private/loopback MCP targets with dashboard auth disabled — via **`config.json`**.

What the guard actually does (`transports/bifrost-http/handlers/mcp.go`, `rejectPrivateMCPTargetIfAuthBypassed`):
- It fires **only** on the **HTTP management API** (`POST /api/mcp/client`).
- And **only** when `BifrostContextKeyAuthBypassed == true` (dashboard auth off).
- The gate is on *the credential check*, not on the capability. Source comment: *"A genuinely authenticated admin keeps the documented ability to point an MCP client at a local or private server."* Guard test `TestRejectPrivateMCPTargetIfAuthBypassed_AuthenticatedLoopbackAllowed` asserts exactly this.
- The **`config.json` startup path never calls the guard.** `loadMCPConfig` (`lib/config.go:1850`) processes `mcp.client_configs` and calls `CreateMCPClientConfig` (`lib/config.go:1924`) directly — no SSRF pre-check.

**Docs confirm it** (`docs/mcp/connecting-to-servers.mdx:227`):
> *"…an HTTP or SSE client whose `connection_string` resolves to a loopback, private-network, link-local, or CGNAT address is refused — you must either enable dashboard auth and authenticate first, **or define the client in `config.json` instead**."*

The same note applies to STDIO (`:203`) — *"Enable dashboard authentication, or provision this client via `config.json` instead."*

**Consequence:** T4 is **not blocked**. Proceed via `config.json` — which was already the recommended option (declarative, git-tracked, survives PVC loss). Enabling dashboard auth (T6) is still worth doing as **defense in depth** (it also closes the tool-execution auth hole from spike #14), but it is a **hardening item, not a prerequisite**. Severity downgraded 🟡.

### Precedence resolved (old Q-T4-1)
The `config.json` root has `source_of_truth`: `"split"` (default — merge DB + file) or `"config.json"` (file sections authoritative). Default `split` means adding `mcp.client_configs` merges with, and does not silently drop, the existing `config.db` virtual keys (`ce-key`, `ce-pi-macbook`). Reconciliation is per-section (`isConfigJSONSourceOfTruth()` + `sectionPresent(...)`), so we can let `mcp` be file-owned without disturbing `virtual_keys` in the DB.

### Trap to avoid when writing the VK in `config.json`
`applyV1Compat` (`lib/config.go:717`) backfills **all** MCP clients into any VK whose `mcp_configs` list is empty — **but only when `version: 1`**. On modern configs (no `version: 1`) an empty list is deny-all (v1.5+ semantics). **Do not set `version: 1`.** Keep the VK's `mcp_configs` explicit.


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

### Draft MCP client + VK — the actual `config.json` (git-tracked ConfigMap)
This is the unblocking artifact. One file, no DB writes, no admin session.

```json
{
  "mcp": {
    "client_configs": [
      {
        "name": "grafana-noc",
        "connection_type": "http",
        "connection_string": "env.GRAFANA_MCP_URL",
        "is_ping_available": true,
        "auth_type": "none",
        "tools_to_execute": ["*"],
        "tools_to_auto_execute": []
      }
    ]
  },
  "virtual_keys": [
    {
      "name": "aios-noc",
      "mcp_configs": [
        { "mcp_client_name": "grafana-noc", "tools_to_execute": ["*"] }
      ]
    }
  ]
}
```
> - `connection_string` uses **`env.GRAFANA_MCP_URL`** so the URL/secret stays in Infisical, not git.
> - `tools_to_auto_execute: []` = **R0**: the model can see/read but nothing executes without an explicit `tool/execute` call.
> - `mcp_configs` is **explicit** and `version` is **omitted** — so deny-by-default applies and no wildcard backfill happens (see the `applyV1Compat` trap in Finding 1).
> - **Do not set `version: 1`** in this file.

### Promotion path (R1 → R2)
Per the `ai-it-team.md` gates: R0 (above) → R1 needs no change (already sees everything, executes nothing) → R2 = add **named** action tools to the client's `tools_to_auto_execute`, one tool at a time, after the 2-week R0 track record.

### The one decision inside T4 — where the config lives
| Option | Pros | Cons |
|---|---|---|
| **A. UI/API (config.db on PVC)** | Matches how Bifrost is run today | **Not declarative; lost on PVC rebuild** (spike caveat); **and the private-IP API path is 403'd unless auth is on** |
| **B. `config.json` via git-tracked ConfigMap** (recommended) | Declarative, diffable, survives PVC loss, matches Chad's hard rule; supports `env.VAR_NAME` so secrets stay in Infisical; **works with dashboard auth off** | Uses `source_of_truth: split` merge semantics — verify existing VKs survive |

**Decision: B.** `config.json` is both the declarative choice *and* the one that sidesteps the auth gate. `source_of_truth: split` (default) merges rather than replaces.

---

## Ordered execution plan
| # | Step | Owner | Tier |
|---|---|---|---|
| 0 | *(Optional hardening)* Enable Bifrost dashboard auth + `enforce_auth_on_inference` | Chad | human/R2 |
| 1 | Deploy `mcp-grafana` read-only (`--disable-write`, `-t streamable-http`, `--enabled-tools` incl. `influxdb`) as an in-cluster Deployment/Service | Chad/AI assist | R1→R2 |
| 2 | Create a **read-only Grafana service account**, token → Infisical; inject as env | Chad | human |
| 3 | **Write the git-tracked `config.json`** with `mcp.client_configs` (this is the unblocking step) | review | R1 |
| 4 | Confirm `source_of_truth` + that existing `config.db` VKs survive the merge | review | R1 |
| 5 | Add the `aios-noc` Virtual Key (explicit `mcp_configs`; **no** `version: 1`) | review | R1 |
| 6 | **Prove end-to-end** (that's T5): one LogQL query through OWBUI/API | — | — |

## Failure modes to write down (NOC agent)
- Read-only token scoped correctly? (`--disable-write` + a Viewer-scope SA token — belt and braces.)
- Loki/Influx timeouts on large ranges → cap range/limit in the tool wrapper.
- MCP client marked reachable by other keys by mistake (`allow_on_all_virtual_keys` must stay `false`).
- Token expiry/rotation breaks the client silently → alert on MCP client health.
- Agent Mode vs Open WebUI streaming (from spike #14) — verify before relying on auto-execute.

## Open questions
1. **Q-T4-1:** *(resolved)* Precedence = `source_of_truth: split` (default) merges DB + file per-section; file `mcp` section can be authoritative without touching DB `virtual_keys`.
2. **Q-T4-2:** Runtime confirmation — deploy `mcp-grafana` in-cluster (recommended) or alongside the future agent loop on the Studio?
3. **Q-T4-3:** Scope the Grafana service account: Viewer role sufficient for the Loki+Influx+Grafana read tools we want? Confirm the exact tool list on first successful connect.
4. **Q-T4-4:** Does `mcp-grafana`'s `influxdb` category query via the Grafana datasource proxy (reusing Grafana's existing Influx token) or need its own InfluxDB creds? Confirm at T5.

## What was NOT done
No MCP client, virtual key, service account, or k8s workload was created. All checks were read-only. The one `POST` was an intentionally-rejected probe; verified afterwards that `GET /api/mcp/clients` still returns `count: 0`.

## Correction log
- **2026-10-04:** Originally reported Finding 1 as a hard blocker ("no MCP client can be registered until dashboard auth is enabled"). **Wrong.** After Chad pushed back, source review showed the guard is API-only + auth-bypassed-only, and `config.json` is a documented, guard-free path. Downgraded to optional hardening. The lesson: I extrapolated from a single 403 to "the capability doesn't exist" without reading the load path.
