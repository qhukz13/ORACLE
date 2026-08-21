// ORACLE desktop shell.
//
// Holds ZERO business logic (ADR-0007). It is a window plus a supervised child
// process; every capability lives behind the local API, which is what lets the
// browser and phone clients be first-class peers — and what makes replacing this
// shell a swap rather than a rewrite.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use std::path::PathBuf;
use std::sync::Mutex;

use backend::Backend;

struct Supervised(#[allow(dead_code)] Mutex<Option<Backend>>);

/// Repo root, four levels up from src-tauri/ in dev.
fn workdir() -> PathBuf {
    std::env::var("ORACLE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .and_then(|p| p.parent())
                .and_then(|p| p.parent())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."))
        })
}

fn main() {
    // ORACLE_NO_SIDECAR lets a developer run oracled by hand (reload, debugger)
    // without the shell fighting them for the port.
    let managed = if std::env::var("ORACLE_NO_SIDECAR").is_ok() {
        None
    } else {
        match Backend::spawn(&workdir()) {
            Ok(b) => {
                eprintln!("oracled started, pid={}", b.pid());
                Some(b)
            }
            Err(e) => {
                // Not fatal: the UI has a real offline state and will reconnect if the
                // user starts the backend themselves.
                eprintln!("could not start oracled: {e}");
                None
            }
        }
    };

    tauri::Builder::default()
        .manage(Supervised(Mutex::new(managed)))
        .run(tauri::generate_context!())
        .expect("failed to run ORACLE shell");
}
