# Session handoff — AIOS mail digest + inbox console (2026-10-21)

> **Purpose:** pick-up-where-we-left-off context for a fresh machine/location.
> This file is the working memory of the 2026-10-16→21 engineering arc on the
> mail digest (`aios/mail/`): what shipped, what is live, the runbook, the
> gotchas, and the queue. Durable decisions/specs live elsewhere: behavior +
> decisions in `helpdesk-mail-agent.md` + `README.md`; console plan + model
> guidance in `inbox-console.md` (§9–§11 are current); incident record inbox-console.md §11.
> **No secret VALUES are in this repo** — secret names + locations only (§8).

## 1. Where the repo stands

| Thing | State |
|---|---|
| Branch / merge | **PR #65 (`fix/privacy-keep`) merged → `main` `bfe3539`** (2026-10-21). Headline commits: `1a01eb9` engine extraction + config store (0.5.0/0.5.1), `912f58c` merge fail-open (0.5.2), `731f9e6` console 0.6.0 + incident hardening |
| License question | DISMISSED by Chad (repo public, no license — fine) |
| Cluster vs repo | **Live cluster == `main`** — verified post-merge (digest 0.5.3, console 0.6.0, config `v6635667ca6` across legs) |
| Selftests | engine 33/33, state 21/21, console 20/20 (all offline, section §6) |

## 2. What shipped (3–4 sessions, 0.3.x → 0.6.0)

1. **Write-ahead ledger + 5-min filer (0.4.x)** — every filing decision is
   journaled (`intent` row) BEFORE the mailbox mutation, outcome row after;
   `is_settled()` dedupe gate; crash between the two = reconciler heals.
   Invariant: **never miss any email** — filing is recoverable, missing mail
   is not. All rule changes must preserve this.
2. **`aios_mail_engine` extraction (M1, 0.5.x)** — `aios/mail/engine/`
   package speaks ONLY (a) a tiny duck-typed port (`list_folders`,
   `list_folder`, `create_folder`, `move`, `delete`) and (b) a `Config`
   object. `digest.py`/`filer.py` are app adapters; dependency points ONE way.
3. **Versioned YAML config store** — `state/config.yaml` overrides env
   PER-KEY (merge, not replace); invalid file → env fallback with LOUD
   warning (fire must not die over config); unknown keys REJECTED;
   append-only `config-journal.jsonl` (D5); version = "v"+sha256[:10].
   ⚠️ never add inert keys — `digest_schedule`/`digest_tz` ship only WITH
   the reconciler that consumes them.
4. **0.5.2 merge fail-open** — qwen3.8 burned full token budget on the
   merge-call (finish=length ×2) → digest composer now degrades to verbatim
   partials concat with a notice. Degraded-but-delivered beats no digest.
5. **Read-only inbox console (0.6.0)** — FastAPI dashboard + dry-run
   preview; zero mutation endpoints; `/state` mounted readOnly in k8s;
   ledger facts via the SAME `state.py` the legs run; `POST /api/dryrun`
   is pure compute (selftest proves the state dir is byte-identical around
   every preview). Editing UI deliberately absent (build-order step 5).
6. **Rolling ledger backup** — `state.compact()` (the ledger's only
   rewriter) keeps `ledger-prev.jsonl` before every rewrite (2026-10-21
   incident compensation; see §7).
7. **Rule identity across legs is STRUCTURAL** — ConfigMap
   `aios-mail-rules` (digest/k8s/rules-configmap.yaml) envFrom'd by digest
   cronjob + filer + console. One rules object; a leg missing the envFrom
   visibly diverges in the console's version chip.

## 3. Live cluster state (namespace `ai`)

