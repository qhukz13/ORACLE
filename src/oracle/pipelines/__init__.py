"""Pipelines: a YAML front end onto the Phase 7 task graph (PIPELINES.md).

There is no pipeline executor. A pipeline is parsed here, rendered here, and compiled into
`orchestration.Task` rows that `orchestration.Scheduler` runs through the same policy gate
as everything else — which is what the 2026-08-24 replan means by *"a step is a delegation
(or a tool task) — no second way to run an agent"*.

**This package is pure.** It may not import `oracle.tools`, `oracle.toolhost`,
`oracle.policy` or `oracle.llm`, and a security test asserts it by parsing the imports.
Pricing a step needs the registry and the gate, so it lives in `oracle.runners.pipeline`,
above the boundary. The compiler is structurally incapable of executing anything, and that
is a property worth having rather than a rule worth remembering.
"""

from oracle.pipelines.compile import (
    PIPELINE_ROLE,
    Rendered,
    RenderedStep,
    compile_pipeline,
    render,
)
from oracle.pipelines.loader import (
    GLOBAL_DIR,
    PROJECT_SUBDIR,
    Loaded,
    Problem,
    Source,
    bind_params,
    discover,
    load_file,
)
from oracle.pipelines.models import (
    ArtifactSpec,
    Capture,
    OnFailure,
    ParamSpec,
    Pipeline,
    RetrySpec,
    StepSpec,
)
from oracle.pipelines.template import PipelineError, evaluate, scope_for, substitute

__all__ = [
    "GLOBAL_DIR",
    "PIPELINE_ROLE",
    "PROJECT_SUBDIR",
    "ArtifactSpec",
    "Capture",
    "Loaded",
    "OnFailure",
    "ParamSpec",
    "Pipeline",
    "PipelineError",
    "Problem",
    "Rendered",
    "RenderedStep",
    "RetrySpec",
    "Source",
    "StepSpec",
    "bind_params",
    "compile_pipeline",
    "discover",
    "evaluate",
    "load_file",
    "render",
    "scope_for",
    "substitute",
]
