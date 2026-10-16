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
   `kubectl -n ai exec deploy/aios-mail-mcp -- ms-365-mcp-server --login --verify-login`
   Follow the URL + code in the browser, confirm verify passes, then
   `kubectl -n ai rollout restart deploy/aios-mail-mcp` so the server picks
   up the fresh cache.
5. **Bifrost side:** MCP client `graph` registration lives in the LAB repo
   (`ce-ai-home-lab`, PR — `clusters/util-server/applications/bifrost/
   kustomization.yaml`). The `aios-helpdesk` VK grant of the graph client is
   done in the Bifrost **dashboard** (Chad) — the VK already exists there;
   do NOT redeclare it in the git ConfigMap (split-merge overwrite risk).
   Copy the VK key value into Infisical `jarvis-jatg` as
   `BIFROST_VK_AIOS_HELPDESK` (UI, never chat) for the digest job.
6. **T-M2 proof:** from the bifrost pod, POST initialize + `tools/list` to
   `http://aios-mail-mcp.ai:3000/mcp` — enumerate the readOnly tools, then
   add `--enabled-tools` (tighten to the exact read list) + optionally
   `--expected-username <upn>` to the Dockerfile ENTRYPOINT in a small PR.
   Record the proof on issue #21.

## Verify it is up

`kubectl -n ai get pods -l app=aios-mail-mcp` (Running), then from the bifrost
pod: `wget -qO- http://aios-mail-mcp.ai.svc.cluster.local:3000/ --server-response`
— TCP reaching the port is the signal (route checks can 200+HTML).