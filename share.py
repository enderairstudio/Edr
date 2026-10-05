import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import tempfile
import time
import zipfile

import error as e
import guard as g
import print as p
import qrterm as qr
import relay as r
import watch as w

DEFAULT_PORT = 5005
PROTOCOL_MAGIC = b"EDR1"
SOCKET_CHUNK_SIZE = 1024 * 1024
MAX_EXTRACT_BYTES = int(os.environ.get("EDR_MAX_EXTRACT_BYTES", 20 * 1024 * 1024 * 1024))  # 20 GiB
MAX_COMPRESSION_RATIO = int(os.environ.get("EDR_MAX_COMPRESSION_RATIO", 300))
# Skipped at any depth.
IGNORE_DIRS = {'.edr', '.git', '__pycache__', 'venv', '.venv', 'node_modules', '.mypy_cache', '.pytest_cache', 'dist', 'build'}
# Skipped only at the project root. These are EDR's own packaging folders; a
# user's `bindings/python/` or `src/launcher/` must not silently vanish.
ROOT_ONLY_IGNORE_DIRS = {'python', 'launcher'}
IGNORE_FILES = {'project_payload.zip'}
CLI_FILES = {
    'command.py', 'handler.py', 'share.py', 'error.py', 'print.py', 'relay.py', 'guard.py',
    'watch.py', 'qrterm.py', 'doctor_checks.py', 'updater.py',
}
# CLI_FILES are only filtered when the folder really is the EDR CLI itself.
# Plain names like handler.py / error.py are extremely common in normal projects.
CLI_SIGNATURE = {'command.py', 'handler.py', 'share.py', 'relay.py', 'guard.py'}
# Seconds a receiver waits for the sender to send *anything* before giving up
# (0 disables). Generous on purpose: the sender scans the project first.
RECV_IDLE_TIMEOUT = int(os.environ.get("EDR_RECV_TIMEOUT", 600))


class TransferAborted(Exception):
    """The other side went away mid-transfer (receiver hit Ctrl+C, link dropped)."""


def get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def is_local_address(host):
    relay_id, _ = r.parse_relay_remote(host)
    if relay_id:
        return False

    try:
        remote_ips = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        remote_ips = {host}

    local_ips = {"127.0.0.1", "::1", "localhost", get_local_ip()}
    try:
        local_ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    return bool(remote_ips & local_ips) or host in local_ips


def _looks_like_edr_cli_dir(base):
    try:
        return CLI_SIGNATURE <= set(os.listdir(base))
    except OSError:
        return False


def _is_ignored_file(filename, include_cli, filter_cli=True):
    if filename in IGNORE_FILES:
        return True
    return filter_cli and not include_cli and filename in CLI_FILES


def iter_project_files(root_dir=".", include_cli=False):
    base = Path(root_dir).resolve()
    base_str = str(base)
    filter_cli = not include_cli and _looks_like_edr_cli_dir(base)
    for root, dirs, files in os.walk(base):
        at_root = root == base_str
        dirs[:] = sorted(
            d for d in dirs
            if d not in IGNORE_DIRS and not (at_root and d in ROOT_ONLY_IGNORE_DIRS)
        )
        for filename in sorted(files):
            if _is_ignored_file(filename, include_cli, filter_cli and at_root):
                continue
            path = Path(root, filename)
            if path.is_symlink():
                # A link that leaves the project would leak files (e.g. ~/.ssh).
                try:
                    if not _is_relative_to(path.resolve(), base):
                        continue
                except OSError:
                    continue
            if path.is_file():
                yield path, path.relative_to(base)


def project_summary(root_dir=".", include_cli=False):
    files = list(iter_project_files(root_dir, include_cli))
    total_bytes = sum(_safe_size(path) for path, _ in files)
    return {
        "files": len(files),
        "bytes": total_bytes,
        "include_cli": include_cli,
        "ignored_dirs": ", ".join(sorted(IGNORE_DIRS | ROOT_ONLY_IGNORE_DIRS)),
    }


