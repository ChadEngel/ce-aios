# T4 — NOC MCP client + per-agent Virtual Keys (design for review)

**Status:** R1 proposal. **Nothing applied.** Corrected 2026-10-04 (see Finding 1).
**Related:** spike #14 (`bifrost-mcp-spike.md`), issue #30, milestone *Tool plane / agent runtime*.

---

## Finding 3 — APPLIED + LIVE (2026-10-06); two wiring traps found in source

**Status (updated session 5): applied to the live cluster; merge-safety and
registration verified. T5 (one tool call) still pending — see
`t4-t5-execution-state.md`. Two execution findings: MCP client names forbid
hyphens (`grafana-noc` → `grafana_noc`, fixed everywhere), and upstream
Bifrost v2.2.6 (released 2026-10-06, pulled via `latest`+Always) now locks
`/api` when dashboard auth is off and no `setup_token` exists — management
routes 403; inference and `/mcp/*` unaffected (decision pending, see N1/N2 in
`open-items.md`).** The Grafana
service-account token is in Infisical and was **verified live**: `sa-1-ai-token`
(`service-account:2`, not a Grafana admin), Loki `GET /api/datasources/proxy/uid/loki/loki/api/v1/labels` → **200**,
`POST /api/folders` → **403**. Read-only confirmed.

Files (all in `~/dev/ce-ai-home-lab`):
- `clusters/util-server/applications/mcp-grafana/kustomization.yaml` — Deployment + Service + NetworkPolicy
- `clusters/util-server/applications/mcp-grafana/README.md`
- `clusters/util-server/applications/bifrost/kustomization.yaml` — new `bifrost-config` ConfigMap (`config.json`) + `subPath` mount at `/app/data/config.json`
- `clusters/util-server/applications/infisical-operator/infisical-secrets-sync.yaml` — new `mcp-grafana-secrets-sync`

### Image tag trap
Docker Hub tags **omit the `v`**: GitHub `v2.0.1` = image **`2.0.1`**. `v2.0.1` returns `MANIFEST_UNKNOWN`. Pinned `grafana/mcp-grafana:2.0.1` (multi-arch amd64+arm64, verified). All flags used (`--enable-query`, `--allowed-hosts`, `--endpoint-path`, `--enabled-tools`) confirmed present in that tag, not just `main`.

### Trap A — the Bearer prefix cannot come from one secret (Q-T4-5)
Bifrost's `sharedHeadersResolver.ConnectionHeaders` (`core/mcp/credstore/shared_headers.go`) sets the header value **verbatim** — **no automatic `Bearer ` prefix**. And `env.` refs resolve **only when the whole value is `env.X`**; there is no `${}` interpolation (`SecretVar.GetValue()` returns the raw field; `NewSecretVar` does a single `os.LookupEnv`). So `"Authorization": "Bearer env.X"` yields the literal string `Bearer env.X`.

mcp-grafana, meanwhile, **requires** the `Bearer ` scheme: `bearerTokenFromRequest` (`caller_auth.go`) rejects anything not beginning `bearer ` (case-insensitive), then constant-time-compares. It **strips** the header before forwarding, so the caller token never reaches Grafana.

**Consequence:** enabling caller auth needs **two** derived values from one source — the raw token for mcp-grafana's `MCP_GRAFANA_SERVER_TOKEN`, and `Bearer <token>` for Bifrost's header. That is why the current manifests **run with caller auth off** and compensate with a NetworkPolicy.

### Trap B — Host validation blocks both Bifrost and the probes
`--allowed-hosts` defaults to loopback variants of `--address`, and validation runs on **every** route on the MCP listener (`/mcp` *and* `/healthz`). Bound to a pod IP:
- Bifrost's request carries `Host: mcp-grafana.ai.svc.cluster.local:8000` → not in default list → **403**. Fixed with an explicit `--allowed-hosts`.
- A k8s `httpGet` probe sends `Host: <podIP>` → also rejected. Fixed by using **`tcpSocket`** probes.

