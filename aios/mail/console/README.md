# AIOS Inbox Console — read-only dashboard + dry-run preview (0.6.0)

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
- **Model in use**: `prose_model` + `privacy_model`.
- **Config journal (D5)**: last config saves (actor/timestamp/version).
- **Fire status**: filer/digest CronJob schedules, last fire, ok/failed
  (in-cluster read-only serviceaccount; Role = get/list batch only).

What it computes (`POST /api/dryrun`, no persistence anywhere):
- **Dry-run preview**: a hypothetical config (validated, unknown keys and
  destructive values refused with 400) is classified against the newest
  ledger items and diffed against the CURRENT rules — groups of
  "from → to" with sample subjects. Nothing is saved, nothing is moved.
  The contacts tier ("you emailed this person") is excluded from previews —
  it needs live sent-folder data the ledger does not carry.

Boundaries (decisions.md D3/D7): zero mutation endpoints; mail content shown
is limited to the ledger's own subject/sender/preview snippets; no GitHub,
LLM, or mail-API calls from this app. Editing UI ships later — deliberately
not in the same image.

## Files
- `console.py` — FastAPI app (auth, `/api/state`, `/api/dryrun`)
- `static/index.html` — single-page dark UI
- `selftest_console.py` — offline tests incl. byte-identical state-dir proof
- `k8s/` — SA + read-only Role + Deployment + NodePort Service + netpol

## Build & deploy
```bash
docker buildx build --platform linux/amd64 --load \
  -t caehomelab/aios-mail-console:0.6.0 \
  --file aios/mail/console/Dockerfile aios/mail/

kubectl -n ai create secret generic aios-console-auth \
  --from-literal=password='CHANGE-ME'
kubectl kustomize aios/mail/console/k8s | ssh util-server kubectl apply -f -
# import image on both workers, then: http://<node>:30080
```