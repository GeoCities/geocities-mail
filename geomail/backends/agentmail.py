"""AgentMail REST backend (https://docs.agentmail.to, API v0).

Folders map to AgentMail labels: INBOX -> "received", Sent -> "sent", Trash -> include_trash + "trash".
Read state uses the "unread" label.
"""
from __future__ import annotations
import base64, mimetypes
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote
import httpx
from .base import Backend, BackendError
from ..models import Attachment, Message, Outgoing

FOLDER_LABELS = {"INBOX": "received", "Sent": "sent", "Drafts": "draft", "Trash": "trash", "Spam": "spam"}


class AgentMailBackend(Backend):
    name = "agentmail"

    def __init__(self, api_key: str, inbox_id: Optional[str] = None, base_url: str = "https://api.agentmail.to",
                 client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(base_url=base_url, timeout=30,
                                             headers={"Authorization": f"Bearer {api_key}"})
        self.inbox_id = inbox_id or self._first_inbox()
        self.address = self.inbox_id

    def _req(self, method, path, **kw):
        r = self.client.request(method, path, **kw)
        if r.status_code >= 400:
            raise BackendError(f"AgentMail {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else r

    def _first_inbox(self) -> str:
        data = self._req("GET", "/v0/inboxes", params={"limit": 1})
        inboxes = data.get("inboxes") or []
        if not inboxes:
            raise BackendError("No AgentMail inboxes found; set inbox_id in config.")
        return inboxes[0]["inbox_id"]

    @property
    def _base(self):
        return f"/v0/inboxes/{quote(self.inbox_id, safe='')}"

    def _to_msg(self, d: dict, folder: str) -> Message:
        labels = d.get("labels") or []
        to = d.get("to") or []
        return Message(
            id=d.get("message_id") or d.get("draft_id"), folder=folder, sender=d.get("from", ""), to=to if isinstance(to, list) else [to],
            cc=d.get("cc") or [], subject=d.get("subject") or "(no subject)",
            date=d.get("timestamp") or d.get("updated_at") or d.get("created_at") or "", unread="unread" in labels,
            starred="starred" in labels,
            text=d.get("text") or "", html=d.get("html") or "", preview=d.get("preview") or "",
            attachments=[Attachment(id=a["attachment_id"], filename=a.get("filename") or a["attachment_id"],
                                    size=a.get("size") or 0, content_type=a.get("content_type") or "")
                         for a in d.get("attachments") or []])

    def folders(self) -> List[str]:
        return ["INBOX", "Drafts", "Sent", "Trash"]

    def set_starred(self, msg_id, starred):
        body = {"add_labels": ["starred"]} if starred else {"remove_labels": ["starred"]}
        self._req("PATCH", self._mid(msg_id), json=body)

    def list_starred(self, limit=100):
        data = self._req("GET", f"{self._base}/messages", params={"limit": limit, "labels": "starred"})
        return [self._to_msg(d, "INBOX") for d in data.get("messages", [])]

    # --- drafts: AgentMail drafts API -----------------------------------
    def _did(self, draft_id):
        return f"{self._base}/drafts/{quote(draft_id, safe='')}"

    def save_draft(self, out: Outgoing, draft_id=None) -> str:
        body = {"to": out.to, "cc": out.cc, "bcc": out.bcc, "subject": out.subject, "text": out.body}
        if draft_id:
            d = self._req("PATCH", self._did(draft_id), json=body)
        else:
            if out.in_reply_to:
                body["in_reply_to"] = out.in_reply_to
                body["reply_all"] = bool(out.reply_all)
            if out.attachments:
                body["attachments"] = self._atts(out.attachments)
            d = self._req("POST", f"{self._base}/drafts", json=body)
        return d.get("draft_id", draft_id or "") if isinstance(d, dict) else (draft_id or "")

    def get_draft(self, draft_id) -> Outgoing:
        d = self._req("GET", self._did(draft_id))
        return Outgoing(to=d.get("to") or [], cc=d.get("cc") or [], bcc=d.get("bcc") or [],
                        subject=d.get("subject") or "", body=d.get("text") or "", in_reply_to=d.get("in_reply_to"))

    def delete_draft(self, draft_id):
        self._req("DELETE", self._did(draft_id))

    def list_messages(self, folder="INBOX", limit=50, query=None) -> List[Message]:
        if folder == "Drafts" and not query:
            data = self._req("GET", f"{self._base}/drafts", params={"limit": limit})
            return [self._to_msg(d, "Drafts") for d in data.get("drafts", [])]
        if query:
            data = self._req("GET", f"{self._base}/messages/search", params={"q": query, "limit": limit})
        else:
            params = {"limit": limit}
            if folder == "Trash":
                params["include_trash"] = "true"
            label = FOLDER_LABELS.get(folder, folder)
            params["labels"] = label
            data = self._req("GET", f"{self._base}/messages", params=params)
        return [self._to_msg(d, folder) for d in data.get("messages", [])]

    def _mid(self, msg_id):
        return f"{self._base}/messages/{quote(msg_id, safe='')}"

    def get_message(self, msg_id):
        d = self._req("GET", self._mid(msg_id))
        labels = d.get("labels") or []
        folder = "Sent" if "sent" in labels else "Trash" if "trash" in labels else "INBOX"
        return self._to_msg(d, folder)

    def set_read(self, msg_id, read):
        body = {"remove_labels": ["unread"], "add_labels": ["read"]} if read else \
               {"add_labels": ["unread"], "remove_labels": ["read"]}
        self._req("PATCH", self._mid(msg_id), json=body)

    def delete(self, msg_id):
        self._req("DELETE", self._mid(msg_id))

    def _atts(self, paths):
        out = []
        for p in paths:
            data = Path(p).read_bytes()
            out.append({"filename": Path(p).name, "content": base64.b64encode(data).decode(),
                        "content_type": mimetypes.guess_type(p)[0] or "application/octet-stream"})
        return out

    def send(self, out: Outgoing) -> str:
        body = {"to": out.to, "text": out.body}
        if out.cc:
            body["cc"] = out.cc
        if out.bcc:
            body["bcc"] = out.bcc
        if out.attachments:
            body["attachments"] = self._atts(out.attachments)
        if out.in_reply_to:
            body["reply_all"] = bool(out.reply_all)
            if out.reply_all:
                body.pop("to")
            d = self._req("POST", f"{self._mid(out.in_reply_to)}/reply", json=body)
        else:
            body["subject"] = out.subject
            d = self._req("POST", f"{self._base}/messages/send", json=body)
        return d.get("message_id", "") if isinstance(d, dict) else ""

    def get_attachment(self, msg_id, att_id) -> bytes:
        d = self._req("GET", f"{self._mid(msg_id)}/attachments/{quote(att_id, safe='')}")
        if isinstance(d, dict) and d.get("download_url"):
            r = httpx.get(d["download_url"], timeout=60, follow_redirects=True)
            r.raise_for_status()
            return r.content
        if isinstance(d, httpx.Response):
            return d.content
        raise BackendError("Attachment had no download_url")

    def close(self):
        self.client.close()
