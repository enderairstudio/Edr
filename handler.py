import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

import error as e
import guard as g
import print as p
import relay as r
import share as s
import doctor_checks as doc

STATE_DIR = ".edr"
SHARERS_FILE = "sharers.json"


class CommandParser(argparse.ArgumentParser):
    def error(self, message):
        raise e.CliError(message)


def main(argv=None):
    args = normalize_legacy_args(normalize_invocation(list(sys.argv[1:] if argv is None else argv)))

    if not args or args[0] in {"help", "-h", "--help"}:
        p.help_menu()
        return 0
    if args[0] in {"version", "v", "--version", "-V"}:
        return cmd_version(None)

    parser = build_parser()

    try:
        parsed = parser.parse_args(args)
        if not hasattr(parsed, "func"):
            raise e.CliError("Missing command. Use: edr help")
        return parsed.func(parsed)
    except e.CliError as err:
        p.error(str(err))
        return 1


def build_parser():
    parser = CommandParser(
        prog="edr",
        description="Git-style LAN and relay project sharing.",
        add_help=False,
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", parser_class=CommandParser)

    create_cmd = subparsers.add_parser("create", aliases=["init"], help="create a reusable sharer", allow_abbrev=False)
    create_cmd.add_argument("path", nargs="?", default=".")
    create_cmd.add_argument("--id", dest="share_id")
    create_cmd.add_argument("--port", type=valid_port, default=s.DEFAULT_PORT)
    create_cmd.add_argument("--auto", action="store_true", help="default to long-running auto mode")
    create_cmd.add_argument("--include-cli", action="store_true")
    create_cmd.add_argument("--non-network", action="store_true", help="share via relay (any network)")
    create_cmd.add_argument("--idnew", action="store_true", help="generate a new random relay id")
    create_cmd.add_argument("--allow-self", action="store_true", help="allow pulling from this same machine")
    create_cmd.add_argument("--skip-guard", action="store_true", help="skip EDR Guard scan when sharing")
    create_cmd.add_argument("--watch", action="store_true", help="detect folder changes while sharing (auto-share)")
    create_cmd.add_argument("--name", help="display name for this sharer")
    create_cmd.set_defaults(func=cmd_create)

    edit_cmd = subparsers.add_parser("edit", help="edit a saved sharer", allow_abbrev=False)
    edit_cmd.add_argument("share_id", nargs="?", help="sharer id, relay id, or display name")
    add_edit_options(edit_cmd)
    edit_cmd.set_defaults(func=cmd_edit)

    rm_cmd = subparsers.add_parser("rm", aliases=["remove", "delete"], help="remove saved data", allow_abbrev=False)
    rm_sub = rm_cmd.add_subparsers(dest="rm_target", parser_class=CommandParser)
    rm_share = rm_sub.add_parser("share", help="remove a saved sharer", allow_abbrev=False)
    rm_share.add_argument("--id", dest="share_id", required=True, help="sharer id or display name")
    rm_share.set_defaults(func=cmd_rm_share)

    list_cmd = subparsers.add_parser("list", aliases=["ls"], help="list saved sharers", allow_abbrev=False)
    list_cmd.set_defaults(func=cmd_list)

    dir_cmd = subparsers.add_parser("dir", aliases=["directory"], help="show a sharer's folder", allow_abbrev=False)
    dir_cmd.add_argument("share_id", nargs="?")
    dir_cmd.set_defaults(func=cmd_dir)

    set_dir_cmd = subparsers.add_parser("set-dir", aliases=["setdir"], help="change a sharer's folder", allow_abbrev=False)
    set_dir_cmd.add_argument("share_id")
    set_dir_cmd.add_argument("path")
    set_dir_cmd.set_defaults(func=cmd_set_dir)

    start_cmd = subparsers.add_parser("start", aliases=["run"], help="start a saved sharer", allow_abbrev=False)
    start_cmd.add_argument("share_id", nargs="?")
    start_cmd.add_argument("--port", type=valid_port)
    start_cmd.add_argument("--once", action="store_true")
    start_cmd.add_argument("--auto", action="store_true")
    start_cmd.add_argument("--dry-run", action="store_true")
    start_cmd.add_argument("--skip-guard", action="store_true", help="skip EDR Guard scan for this session")
    start_cmd.add_argument("--watch", action="store_true", help="detect folder changes while waiting (auto-share)")
    start_cmd.add_argument("--no-qr", action="store_true", help="do not print a pull QR code")
    start_cmd.set_defaults(func=cmd_start)

    share_cmd = subparsers.add_parser("share", aliases=["serve"], help="serve a folder without saving it", allow_abbrev=False)
    share_cmd.add_argument("path", nargs="?", default=".")
    add_share_options(share_cmd)
    share_cmd.set_defaults(func=cmd_share)

    push_cmd = subparsers.add_parser("push", aliases=["send"], help="serve a saved sharer once", allow_abbrev=False)
    push_cmd.add_argument("share_id", nargs="?")
    push_cmd.add_argument("--port", type=valid_port)
    push_cmd.add_argument("--auto", action="store_true")
    push_cmd.add_argument("--dry-run", action="store_true")
    push_cmd.set_defaults(func=cmd_push)

    pull_cmd = subparsers.add_parser("pull", help="receive from a remote sharer", allow_abbrev=False)
    add_receive_options(pull_cmd)
    pull_cmd.set_defaults(func=cmd_receive)

    receive_cmd = subparsers.add_parser("receive", aliases=["recv"], help="receive from a remote sharer", allow_abbrev=False)
    add_receive_options(receive_cmd)
    receive_cmd.set_defaults(func=cmd_receive)

    clone_cmd = subparsers.add_parser("clone", help="alias for receive", allow_abbrev=False)
    add_receive_options(clone_cmd)
    clone_cmd.set_defaults(func=cmd_receive)

    pack_cmd = subparsers.add_parser("pack", help="create a zip bundle", allow_abbrev=False)
    pack_cmd.add_argument("output", nargs="?", default="project_payload.zip")
    pack_cmd.add_argument("--path", default=".")
    pack_cmd.add_argument("--include-cli", action="store_true")
    pack_cmd.add_argument("--force", action="store_true")
    pack_cmd.add_argument("--skip-guard", action="store_true", help="skip EDR Guard scan")
    pack_cmd.set_defaults(func=cmd_pack)

    scan_cmd = subparsers.add_parser("scan", help="run EDR Guard on a folder", allow_abbrev=False)
    scan_cmd.add_argument("path", nargs="?", default=".")
    scan_cmd.add_argument("--include-cli", action="store_true")
    scan_cmd.add_argument("--report", metavar="FILE", help="export JSON guard report (also writes .txt)")
    scan_cmd.set_defaults(func=cmd_scan)

    status_cmd = subparsers.add_parser("status", aliases=["st"], help="show what a sharer would send", allow_abbrev=False)
    status_cmd.add_argument("share_id", nargs="?")
    status_cmd.add_argument("--path")
    status_cmd.add_argument("--include-cli", action="store_true")
    status_cmd.set_defaults(func=cmd_status)

    ip_cmd = subparsers.add_parser("ip", help="print this machine's sharing IP", allow_abbrev=False)
    ip_cmd.set_defaults(func=cmd_ip)

    version_cmd = subparsers.add_parser("version", aliases=["v", "--version"], help="show version", allow_abbrev=False)
    version_cmd.set_defaults(func=cmd_version)

    doctor_cmd = subparsers.add_parser("doctor", help="show CLI debug paths", allow_abbrev=False)
    doctor_cmd.set_defaults(func=cmd_doctor)

    uninstall_cmd = subparsers.add_parser("uninstall", help="remove EDR from this user account", allow_abbrev=False)
    uninstall_cmd.add_argument("-v", "--full", action="store_true", help="fully remove EDR files, state, and PATH entries")
    uninstall_cmd.set_defaults(func=cmd_uninstall)

    relay_cmd = subparsers.add_parser("relay", help="relay server for non-network sharing", allow_abbrev=False)
    relay_sub = relay_cmd.add_subparsers(dest="relay_action", parser_class=CommandParser)
    relay_start = relay_sub.add_parser("start", help="start local relay server", allow_abbrev=False)
    relay_start.add_argument("--host", default="0.0.0.0")
    relay_start.add_argument("--port", type=valid_port, default=8765)
    relay_start.set_defaults(func=cmd_relay_start)

    return parser


def add_share_options(parser):
    parser.add_argument("--port", type=valid_port, default=s.DEFAULT_PORT)
    parser.add_argument("--include-cli", action="store_true")
    parser.add_argument("--auto", action="store_true", help="keep serving new pulls")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--non-network", action="store_true")
    parser.add_argument("--idnew", action="store_true")
    parser.add_argument("--skip-guard", action="store_true", help="skip EDR Guard scan")
    parser.add_argument("--watch", action="store_true", help="detect folder changes while waiting")
    parser.add_argument("--no-qr", action="store_true", help="do not print a pull QR code")


def add_edit_options(parser):
    parser.add_argument("--name", default=argparse.SUPPRESS, help="display name (empty clears)")
    parser.add_argument("--path", default=argparse.SUPPRESS, help="project folder")
    parser.add_argument("--port", type=valid_port, default=argparse.SUPPRESS)
    parser.add_argument("--auto", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--no-auto", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--include-cli", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--no-include-cli", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--non-network", action="store_true", default=argparse.SUPPRESS, help="share via relay")
    parser.add_argument("--network", action="store_true", default=argparse.SUPPRESS, help="share on LAN (turn off relay)")
    parser.add_argument("--idnew", action="store_true", default=argparse.SUPPRESS, help="new relay share code")
    parser.add_argument("--allow-self", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--no-allow-self", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--skip-guard", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--no-skip-guard", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--watch", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--no-watch", action="store_true", default=argparse.SUPPRESS)


def add_receive_options(parser):
    parser.add_argument("remote", nargs="?")
    parser.add_argument("--port", type=valid_port, default=s.DEFAULT_PORT)
    parser.add_argument("--to")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-self", action="store_true", help="allow pulling from this same machine")


def cmd_create(args):
    folder_path = args.path
    if folder_path == "sharer":
        folder_path = "."

    store = load_store()
    non_network = args.non_network

    if non_network:
        if args.share_id:
            relay_id = args.share_id
            if relay_id.startswith(r.RELAY_PREFIX):
                relay_id = relay_id[len(r.RELAY_PREFIX):]
        elif args.idnew or not args.share_id:
            relay_id = r.generate_relay_id()
        else:
            relay_id = r.generate_relay_id()
        share_id = relay_id
    else:
        share_id = args.share_id or generate_share_id()

    if share_id in store:
        raise e.CliError(f"Sharer '{share_id}' already exists.")

    display_name = resolve_display_name(args.name, prompt_if_missing=True)
    if display_name and sharer_name_taken(store, display_name):
        raise e.CliError(f"Name '{display_name}' is already used. Pick another with --name.")

    folder = resolve_folder(folder_path)
    entry = {
        "id": share_id,
        "path": str(folder),
        "port": args.port,
        "auto": args.auto,
        "include_cli": args.include_cli,
        "non_network": non_network,
        "allow_self": args.allow_self,
        "skip_guard": args.skip_guard,
        "watch": args.watch,
    }
    if display_name:
        entry["name"] = display_name
    if non_network:
        entry["relay_id"] = relay_id
        entry["relay_code"] = r.relay_code(relay_id)

    store[share_id] = entry
    save_store(store)

    created_label = format_sharer_label(entry, share_id)
    p.success(f"Created sharer {created_label}.")
    p.key_value("Folder", folder)
    p.key_value("Port", args.port)
    p.key_value("Auto", "yes" if args.auto else "no")
    if non_network:
        p.key_value("Network", "relay (anywhere)")
        p.key_value("Share code", entry["relay_code"])
        p.key_value("Relay", r.relay_base_url())
        p.info(f"Start sharing (waits for pull): edr start {share_id}")
        p.info(f"Pull anywhere: edr pull {entry['relay_code']}")
    else:
        p.info(f"Start it with: edr start {share_id}")
    return 0


def cmd_list(args):
    store = load_store()
    if not store:
        p.info("No sharers created yet. Use: edr create <folder>")
        return 0

    for share_id in sorted(store):
        item = store[share_id]
        network = "relay" if item.get("non_network") else "lan"
        code = item.get("relay_code", "")
        suffix = f"  code={code}" if code else ""
        watch_flag = "  watch=yes" if item.get("watch") else ""
        p.key_value(
            format_sharer_label(item, share_id),
            f"{item['path']}  port={item['port']}  auto={'yes' if item.get('auto') else 'no'}  net={network}{suffix}{watch_flag}",
        )
    return 0


def cmd_edit(args):
    store = load_store()
    if not store:
        raise e.CliError("No sharers exist. Use: edr create <folder>")

    key = resolve_sharer_key(store, args.share_id) if args.share_id else None
    if not key:
        if len(store) == 1:
            key = next(iter(store))
        else:
            raise e.CliError("Multiple sharers exist. Pass id or name: edr edit sharer <id>")

    item = store[key]
    changed = apply_sharer_edits(store, key, item, args)
    if not changed:
        raise e.CliError("Nothing to change. Pass options, e.g. --path, --name, --network, --non-network")

    new_key = key
    if item.get("non_network") and item.get("relay_id") and item["relay_id"] != key:
        new_key = item["relay_id"]
        store[new_key] = item
        if new_key != key:
            del store[key]
            key = new_key

    store[key] = item
    save_store(store)
    p.success(f"Updated sharer {format_sharer_label(item, key)}.")
    _print_sharer_details(item, key)
    return 0


def cmd_rm_share(args):
    store = load_store()
    key = resolve_sharer_key(store, args.share_id)
    item = store.pop(key)
    save_store(store)
    p.success(f"Removed sharer {format_sharer_label(item, key)}.")
    return 0


def cmd_dir(args):
    item = get_selected_sharer(args.share_id)
    print(item["path"])
    return 0


def cmd_set_dir(args):
    store = load_store()
    key = resolve_sharer_key(store, args.share_id)
    item = store[key]
    folder = resolve_folder(args.path)
    item["path"] = str(folder)
    save_store(store)
    p.success(f"Updated {format_sharer_label(item, key)} folder.")
    p.key_value("Folder", folder)
    return 0


def cmd_start(args):
    item = get_selected_sharer(args.share_id)
    forever = args.auto or (item.get("auto") and not args.once)
    port = args.port or item["port"]
    skip_guard = args.skip_guard or item.get("skip_guard", False)
    watch = args.watch or item.get("watch", False)
    start_profile(
        item,
        port=port,
        forever=forever,
        dry_run=args.dry_run,
        skip_guard=skip_guard,
        watch=watch,
        show_qr=not args.no_qr,
    )
    return 0


def cmd_share(args):
    folder = resolve_folder(args.path)
    relay_id = None
    if args.non_network:
        relay_id = r.generate_relay_id() if args.idnew else r.generate_relay_id()
        p.info(f"Ephemeral share code: {r.relay_code(relay_id)}")
    s.start_server(
        root_dir=folder,
        port=args.port,
        include_cli=args.include_cli,
        dry_run=args.dry_run,
        share_id="ephemeral",
        forever=args.auto,
        non_network=args.non_network,
        relay_id=relay_id,
        skip_guard=args.skip_guard,
        watch=args.watch,
        show_qr=not args.no_qr,
    )
    return 0


def cmd_push(args):
    item = get_selected_sharer(args.share_id)
    port = args.port or item["port"]
    start_profile(item, port=port, forever=args.auto, dry_run=args.dry_run, skip_guard=item.get("skip_guard", False))
    return 0


def cmd_receive(args):
    if not args.remote:
        raise e.CliError("Missing remote. Use: edr pull <ip> or edr pull Edrnko_<id>")

    relay_id, _relay_code = r.parse_relay_remote(args.remote)
    if relay_id:
        extracted, destination = s.receive_project(args.remote, target_dir=args.to, force=args.force)
    else:
        if s.is_local_address(args.remote) and not receive_allows_self(args):
            raise e.CliError(
                "Refusing to pull from this same device. Run edr pull on the other device, "
                "or pass --allow-self for testing."
            )
        extracted, destination = s.receive_project(
            args.remote,
            port=args.port,
            target_dir=args.to,
            force=args.force,
        )

    p.success(f"Pulled {extracted} files into {destination}.")
    return 0


def cmd_pack(args):
    folder = resolve_folder(args.path)
    target, size = s.bundle_to_file(
        args.output,
        root_dir=folder,
        include_cli=args.include_cli,
        force=args.force,
        skip_guard=args.skip_guard,
    )
    p.success(f"Packed {target} ({s.format_bytes(size)}).")
    return 0


def cmd_scan(args):
    folder = resolve_folder(args.path)

    if args.report:
        p.progress("running security scan", 0)

        def on_progress(percent):
            p.progress("running security scan", percent)

        report = g.scan_project_report(folder, args.include_cli, on_progress=on_progress)
        p.progress("running security scan", 100)
        json_path, text_path = g.write_guard_report(report, args.report)
        if report["clean"]:
            p.success(f"EDR Guard: {report['file_count']} files clean — report saved.")
        else:
            p.warn(f"EDR Guard: {report['threat_count']} threat(s) — report saved.")
        p.key_value("JSON", json_path)
        if text_path:
            p.key_value("Text", text_path)
        if not report["clean"]:
            raise e.CliError("Threats found. See report for details.")
        return 0

    count = g.require_clean_project(folder, args.include_cli)
    p.success(f"EDR Guard: {count} files clean — safe to share.")
    return 0


def cmd_status(args):
    include_cli = args.include_cli
    if args.share_id:
        item = get_selected_sharer(args.share_id)
        folder = Path(item["path"])
        include_cli = include_cli or item.get("include_cli", False)
        p.key_value("Sharer", format_sharer_label(item, item["id"]))
        p.key_value("Port", item["port"])
        p.key_value("Auto", "yes" if item.get("auto") else "no")
        if item.get("non_network"):
            p.key_value("Network", "relay")
            p.key_value("Share code", item.get("relay_code", ""))
    else:
        folder = resolve_folder(args.path or ".")

    summary = s.project_summary(root_dir=folder, include_cli=include_cli)
    p.key_value("Folder", folder)
    p.key_value("Files", summary["files"])
    p.key_value("Size", s.format_bytes(summary["bytes"]))
    p.key_value("Include CLI", "yes" if summary["include_cli"] else "no")
    p.key_value("Ignored dirs", summary["ignored_dirs"])
    return 0


def cmd_ip(args):
    print(s.get_local_ip())
    return 0


def cmd_version(args):
    print(p.VERSION)
    return 0


def cmd_doctor(args):
    handler_path = Path(__file__).resolve()
    p.section("EDR doctor")
    p.key_value("CWD", Path.cwd())
    p.key_value("Relay URL", r.relay_base_url())

    fails = 0
    warns = 0
    for level, message in doc.run_all(handler_path):
        if level == "ok":
            p.success(message)
        elif level == "warn":
            p.warn(message)
            warns += 1
        else:
            p.warn(f"[fail] {message}")
            fails += 1

    p.section("Summary")
    if fails:
        p.error(f"{fails} failure(s), {warns} warning(s)")
        return 1
    if warns:
        p.warn(f"All critical checks passed with {warns} warning(s)")
    else:
        p.success("All checks passed")
    return 0


def cmd_uninstall(args):
    targets = uninstall_targets()
    p.section("EDR uninstall")

    if not args.full:
        p.info("Dry run only. Run `edr uninstall -v` to fully remove EDR for this user.")
        for target in targets:
            p.key_value(target["kind"], target["path"])
        p.key_value("PATH cleanup", "yes")
        p.key_value("npm package", "@enderair/edr if installed globally")
        return 0

    removed = 0
    failed = 0
    removed_messages = []
    failed_messages = []
    progress_label = "uninstalling EDR from system"
    uninstall_progress(progress_label, 0, 0)

    for target in targets:
        path = target["path"]
        if not path.exists() and not path.is_symlink():
            continue
        try:
            result = remove_path(path)
            if result == "scheduled":
                removed_messages.append(f"Scheduled {target['kind']} for deletion after EDR exits: {path}")
            else:
                removed_messages.append(f"Removed {target['kind']}: {path}")
            removed += 1
        except OSError as err:
            failed_messages.append(f"Could not remove {path}: {err}")
            failed += 1

    uninstall_progress(progress_label, 1, 60)

    if remove_edr_from_path():
        removed_messages.append("Removed EDR install entries from user PATH")
        removed += 1

    uninstall_progress(progress_label, 61, 80)

    npm_removed = uninstall_npm_package()
    if npm_removed:
        removed_messages.append("Removed global npm package @enderair/edr and its shims")
        removed += 1

    uninstall_progress(progress_label, 81, 100, done=True)

    p.section("Summary")
    for message in removed_messages:
        p.success(message)
    for message in failed_messages:
        p.warn(message)
    if failed:
        p.warn(f"Removed {removed} item(s), {failed} item(s) need manual cleanup.")
        print("GoodBye :(")
        return 1
    p.success(f"EDR uninstall complete. Removed {removed} item(s).")
    print("GoodBye :(")
    return 0


def cmd_relay_start(args):
    _, url = r.start_relay_server(host=args.host, port=args.port)
    p.success(f"Relay listening at {url}")
    p.info("Set EDR_RELAY_URL on all devices that should use this relay.")
    p.info("Press Ctrl+C to stop.")
    try:
        while True:
            import time
            time.sleep(3600)
    except KeyboardInterrupt:
        p.info("Relay stopped.")
    return 0


def receive_allows_self(args):
    if args.allow_self:
        return True
    store = load_store()
    port = args.port
    for item in store.values():
        if item.get("allow_self") and port == item.get("port", s.DEFAULT_PORT):
            return True
    return False


def uninstall_targets():
    home = Path.home()
    targets = [
        {"kind": "state", "path": home / STATE_DIR},
    ]

    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home))
        roaming = Path(os.environ.get("APPDATA", home))
        targets.extend(
            [
                {"kind": "install", "path": local / "EDR"},
                {"kind": "install", "path": local / "EDR" / "EDR-Setup"},
                {"kind": "install", "path": roaming / "EDR"},
            ]
        )
    elif sys.platform == "darwin":
        targets.extend(
            [
                {"kind": "install", "path": home / "Applications" / "EDR"},
                {"kind": "install", "path": home / ".local" / "share" / "edr"},
            ]
        )
    else:
        targets.extend(
            [
                {"kind": "install", "path": home / ".local" / "share" / "edr"},
                {"kind": "launcher", "path": home / ".local" / "bin" / "edr"},
                {"kind": "system launcher", "path": Path("/usr/bin/edr")},
                {"kind": "system install", "path": Path("/usr/share/edr")},
            ]
        )

    legacy_state = Path.cwd() / STATE_DIR
    app_dir = Path(__file__).resolve().parent
    if Path.cwd().resolve() != app_dir and legacy_state != home / STATE_DIR and (legacy_state / SHARERS_FILE).exists():
        targets.append({"kind": "legacy state", "path": legacy_state})

    return targets


