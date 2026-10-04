# EDR Project Sharer

EDR is a command-line tool for sending a project folder from one computer to another, over your LAN or through a relay code, with **EDR Guard** scanning the files on both ends.

```bash
# on the sender                     # on the receiver
edr share .                         edr pull <sender-ip>
```

- [What EDR is / is not](#what-edr-is--is-not)
- [Install](#install)
- [Quick start](#quick-start)
- [Command reference](#command-reference)
- [What gets shared](#what-gets-shared)
- [How a transfer works](#how-a-transfer-works)
- [EDR Guard](#edr-guard)
- [Relay server (Python and Rust)](#relay-server-python-and-rust)
- [Configuration](#configuration)
- [Performance](#performance)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Build locally](#build-locally) · [CI](#ci) · [Uninstall](#uninstall)

## What EDR is / is not

**EDR is**

- A small CLI for sharing project folders between computers, with saved, reusable profiles for folders you share often.
- A LAN sender/receiver (direct TCP) and a relay-code workflow (`Edrnko_<id>`) for machines on different networks.
- Streaming and low-memory on both ends: neither side ever holds the whole project in RAM, and relay transfers reuse one HTTP connection with large chunks.
- All-or-nothing on the receiving side: a pull is staged first, so a cancelled or failed pull leaves your folder exactly as it was (even with `--force`).
- Optional `--fast` mode that skips ZIP compression when raw throughput matters more than payload size.
- Packaged for Windows, macOS, Linux and npm.

**EDR is not**

- Git, GitHub, or a source-control replacement.
- Cloud storage, backup software, or a public file host.
- A guarantee that files are safe. Guard is a tripwire, not an antivirus ([details](#edr-guard)).
- A remote desktop or remote shell.
- A way to share secrets, private keys, credentials or production data. The relay speaks plain HTTP unless you put TLS in front of it ([details](#security-model)).

## Install

```bash
npm install -g @enderair/edr
edr version
```

EDR needs Python 3.11+ on the machine (`winget install Python.Python.3.11` on Windows).

| Platform | File | Install |
|----------|------|---------|
| Windows | `EDR-Setup.exe` | Run the installer, or use `START-HERE.cmd` if Smart App Control blocks the EXE |
| macOS | `EDR-Setup.dmg` | Open the disk image, then run the installer |
| Linux | `EDR-Setup.deb` | `sudo apt install ./EDR-Setup.deb`, then run `edr` (the package is architecture-independent, so it also installs on ARM) |

The Windows installer removes old EDR installs before copying the new one.

## Quick start

**LAN** (same Wi-Fi / network):

```bash
edr create sharer . --id myproject      # save a profile once
edr start myproject                     # serve it (prints a QR code)
edr pull <sender-ip>                    # on the other machine; find the IP with `edr ip`
```

**Relay** (different networks; both sides use the same relay):

```bash
edr relay start --host 0.0.0.0 --port 8765                                   # on a reachable host
edr create sharer . --non-network --idnew --relay-url http://<relay-ip>:8765
edr start <id>
edr pull Edrnko_<id> --relay-url http://<relay-ip>:8765
```

`edr start` prints the exact `edr pull ...` command to run on the other side, including `--relay-url` whenever the relay is not the default `http://127.0.0.1:8765`. For local testing EDR auto-starts a relay on `127.0.0.1:8765`; real cross-machine use needs a relay host that both machines can reach.

**One-off share without a profile:** `edr share [folder]` (add `--non-network` for relay mode).

**Maximum speed on a fast LAN:** add `--fast` to `create`, `start`, `push` or `share`. Files are stored without compression, which usually helps with large folders, already-compressed data and slower CPUs. Leave it off when the network is the bottleneck.

**Keep serving / follow changes:** `--auto` keeps serving after each pull, `--watch` notices folder changes while waiting so the next pull gets the latest files.

## Command reference

| Command | What it does |
|---------|--------------|
| `edr create [sharer] [folder]` | Save a reusable sharer profile |
| `edr list` | List saved profiles |
| `edr edit sharer [id\|name]` | Change path, name, LAN/relay mode, flags |
| `edr rm share --id <id\|name>` | Delete a profile |
| `edr dir [id\|name]` / `edr set-dir <id\|name> <folder>` | Show / change a profile's folder |
| `edr status [id\|name]` | File count and payload size for a profile |
| `edr start [id\|name]` | Serve until a pull completes (shows QR) |
| `edr push [id\|name]` | Serve once from a saved profile |
| `edr share [folder]` | One-off share, no profile |
| `edr pull <ip\|Edrnko_id>` | Download a shared project |
| `edr relay start [--engine auto\|python\|rust]` | Run a relay server |
| `edr pack [zip]` | Zip a folder locally (streams to disk) |
| `edr scan [folder] [--report file]` | Run EDR Guard without transferring (writes `.json` + `.txt` with `--report`) |
| `edr ip` | Show this PC's LAN IP |
| `edr doctor` | Health check: Python, files, ports, disk, relay, PATH |
| `edr uninstall [-v]` | Preview / perform removal |
| `edr version`, `edr help` | Version / help menu |

Aliases: `v`=version, `ls`=list, `run`=start, `serve`=share, `send`=push, `init`=create, `st`=status, `recv`=receive, `dir`=directory. `start`, `edit`, `rm`, `dir` and `status` accept the id, the relay id, or the display name.

**Common flags**

| Flag | Meaning |
|------|---------|
| `--non-network` | Relay mode (`Edrnko_<id>` code) |
| `--idnew` | Generate a new random relay id |
| `--id <id>` | (`create`) Choose the id; relay ids are 4-64 lowercase letters/digits |
| `--relay-url <url>` | Relay shared by sender and receiver |
| `--port <port>` | LAN TCP port (default 5005) |
| `--fast` | Skip ZIP compression |
| `--auto` / `--watch` | Keep serving / auto-detect folder changes |
| `--skip-guard` | Skip the Guard scan when sending |
| `--no-qr` | Don't print the pull QR code |
| `--name <name>` | Display name (asked in a terminal when omitted; never prompts in scripts) |
| `--to <dir>` / `--force` | Pull destination / overwrite existing files |
| `--allow-self` | Allow pulling on the same machine |
| `--include-cli` | Also share EDR's own CLI files (see below) |

## What gets shared

EDR sends everything in the folder except:

- **Anywhere in the tree:** `.edr`, `.git`, `__pycache__`, `venv`, `.venv`, `node_modules`, `.mypy_cache`, `.pytest_cache`, `dist`, `build`, and any file named `project_payload.zip`.
- **Only at the project root:** `python/` and `launcher/` (EDR's own packaging folders). A `bindings/python/` or `src/launcher/` in your project is shared normally.
- **EDR's own source files** (`handler.py`, `share.py`, ...) only when the folder *is* the EDR CLI (it contains `command.py`, `handler.py`, `share.py`, `relay.py` and `guard.py`) and `--include-cli` is not set. Your project's own `handler.py` or `error.py` are always shared.
- **Symlinks that point outside the project.** Links that stay inside the project are shared as regular files.

Files with timestamps before 1980 are accepted (the date is clamped), and files that disappear while bundling are skipped with a warning instead of aborting the share.

## How a transfer works

```
LAN    sender ──TCP stream: header + ZIP──▶ receiver
Relay  sender ──PUT chunks──▶ relay ◀──GET stream── receiver
```

1. **Sender** scans the project with Guard, then streams `header + ZIP`. Small writes are batched into large socket sends. For relay mode the ZIP is built in a temporary file (not RAM) and uploaded from disk in large chunks over one kept-alive connection, with automatic retries on network errors. The chunk size follows the relay's advertised limit (`/v1/health`).
2. **Receiver** streams the payload into a temporary file, so memory stays flat regardless of project size. A download-size cap and an idle timeout (`EDR_RECV_TIMEOUT`, default 600 s) protect against runaway or stalled senders.
3. **Receiver** runs Guard over the archive, then validates *every* member (path traversal, conflicts, zip-bomb size/ratio, free disk space) **before writing anything**.
4. Files are written to a private staging folder and then moved into place. Anything a move replaces is parked first, so a failure or Ctrl+C at any point restores the folder exactly as it was, and a brand-new target folder is removed again.
5. **Relay rooms** are only marked *consumed* once the whole payload was delivered. If the receiver disconnects mid-download the payload stays available for a retry, and the sender is never told the share succeeded when it didn't. A sender that polls late still learns the transfer finished.
6. With `--auto`, a receiver that aborts does not stop the sender; it logs the aborted pull and keeps serving.

Cancelling a share (Ctrl+C) deletes its relay room. EDR never deletes files from the sharer's folder.

## EDR Guard

Guard runs on the sender before sharing and again on the receiver before extraction. It blocks an archive or folder when it finds:

- **Blocked file types:** `.exe .msi .msp .msm .scr .com .pif .cpl .bat .cmd .ps1 .psm1 .vbs .vbe .ws .wsf .wsc .wsh .hta .jar .dll .sys .drv .ocx .reg .inf .lnk .iso .img`. `edr.cmd` / `edr.ps1` are allowed **only at the project root**.
- **Disguises:** double extensions (`invoice.pdf.exe`), trailing dots/spaces that Windows would strip (`evil.exe.`), backslash paths, NTFS stream syntax (`a.txt:b.exe`, Windows only), and executables hiding behind a document extension (`MZ` header).
- **Suspicious content** in the first 512 KiB of each file: encoded PowerShell, `Invoke-Expression`, `DownloadString`, `WScript.Shell`, `regsvr32`/`mshta`/`rundll32` one-liners, shadow-copy deletion, in-memory loaders and the EICAR test string.
- **EDR's own state folder** (`.edr/`) inside a shared project. EDR never sends it, so an archive containing one is trying to plant a sharer profile on the receiver.

Everything in an archive is scanned, including `__MACOSX/` entries, and archives are scanned by reading only the scan window of each member, so a zip bomb cannot exhaust memory during the scan.

Guard is pattern matching on file names and the start of each file. It does not detect arbitrary malware, scripts that avoid the listed patterns, or anything past the first 512 KiB. Use `--skip-guard` only for folders you fully trust; `edr scan . --report guard-report` shows what Guard sees without transferring.

## Relay server (Python and Rust)

A relay is only needed when sender and receiver cannot reach each other directly. Both machines must use the same relay URL.

Two interchangeable implementations speak the same HTTP protocol:

| | Python relay | Rust relay (`relay-rs/`) |
|---|---|---|
| Start | `edr relay start --engine python` | `edr relay start --engine rust` or run `edr-relay` directly |
| Needs | Python (already installed with EDR) | nothing: one self-contained binary, no Python runtime |
| Concurrency | one thread per connection | async I/O, no thread per connection |
| Best for | local testing, the auto-started `127.0.0.1` relay | a relay you leave running on a server |

`edr relay start` (default `--engine auto`) uses the Rust binary when it finds one (`EDR_RELAY_BIN`, `edr-relay` on `PATH`, or next to the CLI) and the built-in Python relay otherwise. Both pass the same conformance test suite.

**Build the Rust relay**

```bash
cargo build --release --manifest-path relay-rs/Cargo.toml
# binary: relay-rs/target/release/edr-relay   (edr-relay.exe on Windows)
export EDR_RELAY_BIN=$PWD/relay-rs/target/release/edr-relay    # optional: let `edr relay start` find it
edr-relay --host 0.0.0.0 --port 8765
```

**Run it as a service (systemd example)**

```ini
[Unit]
Description=EDR relay
After=network.target

[Service]
ExecStart=/usr/local/bin/edr-relay --host 127.0.0.1 --port 8765
Environment=EDR_RELAY_MAX_BYTES=2147483648
Restart=on-failure
DynamicUser=yes

[Install]
WantedBy=multi-user.target
```

**HTTPS in front (Caddy example)**, then use `--relay-url https://relay.example.com` on every machine:

```
relay.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

### Security model

- A room code (`Edrnko_` + 10 random characters) is the only credential. Anyone who has it can pull that project, and the first successful pull consumes it.
- The relay stores payloads unencrypted on its disk while they are in transit and speaks plain HTTP. Terminate TLS in front of it for anything that crosses the internet, and never share secrets.
- Receivers still run Guard on everything they download, so a hostile sender or relay cannot get a blocked file type or a path-traversal archive extracted.
- Limits keep a public relay from filling its disk: per-project size, per-request size, maximum concurrent rooms, idle expiry, and a strict "chunk must lie inside the declared size" rule.
- Spool files live in a private temp directory that is deleted on exit; directories abandoned by a killed relay are purged by the next one after two hours.
- The relay has no built-in slow-client protection beyond request timeouts; for a public relay, run it behind a reverse proxy such as Caddy or nginx.

Relay API (for reference):

| Request | Purpose |
|---------|---------|
| `GET /v1/health` | `{"ok":true,"max_chunk_bytes":N}` |
| `POST /v1/rooms/{id}/wait` | Sender registers |
| `POST /v1/rooms/{id}/request` | Receiver asks for the share |
| `PUT /v1/rooms/{id}` + `X-EDR-Offset`, `X-EDR-Total` | Upload one chunk |
| `GET /v1/rooms/{id}/status` | `{"ready","requested","waiting","consumed","bytes"}` |
| `GET /v1/rooms/{id}` | Stream the payload (consumes the room once fully delivered) |
| `DELETE /v1/rooms/{id}` | Cancel and delete |

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `EDR_RELAY_URL` | `http://127.0.0.1:8765` | Default relay URL |
| `EDR_RELAY_BIN` | – | Path to the `edr-relay` binary |
| `EDR_RECV_TIMEOUT` | `600` | Seconds a receiver waits for the sender to send anything (`0` disables) |
| `EDR_MAX_EXTRACT_BYTES` | 20 GiB | Max download / extracted size |
| `EDR_MAX_COMPRESSION_RATIO` | `300` | Zip-bomb ratio limit for members over 10 MiB |
| `EDR_PACED_PROGRESS` | off | `1` re-enables the old "animated" progress that holds each stage on screen (slower) |
| `EDR_DEBUG` | off | `1` shows Python tracebacks instead of one-line errors |
| `EDR_RELAY_MAX_BYTES` | 8 GiB | Relay: max size of one shared project |
| `EDR_RELAY_MAX_CHUNK_BYTES` | 16 MiB | Relay: max size of one upload request |
| `EDR_RELAY_MAX_ROOMS` | 1024 | Relay: max concurrent rooms |
| `EDR_RELAY_IDLE_SECONDS` | 3600 | Relay: drop rooms idle this long |
| `EDR_RELAY_SPOOL_DIR` | private temp dir | Relay: where payloads are spooled |

Saved profiles live in `~/.edr/sharers.json` (`%USERPROFILE%\.edr\sharers.json` on Windows) and are written atomically. EDR does not read profiles from the folder you happen to be in.

## Performance

Measured in a Linux sandbox, same machine for every row; the relay rows use a 200 MB incompressible project. Treat the numbers as relative, not absolute.

| | Before | Now |
|---|---|---|
| LAN pull of a 3 MB / 11-file project (loopback) | 11.3 s | 0.28 s |
| Relay transfer of 200 MB, end to end | 44.5 s | 7.0 s |
| Peak memory, sender / receiver (200 MB project) | 405 MiB / 977 MiB | 30 MiB / 26 MiB |
| Relay upload of 20 MiB over a simulated 50 ms round-trip link | 8.3 s | 0.4 s |
| Guard scanning a 3.4 MiB zip that inflates to 768 MiB | 1556 MiB RAM | 18 MiB RAM |

The biggest single change: progress used to sleep so each stage stayed on screen for a minimum time, adding seconds to every transfer regardless of project size. It now follows the real work (`EDR_PACED_PROGRESS=1` brings the old behavior back). The Python and Rust relays performed identically in the 200 MB single-transfer test; the Rust relay's benefit is not needing a Python runtime and using async I/O on a shared server, which that test did not measure.

## Troubleshooting

Run `edr doctor` first. It checks Python, EDR's files, free disk space, port availability, the relay, your LAN IP, saved profiles, `PATH` and the QR library.

| Symptom | Likely cause / fix |
|---------|--------------------|
| `Cannot reach relay at ...` | Start one with `edr relay start`, or fix `--relay-url` / `EDR_RELAY_URL` on **both** machines. |
| Receiver waits and says no sender is sharing | The sender isn't running `edr start <id>`, or it is using a different relay. Copy the pull command EDR printed. |
| Pull hangs or is refused on LAN | Open TCP port 5005 (or your `--port`) in the sender's firewall; check the IP with `edr ip`. |
| `Cannot start the relay on ...: Address already in use` | Something else holds the port. Pick another with `--port`. |
| `EDR Guard blocked ...` | Read the reason (file type, pattern, or archive trick). Inspect with `edr scan <folder> --report guard-report`. |
| `Invalid relay id` | Relay ids are 4-64 lowercase letters/digits. Drop `--id` or use `--idnew`. |
| `... exists. Use --force to overwrite it.` | Pull into a new folder with `--to`, or add `--force` (it restores the original if the pull fails). |
| Need the full Python traceback | Set `EDR_DEBUG=1`. |

## Development

| Path | Role |
|------|------|
| `command.py` | Entry point |
| `handler.py` | CLI parsing, profile store, commands |
| `share.py` | File selection, bundling, LAN/relay send and receive, atomic extraction |
| `relay.py` | Relay client and the built-in Python relay server |
| `relay-rs/` | Rust relay (`edr-relay`) |
| `guard.py` | EDR Guard |
| `watch.py`, `qrterm.py`, `doctor_checks.py`, `print.py`, `error.py` | Folder watcher, QR output, health checks, console output, error type |
| `tests/` | Unit and end-to-end tests |
| `installer/`, `launcher/`, `scripts/`, `winget/`, `bin/` | Packaging |

**Tests** (standard library only):

```bash
python -m unittest discover -s tests            # everything; Rust relay tests run if the binary is built
python -m unittest tests.test_relay_protocol -v # the same protocol suite against the Python and Rust relays
cargo test --manifest-path relay-rs/Cargo.toml  # Rust unit tests
```

**Adding a Python module** means updating several lists: `CLI_FILES` in `share.py`, `check_handler_files` in `doctor_checks.py`, `files` in `package.json`, the file lists in `build.ps1` / `build-unix.sh` / `scripts/install-*.sh`, and the installer scripts. `AGENTS.md` has the full checklist and the project conventions.

## Build locally

### Windows

```powershell
winget install JRSoftware.InnoSetup
powershell -File build.ps1
```

Output: `dist\EDR-Setup.exe`.

`build.ps1` also syncs `package.json` with the CLI version and tries to publish `@enderair/edr` to npm when that version is not published yet. Publishing needs `npm login` and an account with access to the `@enderair` scope. To build without publishing:

```powershell
powershell -File build.ps1 -SkipNpmPublish
```

### macOS / Linux

```bash
chmod +x build-unix.sh
./build-unix.sh macos      # dist/EDR-Setup.dmg
./build-unix.sh linux      # dist/EDR-Setup.deb
```

## CI

- Pushing a tag `vX.Y.Z` (for example `v0.5.16`) runs [`release.yml`](.github/workflows/release.yml) and publishes the platform installers.
- [`relay-rs/ci/relay-rs.yml`](relay-rs/ci/relay-rs.yml) is a ready-made workflow that builds and tests the Rust relay on Linux, Windows and macOS, runs the protocol conformance suite and uploads the binaries as artifacts. It is not active until you move it to `.github/workflows/` (`git mv relay-rs/ci/relay-rs.yml .github/workflows/relay-rs.yml`); it does not touch the release workflow.

## Uninstall

Preview what would be removed:

```bash
edr uninstall
```

Fully remove EDR for the current user:

```bash
edr uninstall -v
```

The full uninstall removes EDR state, known EDR install folders, EDR-specific `PATH` entries and the global npm package when installed. On Windows, if `edr.exe` is still running from the install folder, EDR schedules that locked folder for deletion right after the command exits.
