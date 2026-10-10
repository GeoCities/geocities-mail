"""Grok Bot Maildir-mirror backend: reading, flags, manifest, outbox queue and status transitions."""
import json
from pathlib import Path
import pytest
from textual.widgets import DataTable
from geomail.backends.base import BackendError
from geomail.backends.maildir import MaildirMirrorBackend, build_eml
from geomail.cli import main
from geomail.models import Outgoing
from geomail.tui import ComposeScreen, GeoMailApp


def eml(subject, mid, frm="Alice <alice@example.com>", attach=False):
    atts = [{"filename": "note.txt", "content_base64": "aGk=", "content_type": "text/plain"}] if attach else []
    return build_eml(frm, ["yourbot@mail.grokbot.com"], subject, f"body of {subject}",
                     date="2026-10-0%dT10:00:00Z" % (int(mid[-2]) if mid[-2].isdigit() else 1),
                     message_id=mid, attachments=atts)


@pytest.fixture
def mirror(tmp_path):
    b = MaildirMirrorBackend(tmp_path / "yourbot", address="yourbot@mail.grokbot.com")
    b.import_eml(eml("Hello", "<m1@x>", attach=True))
    b.import_eml(eml("Second", "<m2@x>"))
    b.import_eml(eml("Old", "<m3@x>"), unread=False)
    return b


def test_layout_and_read(mirror):
    root = mirror.root
    for d in ["INBOX/new", "INBOX/cur", "INBOX/tmp", "Sent/cur", "outbox/pending", "outbox/sent", "outbox/failed"]:
        assert (root / d).is_dir()
    msgs = mirror.list_messages("INBOX")
    assert {m.subject for m in msgs} == {"Hello", "Second", "Old"}
    assert {m.subject for m in msgs if m.unread} == {"Hello", "Second"}
    hello = next(m for m in msgs if m.subject == "Hello")
    assert hello.attachments[0].filename == "note.txt"
    assert mirror.get_attachment(hello.id, "0") == b"hi"
    # idempotent import
    assert mirror.import_eml(eml("Hello", "<m1@x>")) == hello.id
    assert len(mirror.list_messages("INBOX")) == 3
    assert [m.subject for m in mirror.list_messages(query="second")] == ["Second"]


def test_flags_and_delete(mirror):
    m = next(m for m in mirror.list_messages() if m.subject == "Hello")
    mirror.set_read(m.id, True)
    files = list((mirror.root / "INBOX" / "cur").iterdir())
    assert any(f.name.endswith(":2,S") for f in files)
    assert not mirror.get_message(m.id).unread
    mirror.set_read(m.id, False)
    assert mirror.get_message(m.id).unread
    mirror.delete(m.id)
    assert m.subject in [x.subject for x in mirror.list_messages("Trash")]
    assert m.subject not in [x.subject for x in mirror.list_messages("INBOX")]
    # re-import of a locally trashed message does not resurrect it
    mirror.import_eml(eml("Hello", "<m1@x>"))
    assert m.subject not in [x.subject for x in mirror.list_messages("INBOX")]


def test_manifest_status_line(mirror):
    assert mirror.status_line() == "Last synced: never"
    (mirror.root / "manifest.json").write_text(json.dumps({"version": 1, "last_synced": "2026-10-10T19:30:00Z"}))
    assert mirror.status_line().startswith("Last synced Oct 10")
    m = mirror.touch_manifest(synced_by="Mail Bot")
    assert m["synced_by"] == "Mail Bot" and m["last_synced"]


