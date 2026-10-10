"""Full-screen Textual inbox with a tasteful retro GeoCities theme.

Everything is clickable (for touch/mouse use, e.g. viewing the bot's desktop from a phone) and also has a key.
"""
from __future__ import annotations
import os, subprocess, tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, ListItem, ListView, Static, TextArea
from .backends import Backend, BackendError
from .backends.base import DRAFTS, SCHEDULED, STARRED
from .htmltext import body_text, quote
from .models import Message, Outgoing
from .schedule import fmt_local, now_local, parse_when, preset

CSS = """
Screen { background: #000080; color: #ffffff; }
Header { background: #008080; color: #ffff00; text-style: bold; }
Footer { background: #c0c0c0; color: #000000; }
#left { width: 22; }
#compose-btn { width: 100%; margin: 0 0 1 0; }
#folders { height: 1fr; border: double #ffff00; background: #000060; }
#folders > ListItem.--highlight { background: #ff00ff; color: #ffffff; }
#right { width: 1fr; }
#messages { height: 45%; border: double #00ffff; background: #000060; }
#toolbar { height: 3; background: #000060; }
#toolbar Button { min-width: 8; margin: 0 1 0 0; }
#reader { height: 1fr; border: double #c0c0c0; background: #000040; padding: 0 1; }
#marquee { height: 1; background: #ffff00; color: #000080; text-style: bold; content-align: center middle; }
ModalScreen { align: center middle; background: #000000 60%; }
.dialog { width: 92; height: auto; max-height: 95%; border: thick #ffff00; background: #000080; padding: 1 2; }
.dialog Input { margin-bottom: 1; }
.dialog TextArea { height: 12; }
.dialog Button { margin: 0 1 1 0; }
.row { height: auto; }
"""

FOLDER_ICONS = {"INBOX": "📥", STARRED: "⭐", DRAFTS: "📝", SCHEDULED: "⏰", "Outbox": "📤", "Sent": "📨", "Trash": "🗑"}


def _short_date(iso: str) -> str:
    return iso[:16].replace("T", " ") if iso else ""


def _split(s):
    return [x.strip() for x in s.split(",") if x.strip()]


class ConfirmScreen(ModalScreen):
    """Generic button dialog. Dismisses with the id of the clicked button (or None on Esc)."""
    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, text: str, buttons: list):
        super().__init__()
        self.text, self.buttons = text, buttons

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.text)
            with Horizontal(classes="row"):
                for bid, label, variant in self.buttons:
                    yield Button(label, id=bid, variant=variant)

    def on_button_pressed(self, e: Button.Pressed):
        e.stop()
        self.dismiss(e.button.id)

    def action_cancel(self):
        self.dismiss(None)


class ScheduleScreen(ModalScreen):
    """Pick a send time. Dismisses with an aware datetime or None."""
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str = ""):
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        now = now_local()
        default = (parse_when(self.current) if self.current else preset("1h", now)).astimezone()
        with Vertical(classes="dialog"):
            yield Label(f"⏰ Schedule send (times are local, {now.strftime('%Z')})")
            with Horizontal(classes="row"):
                yield Button("In 1 hour", id="p-1h", variant="primary")
                yield Button("Tomorrow 8 AM", id="p-tomorrow", variant="primary")
                yield Button("Monday 8 AM", id="p-monday", variant="primary")
            yield Label("Custom (YYYY-MM-DD HH:MM):")
            yield Input(default.strftime("%Y-%m-%d %H:%M"), id="when")
            with Horizontal(classes="row"):
                yield Button("Schedule", id="custom", variant="success")
                yield Button("Cancel", id="cancel", variant="error")

    def on_button_pressed(self, e: Button.Pressed):
        e.stop()
        bid = e.button.id
        if bid == "cancel":
            self.dismiss(None)
        elif bid.startswith("p-"):
            self.dismiss(preset(bid[2:]))
        else:
            self._custom()

    def on_input_submitted(self, e: Input.Submitted):
        self._custom()

    def _custom(self):
        try:
            dt = parse_when(self.query_one("#when", Input).value)
        except ValueError:
            self.notify("Use YYYY-MM-DD HH:MM", severity="error")
            return
        if dt <= now_local():
            self.notify("Pick a time in the future", severity="error")
            return
        self.dismiss(dt)

    def action_cancel(self):
        self.dismiss(None)


