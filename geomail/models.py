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
    status: str = ""  # e.g. outbox status: "Waiting for approval", "Sent", "Failed: ..."

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