def test_outbox_pending_and_transitions(mirror, tmp_path):
    f = tmp_path / "pic.txt"; f.write_text("data")
    orig = next(m for m in mirror.list_messages() if m.subject == "Hello")
    oid = mirror.send(Outgoing(to=["alice@example.com"], cc=["c@example.com"], bcc=["b@example.com"],
                               subject="Re: Hello", body="Thanks!", in_reply_to=orig.id, attachments=[str(f)]))
    key = oid.split("/", 1)[1]
    pend = mirror.root / "outbox" / "pending"
    side = json.loads((pend / f"{key}.json").read_text())
    assert side["to"] == ["alice@example.com"] and side["bcc"] == ["b@example.com"]
    assert side["subject"] == "Re: Hello" and side["body"] == "Thanks!"
    assert side["in_reply_to"] == orig.id and side["in_reply_to_message_id"] == "<m1@x>"
    assert side["attachments"] == [str(f.resolve())] and side["status"] == "pending"
    assert side["require_approval"] is False
    raw = (pend / f"{key}.eml").read_bytes()
    assert b"In-Reply-To: <m1@x>" in raw and b"pic.txt" in raw and b"Bcc" not in raw
    ob = mirror.list_messages("Outbox")
    assert ob[0].status == "Sending" and ob[0].id == oid
    assert mirror.send_notice == "Sending… your mail bot will deliver it on its next sync"
    assert mirror.send_label == "Send"
    mirror.require_approval = True
    assert mirror.list_messages("Outbox")[0].status == "Waiting for approval"
    assert "after you approve" in mirror.send_notice and mirror.send_label == "Queue for approval"
    mirror.require_approval = False
    # bot sends it
    mirror.mark_outbox(key, "sent")
    assert not (pend / f"{key}.json").exists()
    assert mirror.get_message(oid).status == "Sent"
    # failed with reason (bot can also just move files + write reason.txt by hand)
    oid2 = mirror.send(Outgoing(to=["x@example.invalid"], subject="S", body="B")).split("/", 1)[1]
    for ext in (".json", ".eml"):
        (pend / f"{oid2}{ext}").rename(mirror.root / "outbox" / "failed" / f"{oid2}{ext}")
    (mirror.root / "outbox" / "failed" / f"{oid2}.reason.txt").write_text("User declined")
    assert mirror.get_message(f"outbox/{oid2}").status == "Failed: User declined"
    assert {i["status"] for i in mirror.outbox_items()} == {"sent", "failed"}
    with pytest.raises(BackendError):
        mirror.delete(f"outbox/{oid2}")  # only pending items can be cancelled


