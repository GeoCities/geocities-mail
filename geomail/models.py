from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class Attachment:
    id: str
    filename: str
    size: int = 0
    content_type: str = "application/octet-stream"


@dataclass
class Message:
    id: str
    folder: str
    sender: str
    to: List[str]
    subject: str
    date: str  # ISO 8601
    unread: bool = False
    cc: List[str] = field(default_factory=list)
    text: str = ""
    html: str = ""
    preview: str = ""
    message_id_header: str = ""
    attachments: List[Attachment] = field(default_factory=list)
    status: str = ""  # e.g. outbox status: "Sending", "Waiting for approval", "Sent", "Failed: ...", "Scheduled for ..."
    starred: bool = False
    send_at: str = ""  # scheduled items: ISO 8601 with timezone

    def to_dict(self, full: bool = True) -> dict:
        d = asdict(self)
        if not full:
            for k in ("text", "html"):
                d.pop(k, None)
        return d


@dataclass
class Outgoing:
    to: List[str]
    subject: str
    body: str
    cc: List[str] = field(default_factory=list)
    bcc: List[str] = field(default_factory=list)
    in_reply_to: Optional[str] = None  # backend message id being answered
    reply_all: bool = False
    attachments: List[str] = field(default_factory=list)  # local file paths
    scheduled_for: Optional[str] = None  # set when a scheduled item is released

    def is_empty(self) -> bool:
        return not (self.to or self.cc or self.bcc or self.subject.strip() or self.body.strip() or self.attachments)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Outgoing":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in keys})
