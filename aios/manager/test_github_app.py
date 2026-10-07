#!/usr/bin/env python3
"""
Test GitHub App credentials without deploying aios-manager.
Prints installations and a sample issue list for ChadEngel/ce-aios.

Requires: pip install PyJWT requests
Usage:
  export GITHUB_APP_ID=...
  export GITHUB_APP_PRIVATE_KEY="$(cat github-app-2026.pem)"
  python3 aios/manager/test_github_app.py
"""
import os
import sys
import time
import jwt
import requests

APP_ID = os.getenv("GITHUB_APP_ID")
PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY")
REPO = os.getenv("GITHUB_APP_REPO", "ChadEngel/ce-aios")

if not APP_ID or not PRIVATE_KEY:
    print("Set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY env vars", file=sys.stderr)
    sys.exit(1)

def make_jwt():
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": int(APP_ID),
    }
    token = jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")
    # PyJWT >=2 returns str, <2 returns bytes
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token

def get_installations(jwt_token):
    r = requests.get("https://api.github.com/app/installations", headers={"Authorization": f"Bearer {jwt_token}"})
    r.raise_for_status()
    return r.json()

def get_installation_token(jwt_token, installation_id):
    r = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/tokens",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    r.raise_for_status()
    return r.json()["token"]

def list_issues(installation_token):
    owner, repo = REPO.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    r = requests.get(url, headers={"Authorization": f"Bearer {installation_token}", "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()

def main():
    jwt_token = make_jwt()
    print("JWT minted")
    installations = get_installations(jwt_token)
    print(f"Installations: {len(installations)}")
    for inst in installations:
        print(f"  id={inst['id']} account={inst['account']['login']} repos={inst.get('repositories_total')}")
    
    # Find installation for the target repo
    target_inst = None
    for inst in installations:
        # Minimal check: assume first installation is ours. For more precision, list repos.
        target_inst = inst
        break
    
    if not target_inst:
        print("No installation found", file=sys.stderr)
        sys.exit(1)

    installation_id = target_inst["id"]
    print(f"Using installation id {installation_id}")
    installation_token = get_installation_token(jwt_token, installation_id)
    print("Installation token acquired")

    issues = list_issues(installation_token)
    print(f"\nOpen issues for {REPO}: {len(issues)}")
    for i in issues[:10]:
        print(f"  #{i['number']} {i['title']} [{i['state']}]")

if __name__ == "__main__":
    main()
