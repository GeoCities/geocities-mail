"""Grok Bot native inbox via a Maildir *mirror* (see README "Mirror spec").

Grok Bot inboxes (mail.grokbot.com) have no public API, so a mail bot (the Grok Bot that reads your inbox) copies
messages into a shared folder and GeoCities Mail reads it. Sending never happens here: outgoing mail is queued in
outbox/pending/ and the mail bot sends it only after the user approves.

Layout under <root>:
  INBOX/{tmp,new,cur}  Sent/{tmp,new,cur}  Trash/{tmp,new,cur}   standard Maildir
  outbox/{pending,sent,failed}/<id>.eml + <id>.json (+ <id>.reason.txt in failed/)
  manifest.json        {"version": 1, "account": ..., "last_synced": ISO-8601, "synced_by": ...}
"""
from __future__ import annotations
import base64, hashlib, json, mimetypes, os, shutil, socket, time, uuid
from datetime import datetime, timezone
from email import message_from_bytes
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from pathlib import Path
from typing import List, Optional
from .base import Backend, BackendError
from .imap import attachment_bytes, parse_email
from ..models import Attachment, Message, Outgoing

MAIL_FOLDERS = ["INBOX", "Sent", "Trash"]
OUTBOX_STATES = {"pending": "Waiting for approval", "sent": "Sent", "failed": "Failed"}


def ensure_layout(root) -> Path:
    root = Path(root)
    for f in MAIL_FOLDERS:
        for sub in ("tmp", "new", "cur"):
            (root / f / sub).mkdir(parents=True, exist_ok=True)
    for s in OUTBOX_STATES:
        (root / "outbox" / s).mkdir(parents=True, exist_ok=True)
    return root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, data: bytes, tmpdir: Optional[Path] = None):
    tmp = (tmpdir or path.parent) / f".{path.name}.{uuid.uuid4().hex}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _split_name(name: str):
    key, _, flags = name.partition(":2,")
    return key, flags


