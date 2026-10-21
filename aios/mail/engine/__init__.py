"""aios_mail_engine — the deterministic mail core, extracted from digest.py.

The engine speaks ONLY (1) the MailGraph port (a tiny duck-typed adapter,
documented in filing.py) and (2) a Config object. It does not know about
Bifrost, MCP, GitHub, or k8s — those are app-layer adapters living next to
digest.py/filer.py. This is roadmap M1: the seam that lets the same proven
engine be wrapped by a WebUI (inbox-console), a direct-Graph client, or an
appliance, without forking the classification rules that are already trusted.

Modules:
  config.py    versioned YAML config store + append-only change journal
  classify.py  deterministic bucketing (the model NEVER classifies)
  filing.py    folder-map/ensure/move/delete primitives over the port
  retention.py bounded soft-delete clear with the F5 ledger-confirmation gate

Invariants carried over from 0.4.0, unchanged:
  - Filing is recoverable; missing mail is not. Mutations are logged before
    attempted (write-ahead, ledger lives in the app layer next to state.py).
  - The model can only PROTECT mail, never file/delete it (D3).
  - Keyword/pattern matching uses subject + sender name only, never bodies.
"""