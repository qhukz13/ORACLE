"""`app.launch`: the alias catalogue, and the one tool that escapes the Job Object.

Two claims are being defended.

**The model never names an executable.** It names an alias, and the mapping to a path
is a file a human wrote. An executable path chosen by a model is an arbitrary-execution
primitive; no amount of confirmation makes one safe to accept.

**The launched process is detached on purpose.** Everything else runs inside the
toolhost's Job Object so HALT can kill a whole process tree. An app the user asked for
must survive that — HALT means "stop what you are doing", not "close my editor". This
is the only carve-out, and these tests pin its shape so it cannot quietly widen.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from oracle.policy.apps import AppCatalogue, AppRejected
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.model import Decision, Tier
from oracle.toolhost import ToolHost
from oracle.tools import Approval, ToolErrorKind, ToolExecutor, build_registry

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
tools:
  fs.read:    {{ tier: T0, scopes: [projects] }}
  app.launch: {{ tier: T1, scopes: [projects] }}
"""

APPS = """
apps:
  fake:
    path: "{exe}"
    tier: T1
    accepts_path: true
    description: "a harmless stand-in"
  noargs:
    path: "{exe}"
    tier: T1
    accepts_path: false
  costly:
    path: "{exe}"
    tier: T2
    accepts_path: false
    description: "something that costs to open"
  missing:
    path: "C:/nowhere/not-installed.exe"
    tier: T1
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    root.mkdir()
    (root / "note.txt").write_text("hello", encoding="utf-8")
    return root


@pytest_asyncio.fixture
async def ex(tmp_path: Path, workspace: Path) -> AsyncIterator[ToolExecutor]:
    # A real, harmless executable: the interpreter running this test. `app.launch`
    # really does start it, so the detachment claim is tested rather than described.
    apps_file = tmp_path / "apps.yaml"
    apps_file.write_text(APPS.format(exe=Path(sys.executable).as_posix()), encoding="utf-8")
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(POLICY.format(root=workspace.as_posix()), encoding="utf-8")

    host = ToolHost()
    executor = ToolExecutor(
        build_registry(),
        PolicyEngine(load_policy(policy_file, apps_file)),
        AuditLog(tmp_path / "audit.jsonl"),
        host=host,
    )
    try:
        yield executor
    finally:
        await host.stop()


def _alive(pid: int) -> bool:
    r = subprocess.run(
        ["tasklist", "/fi", f"PID eq {pid}", "/nh"], capture_output=True, text=True
    )
    return str(pid) in r.stdout


class TestOnlyAliases:
    async def test_an_unknown_alias_is_refused_naming_the_rule(self, ex: ToolExecutor) -> None:
        out = await ex.execute("app.launch", {"app": "solitaire"})
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.DENIED
        assert out.error.detail == "apps.catalogue"

    async def test_an_executable_path_is_not_an_alias(self, ex: ToolExecutor) -> None:
        """The attack this design exists to make impossible: naming the binary."""
        out = await ex.execute("app.launch", {"app": r"C:\Windows\System32\cmd.exe"})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED

    async def test_a_catalogued_app_that_is_not_installed_refuses(
        self, ex: ToolExecutor
    ) -> None:
        out = await ex.execute("app.launch", {"app": "missing"})
        assert not out.ok
        assert out.error is not None and out.error.detail == "apps.missing.path"

    def test_an_empty_catalogue_can_open_nothing(self) -> None:
        empty = AppCatalogue.parse(None)
        assert empty.aliases == []
        with pytest.raises(AppRejected):
            empty.resolve("editor")

    def test_a_missing_catalogue_file_is_empty_not_fatal(self, tmp_path: Path) -> None:
        """Same reasoning as policy's lockdown, one file down: if we cannot read the
        catalogue, ORACLE opens nothing rather than opening anything."""
        assert AppCatalogue.load(tmp_path / "absent.yaml").aliases == []


class TestPathsAreStillScoped:
    async def test_a_path_outside_the_scope_is_refused(
        self, ex: ToolExecutor
    ) -> None:
        out = await ex.execute("app.launch", {"app": "fake", "path": r"C:\Windows\win.ini"})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED

    async def test_an_alias_that_takes_no_path_refuses_one(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        out = await ex.execute(
            "app.launch", {"app": "noargs", "path": str(workspace / "note.txt")}
        )
        assert not out.ok
        assert out.error is not None
        assert "does not take a path argument" in out.error.message

    async def test_omitting_the_optional_path_is_not_a_path_check(
        self, ex: ToolExecutor
    ) -> None:
        """An unset optional path must not be canonicalised as the string "None"."""
        out = await ex.execute("app.launch", {"app": "noargs"})
        assert out.ok, out.error and out.error.message
        _terminate(out)


class TestTierComesFromTheCatalogue:
    async def test_a_t2_app_asks_first(self, ex: ToolExecutor) -> None:
        """`app.launch` is T1 in policy, but the catalogue can raise it — opening a
        browser is not the same act as opening Explorer."""
        verdict, digest = ex.preview("app.launch", {"app": "costly"})
        assert verdict.decision is Decision.CONFIRM
        assert verdict.tier is Tier.T2
        assert verdict.rule == "apps.costly"

        out = await ex.execute("app.launch", {"app": "costly"})
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_REQUIRED

        ex.grant(
            Approval(
                approval_id="ap",
                tool="app.launch",
                args_digest=digest,
                tier=Tier.T2,
                expires_at=time.time() + 60,
            )
        )
        approved = await ex.execute("app.launch", {"app": "costly"}, approval_id="ap")
        assert approved.ok, approved.error and approved.error.message
        _terminate(approved)

    async def test_a_t1_app_opens_without_asking(self, ex: ToolExecutor) -> None:
        out = await ex.execute("app.launch", {"app": "noargs"})
        assert out.ok
        assert out.verdict.decision is Decision.ALLOW
        _terminate(out)


class TestDetachment:
    async def test_the_launched_app_survives_the_toolhost_dying(
        self, ex: ToolExecutor
    ) -> None:
        """The claim, tested rather than asserted in a comment.

        If `app.launch` ran inside the toolhost, killing the job would take the app with
        it — which is what would happen to a user's editor on the next HALT.
        """
        out = await ex.execute("app.launch", {"app": "noargs"})
        assert out.ok, out.error and out.error.message
        pid = out.result.pid  # type: ignore[union-attr]
        assert _alive(pid)

        # Everything the toolhost owns dies here, grandchildren included.
        await ex._host.kill_tree()  # type: ignore[union-attr]

        assert _alive(pid), "the launched application died with the toolhost"
        _terminate(out)

    async def test_the_contract_forbids_mixing_a_launcher_with_a_spawner(self) -> None:
        """The carve-out is exactly one shape. A tool that could both escape the job and
        run an allowlisted program in the parent would be a much larger hole."""
        contract = build_registry().get("app.launch")
        assert contract.app_field == "app"
        assert not contract.programs
        assert contract.program_field is None


def _terminate(outcome: object) -> None:
    """Close what a test opened. The tool deliberately keeps no handle, so this uses
    the pid it reported — the same thing a user closing the window would do."""
    result = getattr(outcome, "result", None)
    pid = getattr(result, "pid", None)
    if isinstance(pid, int):
        try:
            os.kill(pid, 9)
        except (OSError, PermissionError):
            pass
