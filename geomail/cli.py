"""geomail command line. With no subcommand it launches the full-screen TUI."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from .backends import BackendError, make_backend
from .config import account_settings, init_config, load_config, CONFIG_PATH
from .htmltext import body_text
from .models import Outgoing


def _backend(args):
    cfg = load_config(args.config)
    name, settings = account_settings(cfg, args.account)
    return make_backend(name, settings)


def _split(v):
    return [a.strip() for a in (v or "").split(",") if a.strip()]


def main(argv=None):
    p = argparse.ArgumentParser(prog="geocities-mail", description="Retro terminal email inbox. Run with no command for the TUI.")
    p.add_argument("--account", "-A", help="account name from config.toml (default: config 'default')")
    p.add_argument("--config", help=f"config path (default {CONFIG_PATH})")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("tui", help="launch the full-screen inbox (default)")
    inp = sub.add_parser("init", help="write a starter config.toml")
    inp.add_argument("--grokbot-address", help="your bot's own address, e.g. you@mail.grokbot.com")
    inp.add_argument("--mirror-path", help="mirror folder (default /workspace/grokbot-mail/<inbox-name>)")
    inp.add_argument("--sender-bot", help="name of the bot that delivers your mail (default 'your mail bot')")
    inp.add_argument("--require-approval", action="store_true",
                     help="grokbot: mail bot asks you before sending each message (default: Send means send)")
    inp.add_argument("--force", action="store_true", help="overwrite an existing config")
    sub.add_parser("folders", help="list folders")
    lp = sub.add_parser("list", help="list messages")
    lp.add_argument("--folder", "-f", default="INBOX")
    lp.add_argument("--limit", "-n", type=int, default=20)
    lp.add_argument("--search", "-s")
    lp.add_argument("--unread", action="store_true")
    rp = sub.add_parser("read", help="print a message")
    rp.add_argument("id")
    rp.add_argument("--keep-unread", action="store_true")
    sp = sub.add_parser("send", help="send a message")
    sp.add_argument("--to", default="", help="comma-separated")
    sp.add_argument("--cc", default="")
    sp.add_argument("--bcc", default="")
    sp.add_argument("--subject", default="")
    sp.add_argument("--body", help="body text; '-' or omitted reads stdin")
    sp.add_argument("--attach", action="append", default=[])
    sp.add_argument("--reply-to", dest="reply_to", help="message id being replied to")
    sp.add_argument("--reply-all", action="store_true")
    sp.add_argument("--at", help="schedule instead of sending now: 'YYYY-MM-DD HH:MM' (local) or ISO 8601")
    sp.add_argument("--draft", action="store_true", help="save as a draft instead of sending")
    stp = sub.add_parser("star", help="star (or --off to unstar) a message")
    stp.add_argument("id")
    stp.add_argument("--off", action="store_true")
    scp = sub.add_parser("scheduler", help="scheduled messages")
    ssub = scp.add_subparsers(dest="sched_cmd", required=True)
    ssub.add_parser("run", help="release due items (grokbot: move to outbox/pending for the mail bot; others: send)")
    ssub.add_parser("list", help="list scheduled items")
    sc = ssub.add_parser("cancel", help="cancel a scheduled item")
    sc.add_argument("id")
    dp = sub.add_parser("delete", help="move message to trash")
    dp.add_argument("id")
    mp = sub.add_parser("mark", help="mark read/unread")
    mp.add_argument("id")
    mp.add_argument("state", choices=["read", "unread"])
    ap = sub.add_parser("save-attachment", help="save an attachment to disk")
    ap.add_argument("id")
    ap.add_argument("attachment_id")
    ap.add_argument("--out", "-o", default=".")
    ip = sub.add_parser("mirror-import", help="(grokbot backend) import .eml or .json message(s) into the mirror")
    ip.add_argument("files", nargs="+", help=".eml files, or .json files (one object or a list); '-' = JSON on stdin")
    ip.add_argument("--folder", default=None, help="INBOX (default) or Sent")
    ip.add_argument("--read", action="store_true", help="import as already read")
    ip.add_argument("--no-touch", action="store_true", help="don't update manifest.json last_synced")
    tp = sub.add_parser("mirror-touch", help="(grokbot backend) set manifest.json last_synced to now")
    tp.add_argument("--synced-by", default="")
    tp.add_argument("--account-address", default="")
    op = sub.add_parser("outbox", help="(grokbot backend) inspect/resolve queued outgoing mail")
    osub = op.add_subparsers(dest="outbox_cmd", required=True)
    ol = osub.add_parser("list")
    ol.add_argument("--status", choices=["pending", "sent", "failed"])
    om = osub.add_parser("mark")
    om.add_argument("id")
    om.add_argument("state", choices=["sent", "failed"])
    om.add_argument("--reason", default="")
    args = p.parse_args(argv)

    if args.cmd == "init":
        try:
            path = init_config(Path(args.config) if args.config else None, args.grokbot_address,
                               args.mirror_path, args.sender_bot, args.force, args.require_approval)
        except (FileExistsError, ValueError) as e:
            print(json.dumps({"error": str(e)}) if args.json else f"geocities-mail: {e}", file=sys.stdout if args.json else sys.stderr)
            return 1
        if args.grokbot_address:
            from .backends.maildir import ensure_layout
            from .config import load_config as _lc
            mp = _lc(path)["accounts"]["grokbot"]["path"]
            ensure_layout(mp)
        print(json.dumps({"config": str(path)}) if args.json else f"Config at {path}")
        return 0
    if args.cmd in (None, "tui"):
        from .tui import run_tui
        cfg = load_config(args.config)
        name, settings = account_settings(cfg, args.account)
        try:
            backend = make_backend(name, settings)
        except BackendError as e:
            print(f"geocities-mail: {e}", file=sys.stderr)
            return 1
        run_tui(backend)
        return 0

    out = sys.stdout
    try:
        b = _backend(args)
        if args.cmd == "folders":
            f = b.all_folders()
            print(json.dumps(f) if args.json else "\n".join(f))
        elif args.cmd == "list":
            msgs = b.list_folder(args.folder, args.limit, args.search)
            if args.unread:
                msgs = [m for m in msgs if m.unread]
            if args.json:
                print(json.dumps([m.to_dict(full=False) for m in msgs], indent=1))
            else:
                for m in msgs:
                    print(f"{'*' if m.unread else ' '} {m.id:<14} {m.date[:16]:<16} {m.sender[:28]:<28} {m.subject}")
        elif args.cmd == "read":
            m = b.open_message(args.id)
            if not args.keep_unread and m.unread:
                b.set_read(m.id, True)
            if args.json:
                d = m.to_dict()
                d["body"] = body_text(m)
                print(json.dumps(d, indent=1))
            else:
                print(f"From: {m.sender}\nTo: {', '.join(m.to)}")
                if m.cc:
                    print(f"Cc: {', '.join(m.cc)}")
                print(f"Date: {m.date}\nSubject: {m.subject}")
                for a in m.attachments:
                    print(f"Attachment [{a.id}]: {a.filename} ({a.size} bytes)")
                print("\n" + body_text(m))
        elif args.cmd == "send":
            body = args.body
            if body is None or body == "-":
                body = sys.stdin.read()
            out = Outgoing(to=_split(args.to), cc=_split(args.cc), bcc=_split(args.bcc), subject=args.subject,
                           body=body, attachments=args.attach, in_reply_to=args.reply_to, reply_all=args.reply_all)
            if args.draft:
                did = b.save_draft(out)
                print(json.dumps({"draft": True, "id": did}) if args.json else f"Draft saved ({did})")
                return 0
            if args.at:
                from .schedule import parse_when, fmt_local
                try:
                    when = parse_when(args.at)
                except ValueError:
                    raise BackendError("--at must be 'YYYY-MM-DD HH:MM' or ISO 8601")
                sid = b.schedule(out, when)
                print(json.dumps({"scheduled": True, "id": sid, "send_at": when.isoformat(timespec="seconds")})
                      if args.json else f"Scheduled for {fmt_local(when.isoformat())} ({sid})")
                return 0
            if not out.to and not (out.in_reply_to and out.reply_all):
                raise BackendError("--to is required")
            mid = b.send(out)
            notice = getattr(b, "send_notice", None)
            if args.json:
                d = {"sent": not notice, "id": mid}
                if notice:
                    d.update(queued=True, status="pending", notice=notice)
                print(json.dumps(d))
            else:
                print(notice + f" ({mid})" if notice else f"Sent ({mid})")
        elif args.cmd == "delete":
            what = b.remove(b.open_message(args.id))
            print(json.dumps({"deleted": args.id, "result": what}) if args.json else what)
        elif args.cmd == "star":
            b.set_starred(args.id, not args.off)
            print(json.dumps({"id": args.id, "starred": not args.off}) if args.json else
                  ("Unstarred" if args.off else "Starred"))
        elif args.cmd == "scheduler":
            if args.sched_cmd == "run":
                res = b.run_due()
                print(json.dumps({"released": res}, indent=1) if args.json else
                      (f"{sum(r['ok'] for r in res)} released, {sum(not r['ok'] for r in res)} failed" if res
                       else "Nothing due"))
            elif args.sched_cmd == "list":
                items = b.schedule_store.items()
                if args.json:
                    print(json.dumps(items, indent=1))
                else:
                    from .schedule import fmt_local
                    for d in items:
                        print(f"sched/{d['id']}  {fmt_local(d['send_at'])}  to={','.join(d.get('to') or [])}  {d.get('subject','')}")
            else:
                sid = args.id if args.id.startswith("sched/") else f"sched/{args.id}"
                b.cancel_scheduled(sid)
                print(json.dumps({"cancelled": sid}) if args.json else "Cancelled")
        elif args.cmd == "mark":
            b.set_read(args.id, args.state == "read")
            print(json.dumps({"id": args.id, "state": args.state}) if args.json else f"Marked {args.state}")
        elif args.cmd == "save-attachment":
            m = b.get_message(args.id)
            att = next((a for a in m.attachments if a.id == args.attachment_id), None)
            if not att:
                raise BackendError("No such attachment")
            dest = Path(args.out)
            if dest.is_dir():
                dest = dest / Path(att.filename).name
            dest.write_bytes(b.get_attachment(m.id, att.id))
            print(json.dumps({"saved": str(dest)}) if args.json else f"Saved {dest}")
        elif args.cmd in ("mirror-import", "mirror-touch", "outbox"):
            if not hasattr(b, "import_eml"):
                raise BackendError("This command needs the grokbot/maildir backend (use -A <account>).")
            if args.cmd == "mirror-touch":
                m = b.touch_manifest(args.synced_by, args.account_address)
                print(json.dumps(m) if args.json else b.status_line())
            elif args.cmd == "mirror-import":
                ids = []
                for f in args.files:
                    if f == "-" or f.endswith(".json"):
                        data = json.loads(sys.stdin.read() if f == "-" else Path(f).read_text())
                        for d in (data if isinstance(data, list) else [data]):
                            if args.folder:
                                d["folder"] = args.folder
                            if args.read:
                                d["unread"] = False
                            ids.append(b.import_json(d))
                    else:
                        ids.append(b.import_eml(Path(f).read_bytes(), args.folder or "INBOX",
                                                False if args.read else None))
                if not args.no_touch:
                    b.touch_manifest()
                print(json.dumps({"imported": ids}) if args.json else "\n".join(ids))
            elif args.outbox_cmd == "list":
                items = b.outbox_items(args.status)
                if args.json:
                    print(json.dumps(items, indent=1))
                else:
                    for it in items:
                        print(f"{it['status']:<8} {it['id']}  to={','.join(it.get('to') or [])}  {it.get('subject','')}")
            else:
                b.mark_outbox(args.id, args.state, args.reason)
                print(json.dumps({"id": args.id, "status": args.state}) if args.json else f"{args.id} -> {args.state}")
        b.close()
    except BackendError as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"geocities-mail: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
