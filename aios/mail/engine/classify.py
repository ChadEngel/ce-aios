"""Deterministic classifier — extracted verbatim from digest.py (0.4.x).

The ONE source of classification truth for both legs (filer + digest). The
model never classifies; it only writes prose (digest) and suggests keeps
(privacy layer 2, app layer). Rules come from a Config object instead of
module globals; with the env-derived Config this behaviour is identical to
0.4.x by construction (same env names, same defaults).
"""
import hashlib
import re

from engine.config import Config


def msg_key(message_id):
    """Graph ids share a long per-mailbox prefix — truncation would collapse
    every message to one key. Hash instead."""
    return (hashlib.sha1((message_id or "").encode()).hexdigest()[:16]
            if message_id else "")


class Classifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.receipt_pats = [re.compile(s, re.I) for s in cfg.receipt_patterns]
        self.order_pats = [re.compile(s, re.I) for s in cfg.order_patterns]
        self.privacy_pats = [re.compile(s, re.I) for s in cfg.privacy_subjects]

    def _is_notice(self, addr, subject):
        """True for the digest's own delivery mail (e.g. GitHub notification
        email). Sender is matched EXACTLY (or by domain when the config value
        is a bare domain) — a substring test would let
        "xnotifications@github.com" through. Subject match is a plain
        case-insensitive substring."""
        sender_ok = False
        for s in self.cfg.notice_senders:
            sender_ok = (addr == s if "@" in s else addr.endswith("@" + s))
            if sender_ok:
                break
        return sender_ok and any(n in subject.lower()
                                 for n in self.cfg.notice_subjects)

    def classify(self, messages, sent_contacts=()):
        """Deterministic bucketing; the model only writes prose.
        type:  "priority" (+reason) or "other" — feeds the prose prompt.
        _file: "receipts"/"orders" for typed matches, "filed" for junk, None
               for priorities and _self_notice items."""
        cfg = self.cfg
        out = []
        for m in messages:
            frm = (m.get("from") or {}).get("emailAddress", {})
            addr = (frm.get("address") or "").lower()
            name = (frm.get("name") or "").lower()
            subject = m.get("subject") or ""
            # Design rule: keyword matching uses subject + sender only.
            # bodyPreview would self-reference the digest's own posted output
            # (GitHub notification emails quote it) and re-promote them to
            # Priority forever — a feedback loop. Never match on body text.
            blob = " ".join([name, addr, subject.lower()])
            reason = None
            if sent_contacts and addr in sent_contacts and not any(
                    addr.endswith("@" + d) or addr.endswith("." + d)
                    for d in cfg.contact_excludes):
                reason = "you email this person"
            else:
                hit = next((k for k in cfg.priority_keywords if k in blob),
                           None)
                dom = next((d for d in cfg.protected_domains
                            if addr.endswith("@" + d) or addr.endswith("." + d)),
                           None)
                if hit:
                    reason = f"matches '{hit}'"
                elif dom:
                    reason = f"protected sender ({dom})"
            _privacy = None
            _self_notice = None
            if self._is_notice(addr, subject):
                _self_notice = "digest notification (kept in inbox, never filed)"
                _file = None
            elif reason:
                _file = None
            else:
                # Privacy layer 1 (deterministic). Runs BEFORE the typed
                # folders: a broker confirmation must never land in
                # Receipts/Orders either.
                if cfg.privacy_on:
                    pdom = next((d for d in cfg.privacy_domains
                                 if addr.endswith("@" + d)
                                 or addr.endswith("." + d)), None)
                    psub = next((p.pattern for p in self.privacy_pats
                                 if p.search(subject) or p.search(name)), None)
                    if pdom:
                        _privacy = f"broker sender domain ({pdom})"
                    elif psub:
                        _privacy = f"privacy subject match ({psub[:40]})"
                _file = ("receipts" if not _privacy and any(
                    p.search(subject) or p.search(name)
                    for p in self.receipt_pats) else None)
                if not _privacy and not _file:
                    _file = ("orders" if any(
                        p.search(subject) or p.search(name)
                        for p in self.order_pats) else None)
                if not reason and not _privacy and not _file:
                    _file = "filed"
            out.append({"type": "priority" if reason else "other",
                        "reason": reason, "_file": _file,
                        "_privacy": _privacy, "_self_notice": _self_notice,
                        "_id": msg_key(m.get("id")), "_fid": m.get("id"),
                        "_received": m.get("receivedDateTime"),
                        "_unread": m.get("isRead") is False,
                        "from": m.get("from"),
                        "subject": subject or "(no subject)",
                        "preview": (m.get("bodyPreview")
                                    or "")[:cfg.preview_chars]})
        return out