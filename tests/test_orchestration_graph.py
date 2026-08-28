"""The graph algebra: validation, the ready set, the skip cascade, aggregation.

Pure functions, so these tests are pure too — no clock, no database, no runners. What
they pin is the vocabulary as much as the arithmetic: several assert that two statuses
stay *different*, because the pressure to fold `TIMEOUT` into `FAILED` or `SKIPPED` into
`CANCELLED` arrives later, during implementation, and the enum alone will not resist it.
"""

from __future__ import annotations

import pytest

from oracle.orchestration.graph import MAX_GRAPH_SIZE, GraphError, TaskGraph, find_cycle, validate
from oracle.orchestration.models import (
    Task,
    TaskKind,
    TaskSpec,
    TaskStatus,
    aggregate,
)

ROOT = "tk_root"


def task(task_id: str, *deps: str, kind: TaskKind = TaskKind.TOOL, root: str = ROOT) -> Task:
    return Task(
        id=task_id,
        root_id=root,
        kind=kind,
        spec=TaskSpec(objective=f"do {task_id}", role="coder"),
        depends_on=tuple(deps),
    )


def chain(*ids: str) -> list[Task]:
    return [task(t, *([ids[i - 1]] if i else [])) for i, t in enumerate(ids)]


# -- validation ----------------------------------------------------------------


def test_a_linear_chain_validates() -> None:
    TaskGraph(chain("a", "b", "c"))


def test_an_empty_graph_is_rejected() -> None:
    with pytest.raises(GraphError, match="no tasks"):
        validate([])


def test_duplicate_ids_are_rejected_before_anything_else() -> None:
    """Named first because every later check reads tasks by id: with a duplicate, the
    dependency and cycle checks are inspecting whichever copy won."""
    with pytest.raises(GraphError, match="duplicate task ids: a"):
        validate([task("a"), task("a"), task("b")])


def test_a_dangling_dependency_names_the_missing_task() -> None:
    with pytest.raises(GraphError, match="depends on unknown task 'ghost'"):
        validate([task("a", "ghost")])


def test_a_graph_larger_than_the_cap_is_rejected() -> None:
    """ORCHESTRATION.md §3: a plan bigger than this is a planner losing the thread."""
    too_many = [task(f"t{i}") for i in range(MAX_GRAPH_SIZE + 1)]
    with pytest.raises(GraphError, match=f"exceeds the graph limit of {MAX_GRAPH_SIZE}"):
        validate(too_many)


def test_tasks_from_two_roots_are_not_one_graph() -> None:
    with pytest.raises(GraphError, match="span 2 roots"):
        validate([task("a"), task("b", root="tk_other")])


# -- the cycle, as a path ------------------------------------------------------


def test_a_cycle_is_reported_as_a_path_not_a_boolean() -> None:
    """'There is a cycle' is not an error message anyone can act on. The path is."""
    tasks = [task("a", "c"), task("b", "a"), task("c", "b")]
    cycle = find_cycle(tasks)
    assert cycle[0] == cycle[-1], "the path does not close"
    assert set(cycle) == {"a", "b", "c"}
    with pytest.raises(GraphError) as caught:
        validate(tasks)
    assert "→" in str(caught.value)
    assert caught.value.cycle == cycle


def test_self_dependency_is_a_cycle_of_one() -> None:
    with pytest.raises(GraphError, match="depends on itself") as caught:
        validate([task("a", "a")])
    assert caught.value.cycle == ("a", "a")


def test_a_diamond_is_not_a_cycle() -> None:
    """The shape most likely to be misdetected by a naive visited-set walk: `d` is
    reached twice by different paths, and that is legal."""
    assert find_cycle([task("a"), task("b", "a"), task("c", "a"), task("d", "b", "c")]) == ()


def test_the_cycle_walk_survives_a_deep_chain() -> None:
    """Iterative on purpose: the graph may come from a planner, and a planner is an
    untrusted source (ADR-0021). 5000 nodes is far past anything the size cap allows —
    the point is that the walk cannot be made to blow the stack."""
    deep = [task("t0")] + [task(f"t{i}", f"t{i - 1}") for i in range(1, 5000)]
    assert find_cycle(deep) == ()
    closed = [*deep[:-1], task("t4999", "t4998")]
    closed[0] = task("t0", "t4999")
    assert find_cycle(closed) != ()


# -- the ready set and the cascade ---------------------------------------------


