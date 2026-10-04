//! Disk-backed room store. Same semantics as the Python relay (relay.py):
//! chunks are written straight into a per-room spool file, a room becomes
//! `ready` once the bytes are contiguous up to the declared total, and a
//! fully delivered room leaves a tiny `consumed` tombstone behind.

use std::collections::{BTreeMap, HashMap};
use std::fs::{self, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[derive(Clone, Debug)]
pub struct Config {
    pub max_room_bytes: u64,
    pub max_chunk_bytes: u64,
    pub max_rooms: usize,
    pub idle: Duration,
}

impl Config {
    /// Same variable names as the Python relay, plus EDR_RELAY_MAX_ROOMS.
    pub fn from_env() -> Self {
        fn var<T: std::str::FromStr>(name: &str, default: T) -> T {
            std::env::var(name).ok().and_then(|v| v.trim().parse().ok()).unwrap_or(default)
        }
        Config {
            max_room_bytes: var("EDR_RELAY_MAX_BYTES", 8u64 * 1024 * 1024 * 1024),
            max_chunk_bytes: var("EDR_RELAY_MAX_CHUNK_BYTES", 16u64 * 1024 * 1024),
            max_rooms: var("EDR_RELAY_MAX_ROOMS", 1024usize),
            idle: Duration::from_secs(var("EDR_RELAY_IDLE_SECONDS", 3600u64)),
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct Rejected {
    pub code: u16,
    pub message: &'static str,
}

fn reject<T>(code: u16, message: &'static str) -> Result<T, Rejected> {
    Err(Rejected { code, message })
}

#[derive(Debug, PartialEq, Eq)]
pub struct Status {
    pub ready: bool,
    pub requested: bool,
    pub waiting: bool,
    pub consumed: bool,
    pub bytes: u64,
}

impl Status {
    pub fn to_json(&self) -> String {
        format!(
            r#"{{"ready":{},"requested":{},"waiting":{},"consumed":{},"bytes":{}}}"#,
            self.ready, self.requested, self.waiting, self.consumed, self.bytes
        )
    }
}

/// Room ids are `[a-z0-9]{4,64}` (the Python side generates 10 random chars).
pub fn is_valid_room_id(id: &str) -> bool {
    (4..=64).contains(&id.len()) && id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
}

struct Room {
    path: PathBuf,
    received: BTreeMap<u64, u64>, // offset -> length
    received_bytes: u64,
    total: Option<u64>,
    ready: bool,
    requested: bool,
    waiting: bool,
    consumed: bool,
    touched: Instant,
}

impl Room {
    fn new(path: PathBuf) -> Self {
        Room {
            path,
            received: BTreeMap::new(),
            received_bytes: 0,
            total: None,
            ready: false,
            requested: false,
            waiting: false,
            consumed: false,
            touched: Instant::now(),
        }
    }

    fn unlink(&self) {
        let _ = fs::remove_file(&self.path);
    }
}

pub struct Store {
    cfg: Config,
    spool: PathBuf,
    rooms: Mutex<HashMap<String, Room>>,
}

impl Store {
    pub fn new(cfg: Config, spool: PathBuf) -> std::io::Result<Self> {
        fs::create_dir_all(&spool)?;
        // Leftovers of a previous run would otherwise sit on disk forever.
        if let Ok(entries) = fs::read_dir(&spool) {
            for entry in entries.flatten() {
                if entry.path().extension().is_some_and(|ext| ext == "part") {
                    let _ = fs::remove_file(entry.path());
                }
            }
        }
        Ok(Store { cfg, spool, rooms: Mutex::new(HashMap::new()) })
    }

    pub fn config(&self) -> &Config {
        &self.cfg
    }

    pub fn spool_dir(&self) -> &Path {
        &self.spool
    }

    fn path_for(&self, id: &str) -> PathBuf {
        self.spool.join(format!("{id}.part"))
    }

    fn check_capacity(&self, rooms: &HashMap<String, Room>, id: &str) -> Result<(), Rejected> {
        if !rooms.contains_key(id) && rooms.len() >= self.cfg.max_rooms {
            return reject(503, "relay is full, try again later");
        }
        Ok(())
    }

    pub fn register_wait(&self, id: &str) -> Result<(), Rejected> {
        if !is_valid_room_id(id) {
            return reject(400, "invalid room id");
        }
        let mut rooms = self.rooms.lock().unwrap();
        self.check_capacity(&rooms, id)?;
        if let Some(old) = rooms.get(id) {
            old.unlink();
        }
        let mut room = Room::new(self.path_for(id));
        room.waiting = true;
        rooms.insert(id.to_string(), room);
        Ok(())
    }

    pub fn request_pull(&self, id: &str) -> Result<(), Rejected> {
        if !is_valid_room_id(id) {
            return reject(400, "invalid room id");
        }
        let mut rooms = self.rooms.lock().unwrap();
        self.check_capacity(&rooms, id)?;
        let room = rooms.entry(id.to_string()).or_insert_with(|| Room::new(self.path_for(id)));
        room.requested = true;
        room.waiting = true;
        room.touched = Instant::now();
        Ok(())
    }

    /// Store one chunk. `offset == 0` starts a fresh upload (like the Python
    /// relay) while keeping the room's requested/waiting flags.
    pub fn put(&self, id: &str, offset: u64, total: u64, chunk: &[u8]) -> Result<(), Rejected> {
        if !is_valid_room_id(id) {
            return reject(400, "invalid room id");
        }
        let len = chunk.len() as u64;
        if len > self.cfg.max_chunk_bytes {
            return reject(413, "chunk too large");
        }
        if total > self.cfg.max_room_bytes {
            return reject(413, "room exceeds max relay size");
        }
        if offset.checked_add(len).is_none_or(|end| end > total) {
            return reject(400, "chunk lies outside the declared total size");
        }

        let mut rooms = self.rooms.lock().unwrap();
        self.check_capacity(&rooms, id)?;
        if offset == 0 {
            let (requested, waiting) = rooms.get(id).map_or((false, false), |old| {
                old.unlink();
                (old.requested, old.waiting)
            });
            let mut fresh = Room::new(self.path_for(id));
            fresh.requested = requested;
            fresh.waiting = waiting;
            rooms.insert(id.to_string(), fresh);
        }
        let room = rooms.entry(id.to_string()).or_insert_with(|| Room::new(self.path_for(id)));
        if room.total.is_some_and(|known| known != total) {
            return reject(409, "total size changed mid-upload");
        }
        room.total = Some(total);
        room.touched = Instant::now();

        // Always create the spool file, even for a zero-byte payload.
        let mut options = OpenOptions::new();
        options.write(true).create(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&room.path).map_err(|_| Rejected { code: 500, message: "cannot write spool file" })?;
        if !chunk.is_empty() {
            file.seek(SeekFrom::Start(offset))
                .and_then(|_| file.write_all(chunk))
                .map_err(|_| Rejected { code: 500, message: "cannot write spool file" })?;
            if let Some(previous) = room.received.insert(offset, len) {
                room.received_bytes -= previous;
            }
            room.received_bytes += len;
        }

        let mut next = 0u64;
        let mut complete = total == 0;
        for (&chunk_offset, &chunk_len) in &room.received {
            if chunk_offset != next {
                complete = false;
                break;
            }
            next += chunk_len;
            complete = next == total;
        }
        if complete {
            room.ready = true;
        }
        Ok(())
    }

    pub fn open_payload(&self, id: &str) -> Option<(PathBuf, u64)> {
        let mut rooms = self.rooms.lock().unwrap();
        let room = rooms.get_mut(id)?;
        if !room.ready {
            return None;
        }
        room.touched = Instant::now();
        let size = fs::metadata(&room.path).ok()?.len();
        Some((room.path.clone(), size))
    }

    /// The whole payload was delivered: drop the bytes, keep a tombstone so a
    /// sender that polls late still learns that the transfer succeeded.
    pub fn consume(&self, id: &str) {
        let mut rooms = self.rooms.lock().unwrap();
        if let Some(room) = rooms.get(id) {
            room.unlink();
            let mut tombstone = Room::new(self.path_for(id));
            tombstone.consumed = true;
            rooms.insert(id.to_string(), tombstone);
        }
    }

    pub fn delete(&self, id: &str) {
        let mut rooms = self.rooms.lock().unwrap();
        if let Some(room) = rooms.remove(id) {
            room.unlink();
        }
    }

    pub fn status(&self, id: &str) -> Status {
        let rooms = self.rooms.lock().unwrap();
        match rooms.get(id) {
            Some(room) => Status {
                ready: room.ready,
                requested: room.requested,
                waiting: room.waiting,
                consumed: room.consumed,
                bytes: room.received_bytes,
            },
            None => Status { ready: false, requested: false, waiting: false, consumed: false, bytes: 0 },
        }
    }

    /// Drop rooms idle for longer than `idle`. Returns how many were removed.
    pub fn sweep(&self, idle: Duration) -> usize {
        let mut rooms = self.rooms.lock().unwrap();
        let stale: Vec<String> = rooms
            .iter()
            .filter(|(_, room)| room.touched.elapsed() > idle)
            .map(|(id, _)| id.clone())
            .collect();
        for id in &stale {
            if let Some(room) = rooms.remove(id) {
                room.unlink();
            }
        }
        stale.len()
    }

    pub fn cleanup(&self) {
        let mut rooms = self.rooms.lock().unwrap();
        for room in rooms.values() {
            room.unlink();
        }
        rooms.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store(max_rooms: usize) -> (Store, PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "edr-relay-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
        ));
        let cfg = Config { max_room_bytes: 1000, max_chunk_bytes: 100, max_rooms, idle: Duration::from_secs(60) };
        (Store::new(cfg, dir.clone()).unwrap(), dir)
    }

    #[test]
    fn room_id_validation() {
        assert!(is_valid_room_id("abcd1234"));
        assert!(!is_valid_room_id("abc")); // too short
        assert!(!is_valid_room_id("ABCD1234")); // upper case
        assert!(!is_valid_room_id("abcd\n")); // trailing newline
        assert!(!is_valid_room_id("ab/../cd"));
        assert!(!is_valid_room_id(&"a".repeat(65)));
    }

    #[test]
    fn multi_chunk_upload_becomes_ready() {
        let (s, dir) = store(8);
        s.put("room1", 0, 150, &[1u8; 100]).unwrap();
        assert!(!s.status("room1").ready);
        s.put("room1", 100, 150, &[2u8; 50]).unwrap();
        let st = s.status("room1");
        assert!(st.ready && st.bytes == 150);
        let (path, size) = s.open_payload("room1").unwrap();
        assert_eq!(size, 150);
        let data = fs::read(path).unwrap();
        assert_eq!(&data[..100], &[1u8; 100][..]);
        assert_eq!(&data[100..], &[2u8; 50][..]);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn empty_payload_is_ready() {
        let (s, dir) = store(8);
        s.put("emptyroom", 0, 0, &[]).unwrap();
        assert!(s.status("emptyroom").ready);
        assert_eq!(s.open_payload("emptyroom").unwrap().1, 0);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn rejects_bad_requests() {
        let (s, dir) = store(8);
        assert_eq!(s.put("BAD", 0, 1, b"x").unwrap_err().code, 400);
        assert_eq!(s.put("room1", 0, 5000, b"x").unwrap_err().code, 413); // room cap
        assert_eq!(s.put("room1", 0, 500, &[0u8; 101]).unwrap_err().code, 413); // chunk cap
        // 1 byte at a huge offset must not create a huge spool file
        assert_eq!(s.put("room1", 900, 10, b"x").unwrap_err().code, 400);
        assert_eq!(s.put("room1", u64::MAX, 10, b"x").unwrap_err().code, 400);
        s.put("room1", 0, 100, &[0u8; 50]).unwrap();
        assert_eq!(s.put("room1", 50, 999, &[0u8; 50]).unwrap_err().code, 409);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn consume_leaves_tombstone_and_frees_disk() {
        let (s, dir) = store(8);
        s.put("room1", 0, 3, b"abc").unwrap();
        let (path, _) = s.open_payload("room1").unwrap();
        s.consume("room1");
        let st = s.status("room1");
        assert!(st.consumed && !st.ready);
        assert!(!path.exists());
        assert!(s.open_payload("room1").is_none());
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn reupload_resets_but_keeps_request_flags() {
        let (s, dir) = store(8);
        s.request_pull("room1").unwrap();
        s.put("room1", 0, 3, b"abc").unwrap();
        s.put("room1", 0, 5, b"hello").unwrap();
        let st = s.status("room1");
        assert!(st.requested && st.waiting && st.ready && st.bytes == 5);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn room_capacity_and_sweep() {
        let (s, dir) = store(2);
        s.register_wait("room1").unwrap();
        s.register_wait("room2").unwrap();
        assert_eq!(s.register_wait("room3").unwrap_err().code, 503);
        s.register_wait("room1").unwrap(); // existing rooms can be re-registered
        assert_eq!(s.sweep(Duration::from_secs(3600)), 0);
        std::thread::sleep(Duration::from_millis(20));
        assert_eq!(s.sweep(Duration::from_millis(1)), 2);
        assert!(!s.status("room1").waiting);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn delete_removes_spool_file() {
        let (s, dir) = store(8);
        s.put("room1", 0, 3, b"abc").unwrap();
        let (path, _) = s.open_payload("room1").unwrap();
        s.delete("room1");
        assert!(!path.exists());
        assert!(!s.status("room1").ready);
        fs::remove_dir_all(dir).ok();
    }
}
