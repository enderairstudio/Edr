import os
import sys
import time

VERSION = "0.5.18"

_active_stage = None
_work_scale = 1.0
_stage_state = None

# Optional "animated" progress: EDR_PACED_PROGRESS=1 holds every stage on screen
# for a minimum time (up to 18 s each), which makes transfers visibly slower.
# Off by default: progress reflects the real work and never sleeps.
PACED_PROGRESS = os.environ.get("EDR_PACED_PROGRESS", "").strip().lower() in {"1", "true", "yes", "on"}
_MIN_DRAW_INTERVAL = 0.05

# Display pacing (only used when PACED_PROGRESS is on).
_TICK_SEC = 0.055
_BASE_STAGE_SEC = 0.65
_MAX_STAGE_SEC = 18.0

_STAGE_WEIGHT = {
    "running security scan": 1.5,
    "scanning received files": 1.3,
    "scanning project": 1.1,
    "copying files": 1.7,
    "compressing archive": 1.4,
    "preparing share": 0.8,
    "sharing files": 1.6,
    "sending payload": 1.4,
    "downloading project": 1.6,
    "extracting files": 1.3,
    "connecting to relay": 0.6,
    "connecting to sharer": 0.6,
    "waiting for receiver": 0.25,
}


class _StageState:
    __slots__ = ("label", "target", "display", "started", "min_sec", "last_draw")

    def __init__(self, label, min_sec):
        self.label = label
        self.target = 0
        self.display = -1
        self.started = time.monotonic()
        self.min_sec = min_sec
        self.last_draw = 0.0


def configure_workload(files=0, bytes_=0):
    """Scale progress pacing from project size (files + payload bytes)."""
    global _work_scale
    file_count = max(int(files), 1)
    megabytes = max(int(bytes_), 1) / (1024 * 1024)
    # ~32 files / 0.7 MB -> ~1.0x; hundreds of files or tens of MB -> slower.
    _work_scale = max(0.7, min(12.0, 0.55 + (megabytes ** 0.55) * 0.35 + (file_count / 32) ** 0.65 * 0.45))


def _stage_seconds(label):
    weight = _STAGE_WEIGHT.get(label, 1.0)
    return min(_MAX_STAGE_SEC, _BASE_STAGE_SEC * weight * _work_scale)


def _draw(label, percent):
    global _active_stage
    percent = max(0, min(100, int(percent)))
    if label != _active_stage:
        progress_finish()
        _active_stage = label

    if percent >= 100:
        print(f"\r{label}.... done   ")
        _active_stage = None
    else:
        print(f"\r{label}.... {percent}", end="", flush=True)


def progress_finish():
    global _active_stage, _stage_state
    if _active_stage:
        print()
        _active_stage = None
    _stage_state = None


def _begin_stage(label):
    global _stage_state
    if _stage_state is None or _stage_state.label != label:
        progress_finish()
        _stage_state = _StageState(label, _stage_seconds(label))
    return _stage_state


def _time_cap(state):
    elapsed = time.monotonic() - state.started
    if state.min_sec <= 0:
        return state.target
    return int(min(99, (elapsed / state.min_sec) * 100))


def _render(state):
    cap = _time_cap(state)
    next_display = min(state.target, cap)
    if next_display < state.display:
        next_display = state.display
    # Gentle steps so numbers do not jump wildly when work finishes instantly.
    if next_display < state.target and state.target < 100:
        next_display = min(state.target, state.display + 2)
    if next_display == state.display:
        return
    state.display = next_display
    now = time.monotonic()
    if now - state.last_draw < _TICK_SEC and state.target < 100:
        return
    state.last_draw = now
    _draw(state.label, state.display)


def _finish_stage(state):
    state.target = 100
    while True:
        elapsed = time.monotonic() - state.started
        cap = _time_cap(state)
        next_display = min(99, max(state.display + 1, cap))
        if next_display > state.display:
            state.display = next_display
            state.last_draw = time.monotonic()
            _draw(state.label, state.display)
        if elapsed >= state.min_sec and state.display >= 99:
            break
        time.sleep(_TICK_SEC)
    _draw(state.label, 100)
    global _stage_state
    _stage_state = None


def progress(label, percent):
    """Show paced stage progress; larger workloads animate more slowly."""
    global _stage_state
    percent = max(0, min(100, int(percent)))
    state = _begin_stage(label)
    state.target = max(state.target, percent)

    if not PACED_PROGRESS:
        if percent >= 100:
            _draw(label, 100)
            _stage_state = None
            return
        now = time.monotonic()
        if state.target != state.display and (state.display < 0 or now - state.last_draw >= _MIN_DRAW_INTERVAL):
            state.display = state.target
            state.last_draw = now
            _draw(label, state.display)
        return

    if percent >= 100:
        _finish_stage(state)
        return

    _render(state)


def info(message):
    progress_finish()
    print(f"[INFO] {message}")


def success(message):
    progress_finish()
    print(f"[SUCCESS] {message}")


def warn(message):
    progress_finish()
    print(f"[WARN] {message}")