def build_manifest(root_dir=".", include_cli=False, share_id=None, non_network=False, relay_code=None):
    root = Path(root_dir).resolve()
    files = []
    for path, archive_path in iter_project_files(root, include_cli):
        files.append({
            "path": archive_path.as_posix(),
            "bytes": _safe_size(path),
        })
    manifest = {
        "share_id": share_id or root.name,
        "root_name": root.name,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
    }
    if non_network:
        manifest["non_network"] = True
        manifest["relay_code"] = relay_code
    return manifest


def bundle_to_memory(root_dir=".", include_cli=False, verbose=False, skip_guard=False, fast=False):
    memory_file = io.BytesIO()
    stream_zip_to_fileobj(memory_file, root_dir, include_cli, verbose=verbose, skip_guard=skip_guard, fast=fast)
    return memory_file.getvalue()


def stream_zip_to_fileobj(fileobj, root_dir=".", include_cli=False, verbose=False, skip_guard=False, fast=False):
    files = list(iter_project_files(root_dir, include_cli))
    total_bytes = sum(_safe_size(path) for path, _ in files)
    p.configure_workload(files=len(files), bytes_=total_bytes)
    if not skip_guard:
        g.require_clean_project(root_dir, include_cli)

    total = len(files)
    written_source_bytes = 0
    vanished = 0
    try:
        if verbose:
            p.progress("scanning project", 100)
            p.progress("copying files", 0)

        compression = zipfile.ZIP_STORED if fast else zipfile.ZIP_DEFLATED
        # strict_timestamps=False: files with an mtime before 1980 (reproducible
        # builds, extracted tarballs, ...) are clamped instead of aborting.
        with zipfile.ZipFile(fileobj, "w", compression, strict_timestamps=False) as zipf:
            for index, (path, archive_path) in enumerate(files, start=1):
                try:
                    zipf.write(path, archive_path)
                except FileNotFoundError:
                    # Deleted between the scan and now (editor swap files, ...).
                    vanished += 1
                    continue
                written_source_bytes += _safe_size(path)
                if verbose:
                    if total_bytes:
                        percent = int(written_source_bytes * 100 / total_bytes)
                    else:
                        percent = int(index * 100 / total) if total else 100
                    p.progress("copying files", min(99, percent))

        if verbose:
            p.progress("copying files", 100)
            p.progress("compressing archive", 100)
        if vanished:
            p.warn(f"{vanished} file(s) disappeared while bundling and were skipped.")
    except TransferAborted:
        raise
    except Exception as err:
        e.handle_error("BundleError", str(err))


def _safe_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def bundle_to_file(output_path="project_payload.zip", root_dir=".", include_cli=False, force=False, skip_guard=False, fast=False):
    target = Path(output_path)
    if target.exists() and not force:
        e.handle_error("FileExists", f"{target} already exists. Use --force to overwrite it.")

    partial = target.with_name(target.name + ".part")
    try:
        with partial.open("wb") as handle:
            stream_zip_to_fileobj(handle, root_dir, include_cli, skip_guard=skip_guard, fast=fast)
        os.replace(partial, target)
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise
    return target, target.stat().st_size


def _print_pull_qr(remote, port=None, relay_url=None):
    text = qr.pull_command_text(remote, port=port, relay_url=relay_url)
    p.info(f"Scan to pull: {text}")
    try:
        qr.print_qr(text)
    except Exception as err:
        p.warn(f"Could not print QR code: {err}")


