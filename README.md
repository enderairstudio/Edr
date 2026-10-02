# EDR Project Sharer

EDR is a command-line project sharing tool. It lets you send a project folder from one machine to another over your LAN or through an optional relay code, with EDR Guard scanning files before transfer.

## Desktop app

Run `python edr_gui.py` (or `edr desktop`) for the desktop version. It has folder browsing, Share, Pull, relay, fast-mode, and activity controls while invoking the same EDR transfer commands as the CLI. The optional packaged desktop build uses `pyinstaller build/edr-gui.spec`.

## What EDR is

- A small CLI for sharing project folders between computers.
- A reusable profile manager for folders you share often.
- A LAN sender/receiver for same-network transfers.
- A relay-code workflow for cross-network transfers when both machines use the same relay.
- Streaming LAN transfers so large projects do not need to be loaded into memory before sending.
- A `--fast` mode that skips ZIP compression when raw throughput matters more than smallest payload size.
- Cancellation cleanup: if a pull is interrupted, EDR removes partial files on the receiving machine.
- A safety layer that scans projects with EDR Guard before sharing.
- A packaging target for Windows, macOS, Linux, and npm global installs.

## What EDR is not

- Not Git, GitHub, or a source-control replacement.
- Not cloud storage or automatic backup software.
- Not a public file hosting service.
- Not a security product that guarantees files are safe.
- Not a remote desktop or remote shell tool.
- Not intended for sharing secrets, private keys, credentials, or sensitive production data.

## Install with npm

```bash
npm install -g @enderair/edr
```

Then run:

```bash
edr version
```

Requires Python 3.11+ on the machine:

```powershell
winget install Python.Python.3.11
```

## Downloads

| Platform | File | Install |
|----------|------|---------|
| Windows | `EDR-Setup.exe` | Run installer, or use `START-HERE.cmd` if Smart App Control blocks the EXE |
| macOS | `EDR-Setup.dmg` | Open the disk image, then run the installer |
| Linux | `EDR-Setup.deb` | `sudo apt install ./EDR-Setup.deb`, then run `edr` |

The Windows installer checks for old EDR installs and removes them before copying the new one.

## Quick start

Relay mode:

```bash
# On the relay host, keep this terminal running:
edr relay start --host 0.0.0.0 --port 8765

# On the sharing computer; use the LAN IP printed by relay start:
edr share . --non-network --idnew --relay-url http://<relay-host-lan-ip>:8765

# On the receiving computer, enter the same relay URL:
edr pull Edrnko_<id-shown-by-share> --relay-url http://<relay-host-lan-ip>:8765
```

The relay server must be running on a machine both EDR clients can reach, and both the sender and receiver must use the exact same `--relay-url`. The default `http://127.0.0.1:8765` is for testing on one computer only; on another computer, `127.0.0.1` points back to that other computer, not to the relay host. For computers on the same Wi-Fi, use the relay host's LAN IP. For off-site transfers, run the relay on a public server or configure router port forwarding and firewall access for TCP port 8765. Put a TLS reverse proxy in front for HTTPS; the built-in relay itself serves HTTP. Keep share codes private.

Example for two computers on one Wi-Fi, where the relay host's LAN address is `192.168.1.25`:

```bash
# Relay host terminal
edr relay start --host 0.0.0.0 --port 8765

# Sharing computer
edr share C:/projects/demo --non-network --idnew --relay-url http://192.168.1.25:8765

# Receiving computer (use the code printed by the share command)
edr pull Edrnko_abc123def4 --relay-url http://192.168.1.25:8765
```

LAN mode:

```bash
edr create sharer . --id myproject
edr start myproject
edr pull <sender-ip>
```

Maximum-speed LAN mode:

```bash
edr create sharer . --id myproject --fast
edr start myproject
edr pull <sender-ip>
```

