"""Self-update: `edr update --latest` installs the newest GitHub release.

Flow: ask the GitHub API for the latest release tag, compare it with the
running version, download the release (checksum-verified when GitHub
provides one), stage it in a temp folder, run the staged copy once as a
self-test, swap the files in with a backup of every replaced file, run the
installed copy once more, and restore the backups if anything goes wrong.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib import error as urlerror
from urllib import request as urlrequest

import error as e
import print as p

REPO = "enderairstudio/Edr"
DEFAULT_API = f"https://api.github.com/repos/{REPO}"
# Windows ships a prebuilt folder (edr.exe launcher + app/). macOS and Linux
# have no extractable archive (.dmg / .deb), so they take the release's
# source snapshot and copy the files listed in build-unix.sh.
WINDOWS_ASSET = "EDR-win64.zip"
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
API_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 30
SELF_TEST_TIMEOUT = 60
BACKUP_SUFFIX = ".edr-old"
NEW_SUFFIX = ".edr-new"
NOT_APP_SCRIPTS = {"setup_installer.py", "version_info.py"}


class Install:
    def __init__(self, root, app_dir):
        self.root = root
        self.app_dir = app_dir


def _is_windows():
    return sys.platform == "win32"


def api_base():
    override = os.environ.get("EDR_UPDATE_API", "").strip().rstrip("/")
    return override or DEFAULT_API


def parse_version(text):
    """'v0.5.17' -> (0, 5, 17). Trailing zeros are ignored (1.0 == 1.0.0).
    Returns None when the text does not start with a version number."""
    match = re.match(r"^[vV]?(\d+(?:\.\d+)*)", str(text or "").strip())
    if not match:
        return None
    parts = [int(piece) for piece in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _describe(err):
    reason = getattr(err, "reason", None) or err
    return str(reason)


def _require_safe_url(url):
    if url.startswith("https://"):
        return
    if os.environ.get("EDR_UPDATE_API") and url.startswith("http://"):
        return
    raise e.CliError(f"Refusing to download over an insecure URL: {url}")


def _open(url, headers=None, timeout=API_TIMEOUT):
    request = urlrequest.Request(url, headers={"User-Agent": f"edr-update/{p.VERSION}", **(headers or {})})
    return urlrequest.urlopen(request, timeout=timeout)


def fetch_latest_release():
    url = f"{api_base()}/releases/latest"
    _require_safe_url(url)
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        with _open(url, headers) as response:
            data = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
    except urlerror.HTTPError as err:
        if err.code == 404:
            raise e.CliError(f"No published release found at github.com/{REPO}.") from err
        if err.code in {403, 429}:
            raise e.CliError(
                "GitHub refused the request (rate limit?). Try again later, or set GITHUB_TOKEN."
            ) from err
        raise e.CliError(f"GitHub API returned HTTP {err.code}.") from err
    except (urlerror.URLError, TimeoutError, OSError) as err:
        raise e.CliError(f"Could not reach GitHub to check for updates: {_describe(err)}") from err
    except ValueError as err:
        raise e.CliError("GitHub sent an unreadable response.") from err
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise e.CliError("GitHub's latest-release response had no tag.")
    return data


def locate_install():
    """Find the installed copy this process runs from, or explain why it cannot be updated."""
    app_dir = Path(__file__).resolve().parent
    if "node_modules" in {part.lower() for part in app_dir.parts}:
        raise e.CliError("EDR was installed with npm. Update it with: npm install -g @enderair/edr@latest")
    if app_dir.name != "app":
        raise e.CliError(
            f"EDR is running from a source folder ({app_dir}), not an installed copy. "
            "Update it with: git pull"
        )
    root = app_dir.parent
    if not (os.access(app_dir, os.W_OK) and os.access(root, os.W_OK)):
        if _is_windows():
            raise e.CliError(f"No write access to {root}. Run again from an elevated terminal, or reinstall with EDR-Setup.exe.")
        raise e.CliError(f"No write access to {root}. Run again with: sudo edr update --latest")
    return Install(root, app_dir)


def _asset_digest(asset):
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        value = digest.split(":", 1)[1].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    return None


def _pick_download(release):
    """-> (url, sha256 or None, kind) where kind is 'windows' or 'source'."""
    if _is_windows():
        for asset in release.get("assets") or []:
            if asset.get("name") == WINDOWS_ASSET and asset.get("browser_download_url"):
                return asset["browser_download_url"], _asset_digest(asset), "windows"
        raise e.CliError(f"Release {release['tag_name']} has no {WINDOWS_ASSET} file to install.")
    url = release.get("zipball_url")
    if not url:
        raise e.CliError(f"Release {release['tag_name']} has no source archive to install.")
    return url, None, "source"


def download(url, dest, expected_sha256=None, on_progress=None):
    _require_safe_url(url)
    digest = hashlib.sha256()
    received = 0
    try:
        with _open(url, timeout=DOWNLOAD_TIMEOUT) as response, Path(dest).open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            if total > MAX_DOWNLOAD_BYTES:
                raise e.CliError("The update download is unexpectedly large; aborted.")
            while True:
                block = response.read(256 * 1024)
                if not block:
                    break
                received += len(block)
                if received > MAX_DOWNLOAD_BYTES:
                    raise e.CliError("The update download is unexpectedly large; aborted.")
                out.write(block)
                digest.update(block)
                if total and on_progress:
                    on_progress(min(99, received * 100 // total))
            if total and received != total:
                raise e.CliError("The update download was cut short. Try again.")
    except e.CliError:
        raise
    except urlerror.HTTPError as err:
        raise e.CliError(f"Download failed: HTTP {err.code}") from err
    except (urlerror.URLError, TimeoutError, OSError) as err:
        raise e.CliError(f"Download failed: {_describe(err)}") from err
    if expected_sha256 and digest.hexdigest() != expected_sha256:
        raise e.CliError("The downloaded update does not match GitHub's checksum; nothing was installed.")


def _clean_member(name):
    """Archive member name -> PurePosixPath, rejecting anything that could escape the folder."""
    parts = [piece for piece in name.replace("\\", "/").split("/") if piece not in {"", "."}]
    if not parts or ".." in parts or re.match(r"^[A-Za-z]:", parts[0]):
        raise e.CliError(f"The update archive contains an unsafe path: {name}")
    return PurePosixPath(*parts)


def _read_members(archive):
    """-> list of (ZipInfo, cleaned path) for files only, with a size cap."""
    members = []
    total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise e.CliError("The update archive is unexpectedly large; aborted.")
        members.append((info, _clean_member(info.filename)))
    return members


def _strip_common_folder(members):
    firsts = {path.parts[0] for _, path in members}
    if len(firsts) == 1 and all(len(path.parts) > 1 for _, path in members):
        return [(info, PurePosixPath(*path.parts[1:])) for info, path in members]
    return members


def _write_member(archive, info, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info) as source, target.open("wb") as out:
        shutil.copyfileobj(source, out)


def stage_windows(archive_path, stage_dir):
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _strip_common_folder(_read_members(archive))
            for info, rel in members:
                _write_member(archive, info, stage_dir / Path(*rel.parts))
    except zipfile.BadZipFile as err:
        raise e.CliError("The downloaded update is not a valid zip file.") from err


def _unix_app_files(archive, members):
    """File names to install, read from build-unix.sh inside the release (the
    list that CI uses to build the .deb / .dmg). Falls back to every top-level
    script that is not installer tooling."""
    by_path = {path.as_posix(): info for info, path in members}
    script = by_path.get("build-unix.sh")
    if script is not None:
        text = archive.read(script).decode("utf-8", errors="replace")
        match = re.search(r"^APP_FILES=\(([^)]*)\)", text, re.MULTILINE)
        if match:
            names = match.group(1).split()
            if names and all(re.fullmatch(r"[A-Za-z0-9_]+\.py", name) for name in names):
                missing = [name for name in names if name not in by_path]
                if missing:
                    raise e.CliError(f"The release is missing app files: {', '.join(missing)}")
                return names
    return sorted(
        name for name in by_path
        if "/" not in name and name.endswith(".py") and name not in NOT_APP_SCRIPTS
    )


def stage_source(archive_path, stage_dir):
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _strip_common_folder(_read_members(archive))
            by_path = {path.as_posix(): info for info, path in members}
            for name in _unix_app_files(archive, members):
                _write_member(archive, by_path[name], stage_dir / "app" / name)
    except zipfile.BadZipFile as err:
        raise e.CliError("The downloaded update is not a valid zip file.") from err


def run_version(app_dir, cwd, stage=False):
    """Run `command.py version` from app_dir; -> the printed version text, or None on failure."""
    if not sys.executable:
        return None
    script = Path(app_dir) / "command.py"
    if not script.is_file():
        return None
    env = dict(os.environ)
    if stage:
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, str(script), "version"],
            capture_output=True,
            text=True,
            timeout=SELF_TEST_TIMEOUT,
            cwd=str(cwd),
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if result.returncode != 0 or not lines:
        return None
    return lines[-1]


def _staged_files(stage_dir):
    return sorted(path.relative_to(stage_dir) for path in Path(stage_dir).rglob("*") if path.is_file())


def _remove_quietly(path):
    try:
        Path(path).unlink()
    except OSError:
        pass


def clean_stale_backups(install):
    for base in (install.root, install.app_dir):
        for stale in base.glob(f"*{BACKUP_SUFFIX}"):
            _remove_quietly(stale)


def apply_files(stage_dir, install_root, on_progress=None):
    """Copy staged files over the install. Every replaced file is renamed to
    <name>.edr-old first (Windows lets a running edr.exe be renamed but not
    overwritten), so a failure restores the install exactly as it was.
    Returns the list of (target, backup or None) needed to roll back."""
    done = []
    files = _staged_files(stage_dir)
    try:
        for index, rel in enumerate(files, start=1):
            source = Path(stage_dir) / rel
            target = Path(install_root) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir() and not target.is_symlink():
                raise e.CliError(f"{target} is a folder; cannot replace it with a file.")
            temp = target.with_name(target.name + NEW_SUFFIX)
            try:
                shutil.copyfile(source, temp)
                backup = None
                if target.exists() or target.is_symlink():
                    shutil.copymode(target, temp)
                    backup = target.with_name(target.name + BACKUP_SUFFIX)
                    _remove_quietly(backup)
                    os.replace(target, backup)
                    done.append((target, backup))
                os.replace(temp, target)
                if backup is None:
                    done.append((target, None))
            finally:
                _remove_quietly(temp)
            if on_progress:
                on_progress(min(99, index * 100 // len(files)))
    except BaseException:
        rollback(done)
        raise
    return done


def rollback(done):
    for target, backup in reversed(done):
        try:
            if backup is not None:
                os.replace(backup, target)
            else:
                target.unlink()
        except FileNotFoundError:
            pass
        except OSError as err:
            p.warn(f"Could not restore {target}: {err}")


def discard_backups(done):
    for _target, backup in done:
        if backup is not None:
            _remove_quietly(backup)


def run_update(check_only=False, force=False):
    current = p.VERSION
    p.info(f"Current version: {current}")

    install = None if check_only else locate_install()

    release = fetch_latest_release()
    tag = str(release["tag_name"])
    latest = parse_version(tag)
    if latest is None:
        raise e.CliError(f"The latest release tag '{tag}' is not a version number.")
    p.info(f"Latest release:  {tag}")

    here = parse_version(current)
    newer = here is None or latest > here
    if check_only:
        if newer:
            p.info(f"Update available: {current} -> {tag}. Run: edr update --latest")
        else:
            p.success(f"EDR is up to date ({current}).")
        return 0
    if not newer and not force:
        if latest == here:
            p.success(f"EDR is up to date ({current}).")
        else:
            p.success(f"EDR {current} is newer than the latest release ({tag}); nothing to do.")
        return 0

    url, sha256, kind = _pick_download(release)
    workdir = Path(tempfile.mkdtemp(prefix="edr-update-"))
    try:
        archive_path = workdir / "release.zip"
        stage_dir = workdir / "stage"
        stage_dir.mkdir()

        p.progress("downloading update", 0)
        download(url, archive_path, sha256, on_progress=lambda pct: p.progress("downloading update", pct))
        p.progress("downloading update", 100)

        if kind == "windows":
            stage_windows(archive_path, stage_dir)
        else:
            stage_source(archive_path, stage_dir)

        staged = run_version(stage_dir / "app", workdir, stage=True)
        if staged is None:
            raise e.CliError("The downloaded update failed its self-test; nothing was changed.")
        staged_version = parse_version(staged)
        if staged_version is None:
            raise e.CliError(f"The downloaded update reported an unreadable version ('{staged}'); nothing was changed.")
        if not force and here is not None and staged_version <= here:
            raise e.CliError(
                f"Release {tag} contains EDR {staged}, which is not newer than {current}; nothing was changed. "
                "(The release tag and the VERSION inside it disagree.)"
            )
        if staged_version != latest:
            p.warn(f"Release tag {tag} contains EDR {staged}.")

        clean_stale_backups(install)
        p.progress("installing update", 0)
        done = apply_files(stage_dir, install.root, on_progress=lambda pct: p.progress("installing update", pct))
        p.progress("installing update", 100)

        installed = run_version(install.app_dir, workdir)
        if installed is None or parse_version(installed) != staged_version:
            rollback(done)
            raise e.CliError("The updated install failed its self-test; the previous version was restored.")
        discard_backups(done)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    p.success(f"Updated EDR {current} -> {installed}.")
    p.info("Open a new terminal if `edr version` still shows the old version.")
    return 0
