import sys

VERSION = "0.5.7"

_active_stage = None


def info(message):
    print(f"[INFO] {message}")


def success(message):
    print(f"[SUCCESS] {message}")


def warn(message):
    print(f"[WARN] {message}")


def error(message):
    print(f"[ERROR] {message}", file=sys.stderr)


def connection_info(pin):
    print("\n" + "=" * 35)
    print(f" Connect via this code: {pin}")
    print("=" * 35 + "\n")


def section(title):
    print(f"\n{title}")
    print("-" * len(title))


def key_value(key, value):
    print(f"{key:<14} {value}")


def progress_finish():
    global _active_stage
    if _active_stage:
        print()
        _active_stage = None


def progress(label, percent):
    """Show a single-line stage progress: 'label.... N' then 'done' at 100."""
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


def transfer(action, current, total, path, size=None):
    """Legacy hook — maps file counts to stage progress."""
    if total <= 0:
        progress(f"{action.lower()} files", 100)
        return
    percent = int(current * 100 / total)
    progress(f"{action.lower()} files", percent)


def help_menu():
    print("""
EDR CLI - Project Sharer

Usage:
  edr create [sharer] [folder]   Create a reusable sharer (use 'sharer' keyword)
  edr list                       List saved sharers
  edr dir [id]                   Show the folder used by a sharer
  edr set-dir <id> <folder>      Change the folder used by a sharer
  edr start [id]                 Start a saved sharer
  edr push [id]                  Serve a saved sharer once
  edr pull <ip|code> [options]   Receive files (LAN IP or Edrnko_<id>)
  edr share [folder]             Serve a folder without saving it
  edr relay start                Start a local relay for --non-network sharing
  edr status [id]                Show what a sharer would send
  edr pack [file]                Create a zip bundle
  edr scan [folder]              Run EDR Guard (blocks viruses before share)
  edr ip                         Print this machine's sharing IP
  edr version                    Show the CLI version
  edr doctor                     Show install path / npm conflicts (Windows)
  edr help                       Show this menu

Non-network sharing:
  edr create sharer --non-network --idnew
  edr start <id>                 Wait for pull; share when receiver connects
  edr pull Edrnko_<id>           Pull from anywhere (same relay server)
  edr relay start                Optional — local relay auto-starts on start if needed

Compatibility:
  edr create share               Same as: edr share .
  edr connect sharer --{ip}      Same as: edr pull {ip}

Common options:
  --port <port>                  TCP port to use, default 5005
  --id <id>                      Set the id when creating a sharer
  --non-network                  Share through relay (any network)
  --idnew                        Generate a new random relay id
  --to <dir>                     Receive into a specific target directory
  --force                        Allow receive/pack to overwrite files
  --allow-self                   Allow pull from this same device for testing
  --include-cli                  Include EDR's own Python files in the bundle
  --auto                         Keep a sharer running for repeated pulls
  --once                         Start an auto sharer for one pull only
  --dry-run                      Show share details without opening a socket

Examples:
  edr create sharer . --non-network --idnew
  edr relay start
  edr start 774g475gy4
  edr pull Edrnko_774g475gy4
  edr create . --id app --auto
  edr pull 192.168.1.20 --to ./app-copy

Aliases:
  init=create, ls=list, run=start, serve=share, send=push, recv=receive, st=status
    """)
