"""The `know.*` tools, against the gate.

Merge gate. Required by the Phase 5 definition of done: *every `know.*` tool has a policy
rule and a `tests/security/` case*.

The specific risk these tools introduce is not that they write — three of the four do
not. It is that they are the first tools whose **return value is attacker-influenced
prose**: a chunk of a file someone else wrote, pulled into a model's context because it
scored well. So the cases below are about the seam between "this tool may run" and "what
it returned may be believed", which are different questions with different answers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oracle.policy.engine import LOCKDOWN, PolicyEngine, load_policy
from oracle.policy.model import Capability, Decision, Provenance, Tier
from oracle.tools import build_registry
from oracle.tools.knowledge import KNOW_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOW_IDS = {c.id for c in KNOW_TOOLS}


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    return PolicyEngine(load_policy(REPO_ROOT / "config/policy.yaml"))


def test_the_tools_query_with_the_model_that_built_the_index() -> None:
    """`know.*` pinned `E5_BASE` while the indexer moved to `bge-m3` (2026-08-24), and
    from that day every live `know.*` call through the toolhost failed `bind()` with a
    SchemaMismatch — invisible to fixture tests, whose empty tmp indexes self-
    consistently bind whatever the tool asks for. Found 2026-08-28 by a latency
    measurement. The tool layer must not keep a private copy of the model name: this
    pins its alias to `embedding.DEFAULT`, the indexer's single switch."""
    from oracle.rag import embedding
    from oracle.tools import knowledge

    assert knowledge._MODEL is embedding.DEFAULT


class TestEveryToolIsGoverned:
    def test_the_shipped_policy_has_a_rule_for_each(self, engine: PolicyEngine) -> None:
        """A tool with no policy entry is denied by default — which is safe, but it means
        the tool silently does not work. Both halves are worth asserting."""
        for tool_id in sorted(KNOW_IDS):
            verdict = engine.evaluate(tool_id)
            assert verdict.rule != "default-deny", f"{tool_id} has no rule in policy.yaml"

    def test_each_is_registered_and_offerable(self) -> None:
        registered = {c.id for c in build_registry().all()}
        assert KNOW_IDS <= registered

    def test_the_tool_count_stays_under_the_cap(self) -> None:
        """40 is the cap, and it is about selection accuracy in a 0.8b model rather than
        about memory (TOOLS.md §1). Adding `know.*` is what makes it worth asserting."""
        assert len(build_registry().all()) <= 40

    def test_no_know_tool_declares_a_writing_capability(self) -> None:
        """`know.reindex` writes `knowledge.db`, but that is a rebuildable cache reached
        through no path argument — not a filesystem write anyone can aim."""
        from oracle.policy.model import WRITING_CAPABILITIES

        for contract in KNOW_TOOLS:
            assert not (contract.capabilities & WRITING_CAPABILITIES), contract.id


class TestTiers:
    @pytest.mark.parametrize(
        ("tool_id", "expected"),
        [
            ("know.search", Tier.T0),
            ("know.search_code", Tier.T0),
            ("know.read_context", Tier.T0),
            ("know.reindex", Tier.T1),
        ],
    )
    def test_tier_matches_the_documented_one(
        self, engine: PolicyEngine, tool_id: str, expected: Tier
    ) -> None:
        assert engine.evaluate(tool_id).tier is expected

    def test_reads_run_without_a_prompt(self, engine: PolicyEngine) -> None:
        """Approval fatigue is a real failure mode (OQ-13). A search that asks permission
        every time is a search nobody uses."""
        for tool_id in ("know.search", "know.search_code", "know.read_context"):
            assert engine.evaluate(tool_id).decision is Decision.ALLOW

    def test_a_contract_tier_cannot_be_lowered_by_policy(self, engine: PolicyEngine) -> None:
        verdict = engine.evaluate("know.search", declared_tier=Tier.T3)
        assert verdict.tier is Tier.T3
        assert verdict.decision is Decision.CONFIRM_STRONG


class TestTaintFlowsOnward:
    """The tools are cheap to call; what they return is not automatically trusted."""

    def test_a_write_planned_from_retrieved_foreign_content_is_confirmed(
        self, engine: PolicyEngine
    ) -> None:
        """The end-to-end property Phase 5 exists to exercise.

        `know.search` returning a `local_foreign` chunk marks the turn tainted, and the
        next write in that turn stops being automatic. This is the path that was built in
        Phase 3 and had nothing to fire it until now.
        """
        clean = engine.evaluate("fs.write", provenances=frozenset({Provenance.USER}))
        assert clean.decision is Decision.ALLOW

        tainted = engine.evaluate(
            "fs.write", provenances=frozenset({Provenance.USER, Provenance.LOCAL_FOREIGN})
        )
        assert tainted.escalated is True
        assert tainted.decision is Decision.CONFIRM

    def test_retrieving_tainted_content_does_not_escalate_further_reading(
        self, engine: PolicyEngine
    ) -> None:
        """Reading more is not the risk. Escalating T0 on taint would make every question
        about a third-party repository a confirmation, and taint would get switched off —
        which is worse than not having it."""
        verdict = engine.evaluate("know.search", provenances=frozenset({Provenance.LOCAL_FOREIGN}))
        assert verdict.decision is Decision.ALLOW
        assert verdict.escalated is False


class TestLockdown:
    def test_reindex_is_refused_in_lockdown(self) -> None:
        """Read-only lockdown must refuse the one `know.*` tool that writes."""
        assert "know.reindex" not in {c.id for c in build_registry(writes=False).all()}

    def test_searching_still_works_in_a_read_only_build(self) -> None:
        """And must refuse *only* that one. A read-only ORACLE that cannot answer a
        question about the projects has lost the thing it is for."""
        read_only = {c.id for c in build_registry(writes=False).all()}
        assert {"know.search", "know.search_code", "know.read_context"} <= read_only

    def test_lockdown_policy_denies_writing_capabilities(self) -> None:
        engine = PolicyEngine(LOCKDOWN)
        assert engine.evaluate("know.reindex").decision is Decision.DENY


class TestNoPathArguments:
    def test_none_of_them_takes_a_path(self) -> None:
        """This is why they carry no `scopes:` in policy.yaml.

        A `know.*` tool can only reach what a human opted into in collections.yaml. If one
        ever grows a path argument it must also grow a scope, and this test is what will
        say so — a path field with no scope is a sandbox escape.
        """
        for contract in KNOW_TOOLS:
            assert not contract.path_fields, f"{contract.id} takes a path but declares no scope"

    def test_they_declare_only_fs_read(self) -> None:
        for contract in KNOW_TOOLS:
            assert contract.capabilities <= {Capability.FS_READ}, contract.id
