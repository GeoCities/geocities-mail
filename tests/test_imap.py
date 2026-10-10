"""IMAP/SMTP backend against a fake imaplib connection and a fake SMTP server (no network)."""
from email.message import EmailMessage
import geomail.backends.imap as imapmod
from geomail.backends.imap import ImapBackend
from geomail.models import Outgoing


def _raw(subject, body="hello", html=None, attach=False, mid="<m1@x>"):
    m = EmailMessage()
    m["From"] = "Alice <alice@example.com>"; m["To"] = "me@example.com"; m["Cc"] = "bob@example.com"
    m["Subject"] = subject; m["Date"] = "Fri, 09 Oct 2026 10:00:00 +0000"; m["Message-ID"] = mid
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    if attach:
        m.add_attachment(b"PDFDATA", maintype="application", subtype="pdf", filename="doc.pdf")
    return m.as_bytes()


class FakeIMAP:
    def __init__(self):
        self.boxes = {"INBOX": {1: [_raw("First", attach=True), set()], 2: [_raw("Second", mid="<m2@x>"), {"\\Seen"}]},
                      "Trash": {}}
        self.sel = None
        self.calls = []
        self.appended = []

    def login(self, u, p): return "OK", [b""]
    def logout(self): pass
    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Trash) "/" "Trash"',
                      b'(\\Noselect) "/" "[Gmail]"', b'(\\Sent) "/" "Sent"']
    def select(self, box, readonly=False):
        self.sel = box.strip('"'); return "OK", [b"1"]
    def expunge(self):
        b = self.boxes[self.sel]
        for k in [k for k, v in b.items() if "\\Deleted" in v[1]]:
            del b[k]
        return "OK", []
    def append(self, box, flags, dt, data):
        self.appended.append((box, data)); return "OK", []
    def uid(self, cmd, *args):
        self.calls.append((cmd, args))
        box = self.boxes[self.sel]
        if cmd == "SEARCH":
            if "SUBJECT" in args:
                q = args[args.index("SUBJECT") + 1].strip('"')
                ids = [k for k, v in box.items() if q.encode() in v[0]]
            else:
                ids = list(box)
            return "OK", [" ".join(map(str, ids)).encode()]
        if cmd == "FETCH":
            out = []
            for u in str(args[0]).split(","):
                raw, flags = box[int(u)]
                out.append((f"{u} (UID {u} FLAGS ({' '.join(flags)}) BODY[] {{{len(raw)}}}".encode(), raw))
                out.append(b")")
            return "OK", out
        if cmd == "STORE":
            u, op, fl = args
            flag = fl.strip("()")
            (box[int(u)][1].add if op.startswith("+") else box[int(u)][1].discard)(flag)
            return "OK", []
        if cmd == "COPY":
            u, dest = args
            self.boxes[dest.strip('"')][99] = [box[int(u)][0], set()]
            return "OK", []
        raise AssertionError(cmd)


class FakeSMTP:
    sent = []
    def __init__(self, host, port, context=None): self.host = host
    def login(self, u, p): pass
    def send_message(self, msg, to_addrs=None): FakeSMTP.sent.append((msg, to_addrs))
    def quit(self): pass
    def starttls(self, context=None): pass


def make(monkeypatch):
    fake = FakeIMAP()
    monkeypatch.setattr(imapmod.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setattr(imapmod.smtplib, "SMTP_SSL", FakeSMTP)
    FakeSMTP.sent = []
    return ImapBackend(imap_host="imap.example.com", username="me@example.com", password="pw"), fake


def test_folders_and_list(monkeypatch):
    b, fake = make(monkeypatch)
    assert b.folders() == ["INBOX", "Sent", "Trash"]
    msgs = b.list_messages("INBOX")
    assert {m.subject for m in msgs} == {"First", "Second"}
    first = next(m for m in msgs if m.subject == "First")
    assert first.unread and first.id == "INBOX/1" and first.sender.startswith("Alice")
    assert next(m for m in msgs if m.subject == "Second").unread is False


def test_read_attachment_flags_delete(monkeypatch):
    b, fake = make(monkeypatch)
    m = b.get_message("INBOX/1")
    assert m.text.strip() == "hello" and m.cc == ["bob@example.com"]
    assert [a.filename for a in m.attachments] == ["doc.pdf"]
    assert b.get_attachment("INBOX/1", "0") == b"PDFDATA"
    b.set_read("INBOX/1", True)
    assert "\\Seen" in fake.boxes["INBOX"][1][1]
    b.delete("INBOX/1")
    assert 1 not in fake.boxes["INBOX"] and fake.boxes["Trash"]


def test_search(monkeypatch):
    b, _ = make(monkeypatch)
    assert [m.subject for m in b.list_messages("INBOX", query="Second")] == ["Second"]


def test_html_parse():
    m = imapmod.parse_email(_raw("H", body="plain", html="<p>hi <b>there</b></p>"), "INBOX/9", "INBOX", False)
    assert m.text.strip() == "plain" and "<b>there</b>" in m.html


def test_send_reply_threads(monkeypatch, tmp_path):
    b, fake = make(monkeypatch)
    f = tmp_path / "a.txt"; f.write_text("x")
    b.send(Outgoing(to=["alice@example.com"], cc=["c@example.com"], bcc=["d@example.com"], subject="Re: First",
                    body="thanks", in_reply_to="INBOX/1", attachments=[str(f)]))
    msg, rcpts = FakeSMTP.sent[0]
    assert rcpts == ["alice@example.com", "c@example.com", "d@example.com"]
    assert msg["In-Reply-To"] == "<m1@x>" and "Bcc" not in msg
    assert any(p.get_filename() == "a.txt" for p in msg.iter_attachments())
    assert fake.appended and fake.appended[0][0] == '"Sent"'
