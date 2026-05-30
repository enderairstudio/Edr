"""Watch project folders for changes (auto-share / watch mode)."""

import hashlib
import time
from pathlib import Path


DEFAULT_POLL_SECONDS = 1.0
DEFAULT_DEBOUNCE_SECONDS = 2.0


def project_fingerprint(root_dir, include_cli=False, iter_files=None):
    """Stable hash of tracked project files (path, size, mtime_ns)."""
    if iter_files is None:
        from share import iter_project_files

        iter_files = iter_project_files

    root = Path(root_dir).resolve()
    digest = hashlib.sha256()
    digest.update(str(root).encode("utf-8"))
    for path, rel in iter_files(root, include_cli):
        try:
            stat = path.stat()
        except OSError:
            continue
        line = f"{rel.as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\n"
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


class ProjectWatcher:
    def __init__(self, root_dir, include_cli=False, poll_seconds=DEFAULT_POLL_SECONDS, debounce_seconds=DEFAULT_DEBOUNCE_SECONDS):
        self.root_dir = root_dir
        self.include_cli = include_cli
        self.poll_seconds = poll_seconds
        self.debounce_seconds = debounce_seconds
        self._last_fp = project_fingerprint(root_dir, include_cli)
        self._pending_since = None
        self._announced = True

    def check(self):
        """
        Poll once. Returns True when the fingerprint changed and debounce elapsed.
        """
        now = time.monotonic()
        fp = project_fingerprint(self.root_dir, self.include_cli)
        if fp != self._last_fp:
            if self._pending_since is None:
                self._pending_since = now
            if now - self._pending_since >= self.debounce_seconds:
                self._last_fp = fp
                self._pending_since = None
                self._announced = False
                return True
            return False

        self._pending_since = None
        return False

    def mark_announced(self):
        self._announced = True

    def needs_announce(self):
        return not self._announced

    def sleep(self):
        time.sleep(self.poll_seconds)


def wait_with_watch(callback, watcher, on_change=None, timeout=None):
    """
    Call callback(poll_seconds) until it returns a truthy value.
    on_change() is called when the project fingerprint changes (debounced).
    """
    deadline = None if timeout is None else time.time() + timeout
    while True:
        if watcher.check() and on_change:
            on_change()
            watcher.mark_announced()
        wait = watcher.poll_seconds
        if deadline is not None:
            remaining = deadline - time.time()
            if remaining <= 0:
                return callback(wait)
            wait = min(wait, remaining)
        result = callback(wait)
        if result:
            return result
        if deadline is not None and time.time() >= deadline:
            return False
