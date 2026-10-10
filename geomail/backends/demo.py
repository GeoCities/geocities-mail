"""Zero-setup demo backend. Sending never leaves the machine; it is stored in the Sent folder."""
from __future__ import annotations
import base64, json, uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from .base import Backend, BackendError
from ..models import Attachment, Message, Outgoing

ME = "you@geocities.demo"

_PIXEL = base64.b64encode(b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;").decode()


def _seed() -> dict:
    msgs = [
        dict(id="d1", folder="INBOX", sender="Webmaster <webmaster@geocities.demo>", to=[ME],
             subject="Welcome to your new homepage neighborhood!", date="2026-10-10T09:15:00+00:00", unread=True,
             text="Howdy neighbor!\n\nYour homestead in SiliconValley/Heights is ready.\nDon't forget to sign the guestbook.\n\n-- The Webmaster",
             attachments=[dict(id="a1", filename="under_construction.gif", size=43, content_type="image/gif")]),
        dict(id="d2", folder="INBOX", sender="Dancing Baby <baby@ugachaka.demo>", to=[ME], cc=["friends@geocities.demo"],
             subject="Ooga chaka ooga ooga", date="2026-10-09T18:02:00+00:00", unread=True,
             html="<h1>Hot new meme</h1><p>Check out my <b>3D dancing baby</b>! <a href='http://example.com'>click here</a></p>"),
        dict(id="d3", folder="INBOX", sender="Hit Counter <counter@geocities.demo>", to=[ME],
             subject="You reached 1,000 visitors", date="2026-10-08T12:00:00+00:00", unread=False,
             text="Congratulations! Your hit counter just rolled over 001000."),
        dict(id="d4", folder="Sent", sender=ME, to=["webmaster@geocities.demo"], subject="Re: guestbook spam",
             date="2026-10-07T08:30:00+00:00", text="Thanks for cleaning it up!"),
    ]
    return {"messages": msgs, "blobs": {"d1:a1": _PIXEL}}


class DemoBackend(Backend):
    name = "demo"
    address = ME

    def __init__(self, state_path: Optional[str] = None):
        self.state_path = Path(state_path) if state_path else None
        if self.state_path and self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        else:
            self.state = _seed()

    def _save(self):
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.state, indent=1))

    def _msg(self, d: dict) -> Message:
        d = dict(d)
        d["attachments"] = [Attachment(**a) for a in d.get("attachments", [])]
        return Message(**d)

    def _find(self, msg_id: str) -> dict:
        for d in self.state["messages"]:
            if d["id"] == msg_id:
                return d
        raise BackendError(f"No such message: {msg_id}")

    def folders(self) -> List[str]:
        return ["INBOX", "Sent", "Trash"]

    def list_messages(self, folder="INBOX", limit=50, query=None) -> List[Message]:
        out = []
        for d in self.state["messages"]:
            if query:
                hay = " ".join([d.get("sender", ""), d.get("subject", ""), d.get("text", ""), d.get("html", "")]).lower()
                if query.lower() not in hay or d["folder"] == "Trash":
                    continue
            elif d["folder"] != folder:
                continue
            out.append(self._msg(d))
        out.sort(key=lambda m: m.date, reverse=True)
        return out[:limit]

    def get_message(self, msg_id):
        return self._msg(self._find(msg_id))

    def set_read(self, msg_id, read):
        self._find(msg_id)["unread"] = not read
        self._save()

    def delete(self, msg_id):
        d = self._find(msg_id)
        if d["folder"] == "Trash":
            self.state["messages"].remove(d)
        else:
            d["folder"] = "Trash"
        self._save()

    def send(self, out: Outgoing) -> str:
        if not out.to:
            raise BackendError("At least one recipient is required")
        mid = "s" + uuid.uuid4().hex[:8]
        atts = []
        for i, p in enumerate(out.attachments):
            data = Path(p).read_bytes()
            aid = f"x{i}"
            self.state["blobs"][f"{mid}:{aid}"] = base64.b64encode(data).decode()
            atts.append(dict(id=aid, filename=Path(p).name, size=len(data)))
        self.state["messages"].append(dict(
            id=mid, folder="Sent", sender=ME, to=list(out.to), cc=list(out.cc), subject=out.subject,
            date=datetime.now(timezone.utc).isoformat(timespec="seconds"), text=out.body, attachments=atts))
        self._save()
        return mid

    def get_attachment(self, msg_id, att_id) -> bytes:
        blob = self.state["blobs"].get(f"{msg_id}:{att_id}")
        if blob is None:
            raise BackendError("No such attachment")
        return base64.b64decode(blob)
