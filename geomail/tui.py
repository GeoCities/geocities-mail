"""Full-screen Textual inbox with a tasteful retro GeoCities theme."""
from __future__ import annotations
import os, subprocess, tempfile
from pathlib import Path
from typing import List, Optional
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, ListItem, ListView, Static, TextArea
from .backends import Backend, BackendError
from .htmltext import body_text, quote
from .models import Message, Outgoing

CSS = """
Screen { background: #000080; color: #ffffff; }
Header { background: #008080; color: #ffff00; text-style: bold; }
Footer { background: #c0c0c0; color: #000000; }
#folders { width: 18; border: double #ffff00; background: #000060; }
#folders > ListItem.--highlight { background: #ff00ff; color: #ffffff; }
#right { width: 1fr; }
#messages { height: 45%; border: double #00ffff; background: #000060; }
#reader { height: 1fr; border: double #c0c0c0; background: #000040; padding: 0 1; }
#marquee { height: 1; background: #ffff00; color: #000080; text-style: bold; content-align: center middle; }
ModalScreen { align: center middle; background: #000000 60%; }
.dialog { width: 90; height: auto; max-height: 90%; border: thick #ffff00; background: #000080; padding: 1 2; }
.dialog Input { margin-bottom: 1; }
.dialog TextArea { height: 14; }
.dialog Button { margin-right: 2; }
"""


def _short_date(iso: str) -> str:
    return iso[:16].replace("T", " ") if iso else ""


class ComposeScreen(ModalScreen):
    BINDINGS = [Binding("ctrl+s", "send", "Send"), Binding("ctrl+e", "editor", "$EDITOR"),
                Binding("escape", "cancel", "Cancel")]

    def __init__(self, to="", cc="", subject="", body="", in_reply_to=None, reply_all=False, title="Compose"):
        super().__init__()
        self.init = dict(to=to, cc=cc, subject=subject, body=body)
        self.in_reply_to, self.reply_all, self.title_text = in_reply_to, reply_all, title

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"✉ {self.title_text}  —  Ctrl+S send · Ctrl+E $EDITOR · Esc cancel")
            yield Input(self.init["to"], placeholder="To (comma separated)", id="to")
            yield Input(self.init["cc"], placeholder="Cc", id="cc")
            yield Input(self.init["subject"], placeholder="Subject", id="subject")
            yield Input("", placeholder="Attach files (comma separated paths)", id="attach")
            yield TextArea(self.init["body"], id="body")
            with Horizontal():
                yield Button("Send", variant="success", id="send")
                yield Button("Cancel", variant="error", id="cancel")

    def on_button_pressed(self, e: Button.Pressed):
        self.action_send() if e.button.id == "send" else self.action_cancel()

    def _split(self, s):
        return [x.strip() for x in s.split(",") if x.strip()]

    def action_send(self):
        out = Outgoing(to=self._split(self.query_one("#to", Input).value),
                       cc=self._split(self.query_one("#cc", Input).value),
                       subject=self.query_one("#subject", Input).value,
                       body=self.query_one("#body", TextArea).text,
                       attachments=self._split(self.query_one("#attach", Input).value),
                       in_reply_to=self.in_reply_to, reply_all=self.reply_all)
        if not out.to and not (self.in_reply_to and self.reply_all):
            self.notify("Add at least one recipient", severity="error")
            return
        self.dismiss(out)

    def action_cancel(self):
        self.dismiss(None)

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

    def on_input_submitted(self, e: Input.Submitted):
        self.dismiss(e.value)

    def action_cancel(self):
        self.dismiss(None)


