# EDR Agent Handoff

This file is for the next coding session. The user's real goal is broader than the current patch: make EDR a very fast, reliable project sharer, fix relay mode, add useful features, harden cancellation cleanup, find bugs/loopholes, and keep the README accurate.

## Current State

Recent work already changed the main transfer path:

- LAN transfers now stream the ZIP payload directly to the socket instead of building the entire protocol payload in memory first.
- Receivers stage incoming LAN payloads in a temp ZIP file before extraction.
- Extraction attempts to clean up newly-created receiver-side files when extraction fails or is interrupted.
- Relay server now uses `ThreadingHTTPServer`.
- Relay rooms can be deleted with `DELETE /v1/rooms/<room_id>`.
- Relay upload now checks chunk offset continuity before marking a room ready.
- `--relay-url` was added to create/edit/start/push/share/pull paths so both machines can target the same relay host.
- `--fast` was added to skip ZIP compression using `ZIP_STORED`, which can improve throughput on fast LANs and already-compressed files.
- README and CLI help were updated for relay URL, fast mode, streaming transfers, and cancellation cleanup.

Verified in the previous session:

- `python -m py_compile share.py relay.py handler.py guard.py print.py doctor_checks.py watch.py qrterm.py command.py`
- Local LAN end-to-end pull using the streaming path.
- Local relay end-to-end pull using an explicit relay URL.
- Fast-mode LAN transfer.
- Partial extraction cleanup with an invalid ZIP.
- CLI smoke checks: `python command.py help` and `python command.py status --path .`

## Important Files

- `share.py`: LAN transfer, bundling, receiving, extraction, cancellation cleanup.
- `relay.py`: HTTP relay server/client, room state, chunk upload/download.
- `handler.py`: CLI parser and command routing.
- `print.py`: CLI help and progress rendering.
- `guard.py`: safety scan before sending and after receiving.
- `README.md`: user-facing docs.

## What Is Still Left

### 0. IMPORTANT: main was force-reverted once already (fixed 2026-10-02)

The `684aaef "Your message here"` commit on `main` silently rolled relay.py/share.py/handler.py/README.md
back to an old pre-streaming, pre-`--fast`, pre-cancellation-cleanup state (lost `delete_room`,
the DELETE endpoint, `ThreadingHTTPServer`, and broke the chunk-completeness check). This was caught
by diffing the clone against the last known-good working copy and restored, with the relay store also
hardened and disk-backed in the same pass (see below). If a future session finds relay.py looking
suspiciously simpler than this file, diff against git history before assuming it's "already done."

### 1. Relay memory usage — RESOLVED 2026-10-02

The relay server (`relay.py`) is now disk-backed: `_RelayStore` writes chunks straight to a per-room
spool file via `os.pwrite` instead of holding them in a dict, and `do_GET` streams the response from
disk instead of loading it into memory. Room IDs are validated (`is_valid_room_id`), chunk/room size
caps exist (`MAX_CHUNK_BYTES`, `MAX_ROOM_BYTES`), and an idle sweeper thread cleans up abandoned rooms
(`ROOM_IDLE_TIMEOUT`). Covered by `tests/test_relay.py`.

Still open: the **sender** side (`bundle_to_memory` + `upload_payload` in `_serve_via_relay`) still
builds the whole zip in memory before uploading, and the **receiver** still downloads the full relay
payload into `raw` before extracting. Only the relay server itself is disk-backed/streaming so far —
making the client (sender+receiver) side streaming is the next real step here.
- Delete relay temp files after successful download, timeout, cancellation, or explicit `DELETE`.
- Add limits for max room size, max idle time, and cleanup of abandoned rooms.

### 2. LAN streaming still creates a ZIP stream, not per-file protocol

LAN is better now, but the receiver cannot start extracting until it has the full ZIP file. For huge projects, a faster architecture would be:

- Send a manifest first.
- Stream each file independently with path, size, hash, and mode metadata.
- Receiver writes each file directly to a staging directory.
- On success, atomically move staging into the final destination.
- On cancel/failure, delete the staging directory only.

This would reduce temp ZIP overhead and make cancellation cleanup simpler.

### 2A. User idea: ultra-fast archive push

The user specifically suggested a speed idea:

- Compress everything into a `tar` or `7z` archive.
- Do this very quickly, potentially multiple times per second.
- Push the archive all at once.
- Unzip/extract it on the receiver.

Treat this as an idea to evaluate, not as a finished design. Important engineering notes for the next session:

