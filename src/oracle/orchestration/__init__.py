"""The task graph and its scheduler (docs/ORCHESTRATION.md).

ORACLE's supervisor, in the sense ADR-0019 means: the runtime that decides *what runs
next*, while every task still crosses the same policy gate the single-turn pipeline
crosses. The scheduler feeds the gate; it is not a second one.
"""

from oracle.orchestration.graph import GraphError, TaskGraph, find_cycle, validate
from oracle.orchestration.models import (
    Cost,
    Task,
    TaskError,
    TaskKind,
    TaskResult,
    TaskSpec,
    TaskStatus,
    aggregate,
)
from oracle.orchestration.recovery import Recovered, recover
from oracle.orchestration.scheduler import Limits, Parked, Runner, Scheduler
from oracle.orchestration.service import GraphService
from oracle.orchestration.store import TaskStore

__all__ = [
    "Cost",
    "GraphError",
    "GraphService",
    "Limits",
    "Parked",
    "Recovered",
    "Runner",
    "Scheduler",
    "Task",
    "TaskError",
    "TaskGraph",
    "TaskKind",
    "TaskResult",
    "TaskSpec",
    "TaskStatus",
    "TaskStore",
    "aggregate",
    "find_cycle",
    "recover",
    "validate",
]
