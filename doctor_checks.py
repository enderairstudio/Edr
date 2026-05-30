"""Health checks for edr doctor."""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

import print as p
import relay as r
import share as s


def _ok(msg):
    return ("ok", msg)


def _warn(msg):
    return ("warn", msg)


def _fail(msg):
    return ("fail", msg)


def check_python_version():
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        return _ok(f"Python {major}.{minor} ({sys.executable})")
    return _warn(f"Python {major}.{minor} — EDR recommends 3.11+ ({sys.executable})")


def check_handler_files(handler_path):
    app_dir = handler_path.parent
    missing = [name for name in ("command.py", "share.py", "guard.py", "relay.py", "watch.py", "qrterm.py") if not (app_dir / name).exists()]
    if missing:
        return _fail(f"Missing app files: {', '.join(missing)}")
    return _ok(f"App bundle at {app_dir}")


def check_disk_space(path, min_mb=50):
    try:
        usage = shutil.disk_usage(path)
        free_mb = usage.free // (1024 * 1024)
        if free_mb < min_mb:
            return _warn(f"Low disk space at {path}: {free_mb} MB free")
        return _ok(f"Disk space at {path}: {free_mb} MB free")
    except OSError as err:
        return _warn(f"Could not read disk space: {err}")


def check_port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
        return _ok(f"TCP port {port} is available for LAN sharing")
    except OSError:
        return _warn(f"TCP port {port} is in use — pick another with --port or stop the other program")


def check_relay(base_url=None):
    base = (base_url or r.relay_base_url()).rstrip("/")
    url = f"{base}/v1/rooms/__doctor__/status"
    try:
        req = urlrequest.Request(url, method="GET")
        with urlrequest.urlopen(req, timeout=3) as response:
            if response.status == 200:
                return _ok(f"Relay reachable at {base}")
    except urlerror.HTTPError as err:
        if err.code in {404, 405}:
            return _ok(f"Relay reachable at {base}")
        return _warn(f"Relay returned HTTP {err.code} at {base}")
    except (urlerror.URLError, TimeoutError, OSError) as err:
        return _warn(f"Relay not reachable at {base} — run: edr relay start  ({err})")
    return _warn(f"Relay check inconclusive at {base}")


def check_lan_ip():
    ip = s.get_local_ip()
    if ip.startswith("127."):
        return _warn(f"LAN IP looks local-only: {ip}")
    return _ok(f"LAN IP for edr pull: {ip}")


def check_sharers_store():
    store_path = Path.home() / ".edr" / "sharers.json"
    if not store_path.exists():
        return _ok("No saved sharers yet (.edr/sharers.json)")
    try:
        import json

        data = json.loads(store_path.read_text(encoding="utf-8"))
        return _ok(f"Sharers store: {len(data)} profile(s) at {store_path}")
    except json.JSONDecodeError as err:
        return _fail(f"Invalid sharers.json: {err}")


def check_edr_on_path():
    if sys.platform != "win32":
        which = shutil.which("edr")
        if which:
            return _ok(f"edr on PATH: {which}")
        return _warn("edr not found on PATH — use full path or install scripts")

    try:
        result = subprocess.run(
            ["where.exe", "edr"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return _warn("No 'edr' found on PATH")
        first = lines[0]
        level = _ok if "appdata\\local\\edr" in first.lower().replace("/", "\\") else _warn
        msg = f"First edr on PATH: {first}"
        if "npm" in first.lower() and ("node_modules" in first.lower() or "@enderair" in first.lower()):
            return _warn(msg + " — old npm global install; prefer EDR-Setup.exe")
        return level(msg)
    except OSError as err:
        return _warn(f"where.exe failed: {err}")


def check_qrcode_library():
    from qrterm import _load_qrcode_module

    if _load_qrcode_module():
        return _ok("QR library available (terminal QR on share)")
    return _warn("QR library missing — pip install qrcode for scan codes in terminal")


def run_all(handler_path):
    checks = [
        lambda: _ok(f"EDR {p.VERSION}"),
        check_python_version,
        lambda: check_handler_files(handler_path),
        lambda: check_disk_space(Path.cwd()),
        lambda: check_port_available(s.DEFAULT_PORT),
        lambda: check_port_available(8765),
        check_lan_ip,
        lambda: check_relay(),
        check_sharers_store,
        check_edr_on_path,
        check_qrcode_library,
    ]
    return [check() for check in checks]