### Decision recorded — Q-T4-2 (placement)
`mcp-grafana` runs as its **own in-cluster Deployment** (no Ingress), same namespace as Grafana. Not stdio-in-Bifrost: Bifrost's `MCPStdioConfig{command,args}` exec()s a **local** binary, and that binary is not in the Bifrost image. HTTP + config.json is the clean path.

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
| 2 | Create a **read-only Grafana service account**, generate a **service-account token** → Infisical; inject via `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` | Chad | human |
| 3 | **Write the git-tracked `config.json`** with `mcp.client_configs` (this is the unblocking step) | review | R1 |
| 4 | Confirm `source_of_truth` + that existing `config.db` VKs survive the merge | review | R1 |
| 5 | Add the `aios-noc` Virtual Key (explicit `mcp_configs`; **no** `version: 1`) | review | R1 |
| 6 | **Prove end-to-end** (that's T5): one LogQL query through OWBUI/API | — | — |

## Grafana credential — service-account token (decided 2026-10-04)
**Use a service-account token, not username/password.** Grafana's own `mcp-grafana` auth doc: *"Use a service account token (**recommended**) or a username and password… basic auth… is less suitable for automation; prefer a service account token when possible."*

Reasons it wins for the NOC agent:
- **Scope control** — SBAC/RBAC grants exactly the read scopes the NOC tools need. Basic auth inherits a human user's role (likely Admin) → blast radius too large.
- **Attribution** — a machine identity, not an impersonated person.
- **Rotation** — revocable independently; no human password in Infisical that also unlocks the Grafana UI.
- **K8s freshness** — `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` is re-read **every request**; the client cache is keyed on the token value, so a rotated Secret produces a new client with **no pod restart**.

### What goes in Infisical
- **Name:** `GRAFANA_SERVICE_ACCOUNT_TOKEN` — the token generated from a **read-only** Grafana service account (Viewer role, or explicit read scopes for the tools used).
- **Not:** a Grafana user password. **Not:** the deprecated `GRAFANA_API_KEY` (works, but deprecated).
- **Not:** the Grafana `admin` password (that's the deferred #4 item — different credential, wrong one to reuse).

### How it's consumed
Mount the Infisical-synced Secret as a volume and set **only** the file var:
```yaml
env:
  - name: GRAFANA_URL
    value: http://grafana.monitoring.svc.cluster.local:3000
  - name: GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE
    value: /var/run/secrets/grafana/token
volumeMounts:
  - name: grafana-token
    mountPath: /var/run/secrets/grafana
    readOnly: true
volumes:
  - name: grafana-token
    secret:
      secretName: grafana-mcp-token
```
> If both `GRAFANA_SERVICE_ACCOUNT_TOKEN` and `..._FILE` are set, the **inline value wins** — set only `..._FILE` so rotation works.

### Prerequisite for the service account itself
Grafana **≥ 9.0** (we're well past). The README also documents the exact pattern we want: *"Using service accounts with limited read-only permissions"* combined with `--disable-write`.

### Caller auth vs Grafana auth (don't confuse them)
- `GRAFANA_SERVICE_ACCOUNT_TOKEN` = the MCP server authenticating **to Grafana** (outbound). This is what we're setting.
- `--server-auth-token` / `MCP_GRAFANA_SERVER_TOKEN` = callers authenticating **to the MCP server** (inbound). **We don't need this here** — Bifrost reaches the server over the cluster network. But note: if the server binds a **non-loopback** address with no caller token, it **logs a security error** at startup (a future release will make it fatal). Options: bind loopback and have Bifrost reach it via the pod network, or add `--server-auth-token` and put it in Bifrost's `auth_type: headers`. Choose at deploy time (new Q-T4-5).

## Failure modes to write down (NOC agent)
- Grafana token scoped read-only? (`--disable-write` **plus** a Viewer-scope SA token — belt and braces; tool-level RBAC as a *third* layer).
- Loki/Influx timeouts on large ranges → cap range/limit in the tool wrapper.
- Token expiry/rotation breaks the client silently → alert on MCP client health. (File-based mount + per-request re-read mitigates.)
- MCP client marked reachable by other keys by mistake (`allow_on_all_virtual_keys` must stay `false`).
- Non-loopback bind without `--server-auth-token` → startup security error; decide bind vs caller token (Q-T4-5).
- Agent Mode vs Open WebUI streaming (from spike #14) — verify before relying on auto-execute.

## Open questions
1. **Q-T4-1:** *(resolved)* Precedence = `source_of_truth: split` (default) merges DB + file per-section; file `mcp` section can be authoritative without touching DB `virtual_keys`.
2. **Q-T4-2:** Runtime confirmation — deploy `mcp-grafana` in-cluster (recommended) or alongside the future agent loop on the Studio?
3. **Q-T4-3:** Scope the Grafana service account: Viewer role sufficient for the Loki+Influx+Grafana read tools we want? Confirm the exact tool list on first successful connect.
4. **Q-T4-4:** Does `mcp-grafana`'s `influxdb` category query via the Grafana datasource proxy (reusing Grafana's existing Influx token) or need its own InfluxDB creds? Confirm at T5.
5. **Q-T4-5:** *(open — Trap A above)* MCP-server inbound bind: currently **caller auth off + NetworkPolicy** (only Bifrost pod admitted). Upstream logs a SECURITY error on non-loopback bind without a caller token (fatal in a future release). The blessed fix needs **two** secret values (raw token + `Bearer <token>`) because Bifrost sends header values verbatim. Follow-up hardening.
6. **Q-T4-6:** *(new)* The Infisical key `GRAFANA_API_TOKEN` (July) is unused by any manifest and is **not** canonical. Remove it to avoid a wrong-token mistake later.
7. **Q-T4-7:** *(new)* `config.json` is mounted via `subPath`, which does **not** hot-reload. A ConfigMap edit requires `kubectl rollout restart deploy/bifrost` — which Bifrost needs anyway to re-read config.

## What was NOT done
Versions 1–2 of this doc: no MCP client, virtual key, service account, or k8s workload was created; all checks were read-only.

Version 3 (2026-10-06): **R1 artifacts written to `ce-ai-home-lab`** — `mcp-grafana/` manifests, the `bifrost-config` ConfigMap, and the InfisicalSecret — validated with `kubectl apply --dry-run=client`. **Still nothing applied to the cluster.** The Grafana token remains in Infisical; no secret value was written to git.

## Correction log
- **2026-10-06:** Chad asserted "HTTP/SSE is unsupported on the config path" and "mcp-grafana cannot do stdio." **Both false.** (1) The private-network guard lives only in the management-API handler (`handlers/mcp.go:1677`); `lib/config.go` never calls it — grep for `IsPublicIP` in the whole tree returns one hit, in `mcp.go`. (2) mcp-grafana's default transport **is** stdio (`-t` defaults to `"stdio"`, with a full `case "stdio"`). The real stdio objection is that Bifrost exec()s a *local* binary not present in its image. Folded into Finding 3.
- **2026-10-04:** Originally reported Finding 1 as a hard blocker ("no MCP client can be registered until dashboard auth is enabled"). **Wrong.** After Chad pushed back, source review showed the guard is API-only + auth-bypassed-only, and `config.json` is a documented, guard-free path. Downgraded to optional hardening. Lesson: I extrapolated from a single 403 to "the capability doesn't exist" without reading the load path.
