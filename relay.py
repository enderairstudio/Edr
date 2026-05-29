"""HTTP relay for cross-network EDR sharing (room code: Edrnko_<id>)."""

import json
import os
import string
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

RELAY_PREFIX = "Edrnko_"
DEFAULT_RELAY_URL = os.environ.get("EDR_RELAY_URL", "http://127.0.0.1:8765")
CHUNK_SIZE = 256 * 1024
RELAY_ID_LENGTH = 10


def generate_relay_id(length=RELAY_ID_LENGTH):
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


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


class _RelayStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._rooms = {}

    def reset_room(self, room_id):
        with self._lock:
            self._rooms[room_id] = {"chunks": {}, "total": None, "ready": False}

    def put_chunk(self, room_id, offset, total, chunk):
        with self._lock:
            room = self._rooms.setdefault(room_id, {"chunks": {}, "total": None, "ready": False})
            room["chunks"][offset] = chunk
            room["total"] = total
            received = sum(len(value) for value in room["chunks"].values())
            if received >= total:
                ordered = b"".join(room["chunks"][index] for index in sorted(room["chunks"]))
                room["payload"] = ordered
                room["ready"] = True

    def get_payload(self, room_id):
        with self._lock:
            room = self._rooms.get(room_id)
            if not room or not room.get("ready"):
                return None
            payload = room.pop("payload", None)
            self._rooms.pop(room_id, None)
            return payload

    def status(self, room_id):
        with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return {"ready": False, "bytes": 0}
            if room.get("ready"):
                size = len(room.get("payload", b""))
            else:
                size = sum(len(value) for value in room["chunks"].values())
            return {"ready": bool(room.get("ready")), "bytes": size}


_STORE = _RelayStore()


class RelayHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path.endswith("/status"):
            room_id = self.path.split("/")[-2]
            payload = json.dumps(_STORE.status(room_id)).encode("utf-8")
            self._respond(200, payload, "application/json")
            return

        room_id = self.path.rstrip("/").split("/")[-1]
        data = _STORE.get_payload(room_id)
        if data is None:
            self._respond(404, b"not ready", "text/plain")
            return
        self._respond(200, data, "application/octet-stream")

    def do_PUT(self):
        room_id = self.path.rstrip("/").split("/")[-1]
        offset = int(self.headers.get("X-EDR-Offset", "0"))
        length = int(self.headers.get("Content-Length", 0))
        chunk = self.rfile.read(length)
        total = int(self.headers.get("X-EDR-Total", str(len(chunk))))
        if offset == 0:
            _STORE.reset_room(room_id)
        _STORE.put_chunk(room_id, offset, total, chunk)
        self._respond(204, b"", "text/plain")

    def _respond(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def start_relay_server(host="0.0.0.0", port=8765):
    server = HTTPServer((host, port), RelayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"


def upload_payload(room_id, data, on_progress=None, base_url=None):
    RelayClient(base_url).upload(room_id, data, on_progress=on_progress)


def download_payload(room_id, on_progress=None, base_url=None):
    client = RelayClient(base_url)
    client.wait_until_ready(room_id)
    return client.download(room_id, on_progress=on_progress)
