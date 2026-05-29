# EDR Project Sharer

Share project folders over LAN or relay. Includes **EDR Guard** to block dangerous files before sharing.

## Downloads (releases)

| Platform | File | Install |
|----------|------|---------|
| **Windows** | `EDR-Setup.exe` | Run installer (or `START-HERE.cmd` if Smart App Control blocks the EXE) |
| **macOS** | `EDR-Setup-mac.tar.gz` | Extract → double-click `Install-EDR.command` |
| **Linux** | `EDR-Setup-linux.tar.gz` | Extract → run `./install-edr.sh` |

Requires **Python 3.11+** on the machine (`winget install Python.Python.3.11` on Windows).

## Build locally

### Windows
```powershell
winget install JRSoftware.InnoSetup
powershell -File build.ps1
```
Output: `dist\EDR-Setup.exe`

### macOS / Linux
```bash
chmod +x build-unix.sh
./build-unix.sh macos   # or linux
```

## CI

Push a tag `v*` (e.g. `v0.5.5`) to run [.github/workflows/release.yml](.github/workflows/release.yml) and publish all platform installers.

## Quick start

```bash
edr create sharer --non-network --idnew
edr start <id>
edr pull Edrnko_<id>    # on another machine
```
