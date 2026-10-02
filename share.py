import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import tempfile
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
IGNORE_DIRS = {'.edr', '.git', '__pycache__', 'venv', '.venv', 'node_modules', '.mypy_cache', '.pytest_cache', 'dist', 'build', 'python', 'launcher'}
IGNORE_FILES = {'project_payload.zip'}
CLI_FILES = {
    'command.py', 'handler.py', 'share.py', 'error.py', 'print.py', 'relay.py', 'guard.py',
    'watch.py', 'qrterm.py', 'doctor_checks.py',
}


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


def _is_ignored_file(filename, include_cli):
    if filename in IGNORE_FILES:
        return True
    return not include_cli and filename in CLI_FILES


def iter_project_files(root_dir=".", include_cli=False):
    base = Path(root_dir).resolve()
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
        for filename in sorted(files):
            if _is_ignored_file(filename, include_cli):
                continue
            path = Path(root, filename)
            if path.is_file():
                yield path, path.relative_to(base)


def project_summary(root_dir=".", include_cli=False):
    files = list(iter_project_files(root_dir, include_cli))
    total_bytes = sum(path.stat().st_size for path, _ in files)
    return {
        "files": len(files),
        "bytes": total_bytes,
        "include_cli": include_cli,
        "ignored_dirs": ", ".join(sorted(IGNORE_DIRS)),
    }


