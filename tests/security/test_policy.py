"""Policy gate: fail-closed, deny-by-default, taint escalation, HALT.

Merge gate. Each test states the failure it prevents, because a security test whose
purpose is unclear gets "fixed" by weakening it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oracle.policy.engine import LOCKDOWN, PolicyEngine, load_policy
from oracle.policy.model import Capability, Decision, Provenance, Tier
from oracle.policy.paths import PathRejected, Scope

GOOD_POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
  vendor:
    roots:
      - {{ path: "{vendor}", mode: ro }}
  deny_always:
    - "**/.ssh/**"
    - "**/*.env"
tools:
  fs.read:   {{ tier: T0, scopes: [projects, vendor] }}
  fs.write:  {{ tier: T1, scopes: [projects] }}
  git.push:  {{ tier: T2, scopes: [projects] }}
  fs.delete: {{ tier: T3, scopes: [projects] }}
"""


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    vendor = root / "vendor"
    vendor.mkdir(parents=True)
    (root / "a.txt").write_text("a")
    (vendor / "v.txt").write_text("v")
    (root / ".env").write_text("SECRET=1")
    p = tmp_path / "policy.yaml"
    p.write_text(
        GOOD_POLICY.format(root=root.as_posix(), vendor=vendor.as_posix()), encoding="utf-8"
    )
    return p


@pytest.fixture
def engine(policy_file: Path) -> PolicyEngine:
    return PolicyEngine(load_policy(policy_file))


# ------------------------------------------------------------------ fail closed


class TestFailClosed:
    def test_missing_policy_yields_lockdown_not_open_access(self, tmp_path: Path) -> None:
        policy = load_policy(tmp_path / "nope.yaml")
        assert policy.read_only is True
        assert policy.scopes == []

    def test_invalid_yaml_yields_lockdown(self, tmp_path: Path) -> None:
        bad = tmp_path / "policy.yaml"
        bad.write_text("scopes: [this is: not: valid", encoding="utf-8")
        assert load_policy(bad).read_only is True

    def test_policy_with_no_scopes_is_refused(self, tmp_path: Path) -> None:
        """Running with zero scopes would mean 'no restrictions' under a naive reading.
        It must mean lockdown instead."""
        p = tmp_path / "policy.yaml"
        p.write_text("version: 1\ntools:\n  fs.read: {tier: T0}\n", encoding="utf-8")
        assert load_policy(p).read_only is True

    def test_lockdown_denies_every_writing_capability(self) -> None:
        engine = PolicyEngine(LOCKDOWN)
        verdict = engine.evaluate("fs.write", capabilities=frozenset({Capability.FS_WRITE}))
        assert verdict.decision is Decision.DENY
        assert "lockdown" in verdict.rule or verdict.rule == "default-deny"

    def test_loading_never_raises(self, tmp_path: Path) -> None:
        """A crash on bad policy tempts someone to delete the policy file."""
        for content in ["", "[]", "null", "\x00", "tools: 5"]:
            p = tmp_path / "p.yaml"
            p.write_bytes(content.encode("utf-8", "ignore"))
            assert load_policy(p).read_only is True


# --------------------------------------------------------------- deny by default


class TestDenyByDefault:
    def test_unlisted_tool_is_denied(self, engine: PolicyEngine) -> None:
        """An unlisted tool is not an implicitly safe tool."""
        v = engine.evaluate("sys.format_disk")
        assert v.decision is Decision.DENY
        assert v.rule == "default-deny"

    def test_every_denial_names_the_rule(self, engine: PolicyEngine) -> None:
        for tool in ["sys.format_disk", "oracle.set_policy"]:
            v = engine.evaluate(tool)
            assert v.rule, f"{tool} denied with no rule named"
            assert v.reason

    def test_policy_cannot_lower_a_contract_tier(self, engine: PolicyEngine) -> None:
        """Policy may only be as permissive as the tool's own contract. If a contract
        says T3, a policy entry of T0 must not grant an auto-run."""
        v = engine.evaluate("fs.read", declared_tier=Tier.T3)
        assert v.tier is Tier.T3
        assert v.decision is Decision.CONFIRM_STRONG


# --------------------------------------------------------------------- scopes