def start_server(
    root_dir=".",
    port=DEFAULT_PORT,
    include_cli=False,
    dry_run=False,
    share_id=None,
    forever=False,
    non_network=False,
    relay_id=None,
    relay_url=None,
    skip_guard=False,
    watch=False,
    show_qr=True,
    fast=False,
):
    root = Path(root_dir).resolve()
    summary = project_summary(root, include_cli)
    p.configure_workload(files=summary["files"], bytes_=summary["bytes"])
    code = r.relay_code(relay_id) if relay_id else None

    if share_id:
        p.key_value("Sharer", share_id)
    p.key_value("Folder", root)
    p.key_value("Files", summary["files"])
    p.key_value("Payload", format_bytes(summary["bytes"]))
    p.key_value("Mode", "auto" if forever else "once")
    if watch:
        p.key_value("Watch", "on (auto-detect folder changes)")

    if non_network and relay_id:
        p.key_value("Network", "relay (anywhere)")
        p.key_value("Share code", code)
        p.key_value("Relay", relay_url or r.relay_base_url())
        shown_relay = relay_url or r.relay_base_url()
        p.info(f"Pull command: {qr.pull_command_text(code, relay_url=shown_relay)}")
        if show_qr:
            _print_pull_qr(code, relay_url=shown_relay)
    else:
        local_ip = get_local_ip()
        p.key_value("IP", local_ip)
        p.key_value("Port", port)
        p.info(f"Pull command: edr pull {local_ip} --port {port}")
        if show_qr:
            _print_pull_qr(local_ip, port=port)

    if dry_run:
        if not skip_guard:
            g.require_clean_project(root, include_cli)
        p.info("Dry run complete. No network socket was opened.")
        return

    if non_network and relay_id:
        _serve_via_relay(root, include_cli, share_id, relay_id, forever, relay_url, skip_guard, watch=watch, fast=fast)
        return

    _serve_via_tcp(root, port, include_cli, share_id, forever, skip_guard, watch=watch, fast=fast)


def _serve_via_tcp(root, port, include_cli, share_id, forever, skip_guard=False, watch=False, fast=False):
    local_ip = get_local_ip()
    watcher = w.ProjectWatcher(root, include_cli) if watch else None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('0.0.0.0', port))
        server.listen(1)
        p.progress("waiting for receiver", 0)
        p.info(f"Waiting for receivers at {local_ip}:{port}")

        while True:
            if watcher:
                server.settimeout(watcher.poll_seconds)
                try:
                    while True:
                        try:
                            conn, addr = server.accept()
                            break
                        except socket.timeout:
                            if watcher.check():
                                p.info("Project changed — next pull will send the latest files.")
                                watcher.mark_announced()
                finally:
                    server.settimeout(None)
            else:
                conn, addr = server.accept()

            try:
                with conn:
                    p.progress("waiting for receiver", 100)
                    p.success(f"Connected by {addr[0]}:{addr[1]}")
                    _send_once(conn, root, include_cli, share_id, network_label="LAN", skip_guard=skip_guard, fast=fast)
            except TransferAborted as err:
                p.warn(f"Transfer to {addr[0]} did not finish: {err}")
                if not forever:
                    raise e.CliError("The receiver disconnected before the transfer finished.") from err
                p.info("Still serving; waiting for the next pull ...")
            if not forever:
                break
            p.progress("waiting for receiver", 0)


def _serve_via_relay(root, include_cli, share_id, relay_id, forever, relay_url, skip_guard=False, watch=False, fast=False):
    base = r.ensure_relay_available(relay_url)
    code = r.relay_code(relay_id)
    watcher = w.ProjectWatcher(root, include_cli) if watch else None

    while True:
        r.register_waiting_room(relay_id, base_url=base)
        p.progress("waiting for receiver", 0)
        p.info(f"Waiting for a pull on {code} …")
        p.info(f"On another device: {qr.pull_command_text(code, relay_url=base)}")

        try:
            if watcher:
                def _on_watch_change():
                    p.info("Project changed — next pull will send the latest files.")
                    watcher.mark_announced()

                r.wait_for_pull_request(
                    relay_id,
                    base_url=base,
                    on_poll=watcher.check,
                    on_poll_hit=_on_watch_change,
                )
            else:
                r.wait_for_pull_request(relay_id, base_url=base)
        except TimeoutError as err:
            raise e.CliError(str(err)) from err

        p.progress("waiting for receiver", 100)
        p.success("Receiver connected — preparing share")

        manifest = build_manifest(
            root,
            include_cli,
            share_id,
            non_network=True,
            relay_code=code,
        )
        try:
            # Zip into a temp file (not RAM), then upload header + zip from disk.
            with tempfile.TemporaryFile(prefix="edr-push-", suffix=".zip") as zip_tmp:
                stream_zip_to_fileobj(zip_tmp, root, include_cli, verbose=True, skip_guard=skip_guard, fast=fast)
                header_block = _encode_header(manifest)
                total_size = len(header_block) + zip_tmp.tell()
                zip_tmp.seek(0)
                payload_reader = _ChainReader(io.BytesIO(header_block), zip_tmp)

                p.progress("sharing files", 0)

                def upload_progress(percent):
                    p.progress("sharing files", min(99, percent))

                r.upload_stream(relay_id, payload_reader, total_size, on_progress=upload_progress, base_url=base)
            p.info("Waiting for receiver to finish downloading ...")
            r.wait_until_consumed(relay_id, base_url=base)
        except KeyboardInterrupt:
            r.delete_room(relay_id, base_url=base)
            p.warn("Share cancelled; relay payload was deleted.")
            raise
        except TimeoutError as err:
            r.delete_room(relay_id, base_url=base)
            raise e.CliError(str(err)) from err
        except Exception:
            r.delete_room(relay_id, base_url=base)
            raise
        p.progress("sharing files", 100)
        p.success(f"Shared {format_bytes(total_size)} via relay — receiver finished.")
        if not forever:
            break
        p.info("Waiting for the next pull …")


