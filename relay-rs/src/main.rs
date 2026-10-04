//! edr-relay: single-binary relay for EDR Project Sharer.
//!
//! Speaks exactly the HTTP protocol of the Python relay (`edr relay start`),
//! so the existing `edr` client works against it unchanged:
//!
//!   GET    /v1/health
//!   GET    /v1/rooms/{id}/status      -> {"ready","requested","waiting","consumed","bytes"}
//!   POST   /v1/rooms/{id}/wait        -> 204   (sender registers)
//!   POST   /v1/rooms/{id}/request     -> 204   (receiver asks for the share)
//!   PUT    /v1/rooms/{id}             -> 204   (one chunk; X-EDR-Offset / X-EDR-Total)
//!   GET    /v1/rooms/{id}             -> payload (room is consumed only after full delivery)
//!   DELETE /v1/rooms/{id}             -> 204

mod store;

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use bytes::Bytes;
use futures_util::stream;
use tokio::io::AsyncReadExt;

use store::{is_valid_room_id, Config, Rejected, Store};

const STREAM_BLOCK: u64 = 1024 * 1024;
const BODY_TIMEOUT: Duration = Duration::from_secs(120);
const USAGE: &str = "edr-relay - relay server for EDR Project Sharer

USAGE:
    edr-relay [--host <addr>] [--port <port>]

OPTIONS:
    --host <addr>   address to listen on (default 0.0.0.0)
    --port <port>   port to listen on (default 8765)
    -V, --version   print version
    -h, --help      print this help

ENVIRONMENT (same names as the Python relay):
    EDR_RELAY_MAX_BYTES        max size of one shared project   (default 8 GiB)
    EDR_RELAY_MAX_CHUNK_BYTES  max size of one upload request   (default 16 MiB)
    EDR_RELAY_MAX_ROOMS        max concurrent rooms             (default 1024)
    EDR_RELAY_IDLE_SECONDS     drop rooms idle this long        (default 3600)
    EDR_RELAY_SPOOL_DIR        where payloads are spooled       (default: private temp dir)
";

type Shared = Arc<Store>;

fn reply(code: StatusCode, content_type: &str, body: impl Into<Body>) -> Response {
    Response::builder()
        .status(code)
        .header(header::CONTENT_TYPE, content_type)
        .body(body.into())
        .expect("static response parts are valid")
}

fn text(code: StatusCode, message: &str) -> Response {
    reply(code, "text/plain", message.to_string())
}

fn no_content() -> Response {
    reply(StatusCode::NO_CONTENT, "text/plain", Body::empty())
}

fn rejected(err: Rejected) -> Response {
    text(StatusCode::from_u16(err.code).unwrap_or(StatusCode::BAD_REQUEST), err.message)
}

fn invalid_id() -> Response {
    text(StatusCode::BAD_REQUEST, "invalid room id")
}

