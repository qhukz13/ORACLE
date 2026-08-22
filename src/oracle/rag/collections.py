"""The collection registry: the only answer to "may ORACLE read this file?".

Policy for *indexing*, held as data in `config/collections.yaml` and edited by a human,
in the same spirit as `config/policy.yaml` — neither the model nor a retrieved document
can reach these types (docs/SECURITY.md#2-design-principles).

Three rules, in the order they are applied, because the order is the security property:

1. **Deny by path, before the file is opened.** A `deny` pattern is matched against the
   path alone. A password file is not read in order to discover that it is a password
   file. No per-collection `include` can override a deny.
2. **Prune while descending, not after.** `Path.rglob` enumerates `node_modules` in full
   before any filter can reject it; on Asterim that walk cost more than embedding the
   whole corpus. Directory names are pruned as the walk descends — the same rule
   docs/RAG.md#6 states for the watcher: drop before hashing, not after.
3. **Only then, look at the file.** Type, size, readability.

The registry never discovers projects for itself. `include_projects` is an explicit
list, checked against `core.projects.discover_projects` so a typo is reported rather
than silently indexing nothing.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from oracle.core.projects import _SKIP as PROJECT_SKIP
from oracle.logsink import get_logger

log = get_logger(__name__)


class ContentKind(StrEnum):
    """What a file is, which decides how it is chunked and whether it is embedded.

    The distinction that matters is `CONFIG`: it is indexed lexically and **not**
    semantically. An embedding of a `tsconfig.json` is a vector that is close to
    everything and means nothing, and it crowds out the prose that would have answered
    the question (docs/RAG.md#2).
    """

    CODE = "code"
    MARKDOWN = "markdown"
    TEXT = "text"
    CONFIG = "config"
    PDF = "pdf"

    @property
    def semantic(self) -> bool:
        """Whether chunks of this kind get an embedding as well as a BM25 posting."""
        return self is not ContentKind.CONFIG


_CODE_SUFFIXES = frozenset(
    {
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs", ".go", ".java",
        ".c", ".h", ".cpp", ".hpp", ".cs", ".sh", ".ps1", ".sql", ".css", ".scss",
        ".lua", ".rb", ".php", ".kt", ".swift",
    }
)  # fmt: skip
_MARKDOWN_SUFFIXES = frozenset({".md", ".mdx"})
_TEXT_SUFFIXES = frozenset({".txt", ".rst"})
_CONFIG_SUFFIXES = frozenset({".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml"})
_PDF_SUFFIXES = frozenset({".pdf"})

#: Extensionless files worth reading. `Dockerfile.relay` is a real answer to a real
#: question in the retrieval fixture set, and it has no suffix to classify it by.
_NAMED = frozenset({"Dockerfile", "Makefile", "Justfile", "Procfile"})


def classify(path: Path) -> ContentKind | None:
    """What kind of content this is, or None for "do not index"."""
    suffix = path.suffix.lower()
    if suffix in _MARKDOWN_SUFFIXES:
        return ContentKind.MARKDOWN
    if suffix in _CODE_SUFFIXES:
        return ContentKind.CODE
    if suffix in _TEXT_SUFFIXES:
        return ContentKind.TEXT
    if suffix in _CONFIG_SUFFIXES:
        return ContentKind.CONFIG
    if suffix in _PDF_SUFFIXES:
        return ContentKind.PDF
    if path.name in _NAMED or path.name.startswith("Dockerfile"):
        return ContentKind.CONFIG
    return None


class Collection(BaseModel):
    """One opted-in source of documents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: Literal["code", "markdown"]
    roots: tuple[Path, ...]
    enabled: bool = True
    #: Projects to index under a `C:/Projects`-style root. Explicit, never discovered:
    #: adding a project to the index is a decision, and a new directory appearing on
    #: disk is not one.
    include_projects: tuple[str, ...] = ()
    respect_gitignore: bool = False
    obsidian: bool = False
    exclude: tuple[str, ...] = ()
    max_file_bytes: int = 1_000_000


class CollectionRegistry(BaseModel):
    """`config/collections.yaml`, parsed. The whole of what ORACLE may index."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    #: Checked first, for every collection, against the path. See the module docstring.
    deny: tuple[str, ...] = ()
    collections: tuple[Collection, ...] = Field(default_factory=tuple)

    def collection(self, id_: str) -> Collection | None:
        return next((c for c in self.collections if c.id == id_), None)


@dataclass(frozen=True)
class Document:
    """A file that survived every rule, with the identity retrieval will cite."""

    collection: str
    #: Project name for a code collection, vault directory name for notes.
    project: str
    #: Corpus-relative, forward slashes. This is what a citation shows and what the
    #: retrieval fixture set matches on — it must be stable across machines.
    path: str
    abs_path: Path
    kind: ContentKind
    size: int
    mtime_ns: int

    @property
    def semantic(self) -> bool:
        return self.kind.semantic


@dataclass
class WalkStats:
    """Why files were not indexed. Surfaced by the index health view (RAG.md §9).

    A count of rejections is not diagnostics padding: "0 documents indexed" and
    "4,000 documents rejected as untracked" are the same symptom with entirely
    different causes, and without this the difference is invisible.
    """

    denied: int = 0
    excluded: int = 0
    untracked: int = 0
    wrong_type: int = 0
    too_large: int = 0
    unreadable: int = 0
    #: Directories skipped without descending. Split by *why*, because folding them
    #: together hides the one that matters: a `Passwords/` folder is pruned by the same
    #: mechanism as `node_modules`, and "the deny list fired" must stay visible in the
    #: health view rather than disappearing into a build-output count.
    denied_dirs: int = 0
    pruned_dirs: int = 0
    missing_roots: list[str] = field(default_factory=list)
    unknown_projects: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "denied": self.denied,
            "denied_dirs": self.denied_dirs,
            "excluded": self.excluded,
            "untracked": self.untracked,
            "wrong_type": self.wrong_type,
            "too_large": self.too_large,
            "unreadable": self.unreadable,
            "pruned_dirs": self.pruned_dirs,
        }


def load_registry(path: Path) -> CollectionRegistry:
    """Parse `config/collections.yaml`. Raises rather than defaulting to "index nothing".

    A malformed collections file is a configuration error a human must see. Falling back
    to an empty registry would make ORACLE quietly stop knowing anything, which reads to
    the user as the index being broken rather than the config being wrong.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return CollectionRegistry.model_validate(raw)


@cache
def _compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """`patterns` as regexes, compiled once per distinct tuple.

    `fnmatch.fnmatch` normcases both arguments on every call, and on Windows that is a
    `LCMapStringEx` call into the OS. At eight deny patterns matched against two forms of
    each path, filtering 5000 `npm install` events spent 1.3 s in the locale mapper alone —
    on the event loop, because the watcher filters there. Compiling keeps the semantics
    (`IGNORECASE` is what normcase was providing on this platform) and removes the syscall.

    Backslashes in a pattern are folded to `/` because paths arrive here as `as_posix()`
    and `normcase` used to reconcile the two. Dropping that would silently stop a deny rule
    written `**\\Passwords\\**` from matching anything, which is the one failure mode this
    family of functions must not have.
    """
    return tuple(
        re.compile(fnmatch.translate(p.replace("\\", "/")), re.IGNORECASE) for p in patterns
    )


def _matches(rel: str, absolute: str, patterns: tuple[str, ...]) -> bool:
    """Glob match against both the corpus-relative and absolute forms.

    Both, because the patterns people write mix the two freely — `**/target/**` is
    relative in spirit and `C:/Users/**/Passwords/**` is not — and a deny rule that
    silently fails to match is the one failure mode this function must not have.
    """
    return any(p.match(rel) or p.match(absolute) for p in _compiled(patterns))


_DIR_PATTERN = re.compile(r"^\*\*/([^*/?\[\]]+)/\*\*$")


def prunable_dirs(patterns: tuple[str, ...]) -> set[str]:
    """Directory names extracted from `**/name/**` patterns, for pruning during a walk.

    Only that exact shape. A pattern the walker cannot prove is a whole-directory
    exclusion is left to the per-file check, which is slower but never wrong.
    """
    return {m.group(1) for p in patterns if (m := _DIR_PATTERN.match(p))}


def _git_tracked(root: Path) -> set[str] | None:
    """What git knows about under `root`, or None when `root` is not a repository.

    `respect_gitignore` asks git rather than reimplementing gitignore semantics — which
    are subtle enough that a second implementation would be wrong in ways nobody would
    notice until something private got indexed.

    Returning None is not a failure: three of the seven declared projects are not
    repositories, and for those the `exclude` globs are the only defence. That is why
    `**/target/**` is written out in collections.yaml rather than left to a `.gitignore`
    that would never be read.
    """
    if not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, never a shell (AGENTS.md)
            ["git", "-C", str(root), "ls-files", "-z"],  # noqa: S607
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("rag.git_ls_files_failed", root=str(root), error=str(exc))
        return None
    if proc.returncode != 0:
        log.warning("rag.git_ls_files_failed", root=str(root), code=proc.returncode)
        return None
    # -z and manual decoding: a repository can hold a path that is not valid UTF-8, and
    # `text=True` would replace characters, producing a name that never matches the file
    # it came from — which presents as "this file is untracked" for no visible reason.
    return {raw.decode("utf-8", "surrogateescape") for raw in proc.stdout.split(b"\0") if raw}


def _tracked_dirs(tracked: set[str]) -> set[str]:
    """Every directory that contains a tracked file, so the walk can prune the rest."""
    dirs = {""}
    for rel in tracked:
        parts = rel.split("/")[:-1]
        for i in range(len(parts)):
            dirs.add("/".join(parts[: i + 1]))
    return dirs


def _units(collection: Collection, root: Path, stats: WalkStats) -> list[tuple[Path, str]]:
    """(directory, project name) pairs to walk for one root of one collection."""
    if not collection.include_projects:
        return [(root, root.name)]
    units: list[tuple[Path, str]] = []
    for name in collection.include_projects:
        candidate = root / name
        if candidate.is_dir():
            units.append((candidate, name))
        else:
            # Named in the config but absent on disk. Reported, not silently dropped:
            # a rename is indistinguishable from a typo unless someone says so.
            stats.unknown_projects.append(name)
            log.warning("rag.project_missing", collection=collection.id, project=name)
    return units


def walk(
    registry: CollectionRegistry,
    *,
    only: str | None = None,
    stats: WalkStats | None = None,
) -> Iterator[Document]:
    """Yield every document the registry permits.

    `only` restricts the walk to one collection id, which is what an incremental
    reindex of a single source uses.
    """
    stats = stats if stats is not None else WalkStats()

    for collection in registry.collections:
        if not collection.enabled or (only is not None and collection.id != only):
            continue
        deny_prune = prunable_dirs(registry.deny)
        prune = deny_prune | prunable_dirs(collection.exclude) | PROJECT_SKIP

        for root in collection.roots:
            if not root.exists():
                stats.missing_roots.append(str(root))
                log.warning("rag.root_missing", collection=collection.id, root=str(root))
                continue

            for unit_root, project in _units(collection, root, stats):
                tracked = _git_tracked(unit_root) if collection.respect_gitignore else None
                allowed_dirs = _tracked_dirs(tracked) if tracked is not None else None

                for dirpath, dirnames, filenames in os.walk(unit_root):
                    reldir = Path(dirpath).relative_to(unit_root).as_posix()
                    reldir = "" if reldir == "." else reldir

                    kept: list[str] = []
                    for name in dirnames:
                        child = f"{reldir}/{name}".lstrip("/")
                        if name in deny_prune:
                            stats.denied_dirs += 1
                            log.info(
                                "rag.denied_dir",
                                collection=collection.id,
                                path=f"{project}/{child}",
                            )
                            continue
                        if name in prune or (
                            allowed_dirs is not None and child not in allowed_dirs
                        ):
                            stats.pruned_dirs += 1
                            continue
                        kept.append(name)
                    dirnames[:] = kept

                    for name in filenames:
                        doc = _consider(
                            Path(dirpath) / name,
                            rel=f"{reldir}/{name}".lstrip("/"),
                            collection=collection,
                            registry=registry,
                            project=project,
                            tracked=tracked,
                            stats=stats,
                        )
                        if doc is not None:
                            yield doc


def _consider(
    path: Path,
    *,
    rel: str,
    collection: Collection,
    registry: CollectionRegistry,
    project: str,
    tracked: set[str] | None,
    stats: WalkStats,
) -> Document | None:
    """Apply every rule to one file. Order matters — see the module docstring."""
    absolute = path.as_posix()

    if _matches(rel, absolute, registry.deny):
        stats.denied += 1
        return None
    if _matches(rel, absolute, collection.exclude):
        stats.excluded += 1
        return None
    if tracked is not None and rel not in tracked:
        stats.untracked += 1
        return None

    kind = classify(path)
    if kind is None:
        stats.wrong_type += 1
        return None

    try:
        stat = path.stat()
    except OSError:
        stats.unreadable += 1
        return None
    if stat.st_size > collection.max_file_bytes:
        stats.too_large += 1
        return None

    key = f"{project}/{rel}" if collection.include_projects else rel
    return Document(
        collection=collection.id,
        project=project,
        path=key,
        abs_path=path,
        kind=kind,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