def _send_once(conn, root, include_cli, share_id, network_label="LAN", skip_guard=False, fast=False):
    p.progress("preparing share", 0)
    manifest = build_manifest(root, include_cli, share_id)
    p.progress("sharing files", 0)
    send_payload_stream(conn, manifest, root, include_cli, skip_guard=skip_guard, fast=fast)
    p.progress("sharing files", 100)
    p.success(f"Sent {format_bytes(manifest.get('total_bytes', 0))} ({network_label}).")


def _encode_header(manifest):
    header = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    return PROTOCOL_MAGIC + struct.pack("!Q", len(header)) + header


def _encode_payload(manifest, data):
    return _encode_header(manifest) + data


class _ChainReader:
    """Read several file-likes back to back as one stream."""

    def __init__(self, *parts):
        self._parts = list(parts)

    def read(self, size):
        out = []
        while size > 0 and self._parts:
            block = self._parts[0].read(size)
            if not block:
                self._parts.pop(0)
                continue
            out.append(block)
            size -= len(block)
        return b"".join(out)


class _SocketReader:
    def __init__(self, sock):
        self._sock = sock

    def read(self, size):
        return self._sock.recv(size)


def receive_project(ip_pin, port=DEFAULT_PORT, target_dir=None, force=False, relay_url=None):
    relay_id, relay_code = r.parse_relay_remote(ip_pin)
    if relay_id:
        return _receive_via_relay(relay_id, relay_code, target_dir, force, relay_url)

    try:
        p.progress("connecting to sharer", 0)
        # create_connection resolves hostnames and tries IPv6 as well as IPv4.
        with socket.create_connection((ip_pin, port), timeout=15) as client:
            client.settimeout(RECV_IDLE_TIMEOUT or None)
            p.progress("connecting to sharer", 100)

            manifest, zip_path = receive_payload_to_tempfile(client)

        return _extract_received(manifest, zip_path, target_dir, force, remote=f"{ip_pin}:{port}")
    except e.CliError:
        raise
    except TimeoutError:
        e.handle_error(
            "ConnectionError",
            f"{ip_pin}:{port} stopped sending for {RECV_IDLE_TIMEOUT} seconds. "
            f"Check that the sharer is still running (EDR_RECV_TIMEOUT changes this limit).",
        )
    except zipfile.BadZipFile:
        e.handle_error("ConnectionError", "The transfer was interrupted or corrupted (incomplete archive).")
    except Exception as err:
        e.handle_error("ConnectionError", f"Failed to receive from {ip_pin}:{port}: {err}")
    return 0, Path(target_dir or ".").resolve()


def _extract_received(manifest, zip_path, target_dir, force, remote=None):
    """Guard-scan the staged archive, then extract it atomically. Always
    removes the temporary archive."""
    try:
        p.progress("downloading project", 100)
        if remote:
            p.key_value("Remote", remote)
        with zip_path.open("rb") as zip_file:
            g.require_clean_archive(zip_file)
        destination = choose_target_dir(manifest, target_dir, force)
        p.key_value("Folder", destination)
        p.key_value("Files", manifest.get("file_count", "unknown"))
        with zip_path.open("rb") as zip_file:
            extracted = safe_extract(zip_file, destination, force, manifest)
        return extracted, destination
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass


def _receive_via_relay(relay_id, relay_code, target_dir, force, relay_url):
    base = None
    response = None
    try:
        base = r.ensure_relay_available(relay_url)
        p.configure_workload(files=1, bytes_=1024 * 1024)
        p.progress("connecting to relay", 0)
        p.key_value("Share code", relay_code)
        p.key_value("Relay", base)
        p.info("Requesting share from sender …")
        r.request_pull(relay_id, base_url=base)

        try:
            response = r.open_download_stream(relay_id, base_url=base)
        except TimeoutError:
            raise e.CliError(
                f"No sender is sharing {relay_code} right now. "
                f"On the sharing PC run: edr start <id>  (then pull again)."
            ) from None
        p.progress("connecting to relay", 100)

        # Stream the payload to a temp file instead of holding it in RAM.
        content_length = int(response.getheader("Content-Length", "0") or 0)
        manifest, zip_path = receive_payload_to_tempfile(response, progress_total=content_length)
        r.close_download_stream(response)
        response = None

        file_count = manifest.get("file_count")
        try:
            file_count = int(file_count)
        except (TypeError, ValueError):
            file_count = len(manifest.get("files", [])) or 1
        p.configure_workload(files=file_count, bytes_=content_length or 1024 * 1024)
        return _extract_received(manifest, zip_path, target_dir, force)
    except KeyboardInterrupt:
        if base:
            try:
                r.delete_room(relay_id, base_url=base)
            except RuntimeError:
                pass
        p.warn("Pull cancelled; partial received files were deleted.")
        raise
    except e.CliError:
        raise
    except TimeoutError:
        e.handle_error("ConnectionError", f"The relay stopped sending data for {relay_code}.")
    except zipfile.BadZipFile:
        e.handle_error("ConnectionError", "The transfer was interrupted or corrupted (incomplete archive).")
    except Exception as err:
        e.handle_error("ConnectionError", f"Failed to receive {relay_code}: {err}")
    finally:
        if response is not None:
            r.close_download_stream(response)
    return 0, Path(target_dir or ".").resolve()


def send_payload(conn, manifest, data):
    payload = _encode_payload(manifest, data)
    total = len(payload)
    sent = 0
    p.progress("sending payload", 0)
    while sent < total:
        chunk_size = min(65536, total - sent)
        conn.sendall(payload[sent:sent + chunk_size])
        sent += chunk_size
        p.progress("sending payload", int(sent * 100 / total))
    p.progress("sending payload", 100)


class _ProgressSocketWriter:
    """File-like writer for zipfile that batches small writes into large
    socket sends (zipfile emits many 8 KiB pieces) and reports progress."""

    def __init__(self, conn, total_bytes, buffer_size=SOCKET_CHUNK_SIZE):
        self._conn = conn
        self._total = max(1, int(total_bytes or 0))
        self._buffer = bytearray()
        self._buffer_size = buffer_size
        self.sent = 0

    def write(self, chunk):
        if not chunk:
            return 0
        self._buffer += chunk
        if len(self._buffer) >= self._buffer_size:
            self.flush()
        return len(chunk)

    def flush(self):
        if not self._buffer:
            return
        try:
            self._conn.sendall(self._buffer)
        except OSError as err:
            raise TransferAborted(f"receiver disconnected: {err}") from err
        self.sent += len(self._buffer)
        self._buffer.clear()
        p.progress("sending payload", min(99, int(self.sent * 100 / self._total)))


def send_payload_stream(conn, manifest, root_dir=".", include_cli=False, skip_guard=False, fast=False):
    header = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    writer = _ProgressSocketWriter(conn, manifest.get("total_bytes"))

    p.progress("sending payload", 0)
    try:
        conn.sendall(PROTOCOL_MAGIC + struct.pack("!Q", len(header)) + header)
    except OSError as err:
        raise TransferAborted(f"receiver disconnected: {err}") from err
    stream_zip_to_fileobj(
        writer,
        root_dir=root_dir,
        include_cli=include_cli,
        verbose=True,
        skip_guard=skip_guard,
        fast=fast,
    )
    writer.flush()
    p.progress("sending payload", 100)


