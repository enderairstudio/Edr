# EDR Project Sharer

EDR is a command-line project sharing tool. It lets you send a project folder from one machine to another over your LAN or through an optional relay code, with EDR Guard scanning files before transfer.

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
edr relay start --host 0.0.0.0 --port 8765
edr create sharer . --non-network --idnew --relay-url http://<relay-ip>:8765
edr start <id>
edr pull Edrnko_<id> --relay-url http://<relay-ip>:8765
```

Both machines must use the same relay URL. For local testing, EDR can auto-start the default local relay at `http://127.0.0.1:8765`, but cross-machine relay mode needs a reachable relay host.

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
- Relay mode uploads by chunks and deletes stale relay rooms when the sender cancels or times out.
- The relay server handles concurrent status, upload, and download requests.
- Pulls stage data before extraction. If the receiver cancels or extraction fails, EDR removes newly-created partial files on the receiver and never deletes files from the sharer's folder.
- Use `--relay-url` on `create`, `edit`, `start`, `push`, `share`, or `pull` when the relay is not the default localhost URL.

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

### Windows

```powershell
winget install JRSoftware.InnoSetup
powershell -File build.ps1
```

Output:

```text
dist\EDR-Setup.exe
```

`build.ps1` also syncs `package.json` with the CLI version and tries to publish `@enderair/edr` to npm when the version is not already published. To build without npm publishing:
Publishing requires `npm login` and an npm account that owns the package name in `package.json`. For `@enderair/edr`, the account must own or have publish access to the `@enderair` npm scope.

To build without npm publishing:

```powershell
powershell -File build.ps1 -SkipNpmPublish
```

### macOS / Linux

```bash
chmod +x build-unix.sh
./build-unix.sh macos
./build-unix.sh linux
```

Outputs:

```text
dist/EDR-Setup.dmg
dist/EDR-Setup.deb
```

## CI

Push a tag `v*`, for example `v0.5.13`, to run [.github/workflows/release.yml](.github/workflows/release.yml) and publish platform installers.
