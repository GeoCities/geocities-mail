"""Scheduled messages: one <id>.json sidecar (+ <id>.eml copy) per item in a Scheduled/ folder."""
from __future__ import annotations
import json, os, time, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from .models import Message, Outgoing


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_when(s: str) -> datetime:
    """'YYYY-MM-DD HH:MM' (local time) or any ISO 8601 string. Returns an aware datetime."""
    s = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s.replace(" ", "T", 1) if "T" not in s and " " in s else s)
    return dt if dt.tzinfo else dt.astimezone()


def preset(name: str, now: Optional[datetime] = None) -> datetime:
    now = now or now_local()
    if name == "1h":
        return (now + timedelta(hours=1)).replace(second=0, microsecond=0)
    if name == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    if name == "monday":
        days = (7 - now.weekday()) % 7 or 7
        return (now + timedelta(days=days)).replace(hour=8, minute=0, second=0, microsecond=0)
    raise ValueError(name)


def fmt_local(iso: str) -> str:
    try:
        return parse_when(iso).astimezone().strftime("%a %b %d, %I:%M %p %Z").replace(" 0", " ")
    except ValueError:
        return iso


class ScheduleStore:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sid: str) -> Path:
        if "/" in sid or sid.startswith("."):
            raise ValueError("bad id")
        return self.dir / f"{sid}.json"

    def save(self, out: Outgoing, send_at: datetime, sid: Optional[str] = None, frm: str = "") -> str:
        if send_at.tzinfo is None:
            send_at = send_at.astimezone()
        sid = sid or time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        d = out.to_dict()
        d.update(id=sid, send_at=send_at.isoformat(timespec="seconds"), from_=frm,
                 created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        d["from"] = d.pop("from_")
        try:  # human-readable copy of the message (not used for sending)
            from .backends.maildir import build_eml
            (self.dir / f"{sid}.eml").write_bytes(build_eml(frm, out.to, out.subject, out.body, cc=out.cc))
        except Exception:
            pass
        tmp = self.dir / f".{sid}.tmp"
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, self._path(sid))
        return sid

    def get(self, sid: str) -> dict:
        return json.loads(self._path(sid).read_text())

    def items(self) -> List[dict]:
        out = []
        for f in self.dir.glob("*.json"):
            try:
                out.append(json.loads(f.read_text()))
            except ValueError:
                pass
        return sorted(out, key=lambda d: d.get("send_at", ""))

    def remove(self, sid: str):
        self._path(sid).unlink(missing_ok=True)
        (self.dir / f"{sid}.eml").unlink(missing_ok=True)

    def set_error(self, sid: str, err: str):
        d = self.get(sid)
        d["last_error"] = err
        self._path(sid).write_text(json.dumps(d, indent=1))

    def due(self, now: Optional[datetime] = None) -> List[dict]:
        now = now or now_local()
        return [d for d in self.items() if parse_when(d["send_at"]) <= now]

    @staticmethod
    def outgoing(d: dict) -> Outgoing:
        return Outgoing.from_dict(d)

    @staticmethod
    def message(d: dict) -> Message:
        status = "Scheduled for " + fmt_local(d["send_at"])
        if d.get("last_error"):
            status += f" (last attempt failed: {d['last_error']})"
        return Message(id=f"sched/{d['id']}", folder="Scheduled", sender=d.get("from", ""), to=d.get("to") or [],
                       cc=d.get("cc") or [], subject=d.get("subject", ""), date=d["send_at"], text=d.get("body", ""),
                       status=status, send_at=d["send_at"])
