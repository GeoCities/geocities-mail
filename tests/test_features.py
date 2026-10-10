"""Stars, drafts, scheduling, folder order — backends, CLI and clickable TUI."""
import json
from datetime import datetime, timedelta
import httpx
import pytest
from textual.widgets import DataTable, Input, TextArea
from geomail.backends.agentmail import AgentMailBackend
from geomail.backends.base import FOLDER_ORDER
from geomail.backends.maildir import MaildirMirrorBackend, build_eml
from geomail.cli import main
from geomail.models import Outgoing
from geomail.schedule import preset
from geomail.tui import ComposeScreen, ConfirmScreen, GeoMailApp, ScheduleScreen

PAST = lambda: datetime.now().astimezone() - timedelta(minutes=5)
FUTURE = lambda: datetime.now().astimezone() + timedelta(days=2)


@pytest.fixture
def mirror(tmp_path):
    b = MaildirMirrorBackend(tmp_path / "yourbot", address="yourbot@mail.grokbot.com")
    for i, subj in enumerate(["Hello", "Second"], 1):
        b.import_eml(build_eml("Alice <alice@example.com>", ["yourbot@mail.grokbot.com"], subj, f"body {subj}",
                               date=f"2026-10-0{i}T10:00:00Z", message_id=f"<m{i}@x>"))
    b.import_eml(build_eml("yourbot@mail.grokbot.com", ["bob@example.com"], "Sent one", "s",
                           message_id="<s1@x>"), folder="Sent")
    return b


# ---------------------------------------------------------------- folder order
def test_folder_order(mirror, demo):
    assert mirror.all_folders() == FOLDER_ORDER
    assert demo.all_folders() == ["INBOX", "Starred", "Drafts", "Scheduled", "Sent", "Trash"]


# ---------------------------------------------------------------- stars
def test_maildir_stars(mirror):
    hello = next(m for m in mirror.list_messages() if m.subject == "Hello")
    sent = mirror.list_messages("Sent")[0]
    mirror.set_starred(hello.id, True)
    mirror.set_starred(sent.id, True)
    assert any(f.name.endswith(":2,F") for f in (mirror.root / "INBOX" / "cur").iterdir())
    assert {m.subject for m in mirror.list_folder("Starred")} == {"Hello", "Sent one"}
    mirror.set_read(hello.id, True)  # flags combine
    assert any(f.name.endswith(":2,FS") for f in (mirror.root / "INBOX" / "cur").iterdir())
    mirror.set_starred(hello.id, False)
    assert [m.subject for m in mirror.list_folder("Starred")] == ["Sent one"]


def test_demo_stars(demo):
    demo.set_starred("d2", True)
    assert [m.id for m in demo.list_folder("Starred")] == ["d2"]
    assert demo.get_message("d2").starred


# ---------------------------------------------------------------- drafts
def test_maildir_drafts(mirror, tmp_path):
    out = Outgoing(to=["a@example.com"], cc=["c@example.com"], bcc=["b@example.com"], subject="Draft 1",
                   body="wip", in_reply_to="INBOX/x", reply_all=True, attachments=[str(tmp_path / "f.pdf")])
    did = mirror.save_draft(out)
    files = list((mirror.root / "Drafts" / "cur").iterdir())
    assert len(files) == 1 and files[0].name.endswith(":2,DS")
    assert [m.subject for m in mirror.list_folder("Drafts")] == ["Draft 1"]
    back = mirror.get_draft(did)
    assert (back.to, back.cc, back.bcc, back.subject, back.body.strip()) == \
        (["a@example.com"], ["c@example.com"], ["b@example.com"], "Draft 1", "wip")
    assert back.in_reply_to == "INBOX/x" and back.reply_all and back.attachments == [str(tmp_path / "f.pdf")]
    back.subject = "Draft 2"
    did2 = mirror.save_draft(back, did)  # edit replaces
    assert [m.subject for m in mirror.list_folder("Drafts")] == ["Draft 2"]
    assert mirror.remove(mirror.get_message(did2)) == "Draft discarded"
    assert mirror.list_folder("Drafts") == []