| Object | Value |
|---|---|
| Digest CronJob | `aios-mail-digest`, image `caehomelab/aios-mail-digest:0.6.4`, `imagePullPolicy: Never`, `0 5,12,17 * * *` America/Chicago |
| Filer CronJob | `aios-mail-filer`, same image, `*/5 * * * *`, pages inbox fully (FILER_PAGE_SIZE 100) |
| Console | Deployment `aios-console`, image `caehomelab/aios-mail-console:0.6.6`, Service NodePort **30080**, HTTP Basic auth (secret `aios-console-auth`, key `password` — Infisical-owned, see §8) |
| State | PVC `aios-mail-state` 1Gi RWX nfs-client uid `pvc-239dc1e3-c659-422f-afc2-54b5cf446596` — **this volume is NEVER deleted/recreated again** (see §7; verify uid unchanged during any pickup) |
| Ledger | `/state/ledger.jsonl` (+ compact's `ledger-prev.jsonl`); ~1300 rows, 458 items, 0 unresolved; ledger rows whose outcomes were lost in the incident are NOT rebuilt — unconfirmed items stay retention-protected forever (F5) |
| Config version | `v6635667ca6` (source=env, prose/privacy `ollama/qwen3.8:latest`) — must stay identical across digest, filer, console logs |
| Secrets | `aios-mail-digest-secrets` (Infisical sync: read VK `BIFROST_VK_AIOS_HELPDESK`, file VK `BIFROST_VK_AIOS_HELPDESK_FILE` w/ execute list = 5 file tools + no send, GitHub App creds) |
| Netpol | digest/filer: egress-only (443 → llm.caehomelab.com, api.github.com; DNS); console: ingress from 192.168.0.0/16 + 10.42/10.43 nets on 8080, egress 53 + 443 + 6443 |
| Schedule | digest 3×/day; filer 5-min heartbeat; manual fire = `kubectl -n ai create job --from=cronjob/aios-mail-digest <name>` |

## 4. File map

```
aios/mail/engine/{__init__,config,classify,filing,retention}.py   # the library (port+Config only)
aios/mail/digest/{digest,filer,state,selftest_engine,selftest_state,selftest_digest}.py  # legs + ledger + tests
aios/mail/digest/k8s/{cronjob,filer-cronjob,rules-configmap,infisical-secret-sync,state-pvc,networkpolicy}.yaml
aios/mail/console/{console.py,selftest_console.py,Dockerfile,README.md,static/index.html}
aios/mail/console/k8s/{console.yaml,kustomization.yaml}
aios/mail/digest/README.md   # behavior + deploy checklist (incident note at top)
```

## 5. Runbook cheatsheet (exact commands that worked)

```bash
# build (context is aios/mail/, NOT aios/mail/digest/ — engine/ must COPY):
docker buildx build --platform linux/amd64 --load \
  -t caehomelab/aios-mail-digest:0.5.3 --file aios/mail/digest/Dockerfile aios/mail/
docker buildx build --platform linux/amd64 --load \
  -t caehomelab/aios-mail-console:0.6.0 --file aios/mail/console/Dockerfile aios/mail/

# import on BOTH workers (k3s = node-local; pod landed on .251 once and failed
# ErrImageNeverPull until imported there — "total: 0.0 B" in the output is benign):
for n in 192.168.30.60 192.168.30.251; do
  docker save caehomelab/aios-mail-digest:0.5.3 | gzip | \
    ssh cengel@$n 'gunzip | sudo -n k3s ctr -n k8s.io images import -'
done

# apply (kubectl lives on util-server; namespace ai is burned into kustomizations):
kubectl kustomize aios/mail/digest/k8s  | ssh util-server kubectl apply -f -
kubectl kustomize aios/mail/console/k8s | ssh util-server kubectl apply -f -

# manual digest fire + logs:
ssh util-server 'kubectl -n ai create job --from=cronjob/aios-mail-digest digest-manual-x'
ssh util-server 'kubectl -n ai logs job/digest-manual-x'

# console checks from off-LAN (console itself is LAN-only, netpol ingress 192.168/16):
ssh util-server "curl -s -u :\$PASSWORD http://192.168.30.251:30080/api/state"
#   password: base64 of secret aios-console-auth key password — read it there:
ssh util-server 'kubectl -n ai get secret aios-console-auth -o jsonpath={.data.password} | base64 -d'
```

## 6. Selftests (run before ANY deploy)

```bash
cd aios/mail/console && python3 selftest_console.py     # 20 (pip3 install 'fastapi==0.115.6' 'uvicorn==0.32.0' 'pyyaml==6.0.3')
cd aios/mail/digest && \
  BIFROST_VK_AIOS_HELPDESK=x BIFROST_VK_AIOS_HELPDESK_FILE=x \
  GITHUB_APP_PRIVATE_KEY=x GITHUB_APP_ID=x GITHUB_INSTALLATION_ID=x \
  DIGEST_ISSUE_NUMBER=1 GITHUB_REPO=o/r DIGEST_STATE_DIR=/tmp/t-$$ \
  python3 selftest_engine.py                            # 33
  python3 selftest_state.py                             # 21
  python3 selftest_digest.py                            # 32 (new-only reporting + tracker
                                                        #     safety + filer post-trigger)
```

## 7. INCIDENT 2026-10-21 14:23Z (the one thing not to repeat)

The `aios-mail-state` PVC was **deleted and recreated** during the 0.5.x
rollout (new uid 14:23:24Z). nfs-client StorageClass has
`archiveOnDelete: "false"` → the provisioner erased the historic ledger,
config journal, and cursor. Mail/folders were untouched and every gate
failed safe (unconfirmed items are skipped by retention forever), but
pre-Oct-21 audit history is gone. Records: `inbox-console.md` §11,
`digest/README.md` deploy-checklist item 0. Compensations shipped:
rolling `ledger-prev.jsonl` (compact-time), PVC lifecycle edits off-limits,
verify the PVC uid is `pvc-239dc1e3…` during any pickup.

## 8. Secrets surface (names only — values Infisical/cluster-side)

- Infisical project `jarvis-jatg` env `prod` root path — VKs + GitHub app
  (hyphen-free key names; see `k8s/infisical-secret-sync.yaml` header).
- Console password: secret `aios-console-auth`, now OWNED BY INFISICAL
  (PR: `console/k8s/infisical-secret-sync.yaml`, key `CONSOLE_PASSWORD` in
  `jarvis-jatg`/prod). Previously hand-created live and not in git. NOTE: the
  key must exist BEFORE the sync is applied — the console fails OPEN on an
  empty password.
- Bifrost URL is literal env (`https://llm.caehomelab.com`), not a secret.

## 9. Gotchas learned 2026-10-21 (the hard-won ones)

- **Dataclass annotations arrive as type OBJECTS**, not strings — a
  `("int", float)` membership test matches neither; `validate()` was
  silently passing negative ints all through 0.5.x (fixed + regression
  tests). When validating via `f.type`, accept both worlds.
- **`latest_by_id()` merged rows carry the OUTCOME's `"t"`** — never key
  merged-row logic off the type marker; use `status`/`folder` presence.
- **`ECFG.from_dict(partial_dict)` resets every absent key to its DEFAULT**
  — when saving config (future UI), merge onto the CURRENT effective dict
  first (`asdict(from_env())` + overrides), exactly as `load()` does.
- **`svc/kubernetes` (10.43.0.1:443) refuses connections from worker pods**
  on this k3s — pods talk to the API at `192.168.30.217:6443`
  (`CONSOLE_K8S_URL` env pins it in console.yaml).
- **kustomize may not escape its root** — console/k8s references the rules
  ConfigMap BY NAME; the object is owned/deployed by digest/k8s (do not
  fork a copy).
- **k3s import is node-local and pods float between workers** — import BOTH
  workers, always.
- Package-import gotcha: `import engine` does NOT auto-import submodules —
  explicit `from engine import config/filing/retention` everywhere.
- NAS (192.168.30.121) has SSH disabled; NFS exports are reachable via
  mounts from util-server (read-only `mount -t nfs` for forensics).

## 10. The queue (build order was explicit: 1→5)

| Step | Status | Pointer |
|---|---|---|
| 1. config store | ✅ 0.5.x | engine/config.py |
| 2. cfg-version stamped on ledger rows | ✅ 0.5.x | filer.py intent rows `"cfg"` |
| 3. dry-run preview | ✅ 0.6.0 | console /api/dryrun |
| 4. read-only console | ✅ 0.6.0 | console/ (live window runs from now) |
| 5. editing UI (diff-save + journal actor) | ✅ 0.6.3 (console 0.6.4) — 2026-10-24 | inbox-console.md §10b |

**Queue 1→5 is COMPLETE** (verified 2026-10-27). Also shipped since: R1
user-scheduled cadence (0.6.0), R2 model picker (0.6.1/0.6.2), one-time/
verification-code keep tier (0.6.4), console `esc()` render fix (0.6.6).
Repo and cluster agree: digest/filer `0.6.4`, console `0.6.6`.

Design inputs already agreed (2026-10-21, Chad): **R1** user schedules
(`digest_schedule`+`digest_tz` ↔ filer-heartbeat CronJob patcher, RBAC
scoped to exactly that cronjob, croniter validation + next-5-fires preview,
fail-safe keeps old schedule) and **R2** model picker (gateway `/v1/models`,
guidance panels — prose = plain instruct ≥8k ctx/no reasoning/temp 0.2–0.3,
privacy = JSON index array only, local-only, keep-only safe) — full spec in
`inbox-console.md` §9.

Leftovers/parked:
- Empty Outlook `filed` folder — cannot delete (no `delete-mail-folder`
  tool; deliberate VK surface).
- `muse-glimmer` survives only as the harmless code-default fallback string
  in engine/config.py `from_env()` (live value comes from the ConfigMap).
- Multi-user (wife's mailbox): BLOCKED on second Entra app registration +
  identity work (M2+ work; appliance path parked pending the next-person
  destination decision — never answered).
- One-time: consider exposing the console beyond LAN (Tailscale) only after
  the editing milestone lands behind auth + journal.