//! Optional native helper. It deliberately has no network privileges: the Python
//! transfer layer remains responsible for protocol, safety policy, and UI.
use std::{env, fs, path::Path};

fn visit(path: &Path, root: &Path, files: &mut u64, bytes: &mut u64) -> std::io::Result<()> {
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let metadata = entry.metadata()?;
        let name = entry.file_name();
        if metadata.is_dir() {
            if !matches!(name.to_string_lossy().as_ref(), ".git" | ".edr" | "node_modules" | "venv" | ".venv" | "__pycache__" | "dist" | "build") {
                visit(&entry.path(), root, files, bytes)?;
            }
        } else if metadata.is_file() {
            *files += 1;
            *bytes += metadata.len();
        }
    }
    Ok(())
}

fn main() -> std::io::Result<()> {
    let root = env::args().nth(1).unwrap_or_else(|| ".".into());
    let mut files = 0; let mut bytes = 0;
    visit(Path::new(&root), Path::new(&root), &mut files, &mut bytes)?;
    // Stable, dependency-free JSON is easy for the Python UI/CLI to consume.
    println!(r#"{{"files":{},"bytes":{}}}"#, files, bytes);
    Ok(())
}