- Compressing the whole project multiple times per second will usually be slower for large projects because CPU and disk reads become the bottleneck.
- A better version may be incremental archive snapshots: detect changed files, package only changed chunks/files, and push those.
- For maximum LAN speed, `tar` with no compression can be faster than ZIP/7z because it is mostly sequential I/O.
- For slow networks, `7z` or compressed ZIP may help because smaller payloads can beat CPU cost.
- For already-compressed assets, use no compression or low compression.
- Consider a new mode such as `--archive tar`, `--archive zip`, `--archive 7z`, or `--compression none|fast|best`.
- If using 7z, check whether a bundled or system `7z` is available on Windows/macOS/Linux before depending on it.
- The receiver should extract into a staging directory first, then atomically move into the final destination after success.
- If the user cancels while pushing or extracting, delete only receiver-side staging/partial files and relay temp payloads. Never delete the sharer's source folder.

### 3. Cancellation semantics need stronger real-world tests

Current cleanup should remove partial receiver-side extracted files. It has not been tested with actual Ctrl+C during:

- LAN download before full ZIP arrives.
- LAN extraction mid-file.
- Relay upload mid-transfer.
- Relay download mid-transfer.
- Receiver cancellation while sender is waiting for `wait_until_consumed`.

Next session should add automated tests that simulate cancellation by terminating the sender/receiver process mid-transfer and then checking:

- Sender's original files are untouched.
- Receiver's partial destination is removed or restored.
- Relay room/temp payload is deleted.
- A new transfer can reuse the same relay code afterward.

### 4. Performance progress reporting is noisy and imperfect

Progress currently reports ZIP writer bytes against source bytes, which can be inaccurate with compression and tiny files. The previous smoke tests showed many repeated progress lines.

Improve by:

- Throttling progress updates by time or percent change.
- Reporting source scan, archive write, network send, download, scan, and extraction clearly.
- Avoiding duplicated progress phases when sender and receiver run in the same console during tests.

### 5. Security and loophole hardening remains incomplete

Review for:

- Relay room ID validation: only allow expected characters and length.
- HTTP path parsing: reject malformed paths, huge headers, invalid offsets, negative offsets, duplicate overlapping chunks, and wrong totals.
- ZIP bombs or excessive uncompressed size during receive scan/extract.
- Symlink entries in ZIP archives.
- File permission/mode surprises across platforms.
- Race conditions when files change during bundling.
- `--skip-guard` visibility: make sure users know it disables safety scanning.

### 6. Add proper tests — partially done 2026-10-02

`tests/test_relay.py` exists and covers: small/multi-chunk/empty payload roundtrips, re-upload reset,
wait/request lifecycle, delete, spool-file cleanup, room-id validation (including over real HTTP),
oversized-chunk rejection, mismatched-total rejection, and idle sweeping. Run with
`python -m unittest tests.test_relay -v`.

Still missing:

- `tests/test_share.py`: manifest creation, LAN streamed transfer, fast mode uses stored ZIP entries,
  `safe_extract` path traversal + the new zip-bomb guard (`_check_zip_bomb` in share.py), partial
  extraction cleanup.
- `tests/test_handler.py`: CLI parser accepts `--relay-url`, `--fast`, `--no-fast`, legacy aliases.

### 7. README needs another pass after final architecture

README is updated for the current behavior, but after relay streaming/disk-backed storage and real cancellation tests, update it again with:

- Exact relay deployment examples.
- Limits and cleanup behavior.
- Performance tips for LAN versus relay.
- What `--fast` does and when not to use it.
- Troubleshooting for firewall, port, and relay URL mistakes.

## Suggested Next Session Order

1. Reinspect `share.py`, `relay.py`, and `handler.py` current state.
2. Add a minimal test harness first so new transfer changes can be verified repeatedly.
3. Convert relay storage from memory-backed to temp-file-backed.
4. Add relay room validation, size limits, idle cleanup, and delete cleanup.
5. Add process-level cancellation tests.
6. Improve progress throttling.
7. Run full smoke tests:
   - compile all Python files
   - LAN normal transfer
   - LAN `--fast` transfer
   - relay transfer with explicit `--relay-url`
   - cancellation during receive
   - relay room cleanup after cancellation
8. Update README and help text to match the final behavior.

## Known Commands

```powershell
python -m py_compile share.py relay.py handler.py guard.py print.py doctor_checks.py watch.py qrterm.py command.py
python command.py help
python command.py status --path .
```

Manual relay shape:

```powershell
python command.py relay start --host 0.0.0.0 --port 8765
python command.py create sharer . --non-network --idnew --relay-url http://127.0.0.1:8765
python command.py start <id>
python command.py pull Edrnko_<id> --relay-url http://127.0.0.1:8765
```

## Caution

This is a Git repository (github.com/enderairstudio/Edr). Check `git log` before assuming the working
tree reflects the latest known-good state — see item 0 above for why that bit once already.