class GeoMailApp(App):
    CSS = CSS
    TITLE = "★ GeoCities Mail ★"
    SUB_TITLE = "Welcome to my Inbox!"
    BINDINGS = [
        Binding("c", "compose", "Compose"), Binding("r", "reply", "Reply"), Binding("a", "reply_all", "Reply all"),
        Binding("f", "forward", "Fwd"), Binding("d", "delete", "Delete"), Binding("u", "toggle_read", "Read/Unread"),
        Binding("slash", "search", "Search"), Binding("g", "refresh", "Refresh"),
        Binding("s", "save_attachment", "Save att."), Binding("q", "quit", "Quit"),
    ]

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
            yield ListView(id="folders")
            with Vertical(id="right"):
                yield DataTable(id="messages", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="reader"):
                    yield Static("Select a message.", id="body")
        yield Footer()

    def on_mount(self):
        self.update_subtitle()
        t = self.query_one("#messages", DataTable)
        t.add_columns("", "From", "Subject", "Date")
        lv = self.query_one("#folders", ListView)
        for name in self.backend.folders():
            lv.append(ListItem(Label(f"📁 {name}"), name=name))
        self.load()
        t.focus()

    def update_subtitle(self):
        parts = [self.backend.address, self.backend.name]
        if hasattr(self.backend, "status_line"):
            parts.append(self.backend.status_line())
        self.sub_title = " · ".join(p for p in parts if p)

    # --- data -----------------------------------------------------------
    def load(self):
        self.update_subtitle()
        try:
            self.messages = self.backend.list_messages(self.folder, 100, self.query_text)
        except BackendError as e:
            self.notify(str(e), severity="error")
            self.messages = []
        t = self.query_one("#messages", DataTable)
        t.clear()
        for m in self.messages:
            style = "bold" if m.unread else ""
            who = ("To: " + ", ".join(m.to))[:30] if m.status else m.sender[:30]
            subj = f"[{m.status}] " if m.status else ""
            t.add_row(Text("●" if m.unread else " ", style="yellow"), Text(who, style=style),
                      Text(subj + (m.subject or "(no subject)"), style=style), Text(_short_date(m.date), style=style),
                      key=m.id)
        label = f"search: {self.query_text}" if self.query_text else self.folder
        t.border_title = f"{label} ({len(self.messages)})"
        if not self.messages:
            self.current = None
            self.query_one("#body", Static).update("No messages here. Sign my guestbook?")

    def selected(self) -> Optional[Message]:
        t = self.query_one("#messages", DataTable)
        if not self.messages or t.cursor_row is None or t.cursor_row >= len(self.messages):
            return None
        return self.messages[t.cursor_row]

    def show(self, m: Message):
        try:
            full = self.backend.get_message(m.id)
            if full.unread:
                self.backend.set_read(full.id, True)
                full.unread = False
                m.unread = False
                self._restyle(m)
        except BackendError as e:
            self.notify(str(e), severity="error")
            return
        self.current = full
        hdr = Text()
        hdr.append(f"From: {full.sender}\n", style="bold yellow")
        hdr.append(f"To: {', '.join(full.to)}\n")
        if full.cc:
            hdr.append(f"Cc: {', '.join(full.cc)}\n")
        hdr.append(f"Date: {_short_date(full.date)}\n")
        hdr.append(f"Subject: {full.subject}\n", style="bold cyan")
        if full.status:
            hdr.append(f"Status: {full.status}\n", style="bold green" if full.status == "Sent" else "bold red"
                       if full.status.startswith("Failed") else "bold yellow")
        for i, a in enumerate(full.attachments, 1):
            hdr.append(f"📎 [{i}] {a.filename} ({a.size} B)  — press s to save\n", style="magenta")
        hdr.append("─" * 60 + "\n")
        hdr.append(body_text(full))
        self.query_one("#body", Static).update(hdr)

    def _restyle(self, m: Message):
        t = self.query_one("#messages", DataTable)
        style = "bold" if m.unread else ""
        try:
            row = t.get_row_index(m.id)
        except Exception:
            return
        for col, val in enumerate([("●" if m.unread else " "), m.sender[:30], m.subject or "(no subject)",
                                   _short_date(m.date)]):
            t.update_cell_at((row, col), Text(val, style="yellow" if col == 0 else style))

    # --- events ---------------------------------------------------------
    def on_data_table_row_highlighted(self, e: DataTable.RowHighlighted):
        m = self.selected()
        if m:
            self.show(m)

    def on_list_view_selected(self, e: ListView.Selected):
        self.folder = e.item.name
        self.query_text = None
        self.load()
        self.query_one("#messages", DataTable).focus()

    # --- actions --------------------------------------------------------
    def _send(self, out: Optional[Outgoing]):
        if out is None:
            return
        try:
            self.backend.send(out)
            self.notify(getattr(self.backend, "send_notice", None) or "Message sent! ✨", timeout=8)
            self.load()
        except (BackendError, OSError) as e:
            self.notify(f"Send failed: {e}", severity="error")

    def action_compose(self):
        self.push_screen(ComposeScreen(), self._send)

    def _reply(self, all_: bool):
        m = self.current
        if not m:
            return
        subj = m.subject if m.subject.lower().startswith("re:") else f"Re: {m.subject}"
        cc = ""
        if all_:
            me = (self.backend.address or "").lower()
            cc = ", ".join(a for a in m.to + m.cc if me not in a.lower())
        body = f"\n\nOn {_short_date(m.date)}, {m.sender} wrote:\n{quote(body_text(m))}"
        self.push_screen(ComposeScreen(to=m.sender, cc=cc, subject=subj, body=body, in_reply_to=m.id,
                                       reply_all=all_, title="Reply all" if all_ else "Reply"), self._send)

    def action_reply(self):
        self._reply(False)

    def action_reply_all(self):
        self._reply(True)

    def action_forward(self):
        m = self.current
        if not m:
            return
        body = (f"\n\n---------- Forwarded message ----------\nFrom: {m.sender}\nDate: {m.date}\n"
                f"Subject: {m.subject}\nTo: {', '.join(m.to)}\n\n{body_text(m)}")
        self.push_screen(ComposeScreen(subject=f"Fwd: {m.subject}", body=body, title="Forward"), self._send)

    def action_delete(self):
        m = self.selected()
        if not m:
            return
        try:
            self.backend.delete(m.id)
            self.notify("Moved to Trash")
        except BackendError as e:
            self.notify(str(e), severity="error")
        self.load()

    def action_toggle_read(self):
        m = self.selected()
        if not m or m.status:
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