def build_manifest(root_dir=".", include_cli=False, share_id=None, non_network=False, relay_code=None):
    root = Path(root_dir).resolve()
    files = []
    for path, archive_path in iter_project_files(root, include_cli):
        files.append({
            "path": archive_path.as_posix(),
            "bytes": path.stat().st_size,
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
    files = list(iter_project_files(root_dir, include_cli))
    total_bytes = sum(path.stat().st_size for path, _ in files)
    p.configure_workload(files=len(files), bytes_=total_bytes)
    if not skip_guard:
        g.require_clean_project(root_dir, include_cli)
    memory_file = io.BytesIO()
    try:
        total = len(files)
        if verbose:
            p.progress("scanning project", 0)

        compression = zipfile.ZIP_STORED if fast else zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(memory_file, 'w', compression) as zipf:
            for index, (path, archive_path) in enumerate(files, start=1):
                if verbose:
                    if index == 1:
                        p.progress("scanning project", 100)
                    percent = int(index * 100 / total) if total else 100
                    p.progress("copying files", percent)
                zipf.write(path, archive_path)

            if verbose:
                p.progress("compressing archive", 50)
        if verbose:
            p.progress("compressing archive", 100)
        return memory_file.getvalue()
    except Exception as err:
            e.handle_error("BundleError", str(err))


def stream_zip_to_fileobj(fileobj, root_dir=".", include_cli=False, verbose=False, skip_guard=False, fast=False):
    files = list(iter_project_files(root_dir, include_cli))
    total_bytes = sum(path.stat().st_size for path, _ in files)
    p.configure_workload(files=len(files), bytes_=total_bytes)
    if not skip_guard:
        g.require_clean_project(root_dir, include_cli)

    total = len(files)
    written_source_bytes = 0
    try:
        if verbose:
            p.progress("scanning project", 100)
            p.progress("copying files", 0)

        compression = zipfile.ZIP_STORED if fast else zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(fileobj, "w", compression) as zipf:
            for index, (path, archive_path) in enumerate(files, start=1):
                zipf.write(path, archive_path)
                try:
                    written_source_bytes += path.stat().st_size
                except OSError:
                    pass
                if verbose:
                    if total_bytes:
                        percent = int(written_source_bytes * 100 / total_bytes)
                    else:
                        percent = int(index * 100 / total) if total else 100
                    p.progress("copying files", min(99, percent))

        if verbose:
            p.progress("copying files", 100)
            p.progress("compressing archive", 100)
    except Exception as err:
        e.handle_error("BundleError", str(err))


def bundle_to_file(output_path="project_payload.zip", root_dir=".", include_cli=False, force=False, skip_guard=False, fast=False):
    target = Path(output_path)
    if target.exists() and not force:
        e.handle_error("FileExists", f"{target} already exists. Use --force to overwrite it.")

    data = bundle_to_memory(root_dir, include_cli, skip_guard=skip_guard, fast=fast)
    target.write_bytes(data)
    return target, len(data)


def _print_pull_qr(remote, port=None):
    text = qr.pull_command_text(remote, port=port)
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
        p.info(f"Pull command: edr pull {code}")
        if show_qr:
            _print_pull_qr(code)
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

            with conn:
                p.progress("waiting for receiver", 100)
                p.success(f"Connected by {addr[0]}:{addr[1]}")
                _send_once(conn, root, include_cli, share_id, network_label="LAN", skip_guard=skip_guard, fast=fast)
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
        p.info(f"On another device: edr pull {code}")

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
            p.error(str(err))
            return

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
            data = bundle_to_memory(root, include_cli, verbose=True, skip_guard=skip_guard, fast=fast)
            p.progress("sharing files", 0)
            payload = _encode_payload(manifest, data)
            p.progress("sharing files", 50)

            def upload_progress(percent):
                p.progress("sharing files", min(99, 50 + percent // 2))

            r.upload_payload(relay_id, payload, on_progress=upload_progress, base_url=base)
            p.progress("sharing files", 90)
            p.info("Waiting for receiver to finish downloading ...")
            r.wait_until_consumed(relay_id, base_url=base)
        except KeyboardInterrupt:
            r.delete_room(relay_id, base_url=base)
            p.warn("Share cancelled; relay payload was deleted.")
            raise
        except TimeoutError as err:
            r.delete_room(relay_id, base_url=base)
            p.error(str(err))
            return
        except Exception:
            r.delete_room(relay_id, base_url=base)
            raise
        p.progress("sharing files", 100)
        p.success(f"Shared {format_bytes(len(payload))} via relay — receiver finished.")
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


def _encode_payload(manifest, data):
    header = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    return PROTOCOL_MAGIC + struct.pack("!Q", len(header)) + header + data


def receive_project(ip_pin, port=DEFAULT_PORT, target_dir=None, force=False, relay_url=None):
    relay_id, relay_code = r.parse_relay_remote(ip_pin)
    if relay_id:
        return _receive_via_relay(relay_id, relay_code, target_dir, force, relay_url)

    try:
        p.progress("connecting to sharer", 0)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(15)
            client.connect((ip_pin, port))
            client.settimeout(None)
            p.progress("connecting to sharer", 100)

            manifest, zip_path = receive_payload_to_tempfile(client)

        try:
            p.progress("downloading project", 100)
            with zip_path.open("rb") as zip_file:
                g.require_clean_archive(zip_file)
            destination = choose_target_dir(manifest, target_dir, force)
            p.key_value("Remote", f"{ip_pin}:{port}")
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
    except e.CliError:
        raise
    except Exception as err:
        e.handle_error("ConnectionError", f"Failed to receive from {ip_pin}:{port}: {err}")
        return 0, Path(target_dir or ".").resolve()


def _receive_via_relay(relay_id, relay_code, target_dir, force, relay_url):
    base = None
    try:
        base = r.ensure_relay_available(relay_url)
        p.configure_workload(files=1, bytes_=1024 * 1024)
        p.progress("connecting to relay", 0)
        p.key_value("Share code", relay_code)
        p.key_value("Relay", base)
        p.info("Requesting share from sender …")
        r.request_pull(relay_id, base_url=base)

        def download_progress(percent):
            p.progress("downloading project", percent)

        try:
            raw = r.download_payload(relay_id, on_progress=download_progress, base_url=base)
        except TimeoutError:
            raise e.CliError(
                f"No sender is sharing {relay_code} right now. "
                f"On the sharing PC run: edr start <id>  (then pull again)."
            ) from None
        p.progress("connecting to relay", 100)
        manifest, zip_buffer = parse_payload(raw)
        g.require_clean_archive(zip_buffer)
        zip_buffer.seek(0)
        file_count = manifest.get("file_count")
        try:
            file_count = int(file_count)
        except (TypeError, ValueError):
            file_count = len(manifest.get("files", [])) or 1
        p.configure_workload(files=file_count, bytes_=len(raw))
        destination = choose_target_dir(manifest, target_dir, force)
        p.key_value("Folder", destination)
        p.key_value("Files", manifest.get("file_count", "unknown"))
        extracted = safe_extract(zip_buffer, destination, force, manifest)
        return extracted, destination
    except KeyboardInterrupt:
        if base:
            r.delete_room(relay_id, base_url=base)
        p.warn("Pull cancelled; partial received files were deleted.")
        raise
    except e.CliError:
        raise
    except Exception as err:
        e.handle_error("ConnectionError", f"Failed to receive {relay_code}: {err}")
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


def send_payload_stream(conn, manifest, root_dir=".", include_cli=False, skip_guard=False, fast=False):
    header = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    total_source_bytes = max(1, int(manifest.get("total_bytes") or 0))
    sent_source_bytes = 0

    class ProgressSocketWriter:
        def write(self, chunk):
            nonlocal sent_source_bytes
            if not chunk:
                return 0
            conn.sendall(chunk)
            sent_source_bytes += len(chunk)
            p.progress("sending payload", min(99, int(sent_source_bytes * 100 / total_source_bytes)))
            return len(chunk)

        def flush(self):
            return None

    p.progress("sending payload", 0)
    conn.sendall(PROTOCOL_MAGIC)
    conn.sendall(struct.pack("!Q", len(header)))
    conn.sendall(header)
    stream_zip_to_fileobj(
        ProgressSocketWriter(),
        root_dir=root_dir,
        include_cli=include_cli,
        verbose=True,
        skip_guard=skip_guard,
        fast=fast,
    )
    p.progress("sending payload", 100)


def _recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(min(SOCKET_CHUNK_SIZE, remaining))
        if not chunk:
            raise ConnectionError("connection closed before payload header completed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def receive_payload_to_tempfile(sock):
    magic = _recv_exact(sock, len(PROTOCOL_MAGIC))
    if magic != PROTOCOL_MAGIC:
        raise e.CliError("Unsupported transfer protocol from sender.")

    header_size = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    if header_size > 16 * 1024 * 1024:
        raise e.CliError("Sender sent an invalid payload header.")
    manifest = json.loads(_recv_exact(sock, header_size).decode("utf-8"))
    expected_bytes = int(manifest.get("total_bytes") or 0)

    temp = tempfile.NamedTemporaryFile(prefix="edr-pull-", suffix=".zip", delete=False)
    temp_path = Path(temp.name)
    received = 0
    try:
        with temp:
            while True:
                chunk = sock.recv(SOCKET_CHUNK_SIZE)
                if not chunk:
                    break
                temp.write(chunk)
                received += len(chunk)
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


MAX_EXTRACT_BYTES = int(os.environ.get("EDR_MAX_EXTRACT_BYTES", 20 * 1024 * 1024 * 1024))  # 20 GiB
MAX_COMPRESSION_RATIO = int(os.environ.get("EDR_MAX_COMPRESSION_RATIO", 300))


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


def safe_extract(buffer, target_dir=".", force=False, manifest=None):
    base = Path(target_dir).resolve()
    base_existed = base.exists()
    base.mkdir(parents=True, exist_ok=True)
    extracted = 0
    created_files = []
    created_dirs = []
    files_by_name = {item["path"]: item for item in (manifest or {}).get("files", [])}

    try:
        with zipfile.ZipFile(buffer) as archive:
            _check_zip_bomb(archive)
            members = [member for member in archive.infolist() if not member.is_dir()]
            total = len(members)
            p.progress("extracting files", 0)
            for member in archive.infolist():
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    e.handle_error("UnsafeArchive", f"Blocked unsafe path: {member.filename}")

                destination = (base / member_path).resolve()
                if not _is_relative_to(destination, base):
                    e.handle_error("UnsafeArchive", f"Blocked path outside target: {member.filename}")

                if member.is_dir():
                    if not destination.exists():
                        created_dirs.append(destination)
                    destination.mkdir(parents=True, exist_ok=True)
                    continue

                if destination.exists() and not force:
                    e.handle_error("FileExists", f"{destination} exists. Use --force to overwrite it.")

                parents_to_create = []
                parent = destination.parent
                while _is_relative_to(parent, base) and not parent.exists():
                    parents_to_create.append(parent)
                    parent = parent.parent
                destination.parent.mkdir(parents=True, exist_ok=True)
                created_dirs.extend(reversed(parents_to_create))
                with archive.open(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, SOCKET_CHUNK_SIZE)
                created_files.append(destination)
                extracted += 1
                percent = int(extracted * 100 / total) if total else 100
                p.progress("extracting files", percent)

        p.progress("extracting files", 100)
        return extracted
    except BaseException:
        cleanup_partial_extract(base, base_existed, created_files, created_dirs)
        raise


def cleanup_partial_extract(base, base_existed, created_files, created_dirs):
    if not base_existed:
        shutil.rmtree(base, ignore_errors=True)
        return

    for file_path in reversed(created_files):
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            p.warn(f"Could not remove partial file: {file_path}")

    for dir_path in sorted(set(created_dirs), key=lambda item: len(item.parts), reverse=True):
        try:
            dir_path.rmdir()
        except OSError:
            pass


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
