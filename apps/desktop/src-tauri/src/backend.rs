//! Attach to a resident `oracled`, or start one and own it.
//!
//! **This module used to say the opposite, and the reversal is deliberate.**
//! [OQ-11](../../../../docs/OPEN_QUESTIONS.md#oq-11) asked whether the Python sidecar dies when the
//! Tauri shell is force-quit, and answered *yes, via a Job Object with
//! `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`*. That was the right answer for the architecture it was
//! asked in: ORACLE was a desktop app with a sidecar, and an orphaned `oracled` holding the
//! database and the port was a bad first-run experience.
//!
//! [ADR-0025](../../../../docs/DECISIONS.md#adr-0025--oracle-is-a-resident-service-the-window-is-a-client)
//! changed the architecture. ORACLE is a resident service and the window is a client, so P13's
//! acceptance list includes *"closing the window does not stop work; reopening it loses nothing"* —
//! which the job object made **impossible**. See
//! [ADR-0030](../../../../docs/DECISIONS.md#adr-0030--the-shell-attaches-to-a-resident-daemon-and-only-owns-one-it-started).
//!
//! The rule that replaces it is about *ownership*, not lifetime:
//!
//! - **A daemon we found, we do not touch.** It may be the service, or a developer's terminal.
//!   Killing someone else's process because our window closed is the bug, not the feature.
//! - **A daemon we started, we still clean up** — in the same job object, for the same reason
//!   OQ-11 gave. Nothing else knows it exists, so an orphan here is still an orphan.
//!
//! Once `oracled` is installed as a service the second case stops happening in normal use, and
//! the shell becomes what ADR-0007 always said it was: a window.

use std::io::{Read, Write};
use std::net::{Shutdown, SocketAddr, TcpStream};
use std::os::windows::io::AsRawHandle;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject,
    JobObjectExtendedLimitInformation, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

/// How long to wait for a resident daemon to answer before deciding to start our own.
///
/// Short on purpose: this is on the path to the first window paint, which P13 budgets at ~400 ms.
/// A daemon that is up answers `/health` in single-digit milliseconds because the route is
/// deliberately state-free; anything slower than this is not a daemon we can attach to anyway.
const PROBE_TIMEOUT: Duration = Duration::from_millis(300);

pub enum Backend {
    /// Someone else's daemon — the service, or a developer running `uv run oracled`. We hold no
    /// handle to it and we do not stop it. This is the case ADR-0025 exists for.
    Attached,
    /// One we started. Nothing else knows it exists, so it dies with us — OQ-11's mechanism,
    /// kept for exactly the case OQ-11 was actually about.
    Owned { child: Child, job: HANDLE },
}

// SAFETY: a Win32 HANDLE is a process-wide kernel object reference, not a thread-bound
// pointer. The only operation we perform on it after creation is `CloseHandle` in
// `Drop`, and `Backend` is owned exclusively by Tauri's managed state behind a Mutex,
// so there is no aliasing. Tauri requires `Send + Sync` for managed state; the
// `windows` crate declines to assert it for the raw pointer newtype, so we assert it
// here at the one place where the invariant is actually known to hold.
unsafe impl Send for Backend {}
unsafe impl Sync for Backend {}

impl Backend {
    /// Attach to a daemon already serving `port`, or start one in a kill-on-close job.
    pub fn connect(workdir: &std::path::Path, port: u16) -> std::io::Result<Self> {
        if daemon_is_serving(port) {
            return Ok(Self::Attached);
        }
        Self::spawn(workdir)
    }

    /// Spawn `oracled` inside a kill-on-close job object.
    fn spawn(workdir: &std::path::Path) -> std::io::Result<Self> {
        let job = unsafe { create_kill_on_close_job() }?;

        // Dev shape: `uv run oracled` from the repo root. Production packaging ships a
        // frozen sidecar binary instead — deferred, tracked in docs/current_report.md.
        let child = Command::new("uv")
            .args(["run", "oracled"])
            .current_dir(workdir)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;

        unsafe {
            let handle = HANDLE(child.as_raw_handle() as _);
            // If assignment fails the child would outlive us, so refuse to continue
            // with a backend we cannot guarantee to clean up.
            if AssignProcessToJobObject(job, handle).is_err() {
                let _ = CloseHandle(job);
                return Err(std::io::Error::other(
                    "failed to assign oracled to the job object",
                ));
            }
        }

        Ok(Self::Owned { child, job })
    }

