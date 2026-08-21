"""Version control: `git.*`.

Intent-shaped, not a shell (docs/TOOLS.md rule 1). `git.commit(path, message)` can only
produce `git commit -m <message>` — that is a *promise about what can happen*, which is
what makes a precise tier and a precise undo possible. `execute_command("git ...")`
would be none of those things.

Three decisions worth knowing before reading the code:

  * **Porcelain v2, never scraped prose.** `git status` output is localised and
    reformatted between versions; `--porcelain=v2` is a stable machine format and is
    the only thing parsed here (rule 4).
  * **Every mutation reports an `UndoPlan`.** That is what buys T1 — a commit that
    could not be reversed would have to prompt, and prompting on every commit is how
    an agent becomes unusable (ADR-0005).
  * **`git` never gets to prompt.** `GIT_TERMINAL_PROMPT=0` and a disabled credential
    helper turn "waiting forever for a password" into a fast, explainable failure. A
    hung `git push` inside a tool call is indistinguishable from a hang in ORACLE.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from oracle.logsink import get_logger
from oracle.policy.model import Capability, Tier
from oracle.tools.contract import ToolArgs, ToolContext, ToolResult, tool
from oracle.tools.proc import Completed, clip, run
from oracle.tools.undo import UndoKind, UndoPlan

log = get_logger(__name__)

ScopedPath = Annotated[str, Field(description="Absolute path to the git repository")]

#: Nothing here may block on a human. A credential prompt inside a tool call looks
#: exactly like a hang, and the toolhost would sit on it until the timeout.
GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GCM_INTERACTIVE": "never",
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C.UTF-8",
}

#: Prepended to every invocation. `quotepath=false` keeps non-ASCII filenames readable
#: instead of octal-escaped, which matters on a machine with Cyrillic paths.
GIT_FLAGS = ["-c", "core.quotepath=false", "--no-pager"]

MAX_DIFF_CHARS = 20_000
MAX_LOG_ENTRIES = 100


async def _git(ctx: ToolContext, repo: Path, args: list[str], *, timeout_s: int = 30) -> Completed:
    return await run(
        ctx.program("git"), [*GIT_FLAGS, *args], cwd=repo, timeout_s=timeout_s, env=GIT_ENV
    )


async def _require_repo(ctx: ToolContext, repo: Path) -> Path:
    """Resolve the repository root, and refuse anything that is not inside one.

    Returns the ROOT, not the path we were given: `git.status` on a subdirectory should
    describe the repository, and staging paths relative to a random subdirectory is how
    you accidentally commit the wrong tree.
    """
    if not repo.exists():
        raise ValueError(f"{repo} does not exist")
    target = repo if repo.is_dir() else repo.parent
    r = await _git(ctx, target, ["rev-parse", "--show-toplevel"])
    if not r.ok:
        raise ValueError(f"{repo} is not inside a git repository")
    return Path(r.stdout.strip())


def _fail(what: str, r: Completed) -> ValueError:
    """Surface git's own words. A wrapper that swallows them and says 'command failed'
    turns a fixable problem into a mystery."""
    message = (r.stderr.strip() or r.stdout.strip() or "no output")[:600]
    return ValueError(f"{what} failed (exit {r.returncode}): {message}")


# ----------------------------------------------------------------------- git.status


class GitStatusArgs(ToolArgs):
    path: ScopedPath


class GitStatusResult(ToolResult):
    repo: str
    branch: str
    upstream: str | None
    ahead: int
    behind: int
    staged: list[str]
    unstaged: list[str]
    untracked: list[str]
    conflicted: list[str]
    clean: bool


@tool(
    id="git.status",
    summary=(
        "Whether the repository is clean: branch, ahead/behind, and which files are "
        "staged, changed or untracked. Does not show the changes themselves."
    ),
    args=GitStatusArgs,
    result=GitStatusResult,
    capabilities={Capability.FS_READ, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T0,
    reversible=True,
    intents={"status", "question", "investigate", "modify"},
    side_effects="None.",
    path_fields={"path"},
    programs={"git"},
)
async def git_status(*, ctx: ToolContext, args: GitStatusArgs) -> GitStatusResult:
    repo = await _require_repo(ctx, ctx.resolved["path"])
    r = await _git(ctx, repo, ["status", "--porcelain=v2", "--branch", "--untracked-files=normal"])
    if not r.ok:
        raise _fail("git status", r)

    branch, upstream = "(detached)", None
    ahead = behind = 0
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    conflicted: list[str] = []

    for line in r.stdout.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head ") :].strip()
        elif line.startswith("# branch.upstream "):
            upstream = line[len("# branch.upstream ") :].strip()
        elif line.startswith("# branch.ab "):
            for token in line[len("# branch.ab ") :].split():
                if token.startswith("+"):
                    ahead = int(token[1:])
                elif token.startswith("-"):
                    behind = int(token[1:])
        elif line.startswith(("1 ", "2 ")):
            fields = line.split(" ", 8)
            xy = fields[1]
            # A rename records "<new>\t<old>"; the new name is what the user cares about.
            path = fields[-1].split("\t")[0]
            if xy[0] != ".":
                staged.append(path)
            if xy[1] != ".":
                unstaged.append(path)
        elif line.startswith("? "):
            untracked.append(line[2:])
        elif line.startswith("u "):
            conflicted.append(line.split(" ", 10)[-1])

    return GitStatusResult(
        repo=str(repo),
        branch=branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicted=conflicted,
        clean=not (staged or unstaged or untracked or conflicted),
    )


# ------------------------------------------------------------------------- git.diff


class GitDiffArgs(ToolArgs):
    path: ScopedPath
    #: What is staged, rather than what is merely changed. The distinction is the whole
    #: point of a review step before a commit.
    staged: bool = False


class GitDiffResult(ToolResult):
    repo: str
    files_changed: int
    insertions: int
    deletions: int
    patch: str
    truncated: bool
    stat: str


@tool(
    id="git.diff",
    summary=(
        "The actual line-by-line changes, as a patch. For whether anything changed at "
        "all, use git.status."
    ),
    args=GitDiffArgs,
    result=GitDiffResult,
    capabilities={Capability.FS_READ, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T0,
    reversible=True,
    intents={"status", "question", "investigate", "modify"},
    side_effects="None.",
    path_fields={"path"},
    programs={"git"},
)
async def git_diff(*, ctx: ToolContext, args: GitDiffArgs) -> GitDiffResult:
    repo = await _require_repo(ctx, ctx.resolved["path"])
    scope = ["--cached"] if args.staged else []

    stat = await _git(ctx, repo, ["diff", *scope, "--numstat"])
    if not stat.ok:
        raise _fail("git diff --numstat", stat)

    insertions = deletions = files = 0
    for line in stat.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        # "-" for binary files: counting them as zero is honest, guessing is not.
        insertions += int(parts[0]) if parts[0].isdigit() else 0
        deletions += int(parts[1]) if parts[1].isdigit() else 0

    patch = await _git(ctx, repo, ["diff", *scope])
    if not patch.ok:
        raise _fail("git diff", patch)
    text, clipped = clip(patch.stdout, MAX_DIFF_CHARS)

    return GitDiffResult(
        repo=str(repo),
        files_changed=files,
        insertions=insertions,
        deletions=deletions,
        patch=text,
        truncated=clipped or patch.truncated,
        stat=stat.stdout.strip(),
    )


# -------------------------------------------------------------------------- git.log


class GitLogArgs(ToolArgs):
    path: ScopedPath
    limit: int = 20


class Commit(ToolResult):
    sha: str
    short: str
    author: str
    date: str
    subject: str


class GitLogResult(ToolResult):
    repo: str
    commits: list[Commit]


@tool(
    id="git.log",
    summary="Recent commits: sha, author, date and subject.",
    args=GitLogArgs,
    result=GitLogResult,
    capabilities={Capability.FS_READ, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T0,
    reversible=True,
    intents={"status", "question", "investigate"},
    side_effects="None.",
    path_fields={"path"},
    programs={"git"},
)
async def git_log(*, ctx: ToolContext, args: GitLogArgs) -> GitLogResult:
    repo = await _require_repo(ctx, ctx.resolved["path"])
    limit = max(1, min(args.limit, MAX_LOG_ENTRIES))
    # \x1f as the field separator: it cannot appear in a commit subject, whereas every
    # printable delimiter can and eventually does.
    fmt = "%H%x1f%h%x1f%an%x1f%aI%x1f%s"
    r = await _git(ctx, repo, ["log", f"-{limit}", f"--format={fmt}"])
    if not r.ok:
        # An empty repository has no HEAD; that is a state, not a failure.
        if "does not have any commits" in r.stderr:
            return GitLogResult(repo=str(repo), commits=[])
        raise _fail("git log", r)

    commits: list[Commit] = []
    for line in r.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        commits.append(
            Commit(sha=parts[0], short=parts[1], author=parts[2], date=parts[3], subject=parts[4])
        )
    return GitLogResult(repo=str(repo), commits=commits)


# -------------------------------------------------------------------------- git.add


class GitAddArgs(ToolArgs):
    #: One path: a file, a directory, or the repository root for "everything". A list
    #: of paths would need every entry canonicalised, and one path covers the real use.
    path: ScopedPath


class GitAddResult(ToolResult):
    repo: str
    staged: list[str]
    undo: UndoPlan


@tool(
    id="git.add",
    summary="Stage files so a later commit can include them. Does NOT create a commit.",
    args=GitAddArgs,
    result=GitAddResult,
    capabilities={Capability.FS_READ, Capability.GIT_WRITE, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T1,
    reversible=True,
    undo="git reset -- <path>",
    intents={"modify", "run"},
    side_effects="Stages changes. Nothing is committed and no file content changes.",
    path_fields={"path"},
    programs={"git"},
)
async def git_add(*, ctx: ToolContext, args: GitAddArgs) -> GitAddResult:
    target = ctx.resolved["path"]
    repo = await _require_repo(ctx, target)

    r = await _git(ctx, repo, ["add", "--", str(target)])
    if not r.ok:
        raise _fail("git add", r)

    staged = await _git(ctx, repo, ["diff", "--cached", "--name-only"])
    return GitAddResult(
        repo=str(repo),
        staged=[line for line in staged.stdout.splitlines() if line],
        undo=UndoPlan(
            kind=UndoKind.GIT_UNSTAGE,
            target=str(repo),
            origin=str(target),
            note=f"unstage {target}",
        ),
    )


# ----------------------------------------------------------------------- git.commit


class GitCommitArgs(ToolArgs):
    path: ScopedPath
    message: str
    #: `git commit -a`: include tracked files that were never staged. Untracked files
    #: are still not swept in — committing a file nobody staged is a surprise.
    all_tracked: bool = False


class GitCommitResult(ToolResult):
    repo: str
    sha: str
    short: str
    branch: str
    files_changed: int
    insertions: int
    deletions: int
    undo: UndoPlan


@tool(
    id="git.commit",
    summary=(
        "Record the staged changes as a commit. Requires a message. Undo puts the "
        "changes back in the staging area."
    ),
    args=GitCommitArgs,
    result=GitCommitResult,
    capabilities={Capability.FS_READ, Capability.GIT_WRITE, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T1,
    reversible=True,
    undo="git reset --soft HEAD~1",
    intents={"modify", "run"},
    side_effects="Creates a commit. Nothing leaves the machine — that is `git.push`.",
    path_fields={"path"},
    programs={"git"},
)
async def git_commit(*, ctx: ToolContext, args: GitCommitArgs) -> GitCommitResult:
    repo = await _require_repo(ctx, ctx.resolved["path"])
    message = args.message.strip()
    if len(message) < 3:
        raise ValueError("a commit message of fewer than 3 characters is not a message")

    argv = ["commit", "-m", message]
    if args.all_tracked:
        argv.insert(1, "-a")

    r = await _git(ctx, repo, argv, timeout_s=60)
    if not r.ok:
        combined = r.combined
        if "nothing to commit" in combined or "no changes added" in combined:
            raise ValueError("nothing is staged, so there is nothing to commit")
        raise _fail("git commit", r)

    sha = (await _git(ctx, repo, ["rev-parse", "HEAD"])).stdout.strip()
    short = sha[:8]
    branch = (await _git(ctx, repo, ["rev-parse", "--abbrev-ref", "HEAD"])).stdout.strip()

    files = insertions = deletions = 0
    stat = await _git(ctx, repo, ["show", "--numstat", "--format=", sha])
    for line in stat.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        insertions += int(parts[0]) if parts[0].isdigit() else 0
        deletions += int(parts[1]) if parts[1].isdigit() else 0

    return GitCommitResult(
        repo=str(repo),
        sha=sha,
        short=short,
        branch=branch,
        files_changed=files,
        insertions=insertions,
        deletions=deletions,
        # The sha is what makes the undo safe: it is checked against HEAD before the
        # reset, so an undo can never silently unmake somebody else's later commit.
        undo=UndoPlan(
            kind=UndoKind.GIT_UNCOMMIT,
            target=str(repo),
            origin=sha,
            note=f"undo commit {short}: {message[:60]}",
        ),
    )


# ----------------------------------------------------------------------- git.branch


class GitBranchArgs(ToolArgs):
    path: ScopedPath
    #: An enum, because Ollama's constrained decoding enforces enums but not patterns
    #: (ADR-0017). "list" is the default so a vague request cannot mutate anything.
    action: Literal["list", "create", "switch"] = "list"
    name: str = ""


class GitBranchResult(ToolResult):
    repo: str
    current: str
    branches: list[str]
    action: str
    undo: UndoPlan


@tool(
    id="git.branch",
    summary="List branches, create one, or switch to one.",
    args=GitBranchArgs,
    result=GitBranchResult,
    capabilities={Capability.FS_READ, Capability.GIT_WRITE, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T1,
    reversible=True,
    undo="delete the created branch, or switch back to the previous one",
    intents={"modify", "run", "status"},
    side_effects="Creates or switches a branch. Working tree changes are never discarded.",
    path_fields={"path"},
    programs={"git"},
)
async def git_branch(*, ctx: ToolContext, args: GitBranchArgs) -> GitBranchResult:
    repo = await _require_repo(ctx, ctx.resolved["path"])
    previous = (await _git(ctx, repo, ["rev-parse", "--abbrev-ref", "HEAD"])).stdout.strip()
    plan = UndoPlan()

    if args.action != "list":
        name = args.name.strip()
        if not name:
            raise ValueError(f"action={args.action} needs a branch name")
        # git has its own rules for what a ref may be called; asking it beats
        # re-implementing them and getting a subtly different answer.
        check = await _git(ctx, repo, ["check-ref-format", "--branch", name])
        if not check.ok:
            raise ValueError(f"{name!r} is not a valid branch name")

        if args.action == "create":
            r = await _git(ctx, repo, ["switch", "-c", name])
            if not r.ok:
                raise _fail("git switch -c", r)
            plan = UndoPlan(
                kind=UndoKind.GIT_DELETE_BRANCH,
                target=str(repo),
                origin=name,
                note=f"delete branch {name} and return to {previous}",
                backup=previous,
            )
        else:
            r = await _git(ctx, repo, ["switch", name])
            if not r.ok:
                raise _fail("git switch", r)
            plan = UndoPlan(
                kind=UndoKind.GIT_CHECKOUT,
                target=str(repo),
                origin=previous,
                note=f"switch back to {previous}",
            )

    listing = await _git(ctx, repo, ["branch", "--format=%(refname:short)"])
    current = (await _git(ctx, repo, ["rev-parse", "--abbrev-ref", "HEAD"])).stdout.strip()
    return GitBranchResult(
        repo=str(repo),
        current=current,
        branches=[b for b in listing.stdout.splitlines() if b],
        action=args.action,
        undo=plan,
    )


# ------------------------------------------------------------------------ git.stash


class GitStashArgs(ToolArgs):
    path: ScopedPath
    action: Literal["list", "save", "pop"] = "list"
    message: str = ""


class GitStashResult(ToolResult):
    repo: str
    action: str
    entries: list[str]
    detail: str
    undo: UndoPlan


@tool(
    id="git.stash",
    summary="List, save or pop stashed changes.",
    args=GitStashArgs,
    result=GitStashResult,
    capabilities={Capability.FS_READ, Capability.GIT_WRITE, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T1,
    reversible=True,
    undo="pop the stash that was just created",
    intents={"modify", "run"},
    side_effects="Moves uncommitted changes into the stash, or back out of it.",
    path_fields={"path"},
    programs={"git"},
)
async def git_stash(*, ctx: ToolContext, args: GitStashArgs) -> GitStashResult:
    repo = await _require_repo(ctx, ctx.resolved["path"])
    plan = UndoPlan()
    detail = ""

    if args.action == "save":
        argv = ["stash", "push"]
        if args.message.strip():
            argv += ["-m", args.message.strip()]
        r = await _git(ctx, repo, argv)
        if not r.ok:
            raise _fail("git stash push", r)
        detail = r.stdout.strip()
        if "No local changes" in detail:
            # Nothing was stashed, so there is nothing to pop. Reporting an undo that
            # would pop somebody else's stash entry would be actively dangerous.
            plan = UndoPlan(note="nothing was stashed")
        else:
            plan = UndoPlan(
                kind=UndoKind.GIT_STASH_POP, target=str(repo), note="restore the stashed changes"
            )
    elif args.action == "pop":
        r = await _git(ctx, repo, ["stash", "pop"])
        if not r.ok:
            raise _fail("git stash pop", r)
        detail = r.stdout.strip()

    listing = await _git(ctx, repo, ["stash", "list"])
    return GitStashResult(
        repo=str(repo),
        action=args.action,
        entries=[line for line in listing.stdout.splitlines() if line],
        detail=detail[:2000],
        undo=plan,
    )


# ------------------------------------------------------------------------- git.push


class GitPushArgs(ToolArgs):
    path: ScopedPath
    remote: str = "origin"
    #: Empty means the current branch. A branch is never inferred from anything the
    #: model wrote — it is read from the repository.
    branch: str = ""


class GitPushResult(ToolResult):
    repo: str
    remote: str
    branch: str
    argv: str
    dry_run: bool
    output: str
    undo: UndoPlan


@tool(
    id="git.push",
    summary="Push the current branch to a remote. Visible to others and not undoable.",
    args=GitPushArgs,
    result=GitPushResult,
    capabilities={
        Capability.FS_READ,
        Capability.GIT_WRITE,
        Capability.NET_EGRESS,
        Capability.PROC_SPAWN,
    },
    scopes={"projects"},
    risk=Tier.T2,
    # Deliberately NOT reversible. A push can be followed by another push, but what was
    # published cannot be unpublished — and pretending otherwise would let it slide to
    # T1 and run without asking.
    reversible=False,
    dry_run=True,
    intents={"modify", "run"},
    side_effects="Publishes commits to a remote. Cannot be taken back.",
    path_fields={"path"},
    programs={"git"},
)
async def git_push(*, ctx: ToolContext, args: GitPushArgs) -> GitPushResult:
    repo = await _require_repo(ctx, ctx.resolved["path"])
    branch = (
        args.branch.strip()
        or (await _git(ctx, repo, ["rev-parse", "--abbrev-ref", "HEAD"])).stdout.strip()
    )
    if branch in ("", "HEAD"):
        raise ValueError("HEAD is detached; refusing to guess which branch to push")

    argv = ["push", args.remote, branch]

    if ctx.dry_run:
        # NOT `git push --dry-run`. That contacts the remote, and a dry run must have no
        # side effect at all — network egress included — because it runs without an
        # approval. The local answer is also the better one: the user wants to know
        # WHICH COMMITS would be published, not that the transport works.
        pending = await _git(
            ctx, repo, ["log", "--format=%h %s", f"{args.remote}/{branch}..{branch}"]
        )
        commits = [line for line in pending.stdout.splitlines() if line] if pending.ok else []
        note = (
            f"{len(commits)} commit(s) would be published to {args.remote}/{branch}"
            if pending.ok
            else f"{args.remote}/{branch} is unknown locally; everything on {branch} would be new"
        )
        return GitPushResult(
            repo=str(repo),
            remote=args.remote,
            branch=branch,
            argv=" ".join(["git", *argv]),
            dry_run=True,
            output="\n".join([note, *commits[:50]]),
            undo=UndoPlan(note="a push cannot be undone"),
        )

    r = await _git(ctx, repo, argv, timeout_s=120)
    if not r.ok:
        raise _fail("git push", r)

    return GitPushResult(
        repo=str(repo),
        remote=args.remote,
        branch=branch,
        argv=r.argv_display(),
        dry_run=False,
        output=clip(r.combined.strip(), 4000)[0],
        undo=UndoPlan(note="a push cannot be undone"),
    )


# ------------------------------------------------------------- git.undo (not offered)


class GitUndoArgs(ToolArgs):
    path: ScopedPath
    kind: str
    #: The branch, sha or previous ref the plan recorded. Never model-supplied.
    ref: str = ""
    extra: str = ""


class GitUndoResult(ToolResult):
    repo: str
    kind: str
    detail: str


@tool(
    id="git.undo",
    summary="Internal: execute a recorded git undo recipe. Not selectable by the model.",
    args=GitUndoArgs,
    result=GitUndoResult,
    capabilities={Capability.FS_READ, Capability.GIT_WRITE, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T1,
    reversible=False,
    intents=frozenset(),
    side_effects="Reverses a git mutation ORACLE previously recorded.",
    path_fields={"path"},
    programs={"git"},
    # The model must never be able to call this. An agent that could undo at will could
    # quietly erase work it had been asked to do.
    hidden=True,
)
async def git_undo(*, ctx: ToolContext, args: GitUndoArgs) -> GitUndoResult:
    repo = await _require_repo(ctx, ctx.resolved["path"])
    kind = UndoKind(args.kind)

    if kind is UndoKind.GIT_UNCOMMIT:
        head = (await _git(ctx, repo, ["rev-parse", "HEAD"])).stdout.strip()
        if head != args.ref:
            # Refuse rather than guess. Undoing "the last commit" when the last commit
            # is no longer the one we made would destroy work nobody asked us to touch.
            raise ValueError(
                f"HEAD has moved since that commit (now {head[:8]}, expected {args.ref[:8]}); "
                "refusing to reset"
            )
        r = await _git(ctx, repo, ["reset", "--soft", "HEAD~1"])
        if not r.ok:
            raise _fail("git reset --soft", r)
        detail = f"commit {args.ref[:8]} undone; the changes are staged again"

    elif kind is UndoKind.GIT_UNSTAGE:
        r = await _git(ctx, repo, ["reset", "--", args.ref or "."])
        if not r.ok:
            raise _fail("git reset", r)
        detail = f"unstaged {args.ref or 'everything'}"

    elif kind is UndoKind.GIT_CHECKOUT:
        r = await _git(ctx, repo, ["switch", args.ref])
        if not r.ok:
            raise _fail("git switch", r)
        detail = f"switched back to {args.ref}"

    elif kind is UndoKind.GIT_DELETE_BRANCH:
        if args.extra:
            back = await _git(ctx, repo, ["switch", args.extra])
            if not back.ok:
                raise _fail("git switch", back)
        r = await _git(ctx, repo, ["branch", "-D", args.ref])
        if not r.ok:
            raise _fail("git branch -D", r)
        detail = f"deleted branch {args.ref}"

    elif kind is UndoKind.GIT_STASH_POP:
        r = await _git(ctx, repo, ["stash", "pop"])
        if not r.ok:
            raise _fail("git stash pop", r)
        detail = r.stdout.strip()[:600]

    else:  # pragma: no cover - the journal only dispatches git kinds here
        raise ValueError(f"{args.kind} is not a git undo recipe")

    return GitUndoResult(repo=str(repo), kind=str(kind), detail=detail)


GIT_TOOLS = [
    git_status,
    git_diff,
    git_log,
    git_add,
    git_commit,
    git_branch,
    git_stash,
    git_push,
    git_undo,
]
