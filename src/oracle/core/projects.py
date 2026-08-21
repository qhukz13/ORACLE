"""Project registry and detection.

Two jobs, and the second is what makes the `dev.*` tools possible:

  1. **Which names exist.** The classifier may only resolve a project that is listed
     here (docs/AGENT_RUNTIME.md step 3): a hallucinated name resolves to nothing and
     triggers a clarification, rather than becoming a filesystem path.
  2. **How to test, build and lint it.** "run the Asterim tests" has to become an argv,
     and the answer differs per project. Detection is by *marker file*, because that is
     the thing that is actually true — a directory called `tests` proves nothing, a
     `Cargo.toml` proves a great deal.

Detection is deliberately shallow and boring. It looks at the root and at one level
under the conventional monorepo folders, and it never executes anything to find out.
A project that cannot be classified reports `unknown` rather than a guess: running the
wrong build command is worse than admitting we do not know.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from oracle.logsink import get_logger

log = get_logger(__name__)

_SKIP = {".git", "node_modules", "target", "dist", "build", "__pycache__", ".venv"}

#: Where a sub-package may live in a monorepo. One level only — deep scanning turns a
#: cheap classification into a filesystem crawl of Source2DemViewer's 3,915-file target.
_NEST = ("apps", "packages", "crates", "services")

#: Instructions written FOR an agent. Read, never executed, and always attributed to
#: the project rather than to ORACLE — this is `local_foreign` content and is tainted
#: accordingly (docs/SECURITY.md#6).
AGENT_DOC_NAMES = ("AGENTS.md", "CLAUDE.md", ".cursorrules", "CONVENTIONS.md")


class ProjectKind(StrEnum):
    PYTHON = "python"
    NODE = "node"
    RUST = "rust"
    ROBLOX = "roblox"
    DOCS = "docs"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Task:
    """One runnable command, already decomposed into program + argv.

    A *string* here would have to be split later, and splitting a command string is how
    quoting bugs become execution bugs. The program name is an allowlist key, not a
    path: resolution happens in the policy layer (docs/SECURITY.md#4b).
    """

    kind: ProjectKind
    program: str
    args: tuple[str, ...]
    #: Relative to the project root. Empty means the root itself.
    subdir: str = ""

    def display(self) -> str:
        where = f" (in {self.subdir})" if self.subdir else ""
        return f"{self.program} {' '.join(self.args)}{where}"


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    root: Path
    kinds: tuple[ProjectKind, ...]
    test: tuple[Task, ...] = ()
    build: tuple[Task, ...] = ()
    lint: tuple[Task, ...] = ()
    #: Relative paths of AGENTS.md / CLAUDE.md and friends, if present.
    agent_docs: tuple[str, ...] = ()
    markers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def primary(self) -> ProjectKind:
        return self.kinds[0] if self.kinds else ProjectKind.UNKNOWN

    def summary(self) -> str:
        return f"{self.name}: {', '.join(self.kinds) or 'unknown'}" + (
            f" · test: {self.test[0].display()}" if self.test else " · no test command"
        )


def discover_projects(root: Path) -> list[str]:
    """Top-level directory names under `root`. Deliberately shallow and boring."""
    if not root.exists():
        return []
    return [
        p.name
        for p in sorted(root.iterdir())
        if p.is_dir() and not p.name.startswith(".") and p.name not in _SKIP
    ]


def _read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("projects.unreadable_json", path=str(path), error=str(exc))
        return {}
    return data if isinstance(data, dict) else {}


def _python_tasks(root: Path, sub: str) -> tuple[list[Task], list[Task], list[Task]]:
    """`uv run` first, the bare interpreter second. Both are returned, in that order.

    MEASURED, and the reason the fallback is second rather than equal: the allowlist
    pins `python` by resolving it once at startup, and ORACLE runs inside its own
    virtualenv — so the pinned interpreter is *ORACLE's*, not the target project's.
    Running `python -m pytest` in someone else's project would test their code against
    our dependencies, and would usually fail with a confusing ImportError.

    `uv run` has no such problem: it resolves the environment from the project
    directory it is invoked in. It is preferred for every Python project, not only
    uv-managed ones. The caller picks the first task whose program is actually
    available (see `dev._pick`).
    """
    return (
        [
            Task(ProjectKind.PYTHON, "uv", ("run", "pytest", "-q"), sub),
            Task(ProjectKind.PYTHON, "python", ("-m", "pytest", "-q"), sub),
        ],
        [],
        [
            Task(ProjectKind.PYTHON, "uv", ("run", "ruff", "check", "."), sub),
            Task(ProjectKind.PYTHON, "python", ("-m", "ruff", "check", "."), sub),
        ],
    )


def _node_tasks(pkg: Path, sub: str) -> tuple[list[Task], list[Task], list[Task]]:
    """Only scripts the project actually declares.

    Inventing `npm run build` for a project with no build script produces a confusing
    failure and teaches the user that the tool is unreliable. If it is not in
    `package.json`, it is not offered.
    """
    scripts = _read_json(pkg).get("scripts")
    names = set(scripts) if isinstance(scripts, dict) else set()
    test = [Task(ProjectKind.NODE, "npm", ("test", "--silent"), sub)] if "test" in names else []
    build = [Task(ProjectKind.NODE, "npm", ("run", "build"), sub)] if "build" in names else []
    lint: list[Task] = []
    for candidate in ("lint", "typecheck"):
        if candidate in names:
            lint.append(Task(ProjectKind.NODE, "npm", ("run", candidate), sub))
    return test, build, lint


def _rust_tasks(sub: str) -> tuple[list[Task], list[Task], list[Task]]:
    return (
        [Task(ProjectKind.RUST, "cargo", ("test",), sub)],
        [Task(ProjectKind.RUST, "cargo", ("build",), sub)],
        [Task(ProjectKind.RUST, "cargo", ("clippy",), sub)],
    )


#: Extensions that cannot be a build system. `.json` is on the list because a marker
#: file (`package.json`, `default.project.json`) has already been checked for by the
#: time this runs — what is left is configuration for a design tool, not a project.
_INERT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".ico",
        ".pdf",
    }
)


def _looks_like_docs(root: Path) -> bool:
    """Markdown and nothing that runs. AsterimDesign is the real example: a project by
    every human measure, with 96 markdown files and no build system to detect."""
    seen = False
    for p in root.rglob("*"):
        if any(part in _SKIP for part in p.parts) or p.is_dir():
            continue
        if p.suffix.lower() not in _INERT_SUFFIXES:
            return False
        seen = True
    return seen


def detect_project(root: Path, name: str | None = None) -> ProjectInfo:
    """Classify one project directory. Never executes anything it finds."""
    name = name or root.name
    kinds: list[ProjectKind] = []
    markers: list[str] = []
    test: list[Task] = []
    build: list[Task] = []
    lint: list[Task] = []

    if not root.is_dir():
        return ProjectInfo(name=name, root=root, kinds=(ProjectKind.UNKNOWN,))

    # Roots first, then one level under the monorepo folders. Order matters: the root's
    # own build system is the project's identity, a nested one is a component of it.
    candidates: list[tuple[Path, str]] = [(root, "")]
    for nest in _NEST:
        nest_dir = root / nest
        if not nest_dir.is_dir():
            continue
        for child in sorted(nest_dir.iterdir()):
            if child.is_dir() and child.name not in _SKIP:
                candidates.append((child, f"{nest}/{child.name}"))

    for base, sub in candidates:
        if (base / "pyproject.toml").exists() or (base / "setup.py").exists():
            if ProjectKind.PYTHON not in kinds:
                kinds.append(ProjectKind.PYTHON)
            markers.append(f"{sub}/pyproject.toml".lstrip("/"))
            t, b, ln = _python_tasks(base, sub)
            test += t
            build += b
            lint += ln
        pkg = base / "package.json"
        if pkg.exists():
            if ProjectKind.NODE not in kinds:
                kinds.append(ProjectKind.NODE)
            markers.append(f"{sub}/package.json".lstrip("/"))
            t, b, ln = _node_tasks(pkg, sub)
            test += t
            build += b
            lint += ln
        if (base / "Cargo.toml").exists():
            # A crate inside a workspace is built from the workspace root; registering
            # every member would run the same suite N times.
            if ProjectKind.RUST not in kinds:
                kinds.append(ProjectKind.RUST)
                markers.append(f"{sub}/Cargo.toml".lstrip("/"))
                t, b, ln = _rust_tasks(sub)
                test += t
                build += b
                lint += ln
        if (base / "default.project.json").exists() or (base / "rojo.json").exists():
            if ProjectKind.ROBLOX not in kinds:
                kinds.append(ProjectKind.ROBLOX)
                markers.append(f"{sub}/default.project.json".lstrip("/"))

    if not kinds and _looks_like_docs(root):
        kinds.append(ProjectKind.DOCS)
    if not kinds:
        kinds.append(ProjectKind.UNKNOWN)

    docs = tuple(n for n in AGENT_DOC_NAMES if (root / n).exists())

    return ProjectInfo(
        name=name,
        root=root,
        kinds=tuple(kinds),
        test=tuple(test),
        build=tuple(build),
        lint=tuple(lint),
        agent_docs=docs,
        markers=tuple(markers),
    )


def detect_all(root: Path) -> dict[str, ProjectInfo]:
    """Classify everything under the projects root, by name."""
    return {name: detect_project(root / name, name) for name in discover_projects(root)}


def read_agent_docs(info: ProjectInfo, max_chars: int = 4000) -> dict[str, str]:
    """The instructions a project has written for coding agents.

    Returned as data to be *shown*, never followed blindly: this is `local_foreign`
    content authored by whoever wrote the repository, and treating it as a system
    prompt would be the cleanest prompt-injection channel in the product
    (docs/SECURITY.md#6).
    """
    out: dict[str, str] = {}
    for rel in info.agent_docs:
        try:
            out[rel] = info.root.joinpath(rel).read_text(encoding="utf-8")[:max_chars]
        except OSError as exc:
            log.warning("projects.unreadable_doc", path=rel, error=str(exc))
    return out


__all__ = [
    "AGENT_DOC_NAMES",
    "ProjectInfo",
    "ProjectKind",
    "Task",
    "detect_all",
    "detect_project",
    "discover_projects",
    "read_agent_docs",
]
