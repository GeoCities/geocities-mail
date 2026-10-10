# ★ GeoCities Mail ★

A traditional email inbox that runs in a terminal, with a retro GeoCities look. It's built so an AI agent (like a Grok Bot) can install it on its own computer and open a full inbox you can drive with your keyboard. It also has a scriptable CLI with JSON output, so the bot can read and send mail itself.

```
┌ Folders ─┐┌ INBOX (3) ─────────────────────────────────────────────┐
│📁 INBOX  ││● Webmaster        Welcome to your new homepage…  10-10 │
│📁 Sent   ││● Dancing Baby     Ooga chaka ooga ooga           10-09 │
│📁 Trash  ││  Hit Counter      You reached 1,000 visitors     10-08 │
└──────────┘└────────────────────────────────────────────────────────┘
```

## Features

- Full-screen TUI built with [Textual](https://textual.textualize.io): folder list, message list (unread in bold), and a reading pane. HTML mail is converted to plain text.
- **Works by touch or mouse, not just the keyboard:** a **✎ Compose** button sits above the folders, and the reading pane has a toolbar (Reply, Reply all, Forward, Star, Edit, Reschedule, Save attachment, Delete, Refresh). Every dialog has buttons, so you can drive it from a phone that's viewing the bot's desktop.
- Folders: **INBOX, ⭐ Starred, Drafts, Scheduled, Outbox, Sent, Trash**.
- Compose, reply, reply-all, forward, trash, read/unread toggle, search, refresh. You can write in the form or hand off to `$EDITOR`.
- **Stars:** tap ☆ Star (or press `s`). Starred messages show a ⭐ in the list and appear in the ⭐ Starred folder, which collects them from every folder.
- **Drafts:** use 💾 Save draft (or `Ctrl+D`) in the compose window. Tap a draft to reopen it, then send or schedule it, and it leaves Drafts. If you cancel a message with content, it asks whether to save or discard it.
- **Scheduling:** tap ⏰ Schedule, then pick *In 1 hour*, *Tomorrow 8 AM*, *Monday 8 AM*, or type a local date and time. Scheduled mail sits in **Scheduled** with its send time, and you can open, edit, reschedule or cancel it (see [Scheduling](#scheduling)).
- Attachments are listed and can be saved, and you can attach files when composing.
- Pluggable backends:
  - **demo**: sample mail, zero setup. Sending only stores the message locally and never leaves the machine.
  - **imap**: generic IMAP and SMTP (Gmail or Outlook app passwords, Fastmail, iCloud, Proton Bridge, and so on).
  - **grokbot** (alias `maildir`): a Grok Bot native inbox through a shared Maildir mirror and an approval-gated outbox (see *Set up with your Grok Bot*).
  - **agentmail**: the [AgentMail](https://docs.agentmail.to) REST API, which gives AI agents real inboxes.
- A non-interactive CLI (`list`, `read`, `send` with `--draft`/`--at`, `delete`, `mark`, `star`, `scheduler`, `save-attachment`, `folders`) with `--json` output for bots.
- Secrets come only from environment variables or the OS keyring. They are never written to config.

## Install

The command is `geocities-mail`. `geomail` still works as an alias.


```bash
pipx install git+https://github.com/GeoCities/geocities-mail
# or, from a clone:
pip install -e .            # add ".[keyring]" for OS keyring support, ".[test]" for tests
```

## Quickstart (demo, no setup)

```bash
geocities-mail            # opens the TUI with sample mail
geocities-mail list       # CLI
geocities-mail read d2
```

Demo state is saved to `~/.local/share/geomail/demo.json`. Delete that file to reset.

## Configuration

Run `geocities-mail init` to write `~/.config/geomail/config.toml`:

```toml
default = "mymail"

[accounts.demo]
backend = "demo"

[accounts.mymail]
backend = "imap"
imap_host = "imap.example.com"
imap_port = 993
smtp_host = "smtp.example.com"
smtp_port = 465                 # 465 = SSL, 587 = STARTTLS
username = "you@example.com"
from = "You <you@example.com>"
password_env = "GEOMAIL_IMAP_PASSWORD"
# trash_folder = "[Gmail]/Trash"   # auto-detected via \Trash if omitted
# sent_folder  = "Sent"             # auto-detected via \Sent if omitted

[accounts.agent]
backend = "agentmail"
inbox_id = "mybot@agentmail.to"  # omit to use your first inbox
api_key_env = "AGENTMAIL_API_KEY"
# base_url = "https://api.agentmail.eu"   # EU region
```

Pick an account with `geocities-mail -A agent …` or `GEOMAIL_ACCOUNT=agent`.

### Secrets

| Backend   | Env var (default)         | Keyring (service `geocities-mail`)  |
|-----------|---------------------------|------------------------------|
| imap      | `GEOMAIL_IMAP_PASSWORD`   | user `<account>:password`    |
| agentmail | `AGENTMAIL_API_KEY`       | user `<account>:api_key`     |

```bash
python -m keyring set geocities-mail mymail:password
```

**IMAP notes:** Gmail and Outlook need an *app password* (2FA on). Fastmail uses `imap.fastmail.com` and `smtp.fastmail.com` with an app password. Message IDs look like `INBOX/1234` (folder/UID).

**AgentMail notes:** get a key at console.agentmail.to. Folders map to AgentMail labels (INBOX → `received`, Sent → `sent`), read state uses the `unread` label, and replies use the `/reply` endpoint so threading is preserved. Message IDs are AgentMail `message_id`s.

## Keybindings

| Key | Action | Key | Action |
|-----|--------|-----|--------|
| `c` | compose (✎ Compose button) | `d` | delete / discard draft / cancel scheduled |
| `r` | reply | `u` | toggle read/unread |
| `a` | reply all | `/` | search (empty clears) |
| `f` | forward | `g` | refresh (also releases due scheduled mail) |
| `s` | star / unstar | `x` | save attachment (`<n> [path]`) |
| `e` / Enter | edit the selected draft or scheduled message | `q` | quit |
| ↑/↓ | move between messages | Tab | switch panes |

Every key also has a button you can tap. In the compose window there are buttons for **Send**, **💾 Save draft**, **⏰ Schedule** and **Cancel**. The matching keys are `Ctrl+S` (send), `Ctrl+D` (save draft), `Ctrl+E` (open `$EDITOR`) and `Esc` (cancel; it asks to save or discard if you've typed something).

## Scheduling

Scheduled messages are stored one per item as `Scheduled/<id>.json`, a sidecar with the message fields and `send_at` (ISO 8601 with timezone), plus a readable `<id>.eml` copy. With the Grok Bot backend that folder is `<mirror>/Scheduled/`. With the other backends it's `~/.local/share/geomail/scheduled/<account>/`.

Something has to check for due items. The running TUI checks every minute and whenever you refresh. Outside the app, run:

```bash
geocities-mail scheduler run      # releases everything that's due
geocities-mail scheduler list
geocities-mail scheduler cancel <id>
geocities-mail send --to a@example.com --subject Hi --body "…" --at "2026-10-12 08:00"   # schedule from the CLI
```

What "due" does depends on the backend:

- **Grok Bot (`grokbot`):** a due item is **only proposed, never auto-sent.** It moves into `outbox/pending/` (its sidecar has `scheduled_for`), and your mail bot asks you to approve it like any other outbox item. Have the mail bot's sync routine run `geocities-mail scheduler run` on every sync.
- **IMAP and AgentMail:** a due item is **sent** by `scheduler run` or by the running TUI. GeoCities Mail, or a scheduled job such as cron running `geocities-mail scheduler run`, has to be running at the send time, or the message goes out the next time one runs. If a send fails, the item stays in Scheduled with the error and is retried on the next run.

## Stars and drafts by backend

| Backend | Star | Drafts |
|---|---|---|
| grokbot / maildir | Maildir `F` (flagged) flag | `Drafts/` Maildir, files flagged `D` |
| imap | `\Flagged` | `APPEND` to the `\Drafts` mailbox (or `drafts_folder` in config) |
| agentmail | `starred` label | AgentMail Drafts API |
| demo | stored locally | stored locally |

## CLI (for bots and scripts)

```bash
geocities-mail --json list [--folder INBOX] [--limit 20] [--search text] [--unread]
geocities-mail --json read <id> [--keep-unread]
geocities-mail --json send --to a@x.com[,b@y.com] --subject "Hi" --body "Text" [--cc ..] [--attach file] [--reply-to <id> [--reply-all]]
echo "body from stdin" | geocities-mail send --to a@x.com --subject Hi
geocities-mail --json delete <id>
geocities-mail --json mark <id> read|unread
geocities-mail --json save-attachment <id> <attachment_id> -o ./downloads
geocities-mail --json folders
```

The exit code is non-zero on errors. With `--json`, errors print as `{"error": "..."}`.

## Set up with your Grok Bot

Any Grok Bot user can do this. You need two things: the bot email address Grok Bot gave you (something like `yourbot@mail.grokbot.com`), and a bot that can read that inbox.

1. **Install it on the bot's computer.** Ask any of your bots to run:
   ```bash
   pipx install git+https://github.com/GeoCities/geocities-mail
   ```
2. **Point it at your inbox.** Use your own address:
   ```bash
   geocities-mail init --grokbot-address yourbot@mail.grokbot.com
   ```
   This writes `~/.config/geomail/config.toml` and creates an empty mirror folder at `/workspace/grokbot-mail/yourbot/` (the part before the `@` becomes the folder name). You can also skip `init` and set `GEOCITIES_MAIL_ADDRESS=yourbot@mail.grokbot.com`. Add `--sender-bot "Name of your mail bot"` to make confirmations name your bot.
3. **Teach your mail bot to sync.** Paste the prompt in [Prompt for your mail bot](#prompt-for-your-mail-bot) into the bot that reads your inbox. Replace `<YOUR_ADDRESS>` and `<MIRROR_PATH>` first.
4. **Optional: make it automatic.** Ask that bot to create a routine, for example: *"Every 15 minutes, run the GeoCities Mail sync from your instructions."* The sync includes `geocities-mail scheduler run`, so scheduled mail is offered to you for approval close to its send time.
5. **Open it.** Tell a bot: *"Run GeoCities Mail in a terminal on your desktop so I can use it."* It runs `geocities-mail` in a visible terminal, and you read and write mail with the keyboard.

If there's no config file, GeoCities Mail picks a backend in this order: `GEOCITIES_MAIL_ADDRESS`, then a single synced mirror under `/workspace/grokbot-mail/*/` (one with a `manifest.json`), then the demo inbox.

## Using with a Grok Bot inbox

Grok Bot's own inboxes (`@mail.grokbot.com`) don't have a public API that code can connect to. So GeoCities Mail works with them through a shared folder on the bot's computer:

1. **Your mail bot copies mail into a folder on a schedule.** The bot that can read the inbox saves each new email as a file in the mirror folder.
2. **GeoCities Mail shows it.** Open `geocities-mail` and you get a normal inbox. The header says when the bot last synced. Marking read or unread and deleting only change your local copy, not the real mailbox.
3. **Sending goes to an Outbox.** When you write, reply or forward, GeoCities Mail doesn't send anything itself. It puts the message in **Outbox** with status *Waiting for approval*. On its next sync, the mail bot shows you each queued message. It sends only the ones you approve, and the status changes to **Sent** or **Failed**.

Config written by `init` (edit it if you like):

```toml
default = "grokbot"

[accounts.grokbot]
backend = "grokbot"                       # alias: "maildir"
address = "yourbot@mail.grokbot.com"      # or env GEOCITIES_MAIL_ADDRESS
path = "/workspace/grokbot-mail/yourbot"  # default: /workspace/grokbot-mail/<inbox-name>
sender_bot = "your mail bot"              # name shown in the send confirmation
```

### Mirror spec (v1)

```
<mirror>/                      default /workspace/grokbot-mail/<inbox-name>/
  manifest.json                {"version":1,"account":"yourbot@mail.grokbot.com","last_synced":"2026-10-10T19:30:00Z","synced_by":"Mail Bot"}
  INBOX/{tmp,new,cur}/         standard Maildir, one RFC 822 .eml file per message
  Sent/{tmp,new,cur}/          (optional) copies of mail already sent
  Trash/{tmp,new,cur}/         local-only; GeoCities Mail moves deleted mail here
  Drafts/{tmp,new,cur}/        local-only drafts (flags :2,DS)
  Scheduled/<id>.json (+.eml)  local-only scheduled mail; send_at = ISO 8601 with timezone
  outbox/pending/<id>.eml      complete message GeoCities Mail wants sent
  outbox/pending/<id>.json     sidecar (shape below); written LAST, so its presence = ready
  outbox/sent/<id>.{eml,json}  bot moved it here after sending
  outbox/failed/<id>.{eml,json,reason.txt}   bot moved it here; reason.txt = one-line reason
```

- **File naming:** a Maildir unique name. `geocities-mail mirror-import` uses `gm-<sha1(Message-ID)[:20]>`, so importing the same message twice does nothing. Write the file to `tmp/`, then rename it into `new/`.
- **Read/unread:** standard Maildir flags. A file in `new/`, or in `cur/` without `S` in its `:2,<flags>` suffix, is unread. `cur/<name>:2,S` is read, and `F` means starred (for example `:2,FS`). GeoCities Mail renames files to change flags. The bot should **not** reset flags or re-add messages that are already mirrored (that includes anything in `Trash/`).
- **manifest.json:** the bot updates `last_synced` (ISO 8601, UTC) at the end of each sync. The TUI header shows "Last synced …".

**Import JSON shape** (`geocities-mail mirror-import msg.json`, one object or a list):

```json
{
  "folder": "INBOX",
  "message_id": "<abc@mail.example>",
  "from": "Alice <alice@example.com>",
  "to": ["yourbot@mail.grokbot.com"],
  "cc": [],
  "subject": "Hello",
  "date": "2026-10-10T12:00:00Z",
  "text": "plain body",
  "html": "<p>optional html</p>",
  "in_reply_to": "<parent@mail.example>",
  "unread": true,
  "attachments": [{"filename": "a.pdf", "path": "/path/a.pdf"},
                  {"filename": "b.txt", "content_base64": "aGk=", "content_type": "text/plain"}]
}
```

**Outbox sidecar shape** (`outbox/*/<id>.json`, also what `geocities-mail outbox list --json` returns, plus `eml_path`, `json_path` and `reason`):

```json
{
  "id": "20261010T153000-1a2b3c4d", "status": "pending", "created_at": "2026-10-10T19:30:00+00:00",
  "from": "yourbot@mail.grokbot.com", "to": ["alice@example.com"], "cc": [], "bcc": [],
  "subject": "Re: Hello", "body": "Thanks!",
  "in_reply_to": "INBOX/gm-…", "in_reply_to_message_id": "<abc@mail.example>", "reply_all": false,
  "message_id": "<…@host>", "attachments": ["/abs/path/file.pdf"], "eml": "20261010T153000-1a2b3c4d.eml",
  "scheduled_for": null
}
```

**Bot helper commands**

```bash
geocities-mail -A grokbot --json mirror-import new1.eml new2.json   # imports + updates last_synced
cat msgs.json | geocities-mail -A grokbot --json mirror-import -     # JSON on stdin
geocities-mail -A grokbot --json mirror-touch --synced-by "Mail Bot"
geocities-mail -A grokbot --json scheduler run                # due scheduled mail -> outbox/pending (approval still required)
geocities-mail -A grokbot --json outbox list --status pending
geocities-mail -A grokbot --json outbox mark <id> sent
geocities-mail -A grokbot --json outbox mark <id> failed --reason "User declined"
```

### Prompt for your mail bot

Replace `<YOUR_ADDRESS>` (for example `yourbot@mail.grokbot.com`) and `<MIRROR_PATH>` (for example `/workspace/grokbot-mail/yourbot`), then paste this into the bot that reads that inbox:

> You keep a local mirror of my Grok Bot inbox `<YOUR_ADDRESS>` so I can use it in the GeoCities Mail terminal app. The mirror is at `<MIRROR_PATH>`. If `geocities-mail` isn't installed, run `pipx install git+https://github.com/GeoCities/geocities-mail`, then `geocities-mail init --grokbot-address <YOUR_ADDRESS> --mirror-path <MIRROR_PATH>`.
>
> Every sync (and whenever I ask), do this:
>
> 1. **Pull new mail.** Use your email tools to find messages in `<YOUR_ADDRESS>` that arrived since `last_synced` in `<MIRROR_PATH>/manifest.json` (all of them if it's missing). For each one, write a JSON object like `{"message_id","from","to","cc","subject","date","text","html","in_reply_to","attachments":[{"filename","path"}]}` (save attachments to files first) and run `geocities-mail -A grokbot --json mirror-import <file.json>`. You can also write a full `.eml` into `<MIRROR_PATH>/INBOX/tmp/` and rename it into `<MIRROR_PATH>/INBOX/new/`. Importing is idempotent, so re-importing is safe. Never delete or modify files that are already in the mirror, and never reset their read flags.
> 2. **Update the manifest.** Run `geocities-mail -A grokbot --json mirror-touch --synced-by "<your bot name>" --account-address <YOUR_ADDRESS>` (import already does this, but run it even when nothing is new).
> 3. **Release scheduled mail.** Run `geocities-mail -A grokbot --json scheduler run`. This only moves messages whose scheduled time has passed into the outbox. Scheduled messages are **proposed for approval at their time, never sent automatically**.
> 4. **Process the outbox.** Run `geocities-mail -A grokbot --json outbox list --status pending`. For each item, show me a draft for approval with the exact From, To, Cc, Bcc, Subject, body and attachments, and say whether it's a reply (use `in_reply_to_message_id` to reply in the original thread). If the item has `scheduled_for`, mention that it was scheduled for that time. **Never send without my explicit approval of that specific message.** Don't treat approval for one message as approval for others, and don't send a declined message another way.
> 5. **Record the result.** After you send an approved message, run `geocities-mail -A grokbot outbox mark <id> sent`. If I decline it or sending fails, run `geocities-mail -A grokbot outbox mark <id> failed --reason "<short reason>"`. Leave items I haven't decided on in `pending`.
> 6. Give me a one-line summary, for example "3 new messages mirrored, 1 sent, 1 waiting for your approval".

When you handle mail for me in other ways, use `geocities-mail --json list/read`. **Never** run `geocities-mail send` with a real IMAP or AgentMail account without my explicit approval of the recipients, subject, and body.

## Note on Grok Bot native inboxes

Grok Bot can give a bot its own `@mail.grokbot.com` address, but there's no public IMAP or API for those inboxes that GeoCities Mail can connect to yet. Until there is, use the mirror (`grokbot`) backend above, or AgentMail/IMAP. If one is published, add a backend in `geomail/backends/`; the interface is small (`backends/base.py`).

## Development

```bash
pip install -e ".[test]"
pytest -q
```

The tests cover the demo backend, the IMAP/SMTP backend against a fake `imaplib`/`smtplib` (no network), the AgentMail backend via `httpx.MockTransport`, the Grok Bot Maildir mirror and outbox (temp dirs), the CLI, and a headless Textual pilot run of the TUI.

## License

MIT, see [LICENSE](LICENSE).
