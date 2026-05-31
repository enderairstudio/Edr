# EDR Project Sharer

EDR is a command-line project sharing tool. It lets you send a project folder from one machine to another over your LAN or through an optional relay code, with EDR Guard scanning files before transfer.

## What EDR is

- A small CLI for sharing project folders between computers.
- A reusable profile manager for folders you share often.
- A LAN sender/receiver for same-network transfers.
- A relay-code workflow for cross-network transfers when both machines use the same relay.
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
edr create sharer . --non-network --idnew
edr start <id>
edr pull Edrnko_<id>
```

LAN mode:

```bash
edr create sharer . --id myproject
edr start myproject
edr pull <sender-ip>
```

Useful commands:

```bash
edr help
edr list
edr status myproject
edr doctor
edr scan . --report guard-report
```

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