class ComposeScreen(ModalScreen):
    """Dismisses with (action, Outgoing, extra) where action is send|draft|schedule, or None."""
    BINDINGS = [Binding("ctrl+s", "send", "Send"), Binding("ctrl+d", "save_draft", "Save draft"),
                Binding("ctrl+e", "editor", "$EDITOR"), Binding("escape", "cancel", "Cancel")]

    def __init__(self, out: Optional[Outgoing] = None, title="Compose", send_label="Send"):
        super().__init__()
        self.out = out or Outgoing(to=[], subject="", body="")
        self.title_text, self.send_label = title, send_label
        self._initial = self.out.to_dict()

    def compose(self) -> ComposeResult:
        o = self.out
        with Vertical(classes="dialog"):
            yield Label(f"✉ {self.title_text}  —  Ctrl+S send · Ctrl+D save draft · Ctrl+E $EDITOR · Esc cancel")
            yield Input(", ".join(o.to), placeholder="To (comma separated)", id="to")
            yield Input(", ".join(o.cc), placeholder="Cc", id="cc")
            yield Input(", ".join(o.bcc), placeholder="Bcc", id="bcc")
            yield Input(o.subject, placeholder="Subject", id="subject")
            yield Input(", ".join(o.attachments), placeholder="Attach files (comma separated paths)", id="attach")
            yield TextArea(o.body, id="body")
            with Horizontal(classes="row"):
                yield Button(self.send_label, variant="success", id="send")
                yield Button("💾 Save draft", variant="primary", id="draft")
                yield Button("⏰ Schedule", variant="warning", id="schedule")
                yield Button("Cancel", variant="error", id="cancel")

    def collect(self) -> Outgoing:
        q = lambda i: self.query_one(f"#{i}", Input).value
        return Outgoing(to=_split(q("to")), cc=_split(q("cc")), bcc=_split(q("bcc")), subject=q("subject"),
                        body=self.query_one("#body", TextArea).text, attachments=_split(q("attach")),
                        in_reply_to=self.out.in_reply_to, reply_all=self.out.reply_all)

    def on_button_pressed(self, e: Button.Pressed):
        e.stop()
        {"send": self.action_send, "draft": self.action_save_draft, "schedule": self.action_schedule,
         "cancel": self.action_cancel}[e.button.id]()

    def _need_rcpt(self, out) -> bool:
        if not out.to and not (out.in_reply_to and out.reply_all):
            self.notify("Add at least one recipient", severity="error")
            return True
        return False

    def action_send(self):
        out = self.collect()
        if not self._need_rcpt(out):
            self.dismiss(("send", out, None))

    def action_save_draft(self):
        self.dismiss(("draft", self.collect(), None))

    def action_schedule(self):
        out = self.collect()
        if self._need_rcpt(out):
            return

        def picked(dt):
            if dt is not None:
                self.dismiss(("schedule", out, dt))
        self.app.push_screen(ScheduleScreen(getattr(self.out, "send_at", "") or ""), picked)

    def action_cancel(self):
        out = self.collect()
        if out.is_empty() or out.to_dict() == self._initial:
            self.dismiss(None)
            return

        def answer(choice):
            if choice == "save":
                self.dismiss(("draft", out, None))
            elif choice == "discard":
                self.dismiss(None)
        self.app.push_screen(ConfirmScreen("Save this message as a draft?", [
            ("save", "💾 Save draft", "primary"), ("discard", "🗑 Discard", "error"), ("keep", "Keep editing", "default")]),
            answer)

    def action_editor(self):
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
        ta = self.query_one("#body", TextArea)
        with tempfile.NamedTemporaryFile("w+", suffix=".eml.txt", delete=False) as f:
            f.write(ta.text)
            path = f.name
        with self.app.suspend():
            subprocess.call([*editor.split(), path])
        ta.text = Path(path).read_text()
        os.unlink(path)


