"""HTTP relay for cross-network EDR sharing (room code: Edrnko_<id>)."""

import atexit
import http.client
import io
import json
import os
import re
import shutil
import string
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

RELAY_PREFIX = "Edrnko_"
BUILTIN_RELAY_URL = "http://127.0.0.1:8765"
DEFAULT_RELAY_URL = os.environ.get("EDR_RELAY_URL", BUILTIN_RELAY_URL)
# Big chunks + a kept-alive connection: throughput over a WAN is roughly
# chunk_size / round_trip_time, so tiny chunks on fresh connections crawl.
CHUNK_SIZE = 4 * 1024 * 1024
DOWNLOAD_BLOCK = 1024 * 1024
MIN_CHUNK_SIZE = 64 * 1024
RELAY_ID_LENGTH = 10
HTTP_TIMEOUT = 120

# Hardening limits (overridable via env for self-hosted deployments).
ROOM_ID_PATTERN = re.compile(r"[a-z0-9]{4,64}")
MAX_ROOM_BYTES = int(os.environ.get("EDR_RELAY_MAX_BYTES", 8 * 1024 * 1024 * 1024))  # 8 GiB
MAX_CHUNK_BYTES = int(os.environ.get("EDR_RELAY_MAX_CHUNK_BYTES", 16 * 1024 * 1024))  # 16 MiB/PUT
MAX_ROOMS = int(os.environ.get("EDR_RELAY_MAX_ROOMS", 1024))
ROOM_IDLE_TIMEOUT = int(os.environ.get("EDR_RELAY_IDLE_SECONDS", 3600))  # sweep stale rooms

_EMBEDDED_RELAY_SERVER = None


def generate_relay_id(length=RELAY_ID_LENGTH):
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def is_valid_room_id(room_id):
    # fullmatch: a trailing "\n" must not sneak past a "$" anchor.
    return bool(room_id) and bool(ROOM_ID_PATTERN.fullmatch(room_id))


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


def is_default_relay_url(url):
    return (url or "").rstrip("/") == BUILTIN_RELAY_URL


class RelayHTTPError(RuntimeError):
    """The relay answered with an HTTP error status."""

    def __init__(self, status, url, body):
        super().__init__(f"Relay error {status} at {url}: {body}")
        self.status = status
        self.body = body


class RelayClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or relay_base_url()).rstrip("/")
        self._conn = None
        self._conn_key = None

    # -- transport ---------------------------------------------------------
    def _connection(self, parts):
        key = (parts.scheme, parts.netloc)
        if self._conn is not None and self._conn_key == key:
            return self._conn
        self._close()
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        self._conn = cls(parts.hostname, parts.port, timeout=HTTP_TIMEOUT)
        self._conn_key = key
        return self._conn

    def _close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
        self._conn = None
        self._conn_key = None

    def close(self):
        self._close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._close()

    def _request(self, method, url, data=None, headers=None, retries=3):
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        last = None
        for attempt in range(retries):
            conn = self._connection(parts)
            try:
                conn.request(method, path, body=data, headers=dict(headers or {}))
                response = conn.getresponse()
                body = response.read()
            except (OSError, http.client.HTTPException) as err:
                # Stale keep-alive connection, flaky network, ...: reconnect.
                self._close()
                last = err
                time.sleep(0.3 * (attempt + 1))
                continue
            if response.status >= 400:
                raise RelayHTTPError(response.status, url, body.decode("utf-8", errors="replace"))
            return body
        raise RuntimeError(
            f"Cannot reach relay at {self.base_url}. "
            f"Start one with: edr relay start  (or set EDR_RELAY_URL)"
        ) from last

    # -- upload / download -------------------------------------------------
    def upload(self, room_id, data, on_progress=None):
        self.upload_stream(room_id, io.BytesIO(data), len(data), on_progress=on_progress)

    def upload_stream(self, room_id, reader, total, on_progress=None):
        """Upload `total` bytes read from `reader` (anything with .read(n))."""
        url = f"{self.base_url}/v1/rooms/{room_id}"
        if total == 0:
            self._request("PUT", url, data=b"", headers={"Content-Length": "0"})
            if on_progress:
                on_progress(100)
            return

        sent = 0
        chunk_size = self._negotiated_chunk_size()
        pending = b""
        while sent < total:
            if not pending:
                pending = _read_exact(reader, min(chunk_size, total - sent))
                if not pending:
                    raise RuntimeError("Payload ended before the expected size was read.")
            headers = {
                "Content-Type": "application/octet-stream",
                "X-EDR-Offset": str(sent),
                "X-EDR-Total": str(total),
            }
            try:
                self._request("PUT", url, data=pending, headers=headers)
            except RelayHTTPError as err:
                # Relay configured with a smaller per-request cap: back off.
                if err.status == 413 and len(pending) > MIN_CHUNK_SIZE:
                    chunk_size = max(MIN_CHUNK_SIZE, len(pending) // 2)
                    reader = _PushbackReader(pending[chunk_size:], reader)
                    pending = pending[:chunk_size]
                    continue
                raise
            sent += len(pending)
            pending = b""
            if on_progress:
                on_progress(int(sent * 100 / total))

    def _negotiated_chunk_size(self):
        """Largest chunk the relay accepts (advertised by /v1/health), capped
        at CHUNK_SIZE. Older relays don't advertise it; the 413 back-off in
        upload_stream covers those."""
        try:
            info = json.loads(self._request("GET", f"{self.base_url}/v1/health", retries=1))
            limit = int(info.get("max_chunk_bytes") or 0)
        except (RuntimeError, ValueError, TypeError):
            return CHUNK_SIZE
        return min(CHUNK_SIZE, limit) if limit >= MIN_CHUNK_SIZE else CHUNK_SIZE

    def open_download(self, room_id):
        """Return an open HTTP response for the payload (caller closes it).
        The room is only consumed once the whole body has been delivered."""
        url = f"{self.base_url}/v1/rooms/{room_id}"
        parts = urlsplit(url)
        # Dedicated connection: a half-read body must not poison the shared one.
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(parts.hostname, parts.port, timeout=HTTP_TIMEOUT)
        try:
            conn.request("GET", parts.path)
            response = conn.getresponse()
        except (OSError, http.client.HTTPException) as err:
            conn.close()
            raise RuntimeError(
                f"Cannot reach relay at {self.base_url}. "
                f"Start one with: edr relay start  (or set EDR_RELAY_URL)"
            ) from err
        if response.status >= 400:
            body = response.read().decode("utf-8", errors="replace")
            conn.close()
            raise RelayHTTPError(response.status, url, body)
        response._edr_conn = conn  # closed together with the response
        return response

    def download(self, room_id, on_progress=None):
        response = self.open_download(room_id)
        try:
            total = int(response.getheader("Content-Length", "0") or 0)
            chunks = []
            received = 0
            while True:
                block = response.read(DOWNLOAD_BLOCK)
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
        finally:
            response._edr_conn.close()
            response.close()

    # -- room lifecycle ----------------------------------------------------
    def wait_until_ready(self, room_id, timeout=600, poll_seconds=0.5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.room_status(room_id).get("ready"):
                return True
            time.sleep(poll_seconds)
        raise TimeoutError(f"Relay room '{room_id}' was not ready before timeout.")

    def register_waiting(self, room_id):
        self._request("POST", f"{self.base_url}/v1/rooms/{room_id}/wait", data=b"")

    def request_pull(self, room_id):
        self._request("POST", f"{self.base_url}/v1/rooms/{room_id}/request", data=b"")

    def wait_for_pull_request(self, room_id, timeout=3600, poll_seconds=1, on_poll=None, on_poll_hit=None):
        deadline = time.time() + timeout
        announced = False
        while time.time() < deadline:
            if self.room_status(room_id).get("requested"):
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
        payload = self._request("GET", f"{self.base_url}/v1/rooms/{room_id}/status")
        return json.loads(payload.decode("utf-8"))

    def wait_until_consumed(self, room_id, timeout=600, poll_seconds=0.25):
        deadline = time.time() + timeout
        saw_ready = False
        while time.time() < deadline:
            status = self.room_status(room_id)
            # `consumed` is set by the relay once the whole payload was delivered,
            # so a receiver that finishes before our first poll is still seen.
            if status.get("consumed"):
                return
            if status.get("ready"):
                saw_ready = True
            if saw_ready and not status.get("ready"):
                return
            time.sleep(poll_seconds)
        raise TimeoutError(
            f"Timed out waiting for receiver to finish downloading {relay_code(room_id)}."
        )

    def delete_room(self, room_id):
        self._request("DELETE", f"{self.base_url}/v1/rooms/{room_id}")


def _read_exact(reader, size):
    parts = []
    remaining = size
    while remaining > 0:
        block = reader.read(remaining)
        if not block:
            break
        parts.append(block)
        remaining -= len(block)
    return b"".join(parts)


class _PushbackReader:
    def __init__(self, head, tail):
        self._head = head
        self._tail = tail

    def read(self, n):
        if self._head:
            out, self._head = self._head[:n], self._head[n:]
            return out
        return self._tail.read(n)


class RoomRejected(Exception):
    """Raised when a request violates relay hardening limits."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class _RelayStore:
    """Disk-backed room store. Chunk bytes go straight to a per-room spool
    file (portable seek+write, so it also works on Windows) instead of
    living in a Python dict, so a large transfer is never held in memory.

    The spool directory is created lazily, private to this process, and
    removed on exit, so a crashed relay cannot leave multi-GiB orphans in a
    shared, predictable temp path."""

    def __init__(self, spool_dir=None):
        self._lock = threading.Lock()
        self._rooms = {}
        self._requested_dir = spool_dir
        self._spool_path = None
        self._owns_spool = False

    @property
    def _spool_dir(self):
        if self._spool_path is None:
            explicit = self._requested_dir or os.environ.get("EDR_RELAY_SPOOL_DIR")
            if explicit:
                path = Path(explicit)
                path.mkdir(parents=True, exist_ok=True)
                for stale in path.glob("*.part"):  # leftovers of a previous run
                    try:
                        stale.unlink()
                    except OSError:
                        pass
            else:
                _purge_stale_spools()
                path = Path(tempfile.mkdtemp(prefix="edr-relay-"))
                self._owns_spool = True
                atexit.register(self.cleanup)
            self._spool_path = path
        return self._spool_path

    def heartbeat(self):
        """Keep our spool dir's mtime fresh so other relays can tell a live
        spool from one left behind by a crashed/killed relay."""
        if self._owns_spool and self._spool_path is not None:
            marker = self._spool_path / ".alive"
            try:
                marker.write_bytes(b"")
                marker.unlink()
            except OSError:
                pass

    def cleanup(self):
        with self._lock:
            for room in self._rooms.values():
                self._unlink(room)
            self._rooms.clear()
        if self._owns_spool and self._spool_path is not None:
            shutil.rmtree(self._spool_path, ignore_errors=True)

    def _empty_room(self, room_id):
        return {
            "path": self._spool_dir / f"{room_id}.part",
            "received": {},  # offset -> length, ints only (cheap to keep in memory)
            "received_bytes": 0,
            "total": None,
            "ready": False,
            "requested": False,
            "waiting": False,
            "consumed": False,
            "touched": time.time(),
        }

    def _unlink(self, room):
        try:
            room["path"].unlink()
        except (FileNotFoundError, KeyError):
            pass
        except OSError:
            pass

    def _check_capacity(self, room_id):
        if room_id not in self._rooms and len(self._rooms) >= MAX_ROOMS:
            raise RoomRejected(503, "relay is full, try again later")

    def register_wait(self, room_id):
        if not is_valid_room_id(room_id):
            raise RoomRejected(400, "invalid room id")
        with self._lock:
            self._check_capacity(room_id)
            old = self._rooms.get(room_id)
            if old:
                self._unlink(old)
            room = self._empty_room(room_id)
            room["waiting"] = True
            self._rooms[room_id] = room

    def request_pull(self, room_id):
        if not is_valid_room_id(room_id):
            raise RoomRejected(400, "invalid room id")
        with self._lock:
            self._check_capacity(room_id)
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
        if total > MAX_ROOM_BYTES:
            raise RoomRejected(413, "room exceeds max relay size")
        if offset + len(chunk) > total:
            # Also blocks "1 byte at offset 3 GB" tricks that would make the
            # relay allocate a huge spool file.
            raise RoomRejected(400, "chunk lies outside the declared total size")

        with self._lock:
            self._check_capacity(room_id)
            room = self._rooms.setdefault(room_id, self._empty_room(room_id))
            if room.get("total") not in (None, total):
                # Total changed mid-upload without an offset==0 reset: reject,
                # don't silently accept a mismatched stream.
                raise RoomRejected(409, "total size changed mid-upload")
            room["total"] = total
            room["touched"] = time.time()

            # Always touch the spool file, even for a zero-byte chunk/total,
            # so open_payload() can stat() it once the room is marked ready.
            fd = os.open(
                room["path"],
                os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                if chunk:
                    os.lseek(fd, offset, os.SEEK_SET)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(fd, view)
                        view = view[written:]
            finally:
                os.close(fd)
            if chunk:
                room["received_bytes"] -= room["received"].get(offset, 0)
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
        memory, or None if not ready. The caller streams the file and calls
        consume_room only after the whole payload was delivered."""
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
        """Payload fully delivered: drop the bytes, keep a tiny tombstone so a
        sender that polls late still learns the transfer succeeded."""
        with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return
            self._unlink(room)
            tombstone = self._empty_room(room_id)
            tombstone["consumed"] = True
            self._rooms[room_id] = tombstone

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
                return {"ready": False, "requested": False, "waiting": False, "consumed": False, "bytes": 0}
            return {
                "ready": bool(room.get("ready")),
                "requested": bool(room.get("requested")),
                "waiting": bool(room.get("waiting")),
                "consumed": bool(room.get("consumed")),
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


STALE_SPOOL_SECONDS = 2 * 3600


def _purge_stale_spools():
    """Remove spool dirs of relays that died without cleaning up (SIGKILL,
    power loss). Live relays refresh their dir via heartbeat(), so only
    abandoned ones age past the cutoff."""
    cutoff = time.time() - STALE_SPOOL_SECONDS
    for old in Path(tempfile.gettempdir()).glob("edr-relay-*"):
        try:
            if old.is_dir() and old.stat().st_mtime < cutoff:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass


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
            _STORE.heartbeat()

    threading.Thread(target=_loop, daemon=True, name="edr-relay-sweeper").start()


class RelayHandler(BaseHTTPRequestHandler):
    STREAM_CHUNK = 1024 * 1024
    # Every response carries a Content-Length, so connections can be kept
    # alive; clients reuse one connection for a whole upload.
    protocol_version = "HTTP/1.1"
    timeout = HTTP_TIMEOUT  # idle / stalled clients cannot pin a thread forever

    def log_message(self, format, *args):
        return

    def _route(self):
        """-> ("health",) | ("room", room_id, action_or_None) | None"""
        parts = [part for part in urlsplit(self.path).path.split("/") if part]
        if parts == ["v1", "health"]:
            return ("health",)
        if 3 <= len(parts) <= 4 and parts[0] == "v1" and parts[1] == "rooms":
            return ("room", parts[2], parts[3] if len(parts) == 4 else None)
        return None

    def _room_route(self, allowed_actions):
        route = self._route()
        if not route or route[0] != "room" or route[2] not in allowed_actions:
            self._respond(404, b"not found", "text/plain")
            return None
        if not is_valid_room_id(route[1]):
            self._respond(400, b"invalid room id", "text/plain")
            return None
        return route[1], route[2]

    def do_GET(self):
        route = self._route()
        if route and route[0] == "health":
            body = json.dumps({"ok": True, "max_chunk_bytes": MAX_CHUNK_BYTES}).encode("utf-8")
            self._respond(200, body, "application/json")
            return

        found_route = self._room_route({None, "status"})
        if not found_route:
            return
        room_id, action = found_route
        if action == "status":
            payload = json.dumps(_STORE.status(room_id)).encode("utf-8")
            self._respond(200, payload, "application/json")
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
            self.wfile.flush()
        except OSError:
            # Receiver vanished (Ctrl+C, Wi-Fi drop): keep the payload so a
            # retry works, and never report this as a finished transfer.
            self.close_connection = True
            return
        _STORE.consume_room(room_id)

    def do_POST(self):
        found = self._room_route({"wait", "request"})
        if not found:
            return
        room_id, action = found
        try:
            if action == "wait":
                _STORE.register_wait(room_id)
            else:
                _STORE.request_pull(room_id)
        except RoomRejected as err:
            self._respond(err.code, err.message.encode("utf-8"), "text/plain")
            return
        self._respond(204, b"", "text/plain")

    def do_PUT(self):
        found = self._room_route({None})
        if not found:
            self.close_connection = True  # request body was not consumed
            return
        room_id, _ = found
        try:
            offset = int(self.headers.get("X-EDR-Offset", "0"))
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._respond(400, b"malformed headers", "text/plain", close=True)
            return
        if length < 0 or length > MAX_CHUNK_BYTES:
            self._respond(413, b"chunk too large", "text/plain", close=True)
            return
        chunk = self.rfile.read(length)
        if len(chunk) != length:
            self._respond(400, b"incomplete body", "text/plain", close=True)
            return
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
        found = self._room_route({None})
        if not found:
            return
        _STORE.delete_room(found[0])
        self._respond(204, b"", "text/plain")

    def _respond(self, code, body, content_type, close=False):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body:
            self.wfile.write(body)


class _RelayServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def start_relay_server(host="0.0.0.0", port=8765):
    server = _RelayServer((host, port), RelayHandler)
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
        with RelayClient(base) as client:
            client._request("GET", f"{base.rstrip('/')}/v1/health", retries=1)
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
    with RelayClient(base_url) as client:
        client.register_waiting(room_id)


def request_pull(room_id, base_url=None):
    with RelayClient(base_url) as client:
        client.request_pull(room_id)


def wait_for_pull_request(room_id, timeout=3600, base_url=None, on_poll=None, on_poll_hit=None):
    with RelayClient(base_url) as client:
        client.wait_for_pull_request(
            room_id,
            timeout=timeout,
            on_poll=on_poll,
            on_poll_hit=on_poll_hit,
        )


def upload_payload(room_id, data, on_progress=None, base_url=None):
    with RelayClient(base_url) as client:
        client.upload(room_id, data, on_progress=on_progress)


def upload_stream(room_id, reader, total, on_progress=None, base_url=None):
    with RelayClient(base_url) as client:
        client.upload_stream(room_id, reader, total, on_progress=on_progress)


def download_payload(room_id, on_progress=None, base_url=None):
    with RelayClient(base_url) as client:
        client.wait_until_ready(room_id)
        return client.download(room_id, on_progress=on_progress)


def open_download_stream(room_id, base_url=None, timeout=600):
    """Wait for the sender, then return an open response to read the payload
    from block by block (no need to hold it all in memory). Close it with
    close_download_stream()."""
    with RelayClient(base_url) as client:
        client.wait_until_ready(room_id, timeout=timeout)
        return client.open_download(room_id)


def close_download_stream(response):
    try:
        response._edr_conn.close()
    finally:
        response.close()


def wait_until_consumed(room_id, timeout=600, base_url=None):
    with RelayClient(base_url) as client:
        client.wait_until_consumed(room_id, timeout=timeout)


def delete_room(room_id, base_url=None):
    with RelayClient(base_url) as client:
        client.delete_room(room_id)
