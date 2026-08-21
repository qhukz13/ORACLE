//! Supervises the `oracled` backend as a child process.
//!
//! The whole point of this module is OQ-11: **when the shell dies, the backend must
//! die with it.** An orphaned `oracled` holding the database and the port is a
//! confusing failure mode and a bad first-run experience.
//!
//! Dropping a `std::process::Child` does not kill it, and neither does a normal
//! `on_window_event` handler when the user force-quits from Task Manager. The only
//! mechanism Windows offers that survives a hard kill is a **Job Object** with
//! `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: when the last handle to the job closes — which
//! the OS does for us on process death, however violent — every process in the job is
//! terminated.

use std::os::windows::io::AsRawHandle;
use std::process::{Child, Command, Stdio};

use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject,
    JobObjectExtendedLimitInformation, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

pub struct Backend {
    child: Child,
    job: HANDLE,
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
    /// Spawn `oracled` inside a kill-on-close job object.
    pub fn spawn(workdir: &std::path::Path) -> std::io::Result<Self> {
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

        Ok(Self { child, job })
    }

    pub fn pid(&self) -> u32 {
        self.child.id()
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        // Graceful first, then the job object guarantees the rest.
        let _ = self.child.kill();
        let _ = self.child.wait();
        unsafe {
            let _ = CloseHandle(self.job);
        }
    }
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
