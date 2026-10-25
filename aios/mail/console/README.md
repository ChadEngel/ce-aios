# AIOS Inbox Console — dashboard + config editing (0.6.6)

The web front end of `aios/mail/`. One box, one mailbox (D4). This app has
**no mutation endpoints** — it can never move, delete, or send mail, and the
`/state` PVC is mounted **read-only** so even a code defect cannot corrupt
the ledger (filing is recoverable; missing mail is not — the console cannot
make mail disappear by construction).

What it shows (`GET /api/state`):
- **Ledger truth**: items, in-inbox estimate, filed-by-folder, unresolved
  intents (red alert if > 0), items awaiting the next digest report.
- **Effective rules with provenance**: every key's value and where its value
  comes from — `file` (config.yaml on /state actually CHANGES the env value),
  `env` (k8s manifest), `default` (engine default).
- **Config journal (D5)**: last config saves (actor/timestamp/version).
- **Fire status**: filer/digest CronJob schedules, last fire, ok/failed
  (in-cluster read-only serviceaccount; Role = get/list batch only).
- **Digest cadence (R1)**: the effective `digest_schedule` + `digest_tz` and
  the next 5 fire times, so the dashboard reflects what the filer-heartbeat
  reconciler enforces. Read-only — the console only reports it; validation
  (croniter + 1-fire/hour floor) runs when the config store is loaded.
- **Models (R2)**: the cached model catalogue with per-model suitability for
  both legs (`prose` needs >=8k context; both must be LOCAL — a remote id is
  refused at save). The catalogue is fetched by the filer (which holds the
  gateway key) and read here read-only; this console makes no LLM calls.
- **One-time-code tier (0.6.4)**: keeps login/2FA/verification mail in the
  inbox. `code_on` / `code_llm_on` / `code_domains` / `code_subjects` are
  editable like any other config key; code-kept mail shows in the dashboard's
  keeps list with the "one-time code" reason. (Code *content* is never posted
  to the digest issue — only a count; the console shows the ledger's snippet,
  which is the same rule as every other tier.)

What it computes (`POST /api/dryrun`, no persistence anywhere):
- **Dry-run preview**: a hypothetical config (validated, unknown keys and
  destructive values refused with 400) is classified against the newest
  ledger items and diffed against the CURRENT rules — groups of
  "from → to" with sample subjects. Nothing is saved, nothing is moved.
  The contacts tier ("you emailed this person") is excluded from previews —
  it needs live sent-folder data the ledger does not carry.

Boundaries (decisions.md D3/D7): the console **never touches mail** — no move,
file, delete, send, no Graph/LLM/GitHub. Its **only** mutation is the config
store, and even that is structurally isolated: the ledger's `/state` mount
stays **read-only**, while the config store subdir (`config/`) is mounted
separately **read-write**. A console bug can edit rules but cannot open
`ledger.jsonl` for writing, whatever path it builds. Mail content shown is
limited to the ledger's own subject/sender/preview snippets.

Editing shipped as build-order **step 5** — after the read-only console proved
true over a live window.

## Editing (step 5)
- **`POST /api/config/diff`** — preview a config edit: field-level old→new diff
  + resulting version. Pure compute (writes nothing), same as `/api/dryrun`.
- **`POST /api/config/save`** — the write path. Requires `confirm:true`;
  validates (unknown keys, negative retention, remote models, bad schedules →
  400), journals the actor (D5), then writes the **complete** snapshot. A no-op
  edit writes nothing.
- **`/api/dryrun`** — shows the effect an edit would have on real mail (grouped
  from→to with samples), writing nothing.
- The UI renders the config diff and requires an explicit **Save**; the actor
  (`CONSOLE_ACTOR`, e.g. `console:chad`) is recorded per edit.

## Files
- `console.py` — FastAPI app (auth, `/api/state`, `/api/dryrun`)
- `static/index.html` — single-page dark UI
- `selftest_console.py` — offline tests (65) incl. byte-identical state-dir
  proof, the R1/R2 views, and the step-5 write path (diff/save/journal,
  destructive-edit refusal, and proof the ledger is untouched by saves)
- `k8s/` — SA + read-only Role + Deployment + NodePort Service + netpol

## Build & deploy
```bash
docker buildx build --platform linux/amd64 --load \
  -t caehomelab/aios-mail-console:0.6.6 \
  --file aios/mail/console/Dockerfile aios/mail/

kubectl -n ai create secret generic aios-console-auth \
  --from-literal=password='CHANGE-ME'
kubectl kustomize aios/mail/console/k8s | ssh util-server kubectl apply -f -
# import image on both workers, then: http://<node>:30080
```