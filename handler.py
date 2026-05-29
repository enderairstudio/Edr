import argparse
import json
from pathlib import Path
import secrets
import sys

import error as e
import print as p
import relay as r
import share as s

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
    if args[0] in {"version", "--version", "-V"}:
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
    create_cmd.set_defaults(func=cmd_create)

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
    pack_cmd.set_defaults(func=cmd_pack)

    status_cmd = subparsers.add_parser("status", aliases=["st"], help="show what a sharer would send", allow_abbrev=False)
    status_cmd.add_argument("share_id", nargs="?")
    status_cmd.add_argument("--path")
    status_cmd.add_argument("--include-cli", action="store_true")
    status_cmd.set_defaults(func=cmd_status)

    ip_cmd = subparsers.add_parser("ip", help="print this machine's sharing IP", allow_abbrev=False)
    ip_cmd.set_defaults(func=cmd_ip)

    version_cmd = subparsers.add_parser("version", aliases=["--version"], help="show version", allow_abbrev=False)
    version_cmd.set_defaults(func=cmd_version)

    doctor_cmd = subparsers.add_parser("doctor", help="show CLI debug paths", allow_abbrev=False)
    doctor_cmd.set_defaults(func=cmd_doctor)

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

    folder = resolve_folder(folder_path)
    entry = {
        "id": share_id,
        "path": str(folder),
        "port": args.port,
        "auto": args.auto,
        "include_cli": args.include_cli,
        "non_network": non_network,
    }
    if non_network:
        entry["relay_id"] = relay_id
        entry["relay_code"] = r.relay_code(relay_id)

    store[share_id] = entry
    save_store(store)

    p.success(f"Created sharer '{share_id}'.")
    p.key_value("Folder", folder)
    p.key_value("Port", args.port)
    p.key_value("Auto", "yes" if args.auto else "no")
    if non_network:
        p.key_value("Network", "relay (anywhere)")
        p.key_value("Share code", entry["relay_code"])
        p.key_value("Relay", r.relay_base_url())
        p.info("Start relay if needed: edr relay start")
        p.info(f"Start sharing: edr start {share_id}")
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
        p.key_value(
            share_id,
            f"{item['path']}  port={item['port']}  auto={'yes' if item.get('auto') else 'no'}  net={network}{suffix}",
        )
    return 0


def cmd_dir(args):
    item = get_selected_sharer(args.share_id)
    print(item["path"])
    return 0


def cmd_set_dir(args):
    store = load_store()
    item = require_sharer(store, args.share_id)
    folder = resolve_folder(args.path)
    item["path"] = str(folder)
    save_store(store)
    p.success(f"Updated '{args.share_id}' folder.")
    p.key_value("Folder", folder)
    return 0


def cmd_start(args):
    item = get_selected_sharer(args.share_id)
    forever = args.auto or (item.get("auto") and not args.once)
    port = args.port or item["port"]
    start_profile(item, port=port, forever=forever, dry_run=args.dry_run)
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
    )
    return 0


def cmd_push(args):
    item = get_selected_sharer(args.share_id)
    port = args.port or item["port"]
    start_profile(item, port=port, forever=args.auto, dry_run=args.dry_run)
    return 0


def cmd_receive(args):
    if not args.remote:
        raise e.CliError("Missing remote. Use: edr pull <ip> or edr pull Edrnko_<id>")

    relay_id, _relay_code = r.parse_relay_remote(args.remote)
    if relay_id:
        extracted, destination = s.receive_project(args.remote, target_dir=args.to, force=args.force)
    else:
        if s.is_local_address(args.remote) and not args.allow_self:
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
    target, size = s.bundle_to_file(args.output, root_dir=folder, include_cli=args.include_cli, force=args.force)
    p.success(f"Packed {target} ({s.format_bytes(size)}).")
    return 0


def cmd_status(args):
    include_cli = args.include_cli
    if args.share_id:
        item = get_selected_sharer(args.share_id)
        folder = Path(item["path"])
        include_cli = include_cli or item.get("include_cli", False)
        p.key_value("Sharer", item["id"])
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
    p.key_value("Version", p.VERSION)
    p.key_value("Python", sys.executable)
    p.key_value("Command", Path(__file__).resolve().with_name("command.py"))
    p.key_value("Handler", Path(__file__).resolve())
    p.key_value("Relay URL", r.relay_base_url())
    p.key_value("CWD", Path.cwd())
    p.key_value("ARGV", " ".join(sys.argv))
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


def start_profile(item, port=None, forever=False, dry_run=False):
    s.start_server(
        root_dir=Path(item["path"]),
        port=port or item["port"],
        include_cli=item.get("include_cli", False),
        dry_run=dry_run,
        share_id=item["id"],
        forever=forever,
        non_network=item.get("non_network", False),
        relay_id=item.get("relay_id"),
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
    return Path.cwd() / STATE_DIR / SHARERS_FILE


def get_selected_sharer(share_id=None):
    store = load_store()
    if share_id:
        return require_sharer(store, share_id)
    if len(store) == 1:
        return next(iter(store.values()))
    if not store:
        raise e.CliError("No sharers exist. Use: edr create <folder>")
    raise e.CliError("Multiple sharers exist. Pass a sharer id.")


def require_sharer(store, share_id):
    if share_id not in store:
        raise e.CliError(f"Unknown sharer '{share_id}'. Use: edr list")
    return store[share_id]


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

    # Support: edr create sharer -non-network -idnew (single-dash flags)
    if len(args) >= 2 and args[0] == "create" and args[1] == "sharer":
        converted = ["create", "sharer"]
        for item in args[2:]:
            if item in {"-non-network", "-idnew"}:
                converted.append("--" + item[1:])
            else:
                converted.append(item)
        return converted

    return args


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
