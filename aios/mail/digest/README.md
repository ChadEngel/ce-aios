# aios-mail-digest — R0 mailbox digest, three fires/day (issue #50)

Fires at **05:00, 12:00, 17:00 America/Chicago** (`timeZone`-pinned). Each run:
unread inbox via Bifrost → `graph-list-mail-messages` (read-only) + recent
sent-folders recipients → **two-tier digest** posted to
[issue #50](https://github.com/ChadEngel/ce-aios/issues/50) via the AIOS
GitHub App, summarized by LOCAL `ollama/muse-glimmer` (`reasoning_effort: none`).

**Two-tier behavior (Chad's priorities, 2026-10-16):** "Priority" = mail from
people Chad has sent email to (sent-items top-50, `DIGEST_SENT_TOP`) or
keyword hits (`DIGEST_PRIORITY_KEYWORDS`: bluff creek, ccha, crossbar,
bearpath, flag football, cc united — env-tunable, no rebuild). Priority bullet
items RE-ALERT every fire while still unread (they are the someday-stay-in-
inbox class); everything else (newsletters/marketing/junk) reports ONCE, then
the job goes quiet: the comment footer carries a `<!--aios-tracker|YYYY-MM-DD|ids:…-->`
ledger (sha1 of Graph message ids — their shared prefix makes truncation
collide), the next fire reads it, and a fire with nothing new posts nothing.
Priority items will be filed automatically only as a deliberate later decision
(R1/R2 write-access tier).

## Deploy checklist (ordered)

1. **Build the image (amd64 — workers run it):**
   `docker buildx build --platform linux/amd64 --load -t caehomelab/aios-mail-digest:0.2.3 aios/mail/digest/`
2. **Import on BOTH amd64 workers** (k3s import is node-local; the job has no
   PVC and only `nodeSelector: kubernetes.io/arch: amd64`, so either can host it):
   ```bash
   docker save caehomelab/aios-mail-digest:0.2.3 | \
     ssh cengel@192.168.30.60  'sudo -n k3s ctr -n k8s.io images import -'
   docker save caehomelab/aios-mail-digest:0.2.3 | \
     ssh cengel@192.168.30.251 'sudo -n k3s ctr -n k8s.io images import -'
   ```
3. **Sync + apply:** jarvis-jatg-universal-auth must exist in ns `ai` (it does),
   then: `kubectl kustomize aios/mail/digest/k8s | ssh util-server kubectl apply -f -`.
   This creates InfisicalSecret `aios-mail-digest-secrets-sync` → Secret
   `aios-mail-digest-secrets` (4 keys: GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY,
   GITHUB_APP_REPO, BIFROST_VK_AIOS_HELPDESK), the egress netpol, and the CronJob.
   Wait ~30s for the operator sync before launching a Job.
4. **Manual proof run:** `kubectl -n ai create job --from=cronjob/aios-mail-digest aios-mail-digest-manual`
   → `kubectl -n ai logs job/aios-mail-digest-manual` → expect a comment on #50.
   Cleanup: `kubectl -n ai delete job aios-mail-digest-manual`.
5. **Verify guards:** `kubectl -n ai exec -it deploy/bifrost -- sh` probe →
   digest pod port is NOT reachable; only egress flows are logged.

## Failure modes to watch (R0 review window, ref #21)

- Job fails with `Failed to acquire token` → the mail server's read-only grant
  went stale again (see aios/mail/README.md step 4 — login carries --read-only).
- `LLM: HTTP 4xx/empty content` → VK or muse-glimmer registry drift; check
  `/v1/models` with the same VK (expect only `ollama/muse-glimmer:latest`).
- Silent no-comment → CronJob schedule hit (07:00 America/Chicago), job logs on
  the node, backoffLimit 2, ttl 3d. Loki carries stdout, no mail bodies.