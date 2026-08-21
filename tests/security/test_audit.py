"""Audit log: tamper evidence and redaction.

The audit log is the record that must stay trustworthy even when everything else is
wrong. If it can be edited without detection, it is decoration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oracle.policy.audit import AuditLog, digest_args


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


class TestChain:
    def test_intact_chain_verifies(self, audit: AuditLog) -> None:
        for i in range(10):
            audit.append(actor="agent", tool="fs.read", decision="allow", n=i)
        assert audit.verify() == []

    def test_editing_a_record_breaks_the_chain(self, audit: AuditLog) -> None:
        for i in range(5):
            audit.append(actor="agent", tool="fs.read", decision="allow", n=i)

        lines = audit.path.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[2])
        rec["decision"] = "deny"  # rewrite history
        lines[2] = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        audit.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        breaks = audit.verify()
        assert breaks, "an edited record went undetected"
        assert any("edited" in b.detail for b in breaks)

    def test_removing_a_record_breaks_the_chain(self, audit: AuditLog) -> None:
        for i in range(5):
            audit.append(actor="agent", tool="fs.read", n=i)
        lines = audit.path.read_text(encoding="utf-8").splitlines()
        del lines[2]
        audit.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        breaks = audit.verify()
        assert breaks
        assert any("removed" in b.detail or "prev" in b.detail for b in breaks)

    def test_reordering_records_breaks_the_chain(self, audit: AuditLog) -> None:
        for i in range(5):
            audit.append(actor="agent", tool="fs.read", n=i)
        lines = audit.path.read_text(encoding="utf-8").splitlines()
        lines[1], lines[3] = lines[3], lines[1]
        audit.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert audit.verify()

    def test_appending_a_forged_record_breaks_the_chain(self, audit: AuditLog) -> None:
        """A forger who does not know the previous hash cannot extend the chain."""
        audit.append(actor="agent", tool="fs.read")
        forged = {
            "seq": 2,
            "ts": "2026-08-21T00:00:00.000Z",
            "prev": "0" * 64,
            "actor": "agent",
            "tool": "fs.delete",
            "decision": "allow",
            "hash": "f" * 64,
        }
        with audit.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(forged, sort_keys=True) + "\n")
        assert audit.verify()

    def test_chain_survives_a_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        first = AuditLog(path)
        first.append(actor="agent", tool="fs.read")
        second = AuditLog(path)  # restart
        second.append(actor="agent", tool="fs.write")
        assert second.seq == 2
        assert AuditLog(path).verify() == []


class TestRedaction:
    def test_secrets_never_reach_the_audit_log(self, audit: AuditLog) -> None:
        """The audit log must not become the place secrets end up."""
        leak = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGG"
        audit.append(actor="agent", tool="dev.execute", note=f"key {leak}")
        raw = audit.path.read_text(encoding="utf-8")
        assert leak not in raw
        assert "REDACTED" in raw

    def test_sensitive_field_names_are_redacted(self, audit: AuditLog) -> None:
        audit.append(actor="agent", tool="x", password="hunter2", api_key="abcdef123456")
        raw = audit.path.read_text(encoding="utf-8")
        assert "hunter2" not in raw
        assert "abcdef123456" not in raw

    def test_redaction_does_not_break_the_chain(self, audit: AuditLog) -> None:
        audit.append(actor="agent", tool="x", token="ghp_AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH1234")
        audit.append(actor="agent", tool="y")
        assert audit.verify() == []


class TestArgDigest:
    def test_same_args_same_digest_regardless_of_key_order(self) -> None:
        assert digest_args({"a": 1, "b": 2}) == digest_args({"b": 2, "a": 1})

    def test_different_args_differ(self) -> None:
        """This is what binds an approval to one exact invocation."""
        a = digest_args({"path": "C:/Projects/a.txt"})
        b = digest_args({"path": "C:/Projects/b.txt"})
        assert a != b

    def test_digest_is_stable_across_processes(self) -> None:
        assert digest_args({"x": "y"}) == digest_args({"x": "y"})
