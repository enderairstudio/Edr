# EDR Agent Handoff

This file is for the next coding session. The user's real goal: make EDR a very fast, reliable project
sharer, keep relay mode solid, add useful features, harden cancellation cleanup, find bugs/loopholes,
and keep the README accurate.

## Current State (updated after the October 2026 review)

Transfer path:

- **LAN:** the ZIP streams straight to the socket through `_ProgressSocketWriter` (batches zipfile's many
  small writes into 1 MiB sends). A receiver that aborts raises `TransferAborted`; `--auto` servers log it
  and keep serving.
- **Relay sender:** zips into a temp file (not RAM) and uploads header+zip from disk via
  `RelayClient.upload_stream` (kept-alive `http.client` connection, 4 MiB chunks sized from the relay's
  `/v1/health` `max_chunk_bytes`, retries on connection errors, 413 back-off for old relays).
- **Receivers (LAN and relay):** stream into a temp zip (`receive_payload_to_tempfile`, takes a socket or
  any `.read(n)` object), Guard-scan it, then `safe_extract`.
- **`safe_extract` is all-or-nothing:** validates every member first (paths, conflicts, zip bomb, free disk
  space), writes to `.edr-staging-*` inside the destination, then moves into place, parking replaced files
  so any failure or Ctrl+C restores the folder. `--force` can no longer destroy the file it overwrites.
- **Relay semantics:** a room is *consumed* only after the whole payload was delivered; consumed rooms
  leave a tombstone so `status.consumed` is true and a late-polling sender sees success. An aborted
  download keeps the payload for a retry. Python relay is HTTP/1.1 keep-alive, disk-backed with portable
  `os.lseek`+`os.write` (no `os.pwrite`, which does not exist on Windows), private temp spool dir, stale
  spool purge, strict routing, `offset+len <= total`.
- **Rust relay** in `relay-rs/` (`edr-relay`): same HTTP protocol, same env vars, same semantics.
  `edr relay start --engine auto|python|rust` (auto = Rust if `EDR_RELAY_BIN`/PATH/app dir has a binary).
- **Progress no longer sleeps** (`print.py`). The old "paced" display added up to 18 s per stage; it is now
  opt-in via `EDR_PACED_PROGRESS=1`. `prompt_name_countdown` returns immediately without a TTY.
- **File selection:** `CLI_FILES` are only filtered when the folder is the EDR CLI itself
  (`CLI_SIGNATURE`) and only at its root; `python/` and `launcher/` are root-only ignores; symlinks that
  leave the project are skipped; zip uses `strict_timestamps=False` (pre-1980 mtimes).
- **Guard:** no `__MACOSX` bypass, trailing dot/space and backslash normalisation, launcher scripts only
  at root, `.edr/` rejected in archives, windows `:` names rejected on win32, scans read only the 512 KiB
  window (no whole-file / whole-member reads).
- **Config safety:** `save_store` is atomic; legacy `.edr/sharers.json` is only migrated when the cwd is
  the EDR app dir (a received project could otherwise plant a sharer profile).
- `.deb` is `Architecture: all`.

## Important Files

- `share.py`: selection, bundling, LAN/relay send+receive, `safe_extract`.
- `relay.py`: relay client + Python relay server. `relay-rs/`: Rust relay (`src/store.rs`, `src/main.rs`).
- `handler.py`: CLI parser and commands. `print.py`: help + progress. `guard.py`: scanning.
- `tests/`: `test_share`, `test_handler`, `test_print`, `test_guard`, `test_relay`,
  `test_relay_protocol` (same suite against the Python AND Rust relay).
- `relay-rs/ci/relay-rs.yml`: ready-made workflow that builds/tests the Rust relay (separate from
  `release.yml`). NOT active yet: it has to be moved to `.github/workflows/`, and pushing workflow files
  needs a GitHub token with the `workflow` scope (the session token lacked it).

## Adding a Python module (checklist)

Update: `CLI_FILES` (+ `CLI_SIGNATURE` if it is core) in `share.py`, `check_handler_files` in
`doctor_checks.py`, `files` in `package.json`, the file lists in `build.ps1` / `build-unix.sh` /
`scripts/install-*.sh`, and the installer scripts. Prefer extending existing modules.

## Still Open

1. **Rust relay is not shipped yet.** The installers/release workflow do not build or bundle `edr-relay`;
   it is built manually (`cargo build --release --manifest-path relay-rs/Cargo.toml`). Wiring it into
   `release.yml` and the installers (put it next to the CLI so `find_rust_relay` picks it up) is the
   natural next step. The relay has no built-in slow-header protection: document/run it behind a proxy.
2. **Real Ctrl+C tests.** Aborts are tested by closing sockets and by raising `KeyboardInterrupt` inside
   the move phase of `safe_extract`. Process-level tests (SIGINT a real sender/receiver mid-transfer, then
   check the relay room, temp files and that the same code can be reused) are still missing.
3. **Per-file protocol idea.** The receiver still needs the whole ZIP before extracting. A manifest +
   per-file stream (path, size, hash, mode) into the staging dir would remove the temp ZIP and allow
   resumable transfers.
4. **Archive-mode idea from the user:** evaluate `tar` (uncompressed, fastest on a LAN) / `7z` / ZIP via
   something like `--compression none|fast|best`; check for a system `7z` before depending on it. Prefer
   incremental snapshots (changed files only) over re-archiving everything repeatedly. Never delete the
   sharer's folder on cancel.
5. **No transfer integrity hash.** ZIP CRCs catch corruption, but there is no end-to-end hash of the
   payload and no encryption; the relay sees plaintext (documented in the README security model).
6. **Relay chunk uploads are sequential** (one connection). Parallel chunk upload would help very
   high-latency links.
7. **Misc.** `edr edit --idnew` can clobber another profile with the same id (collision is astronomically
   unlikely); a `request_pull` that lands between consume and the sender's next `register_wait` is wiped
   in `--auto` relay mode; `scripts/install-linux.sh` does `rm -rf $EDR_INSTALL_DIR` (custom dir only).

## Known Commands

```bash
python -m unittest discover -s tests                         # everything
python -m unittest tests.test_relay_protocol -v              # Python + Rust relay conformance
cargo test --manifest-path relay-rs/Cargo.toml               # Rust unit tests
cargo build --release --manifest-path relay-rs/Cargo.toml    # binary for EDR_RELAY_BIN
python command.py help
```

Manual relay shape:

```bash
python command.py relay start --host 0.0.0.0 --port 8765
python command.py create sharer . --non-network --idnew --relay-url http://127.0.0.1:8765
python command.py start <id>
python command.py pull Edrnko_<id> --relay-url http://127.0.0.1:8765
```

## Caution

This is a Git repository (github.com/enderairstudio/Edr). `main` was force-reverted once (commit
`684aaef "Your message here"` rolled relay.py/share.py/handler.py/README.md back to an old state). If a
file looks suspiciously simpler than this document describes, diff against git history before assuming
something is "already done". Test tooling note: when benchmarking, do not `pkill -f command.py` from a
shell whose own command line contains that string.
