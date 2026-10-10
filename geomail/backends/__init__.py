from __future__ import annotations
import os
from ..config import get_secret
from .base import Backend, BackendError


def make_backend(name: str, settings: dict) -> Backend:
    kind = settings.get("backend", "demo")
    if kind == "demo":
        from .demo import DemoBackend
        default = os.path.join(os.path.expanduser("~"), ".local", "share", "geomail", "demo.json")
        return DemoBackend(state_path=os.path.expanduser(settings.get("state_path", default)))
    if kind in ("grokbot", "maildir"):
        from .maildir import MaildirMirrorBackend
        from ..config import ADDRESS_ENV, mirror_path_for
        address = settings.get("address") or os.environ.get(ADDRESS_ENV, "")
        path = settings.get("path") or (str(mirror_path_for(address)) if address else "")
        if not path:
            raise BackendError("grokbot backend needs your bot address: run "
                               "`geocities-mail init --grokbot-address you@mail.grokbot.com` "
                               f"or set {ADDRESS_ENV}.")
        return MaildirMirrorBackend(path=os.path.expanduser(path), address=address,
                                    sender_bot=settings.get("sender_bot", "your mail bot"))
    if kind == "imap":
        from .imap import ImapBackend
        pw = get_secret(name, settings.get("password_env", "GEOMAIL_IMAP_PASSWORD"))
        if not pw:
            raise BackendError("No IMAP password: set GEOMAIL_IMAP_PASSWORD (or password_env) or store it in the OS keyring.")
        return ImapBackend(password=pw, **{k: v for k, v in settings.items() if k not in ("backend", "password_env")})
    if kind == "agentmail":
        from .agentmail import AgentMailBackend
        key = get_secret(name, settings.get("api_key_env", "AGENTMAIL_API_KEY"), key="api_key")
        if not key:
            raise BackendError("No AgentMail key: set AGENTMAIL_API_KEY or store it in the OS keyring.")
        return AgentMailBackend(api_key=key, inbox_id=settings.get("inbox_id"),
                                base_url=settings.get("base_url", "https://api.agentmail.to"))
    raise BackendError(f"Unknown backend '{kind}'")

__all__ = ["Backend", "BackendError", "make_backend"]
