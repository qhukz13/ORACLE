"""Finding pipeline files and turning them into `Pipeline`s — or into problems with line
numbers (PIPELINES.md §2, §3).

> *"A typo in step 5 must not be discovered after step 4 has already pushed a branch."*

Two properties carry that sentence, and both are here rather than in the caller:

* **Nothing raises.** An unreadable file, malformed YAML, a schema violation — each
  becomes a `Problem`, the same fail-closed shape `load_policy` and `load_registry` use.
  A broken pipeline in a project must not stop the daemon from starting or hide the
  pipelines that *are* valid.
* **Every problem carries a line.** `pydantic` reports `steps.3.tool`, which is a path
  through a parsed document and not a thing a person can find. A `SafeLoader` subclass
  records the line each mapping and sequence started on, and `_locate` walks the error's
  path through that map to turn it back into `asterim-check.yaml:47`.

**Where a file was found decides how far it can reach.** A pipeline under
`<projects_root>/<P>/.oracle/pipelines/` is pinned to project `P`: it is repository
content, so a `project:` key naming somewhere else is a validation error rather than a
redirection. `config/pipelines/` is owner-authored configuration, beside `policy.yaml`,
and may name any project.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from oracle.logsink import get_logger
from oracle.pipelines.models import Pipeline
from oracle.pipelines.template import PipelineError

log = get_logger(__name__)

#: Owner-authored, versioned beside `policy.yaml` — trusted the way configuration is.
GLOBAL_DIR = Path("config/pipelines")

#: Per project, and *untrusted*: this is repository content, the same trust class as a
#: checked-in `AGENTS.md`. The taint is applied where the approval is priced, not here.
PROJECT_SUBDIR = Path(".oracle/pipelines")


class Source:
    GLOBAL = "global"
    PROJECT = "project"


@dataclass(frozen=True)
class Problem:
    """One reason a file is not a pipeline, addressed the way a compiler addresses one."""

    path: Path
    line: int | None
    message: str

    def __str__(self) -> str:
        where = f"{self.path.name}:{self.line}" if self.line else self.path.name
        return f"{where}: {self.message}"


@dataclass(frozen=True)
class Loaded:
    pipeline: Pipeline
    path: Path
    source: str
    #: The project this file may act on: pinned for a project-sourced file, whatever the
    #: header said for a global one.
    project: str | None


class _LineLoader(yaml.SafeLoader):
    """`SafeLoader`, plus the line each collection began on.

    `SafeLoader` and not `Loader`: a pipeline file is untrusted input and
    `yaml.load`/`FullLoader` can construct arbitrary Python objects. This subclass adds
    nothing that changes that — it only records positions.
    """


def _with_lines(loader: _LineLoader, node: yaml.Node) -> Any:
    mapping = yaml.SafeLoader.construct_mapping(loader, node, deep=True)  # type: ignore[arg-type]
    mapping["__line__"] = node.start_mark.line + 1
    return mapping


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _with_lines)


def _strip_lines(value: Any) -> Any:
    """The document as the models should see it: line marks removed, structure intact."""
    if isinstance(value, dict):
        return {k: _strip_lines(v) for k, v in value.items() if k != "__line__"}
    if isinstance(value, list):
        return [_strip_lines(v) for v in value]
    return value


def _locate(raw: Any, location: tuple[Any, ...]) -> int | None:
    """The line for a pydantic error path, or the nearest ancestor's.

    "Nearest ancestor" is deliberate. An error on `steps.3.tool` where `tool` is *missing*
    has no line of its own — the useful answer is the line step 3 starts on, which is
    where a person has to go and look.
    """
    line: int | None = raw.get("__line__") if isinstance(raw, dict) else None
    node = raw
    for key in location:
        if isinstance(node, dict):
            if isinstance(key, str) and key in node:
                node = node[key]
            else:
                break
        elif isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
            node = node[key]
        else:
            break
        if isinstance(node, dict) and "__line__" in node:
            line = node["__line__"]
    return line


def load_file(
    path: Path, *, source: str, pinned_project: str | None = None
) -> tuple[Loaded | None, list[Problem]]:
    """One file. Returns the pipeline, or every reason it is not one."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [Problem(path, None, f"cannot be read: {exc}")]

    try:
        raw = yaml.load(text, Loader=_LineLoader)  # noqa: S506 — _LineLoader IS SafeLoader
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark else None
        return None, [Problem(path, line, f"is not valid YAML: {getattr(exc, 'problem', exc)}")]

    if not isinstance(raw, dict):
        return None, [Problem(path, None, "is not a mapping")]

    try:
        pipeline = Pipeline.model_validate(_strip_lines(raw))
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"]) or "(file)"
            problems.append(Problem(path, _locate(raw, err["loc"]), f"{where}: {err['msg']}"))
        return None, problems

    if pinned_project is not None and pipeline.project not in (None, pinned_project):
        # The one rule that cannot be expressed in the model, because the model does not
        # know where the file came from. A repository must not ship a pipeline that acts
        # on a different repository.
        return None, [
            Problem(
                path,
                raw.get("__line__"),
                f"declares project {pipeline.project!r} but was found inside "
                f"{pinned_project!r}; a project's pipeline may only act on that project",
            )
        ]

    return Loaded(pipeline, path, source, pinned_project or pipeline.project), []