def error(message):
    progress_finish()
    print(f"[ERROR] {message}", file=sys.stderr)


def connection_info(pin):
    progress_finish()
    print("\n" + "=" * 35)
    print(f" Connect via this code: {pin}")
    print("=" * 35 + "\n")


def section(title):
    progress_finish()
    print(f"\n{title}")
    print("-" * len(title))


def key_value(key, value):
    print(f"{key:<14} {value}")


def transfer(action, current, total, path, size=None):
    """Legacy hook — maps file counts to stage progress."""
    if total <= 0:
        progress(f"{action.lower()} files", 100)
        return
    percent = int(current * 100 / total)
    progress(f"{action.lower()} files", percent)


def prompt_name_countdown(seconds=3):
    """Prompt for a display name; returns None if the countdown expires."""
    import threading

    progress_finish()
    # Scripts / CI / piped input: nobody can answer, so don't stall for 3 s.
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return None
    result = [None]

    def reader():
        try:
            line = sys.stdin.readline()
            if line is not None:
                value = line.strip()
                if value:
                    result[0] = value
        except Exception:
            pass

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    for remaining in range(seconds, 0, -1):
        print(f"\rYou didn't add a name, pick a name... {remaining}", end="", flush=True)
        time.sleep(1)
    print()
    thread.join(timeout=0.2)
    return result[0]


_HELP_COL = 32


def _help_title(text):
    print()
    print(text)


def _help_row(left, right=""):
    left = f"  {left}"
    if not right:
        print(left)
    elif len(left) + 2 > _HELP_COL:
        print(left)
        print(" " * _HELP_COL + right)
    else:
        print(f"{left:<{_HELP_COL}}{right}")


def help_menu():
    """Print the EDR command reference (ASCII only, safe for Windows consoles)."""
    progress_finish()
    store = os.path.join(os.path.expanduser("~"), ".edr", "sharers.json")

    print(f"EDR Project Sharer {VERSION}")
    print("Share project folders over LAN or relay. EDR Guard scans every transfer.")

    _help_title("USAGE")
    _help_row("edr <command> [options]")

    _help_title("QUICK START")
    _help_row("edr create . --id myapp", "Save this folder as a sharer")
    _help_row("edr start myapp", "Share it (prints a pull command + QR)")
    _help_row("edr pull <ip>", "Receive it on another device")

    _help_title("SHARE AND RECEIVE")
    _help_row("start [id|name]", "Share a saved sharer until it is pulled")
    _help_row("push [id|name]", "Share a saved sharer once")
    _help_row("share [folder]", "One-off share, nothing saved")
    _help_row("pull <ip|Edrnko_id>", "Download a shared project")
    _help_row("relay start", "Run a relay (sharing across networks)")

    _help_title("SHARERS")
    _help_row("create [folder]", "Save a reusable sharer")
    _help_row("list", "List saved sharers")
    _help_row("edit [id|name]", "Change folder, name, mode or flags")
    _help_row("rm share --id <id|name>", "Delete a sharer")
    _help_row("dir [id|name]", "Print a sharer's folder")
    _help_row("set-dir <id|name> <folder>", "Point a sharer at another folder")
    _help_row("status [id|name]", "Show the files and size it would send")

    _help_title("TOOLS")
    _help_row("scan [folder] [--report f]", "Run EDR Guard without sharing")
    _help_row("pack [zip]", "Zip a folder locally")
    _help_row("ip", "Show this device's LAN IP")
    _help_row("doctor", "Check Python, ports, relay and PATH")
    _help_row("update --latest", "Install the newest EDR release")
    _help_row("update --check", "Only check for a newer release")
    _help_row("uninstall [-v]", "Preview removal (-v removes EDR)")
    _help_row("version", "Show the version")

    _help_title("OPTIONS")
    _help_row("--non-network", "Share through a relay instead of LAN")
    _help_row("--relay-url <url>", "Relay address (same on both sides)")
    _help_row("--idnew", "Generate a new random relay id")
    _help_row("--port <n>", "LAN port (default 5005)")
    _help_row("--name <name>", "Display name for a sharer")
    _help_row("--watch", "Re-announce when the folder changes")
    _help_row("--auto", "Keep serving after each pull")
    _help_row("--fast", "Skip compression (fastest on a LAN)")
    _help_row("--skip-guard", "Skip the security scan when sending")
    _help_row("--no-qr", "Do not print the QR code")
    _help_row("--allow-self", "Allow pulling on the same machine")
    _help_row("--to <dir>", "Pull into this folder")
    _help_row("--force", "Overwrite existing files when pulling")

    _help_title("ACROSS NETWORKS")
    _help_row("edr relay start --host 0.0.0.0 --port 8765")
    _help_row("edr create . --non-network --idnew --relay-url http://<relay-ip>:8765")
    _help_row("edr pull Edrnko_<id> --relay-url http://<relay-ip>:8765")

    print()
    print("Aliases: ls=list run=start serve=share send=push init=create st=status")
    print("         recv=receive dir=directory v=version")
    print(f"Sharers are saved in {store}")
    print("Run edr <command> -h for the options of one command.")
    print()
