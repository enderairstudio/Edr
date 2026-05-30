import sys
import time

VERSION = "0.5.10"

_active_stage = None
_work_scale = 1.0
_stage_state = None

# Display pacing: larger projects spend longer on each stage label.
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
    import sys
    import threading

    progress_finish()
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


_HELP_WIDTH = 70
_HELP_CMD_COL = 38


def _help_rule(char="="):
    print(char * _HELP_WIDTH)


def _help_blank():
    print()


def _help_heading(text):
    print(f"  {text}")


def _help_section(title):
    _help_blank()
    print(f"  >> {title}")
    print(f"  {'-' * (len(title) + 2)}")


def _help_cmd(command, description):
    print(f"    {command:<{_HELP_CMD_COL}}{description}")


def _help_note(text):
    print(f"      {text}")


def _help_flow(steps):
    for index, step in enumerate(steps, start=1):
        print(f"    {index}. {step}")


def help_menu():
    """Print the EDR command reference (ASCII-safe for Windows consoles)."""
    progress_finish()
    store = "%USERPROFILE%\\.edr\\sharers.json"

    _help_rule()
    title = "  EDR Project Sharer"
    version_label = f"v{VERSION}"
    print(title + version_label.rjust(_HELP_WIDTH - len(title)))
    print("  Share folders over LAN or relay  |  EDR Guard scans every transfer")
    print(f"  Saved profiles: {store}")
    _help_rule()

    _help_section("Quick start")
    _help_flow([
        "LAN:   edr create sharer <folder> [--id myapp]  then  edr start myapp",
        "Relay: edr create sharer <folder> --non-network --idnew  then  edr start <id>",
        "Pull:  edr pull <ip>  or  edr pull Edrnko_<id>  (QR shown when sharing)",
    ])

    _help_section("Profiles")
    _help_cmd("edr create [sharer] [folder]", "Save a reusable sharer profile")
    _help_cmd("edr list", "List saved sharers (name + id)")
    _help_cmd("edr edit sharer [id|name]", "Change path, name, LAN/relay, flags")
    _help_cmd("edr rm share --id <id|name>", "Delete a profile")
    _help_cmd("edr dir [id|name]", "Show profile folder path")
    _help_cmd("edr set-dir <id|name> <folder>", "Change folder only")
    _help_cmd("edr status [id|name]", "Files + payload size for a profile")

    _help_section("Share and receive")
    _help_cmd("edr start [id|name]", "Serve until pull completes (+ QR code)")
    _help_cmd("edr push [id|name]", "Serve once from a saved profile")
    _help_cmd("edr share [folder]", "One-off share (no profile)")
    _help_cmd("edr pull <ip|Edrnko_id>", "Download a shared project")
    _help_cmd("edr relay start", "Relay server (cross-network; optional)")

    _help_section("Tools")
    _help_cmd("edr pack [zip]", "Zip a folder locally")
    _help_cmd("edr scan [folder]", "Run EDR Guard (no transfer)")
    _help_cmd("edr scan [folder] --report <file>", "Export Guard report (.json + .txt)")
    _help_cmd("edr ip", "Show this PC's LAN IP")
    _help_cmd("edr doctor", "Health check: Python, ports, relay, PATH")
    _help_cmd("edr version  |  edr v", "Show version")
    _help_cmd("edr help", "Show this menu")

    _help_section("Common flags")
    _help_cmd("--watch", "While waiting, detect folder changes (auto-share)")
    _help_cmd("--auto", "Keep serving after each pull")
    _help_cmd("--non-network", "Relay mode (Edrnko_ code)")
    _help_cmd("--idnew", "New random relay id")
    _help_cmd("--name <name>", "Display name (3s prompt if omitted)")
    _help_cmd("--port <port>", "LAN TCP port (default 5005)")
    _help_cmd("--allow-self", "Allow pull on this same PC")
    _help_cmd("--skip-guard", "Skip security scan when sending")
    _help_cmd("--no-qr", "Hide terminal QR on share")
    _help_cmd("--to <dir>  --force", "Pull destination / overwrite")

    _help_section("Relay vs LAN")
    _help_note("Relay (any network): same EDR_RELAY_URL on every PC")
    _help_cmd("edr create sharer <folder> --non-network --idnew", "Create relay profile")
    _help_cmd("edr pull Edrnko_<id>", "Receive on another machine")
    _help_blank()
    _help_note("LAN (same Wi-Fi): use IP from edr ip")
    _help_cmd("edr pull <ip> --port 5005", "Receive on another device")

    _help_section("Examples")
    _help_cmd("edr create sharer . --id devbox --watch", "Profile with auto-share")
    _help_cmd("edr start devbox", "Share and show pull QR")
    _help_cmd("edr scan . --report guard-report", "Write guard-report.json/.txt")
    _help_cmd("edr pull 192.168.1.20 --to .\\copy --force", "LAN pull into folder")

    _help_rule("-")
    print("  Aliases: v=version  ls=list  run=start  serve=share  send=push")
    print("           init=create  st=status  recv=receive  dir=directory")
    print("  Names: start/edit/rm/dir/status accept id, relay id, or display name")
    _help_rule()
    _help_blank()