def discover(
    *,
    config_dir: Path = GLOBAL_DIR,
    projects_root: Path | None = None,
    projects: tuple[str, ...] = (),
) -> tuple[dict[str, Loaded], list[Problem]]:
    """Every pipeline on this machine, by name, plus everything that failed to load.

    **Global wins a name collision**, and loudly. The alternative — a repository being
    able to shadow an owner-authored pipeline by choosing its name — is a way to get a
    person to approve a card they have approved before for something they have not.
    """
    found: dict[str, Loaded] = {}
    problems: list[Problem] = []

    def take(loaded: Loaded) -> None:
        name = loaded.pipeline.name
        existing = found.get(name)
        if existing is None:
            found[name] = loaded
            return
        keep, drop = (existing, loaded) if existing.source == Source.GLOBAL else (loaded, existing)
        found[name] = keep
        log.warning(
            "pipelines.name_collision",
            name=name,
            kept=str(keep.path),
            ignored=str(drop.path),
        )
        problems.append(Problem(drop.path, None, f"name {name!r} is already taken by {keep.path}"))

    for path in sorted(config_dir.glob("*.yaml")) if config_dir.is_dir() else []:
        loaded, errs = load_file(path, source=Source.GLOBAL)
        problems.extend(errs)
        if loaded:
            take(loaded)

    for project in projects if projects_root else ():
        directory = projects_root / project / PROJECT_SUBDIR if projects_root else None
        if directory is None or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            loaded, errs = load_file(path, source=Source.PROJECT, pinned_project=project)
            problems.extend(errs)
            if loaded:
                take(loaded)

    return found, problems


def bind_params(pipeline: Pipeline, given: dict[str, Any]) -> dict[str, Any]:
    """Declared defaults, overridden by what the caller passed. Refuses the rest.

    An undeclared parameter is a refusal rather than an ignored key: a caller who thinks
    they turned something off, and did not, is the failure this prevents.
    """
    unknown = set(given) - set(pipeline.params)
    if unknown:
        raise PipelineError(f"unknown parameter(s): {sorted(unknown)}")

    bound: dict[str, Any] = {}
    for name, spec in pipeline.params.items():
        value = given.get(name, spec.default)
        if spec.type == "bool":
            if not isinstance(value, bool):
                raise PipelineError(f"{name!r} is a bool, got {value!r}")
        elif spec.type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise PipelineError(f"{name!r} is an int, got {value!r}")
        elif spec.type == "enum":
            if str(value) not in spec.choices:
                raise PipelineError(f"{name!r} must be one of {list(spec.choices)}, got {value!r}")
            value = str(value)
        else:
            value = str(value)
        bound[name] = value
    return bound
