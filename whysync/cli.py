"""The `whysync` command line. Settings commands also work with the service stopped."""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from whysync import __version__, actions, config, status, trash
from whysync.daemon import AlreadyRunning, Daemon
from whysync.i18n import init_language, t, tn

HELD_SHOWN = 20


def _fmt_time(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def _fail(exc: actions.ActionError) -> int:
    print(t(exc.key, **exc.params), file=sys.stderr)
    return 1 if exc.key == "cli.needs_daemon" else 2


def _load_config() -> config.Config:
    try:
        return config.load()
    except config.ConfigError as exc:
        print(t(str(exc)), file=sys.stderr)
        raise SystemExit(2) from exc


def cmd_status(_args) -> int:
    cfg = _load_config()
    info = actions.service_info()
    print(f"whySYNC {__version__} — " + (
        t("cli.daemon_running", pid=info.get("pid")) if info else t("cli.daemon_stopped")))
    if not cfg.pairs:
        print(t("cli.no_pairs"))
        return 0
    states = actions.pair_states() if info else {}
    for pair in cfg.pairs:
        st = states.get(pair.id, {})
        state = "paused" if pair.paused else st.get("state", "unknown")
        label = t(f"state.{state}")
        if st.get("reason") and state in ("waiting", "error", "idle"):
            label += f": {t(st['reason'])}"
        print(f"\n• {pair.id}  [{label}]")
        print(f"    {pair.source} → {pair.target}")
        if last := st.get("last_sync"):
            print("    " + t("cli.last_sync", at=_fmt_time(last.get("at")), created=last.get("created", 0),
                              updated=last.get("updated", 0), deleted=last.get("deleted", 0)))
        if st.get("checked_at"):
            print("    " + t("cli.checked", at=_fmt_time(st["checked_at"])))
        if (verify := st.get("verify") or {}).get("count"):
            print("    " + tn("cli.mismatch", verify["count"], id=pair.id))
        if days := status.stale_days(pair, st):
            print("    " + tn("cli.stale", days, limit=pair.stale_days))
        if state == "held" and (held := st.get("held")):
            key = "cli.first_hint" if held.get("kind") == "first_round" else "cli.held_hint"
            print("    " + t(key, count=held.get("count", 0), id=pair.id))
        if state == "error" and st.get("error"):
            print("    " + st["error"].replace("\n", "\n    "))
    return 0


def cmd_add(args) -> int:
    try:
        pair = actions.add_pair(args.source, args.target, args.max_deletes, args.exclude)
    except actions.ActionError as exc:
        return _fail(exc)
    print(t("cli.added", id=pair.id, source=pair.source, target=pair.target))
    return 0


def cmd_remove(args) -> int:
    try:
        pair = actions.remove_pair(args.id)
    except actions.ActionError as exc:
        return _fail(exc)
    print(t("cli.removed", id=pair.id))
    return 0


def cmd_repair(args) -> int:
    if _load_config().find(args.id) is None:
        return _fail(actions.ActionError("pair.unknown", id=args.id))
    if actions.service_info() is None:
        return _fail(actions.ActionError("cli.needs_daemon"))
    verify = actions.pair_states().get(args.id, {}).get("verify") or {}
    if not verify.get("count"):
        print(t("cli.no_mismatch", id=args.id))
        return 1
    print(tn("cli.mismatch_list", verify["count"], id=args.id))
    shown = verify.get("sample", [])[:HELD_SHOWN]
    for path in shown:
        print(f"  - {path}")
    if verify["count"] > len(shown):
        print("  " + t("cli.held_more", more=verify["count"] - len(shown)))
    print(t("cli.repair_note"))
    if not args.yes and input(t("cli.confirm")).strip().lower() not in ("y", "yes", "e", "evet"):
        print(t("cli.cancelled"))
        return 1
    try:
        actions.repair(args.id)
    except actions.ActionError as exc:
        return _fail(exc)
    print(t("cli.repair_requested", id=args.id))
    return 0


def cmd_set(args) -> int:
    numbers = {name: value for name, value in (("max_deletes", args.max_deletes), ("trash_days", args.trash_days),
                                               ("stale_days", args.stale_days), ("verify_days", args.verify_days))
               if value is not None}
    excludes = [] if args.no_excludes else args.exclude
    try:
        if numbers or excludes is not None:
            pair = actions.update_pair(args.id, excludes=excludes, **numbers)
        elif (pair := _load_config().find(args.id)) is None:
            raise actions.ActionError("pair.unknown", id=args.id)
    except actions.ActionError as exc:
        return _fail(exc)
    print(t("cli.settings", id=pair.id, max_deletes=pair.max_deletes, trash_days=pair.trash_days,
            stale_days=pair.stale_days, verify_days=pair.verify_days, excludes=", ".join(pair.excludes) or "—"))
    return 0


def _set_paused(args, paused: bool) -> int:
    try:
        pair = actions.set_paused(args.id, paused)
    except actions.ActionError as exc:
        return _fail(exc)
    print(t("cli.paused" if paused else "cli.resumed", id=pair.id))
    return 0


def cmd_sync(args) -> int:
    if _load_config().find(args.id) is None:
        return _fail(actions.ActionError("pair.unknown", id=args.id))
    try:
        actions.sync_now(args.id)
    except actions.ActionError as exc:
        return _fail(exc)
    print(t("cli.sync_requested", id=args.id))
    return 0


def cmd_approve(args, adopt: bool = False) -> int:
    if _load_config().find(args.id) is None:
        return _fail(actions.ActionError("pair.unknown", id=args.id))
    if actions.service_info() is None:
        return _fail(actions.ActionError("cli.needs_daemon"))
    st = actions.pair_states().get(args.id, {})
    held = st.get("held")
    if st.get("state") != "held" or not held:
        print(t("cli.not_held", id=args.id))
        return 1
    sample = held.get("sample", [])
    shown = sample[:HELD_SHOWN]
    print(t("cli.held_list", id=args.id, count=held["count"],
            source_files=held.get("source_files", 0), shown=len(shown)))
    for path in shown:
        print(f"  - {path}")
    if held["count"] > len(shown):
        print("  " + t("cli.held_more", more=held["count"] - len(shown)))
    first = held.get("kind") == "first_round"
    if adopt and not first:
        print(t("cli.adopt_only_first", id=args.id), file=sys.stderr)
        return 1
    print(t("cli.adopt_note") if adopt else t("cli.held_trash"))
    if first and not adopt:
        print(t("cli.first_hint", id=args.id))
    if not args.yes:
        answer = input(t("cli.confirm")).strip().lower()
        if answer not in ("y", "yes", "e", "evet"):
            print(t("cli.cancelled"))
            return 1
    try:
        (actions.adopt if adopt else actions.approve)(args.id, held["fingerprint"])
    except actions.ActionError as exc:
        return _fail(exc)
    print(t("cli.adopted" if adopt else "cli.approved", id=args.id))
    return 0


def _pair_or_fail(pair_id: str) -> config.Pair | None:
    pair = _load_config().find(pair_id)
    if pair is None:
        _fail(actions.ActionError("pair.unknown", id=pair_id))
    return pair


def cmd_trash(args) -> int:
    pair = _pair_or_fail(args.id)
    if pair is None:
        return 2
    found = trash.rounds(pair)
    if not found:
        print(t("cli.trash_empty", id=pair.id))
        return 0
    for rnd in found:
        print(t("cli.trash_round", round=rnd.name, when=_fmt_time(rnd.at), count=len(rnd.files), size=rnd.size))
        for item in rnd.files[:HELD_SHOWN]:
            print(f"  - {item.path}")
        if len(rnd.files) > HELD_SHOWN:
            print("  " + t("cli.held_more", more=len(rnd.files) - HELD_SHOWN))
    return 0


def cmd_restore(args) -> int:
    pair = _pair_or_fail(args.id)
    if pair is None:
        return 2
    try:
        result = trash.restore(pair, args.round, args.paths or None)
    except trash.RestoreError as exc:
        print(t(exc.key, **exc.params), file=sys.stderr)
        return 2
    for rel, dest in result.restored:
        print(f"  ← {dest}" if dest == rel else f"  ← {dest}  ({rel})")
    for rel, error in result.failed:
        print(f"  ✗ {rel}: {error}", file=sys.stderr)
    print(tn("cli.restored", len(result.restored), renamed=result.renamed))
    return 1 if result.failed else 0


def cmd_gui(_args) -> int:
    from whysync.ui.app import run

    return run()


def cmd_daemon(_args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    daemon = Daemon()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: daemon.stop())
    try:
        daemon.run()
    except AlreadyRunning:
        print(t("cli.already_running"), file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whysync", description=t("cli.description"))
    parser.add_argument("--version", action="version", version=f"whySYNC {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help=t("cli.cmd.status")).set_defaults(func=cmd_status)

    add = sub.add_parser("add", help=t("cli.cmd.add"))
    add.add_argument("source")
    add.add_argument("target")
    add.add_argument("--max-deletes", type=int, default=50, help=t("cli.arg.max_deletes"))
    add.add_argument("--exclude", action="append", help=t("cli.arg.exclude"))
    add.set_defaults(func=cmd_add)

    for name, func in (("remove", cmd_remove), ("sync", cmd_sync)):
        p = sub.add_parser(name, help=t(f"cli.cmd.{name}"))
        p.add_argument("id")
        p.set_defaults(func=func)
    for name, paused in (("pause", True), ("resume", False)):
        p = sub.add_parser(name, help=t(f"cli.cmd.{name}"))
        p.add_argument("id")
        p.set_defaults(func=lambda a, _p=paused: _set_paused(a, _p))

    settings = sub.add_parser("set", help=t("cli.cmd.set"))
    settings.add_argument("id")
    settings.add_argument("--max-deletes", type=int, help=t("cli.arg.max_deletes"))
    settings.add_argument("--trash-days", type=int, help=t("cli.arg.trash_days"))
    settings.add_argument("--stale-days", type=int, help=t("cli.arg.stale_days"))
    settings.add_argument("--verify-days", type=int, help=t("cli.arg.verify_days"))
    settings.add_argument("--exclude", action="append", help=t("cli.arg.exclude_set"))
    settings.add_argument("--no-excludes", action="store_true", help=t("cli.arg.no_excludes"))
    settings.set_defaults(func=cmd_set)

    repair = sub.add_parser("repair", help=t("cli.cmd.repair"))
    repair.add_argument("id")
    repair.add_argument("-y", "--yes", action="store_true", help=t("cli.arg.yes"))
    repair.set_defaults(func=cmd_repair)

    for name, adopt in (("approve", False), ("adopt", True)):
        p = sub.add_parser(name, help=t(f"cli.cmd.{name}"))
        p.add_argument("id")
        p.add_argument("-y", "--yes", action="store_true", help=t("cli.arg.yes"))
        p.set_defaults(func=lambda a, _adopt=adopt: cmd_approve(a, adopt=_adopt))

    trash_cmd = sub.add_parser("trash", help=t("cli.cmd.trash"))
    trash_cmd.add_argument("id")
    trash_cmd.set_defaults(func=cmd_trash)
    restore_cmd = sub.add_parser("restore", help=t("cli.cmd.restore"))
    restore_cmd.add_argument("id")
    restore_cmd.add_argument("round")
    restore_cmd.add_argument("paths", nargs="*")
    restore_cmd.set_defaults(func=cmd_restore)

    sub.add_parser("daemon", help=t("cli.cmd.daemon")).set_defaults(func=cmd_daemon)
    sub.add_parser("gui", help=t("cli.cmd.gui")).set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        lang = config.load().language
    except config.ConfigError:
        lang = None
    init_language(lang)
    args = build_parser().parse_args(argv)
    return getattr(args, "func", cmd_status)(args)