async fn health(State(store): State<Shared>) -> Response {
    // The client sizes its upload chunks from max_chunk_bytes.
    let body = format!(r#"{{"ok":true,"max_chunk_bytes":{}}}"#, store.config().max_chunk_bytes);
    reply(StatusCode::OK, "application/json", body)
}

async fn status(State(store): State<Shared>, Path(id): Path<String>) -> Response {
    if !is_valid_room_id(&id) {
        return invalid_id();
    }
    reply(StatusCode::OK, "application/json", store.status(&id).to_json())
}

async fn wait(State(store): State<Shared>, Path(id): Path<String>) -> Response {
    store.register_wait(&id).map_or_else(rejected, |_| no_content())
}

async fn request(State(store): State<Shared>, Path(id): Path<String>) -> Response {
    store.request_pull(&id).map_or_else(rejected, |_| no_content())
}

async fn delete(State(store): State<Shared>, Path(id): Path<String>) -> Response {
    if !is_valid_room_id(&id) {
        return invalid_id();
    }
    store.delete(&id);
    no_content()
}

fn header_u64(headers: &HeaderMap, name: &str) -> Result<Option<u64>, ()> {
    match headers.get(name) {
        None => Ok(None),
        Some(value) => value.to_str().ok().and_then(|v| v.trim().parse::<u64>().ok()).map(Some).ok_or(()),
    }
}

async fn upload(State(store): State<Shared>, Path(id): Path<String>, headers: HeaderMap, body: Body) -> Response {
    if !is_valid_room_id(&id) {
        return invalid_id();
    }
    let (Ok(offset), Ok(total)) = (header_u64(&headers, "x-edr-offset"), header_u64(&headers, "x-edr-total")) else {
        return text(StatusCode::BAD_REQUEST, "malformed headers");
    };
    let limit = store.config().max_chunk_bytes as usize;
    let chunk = match tokio::time::timeout(BODY_TIMEOUT, axum::body::to_bytes(body, limit)).await {
        Err(_) => return text(StatusCode::REQUEST_TIMEOUT, "upload stalled"),
        Ok(Err(_)) => return text(StatusCode::PAYLOAD_TOO_LARGE, "chunk too large"),
        Ok(Ok(bytes)) => bytes,
    };
    let offset = offset.unwrap_or(0);
    let total = total.unwrap_or(chunk.len() as u64);
    let worker = store.clone();
    // Disk I/O happens off the async threads.
    match tokio::task::spawn_blocking(move || worker.put(&id, offset, total, &chunk)).await {
        Ok(Ok(())) => no_content(),
        Ok(Err(err)) => rejected(err),
        Err(_) => text(StatusCode::INTERNAL_SERVER_ERROR, "internal error"),
    }
}

struct Download {
    file: tokio::fs::File,
    store: Shared,
    id: String,
    sent: u64,
    size: u64,
    failed: bool,
}

impl Drop for Download {
    /// hyper drops the body once the response is finished. If every byte was
    /// handed to the connection by then, the transfer is complete: consume the
    /// room. If the receiver vanished earlier, `sent < size` and the payload
    /// stays available for a retry.
    fn drop(&mut self) {
        if !self.failed && self.sent >= self.size {
            self.store.consume(&self.id);
        }
    }
}

async fn download(State(store): State<Shared>, Path(id): Path<String>) -> Response {
    if !is_valid_room_id(&id) {
        return invalid_id();
    }
    let Some((path, size)) = store.open_payload(&id) else {
        return text(StatusCode::NOT_FOUND, "not ready");
    };
    let Ok(file) = tokio::fs::File::open(&path).await else {
        return text(StatusCode::NOT_FOUND, "not ready");
    };

    // The room is consumed only when the last byte has been handed to the
    // connection (see `Drop for Download`). If the receiver disappears
    // (Ctrl+C, network drop) the room stays `ready` and a retry works.
    let state = Download { file, store: store.clone(), id, sent: 0, size, failed: false };
    let body = stream::unfold(state, |mut st| async move {
        if st.failed {
            return None;
        }
        if st.sent >= st.size {
            return None;
        }
        let want = (st.size - st.sent).min(STREAM_BLOCK) as usize;
        let mut buf = vec![0u8; want];
        match st.file.read_exact(&mut buf).await {
            Ok(_) => {
                st.sent += want as u64;
                Some((Ok::<Bytes, std::io::Error>(Bytes::from(buf)), st))
            }
            Err(err) => {
                st.failed = true;
                Some((Err(err), st))
            }
        }
    });

    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/octet-stream")
        .header(header::CONTENT_LENGTH, size)
        .body(Body::from_stream(body))
        .expect("static response parts are valid")
}

fn router(store: Shared) -> Router {
    Router::new()
        .route("/v1/health", get(health))
        .route("/v1/rooms/{id}/status", get(status))
        .route("/v1/rooms/{id}/wait", post(wait))
        .route("/v1/rooms/{id}/request", post(request))
        .route("/v1/rooms/{id}", get(download).put(upload).delete(delete))
        .fallback(|| async { text(StatusCode::NOT_FOUND, "not found") })
        .with_state(store)
}

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        if let Ok(mut sig) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            sig.recv().await;
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! { _ = ctrl_c => {}, _ = terminate => {} }
}