    /// What this shell did, for the log line. `None` when we attached to someone else's.
    pub fn pid(&self) -> Option<u32> {
        match self {
            Self::Attached => None,
            Self::Owned { child, .. } => Some(child.id()),
        }
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        match self {
            // The whole point. A resident daemon keeps running when the window closes.
            Self::Attached => {}
            Self::Owned { child, job } => {
                // Graceful first, then the job object guarantees the rest.
                let _ = child.kill();
                let _ = child.wait();
                unsafe {
                    let _ = CloseHandle(*job);
                }
            }
        }
    }
}

/// Is *ORACLE* serving on this port — not merely, is something listening?
///
/// The distinction matters and a bare `TcpStream::connect` gets it wrong. Port 8787 answering
/// could be any process; attaching to it would leave the shell pointed at a stranger with no
/// daemon of its own, and the UI would sit in a reconnect loop against a server that will never
/// speak the protocol. So this sends a real `GET /health` and requires ORACLE's own answer.
///
/// Hand-rolled rather than pulling in an HTTP client: the request is one line, the response we
/// care about is two fields, and the shell holds zero business logic by ADR-0007 — a dependency
/// here would be the largest thing in the crate.
fn daemon_is_serving(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, PROBE_TIMEOUT) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(PROBE_TIMEOUT));
    let _ = stream.set_write_timeout(Some(PROBE_TIMEOUT));

    let request = format!("GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    // Bounded read: a hostile or broken server must not be able to hold the window open by
    // never closing the connection. 1 KiB is far more than `{"status":"ok"}` plus headers.
    let mut buf = [0u8; 1024];
    let mut read = 0usize;
    while read < buf.len() {
        match stream.read(&mut buf[read..]) {
            Ok(0) => break,
            Ok(n) => read += n,
            Err(_) => break,
        }
    }
    let _ = stream.shutdown(Shutdown::Both);

    let body = String::from_utf8_lossy(&buf[..read]);
    body.starts_with("HTTP/1.1 200") && body.contains("\"status\":\"ok\"")
}

unsafe fn create_kill_on_close_job() -> std::io::Result<HANDLE> {
    let job = CreateJobObjectW(None, None).map_err(std::io::Error::other)?;

    let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

    let ok = SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        &info as *const _ as *const core::ffi::c_void,
        std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
    );
    if ok.is_err() {
        let _ = CloseHandle(job);
        return Err(std::io::Error::other("SetInformationJobObject failed"));
    }
    Ok(job)
}

#[cfg(test)]
mod tests {
    use super::daemon_is_serving;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    /// A one-shot listener that answers the first request with `response`, then closes.
    /// Returns the port it bound, so tests never race each other over a fixed one.
    fn serve_once(response: &'static str) -> u16 {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind");
        let port = listener.local_addr().expect("addr").port();
        thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buf = [0u8; 512];
                let _ = stream.read(&mut buf);
                let _ = stream.write_all(response.as_bytes());
            }
        });
        port
    }

    #[test]
    fn a_closed_port_is_not_a_daemon() {
        // Port 1 on loopback: reserved and nothing binds it. The shell must decide to start
        // its own rather than attach to nothing.
        assert!(!daemon_is_serving(1));
    }

    #[test]
    fn oracles_own_health_answer_is_recognised() {
        let port = serve_once(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ok\"}",
        );
        assert!(daemon_is_serving(port));
    }

    #[test]
    fn a_listener_that_is_not_oracle_is_not_attached_to() {
        // The reason this probe sends a real request instead of just connecting. Port 8787
        // answering could be anything; attaching to a stranger would leave the shell with no
        // daemon of its own and the UI in a reconnect loop against a server that will never
        // speak the protocol — a failure that looks like ORACLE being broken.
        let port = serve_once("HTTP/1.1 200 OK\r\n\r\n<html>hello from some other app</html>");
        assert!(!daemon_is_serving(port));
    }

    #[test]
    fn a_daemon_that_is_up_but_unhealthy_is_still_not_attached_to() {
        // 503 from something that *is* ORACLE-shaped. Starting our own is the safe read:
        // ADR-0011 makes a degraded daemon report itself as ONLINE, so a non-200 here means
        // something is wrong enough that it is not serving, not merely missing a capability.
        let port = serve_once("HTTP/1.1 503 Service Unavailable\r\n\r\n{\"status\":\"ok\"}");
        assert!(!daemon_is_serving(port));
    }
}