`--fast` stores files in the transfer ZIP without compression. This usually improves speed for large folders, already-compressed files, fast LANs, and slower CPUs. Leave it off when the network is the bottleneck and smaller payloads matter more.

Useful commands:

```bash
edr help
edr list
edr status myproject
edr doctor
edr scan . --report guard-report
```

## Transfer behavior

- LAN sends stream directly to the receiver instead of building the entire payload in RAM.
- Relay mode uploads and downloads by chunks through disk-backed temporary payloads rather than keeping whole projects in RAM. Rooms enforce contiguous chunks, a configurable 10 GB maximum (`EDR_RELAY_MAX_BYTES`), and a one-hour idle cleanup (`EDR_RELAY_IDLE_SECONDS`).
- The relay server handles concurrent status, upload, and download requests.
- Pulls stage data before extraction. If the receiver cancels or extraction fails, EDR removes newly-created partial files on the receiver and never deletes files from the sharer's folder.
- Use `--relay-url` on `create`, `edit`, `start`, `push`, `share`, or `pull` when the relay is not the default localhost URL.

## Network troubleshooting

- Relay on the same Wi-Fi: start `edr relay start --host 0.0.0.0 --port 8765` on the relay computer, then use the LAN URL printed by EDR from both clients. Allow inbound TCP port 8765 through that computer's firewall.
- Relay between different networks: both clients must connect to one publicly reachable relay host. A private LAN IP such as `192.168.x.x` cannot be reached from outside that LAN. Configure public DNS/IP, router port forwarding, and firewall access, or use a hosted server. Use HTTPS through a TLS reverse proxy for internet traffic.
- Never use `127.0.0.1` or `localhost` on a second computer unless the relay itself runs on that computer; those addresses always refer to the current computer.
- Direct LAN sharing uses TCP port 5005 by default. Allow it through the sender's firewall, and confirm both devices are on the same LAN. Use `edr ip` on the sender and pass that address to `edr pull` on the receiver.
- If `edr pull` reports that no sender is waiting, check that the sender is still running and that its code and relay URL match exactly.

## Uninstall

Preview what would be removed:

```bash
edr uninstall
```

Fully remove EDR for the current user:

```bash
edr uninstall -v
```

The full uninstall removes EDR state, known EDR install folders, EDR-specific PATH entries, and the global npm package when installed. It shows `uninstalling EDR from system... 0` up to `100`, then prints `GoodBye :(`.
On Windows, if `edr.exe` is still running from the install folder, EDR schedules that locked folder for deletion right after the command exits.

## Build locally

From a fresh checkout, double-click `build.bat` on Windows. On macOS/Linux, run `./build.sh` in a terminal; if needed, first run `chmod +x build.sh`. Both launchers select the current platform and write release files under `dist/`. The platform scripts and desktop packaging spec are in `build/`.

### Windows requirements and output

```powershell
build.bat
```

The Windows script skips npm publishing. It installs Inno Setup 6 through `winget` when it is missing and builds:

```text
dist\EDR-Setup.exe
dist\EDR-Install.zip
dist\EDR-win64.zip
```

It needs Windows PowerShell, `winget`, and the Windows C# compiler (`csc.exe`). Python is optional for the build itself; if available, it is used for a CLI smoke check and bundling the optional terminal QR dependency.

### macOS and Linux

Run `build.sh` in a terminal:

```bash
./build.sh
```

It selects the current platform automatically. macOS needs `hdiutil`; Linux needs `dpkg-deb`. To invoke the lower-level script directly, run `bash build/build-unix.sh macos` or `bash build/build-unix.sh linux`. Outputs are `dist/EDR-Setup.dmg` and `dist/EDR-Setup.deb`, respectively.

To package just the optional desktop app, install PyInstaller and run `pyinstaller build/edr-gui.spec` from the project root.

## CI

Push a tag `v*`, for example `v0.5.13`, to run [.github/workflows/release.yml](.github/workflows/release.yml) and publish platform installers.
