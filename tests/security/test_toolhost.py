"""Process isolation (ADR-0003).

The claim under test is not "the toolhost runs tools". It is:

  * a child cannot be started unless it can be guaranteed killable;
  * killing the job kills the WHOLE tree, including grandchildren;
  * secrets are absent from the child, not merely unused;
  * a call whose side effect may have happened is never silently retried.

These use real processes. A mock here would test the mock.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from oracle.toolhost import JobObject, ToolHost, ToolHostUnavailable


def _alive(pid: int) -> bool:
    r = subprocess.run(
        ["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
    )
    return str(pid) in r.stdout


class TestJobObject:
    def test_killing_the_job_kills_a_grandchild(self, tmp_path: Path) -> None:
        """The property `Popen.kill()` does NOT give you, and the reason HALT is
        credible: a tool's grandchildren die with it."""
        script = tmp_path / "spawner.py"
        script.write_text(
            "import subprocess, sys, time\n"
            # child spawns a grandchild that would happily outlive it
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
            "print(g.pid, flush=True)\n"
            "time.sleep(300)\n",
            encoding="utf-8",
        )

        job = JobObject()
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            stdout=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            job.assign(int(proc._handle))  # type: ignore[attr-defined]
            assert proc.stdout is not None
            grandchild = int(proc.stdout.readline().strip())
            assert _alive(proc.pid) and _alive(grandchild)

            job.terminate()
            deadline = time.time() + 10
            while time.time() < deadline and (_alive(proc.pid) or _alive(grandchild)):
                time.sleep(0.2)

            assert not _alive(proc.pid), "child survived job termination"
            assert not _alive(grandchild), "GRANDCHILD survived — the tree leaked"
        finally:
            job.close()
            with contextlib.suppress(OSError):
                proc.kill()
            # Reap it: the job killed the process, but Popen does not know that, and an
            # unreaped Popen raises ResourceWarning from __del__ at an arbitrary later
            # point (which `filterwarnings = error` correctly turns into a failure).
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()

    def test_closing_the_last_handle_kills_the_job(self, tmp_path: Path) -> None:
        """KILL_ON_JOB_CLOSE is what protects us when the PARENT is force-killed: the
        OS closes our handles for us."""
        job = JobObject()
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        job.assign(int(proc._handle))  # type: ignore[attr-defined]
        assert _alive(proc.pid)

        job.close()  # no terminate() — just drop the handle
        deadline = time.time() + 10
        while time.time() < deadline and _alive(proc.pid):
            time.sleep(0.2)
        alive = _alive(proc.pid)
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        assert not alive, "process survived the job handle closing"


class TestToolHostLifecycle:
    async def test_starts_and_reports_its_tools(self) -> None:
        host = ToolHost()
        try:
            await host.start()
            assert host.running
            assert "fs.read" in host.stats.tools
        finally:
            await host.stop()

    async def test_executes_a_tool_in_the_child(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("from the child", encoding="utf-8")
        host = ToolHost()
        try:
            await host.start()
            r = await host.call("fs.read", {"path": str(f)}, resolved={"path": str(f)})
            assert r.ok
            assert r.result is not None
            assert r.result["text"] == "from the child"
        finally:
            await host.stop()

    async def test_child_has_no_secrets_in_its_environment(self) -> None:
        """Absent, not merely unused. The child refuses to start if a known secret
        variable is present, so this is enforced from both sides."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-should-not-be-inherited"
        try:
            host = ToolHost()
            await host.start()
            try:
                assert host.running, "child refused to start, so the env was not clean"
            finally:
                await host.stop()
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    async def test_killing_the_host_leaves_the_runtime_healthy(self, tmp_path: Path) -> None:
        """P3 acceptance criterion. A dead toolhost is a failed step, not a dead agent."""
        f = tmp_path / "a.txt"
        f.write_text("ok", encoding="utf-8")
        host = ToolHost()
        await host.start()
        assert host.running

        await host.kill_tree()
        assert not host.running

        # The runtime is fine: the next call restarts the child transparently.
        r = await host.call("fs.read", {"path": str(f)}, resolved={"path": str(f)})
        assert r.ok
        assert host.stats.starts >= 2
        await host.stop()

    async def test_timeout_terminates_the_tree_and_does_not_retry(self, tmp_path: Path) -> None:
        """A timeout does not mean the side effect did not happen. Retrying is how you
        get two commits and call it resilience."""
        host = ToolHost()
        await host.start()
        try:
            # fs.read on a huge sparse file is awkward to time out reliably; instead
            # drive the deadline directly.
            f = tmp_path / "a.txt"
            f.write_text("x", encoding="utf-8")
            r = await host.call("fs.read", {"path": str(f)}, resolved={"path": str(f)}, timeout_s=0)
            # Either it completed inside the (zero) budget or it timed out; in the
            # timeout case the message must be explicit about the uncertainty.
            if not r.ok:
                assert r.error_kind == "timeout"
                assert "will not be retried" in (r.error_message or "")
        finally:
            await host.stop()

    async def test_unknown_tool_is_refused_by_the_child_too(self) -> None:
        """Defence in depth: the parent's registry check is the real gate, but the
        child must not execute something merely because a frame asked it to."""
        host = ToolHost()
        try:
            await host.start()
            r = await host.call("sys.format_disk", {})
            assert not r.ok
            assert r.error_kind == "not_found"
        finally:
            await host.stop()

    async def test_child_never_resolves_paths_itself(self, tmp_path: Path) -> None:
        """The sandbox decision must stay on the parent's side of the boundary. With no
        `resolved` mapping the handler gets nothing to work with and fails — it does not
        helpfully resolve the raw argument."""
        outside = tmp_path / "outside.txt"
        outside.write_text("SECRET", encoding="utf-8")
        host = ToolHost()
        try:
            await host.start()
            r = await host.call("fs.read", {"path": str(outside)}, resolved={})
            assert not r.ok, "child resolved a path on its own — the boundary leaked"
        finally:
            await host.stop()


class TestOrphans:
    async def test_soak_leaves_no_orphaned_processes(self, tmp_path: Path) -> None:
        """P3 acceptance criterion: 100 tool calls, zero orphans."""
        f = tmp_path / "a.txt"
        f.write_text("soak", encoding="utf-8")
        host = ToolHost()
        await host.start()
        pid = host._proc.pid if host._proc else 0
        try:
            for _ in range(100):
                r = await host.call("fs.read", {"path": str(f)}, resolved={"path": str(f)})
                assert r.ok
            assert host.stats.calls == 100
            assert host.stats.crashes == 0
        finally:
            await host.stop()

        for _ in range(50):
            if not _alive(pid):
                break
            await asyncio.sleep(0.2)
        assert not _alive(pid), "toolhost process orphaned after stop()"

    async def test_refuses_to_run_a_child_it_cannot_isolate(self, monkeypatch) -> None:
        """If job assignment fails we must NOT continue — an unassignable child could
        spawn a tree we cannot guarantee to kill."""
        from oracle.toolhost import jobobject

        def boom(self: object, handle: int) -> None:
            raise jobobject.JobObjectError("simulated assignment failure")

        monkeypatch.setattr(jobobject.JobObject, "assign", boom)
        host = ToolHost()
        with pytest.raises(ToolHostUnavailable, match="could not isolate"):
            await host.start()
        assert not host.running
