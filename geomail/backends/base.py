from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional
from ..models import Message, Outgoing


class BackendError(Exception):
    pass


class Backend(ABC):
    name = "base"
    address = ""

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
