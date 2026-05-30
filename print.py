import sys
import time

VERSION = "0.5.9"

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


def help_menu():
    print(f"""
EDR CLI - Project Sharer  (v{VERSION})

Sharer setup (saved profiles in %USERPROFILE%\\.edr\\sharers.json):
  edr create [sharer] [folder]     Create a sharer (keyword 'sharer' is optional)
  edr list                         List sharers (shows display name + id)
  edr edit sharer [id|name]        Edit folder, name, LAN/relay, ports, flags
  edr rm share --id <id|name>      Delete a saved sharer
  edr dir [id|name]                Print a sharer's folder path
  edr set-dir <id|name> <folder>   Change only the folder
  edr status [id|name]             Show file count and payload size

Share and receive:
  edr start [id|name]              Serve a saved sharer (waits until pull finishes)
  edr push [id|name]               Serve a saved sharer once
  edr pull <ip|Edrnko_id>          Receive a project (LAN IP or relay code)
  edr share [folder]               One-off share without saving a profile
  edr relay start                  Run relay server (for --non-network / cross-network)

Other:
  edr pack [zip]                   Zip a folder locally
  edr scan [folder]                Run EDR Guard only (no transfer)
  edr scan [folder] --report out   Export Guard report (out.json + out.txt)
  edr ip                           Show this PC's LAN IP for edr pull <ip>
  edr version                      Show CLI version
  edr v                            Same as edr version (short alias)
  edr doctor                       Health checks: Python, ports, relay, PATH, disk
  edr help                         Show this menu

Names:
  edr create sharer . --name MyGame          Set a display name
  edr create sharer . --name-MyGame          Same (--name- prefix form)
  (no --name)                                3s countdown to type a name, or stay nameless
  start / edit / rm / dir / status           Accept id, relay id, or display name

Relay sharing (any network, same relay URL on all PCs):
  edr create sharer <folder> --non-network --idnew [--name X] [--allow-self]
  edr start <id|name>                          Stays up until receiver finishes download
  edr pull Edrnko_<id>                         On another PC (set EDR_RELAY_URL if needed)
  edr relay start                              Optional; localhost relay auto-starts on start

LAN sharing (same Wi-Fi / Ethernet):
  edr create sharer <folder> [--name X] [--id myapp]
  edr start <id|name>
  edr pull <ip> --port 5005                    On another device on the LAN

Edit sharer (only pass what you want to change):
  edr edit sharer <id|name> --path <folder>
  edr edit sharer <id|name> --name <name>      Use --name with empty value to clear name
  edr edit sharer <id|name> --network          Switch to LAN
  edr edit sharer <id|name> --non-network      Switch to relay
  edr edit sharer <id|name> --non-network --idnew   New Edrnko_ share code
  edr edit sharer <id|name> --port <port> --auto --watch --allow-self --skip-guard
  edr edit sharer <id|name> --no-auto --no-watch --no-allow-self --no-skip-guard

Create / share options:
  --port <port>        TCP port (default 5005)
  --name <name>        Display name (3s prompt if omitted on create)
  --id <id>            Sharer id (LAN mode; auto-generated if omitted)
  --non-network        Use relay (Edrnko_ code) instead of LAN IP
  --idnew              New random relay id (create or edit)
  --allow-self         Allow pull from this same PC (testing)
  --skip-guard         Skip EDR Guard scan when sending
  --include-cli        Include EDR's own Python files in the bundle
  --auto               Keep serving after each pull (profile or --auto on start)
  --watch              Auto-detect folder changes while waiting (next pull gets latest)
  --once               One pull only when profile has auto enabled
  --dry-run            Show plan without opening a socket
  --no-qr              Do not print a terminal QR for the pull command

Pull / pack options:
  --to <dir>           Extract into a specific folder
  --force              Overwrite existing files

Compatibility:
  edr create share                 Same as: edr share .
  edr connect sharer --<ip>        Same as: edr pull <ip>
  edr rm share --id-<id>           Same as: edr rm share --id <id>

Examples:
  edr create sharer C:\\Projects\\MyGame --non-network --idnew --name MyGame
  edr start MyGame
  edr pull Edrnko_abc123xyz --to C:\\Downloads\\MyGame-copy
  edr edit sharer MyGame --path C:\\Projects\\MyGame-v2
  edr rm share --id MyGame
  edr create sharer . --id devbox --auto --watch
  edr start devbox --watch
  edr scan . --report guard-report
  edr pull 192.168.1.20 --port 5005 --allow-self

Aliases:
  init=create   ls=list   run=start   serve=share   send=push
  recv=receive  st=status   v=version   directory=dir   setdir=set-dir
  remove=rm   delete=rm   clone=pull (receive)

Watch mode (auto-share):
  Watches the project folder while edr start is waiting. When files change,
  the next pull receives the latest bundle. Use --watch on create, start, or share.
    """)