class TestScopes:
    def test_read_is_allowed_in_scope(self, engine: PolicyEngine, policy_file: Path) -> None:
        root = policy_file.parent / "Projects"
        p = engine.resolve_path(str(root / "a.txt"))
        v = engine.evaluate("fs.read", capabilities=frozenset({Capability.FS_READ}), paths=[p])
        assert v.decision is Decision.ALLOW

    def test_write_into_a_read_only_scope_is_denied(self, tmp_path: Path) -> None:
        """The scope's `mode` must hold even when the tool IS permitted in that scope.

        Written with its own policy on purpose: with the shared fixture, `fs.write`
        isn't listed for `vendor`, so the scope allowlist denies it first and this
        path — a read-only *mode* — never gets exercised. Two correct denials are not
        the same as one tested control.
        """
        ro = tmp_path / "vendor"
        ro.mkdir()
        (ro / "v.txt").write_text("v")
        p = tmp_path / "policy.yaml"
        p.write_text(
            "version: 1\n"
            "scopes:\n"
            f'  vendor:\n    roots:\n      - {{ path: "{ro.as_posix()}", mode: ro }}\n'
            "tools:\n"
            "  fs.write: { tier: T1, scopes: [vendor] }\n"
            "  fs.read:  { tier: T0, scopes: [vendor] }\n",
            encoding="utf-8",
        )
        engine = PolicyEngine(load_policy(p))
        resolved = engine.resolve_path(str(ro / "v.txt"))
        assert resolved.scope.name == "vendor"
        assert resolved.writable is False

        write = engine.evaluate(
            "fs.write", capabilities=frozenset({Capability.FS_WRITE}), paths=[resolved]
        )
        assert write.decision is Decision.DENY
        assert "mode" in write.rule

        # ...and reading the same file is still fine.
        read = engine.evaluate(
            "fs.read", capabilities=frozenset({Capability.FS_READ}), paths=[resolved]
        )
        assert read.decision is Decision.ALLOW

    def test_tool_restricted_to_scopes_it_does_not_have(
        self, engine: PolicyEngine, policy_file: Path
    ) -> None:
        vendor = policy_file.parent / "Projects" / "vendor"
        p = engine.resolve_path(str(vendor / "v.txt"))
        v = engine.evaluate("fs.write", capabilities=frozenset({Capability.FS_WRITE}), paths=[p])
        assert v.decision is Decision.DENY

    def test_deny_always_beats_an_allowed_scope(
        self, engine: PolicyEngine, policy_file: Path
    ) -> None:
        root = policy_file.parent / "Projects"
        with pytest.raises(PathRejected):
            engine.resolve_path(str(root / ".env"))

    def test_path_outside_every_scope_is_rejected(self, engine: PolicyEngine) -> None:
        with pytest.raises(PathRejected):
            engine.resolve_path(r"C:\Windows\System32\drivers\etc\hosts")


# ---------------------------------------------------------------------- tiers


class TestTiers:
    @pytest.mark.parametrize(
        "tool,expected",
        [
            ("fs.read", Decision.ALLOW),
            ("fs.write", Decision.ALLOW),  # reversible + journalled, not prompted
            ("git.push", Decision.CONFIRM),  # visible to others, cannot be unpublished
            ("fs.delete", Decision.CONFIRM_STRONG),
        ],
    )
    def test_tier_maps_to_the_expected_path(
        self, engine: PolicyEngine, tool: str, expected: Decision
    ) -> None:
        assert engine.evaluate(tool).decision is expected

    def test_reversibility_beats_permission(self, engine: PolicyEngine) -> None:
        """ADR-0005: prompting on every commit-shaped action produces prompt fatigue,
        which is itself a security failure. T1 runs automatically with an undo."""
        assert engine.evaluate("fs.write").decision is Decision.ALLOW


# ---------------------------------------------------------------------- taint