def test_demo_drafts(demo):
    did = demo.save_draft(Outgoing(to=["a@example.com"], subject="D", body="x"))
    assert demo.list_folder("Drafts")[0].id == did
    assert demo.get_draft(did).subject == "D"
    demo.save_draft(Outgoing(to=["a@example.com"], subject="D2", body="x"), did)
    assert [m.subject for m in demo.list_folder("Drafts")] == ["D2"]
    demo.delete_draft(did)
    assert demo.list_folder("Drafts") == []


# ---------------------------------------------------------------- scheduling
def test_presets():
    mon = datetime(2026, 10, 12, 9, 30).astimezone()  # a Monday
    assert preset("monday", mon).date().isoformat() == "2026-10-19"
    assert preset("tomorrow", mon).hour == 8 and preset("tomorrow", mon).day == 13
    assert preset("1h", mon).hour == 10


def test_maildir_schedule_lifecycle(mirror):
    sid = mirror.schedule(Outgoing(to=["a@example.com"], subject="Later", body="b"), FUTURE())
    files = list((mirror.root / "Scheduled").glob("*.json"))
    assert len(files) == 1
    side = json.loads(files[0].read_text())
    assert side["send_at"][-6] in "+-" and side["subject"] == "Later"  # ISO with timezone
    sched = mirror.list_folder("Scheduled")
    assert sched[0].status.startswith("Scheduled for") and sched[0].id == sid
    assert mirror.run_due() == []  # not due yet
    # reschedule in place to the past, then release
    mirror.schedule(mirror.get_outgoing(sched[0]), PAST(), sid.split("/", 1)[1])
    assert len(mirror.list_folder("Scheduled")) == 1
    res = mirror.run_due()
    assert res[0]["ok"] and mirror.list_folder("Scheduled") == []
    pend = mirror.outbox_items("pending")  # queued for approval, NOT sent
    assert pend[0]["subject"] == "Later" and pend[0]["scheduled_for"]
    assert "Sending (scheduled for" in mirror.list_folder("Outbox")[0].status
    # cancel
    sid2 = mirror.schedule(Outgoing(to=["a@example.com"], subject="X", body="b"), FUTURE())
    assert mirror.remove(mirror.open_message(sid2)) == "Scheduled message cancelled"
    assert mirror.list_folder("Scheduled") == []


def test_demo_schedule_sends_when_due(demo):
    demo.schedule(Outgoing(to=["a@example.com"], subject="Due", body="b"), PAST())
    demo.schedule(Outgoing(to=["a@example.com"], subject="NotYet", body="b"), FUTURE())
    res = demo.run_due()
    assert len(res) == 1 and res[0]["ok"]
    assert any(m.subject == "Due" for m in demo.list_messages("Sent"))
    assert [m.subject for m in demo.list_folder("Scheduled")] == ["NotYet"]


def test_failed_release_stays_scheduled(demo):
    demo.schedule(Outgoing(to=["a@example.com"], subject="Bad", body="b", attachments=["/nope/missing"]), PAST())
    res = demo.run_due()
    assert not res[0]["ok"]
    assert "last attempt failed" in demo.list_folder("Scheduled")[0].status


