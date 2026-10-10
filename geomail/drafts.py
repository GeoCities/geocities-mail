"""Drafts as .eml with a few X-GeoCities-Mail-* headers so they round-trip back into the compose form."""
from __future__ import annotations
import json
from email import message_from_bytes
from email.utils import getaddresses
from .models import Outgoing

H_REPLY = "X-GeoCities-Mail-Reply-To-Id"
H_REPLY_ALL = "X-GeoCities-Mail-Reply-All"
H_ATTACH = "X-GeoCities-Mail-Attachments"


def draft_eml(out: Outgoing, frm: str = "") -> bytes:
    from .backends.maildir import build_eml
    from email import policy
    raw = build_eml(frm, out.to, out.subject, out.body, cc=out.cc)
    m = message_from_bytes(raw, policy=policy.default)
    if out.bcc:
        m["Bcc"] = ", ".join(out.bcc)
    if out.in_reply_to:
        m[H_REPLY] = out.in_reply_to
        m[H_REPLY_ALL] = "1" if out.reply_all else "0"
    if out.attachments:
        m[H_ATTACH] = json.dumps(out.attachments)
    return m.as_bytes()


def outgoing_from_eml(raw: bytes) -> Outgoing:
    from .backends.imap import parse_email
    m = message_from_bytes(raw)
    msg = parse_email(raw, "", "", False)
    addrs = lambda h: [f"{n} <{a}>" if n else a for n, a in getaddresses([m.get(h, "")]) if a]
    try:
        atts = json.loads(m.get(H_ATTACH, "[]"))
    except ValueError:
        atts = []
    return Outgoing(to=addrs("To"), cc=addrs("Cc"), bcc=addrs("Bcc"), subject=msg.subject, body=msg.text,
                    in_reply_to=m.get(H_REPLY) or None, reply_all=m.get(H_REPLY_ALL) == "1", attachments=atts)
