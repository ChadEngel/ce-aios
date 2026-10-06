# T4/T5 — deploy + prove runbook (review then go)

**Status:** staged, **not executed**. Everything below is validated with
`kubectl apply --dry-run=server`. T4 = #30, T5 = #31.

---

## Preconditions (already true at last check)

- Grafana service-account token present in Infisical as
  `GRAFANA_SERVICE_ACCOUNT_TOKEN` — **verified live**, read-only
  (`sa-1-ai-token`; `POST /api/folders` → 403).
- `bifrost-pvc-local` is already the live PVC (your migration is applied),
  so this does not touch storage.
- `/app/data/config.json` is currently **absent** in the running pod.

---

## Step 1 — apply (one command)

```bash
./scripts/deploy-mcp-grafana.sh
```

What it does, in order:
1. Applies `infisical-secrets-sync.yaml` → operator creates K8s Secret
   `mcp-grafana-secrets`. **Waits** for it before starting the pod.
2. Applies `mcp-grafana/` → Deployment + Service + NetworkPolicy.
3. Applies `bifrost/kustomization.yaml` → `bifrost-config` ConfigMap + mount.
4. `rollout restart deployment/bifrost` + `rollout status`.
5. Prints MCP clients + virtual keys for verification.

> ⚠️ The script applies the **working-tree** Bifrost manifest, which includes
> your unstaged PVC migration. That migration is already live, so the apply is
> a no-op for the PVC (`unchanged`, confirmed by server dry-run).

---

## Step 2 — verify registration

```bash
# MCP client present, state connected
kubectl -n ai exec deploy/bifrost -- \
  wget -qO- http://localhost:8080/api/mcp/clients
# expect: "grafana-noc" among clients

# Virtual key present, and the two pre-existing keys survived the merge
kubectl -n ai exec deploy/bifrost -- \
  wget -qO- http://localhost:8080/api/governance/virtual-keys
# expect: aios-noc, ce-key, ce-pi-macbook
```

**Merge-safety check (important):** `ce-key` and `ce-pi-macbook` must still be
present. `source_of_truth` defaults to `split`, so the file `mcp` section is
additive and the VK matches by name. If either pre-existing key is gone, stop
and roll back (Step 5) — that would mean the semantics differ from what the
source review concluded.

---

## Step 3 — the T5 proof (one read-only tool, end-to-end)

The `aios-noc` key value is **auto-generated** (`sk-bf-…`); read it first:

```bash
kubectl -n ai exec deploy/bifrost -- \
  wget -qO- http://localhost:8080/api/governance/virtual-keys \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print([k['value'] for k in (d.get('virtual_keys') or []) if k['name']=='aios-noc'][0])"
```

Then list the tools the NOC key can see and run one LogQL query through
Bifrost (exact request shape to be confirmed against the live MCP endpoint —
this is the point of T5: prove it, don't assume it).

Suggested first proof: a `labels`/`query_loki_logs` call against Loki, or a
trivial `list_datasources`, through the `grafana-noc` client as `aios-noc`.

Success criteria:
- Tool call returns real Grafana/Loki data (not 403/401).
- `tools_to_auto_execute: []` — nothing ran without an explicit tool call (R0).

---

## Step 4 — record + close

- Comment results on #31; if clean, close #31; then #30.
- Note the `aios-noc` key value lands in Infisical/session notes as needed —
  **do not commit it** (generated key; treat like a secret).
- Update `t4-noc-mcp-design.md` Finding 3 from "written" → "applied + proven".

---

## Step 5 — rollback (if needed)

```bash
kubectl -n ai delete -f clusters/util-server/applications/mcp-grafana/kustomization.yaml
kubectl -n ai delete configmap bifrost-config
# remove the subPath mount: re-apply the pre-T4 Bifrost Deployment, or
kubectl -n ai rollout restart deployment/bifrost
```

No PVC/state is destroyed by rollback.

---

## Open follow-ups (not blockers)

- **Q-T4-5:** caller auth (`MCP_GRAFANA_SERVER_TOKEN`) — needs **two** secret values (Bifrost sends
  header values verbatim, no auto-`Bearer`; mcp-grafana requires `Bearer `).
  Currently compensated by NetworkPolicy.
- **Q-T4-6:** remove orphan `GRAFANA_API_TOKEN` from Infisical.
- **Q-T4-7:** `subPath` does not hot-reload → restart to apply ConfigMap edits.