# ---------------------------------------------------------------- IMAP (mocked)
def test_imap_star_and_drafts(monkeypatch):
    from tests.test_imap import FakeIMAP, make
    b, fake = make(monkeypatch)
    fake.boxes["Drafts"] = {}
    fake.boxes["Sent"] = {}
    orig_list = FakeIMAP.list
    monkeypatch.setattr(FakeIMAP, "list", lambda self: ("OK", orig_list(self)[1] + [b'(\\Drafts) "/" "Drafts"']))

    def append(box, flags, dt, data):
        fake.appended.append((box, flags, data))
        box = box.strip('"')
        fake.boxes[box][len(fake.boxes[box]) + 10] = [data, set(flags.strip("()").split())]
        return "OK", []
    fake.append = append
    b.set_starred("INBOX/1", True)
    assert "\\Flagged" in fake.boxes["INBOX"][1][1]
    assert [m.subject for m in b.list_folder("Starred")] == ["First"]
    b.save_draft(Outgoing(to=["a@example.com"], bcc=["z@example.com"], subject="IMAP draft", body="hi"))
    assert fake.appended[-1][0] == '"Drafts"' and "\\Draft" in fake.appended[-1][1]
    drafts = b.list_folder("Drafts")
    assert drafts[0].subject == "IMAP draft" and drafts[0].folder == "Drafts"
    back = b.get_draft(drafts[0].id)
    assert back.bcc == ["z@example.com"] and back.body.strip() == "hi"
    b.delete_draft(drafts[0].id)
    assert fake.boxes["Drafts"] == {}


# ---------------------------------------------------------------- AgentMail (mocked)
def test_agentmail_star_and_drafts():
    calls = []

    def handler(req):
        calls.append(req)
        p = req.url.path
        if p.endswith("/drafts") and req.method == "POST":
            return httpx.Response(200, json={"draft_id": "dr1"})
        if p.endswith("/drafts"):
            return httpx.Response(200, json={"drafts": [{"draft_id": "dr1", "subject": "D", "to": ["a@example.com"],
                                                         "updated_at": "2026-10-10T00:00:00Z"}]})
        if "/drafts/" in p and req.method == "GET":
            return httpx.Response(200, json={"draft_id": "dr1", "subject": "D", "to": ["a@example.com"], "text": "t"})
        return httpx.Response(200, json={"messages": []})

    c = httpx.Client(base_url="https://api.agentmail.to", transport=httpx.MockTransport(handler))
    b = AgentMailBackend(api_key="K", inbox_id="bot@agentmail.to", client=c)
    b.set_starred("<m@x>", True)
    assert json.loads(calls[-1].content) == {"add_labels": ["starred"]}
    b.list_starred()
    assert calls[-1].url.params["labels"] == "starred"
    assert b.save_draft(Outgoing(to=["a@example.com"], subject="D", body="t")) == "dr1"
    assert b.list_folder("Drafts")[0].id == "dr1"
    assert b.get_draft("dr1").body == "t"
    b.delete_draft("dr1")
    assert calls[-1].method == "DELETE" and calls[-1].url.path.endswith("/drafts/dr1")


# ---------------------------------------------------------------- CLI
def test_cli_features(tmp_path, capsys):
    root = tmp_path / "m"
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'default="g"\n[accounts.g]\nbackend="grokbot"\npath="{root}"\naddress="me@example.com"\n')
    run = lambda *a: (main(["--config", str(cfg), "--json", *a]), json.loads(capsys.readouterr().out))[1]
    e = tmp_path / "a.eml"
    e.write_bytes(build_eml("x@example.com", ["me@example.com"], "In", "b", message_id="<i@x>"))
    mid = run("mirror-import", str(e))["imported"][0]
    assert run("star", mid)["starred"]
    assert [m["subject"] for m in run("list", "--folder", "Starred")] == ["In"]
    assert run("send", "--to", "a@example.com", "--subject", "D", "--body", "b", "--draft")["draft"]
    assert [m["subject"] for m in run("list", "--folder", "Drafts")] == ["D"]
    past = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    s = run("send", "--to", "a@example.com", "--subject", "S", "--body", "b", "--at", past)
    assert s["scheduled"] and s["id"].startswith("sched/")
    assert len(run("scheduler", "list")) == 1
    r = run("scheduler", "run")["released"]
    assert r[0]["ok"] and run("scheduler", "list") == []
    assert run("outbox", "list", "--status", "pending")[0]["subject"] == "S"
    assert run("folders") == FOLDER_ORDER