def uninstall_progress(label, start, end, done=False):
    step = 1 if end >= start else -1
    for percent in range(start, end + step, step):
        sys.stdout.write(f"\r{label}... {percent}")
        sys.stdout.flush()
        if percent != end:
            time.sleep(0.04)
    if done:
        sys.stdout.write(f"\r{label}... done   \n")
        sys.stdout.flush()


def remove_path(path):
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        return "removed"
    except PermissionError:
        if sys.platform == "win32" and schedule_windows_delete(path):
            return "scheduled"
        raise


def schedule_windows_delete(path):
    if not path.exists() and not path.is_symlink():
        return False

    path_text = str(path)
    safe_path = path_text.replace('"', '""')
    if path.is_dir() and not path.is_symlink():
        delete_command = f'timeout /t 2 /nobreak >nul & rmdir /s /q "{safe_path}"'
    else:
        delete_command = f'timeout /t 2 /nobreak >nul & del /f /q "{safe_path}"'

    try:
        subprocess.Popen(
            ["cmd.exe", "/c", delete_command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            ),
            close_fds=True,
        )
        return True
    except OSError:
        return False


def remove_edr_from_path():
    if sys.platform == "win32":
        return remove_edr_from_windows_user_path()
    return remove_edr_from_shell_profiles()


