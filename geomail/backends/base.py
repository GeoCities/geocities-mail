from __future__ import annotations
import os, re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from ..models import Message, Outgoing

STARRED, DRAFTS, SCHEDULED = "Starred", "Drafts", "Scheduled"
FOLDER_ORDER = ["INBOX", STARRED, DRAFTS, SCHEDULED, "Outbox", "Sent", "Trash"]


class BackendError(Exception):
    pass


class Backend(ABC):
    name = "base"
    address = ""
    account = "default"

    # --- required -------------------------------------------------------
    @abstractmethod
    def folders(self) -> List[str]: ...

    @abstractmethod
    def list_messages(self, folder: str = "INBOX", limit: int = 50, query: Optional[str] = None) -> List[Message]: ...

    @abstractmethod
    def get_message(self, msg_id: str) -> Message: ...

    @abstractmethod
    def set_read(self, msg_id: str, read: bool) -> None: ...

    @abstractmethod
    def delete(self, msg_id: str) -> None: ...

    @abstractmethod
    def send(self, out: Outgoing) -> str: ...

    @abstractmethod
    def get_attachment(self, msg_id: str, att_id: str) -> bytes: ...

    def close(self) -> None:
        pass

    # --- stars ----------------------------------------------------------
    star_folders = ("INBOX", "Sent")

    def set_starred(self, msg_id: str, starred: bool) -> None:
        raise BackendError("Stars are not supported by this backend")

    def list_starred(self, limit: int = 100) -> List[Message]:
        out = []
        for f in self.star_folders:
            if f in self.folders():
                out += [m for m in self.list_messages(f, 500) if m.starred]
        out.sort(key=lambda m: m.date, reverse=True)
        return out[:limit]

    # --- drafts (backends override) -------------------------------------
    def save_draft(self, out: Outgoing, draft_id: Optional[str] = None) -> str:
        raise BackendError("Drafts are not supported by this backend")

    def get_draft(self, draft_id: str) -> Outgoing:
        raise BackendError("Drafts are not supported by this backend")

    def delete_draft(self, draft_id: str) -> None:
        raise BackendError("Drafts are not supported by this backend")

    # --- scheduling (generic, file based) -------------------------------
    def scheduled_dir(self) -> Path:
        base = Path(os.path.expanduser("~/.local/share/geomail/scheduled"))
        return base / re.sub(r"[^A-Za-z0-9._@+-]", "_", f"{self.name}-{self.account}-{self.address or 'default'}")

    @property
    def schedule_store(self):
        if getattr(self, "_sched", None) is None:
            from ..schedule import ScheduleStore
            self._sched = ScheduleStore(self.scheduled_dir())
        return self._sched

    def schedule(self, out: Outgoing, send_at: datetime, sched_id: Optional[str] = None) -> str:
        if not out.to and not (out.in_reply_to and out.reply_all):
            raise BackendError("At least one recipient is required")
        frm = self.address if "@" in (self.address or "") else ""
        return "sched/" + self.schedule_store.save(out, send_at, sched_id, frm)

    def list_scheduled(self) -> List[Message]:
        return [self.schedule_store.message(d) for d in self.schedule_store.items()]

    def cancel_scheduled(self, msg_id: str) -> None:
        self.schedule_store.remove(msg_id.split("/", 1)[1])

    def run_due(self, now: Optional[datetime] = None) -> List[dict]:
        """Release due scheduled items through send() (grokbot: queues for approval, never sends)."""
        results = []
        for d in self.schedule_store.due(now):
            out = self.schedule_store.outgoing(d)
            out.scheduled_for = d["send_at"]
            try:
                res = self.send(out)
                self.schedule_store.remove(d["id"])
                results.append({"id": d["id"], "ok": True, "result": res, "send_at": d["send_at"]})
            except Exception as e:  # keep it scheduled; retry next run
                self.schedule_store.set_error(d["id"], str(e))
                results.append({"id": d["id"], "ok": False, "error": str(e), "send_at": d["send_at"]})
        return results

    # --- unified helpers used by the TUI and CLI ------------------------
    def all_folders(self) -> List[str]:
        native = self.folders()
        out = [f for f in FOLDER_ORDER if f in native or f in (STARRED, DRAFTS, SCHEDULED)]
        if not self.supports_drafts:
            out.remove(DRAFTS)
        return out + [f for f in native if f not in out and f not in self.hidden_folders()]

    def hidden_folders(self) -> List[str]:
        return []

    @property
    def supports_drafts(self) -> bool:
        return type(self).save_draft is not Backend.save_draft

    def list_folder(self, folder: str, limit: int = 50, query: Optional[str] = None) -> List[Message]:
        if query:
            return self.list_messages(folder, limit, query)
        if folder == STARRED:
            return self.list_starred(limit)
        if folder == SCHEDULED:
            return self.list_scheduled()[:limit]
        return self.list_messages(folder, limit)

    def open_message(self, msg_id: str) -> Message:
        if msg_id.startswith("sched/"):
            return self.schedule_store.message(self.schedule_store.get(msg_id.split("/", 1)[1]))
        return self.get_message(msg_id)

    def get_outgoing(self, m: Message) -> Outgoing:
        if m.id.startswith("sched/"):
            return self.schedule_store.outgoing(self.schedule_store.get(m.id.split("/", 1)[1]))
        return self.get_draft(m.id)

    def remove(self, m: Message) -> str:
        """Delete/cancel whatever kind of item this is. Returns a short description."""
        if m.id.startswith("sched/"):
            self.cancel_scheduled(m.id)
            return "Scheduled message cancelled"
        if m.folder == DRAFTS:
            self.delete_draft(m.id)
            return "Draft discarded"
        self.delete(m.id)
        return "Moved to Trash"