fn default_spool_dir() -> (PathBuf, bool) {
    if let Some(dir) = std::env::var_os("EDR_RELAY_SPOOL_DIR") {
        return (PathBuf::from(dir), false);
    }
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    (std::env::temp_dir().join(format!("edr-relay-{}-{nanos}", std::process::id())), true)
}

/// Spool dirs left behind by relays that were killed (SIGKILL, power loss).
/// Live relays refresh their dir's mtime via `heartbeat`, so only abandoned
/// ones age past the cutoff.
fn purge_stale_spools() {
    let cutoff = Duration::from_secs(2 * 3600);
    let Ok(entries) = std::fs::read_dir(std::env::temp_dir()) else { return };
    for entry in entries.flatten() {
        let is_ours = entry.file_name().to_string_lossy().starts_with("edr-relay-");
        let stale = entry
            .metadata()
            .ok()
            .filter(|meta| meta.is_dir())
            .and_then(|meta| meta.modified().ok())
            .and_then(|modified| modified.elapsed().ok())
            .is_some_and(|age| age > cutoff);
        if is_ours && stale {
            let _ = std::fs::remove_dir_all(entry.path());
        }
    }
}

fn heartbeat(spool: &std::path::Path) {
    let marker = spool.join(".alive");
    if std::fs::write(&marker, b"").is_ok() {
        let _ = std::fs::remove_file(&marker);
    }
}

fn parse_args() -> Result<(String, u16), String> {
    let mut host = "0.0.0.0".to_string();
    let mut port = 8765u16;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "-h" | "--help" => {
                print!("{USAGE}");
                std::process::exit(0);
            }
            "-V" | "--version" => {
                println!("edr-relay {}", env!("CARGO_PKG_VERSION"));
                std::process::exit(0);
            }
            "--host" => host = args.next().ok_or("--host needs a value")?,
            "--port" => {
                let value = args.next().ok_or("--port needs a value")?;
                port = value.parse().map_err(|_| format!("invalid port '{value}'"))?;
                if port == 0 {
                    return Err("port must be between 1 and 65535".into());
                }
            }
            other => return Err(format!("unknown argument '{other}' (try --help)")),
        }
    }
    Ok((host, port))
}

#[tokio::main]
async fn main() {
    let (host, port) = match parse_args() {
        Ok(parsed) => parsed,
        Err(message) => {
            eprintln!("edr-relay: {message}");
            std::process::exit(2);
        }
    };

    let cfg = Config::from_env();
    let idle = cfg.idle;
    let (spool, owns_spool) = default_spool_dir();
    if owns_spool {
        purge_stale_spools();
    }
    let store = match Store::new(cfg, spool.clone()) {
        Ok(store) => Arc::new(store),
        Err(err) => {
            eprintln!("edr-relay: cannot use spool dir {}: {err}", spool.display());
            std::process::exit(1);
        }
    };

    let listener = match tokio::net::TcpListener::bind((host.as_str(), port)).await {
        Ok(listener) => listener,
        Err(err) => {
            eprintln!("edr-relay: cannot listen on {host}:{port}: {err}");
            std::process::exit(1);
        }
    };
    println!("edr-relay {} listening on http://{host}:{port} (spool: {})", env!("CARGO_PKG_VERSION"), spool.display());

    let sweeper = store.clone();
    tokio::spawn(async move {
        let every = Duration::from_secs((idle.as_secs() / 4).clamp(30, 300));
        loop {
            tokio::time::sleep(every).await;
            sweeper.sweep(idle);
            heartbeat(sweeper.spool_dir());
        }
    });

    if let Err(err) = axum::serve(listener, router(store.clone())).with_graceful_shutdown(shutdown_signal()).await {
        eprintln!("edr-relay: server error: {err}");
    }

    store.cleanup();
    if owns_spool {
        let _ = std::fs::remove_dir_all(store.spool_dir());
    }
    println!("edr-relay stopped.");
}