def _read_exact(reader, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = reader.read(min(SOCKET_CHUNK_SIZE, remaining))
        if not chunk:
            raise ConnectionError("connection closed before payload header completed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_exact(sock, size):
    return _read_exact(_SocketReader(sock), size)


def receive_payload_to_tempfile(source, progress_total=None):
    """Read an EDR payload (magic + header + zip) from a socket or any object
    with .read(n) and stage the zip part in a temp file.
    Returns (manifest, Path-to-zip). The caller owns the temp file."""
    reader = source if hasattr(source, "read") else _SocketReader(source)
    magic = _read_exact(reader, len(PROTOCOL_MAGIC))
    if magic != PROTOCOL_MAGIC:
        raise e.CliError("Unsupported transfer protocol from sender.")

    header_size = struct.unpack("!Q", _read_exact(reader, 8))[0]
    if header_size > 16 * 1024 * 1024:
        raise e.CliError("Sender sent an invalid payload header.")
    manifest = json.loads(_read_exact(reader, header_size).decode("utf-8"))
    expected_bytes = int(progress_total or manifest.get("total_bytes") or 0)

    temp = tempfile.NamedTemporaryFile(prefix="edr-pull-", suffix=".zip", delete=False)
    temp_path = Path(temp.name)
    received = 0
    # A sender could otherwise stream an unbounded amount of data and fill the
    # receiver's disk before the post-download zip-bomb check ever runs.
    try:
        with temp:
            while True:
                chunk = reader.read(SOCKET_CHUNK_SIZE)
                if not chunk:
                    break
                temp.write(chunk)
                received += len(chunk)
                if received > MAX_EXTRACT_BYTES:
                    raise e.CliError(
                        f"Sender tried to send more than the max allowed download size "
                        f"({format_bytes(MAX_EXTRACT_BYTES)}). Aborted."
                    )
                if expected_bytes:
                    p.progress("downloading project", min(99, int(received * 100 / expected_bytes)))
                else:
                    p.progress("downloading project", min(99, received // (1024 * 64)))
        if received == 0 and manifest.get("file_count", 0):
            raise ConnectionError("connection closed before archive data was received")
        return manifest, temp_path
    except BaseException:
        try:
            temp.close()
        except OSError:
            pass
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def parse_payload(data):
    if not data.startswith(PROTOCOL_MAGIC):
        return {"root_name": "received-project", "files": [], "file_count": "unknown"}, io.BytesIO(data)

    offset = len(PROTOCOL_MAGIC)
    if len(data) < offset + 8:
        raise e.CliError("Incomplete EDR payload header.")
    header_size = struct.unpack("!Q", data[offset:offset + 8])[0]
    offset += 8
    if header_size > 16 * 1024 * 1024 or len(data) < offset + header_size:
        raise e.CliError("Invalid EDR payload header.")
    header = json.loads(data[offset:offset + header_size].decode("utf-8"))
    offset += header_size
    return header, io.BytesIO(data[offset:])


def choose_target_dir(manifest, target_dir=None, force=False):
    if target_dir:
        return Path(target_dir).expanduser().resolve()

    folder_name = safe_folder_name(manifest.get("root_name") or manifest.get("share_id") or "received-project")
    base = Path.cwd() / folder_name
    if force or not base.exists():
        return base.resolve()

    index = 1
    while True:
        candidate = Path.cwd() / f"{folder_name}-{index}"
        if not candidate.exists():
            return candidate.resolve()
        index += 1


def safe_folder_name(value):
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value.strip())
    return cleaned.strip(".-") or "received-project"


def _check_zip_bomb(archive):
    """Guard against ZIP bombs: cap total uncompressed size and flag any
    single member whose compression ratio is absurd (classic bomb shape)."""
    total_uncompressed = 0
    for member in archive.infolist():
        if member.is_dir():
            continue
        total_uncompressed += member.file_size
        if total_uncompressed > MAX_EXTRACT_BYTES:
            e.handle_error(
                "UnsafeArchive",
                f"Archive exceeds max allowed extracted size ({format_bytes(MAX_EXTRACT_BYTES)}).",
            )
        if member.compress_size > 0:
            ratio = member.file_size / member.compress_size
            if ratio > MAX_COMPRESSION_RATIO and member.file_size > 10 * 1024 * 1024:
                e.handle_error(
                    "UnsafeArchive",
                    f"Blocked suspicious archive member (compression ratio {ratio:.0f}x): {member.filename}",
                )


def _nearest_existing(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _mkdirs(path, base, created):
    """mkdir -p inside base, remembering which directories we created."""
    missing = []
    current = Path(path)
    while _is_relative_to(current, base) and not current.exists():
        missing.append(current)
        current = current.parent
    Path(path).mkdir(parents=True, exist_ok=True)
    created.extend(reversed(missing))


def safe_extract(buffer, target_dir=".", force=False, manifest=None):
    """Extract an archive all-or-nothing.

    1. Validate every member (paths, conflicts, size, disk space) before
       touching the destination.
    2. Write the files into a private staging folder inside the destination.
    3. Move them into place; each replaced file is parked first, so a failure
       or Ctrl+C at any point restores the destination exactly as it was
       (`--force` never loses the file it was about to overwrite).
    """
    base = Path(target_dir).resolve()
    base_existed = base.exists()
    staging = None

    try:
        with zipfile.ZipFile(buffer) as archive:
            _check_zip_bomb(archive)

            plan = []  # (member, relative path, destination)
            for member in archive.infolist():
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    e.handle_error("UnsafeArchive", f"Blocked unsafe path: {member.filename}")
                destination = (base / member_path).resolve()
                if not _is_relative_to(destination, base):
                    e.handle_error("UnsafeArchive", f"Blocked path outside target: {member.filename}")
                if member.is_dir():
                    if destination.exists() and not destination.is_dir():
                        e.handle_error("FileExists", f"{destination} exists and is not a folder.")
                else:
                    if destination.is_dir():
                        e.handle_error("FileExists", f"{destination} is a folder; cannot replace it with a file.")
                    if destination.exists() and not force:
                        e.handle_error("FileExists", f"{destination} exists. Use --force to overwrite it.")
                plan.append((member, member_path, destination))

            needed = sum(member.file_size for member, _, _ in plan if not member.is_dir())
            free = shutil.disk_usage(_nearest_existing(base)).free
            if needed > free:
                e.handle_error(
                    "DiskSpace",
                    f"Not enough disk space: need {format_bytes(needed)}, only {format_bytes(free)} free.",
                )

            base.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".edr-staging-", dir=base))
            new_dir = staging / "new"
            backup_dir = staging / "old"

            files = [item for item in plan if not item[0].is_dir()]
            total = len(files)
            p.progress("extracting files", 0)
            for index, (member, member_path, _destination) in enumerate(files, start=1):
                staged = new_dir / member_path
                staged.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, staged.open("wb") as output:
                    shutil.copyfileobj(source, output, SOCKET_CHUNK_SIZE)
                p.progress("extracting files", min(99, int(index * 100 / total)) if total else 99)

            moved = []  # (destination, parked original or None)
            created_dirs = []
            try:
                for member, member_path, destination in plan:
                    if member.is_dir():
                        _mkdirs(destination, base, created_dirs)
                        continue
                    _mkdirs(destination.parent, base, created_dirs)
                    parked = None
                    if destination.exists():
                        backup_dir.mkdir(exist_ok=True)
                        parked = backup_dir / str(len(moved))
                        os.replace(destination, parked)
                    os.replace(new_dir / member_path, destination)
                    moved.append((destination, parked))
            except BaseException:
                for destination, parked in reversed(moved):
                    try:
                        destination.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        p.warn(f"Could not remove partial file: {destination}")
                    if parked is not None:
                        os.replace(parked, destination)
                for dir_path in sorted(set(created_dirs), key=lambda item: len(item.parts), reverse=True):
                    try:
                        dir_path.rmdir()
                    except OSError:
                        pass
                raise

        p.progress("extracting files", 100)
        return total
    except BaseException:
        if not base_existed:
            shutil.rmtree(base, ignore_errors=True)
        raise
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def connect_to_server(ip_pin):
    extracted, _ = receive_project(ip_pin)
    return extracted > 0


def _is_relative_to(path, base):
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def format_bytes(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