def test_ready_requires_dependencies_to_have_succeeded_not_merely_finished() -> None:
    """Fail-closed for free: `FAILED` is terminal but not success, so nothing downstream
    is ever eligible. This is the single line that makes the skip cascade a consequence
    rather than a feature."""
    graph = TaskGraph(chain("a", "b"))
    assert [t.id for t in graph.ready()] == ["a"]

    graph.replace(graph["a"].with_status(TaskStatus.FAILED))
    assert graph.ready() == []
    assert [t.id for t in graph.blocked()] == ["b"]

    graph.replace(graph["b"].with_status(TaskStatus.PENDING))
    graph.replace(graph["a"].with_status(TaskStatus.SUCCEEDED))
    assert [t.id for t in graph.ready()] == ["b"]


def test_a_graph_with_no_edges_makes_everything_ready_at_once() -> None:
    """The common case, not the degenerate one: five of twelve valid plans in the P6-T5
    spike declared no dependencies at all (OQ-20)."""
    graph = TaskGraph([task("a"), task("b"), task("c")])
    assert [t.id for t in graph.ready()] == ["a", "b", "c"]
    assert graph.blocked() == []


def test_a_timed_out_dependency_also_blocks() -> None:
    """TIMEOUT is not FAILED, but it is not success either — and only success unblocks."""
    graph = TaskGraph(chain("a", "b"))
    graph.replace(graph["a"].with_status(TaskStatus.TIMEOUT))
    assert graph.ready() == []
    assert [t.id for t in graph.blocked()] == ["b"]


def test_dependents_are_found_by_edge_not_by_order() -> None:
    graph = TaskGraph([task("a"), task("b", "a"), task("c", "a"), task("d", "b")])
    assert sorted(t.id for t in graph.dependents("a")) == ["b", "c"]
    assert [t.id for t in graph.dependents("d")] == []


# -- aggregation ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([TaskStatus.SUCCEEDED, TaskStatus.SUCCEEDED], TaskStatus.SUCCEEDED),
        ([TaskStatus.SUCCEEDED, TaskStatus.RUNNING], TaskStatus.RUNNING),
        ([TaskStatus.SUCCEEDED, TaskStatus.TIMEOUT], TaskStatus.TIMEOUT),
        ([TaskStatus.TIMEOUT, TaskStatus.FAILED], TaskStatus.FAILED),
        ([TaskStatus.FAILED, TaskStatus.CANCELLED], TaskStatus.CANCELLED),
        ([TaskStatus.SUCCEEDED, TaskStatus.SKIPPED], TaskStatus.SKIPPED),
        ([], TaskStatus.SUCCEEDED),
    ],
)
def test_aggregate_follows_the_precedence_order(
    statuses: list[TaskStatus], expected: TaskStatus
) -> None:
    """CANCELLED > FAILED > TIMEOUT > RUNNING/WAITING > SUCCEEDED — read as 'the worst
    thing that happened to this graph'."""
    assert aggregate(statuses) is expected


def test_a_failure_outranks_a_timeout_and_a_cancellation_outranks_both() -> None:
    """The distinctions restated as one assertion, because losing them is a silent
    regression: nothing crashes when TIMEOUT starts reporting as FAILED."""
    assert aggregate([TaskStatus.TIMEOUT, TaskStatus.FAILED]) is TaskStatus.FAILED
    assert aggregate([TaskStatus.TIMEOUT, TaskStatus.CANCELLED]) is TaskStatus.CANCELLED
    assert aggregate([TaskStatus.SKIPPED, TaskStatus.FAILED]) is TaskStatus.FAILED


# -- transitions ---------------------------------------------------------------


def test_a_transition_is_a_copy_and_stamps_its_clocks() -> None:
    """The row is the record: a mutated object in someone's local variable is not."""
    original = task("a")
    running = original.with_status(TaskStatus.RUNNING)
    assert original.status is TaskStatus.PENDING, "the original was mutated"
    assert running.started_at is not None and running.finished_at is None

    done = running.with_status(TaskStatus.SUCCEEDED)
    assert done.finished_at is not None
    assert done.started_at == running.started_at, "a terminal transition reset the start"
    assert done.terminal and not running.terminal


def test_replacing_a_task_the_graph_does_not_hold_is_an_error() -> None:
    graph = TaskGraph([task("a")])
    with pytest.raises(GraphError, match="not in this graph"):
        graph.replace(task("z"))
