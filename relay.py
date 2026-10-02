"""HTTP relay for cross-network EDR sharing (room code: Edrnko_<id>)."""

import json
import os
import re
import string
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

RELAY_PREFIX = "Edrnko_"
DEFAULT_RELAY_URL = os.environ.get("EDR_RELAY_URL", "http://127.0.0.1:8765")
CHUNK_SIZE = 256 * 1024
RELAY_ID_LENGTH = 10

# Hardening limits (overridable via env for self-hosted deployments).
ROOM_ID_PATTERN = re.compile(r"^[a-z0-9]{4,64}$")
MAX_ROOM_BYTES = int(os.environ.get("EDR_RELAY_MAX_BYTES", 8 * 1024 * 1024 * 1024))  # 8 GiB
MAX_CHUNK_BYTES = int(os.environ.get("EDR_RELAY_MAX_CHUNK_BYTES", 16 * 1024 * 1024))  # 16 MiB/PUT
ROOM_IDLE_TIMEOUT = int(os.environ.get("EDR_RELAY_IDLE_SECONDS", 3600))  # sweep stale rooms

_EMBEDDED_RELAY_SERVER = None


def generate_relay_id(length=RELAY_ID_LENGTH):
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def is_valid_room_id(room_id):
    return bool(room_id) and bool(ROOM_ID_PATTERN.match(room_id))


def relay_code(relay_id):
    return f"{RELAY_PREFIX}{relay_id}"


def parse_relay_remote(remote):
    if not remote:
        return None, None
    if remote.startswith(RELAY_PREFIX):
        return remote[len(RELAY_PREFIX) :], remote
    return None, None


def relay_base_url():
    return DEFAULT_RELAY_URL.rstrip("/")


class RelayClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or relay_base_url()).rstrip("/")

    def upload(self, room_id, data, on_progress=None):
        url = f"{self.base_url}/v1/rooms/{room_id}"
        total = len(data)
        if total == 0:
            self._request("PUT", url, data=b"", headers={"Content-Length": "0"})
            if on_progress:
                on_progress(100)
            return

        sent = 0
        buffer = memoryview(data)
        while sent < total:
            chunk = buffer[sent : sent + CHUNK_SIZE]
            headers = {
                "Content-Type": "application/octet-stream",
                "X-EDR-Offset": str(sent),
                "X-EDR-Total": str(total),
            }
            self._request("PUT", url, data=chunk.tobytes(), headers=headers)
            sent += len(chunk)
            if on_progress:
                on_progress(int(sent * 100 / total))

    def download(self, room_id, on_progress=None):
        url = f"{self.base_url}/v1/rooms/{room_id}"
        with urlrequest.urlopen(url, timeout=120) as response:
            total = int(response.headers.get("Content-Length", "0") or 0)
            chunks = []
            received = 0
            while True:
                block = response.read(CHUNK_SIZE)
                if not block:
                    break
                chunks.append(block)
                received += len(block)
                if on_progress:
                    if total > 0:
                        on_progress(int(received * 100 / total))
                    else:
                        on_progress(min(99, received // (1024 * 64)))
            if on_progress:
                on_progress(100)
            return b"".join(chunks)

    def wait_until_ready(self, room_id, timeout=600, poll_seconds=2):
        url = f"{self.base_url}/v1/rooms/{room_id}/status"
        deadline = time.time() + timeout
        while time.time() < deadline:
            payload = self._request("GET", url)
            status = json.loads(payload.decode("utf-8"))
            if status.get("ready"):
                return True
            time.sleep(poll_seconds)
        raise TimeoutError(f"Relay room '{room_id}' was not ready before timeout.")

    def register_waiting(self, room_id):
        url = f"{self.base_url}/v1/rooms/{room_id}/wait"
        self._request("POST", url, data=b"")

    def request_pull(self, room_id):
        url = f"{self.base_url}/v1/rooms/{room_id}/request"
        self._request("POST", url, data=b"")

    def wait_for_pull_request(self, room_id, timeout=3600, poll_seconds=1, on_poll=None, on_poll_hit=None):
        url = f"{self.base_url}/v1/rooms/{room_id}/status"
        deadline = time.time() + timeout
        announced = False
        while time.time() < deadline:
            payload = self._request("GET", url)
            status = json.loads(payload.decode("utf-8"))
            if status.get("requested"):
                return True
            if on_poll and on_poll():
                if on_poll_hit and not announced:
                    on_poll_hit()
                    announced = True
            time.sleep(poll_seconds)
        raise TimeoutError(
            f"Timed out waiting for a pull on {relay_code(room_id)}. "
            f"On another device run: edr pull {relay_code(room_id)}"
        )

    def room_status(self, room_id):
        url = f"{self.base_url}/v1/rooms/{room_id}/status"
        payload = self._request("GET", url)
        return json.loads(payload.decode("utf-8"))

    def wait_until_consumed(self, room_id, timeout=600, poll_seconds=0.5):
        deadline = time.time() + timeout
        saw_ready = False
        while time.time() < deadline:
            status = self.room_status(room_id)
            if status.get("ready"):
                saw_ready = True
            if saw_ready and not status.get("ready"):
                return
            time.sleep(poll_seconds)
        raise TimeoutError(
            f"Timed out waiting for receiver to finish downloading {relay_code(room_id)}."
        )

    def delete_room(self, room_id):
        url = f"{self.base_url}/v1/rooms/{room_id}"
        self._request("DELETE", url)

    def _request(self, method, url, data=None, headers=None):
        req = urlrequest.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urlrequest.urlopen(req, timeout=120) as response:
                return response.read()
        except urlerror.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Relay error {err.code} at {url}: {body}") from err
        except urlerror.URLError as err:
            raise RuntimeError(
                f"Cannot reach relay at {self.base_url}. "
                f"Start one with: edr relay start  (or set EDR_RELAY_URL)"
            ) from err


class RoomRejected(Exception):
    """Raised when a request violates relay hardening limits."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class _RelayStore:
    """Disk-backed room store. Chunk bytes land directly on disk via
    positional writes (os.pwrite) instead of living in a Python dict, so a
    large transfer never has to be held fully in relay-process memory."""

    def __init__(self, spool_dir=None):
        self._lock = threading.Lock()
        self._rooms = {}
        self._spool_dir = Path(spool_dir or tempfile.gettempdir()) / "edr-relay-spool"
        self._spool_dir.mkdir(parents=True, exist_ok=True)

    def _empty_room(self, room_id):
        return {
            "path": self._spool_dir / f"{room_id}.part",
            "received": {},  # offset -> length, ints only (cheap to keep in memory)
            "received_bytes": 0,
            "total": None,
            "ready": False,
            "requested": False,
            "waiting": False,
            "touched": time.time(),
        }

    def _unlink(self, room):
        try:
            room["path"].unlink()
        except (FileNotFoundError, KeyError):
            pass
        except OSError:
            pass

    def register_wait(self, room_id):
        if not is_valid_room_id(room_id):
            raise RoomRejected(400, "invalid room id")
        with self._lock:
            old = self._rooms.get(room_id)
            if old:
                self._unlink(old)
            self._rooms[room_id] = self._empty_room(room_id)
            self._rooms[room_id]["waiting"] = True

    def request_pull(self, room_id):
        if not is_valid_room_id(room_id):
            raise RoomRejected(400, "invalid room id")
        with self._lock:
            room = self._rooms.setdefault(room_id, self._empty_room(room_id))
            room["requested"] = True
            room["waiting"] = True
            room["touched"] = time.time()

    def reset_room(self, room_id):
        with self._lock:
            old = self._rooms.get(room_id)
            if old:
                self._unlink(old)
            room = self._empty_room(room_id)
            room["requested"] = old.get("requested", False) if old else False
            room["waiting"] = old.get("waiting", False) if old else False
            self._rooms[room_id] = room

    def put_chunk(self, room_id, offset, total, chunk):
        if not is_valid_room_id(room_id):
            raise RoomRejected(400, "invalid room id")
        if offset < 0 or total < 0:
            raise RoomRejected(400, "negative offset/total")
        if len(chunk) > MAX_CHUNK_BYTES:
            raise RoomRejected(413, "chunk too large")
        if total > MAX_ROOM_BYTES or offset + len(chunk) > max(total, MAX_ROOM_BYTES):
            raise RoomRejected(413, "room exceeds max relay size")

        with self._lock:
            room = self._rooms.setdefault(room_id, self._empty_room(room_id))
            if room.get("total") not in (None, total):
                # Total changed mid-upload without an offset==0 reset: reject,
                # don't silently accept a mismatched stream.
                raise RoomRejected(409, "total size changed mid-upload")
            room["total"] = total
            room["touched"] = time.time()

            # Always touch the spool file, even for a zero-byte chunk/total,
            # so open_payload() can stat() it once the room is marked ready.
            fd = os.open(room["path"], os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                if chunk:
                    os.pwrite(fd, chunk, offset)
            finally:
                os.close(fd)
            if chunk:
                room["received"][offset] = len(chunk)
                room["received_bytes"] += len(chunk)

            ordered_offsets = sorted(room["received"])
            next_offset = 0
            complete = total == 0
            for chunk_offset in ordered_offsets:
                if chunk_offset != next_offset:
                    complete = False
                    break
                next_offset += room["received"][chunk_offset]
                complete = next_offset == total
            if complete:
                room["ready"] = True

    def open_payload(self, room_id):
        """Return (file_path, size) for a ready room without loading it into
        memory, or None if not ready. Caller is responsible for streaming and
        must call consume_room afterwards."""
        with self._lock:
            room = self._rooms.get(room_id)
            if not room or not room.get("ready"):
                return None
            room["touched"] = time.time()
            try:
                size = room["path"].stat().st_size
            except FileNotFoundError:
                return None
            return room["path"], size

    def consume_room(self, room_id):
        with self._lock:
            room = self._rooms.get(room_id)
            if room:
                self._unlink(room)
            self._rooms.pop(room_id, None)

    def delete_room(self, room_id):
        with self._lock:
            room = self._rooms.get(room_id)
            if room:
                self._unlink(room)
            self._rooms.pop(room_id, None)

    def status(self, room_id):
        with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return {"ready": False, "requested": False, "waiting": False, "bytes": 0}
            return {
                "ready": bool(room.get("ready")),
                "requested": bool(room.get("requested")),
                "waiting": bool(room.get("waiting")),
                "bytes": room.get("received_bytes", 0),
            }

    def sweep_idle(self, idle_seconds=ROOM_IDLE_TIMEOUT):
        """Delete rooms with no activity for idle_seconds. Called by a
        background thread so abandoned uploads don't fill disk forever."""
        cutoff = time.time() - idle_seconds
        with self._lock:
            stale = [rid for rid, room in self._rooms.items() if room["touched"] < cutoff]
            for room_id in stale:
                self._unlink(self._rooms[room_id])
                self._rooms.pop(room_id, None)
        return stale


_STORE = _RelayStore()
_SWEEPER_STARTED = False


def _start_idle_sweeper():
    global _SWEEPER_STARTED
    if _SWEEPER_STARTED:
        return
    _SWEEPER_STARTED = True

    def _loop():
        while True:
            time.sleep(min(300, max(30, ROOM_IDLE_TIMEOUT // 4)))
            _STORE.sweep_idle()

    threading.Thread(target=_loop, daemon=True, name="edr-relay-sweeper").start()


class RelayHandler(BaseHTTPRequestHandler):
    STREAM_CHUNK = 256 * 1024

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/health":
            self._respond(200, b'{"ok":true}', "application/json")
            return

        if self.path.endswith("/status"):
            room_id = self.path.split("/")[-2]
            if not is_valid_room_id(room_id):
                self._respond(400, b"invalid room id", "text/plain")
                return
            payload = json.dumps(_STORE.status(room_id)).encode("utf-8")
            self._respond(200, payload, "application/json")
            return

        room_id = self.path.rstrip("/").split("/")[-1]
        if not is_valid_room_id(room_id):
            self._respond(400, b"invalid room id", "text/plain")
            return
        found = _STORE.open_payload(room_id)
        if found is None:
            self._respond(404, b"not ready", "text/plain")
            return
        path, size = found
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(path, "rb") as handle:
                while True:
                    block = handle.read(self.STREAM_CHUNK)
                    if not block:
                        break
                    self.wfile.write(block)
        finally:
            _STORE.consume_room(room_id)

    def do_POST(self):
        parts = [part for part in self.path.rstrip("/").split("/") if part]
        if len(parts) >= 4 and parts[0] == "v1" and parts[1] == "rooms":
            room_id = parts[2]
            action = parts[3]
            try:
                if action == "wait":
                    _STORE.register_wait(room_id)
                    self._respond(204, b"", "text/plain")
                    return
                if action == "request":
                    _STORE.request_pull(room_id)
                    self._respond(204, b"", "text/plain")
                    return
            except RoomRejected as err:
                self._respond(err.code, err.message.encode("utf-8"), "text/plain")
                return
        self._respond(404, b"not found", "text/plain")

    def do_PUT(self):
        room_id = self.path.rstrip("/").split("/")[-1]
        if not is_valid_room_id(room_id):
            self._respond(400, b"invalid room id", "text/plain")
            return
        try:
            offset = int(self.headers.get("X-EDR-Offset", "0"))
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._respond(400, b"malformed headers", "text/plain")
            return
        if length < 0 or length > MAX_CHUNK_BYTES:
            self._respond(413, b"chunk too large", "text/plain")
            return
        chunk = self.rfile.read(length)
        try:
            total = int(self.headers.get("X-EDR-Total", str(len(chunk))))
        except ValueError:
            self._respond(400, b"malformed headers", "text/plain")
            return
        try:
            if offset == 0:
                _STORE.reset_room(room_id)
            _STORE.put_chunk(room_id, offset, total, chunk)
        except RoomRejected as err:
            self._respond(err.code, err.message.encode("utf-8"), "text/plain")
            return
        self._respond(204, b"", "text/plain")

    def do_DELETE(self):
        room_id = self.path.rstrip("/").split("/")[-1]
        if not is_valid_room_id(room_id):
            self._respond(400, b"invalid room id", "text/plain")
            return
        _STORE.delete_room(room_id)
        self._respond(204, b"", "text/plain")

    def _respond(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def start_relay_server(host="0.0.0.0", port=8765):
    server = ThreadingHTTPServer((host, port), RelayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="edr-relay")
    thread.start()
    _start_idle_sweeper()
    return server, f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"


def _is_local_relay_url(base_url):
    from urllib.parse import urlparse

    parsed = urlparse((base_url or relay_base_url()).rstrip("/"))
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _relay_health_ok(base):
    try:
        RelayClient(base)._request("GET", f"{base.rstrip('/')}/v1/health")
        return True
    except RuntimeError:
        return False


def ensure_relay_available(base_url=None):
    """Ping relay; auto-start embedded server for default localhost URL."""
    global _EMBEDDED_RELAY_SERVER
    base = (base_url or relay_base_url()).rstrip("/")
    if _relay_health_ok(base):
        return base

    if not _is_local_relay_url(base):
        raise RuntimeError(
            f"Cannot reach relay at {base}. "
            f"Start one with: edr relay start  (or set EDR_RELAY_URL)"
        )

    from urllib.parse import urlparse

    parsed = urlparse(base)
    port = parsed.port or 8765
    if _EMBEDDED_RELAY_SERVER is None:
        _EMBEDDED_RELAY_SERVER, started = start_relay_server(port=port)
        base = started.rstrip("/")

    deadline = time.time() + 10
    while time.time() < deadline:
        if _relay_health_ok(base):
            return base
        time.sleep(0.2)

    raise RuntimeError(
        f"Cannot reach relay at {base}. "
        f"Start one with: edr relay start  (or set EDR_RELAY_URL)"
    )


def register_waiting_room(room_id, base_url=None):
    RelayClient(base_url).register_waiting(room_id)


def request_pull(room_id, base_url=None):
    RelayClient(base_url).request_pull(room_id)


def wait_for_pull_request(room_id, timeout=3600, base_url=None, on_poll=None, on_poll_hit=None):
    RelayClient(base_url).wait_for_pull_request(
        room_id,
        timeout=timeout,
        on_poll=on_poll,
        on_poll_hit=on_poll_hit,
    )


def upload_payload(room_id, data, on_progress=None, base_url=None):
    RelayClient(base_url).upload(room_id, data, on_progress=on_progress)


def download_payload(room_id, on_progress=None, base_url=None):
    client = RelayClient(base_url)
    client.wait_until_ready(room_id)
    return client.download(room_id, on_progress=on_progress)


def wait_until_consumed(room_id, timeout=600, base_url=None):
    RelayClient(base_url).wait_until_consumed(room_id, timeout=timeout)


def delete_room(room_id, base_url=None):
    RelayClient(base_url).delete_room(room_id)
