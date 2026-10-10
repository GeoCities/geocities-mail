"""Config lives in ~/.config/geomail/config.toml. Secrets come ONLY from env vars or the OS keyring."""
from __future__ import annotations
import os
import re
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

CONFIG_DIR = Path(os.environ.get("GEOMAIL_CONFIG_DIR", Path.home() / ".config" / "geomail"))
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_CONFIG = """# geomail config. NEVER put passwords or API keys in this file.
# Secrets: env vars (GEOMAIL_IMAP_PASSWORD, AGENTMAIL_API_KEY) or OS keyring
# (service "geomail", username = the account name below).

default = "demo"

[accounts.demo]
backend = "demo"

# Grok Bot native inbox via a Maildir mirror kept up to date by your mail bot.
# (This is the default automatically when /workspace/grokbot-mail exists.)
# [accounts.grokbot]
# backend = "grokbot"            # alias: "maildir"
# address = "yourbot@mail.grokbot.com"    # or env GEOCITIES_MAIL_ADDRESS
# path = "/workspace/grokbot-mail/yourbot" # default: /workspace/grokbot-mail/<inbox-name>
# sender_bot = "your mail bot"             # name shown in the send confirmation
# require_approval = false                 # true: mail bot asks before sending each message

# [accounts.mymail]
# backend = "imap"
# imap_host = "imap.example.com"
# imap_port = 993
# smtp_host = "smtp.example.com"
# smtp_port = 465          # 465 = SSL, 587 = STARTTLS
# username = "you@example.com"
# from = "You <you@example.com>"
# password_env = "GEOMAIL_IMAP_PASSWORD"   # app password

# [accounts.agent]
# backend = "agentmail"
# inbox_id = "mybot@agentmail.to"
# api_key_env = "AGENTMAIL_API_KEY"
# base_url = "https://api.agentmail.to"
"""


GROKBOT_MAIL_ROOT = Path(os.environ.get("GEOMAIL_GROKBOT_ROOT", "/workspace/grokbot-mail"))
ADDRESS_ENV = "GEOCITIES_MAIL_ADDRESS"


def mirror_path_for(address: str) -> Path:
    """Default mirror folder for a Grok Bot address: /workspace/grokbot-mail/<inbox-name>/."""
    local = address.split("@", 1)[0].strip().lower()
    local = re.sub(r"[^a-z0-9._+-]", "_", local) or "inbox"
    return GROKBOT_MAIL_ROOT / local


def detect_mirrors() -> list:
    """Mirror folders under GROKBOT_MAIL_ROOT that a mail bot has synced (have a manifest.json)."""
    if not GROKBOT_MAIL_ROOT.is_dir():
        return []
    return sorted(d for d in GROKBOT_MAIL_ROOT.iterdir() if d.is_dir() and (d / "manifest.json").is_file())


def _auto_grokbot() -> dict | None:
    addr = os.environ.get(ADDRESS_ENV, "").strip()
    if addr:
        return {"backend": "grokbot", "address": addr, "path": str(mirror_path_for(addr))}
    found = detect_mirrors()
    if len(found) == 1:
        acc = {"backend": "grokbot", "path": str(found[0])}
        try:
            import json
            a = json.loads((found[0] / "manifest.json").read_text()).get("account")
            if a:
                acc["address"] = a
        except (OSError, ValueError):
            pass
        return acc
    return None


def load_config(path: Path | None = None) -> dict:
    path = Path(path) if path else CONFIG_PATH
    if not path.exists():
        cfg = tomllib.loads(DEFAULT_CONFIG)
        acc = _auto_grokbot()  # env address, or exactly one synced mirror on this box
        if acc:
            cfg["accounts"]["grokbot"] = acc
            cfg["default"] = "grokbot"
        return cfg
    with open(path, "rb") as f:
        return tomllib.load(f)


def init_config(path: Path | None = None, grokbot_address: str | None = None, mirror_path: str | None = None,
                sender_bot: str | None = None, force: bool = False, require_approval: bool = False) -> Path:
    path = Path(path) if path else CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        if grokbot_address:
            raise FileExistsError(f"{path} already exists; edit it or re-run with --force to overwrite")
        return path
    text = DEFAULT_CONFIG
    if grokbot_address:
        if "@" not in grokbot_address:
            raise ValueError("Grok Bot address must look like you@mail.grokbot.com")
        mp = mirror_path or str(mirror_path_for(grokbot_address))
        text = text.replace('default = "demo"', 'default = "grokbot"', 1)
        text += (f'\n[accounts.grokbot]\nbackend = "grokbot"\naddress = "{grokbot_address}"\n'
                 f'path = "{mp}"\nsender_bot = "{sender_bot or "your mail bot"}"\n'
                 f'require_approval = {"true" if require_approval else "false"}\n')
    path.write_text(text)
    return path


def get_secret(account: str, env_name: str | None, key: str = "password") -> str | None:
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    try:
        import keyring  # optional
        return keyring.get_password("geomail", f"{account}:{key}") or keyring.get_password("geomail", account)
    except Exception:
        return None


def account_settings(cfg: dict, name: str | None) -> tuple[str, dict]:
    accounts = cfg.get("accounts") or {"demo": {"backend": "demo"}}
    name = name or os.environ.get("GEOMAIL_ACCOUNT") or cfg.get("default") or next(iter(accounts))
    if name not in accounts:
        raise SystemExit(f"geomail: unknown account '{name}'. Known: {', '.join(accounts)}")
    return name, accounts[name]
