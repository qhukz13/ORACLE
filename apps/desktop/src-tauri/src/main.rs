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

/// The port the daemon serves on, matching `Settings.port`.
///
/// Read from the environment so the shell and `oracled` cannot disagree when someone moves it;
/// the default is duplicated from `src/oracle/config.py` because the shell holds no business
/// logic and cannot import Python to ask.
fn port() -> u16 {
    std::env::var("ORACLE_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8787)
}

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
    // ORACLE_NO_SIDECAR predates the attach path and is now mostly redundant — a developer
    // running `uv run oracled` by hand is attached to, not fought with. Kept because it is
    // also the way to open the window against a *deliberately* dead backend and watch the
    // offline state, which is a thing worth being able to do on purpose.
    let managed = if std::env::var("ORACLE_NO_SIDECAR").is_ok() {
        None
    } else {
        match Backend::connect(&workdir(), port()) {
            Ok(b) => {
                match b.pid() {
                    // ADR-0025: the daemon outlives the window. Closing this one leaves it running.
                    None => eprintln!("attached to a resident oracled on port {}", port()),
                    Some(pid) => eprintln!("oracled started, pid={pid} (owned by this shell)"),
                }
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