def remove_edr_from_windows_user_path():
    try:
        import winreg
    except ImportError:
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                current, value_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return False
            parts = [part for part in current.split(";") if part and not is_edr_path_entry(part)]
            updated = ";".join(parts).strip(";")
            if updated == current:
                return False
            winreg.SetValueEx(key, "Path", 0, value_type, updated)
            return True
    except OSError as err:
        p.warn(f"Could not update user PATH: {err}")
        return False


def remove_edr_from_shell_profiles():
    install_dirs = {
        str(Path.home() / "Applications" / "EDR"),
        str(Path.home() / ".local" / "share" / "edr"),
    }
    changed = False
    for profile in (Path.home() / ".profile", Path.home() / ".zprofile"):
        if not profile.exists():
            continue
        try:
            lines = profile.read_text(encoding="utf-8", errors="ignore").splitlines()
            kept = [line for line in lines if not any(folder in line and "PATH" in line for folder in install_dirs)]
            if kept != lines:
                profile.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
                changed = True
        except OSError as err:
            p.warn(f"Could not update {profile}: {err}")
    return changed


def is_edr_path_entry(entry):
    if not entry:
        return False
    upper = entry.upper().replace("/", "\\")
    return (
        "\\EDR\\" in upper
        or upper.endswith("\\EDR")
        or "\\EDR-SETUP\\" in upper
        or "\\NODE_MODULES\\@ENDERAIR\\EDR" in upper
    )


