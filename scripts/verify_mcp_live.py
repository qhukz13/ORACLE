#!/usr/bin/env python
"""Prove the delegate can call back into ORACLE — the real CLI, the real bridge.

    uv run python scripts/verify_mcp_live.py --dry-run   # show the payload, send nothing
    uv run python scripts/verify_mcp_live.py             # confirm, then run

Closes P6-T3 requirement 1 and P6-T4 requirement 5 in one supervised egress. Everything
below the vendor is real: a live daemon on loopback, a real capability token, the real
`python -m oracle.mcp` bridge spawned by the real `claude` CLI, and the audit log read
afterwards to prove the call arrived.

The claim under test is the one INTEGRATIONS.md §4 makes and no offline test can:

    the delegate calls `mcp__oracle__fs_read` instead of shelling out, and the call
    lands in ORACLE's audit log, having gone through ORACLE's gate

This is the egress preview until the P6-T2 UI covers this path: it prints every byte
that will leave the machine and refuses to run until that payload is confirmed.

Auth is the machine's existing Claude subscription login — no key, no token
(see logs/development/2026-08-23-claude-auth-contract.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oracle.config import Settings
from oracle.mcp.tokens import TokenStore
from oracle.policy.audit import AuditLog

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"

#: A task the delegate can only complete by using ORACLE's tool: the file is inside the
#: workspace, and the answer is checkable without trusting the agent's report.
SECRET_LINE = "the answer is forty-two\n"  # noqa: S105 - a fixture sentence, not a credential
PROMPT = (
    "Use the ORACLE tool `mcp__oracle__fs_read` to read the file note.txt in this "
    "directory, and reply with the single word that follows 'the answer is'. "
    "Do not use Read, Bash, or any other tool to open it — only the ORACLE tool."
)


def preview(cmd: list[str], workspace: Path, port: int) -> None:
    print(f"\n{B}egress preview{X} — everything below leaves this machine (api.anthropic.com)\n")
    print(f"  {Y}prompt:{X} {PROMPT}")
    print(f"  {Y}workspace the delegate can read:{X} {workspace}")
    print(f"    note.txt: {D}{SECRET_LINE.strip()}{X}")
    print(f"  {Y}ORACLE tools lent:{X} fs.read, fs.list, git.status, git.diff, know.search,")
    print(f"    dev.run_tests {D}(read and verify only; T2+ is refused){X}")
    print(f"  {Y}daemon:{X} http://127.0.0.1:{port} {D}(loopback; the bridge forwards here){X}")
    print(f"  {Y}command:{X}")
    for part in cmd:
        print(f"    {D}{part}{X}")


async def run(dry_run: bool) -> int:
    settings = Settings()
    port = 8177  # not the daemon's usual port: this runs its own, and never touches yours
    workspace = Path(tempfile.mkdtemp(prefix="oracle-mcp-live-"))
    (workspace / "note.txt").write_text(SECRET_LINE, encoding="utf-8")

    tokens = TokenStore()
    token = tokens.mint("live-verify", workspace)
    config = {
        "mcpServers": {
            "oracle": {
                "command": sys.executable,
                "args": ["-m", "oracle.mcp"],
                "env": {
                    "ORACLE_MCP_URL": f"http://127.0.0.1:{port}",
                    "ORACLE_MCP_TOKEN": token,
                },
            }
        }
    }
    config_path = workspace / "mcp.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    cmd = [
        "claude",
        "-p",
        PROMPT,
        "--setting-sources",
        "user",
        "--strict-mcp-config",
        "--mcp-config",
        str(config_path),
        "--output-format",
        "stream-json",
        "--verbose",
        "--allowedTools",
        "mcp__oracle__fs_read",
        "--permission-mode",
        "dontAsk",
        "--add-dir",
        str(workspace),
    ]
    preview(cmd, workspace, port)

    if dry_run:
        print(f"\n{Y}dry run{X} — nothing sent, no daemon started.")
        return 0
    answer = await asyncio.to_thread(input, f"\nSend this payload? Type {G}yes{X} to proceed: ")
    if answer.strip().lower() != "yes":
        print("Cancelled. Nothing sent.")
        return 1

    # A daemon of this script's own, on its own port and its own data dir: the point is
    # to prove the path, not to disturb whatever the owner has running.
    server = await start_daemon(settings, port, workspace)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=300)
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)

    text = out.decode("utf-8", "replace")
    fixtures = Path(__file__).resolve().parent.parent / "tests/fixtures/mcp"
    await asyncio.to_thread(fixtures.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread((fixtures / "live-verify.jsonl").write_text, text, encoding="utf-8")

    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    init = next((e for e in events if e.get("subtype") == "init"), {})
    result = next((e for e in events if e.get("type") == "result"), {})
    tool_uses = [
        c.get("name")
        for e in events
        if e.get("type") == "assistant"
        for c in (e.get("message") or {}).get("content", [])
        if c.get("type") == "tool_use"
    ]
    audit_path = AuditLog(workspace / "data" / "audit.jsonl").path
    audited = (
        (await asyncio.to_thread(audit_path.read_text, encoding="utf-8")).splitlines()
        if await asyncio.to_thread(audit_path.exists)
        else []
    )

    print(f"\n{B}result{X}  {D}{len(events)} events{X}")
    checks = [
        (
            "mcp server loaded",
            not init.get("mcp_server_errors"),
            str(init.get("mcp_server_errors")),
        ),
        ("oracle tool offered", any("oracle" in str(x) for x in init.get("tools", [])), ""),
        ("delegate used it", any("oracle" in str(x) for x in tool_uses), str(tool_uses)),
        (
            "answer correct",
            "forty-two" in str(result.get("result", "")),
            str(result.get("result"))[:60],
        ),
        (
            "call is in the audit log",
            any("fs.read" in line for line in audited),
            f"{len(audited)} entries",
        ),
    ]
    for label, ok, detail in checks:
        print(f"  {label:<26} {G + 'ok' + X if ok else R + 'FAIL' + X}  {D}{detail}{X}")
    if err.strip():
        print(f"\n{D}stderr: {err.decode('utf-8', 'replace')[:400]}{X}")
    return 0 if all(ok for _, ok, _ in checks) else 1


async def start_daemon(settings: Settings, port: int, workspace: Path) -> object:
    """A throwaway daemon whose policy scopes only the verification workspace."""
    import uvicorn

    from oracle.api.app import create_app

    policy = workspace / "policy.yaml"
    policy.write_text(
        "version: 1\n"
        "scopes:\n  projects:\n    roots:\n"
        f'      - {{ path: "{workspace.as_posix()}", mode: rw }}\n'
        "tools:\n"
        "  fs.read:  { tier: T0, scopes: [projects] }\n"
        "  fs.list:  { tier: T0, scopes: [projects] }\n",
        encoding="utf-8",
    )
    local = settings.model_copy(
        update={
            "port": port,
            "policy_path": policy,
            "llm_enabled": False,
            "watch_knowledge": False,
            "prewarm_toolhost": False,
            "projects_root": workspace,
            "data_dir": workspace / "data",
        }
    )
    app = create_app(local)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())  # noqa: RUF006 - stopped in the finally above
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.1)
    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show the payload, send nothing")
    args = parser.parse_args()
    try:
        subprocess.run(
            ["claude", "--version"],  # noqa: S607 - resolved from PATH like everywhere else
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        print(f"{R}the claude CLI is not available{X}")
        return 2
    return asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
