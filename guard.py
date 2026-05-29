"""EDR Guard — blocks dangerous files and known malicious patterns before share/receive."""

import re
from pathlib import Path

import error as e
import print as p

# Extensions that must never be shared (executables / script droppers).
BLOCKED_EXTENSIONS = {
    ".exe", ".msi", ".msp", ".msm", ".scr", ".com", ".pif", ".cpl",
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
    ".ws", ".wsf", ".wsc", ".wsh", ".hta", ".jar", ".dll", ".sys",
    ".drv", ".ocx", ".reg", ".inf", ".lnk", ".iso", ".img",
}

# Extensions allowed only when the inner name is not disguised (e.g. file.pdf.exe).
SUSPICIOUS_DOUBLE_EXT = re.compile(
    r"\.(pdf|doc|docx|xls|xlsx|txt|jpg|png|zip|rar|mp4|mp3)\.(exe|scr|bat|cmd|ps1|vbs|js|jar|dll)$",
    re.IGNORECASE,
)

# Content signatures (hex or text) seen in malware / droppers.
BINARY_SIGNATURES = [
    b"MZ",  # PE — checked only when extension is not a known binary allowlist
]

# Text patterns in scripts / payloads (case-insensitive).
TEXT_PATTERNS = [
    rb"powershell\s+-(enc|e)\s+",
    rb"frombase64string",
    rb"iex\s*\(",
    rb"invoke-expression",
    rb"downloadstring\s*\(",
    rb"downloadfile\s*\(",
    rb"wscript\.shell",
    rb"creatobject\s*\(\s*[\"']shell",
    rb"autoopen\s*\(",
    rb"cmd\.exe\s+/c",
    rb"regsvr32\s+/",
    rb"mshta\s+",
    rb"rundll32\s+",
    rb"schtasks\s+/create",
    rb"vssadmin\s+delete\s+shadows",
    rb"bcdedit\s+/set",
    rb"reflection\.assembly",
    rb"virtualalloc",
    rb"write-processmemory",
    rb"EICAR-STANDARD-ANTIVIRUS-TEST-FILE",
]

# Max bytes to scan per file for content (keeps large projects fast).
MAX_SCAN_BYTES = 512 * 1024

# Legitimate EDR launcher scripts (project root only).
ALLOWED_FILENAMES = {"edr.cmd", "edr.ps1"}


class ThreatFound(Exception):
    def __init__(self, path, reason):
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def scan_path(path: Path, archive_name=None):
    """Raise ThreatFound if a single file is unsafe."""
    name = archive_name or path.name
    lower = name.lower()

    if SUSPICIOUS_DOUBLE_EXT.search(lower):
        raise ThreatFound(name, "double extension disguise")

    suffix = Path(lower).suffix
    if suffix in BLOCKED_EXTENSIONS:
        if Path(name).name.lower() not in ALLOWED_FILENAMES:
            raise ThreatFound(name, f"blocked file type ({suffix})")

    try:
        size = path.stat().st_size
    except OSError:
        return

    if size == 0:
        return

    read_len = min(size, MAX_SCAN_BYTES)
    try:
        data = path.read_bytes()[:read_len]
    except OSError:
        return

    _scan_content(name, data, suffix)


def _scan_content(name, data: bytes, suffix: str):
    lower_name = name.lower()

    # PE binary hidden under a safe-looking extension
    if data.startswith(b"MZ") and suffix not in {".exe", ".dll", ".sys", ".scr", ".com"}:
        if suffix not in BLOCKED_EXTENSIONS:
            raise ThreatFound(name, "executable content in non-executable file")

    for pattern in TEXT_PATTERNS:
        if pattern.lower() in data.lower():
            raise ThreatFound(name, "malicious pattern detected")

    # High entropy + MZ in tiny scripts
    if suffix in {".py", ".txt", ".json", ".md", ".html", ".htm"} and data.startswith(b"MZ"):
        raise ThreatFound(name, "embedded executable header in text file")


def scan_project_files(file_paths, on_progress=None):
    """
    Scan an iterable of (path, archive_name) before sharing.
    Returns count scanned. Raises ThreatFound on first hit.
    """
    items = list(file_paths)
    total = len(items) or 1
    for index, (path, archive_name) in enumerate(items, start=1):
        scan_path(Path(path), archive_name=archive_name.as_posix() if hasattr(archive_name, "as_posix") else archive_name)
        if on_progress:
            on_progress(int(index * 100 / total))
    return len(items)


def scan_project(root_dir=".", include_cli=False, on_progress=None):
    from share import iter_project_files

    files = list(iter_project_files(root_dir, include_cli))
    return scan_project_files(files, on_progress=on_progress)


def scan_zip_buffer(buffer, on_progress=None):
    """Scan zip members before extract; raises ThreatFound."""
    import io
    import zipfile

    with zipfile.ZipFile(buffer) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        total = len(members) or 1
        for index, member in enumerate(members, start=1):
            if member.filename.startswith("__MACOSX"):
                continue
            lower = member.filename.lower()
            suffix = Path(lower).suffix
            if SUSPICIOUS_DOUBLE_EXT.search(lower):
                raise ThreatFound(member.filename, "double extension disguise")
            if suffix in BLOCKED_EXTENSIONS:
                if Path(member.filename).name.lower() not in ALLOWED_FILENAMES:
                    raise ThreatFound(member.filename, f"blocked file type ({suffix})")

            data = archive.read(member)[:MAX_SCAN_BYTES]
            _scan_content(member.filename, data, suffix)
            if on_progress:
                on_progress(int(index * 100 / total))
    return len(members)


def require_clean_project(root_dir=".", include_cli=False):
    """Run guard scan; abort share via CliError on threat."""
    try:
        p.progress("running security scan", 0)

        def on_progress(percent):
            p.progress("running security scan", percent)

        count = scan_project(root_dir, include_cli, on_progress=on_progress)
        return count
    except ThreatFound as threat:
        p.progress_finish()
        raise e.CliError(
            f"EDR Guard blocked share: {threat.path} — {threat.reason}. "
            "Remove the file or use a clean project folder."
        ) from threat


def require_clean_archive(buffer):
    try:
        p.progress("scanning received files", 0)

        def on_progress(percent):
            p.progress("scanning received files", percent)

        count = scan_zip_buffer(buffer, on_progress=on_progress)
        return count
    except ThreatFound as threat:
        p.progress_finish()
        raise e.CliError(
            f"EDR Guard blocked receive: {threat.path} — {threat.reason}. "
            "Do not extract this archive."
        ) from threat
