# aios-noc

The AIOS NOC agent (hire #1) — R0 read-only triage. Twice daily it asks the
tool plane a fixed set of questions, then posts what it found as a comment on
GitHub issue #17. **No model is involved in R0**: every line in the report is a
measured fact, so the two-week review window reviews *observations*, not prose.

## What it checks

| # | Check | Source |
|---|---|---|
| 1 | Datasource health — are Loki and InfluxDB reachable at all? | `check_datasources_health` |
| 2 | Log freshness per `job` — newest entry age; "is anything arriving?" | `query_loki_logs` / `query_loki_stats` |
| 3 | Error volume per `job` — line-filtered counts over the window | `query_loki_logs` |
| 4 | InfluxDB buckets — bucket list | `query_influxdb` |

Ends with a one-line **verdict** so the reader does not have to infer it.

**It does NOT check network device up/down.** That was the gap behind "why
didn't the NOC notice my UDM device offline?" (2026-10-30): the agent watches
*log/datasource* health, not device state. `unpoller` records UniFi
gateway/AP/switch state into InfluxDB's `network_metrics` bucket, which this
agent already has read access to — so a device-offline check is a future
addition to `noc.py`, not new plumbing. See "Roadmap" below.

## Why this file exists (and the repo it's in)

The **first** build of this agent (image `0.1.0`, 2026-10-27) shipped with its
source living nowhere but *inside the image* (`/app/noc.py`) — there was no
repo, no git history, and no way to patch it except exporting the image from a
k3s node and hand-editing the extracted file. Recovering it on 2026-10-30
meant exactly that: `k3s ctr images export` → untar the OCI layers → read
`/app/noc.py`.

That is a bad choice for anything an agent *runs unattended*, so the source is
now here, byte-identical to what was running (sha256 verified at extraction),
matching the `aios/manager/` layout. This directory is the source of truth; the
image is a build artifact of it.

## Layout

| File | Purpose |
|---|---|
| `noc.py` | The agent. Stdlib + `pyjwt` only — speaks raw HTTP to the MCP gateway and GitHub. |
| `Dockerfile` | `python:3.12-slim` + `pyjwt[crypto]`. Build context is the **repo root**. |
| `requirements.txt` | Pinned `pyjwt[crypto]==2.9.0`. |
| `selftest_noc.py` | Offline test of report assembly + Loki/Influx parsing (no network). |
| `k8s/cronjob.yaml` | The CronJob (`15 6,18 * * *` America/Chicago). |
| `k8s/networkpolicy.yaml` | Egress-only: DNS + 443-anywhere (MCP is RFC1918). |
| `k8s/infisical-secret-sync.yaml` | Infisical `jarvis-jatg` → `aios-noc-secrets`. |
| `k8s/kustomization.yaml` | Kustomize wrapper, `namespace: ai`. |

## Config (env)

| Var | Default | Notes |
|---|---|---|
| `BIFROST_VK_AIOS_NOC` | (required, secret) | Read-only VK with the `grafana_noc` MCP grant |
| `GITHUB_APP_ID` / `GITHUB_APP_PRIVATE_KEY` / `GITHUB_APP_REPO` | (required, secret) | Report comment identity |
| `NOC_ISSUE_NUMBER` | (required) | Report target (`17`) |
| `GITHUB_INSTALLATION_ID` | `168700840` | Hard-coded for the single install |
| `BIFROST_URL` | `https://llm.caehomelab.com` | MCP gateway |
| `NOC_WINDOW_HOURS` | `24` | "What changed" window |
| `NOC_STALE_HOURS` | `6` | A silent source older than this is reported STALE |
| `NOC_LOKI_UID` / `NOC_INFLUX_UID` | `loki` / `dfdkew37wk1dse` | Datasource UIDs |
| `NOC_EXPECTED_JOBS` | (empty) | Explicit job list; empty = discover from Loki. Setting it lets the report flag a source that **vanished entirely**. |
| `NOC_DRY_RUN` | (unset) | `1` prints the report instead of posting (used by tests/ops) |

## Build & deploy

```bash
# Build (context = repo root, so the COPY path aios/noc/noc.py resolves):
docker buildx build --platform linux/amd64 --load \
  -t caehomelab/aios-noc:0.1.1 --file aios/noc/Dockerfile .

# Import to BOTH amd64 workers (k3s import is node-local; pods float):
for n in 192.168.30.60 192.168.30.251; do
  docker save caehomelab/aios-noc:0.1.1 | gzip | \
    ssh cengel@$n 'gunzip | sudo -n k3s ctr -n k8s.io images import -'
done

# Apply:
kubectl kustomize aios/noc/k8s | ssh util-server kubectl apply -f -

# Run once now (skips the schedule):
ssh util-server 'kubectl -n ai create job --from=cronjob/aios-noc noc-manual'
ssh util-server 'kubectl -n ai logs job/noc-manual'
```

## Salient design notes / traps

- **MCP handshake:** `initialize` once before any `tools/call`.
- **Bifrost answers `application/json`** for non-streamed calls (not SSE).
- **Loki `query_loki_stats` accepts ONLY label matchers** — no aggregations, no
  line filters. So the unfiltered count uses stats (exact); a *filtered* count
  uses `query_loki_logs` and counts returned rows, reported as "at least N".
- **Zero total is not health.** A job with no entries in the window is reported
  as ⚪ / stale, never as a green "0 errors" (that would contradict check 2).
- **Every timestamp in the report is explicit** — a stale report must never read
  as current state.
- **GitHub comments API is oldest-first and ignores sort/direction** — any
  future "read the thread" logic must page forward.

## Roadmap (next additions)

1. **Device-offline check** — query `network_metrics` in InfluxDB for UniFi
   device state (gateways/APs/switches), compare against the expected set, flag
   anything down. This is the check whose absence prompted the source-from-image
   rescue above.
2. **Delivery** — the agent currently posts *only* to issue #17, so findings can
   be missed. Route findings to `pushover-bridge` (already deployed in-cluster)
   for anything above a threshold (e.g. a 🔴 or "attention needed" verdict).
3. **Per-bucket Influx freshness** — the docstring once claimed this; the code
   only lists bucket names. Add newest-write-age per bucket.
4. **`NOC_EXPECTED_JOBS` in the manifest** — set it so a source that disappears
   entirely (not just goes stale) is flagged.

## Tiers

R0 — read-only. The `aios-noc` Virtual Key's `tools_to_auto_execute` is empty
and the only GitHub write is the report comment itself. Promotion to R1 (an
action type) only after the two-week R0 review window per `ai-it-team.md`.
