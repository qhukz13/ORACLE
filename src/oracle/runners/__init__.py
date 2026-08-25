"""Runners: the adapters between the supervisor and the machinery that does the work.

The scheduler takes runners by injection and imports nothing that executes
(`tests/security/test_orchestration_boundary.py` enforces that against the source). This
package is the other side of that seam — it is *allowed* to import both, because
composing is what it is for, and it is the only place where "a task" and "a tool call"
are both in scope.

Everything here is an adapter over something that already worked before the graph
existed: `ToolExecutor` ran gated tool calls, `DelegationService` ran a complete
delegation lifecycle, `dev.run_tests` ran a suite. None of them is rewritten here.
"""

from oracle.runners.delegation import make_delegation_runner
from oracle.runners.tool import make_tool_runner
from oracle.runners.verify import BaselineCache, make_verify_runner

__all__ = [
    "BaselineCache",
    "make_delegation_runner",
    "make_tool_runner",
    "make_verify_runner",
]
