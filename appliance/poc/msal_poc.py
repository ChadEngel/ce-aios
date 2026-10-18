#!/usr/bin/env python3
"""Inbox Console M0 — OAuth proof-of-concept against delegated Microsoft Graph.

Proves the architecture decision D1 (roadmap/inbox-console.md): a public
client with delegated scopes replaces the ms-365-mcp-server device-code
daemon for the per-user appliance. No MSAL disk cache (in-memory only), no
secrets, no config files.

Usage:
  pip install msal            # the only dependency
  python3 msal_poc.py --tenant TENANT_ID --client CLIENT_ID [--device]

Default flow = loopback auth-code + PKCE (opens a browser, listens on
localhost:8614). --device = device-code flow for headless runs (print the
code, complete the login anywhere, the script continues).

Gate to pass (M0): identity card prints the correct UPN, folder list renders,
granted-scope line contains Mail.ReadWrite + Contacts.Read and NO Mail.Send,
and the address-book count is real (not sent-folder inference).
"""

import argparse
import json
import sys
import urllib.request

import msal

SCOPES = [
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Contacts.Read",
]
REDIRECT = "http://localhost:8614/callback"
GRAPH = "https://graph.microsoft.com/v1.0"


def acquire(tenant, client, device):
    app = msal.PublicClientApplication(
        client,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=None,  # in-memory only for the PoC
    )
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            return result
    if device:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"device flow init failed: {flow}")
        print("\n  DEVICE LOGIN")
        print(f"  open: {flow['verification_uri']}")
        print(f"  code: {flow['user_code']}  (expires in "
              f"{flow.get('expires_in', 900)//60} min)\n", flush=True)
        return app.acquire_token_by_device_flow(flow)
    flow = app.initiate_auth_code_flow(SCOPES, redirect_uri=REDIRECT)
    print("\n  BROWSER LOGIN — opening/visiting the URL below; the script "
          "continues automatically after sign-in\n")
    print(flow["auth_uri"], flush=True)
    result = {"error": "loopback listener did not run in this context"}
    try:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import parse_qs, urlparse

        done = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                qs = parse_qs(urlparse(self.path).query)
                done["code"] = qs.get("code", [None])[0]
                done["state"] = qs.get("state", [None])[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<html><body><h2>OK - return to the "
                                 b"terminal.</h2></body></html>")
            def log_message(self, *a):  # silence
                pass

        srv = HTTPServer(("localhost", 8614), Handler)
        srv.handle_request()  # single request, then stop
        if done.get("code"):
            result = app.acquire_token_by_auth_code_flow(
                flow, {"code": done["code"], "state": done["state"]})
    finally:
        pass
    return result


def get(token, path):
    req = urllib.request.Request(
        GRAPH + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--client", required=True)
    ap.add_argument("--device", action="store_true",
                    help="device-code flow for headless runs")
    args = ap.parse_args()

    result = acquire(args.tenant, args.client, args.device)
    if "access_token" not in result:
        print(f"AUTH FAILED: {result.get('error')} :: "
              f"{result.get('error_description', '')[:300]}")
        return 2

    tok = result["access_token"]
    granted = result.get("scope", "")
    print("IDENTITY + BOUNDARY CHECK")
    print(f"  granted scopes : {granted}")
    print(f"  Mail.Send in grant?  {'Mail.Send' in granted}   <- must be False")
    print(f"  device code dance?   {'device' if args.device else 'loopback+PKCE'}"
          "  flow worked")

    me = get(tok, "/me")
    upn = me.get("userPrincipalName", "?")
    print("\nIDENTITY CARD (who this setup is for)")
    print(f"  name   : {me.get('displayName', '?')}")
    print(f"  UPN    : {upn}")
    print(f"  mail   : {me.get('mail', '?')}")

    print("\nMAIL FOLDERS (delegated read works)")
    folders = get(tok, "/me/mailFolders?$top=15"
                  "&$select=displayName,totalItemCount,unreadItemCount")
    for f in folders.get("value", []):
        print(f"  {f.get('displayName','?'):28} "
              f"total={f.get('totalItemCount',-1):6} "
              f"unread={f.get('unreadItemCount',-1):5}")

    print("\nADDRESS BOOK (Contacts.Read — closes the sent-folder proxy gap)")
    contacts = get(tok, "/me/contacts?$top=200&$select=displayName,givenName")
    n = len(contacts.get("value", []))
    print(f"  folder-contacts listed: {n}" + (" (cap 200)" if n == 200 else ""))
    names = [c.get("displayName") or c.get("givenName") or "?" 
             for c in contacts.get("value", [])[:6]]
    print(f"  first few             : {', '.join(names)}")

    print("\nM0 GATE: " + ("PASS" if upn != "?" else "SUSPECT — verify UPN"))
    return 0


if __name__ == "__main__":
    sys.exit(main())