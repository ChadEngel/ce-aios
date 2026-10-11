#!/usr/bin/env python3
"""
aios-manager — Phase 1 minimal viable service.

Polls the aios-manager GitHub App's installation every POLL_INTERVAL seconds,
lists open issues, and logs a structured summary to stdout (Loki picks it up).
This is intentionally minimal — proves the in-cluster wiring (Infisical secret
sync -> Secret mount -> env -> JWT mint -> install token -> GitHub API) without
yet implementing the LLM/decision loop that the real Phase 1 needs.

Required env (mounted from the aios-manager-secrets K8s Secret, populated by
the InfisicalOperator from jarvis-jatg/prod via the aios-manager-secrets-sync
InfisicalSecret resource):
  GITHUB_APP_ID          — App numeric id
  GITHUB_APP_PRIVATE_KEY — RSA private key (PEM, may contain \n)
  GITHUB_REPO            — owner/repo (e.g. ChadEngel/ce-aios)

Optional:
  POLL_INTERVAL          — seconds between polls (default 900 = 15 min, per issue #2)
  LOG_LEVEL              — DEBUG / INFO / WARNING / ERROR (default INFO)

Graceful shutdown: SIGTERM -> set a stop event -> exit after the current poll
completes (never mid-call; GitHub calls are idempotent so a partial cycle is
fine but not ideal).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone

import jwt
import requests

# --- Config -----------------------------------------------------------------

APP_ID = os.environ.get("GITHUB_APP_ID")
PRIVATE_KEY = os.environ.get("GITHUB_APP_PRIVATE_KEY")
REPO = os.environ.get("GITHUB_REPO", "ChadEngel/ce-aios")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "900"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Install id is hard-coded for Phase 1 (single repo, single install).
# If this App ever gets a second install, switch to listing /app/installations
# at startup and selecting by repository_selection == "all" or by matching
# repository.full_name in the install's repositories list.
INSTALL_ID = int(os.environ.get("GITHUB_APP_INSTALLATION_ID", "168700840"))

# --- Logging ----------------------------------------------------------------
# Structured JSON to stdout. Loki's promtail tails container stdout and
# parses JSON if it's valid; if not, it falls back to plain text. JSON gives
# us queryable fields (issue_count, latency_ms, etc.) in Grafana.

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("aios-manager")


def log_event(event: str, **fields) -> None:
    """Emit a single-line JSON record to stdout."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "service": "aios-manager",
        "event": event,
        **fields,
    }
    log.info(json.dumps(record))


# --- GitHub App auth --------------------------------------------------------

def mint_jwt() -> str:
    """Mint a GitHub App JWT (10 min TTL, RS256)."""
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        # PyJWT >=2 requires iss to be a string (RFC 7519 StringOrURI).
        "iss": str(int(APP_ID)),
    }
    token = jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")
    if isinstance(token, bytes):  # PyJWT <2
        token = token.decode("utf-8")
    return token


def get_installation_token(jwt_token: str) -> str:
    """Exchange the App JWT for a 1-hour installation access token.

    Endpoint is /access_tokens (NOT /tokens) — see aios/manager/test_github_app.py
    docstring + issue #43.
    """
    r = requests.post(
        f"https://api.github.com/app/installations/{INSTALL_ID}/access_tokens",
        headers={"Authorization": f"Bearer {jwt_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["token"]


# --- One poll cycle ---------------------------------------------------------

def list_open_issues(installation_token: str) -> list[dict]:
    """List open issues in the configured repo (read-only, R0)."""
    owner, repo = REPO.split("/", 1)
    r = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/issues",
        params={"state": "open", "per_page": 100},
        headers={
            "Authorization": f"token {installation_token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def poll_once() -> None:
    """One end-to-end poll cycle: JWT -> install token -> list issues -> log."""
    t0 = time.monotonic()
    jwt_token = mint_jwt()
    install_token = get_installation_token(jwt_token)
    issues = list_open_issues(install_token)
    latency_ms = int((time.monotonic() - t0) * 1000)

    # Log a summary, not every issue — keeps Loki volume sane at 15-min cadence.
    # Per-issue detail is queryable from GitHub itself.
    log_event(
        "poll_ok",
        repo=REPO,
        install_id=INSTALL_ID,
        issue_count=len(issues),
        latency_ms=latency_ms,
        sample_titles=[i["title"][:80] for i in issues[:5]],
    )


# --- Signal handling --------------------------------------------------------

_stop = threading.Event()


def _on_signal(signum, _frame):
    log_event("signal_received", signum=signum)
    _stop.set()


signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT, _on_signal)


# --- Main loop --------------------------------------------------------------

def main() -> int:
    # Pre-flight: env-var check (matches test_github_app.py behavior).
    if not APP_ID or not PRIVATE_KEY:
        log_event("startup_failed", reason="missing_env",
                  required=["GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY"])
        return 2

    log_event("startup", repo=REPO, install_id=INSTALL_ID,
              poll_interval_s=POLL_INTERVAL, log_level=LOG_LEVEL)

    while not _stop.is_set():
        try:
            poll_once()
        except Exception as e:
            # Don't crash the pod on a transient error (rate limit, network
            # blip, GitHub 5xx). Log and back off.
            log_event("poll_failed", error=type(e).__name__, message=str(e)[:200])

        # Sleep in small slices so SIGTERM is responsive (<5s) instead of
        # waiting up to POLL_INTERVAL for the next cycle to start.
        slept = 0
        while slept < POLL_INTERVAL and not _stop.is_set():
            _stop.wait(timeout=min(5, POLL_INTERVAL - slept))
            slept += 5

    log_event("shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
