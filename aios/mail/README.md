# aios-mail-mcp

Microsoft Graph MCP server for the helpdesk mail agent (hire #4, R0 read-only).
Wraps [`@softeria/ms-365-mcp-server`](https://github.com/Softeria/ms-365-mcp-server)
pinned at `0.168.0`. Design + decisions: `aios/roadmap/helpdesk-mail-agent.md`,
`decisions.md` 2026-10-16.

**Mode:** HTTP streamable on `:3000`, `--trust-proxy-auth` (the only caller is
Bifrost via NetworkPolicy — this is what makes daemon mode safe), `--read-only`
(non-GET Graph calls blocked in-server). One cached delegated login = Chad's
mailbox, per the mailbox=primary decision. No secrets injected; the login
cache lives on the `aios-mail-cache` PVC.

## Deploy checklist (in order)

1. **Prereq (shared with aios-manager):** `jarvis-jatg-universal-auth` Secret
   in ns `ai` (steps in PR #44 / issue #43 follow-up). Deploy gate.
2. **Build + import** (Mac Studio, Docker Desktop running):
   `docker build -t caehomelab/aios-mail-mcp:0.1.0 aios/mail/ \
    && docker save caehomelab/aios-mail-mcp:0.1.0 | ssh util-server k3s ctr images import -`
3. **Apply:** `kubectl apply -k aios/mail/k8s/` — wait for
   `kubectl -n ai rollout status deploy/aios-mail-mcp`.
4. **ONE human step — device-code login** (Chad, ~5 min):
   `kubectl -n ai exec -it deploy/aios-mail-mcp -- ms-365-mcp-server --login`
   then `kubectl -n ai rollout restart deploy/aios-mail-mcp`.
   **The login now runs WITHOUT `--read-only` (changed 2026-10-17 with the
   R2 filing promotion):** the filing tier needs `Mail.ReadWrite` (move +
   folder-create + soft-delete), and the MSAL grant must cover the full
   scope set the daemon acquires. Scope-match rule unchanged and load-
   bearing: login flags must MATCH the daemon boot flags — the daemon boots
   WITHOUT `--read-only` (see aios/mail/k8s/deployment.yaml `command/args`),
   so the login must too. Log in with `--read-only` and the filing leg dies
   at every refresh with "Failed to acquire token" after the first access-
   token expiry (~1h); the daemon surface itself is pinned separately by
   `ENABLED_TOOLS` (12 mail reads + move/create/delete only — send, drafts,
   rule/attachment verbs and graph-batch are excluded regardless of scope).
   (Upstream #522 diagnosed the same symptom class earlier; our variant =
   scope mismatch between login and daemon.) Verify with a real tool call
   through Bifrost AND a real, folder-scoped filing probe (move a test
   message and move it back), never a second --verify-login process.
5. **Bifrost side (all in the LAB repo, merged PRs #11/#12):** the `graph`
   MCP client is file-declared (in-memory only — it never appears in
   config_mcp_clients), and file-declared clients can ONLY be granted to a
   VK via the file governance section (aios-noc pattern). The aios-helpdesk
   VK block lives there; its key value is deliberately absent from git
   (value-less declaration preserves the dashboard-issued key).
   The key is in Infisical `jarvis-jatg` as `BIFROST_VK_AIOS_HELPDESK`.
   NOTE: the lab repo merges are NOT live until `kubectl apply -f` the
   kustomization + `rollout restart deploy/bifrost` (merge ≠ apply).
6. **T-M2 proof (PASSED 2026-10-16):** initialize + tools/list through
   llm.caehomelab.com/mcp/graph with the VK → 75 tools, namespaced
   `graph-<tool>` (e.g. `graph-list-mail-messages` — the digest job MUST use
   namespaced names). get-my-profile + list-mail-messages returned real
   Identity/Inbox data. Remaining: tighten `--enabled-tools` to the R0 mail
   subset in a small PR.

## Verify it is up

`kubectl -n ai get pods -l app=aios-mail-mcp` (Running), then from the bifrost
pod: `wget -qO- http://aios-mail-mcp.ai.svc.cluster.local:3000/ --server-response`
— TCP reaching the port is the signal (route checks can 200+HTML).

## Image arch note (multi-node cluster)

The cluster is MIXED-ARCH: caelx002/caelx003 are amd64, util-server (control-
plane) is arm64. The pod lands where the local-path PVC binds; import the image
matching THAT node's arch under the same tag:

- amd64 (caelx002/caelx003): `docker buildx build --platform linux/amd64 --load \
  -t caehomelab/aios-mail-mcp:0.1.0 aios/mail/` then `docker save \
  | ssh cengel@<node-ip> 'sudo -n k3s ctr -n k8s.io images import -'`
- arm64 (util-server): plain `docker build`, `ssh util-server`, same import
- Both nodes are `cengel@` + passwordless sudo (`sudo -n k3s ctr ...`)