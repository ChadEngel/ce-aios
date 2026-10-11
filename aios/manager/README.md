# aios-manager

GitHub Issues manager for AIOS. In-cluster service that polls the aios-manager
GitHub App's installation, lists open issues, and logs structured summaries
to stdout (Loki picks them up). Phase 1 MVP — proves the in-cluster wiring
without yet implementing the LLM/decision loop.

## Components

| File | Purpose |
|---|---|
| `aios_manager.py` | The service. Polls every `POLL_INTERVAL` seconds (default 900 = 15 min, per issue #2). Structured JSON to stdout. |
| `Dockerfile` | Slim `python:3.12-slim` + deps. ~50 MB final. |
| `requirements.txt` | `PyJWT[crypto]>=2.8.0` (RS256 needs cryptography), `requests>=2.32.0`. |
| `test_github_app.py` | Developer-machine smoke test. **Not** deployed; for local verification. See PR #41 for the fixes to PyJWT 2.x `iss` strictness + the `/access_tokens` endpoint. |
| `k8s/infisical-secret-sync.yaml` | `InfisicalSecret` CRD that pulls `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_REPO` from Infisical `jarvis-jatg` project into a K8s `Secret` named `aios-manager-secrets`. |
| `k8s/deployment.yaml` | The Deployment. Mounts the synced Secret as env vars. Includes liveness/readiness probes and a graceful-shutdown `preStop`. |
| `k8s/networkpolicy.yaml` | Egress lockdown: DNS to k3s CoreDNS + GitHub API IP ranges. No ingress. |
| `k8s/kustomization.yaml` | Kustomize wrapper. `namespace: ai`. |

## Source of truth: `jarvis-jatg` (not `caehomelab-v1q6`)

The GitHub App credentials live in the **`jarvis-jatg`** Infisical project
(under the AIOS knowledge base at `ChadEngel/ce-aios`). They are *not* in
`caehomelab-v1q6` (the lab project). Rationale: see issue #43 and the
`decisions.md` entry "Tenant confirmed: personal `engelmn.com`" pattern —
agent credentials belong in the agent project, infrastructure creds in the
infrastructure project.

## Deploy

One-time prerequisites (human-executed):

1. **Create the `jarvis-jatg-universal-auth` K8s Secret in `ns ai`.**
   The operator authenticates to Infisical via a Universal-Auth
   `clientId` + `clientSecret` pair stored in a K8s `Secret`. Universal
   Auth credentials are per-project, so the existing
   `infisical-universal-auth` in `ns ai` (which is bound to
   `caehomelab-v1q6`) won't work. Steps:
   - Infisical UI → Organization Settings → Machine Identities
   - Open the `jarvis-jatg` identity → **Universal Auth** tab
   - Add a configuration: project = `jarvis-jatg`, env = `prod`,
     role = **read-only** (operator only reads)
   - Copy the generated `clientId` + `clientSecret`
   - On `util-server`:
     ```bash
     kubectl -n ai create secret generic jarvis-jatg-universal-auth \
       --type=Opaque \
       --from-literal=clientId='<paste>' \
       --from-literal=clientSecret='<paste>'
     ```

2. **Build + push the image.** No external registry in the lab; use the
   in-cluster registry or `k3s ctr image import`:
   ```bash
   docker build -t caehomelab/aios-manager:0.1.0 aios/manager/
   docker save caehomelab/aios-manager:0.1.0 | ssh util-server k3s ctr image import -
   ```
   Or with a registry: `docker push` to whatever registry the lab uses.

3. **Apply the manifests** (from the lab repo `ce-ai-home-lab` per the
   project convention — `aios/manager/k8s/` is mirrored there):
   ```bash
   # Confirm dry-run first
   kubectl apply --dry-run=client -k aios/manager/k8s/
   # Then apply for real
   kubectl apply -k aios/manager/k8s/
   ```

4. **Verify the chain:**
   ```bash
   # Operator synced the secret?
   kubectl -n ai get secret aios-manager-secrets -o yaml
   # Pod started?
   kubectl -n ai get pods -l app.kubernetes.io/name=aios-manager
   # Logs flowing?
   kubectl -n ai logs -l app.kubernetes.io/name=aios-manager --tail=20
   # Loki has them?
   # In Grafana: {service="aios-manager"} (after Loki index catches up)
   ```

5. **Smoke test in-cluster** (proves the in-cluster wiring matches the
   dev-machine verify from PR #41):
   ```bash
   kubectl -n ai exec -it deploy/aios-manager -- \
     python -c "
   import os, jwt, time, requests
   priv = os.environ['GITHUB_APP_PRIVATE_KEY']
   tok = jwt.encode({'iat': int(time.time())-60, 'exp': int(time.time())+600,
                     'iss': str(int(os.environ['GITHUB_APP_ID']))}, priv, algorithm='RS256')
   r = requests.post('https://api.github.com/app/installations/168700840/access_tokens',
                     headers={'Authorization': f'Bearer {tok}'}, timeout=10)
   r.raise_for_status()
   it = r.json()['token']
   r = requests.get('https://api.github.com/repos/ChadEngel/ce-aios/issues',
                    headers={'Authorization': f'token {it}'}, timeout=10)
   print(f'issues: {len(r.json())}')
   "
   ```

## Configuration

| Env var | Source | Default | Notes |
|---|---|---|---|
| `GITHUB_APP_ID` | Infisical (jarvis-jatg) | (required) | App numeric id |
| `GITHUB_APP_PRIVATE_KEY` | Infisical (jarvis-jatg) | (required) | RSA PEM, may contain `\n` |
| `GITHUB_REPO` | Infisical (jarvis-jatg) | `ChadEngel/ce-aios` | `owner/repo` format |
| `GITHUB_APP_INSTALLATION_ID` | Deployment env | `168700840` | Hard-coded for Phase 1; see comment in `aios_manager.py` |
| `POLL_INTERVAL` | Deployment env | `900` | Seconds between polls. Issue #2 spec = 15 min. |
| `LOG_LEVEL` | Deployment env | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

## Observability

- **Logs:** structured JSON to stdout. Loki scrapes via the
  `caehomelab.com/log-source: aios-manager` pod label.
- **Events:**
  - `startup` — once, on process start
  - `poll_ok` — every successful cycle (issue_count, latency_ms, sample_titles[5])
  - `poll_failed` — on any transient error (caught; doesn't crash the pod)
  - `signal_received` — on SIGTERM/SIGINT
  - `shutdown` — on clean exit

## Promotion gates

Per `decisions.md` "Goal is an AI IT team" — every agent goes through:
1. R0 (read-only) for 2 weeks with logged calls
2. Failure-mode list written
3. Then R1/R2 (one action type at a time)

This is currently **R0-ready**: the only actions are `list_issues` (GET, no
side effects) and the health/startup logs. **No writes to GitHub yet** —
that's the next PR after the wiring is proven.

## What's NOT in this MVP

- LLM/decision layer (Bifrost calls, prompt construction, etc.) — next PR
- Write actions (comment on issues, close issues, apply labels) — after R0
- Multi-repo / multi-install support — `INSTALL_ID` is hard-coded
- Metrics endpoint (Prometheus `/metrics`) — not needed yet
- HA (multiple replicas) — `Recreate` strategy, no leader election