def uninstall_npm_package():
    removed_any = False

    if shutil.which("npm"):
        try:
            result = subprocess.run(
                ["npm", "uninstall", "-g", "@enderair/edr", "--loglevel=error"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            removed_any = result.returncode == 0
        except OSError:
            pass

    removed_any = remove_npm_global_install() or removed_any
    return removed_any


def remove_npm_global_install():
    prefix = get_npm_global_prefix()
    if not prefix:
        return False

    removed = False
    package_dir = prefix / "node_modules" / "@enderair" / "edr"
    shim_paths = [prefix / "edr", prefix / "edr.cmd", prefix / "edr.ps1"]
    if sys.platform != "win32":
        shim_paths = [prefix / "bin" / "edr"]

    if package_dir.exists() or package_dir.is_symlink():
        try:
            remove_path(package_dir)
            removed = True
        except OSError:
            pass

    for shim in shim_paths:
        if shim.exists() or shim.is_symlink():
            try:
                remove_path(shim)
                removed = True
            except OSError:
                pass

    return removed


def get_npm_global_prefix():
    if not shutil.which("npm"):
        return None
    try:
        result = subprocess.run(
            ["npm", "config", "get", "prefix"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None

    value = (result.stdout or "").strip().splitlines()
    if not value:
        return None
    prefix = value[-1].strip()
    return Path(prefix) if prefix else None


def start_profile(item, port=None, forever=False, dry_run=False, skip_guard=False, watch=False, show_qr=True):
    folder = Path(item["path"])
    summary = s.project_summary(root_dir=folder, include_cli=item.get("include_cli", False))
    if summary["files"] == 0:
        p.warn(
            f"Sharer folder has 0 files to send: {folder}\n"
            f"  Fix with: edr set-dir {item['id']} <your-project-folder>"
        )

    s.start_server(
        root_dir=folder,
        port=port or item["port"],
        include_cli=item.get("include_cli", False),
        dry_run=dry_run,
        share_id=item["id"],
        forever=forever,
        non_network=item.get("non_network", False),
        relay_id=item.get("relay_id"),
        skip_guard=skip_guard,
        watch=watch,
        show_qr=show_qr,
    )


def load_store():
    path = store_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise e.CliError(f"Cannot read {path}: {err}")


def save_store(store):
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")


def store_path():
    path = Path.home() / STATE_DIR / SHARERS_FILE
    legacy = Path.cwd() / STATE_DIR / SHARERS_FILE
    if legacy.exists() and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def get_selected_sharer(share_id=None):
    store = load_store()
    if share_id:
        return require_sharer(store, share_id)
    if len(store) == 1:
        return next(iter(store.values()))
    if not store:
        raise e.CliError("No sharers exist. Use: edr create <folder>")
    raise e.CliError("Multiple sharers exist. Pass a sharer id.")


def require_sharer(store, ref):
    key = resolve_sharer_key(store, ref)
    return store[key]


def resolve_sharer_key(store, ref):
    if ref in store:
        return ref
    if ref.startswith(r.RELAY_PREFIX):
        relay_id = ref[len(r.RELAY_PREFIX) :]
        if relay_id in store:
            return relay_id
    name_key = ref.lower()
    matches = [key for key, item in store.items() if item.get("name", "").lower() == name_key]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise e.CliError(f"Multiple sharers named '{ref}'. Use: edr list")
    raise e.CliError(f"Unknown sharer '{ref}'. Use: edr list")


def sharer_name_taken(store, name, except_key=None):
    name_key = name.lower()
    for key, item in store.items():
        if key == except_key:
            continue
        if item.get("name", "").lower() == name_key:
            return True
    return False


def format_sharer_label(item, share_id):
    name = item.get("name")
    if name:
        return f"'{name}' ({share_id})"
    return f"'{share_id}'"


def resolve_display_name(name, prompt_if_missing=False):
    if name:
        return name.strip()
    if not prompt_if_missing:
        return None
    return p.prompt_name_countdown(3)


def apply_sharer_edits(store, key, item, args):
    changed = False

    if hasattr(args, "name"):
        new_name = args.name.strip() if args.name else None
        if new_name and sharer_name_taken(store, new_name, except_key=key):
            raise e.CliError(f"Name '{new_name}' is already used.")
        if new_name:
            item["name"] = new_name
        else:
            item.pop("name", None)
        changed = True

    if hasattr(args, "path"):
        item["path"] = str(resolve_folder(args.path))
        changed = True

    if hasattr(args, "port"):
        item["port"] = args.port
        changed = True

    if hasattr(args, "auto"):
        item["auto"] = True
        changed = True
    if hasattr(args, "no_auto"):
        item["auto"] = False
        changed = True

    if hasattr(args, "include_cli"):
        item["include_cli"] = True
        changed = True
    if hasattr(args, "no_include_cli"):
        item["include_cli"] = False
        changed = True

    if hasattr(args, "allow_self"):
        item["allow_self"] = True
        changed = True
    if hasattr(args, "no_allow_self"):
        item["allow_self"] = False
        changed = True

    if hasattr(args, "skip_guard"):
        item["skip_guard"] = True
        changed = True
    if hasattr(args, "no_skip_guard"):
        item["skip_guard"] = False
        changed = True

    if hasattr(args, "watch"):
        item["watch"] = True
        changed = True
    if hasattr(args, "no_watch"):
        item["watch"] = False
        changed = True

    if hasattr(args, "network"):
        if item.get("non_network"):
            item["non_network"] = False
            item.pop("relay_id", None)
            item.pop("relay_code", None)
            changed = True

    if hasattr(args, "non_network"):
        item["non_network"] = True
        relay_id = item.get("relay_id")
        if hasattr(args, "idnew") or not relay_id:
            relay_id = r.generate_relay_id()
            item["id"] = relay_id
            item["relay_id"] = relay_id
            item["relay_code"] = r.relay_code(relay_id)
        else:
            item["relay_code"] = r.relay_code(relay_id)
        changed = True
    elif hasattr(args, "idnew") and item.get("non_network"):
        relay_id = r.generate_relay_id()
        item["id"] = relay_id
        item["relay_id"] = relay_id
        item["relay_code"] = r.relay_code(relay_id)
        changed = True

    return changed


def _print_sharer_details(item, share_id):
    p.key_value("Folder", item["path"])
    p.key_value("Port", item["port"])
    p.key_value("Auto", "yes" if item.get("auto") else "no")
    p.key_value("Watch", "yes" if item.get("watch") else "no")
    if item.get("name"):
        p.key_value("Name", item["name"])
    if item.get("non_network"):
        p.key_value("Network", "relay (anywhere)")
        p.key_value("Share code", item.get("relay_code", ""))
        p.key_value("Relay", r.relay_base_url())
    else:
        p.key_value("Network", "LAN")


def resolve_folder(path):
    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise e.CliError(f"Folder does not exist: {folder}")
    if not folder.is_dir():
        raise e.CliError(f"Not a folder: {folder}")
    return folder


def generate_share_id():
    return f"sharer-{secrets.token_hex(3)}"


def normalize_invocation(args):
    if not args:
        return args

    first = args[0].replace("\\", "/").lower()
    if first.endswith("/command.py") or first.endswith("/handler.py") or first in {"command.py", "handler.py", "edr", "edr.exe"}:
        return args[1:]
    return args


def normalize_legacy_args(args):
    if len(args) >= 2 and args[0] == "create" and args[1] == "share":
        converted = ["share"] + args[2:]
        return ["share", "."] + converted[1:] if len(converted) == 1 else converted

    if len(args) >= 2 and args[0] == "connect" and args[1] == "sharer":
        rest = args[2:]
        ip = None
        cleaned = []
        for item in rest:
            if item.startswith("--") and _looks_like_legacy_ip(item[2:]):
                ip = item[2:]
            else:
                cleaned.append(item)
        return ["receive", ip or ""] + cleaned

    # Support: edr create sharer [folder] --non-network --idnew
    if len(args) >= 2 and args[0] == "create" and args[1] == "sharer":
        converted = ["create"]
        rest = args[2:]
        folder = "."
        if rest and not rest[0].startswith("-"):
            folder = rest[0]
            rest = rest[1:]
        converted.append(folder)
        converted.extend(_expand_legacy_flags(rest))
        return converted

    if len(args) >= 2 and args[0] == "edit" and args[1] == "sharer":
        converted = ["edit"]
        rest = args[2:]
        if rest and not rest[0].startswith("-"):
            converted.append(rest[0])
            rest = rest[1:]
        converted.extend(_expand_legacy_flags(rest))
        return converted

    if args and args[0] in {"rm", "remove", "delete"}:
        return _expand_legacy_flags(args)

    return _expand_legacy_flags(args)


def _expand_legacy_flags(args):
    converted = []
    index = 0
    while index < len(args):
        item = args[index]
        if item.startswith("--name-") and len(item) > 7:
            converted.append("--name")
            converted.append(item[7:])
        elif item.startswith("--id-") and len(item) > 5 and args[0] in {"rm", "remove", "delete"}:
            converted.append("--id")
            converted.append(item[5:])
        elif item in {"-non-network", "-idnew"}:
            converted.append("--" + item[1:])
        else:
            converted.append(item)
        index += 1
    return converted


def _looks_like_legacy_ip(value):
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def valid_port(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be a number")
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port