def test_cli_mirror_flow(tmp_path, capsys):
    root = tmp_path / "m"
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'default="g"\n[accounts.g]\nbackend="grokbot"\npath="{root}"\n')
    e = tmp_path / "a.eml"; e.write_bytes(eml("From eml", "<e1@x>"))
    j = tmp_path / "b.json"
    j.write_text(json.dumps([{"from": "Bob <bob@example.com>", "to": ["yourbot@mail.grokbot.com"],
                              "subject": "From json", "text": "hey", "date": "2026-10-10T12:00:00Z",
                              "message_id": "<j1@x>"}]))
    assert main(["--config", str(cfg), "--json", "mirror-import", str(e), str(j)]) == 0
    assert len(json.loads(capsys.readouterr().out)["imported"]) == 2
    assert json.loads((root / "manifest.json").read_text())["last_synced"]
    assert main(["--config", str(cfg), "--json", "send", "--to", "z@example.invalid", "--subject", "Q", "--body", "B"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["queued"] and d["sent"] is False and "next sync" in d["notice"]
    assert main(["--config", str(cfg), "--json", "outbox", "list", "--status", "pending"]) == 0
    items = json.loads(capsys.readouterr().out)
    assert items[0]["subject"] == "Q" and Path(items[0]["eml_path"]).exists()
    assert main(["--config", str(cfg), "--json", "outbox", "mark", items[0]["id"], "failed", "--reason", "nope"]) == 0
    capsys.readouterr()
    assert main(["--config", str(cfg), "--json", "outbox", "list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["reason"] == "nope"


def test_default_backend_detection(tmp_path, monkeypatch):
    import geomail.config as c
    from geomail.backends import BackendError, make_backend
    monkeypatch.setattr(c, "GROKBOT_MAIL_ROOT", tmp_path)
    monkeypatch.delenv(c.ADDRESS_ENV, raising=False)
    missing = tmp_path / "missing.toml"
    # root exists but nothing synced -> demo
    (tmp_path / "empty").mkdir()
    assert c.load_config(missing)["default"] == "demo"
    # exactly one synced mirror -> auto-detected, address from manifest
    (tmp_path / "alice").mkdir()
    (tmp_path / "alice" / "manifest.json").write_text(json.dumps({"account": "alice@mail.grokbot.com"}))
    cfg = c.load_config(missing)
    assert cfg["default"] == "grokbot"
    assert cfg["accounts"]["grokbot"] == {"backend": "grokbot", "path": str(tmp_path / "alice"),
                                          "address": "alice@mail.grokbot.com"}
    # two mirrors -> ambiguous -> demo
    (tmp_path / "bob").mkdir(); (tmp_path / "bob" / "manifest.json").write_text("{}")
    assert c.load_config(missing)["default"] == "demo"
    # env address wins and derives the path
    monkeypatch.setenv(c.ADDRESS_ENV, "Carol@mail.grokbot.com")
    acc = c.load_config(missing)["accounts"]["grokbot"]
    assert acc["path"] == str(tmp_path / "carol") and acc["address"] == "Carol@mail.grokbot.com"
    monkeypatch.delenv(c.ADDRESS_ENV)
    # grokbot account without address or path -> clear error
    with pytest.raises(BackendError, match="--grokbot-address"):
        make_backend("grokbot", {"backend": "grokbot"})


def test_init_grokbot_address(tmp_path, monkeypatch, capsys):
    import geomail.config as c
    monkeypatch.setattr(c, "GROKBOT_MAIL_ROOT", tmp_path / "gm")
    cfgp = tmp_path / "cfg" / "config.toml"
    assert main(["--config", str(cfgp), "init", "--grokbot-address", "dave@mail.grokbot.com",
                 "--sender-bot", "Dave Mail"]) == 0
    cfg = c.load_config(cfgp)
    assert cfg["default"] == "grokbot"
    g = cfg["accounts"]["grokbot"]
    assert g["address"] == "dave@mail.grokbot.com" and g["path"] == str(tmp_path / "gm" / "dave")
    assert (tmp_path / "gm" / "dave" / "INBOX" / "new").is_dir()
    assert main(["--config", str(cfgp), "init", "--grokbot-address", "x@mail.grokbot.com"]) == 1  # no clobber
    capsys.readouterr()
    assert main(["--config", str(cfgp), "--json", "send", "--to", "e@example.com", "--subject", "s", "--body", "b"]) == 0
    assert "Dave Mail" in json.loads(capsys.readouterr().out)["notice"]


async def test_tui_mirror_smoke(mirror):
    (mirror.root / "manifest.json").write_text(json.dumps({"last_synced": "2026-10-10T19:30:00Z"}))
    app = GeoMailApp(mirror)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        assert "Last synced Oct 10" in app.sub_title
        assert app.query_one("#messages", DataTable).row_count == 3
        first = app.current
        assert first is not None and not mirror.get_message(first.id).unread
        await pilot.press("r"); await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        await pilot.press("ctrl+s"); await pilot.pause()
        pending = list((mirror.root / "outbox" / "pending").glob("*.json"))
        assert len(pending) == 1
        assert json.loads(pending[0].read_text())["in_reply_to"] == first.id
        app.folder = "Outbox"; app.load(); await pilot.pause()
        assert app.query_one("#messages", DataTable).row_count == 1
        assert app.messages[0].status == "Sending"
        await pilot.press("q")


def test_require_approval_config(tmp_path, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'default="g"\n[accounts.g]\nbackend="grokbot"\npath="{tmp_path / "m"}"\nrequire_approval=true\n')
    assert main(["--config", str(cfg), "--json", "send", "--to", "a@example.com", "--subject", "s", "--body", "b"]) == 0
    assert "after you approve" in json.loads(capsys.readouterr().out)["notice"]
    cfg2 = tmp_path / "c2.toml"
    assert main(["--config", str(cfg2), "init", "--grokbot-address", "me@mail.grokbot.com", "--mirror-path",
                 str(tmp_path / "m2"), "--require-approval"]) == 0
    assert "require_approval = true" in cfg2.read_text()
