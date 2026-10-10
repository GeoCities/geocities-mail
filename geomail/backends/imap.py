"""Generic IMAP (read) + SMTP (send) backend. Message ids are "<folder>/<uid>"."""
from __future__ import annotations
import email, imaplib, mimetypes, re, smtplib, ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parsedate_to_datetime, formatdate
from pathlib import Path
from typing import List, Optional
from .base import Backend, BackendError
from ..models import Attachment, Message, Outgoing

_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<name>.+)')


def _dec(v) -> str:
    if v is None:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return str(v)


def _date(v) -> str:
    try:
        return parsedate_to_datetime(v).isoformat()
    except Exception:
        return v or ""


def _addrs(v) -> List[str]:
    return [f"{n} <{a}>" if n else a for n, a in getaddresses([_dec(v)]) if a]


def parse_email(raw: bytes, msg_id: str, folder: str, unread: bool) -> Message:
    m = email.message_from_bytes(raw)
    text, html, atts = "", "", []
    idx = 0
    for part in m.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        fn = part.get_filename()
        ctype = part.get_content_type()
        if fn or disp == "attachment":
            payload = part.get_payload(decode=True) or b""
            atts.append(Attachment(id=str(idx), filename=_dec(fn) or f"part{idx}", size=len(payload), content_type=ctype))
            idx += 1
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        s = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and not text:
            text = s
        elif ctype == "text/html" and not html:
            html = s
    return Message(id=msg_id, folder=folder, sender=_dec(m.get("From")), to=_addrs(m.get("To")),
                   cc=_addrs(m.get("Cc")), subject=_dec(m.get("Subject")), date=_date(m.get("Date")),
                   unread=unread, text=text, html=html, preview=(text or "")[:120],
                   message_id_header=m.get("Message-ID", ""), attachments=atts)


def attachment_bytes(raw: bytes, att_id: str) -> bytes:
    m = email.message_from_bytes(raw)
    idx = 0
    for part in m.walk():
        if part.is_multipart():
            continue
        if part.get_filename() or (part.get_content_disposition() or "") == "attachment":
            if str(idx) == att_id:
                return part.get_payload(decode=True) or b""
            idx += 1
    raise BackendError("No such attachment")