# ---------------------------------------------------------------- TUI (clicks)
async def test_tui_click_everything(mirror):
    app = GeoMailApp(mirror)
    async with app.run_test(size=(150, 50)) as pilot:
        await pilot.pause()
        folders = [i.name for i in app.query_one("#folders").children]
        assert folders == FOLDER_ORDER
        # star via button
        first = app.current
        await pilot.click("#btn-star"); await pilot.pause()
        assert mirror.get_message(first.id).starred
        app.open_folder("Starred"); await pilot.pause()
        assert app.query_one("#messages", DataTable).row_count == 1
        app.open_folder("INBOX"); await pilot.pause()
        # compose via button -> save draft via button
        await pilot.click("#compose-btn"); await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        assert str(app.screen.query_one("#send").label) == "Send"
        app.screen.query_one("#to", Input).value = "pal@example.com"
        app.screen.query_one("#subject", Input).value = "Draft subj"
        await pilot.click("#draft"); await pilot.pause()
        assert [m.subject for m in mirror.list_folder("Drafts")] == ["Draft subj"]
        # open draft, edit, send -> draft gone, outbox pending
        app.open_folder("Drafts"); await pilot.pause()
        await pilot.click("#btn-edit"); await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        assert app.screen.query_one("#subject", Input).value == "Draft subj"
        app.screen.query_one("#subject", Input).value = "Final subj"
        await pilot.click("#send"); await pilot.pause()
        assert mirror.list_folder("Drafts") == []
        assert mirror.outbox_items("pending")[0]["subject"] == "Final subj"
        # cancel with content -> asks -> save
        await pilot.click("#compose-btn"); await pilot.pause()
        app.screen.query_one("#to", Input).value = "x@example.com"
        await pilot.click("#cancel"); await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#save"); await pilot.pause()
        assert len(mirror.list_folder("Drafts")) == 1
        # cancel with content -> discard
        await pilot.click("#compose-btn"); await pilot.pause()
        app.screen.query_one("#to", Input).value = "y@example.com"
        await pilot.click("#cancel"); await pilot.pause()
        await pilot.click("#discard"); await pilot.pause()
        assert len(mirror.list_folder("Drafts")) == 1
        # schedule via picker preset
        await pilot.click("#compose-btn"); await pilot.pause()
        app.screen.query_one("#to", Input).value = "z@example.com"
        app.screen.query_one("#subject", Input).value = "Sched"
        await pilot.click("#schedule"); await pilot.pause()
        assert isinstance(app.screen, ScheduleScreen)
        await pilot.click("#p-tomorrow"); await pilot.pause()
        sched = mirror.list_folder("Scheduled")
        assert [m.subject for m in sched] == ["Sched"] and "08:00" in sched[0].send_at
        app.open_folder("Scheduled"); await pilot.pause()
        assert app.query_one("#messages", DataTable).row_count == 1
        # reschedule via custom time
        await pilot.click("#btn-reschedule"); await pilot.pause()
        when = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d 09:15")
        app.screen.query_one("#when", Input).value = when
        await pilot.click("#custom"); await pilot.pause()
        assert "09:15" in mirror.list_folder("Scheduled")[0].send_at
        # cancel scheduled via delete button
        await pilot.click("#btn-delete"); await pilot.pause()
        assert mirror.list_folder("Scheduled") == []
        # due item released on refresh -> outbox pending (approval still required)
        mirror.schedule(Outgoing(to=["q@example.com"], subject="Due now", body="b"), PAST())
        await pilot.click("#btn-refresh"); await pilot.pause()
        assert any(i["subject"] == "Due now" for i in mirror.outbox_items("pending"))
        await pilot.press("q")


async def test_tui_keys_star_and_save_moved(demo):
    app = GeoMailApp(demo)
    async with app.run_test(size=(150, 50)) as pilot:
        await pilot.pause()
        await pilot.press("s"); await pilot.pause()
        assert demo.get_message("d1").starred
        await pilot.press("x"); await pilot.pause()  # save attachment prompt (d1 has one)
        assert app.screen.__class__.__name__ == "PromptScreen"
        await pilot.press("escape"); await pilot.press("q")