class TestTaintEscalation:
    def test_untrusted_content_escalates_a_write_to_a_confirm(self, engine: PolicyEngine) -> None:
        """A plan built from a node_modules README does not get to auto-write."""
        clean = engine.evaluate("fs.write", provenances=frozenset({Provenance.USER}))
        assert clean.decision is Decision.ALLOW
        assert clean.tainted is False

        tainted = engine.evaluate(
            "fs.write", provenances=frozenset({Provenance.USER, Provenance.LOCAL_FOREIGN})
        )
        assert tainted.tainted is True
        assert tainted.escalated is True
        assert tainted.decision is Decision.CONFIRM
        assert "taint" in tainted.rule

    def test_external_content_also_taints(self, engine: PolicyEngine) -> None:
        v = engine.evaluate("git.push", provenances=frozenset({Provenance.EXTERNAL}))
        assert v.decision is Decision.CONFIRM_STRONG  # T2 -> T3

    def test_taint_does_not_escalate_reads(self, engine: PolicyEngine) -> None:
        """Reading more is not the risk; escalating T0 would make taint unbearable and
        get it switched off, which is worse than not having it."""
        v = engine.evaluate("fs.read", provenances=frozenset({Provenance.LOCAL_FOREIGN}))
        assert v.decision is Decision.ALLOW
        assert v.escalated is False

    def test_trusted_provenance_never_taints(self, engine: PolicyEngine) -> None:
        v = engine.evaluate(
            "fs.write", provenances=frozenset({Provenance.SYSTEM, Provenance.LOCAL_OWNED})
        )
        assert v.tainted is False

    def test_escalation_cannot_exceed_t4(self, engine: PolicyEngine) -> None:
        v = engine.evaluate("fs.delete", provenances=frozenset({Provenance.EXTERNAL}))
        assert v.tier is Tier.T4
        assert v.decision is Decision.DENY


# ----------------------------------------------------------------------- HALT


class TestHalt:
    def test_halt_denies_everything_including_reads(self, engine: PolicyEngine) -> None:
        engine.halt("user pressed the hotkey")
        for tool in ["fs.read", "fs.write", "git.push", "oracle.status"]:
            v = engine.evaluate(tool)
            assert v.decision is Decision.DENY, tool
            assert v.rule == "halt"

    def test_halt_does_not_clear_itself(self, engine: PolicyEngine) -> None:
        """Auto-recovery would defeat the purpose: HALT means a human decides when
        it is over."""
        engine.halt("test")
        assert engine.evaluate("fs.read").decision is Decision.DENY
        assert engine.halted is True
        engine.resume()
        assert engine.evaluate("fs.read").decision is Decision.ALLOW

    def test_halt_reason_is_reported(self, engine: PolicyEngine) -> None:
        engine.halt("runaway npm install")
        assert "runaway npm install" in engine.evaluate("fs.read").reason


# -------------------------------------------------------------- scope overrides


class TestScopeTierOverride:
    def test_scope_override_can_raise_but_reads_stay_cheap(self, tmp_path: Path) -> None:
        proj = tmp_path / "Projects"
        scratch = tmp_path / "scratch"
        proj.mkdir()
        scratch.mkdir()
        (proj / "f.txt").write_text("x")
        (scratch / "f.txt").write_text("x")
        p = tmp_path / "policy.yaml"
        p.write_text(
            "version: 1\n"
            "scopes:\n"
            f'  projects:\n    roots:\n      - {{ path: "{proj.as_posix()}", mode: rw }}\n'
            f'  scratch:\n    roots:\n      - {{ path: "{scratch.as_posix()}", mode: rw }}\n'
            "tools:\n"
            "  fs.write:\n"
            "    tier: T1\n"
            "    scopes: [projects, scratch]\n"
            "    scope_tiers: { projects: T2 }\n",
            encoding="utf-8",
        )
        engine = PolicyEngine(load_policy(p))
        caps = frozenset({Capability.FS_WRITE})

        in_scratch = engine.evaluate(
            "fs.write", capabilities=caps, paths=[engine.resolve_path(str(scratch / "f.txt"))]
        )
        in_project = engine.evaluate(
            "fs.write", capabilities=caps, paths=[engine.resolve_path(str(proj / "f.txt"))]
        )
        assert in_scratch.decision is Decision.ALLOW
        assert in_project.decision is Decision.CONFIRM
        assert "scope_tiers" in in_project.rule


def test_scope_dataclass_key_is_normalised() -> None:
    a = Scope("x", Path(r"C:\Projects\\"), writable=True)
    assert not a.key.endswith("\\")
    assert a.key == a.key.lower()
