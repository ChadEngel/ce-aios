# Inbox Console — appliance + Entra onboarding (M0 in flight)

Per-user management console for the deterministic mail digest agent (spec:
`aios/roadmap/inbox-console.md`, tracking issue: ce-aios #60).

## M0 flow (who does what)

1. **You (Chad / each appliance user)** — on a machine with the `az` CLI:
   ```
   az login
   bash appliance/entra-app.sh
   ```
   Prints `TENANT_ID` and `CLIENT_ID`. The registration is a public client
   (no secrets), single-tenant, with delegated scopes
   `User.Read, Mail.Read, Mail.ReadWrite, Contacts.Read` and **no Mail.Send
   — it does not exist on the registration, so the appliance cannot send.**
2. **Assistant** — runs the proof-of-concept:
   ```
   python3 appliance/poc/msal_poc.py --tenant <TENANT_ID> --client <CLIENT_ID>
   ```
   (add `--device` for a headless run). You complete the browser or device-code
   login; the script prints the identity card (UPN binding = "who this setup
   is for"), a real folder list, the address-book count (fixes the sent-folder
   contact proxy), and the granted-scope line proving `Mail.Send` is absent.
3. **Gate:** identity card correct + folders render + `Mail.Send in grant?
   False` → M0 pass, M1 (engine extraction + read-only dashboard) starts.

## Layout

```
appliance/
  entra-app.sh      one-command Entra app registration (az CLI)
  poc/msal_poc.py   OAuth PoC — delegated Graph login, UPN, folders, contacts
  README.md         this file
  (M4) packer/ + ignite/ + versioned container build for the micro-VM images
```

Security notes: public client (no client secret), PKCE loopback on
`:8614`, device-code fallback, MSAL in-memory for the PoC (the appliance adds
an encrypted cache — `systemd-creds`/age — in M3/M4). Conditional Access on
the tenant applies automatically; failures fail closed.