class ImapBackend(Backend):
    name = "imap"

    def __init__(self, imap_host: str, username: str, password: str, imap_port: int = 993,
                 smtp_host: Optional[str] = None, smtp_port: int = 465, trash_folder: Optional[str] = None,
                 sent_folder: Optional[str] = None, drafts_folder: Optional[str] = None, **kw):
        self.drafts_folder = drafts_folder
        self.username, self.password = username, password
        self.address = kw.get("from") or username
        self.imap_host, self.imap_port = imap_host, int(imap_port)
        self.smtp_host, self.smtp_port = smtp_host or imap_host.replace("imap", "smtp", 1), int(smtp_port)
        self.trash_folder, self.sent_folder = trash_folder, sent_folder
        self._conn: Optional[imaplib.IMAP4] = None
        self._selected = None

    # --- plumbing -------------------------------------------------------
    def conn(self) -> imaplib.IMAP4:
        if self._conn is None:
            c = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=ssl.create_default_context())
            c.login(self.username, self.password)
            self._conn = c
        return self._conn

    def _select(self, folder: str, readonly=False):
        typ, _ = self.conn().select(self._q(folder), readonly=readonly)
        if typ != "OK":
            raise BackendError(f"Cannot open folder {folder}")
        self._selected = folder

    @staticmethod
    def _q(folder: str) -> str:
        return folder if folder.startswith('"') else '"' + folder.replace('"', '\\"') + '"'

    @staticmethod
    def _split(msg_id: str):
        folder, _, uid = msg_id.rpartition("/")
        if not folder:
            raise BackendError(f"Bad message id {msg_id}")
        return folder, uid

    def _special(self, flag: str) -> Optional[str]:
        for name, flags in self._list():
            if flag.lower() in flags.lower():
                return name
        return None

    def _list(self):
        typ, data = self.conn().list()
        out = []
        for line in data or []:
            s = line.decode() if isinstance(line, bytes) else str(line)
            mm = _LIST_RE.match(s)
            if mm:
                out.append((mm.group("name").strip().strip('"'), mm.group("flags")))
        return out

    # --- API ------------------------------------------------------------
    def folders(self) -> List[str]:
        names = [n for n, f in self._list() if "\\Noselect" not in f]
        names.sort(key=lambda n: (n.upper() != "INBOX", n.lower()))
        return names or ["INBOX"]

    def _drafts_box(self) -> str:
        return self.drafts_folder or self._special("\\Drafts") or "Drafts"

    def hidden_folders(self):
        box = self._drafts_box()
        return [box] if box != "Drafts" else []

    def list_messages(self, folder="INBOX", limit=50, query=None) -> List[Message]:
        if folder == "Drafts" and self._drafts_box() != "Drafts":
            msgs = self.list_messages(self._drafts_box(), limit, query)
            for m in msgs:
                m.folder = "Drafts"
            return msgs
        self._select(folder, readonly=True)
        c = self.conn()
        if query:
            q = query.replace('"', "")
            typ, data = c.uid("SEARCH", "CHARSET", "UTF-8", "OR", "OR", "SUBJECT", f'"{q}"', "FROM", f'"{q}"', "BODY", f'"{q}"')
        else:
            typ, data = c.uid("SEARCH", None, "ALL")
        uids = (data[0] or b"").split()[-limit:]
        if not uids:
            return []
        typ, data = c.uid("FETCH", b",".join(uids).decode(),
                          "(FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)])")
        out = []
        for item in data:
            if not isinstance(item, tuple):
                continue
            meta = item[0].decode(errors="replace")
            um = re.search(r"UID (\d+)", meta)
            if not um:
                continue
            unread = "\\Seen" not in meta
            m = parse_email(item[1], f"{folder}/{um.group(1)}", folder, unread)
            m.starred = "\\Flagged" in meta
            if folder == "Drafts":
                m.folder = "Drafts"
            out.append(m)
        out.sort(key=lambda m: m.date, reverse=True)
        return out

    def _raw(self, msg_id: str) -> bytes:
        folder, uid = self._split(msg_id)
        self._select(folder, readonly=True)
        typ, data = self.conn().uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
        for item in data or []:
            if isinstance(item, tuple):
                return item[1]
        raise BackendError(f"No such message: {msg_id}")

    def get_message(self, msg_id):
        folder, uid = self._split(msg_id)
        self._select(folder, readonly=True)
        typ, data = self.conn().uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
        for item in data or []:
            if isinstance(item, tuple):
                meta = item[0].decode(errors="replace")
                m = parse_email(item[1], msg_id, folder, "\\Seen" not in meta)
                m.starred = "\\Flagged" in meta
                if folder == self._drafts_box():
                    m.folder = "Drafts"
                return m
        raise BackendError(f"No such message: {msg_id}")

    def set_read(self, msg_id, read):
        folder, uid = self._split(msg_id)
        self._select(folder)
        self.conn().uid("STORE", uid, "+FLAGS" if read else "-FLAGS", "(\\Seen)")

    def set_starred(self, msg_id, starred):
        folder, uid = self._split(msg_id)
        self._select(folder)
        self.conn().uid("STORE", uid, "+FLAGS" if starred else "-FLAGS", "(\\Flagged)")

    def save_draft(self, out: Outgoing, draft_id: Optional[str] = None) -> str:
        from ..drafts import draft_eml
        box = self._drafts_box()
        typ, _ = self.conn().append(self._q(box), "(\\Draft \\Seen)", None, draft_eml(out, self.address))
        if typ != "OK":
            raise BackendError(f"Could not save draft to {box}")
        if draft_id:
            self.delete_draft(draft_id)
        return box

    def get_draft(self, draft_id) -> Outgoing:
        from ..drafts import outgoing_from_eml
        return outgoing_from_eml(self._raw(draft_id))

    def delete_draft(self, draft_id):
        folder, uid = self._split(draft_id)
        self._select(folder)
        self.conn().uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        self.conn().expunge()

    def delete(self, msg_id):
        folder, uid = self._split(msg_id)
        trash = self.trash_folder or self._special("\\Trash")
        self._select(folder)
        c = self.conn()
        if trash and folder != trash:
            typ, _ = c.uid("COPY", uid, self._q(trash))
            if typ != "OK":
                raise BackendError("Could not move to Trash")
        c.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        c.expunge()

    def build_mime(self, out: Outgoing) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = ", ".join(out.to)
        if out.cc:
            msg["Cc"] = ", ".join(out.cc)
        msg["Subject"] = out.subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()
        if out.in_reply_to:
            try:
                orig = self.get_message(out.in_reply_to).message_id_header
                if orig:
                    msg["In-Reply-To"] = orig
                    msg["References"] = orig
            except BackendError:
                pass
        msg.set_content(out.body)
        for p in out.attachments:
            data = Path(p).read_bytes()
            ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=Path(p).name)
        return msg

    def send(self, out: Outgoing) -> str:
        if not out.to:
            raise BackendError("At least one recipient is required")
        msg = self.build_mime(out)
        rcpts = [a for _, a in getaddresses(out.to + out.cc + out.bcc)]
        if self.smtp_port == 465:
            s = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(self.smtp_host, self.smtp_port)
            s.starttls(context=ssl.create_default_context())
        try:
            s.login(self.username, self.password)
            s.send_message(msg, to_addrs=rcpts)
        finally:
            s.quit()
        sent = self.sent_folder or self._special("\\Sent")
        if sent:  # many servers (not Gmail) don't save sent mail automatically
            try:
                self.conn().append(self._q(sent), "(\\Seen)", None, msg.as_bytes())
            except Exception:
                pass
        return msg["Message-ID"]

    def get_attachment(self, msg_id, att_id) -> bytes:
        return attachment_bytes(self._raw(msg_id), att_id)

    def close(self):
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None