class PromptScreen(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, label: str, value: str = ""):
        super().__init__()
        self.label, self.value = label, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.label)
            yield Input(self.value, id="prompt")
            with Horizontal(classes="row"):
                yield Button("OK", id="ok", variant="success")
                yield Button("Cancel", id="cancel", variant="error")

    def on_input_submitted(self, e: Input.Submitted):
        self.dismiss(e.value)

    def on_button_pressed(self, e: Button.Pressed):
        e.stop()
        self.dismiss(self.query_one("#prompt", Input).value if e.button.id == "ok" else None)

    def action_cancel(self):
        self.dismiss(None)


class GeoMailApp(App):
    CSS = CSS
    TITLE = "★ GeoCities Mail ★"
    SUB_TITLE = "Welcome to my Inbox!"
    BINDINGS = [
        Binding("c", "compose", "Compose"), Binding("r", "reply", "Reply"), Binding("a", "reply_all", "Reply all"),
        Binding("f", "forward", "Fwd"), Binding("d", "delete", "Delete"), Binding("s", "star", "Star"),
        Binding("e", "edit", "Edit", show=False), Binding("u", "toggle_read", "Read/Unread"),
        Binding("slash", "search", "Search"), Binding("g", "refresh", "Refresh"),
        Binding("x", "save_attachment", "Save att."), Binding("q", "quit", "Quit"),
    ]
    SCHEDULER_INTERVAL = 60.0

    def __init__(self, backend: Backend):
        super().__init__()
        self.backend = backend
        self.folder = "INBOX"
        self.query_text: Optional[str] = None
        self.messages: List[Message] = []
        self.current: Optional[Message] = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("~*~ You are visitor #001337 · this inbox is best viewed in a terminal ~*~", id="marquee")
        with Horizontal():
            with Vertical(id="left"):
                yield Button("✎ Compose", id="compose-btn", variant="success")
                yield ListView(id="folders")
            with Vertical(id="right"):
                yield DataTable(id="messages", cursor_type="row", zebra_stripes=True)
                with Horizontal(id="toolbar"):
                    yield Button("↩ Reply", id="btn-reply")
                    yield Button("↩↩ All", id="btn-reply-all")
                    yield Button("➜ Fwd", id="btn-forward")
                    yield Button("☆ Star", id="btn-star")
                    yield Button("✎ Edit", id="btn-edit")
                    yield Button("⏰ Resched.", id="btn-reschedule")
                    yield Button("📎 Save", id="btn-save")
                    yield Button("🗑 Delete", id="btn-delete", variant="error")
                    yield Button("⟳", id="btn-refresh")
                with VerticalScroll(id="reader"):
                    yield Static("Select a message.", id="body")
        yield Footer()

    def on_mount(self):
        t = self.query_one("#messages", DataTable)
        t.add_columns("", "⭐", "From", "Subject", "Date")
        lv = self.query_one("#folders", ListView)
        for name in self.backend.all_folders():
            label = f"{FOLDER_ICONS.get(name, '📁')} {name}"
            lv.append(ListItem(Label(label), name=name))
        self.run_scheduler(reload=False)
        self.load()
        self.update_toolbar()
        t.focus()
        self.set_interval(self.SCHEDULER_INTERVAL, self.run_scheduler)

    def update_subtitle(self):
        parts = [self.backend.address, self.backend.name]
        if hasattr(self.backend, "status_line"):
            parts.append(self.backend.status_line())
        self.sub_title = " · ".join(p for p in parts if p)

    # --- scheduler ------------------------------------------------------
    def run_scheduler(self, reload: bool = True):
        try:
            res = self.backend.run_due()
        except Exception as e:  # never crash the UI because of the scheduler
            self.notify(f"Scheduler: {e}", severity="error")
            return
        ok = [r for r in res if r["ok"]]
        if ok:
            notice = getattr(self.backend, "send_notice", None)
            self.notify(f"{len(ok)} scheduled message(s) released. " +
                        ("Queued for your approval in Outbox." if notice else "Sent."), timeout=8)
        for r in res:
            if not r["ok"]:
                self.notify(f"Scheduled send failed: {r['error']}", severity="error")
        if res and reload:
            self.load()

    # --- data -----------------------------------------------------------
    def load(self):
        self.update_subtitle()
        try:
            self.messages = self.backend.list_folder(self.folder, 100, self.query_text)
        except BackendError as e:
            self.notify(str(e), severity="error")
            self.messages = []
        t = self.query_one("#messages", DataTable)
        t.clear()
        for m in self.messages:
            t.add_row(*self._cells(m), key=m.id)
        label = f"search: {self.query_text}" if self.query_text else self.folder
        t.border_title = f"{label} ({len(self.messages)})"
        if not self.messages:
            self.current = None
            self.query_one("#body", Static).update("No messages here. Sign my guestbook?")
            self.update_toolbar()

    def _cells(self, m: Message):
        style = "bold" if m.unread else ""
        if m.folder in (SCHEDULED, "Outbox", DRAFTS) or m.status:
            who = ("To: " + ", ".join(m.to))[:30]
        else:
            who = m.sender[:30]
        prefix = f"[{m.status}] " if m.status else ""
        date = fmt_local(m.send_at) if m.send_at else _short_date(m.date)
        return (Text("●" if m.unread else " ", style="yellow"), Text("⭐" if m.starred else " "),
                Text(who, style=style), Text(prefix + (m.subject or "(no subject)"), style=style),
                Text(date, style=style))

    def selected(self) -> Optional[Message]:
        t = self.query_one("#messages", DataTable)
        if not self.messages or t.cursor_row is None or t.cursor_row >= len(self.messages):
            return None
        return self.messages[t.cursor_row]

    def _kind(self, m: Optional[Message]) -> str:
        if not m:
            return "none"
        if m.id.startswith("sched/"):
            return "scheduled"
        if m.folder == DRAFTS:
            return "draft"
        if m.id.startswith("outbox/"):
            return "outbox"
        return "mail"

    def update_toolbar(self):
        kind = self._kind(self.current)
        show = {
            "btn-reply": kind == "mail", "btn-reply-all": kind == "mail", "btn-forward": kind in ("mail", "outbox"),
            "btn-star": kind == "mail", "btn-edit": kind in ("draft", "scheduled"),
            "btn-reschedule": kind == "scheduled",
            "btn-save": bool(self.current and self.current.attachments),
            "btn-delete": kind != "none", "btn-refresh": True,
        }
        for bid, vis in show.items():
            self.query_one(f"#{bid}", Button).display = vis
        if self.current:
            self.query_one("#btn-star", Button).label = "★ Unstar" if self.current.starred else "☆ Star"
            self.query_one("#btn-delete", Button).label = {"scheduled": "✖ Cancel", "draft": "🗑 Discard"}.get(
                kind, "🗑 Delete")

    def show(self, m: Message):
        try:
            full = self.backend.open_message(m.id)
            if full.unread and self._kind(full) == "mail":
                self.backend.set_read(full.id, True)
                full.unread = m.unread = False
                self._restyle(m)
        except BackendError as e:
            self.notify(str(e), severity="error")
            return
        full.folder = m.folder if m.folder in (DRAFTS, SCHEDULED) else full.folder
        full.starred = full.starred or m.starred
        self.current = full
        hdr = Text()
        hdr.append(f"From: {full.sender}\n", style="bold yellow")
        hdr.append(f"To: {', '.join(full.to)}\n")
        if full.cc:
            hdr.append(f"Cc: {', '.join(full.cc)}\n")
        hdr.append(f"Date: {_short_date(full.date)}\n")
        hdr.append(f"Subject: {full.subject}" + ("  ⭐" if full.starred else "") + "\n", style="bold cyan")
        if full.status:
            hdr.append(f"Status: {full.status}\n", style="bold green" if full.status == "Sent" else "bold red"
                       if full.status.startswith("Failed") else "bold yellow")
        if self._kind(full) in ("draft", "scheduled"):
            hdr.append("Tap ✎ Edit (or press e / Enter) to open it in the editor.\n", style="italic")
        for i, a in enumerate(full.attachments, 1):
            hdr.append(f"📎 [{i}] {a.filename} ({a.size} B)  — tap 📎 Save or press x\n", style="magenta")
        hdr.append("─" * 60 + "\n")
        hdr.append(body_text(full))
        self.query_one("#body", Static).update(hdr)
        self.update_toolbar()

    def _restyle(self, m: Message):
        t = self.query_one("#messages", DataTable)
        try:
            row = t.get_row_index(m.id)
        except Exception:
            return
        for col, val in enumerate(self._cells(m)):
            t.update_cell_at((row, col), val)

    # --- events ---------------------------------------------------------
    def on_data_table_row_highlighted(self, e: DataTable.RowHighlighted):
        m = self.selected()
        if m:
            self.show(m)

    def on_data_table_row_selected(self, e: DataTable.RowSelected):
        if self._kind(self.current) in ("draft", "scheduled"):
            self.action_edit()

    def on_list_view_selected(self, e: ListView.Selected):
        self.open_folder(e.item.name)

    def open_folder(self, name: str):
        self.folder = name
        self.query_text = None
        self.current = None
        self.load()
        self.update_toolbar()
        self.query_one("#messages", DataTable).focus()

    def on_button_pressed(self, e: Button.Pressed):
        actions = {"compose-btn": self.action_compose, "btn-reply": self.action_reply,
                   "btn-reply-all": self.action_reply_all, "btn-forward": self.action_forward,
                   "btn-star": self.action_star, "btn-edit": self.action_edit,
                   "btn-reschedule": self.action_reschedule, "btn-save": self.action_save_attachment,
                   "btn-delete": self.action_delete, "btn-refresh": self.action_refresh}
        if e.button.id in actions:
            actions[e.button.id]()

    # --- compose / drafts / schedule ------------------------------------
    def open_compose(self, out: Optional[Outgoing] = None, title="Compose", draft_id=None, sched_id=None,
                     send_at: str = ""):
        notice = getattr(self.backend, "send_notice", None)
        scr = ComposeScreen(out, title=title, send_label="Queue for approval" if notice else "Send")
        scr.out.send_at = send_at  # type: ignore[attr-defined]
        self.push_screen(scr, lambda res: self._after_compose(res, draft_id, sched_id))

    def _after_compose(self, res, draft_id=None, sched_id=None):
        if res is None:
            return
        action, out, extra = res
        try:
            if action == "send":
                self.backend.send(out)
                self.notify(getattr(self.backend, "send_notice", None) or "Message sent! ✨", timeout=8)
            elif action == "draft":
                if not self.backend.supports_drafts:
                    raise BackendError("Drafts aren't supported by this backend")
                self.backend.save_draft(out, draft_id)
                draft_id = None  # save_draft replaced it
                self.notify("Draft saved 💾")
            elif action == "schedule":
                sid = sched_id.split("/", 1)[1] if sched_id else None
                self.backend.schedule(out, extra, sid)
                sched_id = None  # rescheduled in place
                notice = " It will be queued for your approval then." if getattr(self.backend, "send_notice", None) \
                    else " Keep GeoCities Mail or `geocities-mail scheduler run` running so it goes out."
                self.notify(f"Scheduled for {fmt_local(extra.isoformat())}.{notice}", timeout=8)
            if draft_id:  # draft was sent or scheduled: remove it
                self.backend.delete_draft(draft_id)
            if sched_id and action in ("send", "draft"):
                self.backend.cancel_scheduled(sched_id)
        except (BackendError, OSError, ValueError) as e:
            self.notify(f"Failed: {e}", severity="error")
        self.load()

    def action_compose(self):
        self.open_compose()

    def action_edit(self):
        m = self.current
        kind = self._kind(m)
        if kind not in ("draft", "scheduled"):
            return
        try:
            out = self.backend.get_outgoing(m)
        except BackendError as e:
            self.notify(str(e), severity="error")
            return
        if kind == "draft":
            self.open_compose(out, title="Edit draft", draft_id=m.id)
        else:
            self.open_compose(out, title=f"Edit scheduled message ({fmt_local(m.send_at)})", sched_id=m.id,
                              send_at=m.send_at)

    def action_reschedule(self):
        m = self.current
        if self._kind(m) != "scheduled":
            return

        def picked(dt):
            if dt is None:
                return
            try:
                self.backend.schedule(self.backend.get_outgoing(m), dt, m.id.split("/", 1)[1])
                self.notify(f"Rescheduled for {fmt_local(dt.isoformat())}")
            except BackendError as e:
                self.notify(str(e), severity="error")
            self.load()
        self.push_screen(ScheduleScreen(m.send_at), picked)

    def _reply(self, all_: bool):
        m = self.current
        if self._kind(m) != "mail":
            return
        subj = m.subject if m.subject.lower().startswith("re:") else f"Re: {m.subject}"
        cc = []
        if all_:
            me = (self.backend.address or "").lower()
            cc = [a for a in m.to + m.cc if not me or me not in a.lower()]
        body = f"\n\nOn {_short_date(m.date)}, {m.sender} wrote:\n{quote(body_text(m))}"
        self.open_compose(Outgoing(to=[m.sender], cc=cc, subject=subj, body=body, in_reply_to=m.id,
                                   reply_all=all_), title="Reply all" if all_ else "Reply")

    def action_reply(self):
        self._reply(False)

    def action_reply_all(self):
        self._reply(True)

    def action_forward(self):
        m = self.current
        if self._kind(m) not in ("mail", "outbox"):
            return
        body = (f"\n\n---------- Forwarded message ----------\nFrom: {m.sender}\nDate: {m.date}\n"
                f"Subject: {m.subject}\nTo: {', '.join(m.to)}\n\n{body_text(m)}")
        self.open_compose(Outgoing(to=[], subject=f"Fwd: {m.subject}", body=body), title="Forward")

    # --- other actions --------------------------------------------------
    def action_delete(self):
        m = self.current or self.selected()
        if not m:
            return
        try:
            self.notify(self.backend.remove(m))
        except BackendError as e:
            self.notify(str(e), severity="error")
        self.current = None
        self.load()
        self.update_toolbar()

    def action_star(self):
        m = self.current
        if self._kind(m) != "mail":
            return
        try:
            self.backend.set_starred(m.id, not m.starred)
        except BackendError as e:
            self.notify(str(e), severity="error")
            return
        m.starred = not m.starred
        for x in self.messages:
            if x.id == m.id:
                x.starred = m.starred
                self._restyle(x)
        self.notify("⭐ Starred" if m.starred else "Unstarred")
        if self.folder == STARRED and not m.starred:
            self.load()
        self.update_toolbar()

    def action_toggle_read(self):
        m = self.selected()
        if self._kind(m) != "mail":
            return
        try:
            self.backend.set_read(m.id, m.unread)
            m.unread = not m.unread
            self._restyle(m)
        except BackendError as e:
            self.notify(str(e), severity="error")

    def action_search(self):
        def done(q):
            if q is None:
                return
            self.query_text = q.strip() or None
            self.load()
        self.push_screen(PromptScreen("🔍 Search (empty to clear):", self.query_text or ""), done)

    def action_refresh(self):
        self.run_scheduler(reload=False)
        self.load()
        self.notify("Refreshed")

    def action_save_attachment(self):
        m = self.current
        if not m or not m.attachments:
            self.notify("No attachments on this message")
            return

        def done(v):
            if not v:
                return
            num, _, dest = v.partition(" ")
            try:
                att = m.attachments[int(num) - 1]
            except (ValueError, IndexError):
                self.notify("Use: <number> [path]", severity="error")
                return
            path = Path(os.path.expanduser(dest.strip() or "~/Downloads"))
            if path.is_dir() or not path.suffix:
                path.mkdir(parents=True, exist_ok=True)
                path = path / Path(att.filename).name
            try:
                path.write_bytes(self.backend.get_attachment(m.id, att.id))
                self.notify(f"Saved {path}")
            except (BackendError, OSError) as e:
                self.notify(str(e), severity="error")
        self.push_screen(PromptScreen("Save attachment: <number> [folder or path]", "1 ~/Downloads"), done)


def run_tui(backend: Backend):
    try:
        GeoMailApp(backend).run()
    finally:
        backend.close()