def build_eml(frm: str, to, subject: str, body: str, cc=(), date: str = "", message_id: str = "",
              in_reply_to: str = "", html: str = "", attachments=()) -> bytes:
    """attachments: list of {"filename", "path"} or {"filename", "content_base64", "content_type"}."""
    m = EmailMessage()
    m["From"] = frm or "unknown@unknown"
    m["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else (to or "")
    if cc:
        m["Cc"] = ", ".join(cc) if isinstance(cc, (list, tuple)) else cc
    m["Subject"] = subject or ""
    if date:
        try:
            m["Date"] = formatdate(datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp())
        except ValueError:
            m["Date"] = date
    else:
        m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = message_id or make_msgid()
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(body or "")
    if html:
        m.add_alternative(html, subtype="html")
    for a in attachments or []:
        if isinstance(a, str):
            a = {"path": a}
        if a.get("path"):
            data = Path(a["path"]).read_bytes()
        else:
            data = base64.b64decode(a.get("content_base64", ""))
        fn = a.get("filename") or Path(a.get("path", "attachment")).name
        ctype = a.get("content_type") or mimetypes.guess_type(fn)[0] or "application/octet-stream"
        mt, st = ctype.split("/", 1)
        m.add_attachment(data, maintype=mt, subtype=st, filename=fn)
    return m.as_bytes()


class MaildirMirrorBackend(Backend):
    name = "grokbot"

    def __init__(self, path, address: str = "", sender_bot: str = "your mail bot"):
        self.root = ensure_layout(path)
        self.sender_bot = sender_bot
        self.address = address or self.manifest().get("account") or "grokbot mirror"

    # --- manifest -------------------------------------------------------
    def manifest(self) -> dict:
        p = self.root / "manifest.json"
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return {}

    def touch_manifest(self, synced_by: str = "", account: str = "") -> dict:
        m = self.manifest()
        m.update({"version": 1, "last_synced": _now()})
        if synced_by:
            m["synced_by"] = synced_by
        if account:
            m["account"] = account
        _atomic_write(self.root / "manifest.json", json.dumps(m, indent=1).encode())
        return m

    def status_line(self) -> str:
        ls = self.manifest().get("last_synced")
        if not ls:
            return "Last synced: never"
        try:
            dt = datetime.fromisoformat(ls.replace("Z", "+00:00")).astimezone()
            return "Last synced " + dt.strftime("%b %d, %I:%M %p %Z").replace(" 0", " ")
        except ValueError:
            return f"Last synced {ls}"

    @property
    def send_notice(self) -> str:
        return (f"Queued in Outbox. It will be sent by {self.sender_bot} after you approve it "
                f"— status: Waiting for approval.")

    # --- mail folders ---------------------------------------------------
    def folders(self) -> List[str]:
        return ["INBOX", "Sent", "Outbox", "Trash"]

    def _files(self, folder: str):
        for sub in ("new", "cur"):
            d = self.root / folder / sub
            if d.is_dir():
                for f in d.iterdir():
                    if f.is_file() and not f.name.startswith("."):
                        yield sub, f

    def _locate(self, msg_id: str):
        folder, _, key = msg_id.partition("/")
        if folder not in MAIL_FOLDERS or not key:
            raise BackendError(f"No such message: {msg_id}")
        for sub, f in self._files(folder):
            if _split_name(f.name)[0] == key:
                return folder, sub, f
        raise BackendError(f"No such message: {msg_id}")

    def _parse(self, folder, sub, f: Path) -> Message:
        key, flags = _split_name(f.name)
        unread = sub == "new" or "S" not in flags
        m = parse_email(f.read_bytes(), f"{folder}/{key}", folder, unread)
        if not m.date:
            m.date = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        return m

    def list_messages(self, folder="INBOX", limit=50, query=None) -> List[Message]:
        if folder == "Outbox" and not query:
            return self.outbox_messages()[:limit]
        folders = ["INBOX", "Sent"] if query else [folder]
        out = []
        for fo in folders:
            for sub, f in self._files(fo):
                m = self._parse(fo, sub, f)
                if query:
                    from ..htmltext import body_text
                    hay = " ".join([m.sender, m.subject, body_text(m), " ".join(m.to)]).lower()
                    if query.lower() not in hay:
                        continue
                out.append(m)
        out.sort(key=lambda m: m.date, reverse=True)
        return out[:limit]

    def get_message(self, msg_id):
        if msg_id.startswith("outbox/"):
            return self._outbox_message(msg_id.split("/", 1)[1])
        return self._parse(*self._locate(msg_id))

    def set_read(self, msg_id, read):
        if msg_id.startswith("outbox/"):
            return
        folder, sub, f = self._locate(msg_id)
        key, flags = _split_name(f.name)
        flags = set(flags)
        (flags.add if read else flags.discard)("S")
        dest = self.root / folder / "cur" / f"{key}:2,{''.join(sorted(flags))}"
        os.replace(f, dest)

    def delete(self, msg_id):
        if msg_id.startswith("outbox/"):
            oid = msg_id.split("/", 1)[1]
            p = self.root / "outbox" / "pending"
            if not (p / f"{oid}.json").exists():
                raise BackendError("Only pending outbox items can be cancelled")
            for ext in (".json", ".eml"):
                (p / f"{oid}{ext}").unlink(missing_ok=True)
            return
        folder, sub, f = self._locate(msg_id)
        if folder == "Trash":
            f.unlink()
        else:
            os.replace(f, self.root / "Trash" / "cur" / (f.name if ":2," in f.name else f.name + ":2,S"))

    def get_attachment(self, msg_id, att_id) -> bytes:
        if msg_id.startswith("outbox/"):
            oid = msg_id.split("/", 1)[1]
            _, base = self._find_outbox(oid)
            return attachment_bytes((base.with_suffix(".eml")).read_bytes(), att_id)
        return attachment_bytes(self._locate(msg_id)[2].read_bytes(), att_id)

    # --- import (used by the mail bot) ----------------------------------
    def import_eml(self, raw: bytes, folder: str = "INBOX", unread: Optional[bool] = None) -> str:
        if folder not in ("INBOX", "Sent"):
            raise BackendError("folder must be INBOX or Sent")
        mid = message_from_bytes(raw).get("Message-ID", "") or hashlib.sha1(raw).hexdigest()
        key = "gm-" + hashlib.sha1(mid.strip().encode()).hexdigest()[:20]
        for fo in MAIL_FOLDERS:  # idempotent: already mirrored (or trashed locally)?
            for _, f in self._files(fo):
                if _split_name(f.name)[0] == key:
                    return f"{fo}/{key}"
        if unread is None:
            unread = folder == "INBOX"
        name = key if unread else f"{key}:2,S"
        sub = "new" if unread else "cur"
        _atomic_write(self.root / folder / sub / name, raw, self.root / folder / "tmp")
        return f"{folder}/{key}"

    def import_json(self, d: dict) -> str:
        raw = build_eml(d.get("from", ""), d.get("to") or [], d.get("subject", ""), d.get("text", ""),
                        cc=d.get("cc") or [], date=d.get("date", ""), message_id=d.get("message_id", ""),
                        in_reply_to=d.get("in_reply_to", ""), html=d.get("html", ""),
                        attachments=d.get("attachments") or [])
        return self.import_eml(raw, d.get("folder", "INBOX"), d.get("unread"))

    # --- outbox ---------------------------------------------------------
    def send(self, out: Outgoing) -> str:
        if not out.to and not (out.in_reply_to and out.reply_all):
            raise BackendError("At least one recipient is required")
        orig_hdr = ""
        if out.in_reply_to and not out.in_reply_to.startswith("outbox/"):
            try:
                orig_hdr = self.get_message(out.in_reply_to).message_id_header
            except BackendError:
                pass
        for p in out.attachments:
            if not Path(p).is_file():
                raise BackendError(f"Attachment not found: {p}")
        oid = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        msgid = make_msgid(domain=socket.gethostname() or "geomail")
        frm = self.address if "@" in self.address else ""
        raw = build_eml(frm, out.to, out.subject, out.body, cc=out.cc, message_id=msgid,
                        in_reply_to=orig_hdr, attachments=[str(Path(p).resolve()) for p in out.attachments])
        side = {
            "id": oid, "status": "pending", "created_at": _now(),
            "from": frm, "to": out.to, "cc": out.cc, "bcc": out.bcc, "subject": out.subject, "body": out.body,
            "in_reply_to": out.in_reply_to or None, "in_reply_to_message_id": orig_hdr or None,
            "reply_all": bool(out.reply_all), "message_id": msgid,
            "attachments": [str(Path(p).resolve()) for p in out.attachments],
            "eml": f"{oid}.eml",
        }
        pend = self.root / "outbox" / "pending"
        _atomic_write(pend / f"{oid}.eml", raw)
        _atomic_write(pend / f"{oid}.json", json.dumps(side, indent=1).encode())  # sidecar last = ready
        return f"outbox/{oid}"

    def _find_outbox(self, oid: str):
        for state in OUTBOX_STATES:
            base = self.root / "outbox" / state / oid
            if base.with_suffix(".json").exists():
                return state, base
        raise BackendError(f"No such outbox item: {oid}")

    def _outbox_message(self, oid: str) -> Message:
        state, base = self._find_outbox(oid)
        side = json.loads(base.with_suffix(".json").read_text())
        status = OUTBOX_STATES[state]
        reason = base.parent / f"{oid}.reason.txt"
        if state == "failed" and reason.exists():
            status += ": " + reason.read_text().strip()
        atts = []
        eml = base.with_suffix(".eml")
        if eml.exists():
            atts = parse_email(eml.read_bytes(), "", "", False).attachments
        return Message(id=f"outbox/{oid}", folder="Outbox", sender=side.get("from") or self.address,
                       to=side.get("to") or [], cc=side.get("cc") or [], subject=side.get("subject", ""),
                       date=side.get("created_at", ""), text=side.get("body", ""), status=status,
                       message_id_header=side.get("message_id", ""), attachments=atts)

    def outbox_messages(self) -> List[Message]:
        out = []
        for state in OUTBOX_STATES:
            for j in (self.root / "outbox" / state).glob("*.json"):
                try:
                    out.append(self._outbox_message(j.stem))
                except (BackendError, ValueError):
                    pass
        out.sort(key=lambda m: m.date, reverse=True)
        return out

    def outbox_items(self, state: Optional[str] = None) -> List[dict]:
        items = []
        for s in ([state] if state else list(OUTBOX_STATES)):
            for j in sorted((self.root / "outbox" / s).glob("*.json")):
                d = json.loads(j.read_text())
                d["status"] = s
                d["eml_path"] = str(j.with_suffix(".eml"))
                d["json_path"] = str(j)
                r = j.parent / f"{j.stem}.reason.txt"
                if r.exists():
                    d["reason"] = r.read_text().strip()
                items.append(d)
        return items

    def mark_outbox(self, oid: str, state: str, reason: str = "") -> None:
        if state not in ("sent", "failed"):
            raise BackendError("state must be sent or failed")
        cur, base = self._find_outbox(oid)
        dest = self.root / "outbox" / state
        d = json.loads(base.with_suffix(".json").read_text())
        d["status"] = state
        d[f"{state}_at"] = _now()
        for ext in (".eml",):
            if base.with_suffix(ext).exists():
                os.replace(base.with_suffix(ext), dest / f"{oid}{ext}")
        if state == "failed":
            (dest / f"{oid}.reason.txt").write_text(reason or "unknown error")
        _atomic_write(dest / f"{oid}.json", json.dumps(d, indent=1).encode())
        base.with_suffix(".json").unlink(missing_ok=True)
