"""Policy vocabulary: capabilities, risk tiers, decisions.

Policy is **data, not code** (docs/SECURITY.md#2-design-principles). Nothing the model
emits and nothing retrieved from a document can reach these types; they are built from
`config/policy.yaml` at startup and on explicit reload only.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Capability(StrEnum):
    """Coarse and few, so the set stays auditable. A tool declares these in its
    contract; it cannot request one at runtime."""

    FS_READ = "fs.read"
    #: Writes a path the CONTRACT names. That is what makes an undo plan possible: we
    #: know in advance which file to back up. A tool whose writes happen inside a
    #: spawned program (a build writing `dist/`, a test suite writing a fixture) does
    #: NOT declare this — `proc.spawn` already means "may write inside its scope", and
    #: claiming fs.write without a named path would promise a backup nobody can take.
    FS_WRITE = "fs.write"
    FS_DELETE = "fs.delete"
    PROC_SPAWN = "proc.spawn"
    PROC_KILL = "proc.kill"
    NET_EGRESS = "net.egress"
    INPUT_SYNTH = "input.synth"
    SYS_INFO = "sys.info"
    SYS_SETTINGS = "sys.settings"
    GIT_WRITE = "git.write"
    #: Typing into a live shell. Deliberately NOT `proc.spawn`
    #: (docs/SECURITY.md#4b): the agent may read a PTY's output all day, but writing
    #: into one is full user privilege with no scope — an allowlist cannot inspect what
    #: a shell will do with a line of text, and there is no undo for it.
    TERM_WRITE = "term.write"
    SECRET_READ = "secret.read"  # noqa: S105 - a capability name, not a credential
    AGENT_DELEGATE = "agent.delegate"


#: Capabilities that can change the world. Used by the registry to police contracts and
#: by the gate to decide what read-only lockdown refuses. ONE definition, because two
#: copies of a security-relevant set is one copy waiting to drift.
WRITING_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.FS_WRITE,
        Capability.FS_DELETE,
        Capability.PROC_SPAWN,
        Capability.PROC_KILL,
        Capability.NET_EGRESS,
        Capability.GIT_WRITE,
        Capability.TERM_WRITE,
        Capability.INPUT_SYNTH,
        Capability.SYS_SETTINGS,
    }
)


class Tier(IntEnum):
    """Ordered so escalation is arithmetic. See docs/SECURITY.md#risk-tiers.

    The tier is a function of (tool, RESOLVED arguments, scope, taint) — never of the
    tool alone. `fs.write` into scratch and `fs.write` into a project are not the same
    act.
    """

    T0 = 0  # no side effect, in scope           -> auto
    T1 = 1  # reversible local write, in scope   -> auto + undo journal
    T2 = 2  # externally visible / costly        -> confirm
    T3 = 3  # destructive / wide blast radius    -> confirm_strong
    T4 = 4  # never                              -> deny, not offerable

    @property
    def label(self) -> str:
        return self.name


class Decision(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    CONFIRM_STRONG = "confirm_strong"
    DENY = "deny"


#: The only mapping from tier to path. Kept here rather than inline so a change is
#: visible in one place and in the audit log.
TIER_DECISION: dict[Tier, Decision] = {
    Tier.T0: Decision.ALLOW,
    Tier.T1: Decision.ALLOW,
    Tier.T2: Decision.CONFIRM,
    Tier.T3: Decision.CONFIRM_STRONG,
    Tier.T4: Decision.DENY,
}


class Provenance(StrEnum):
    """Where a piece of context came from. Drives taint
    (docs/SECURITY.md#6-prompt-injection-and-taint-tracking)."""

    USER = "user"
    SYSTEM = "system"
    LOCAL_OWNED = "local_owned"
    LOCAL_FOREIGN = "local_foreign"
    EXTERNAL = "external"

    @property
    def trusted(self) -> bool:
        return self in (Provenance.USER, Provenance.SYSTEM)


UNTRUSTED = frozenset({Provenance.LOCAL_FOREIGN, Provenance.EXTERNAL})


class PolicyVerdict(BaseModel):
    """The output of the gate. Always carries the rule that fired — a denial that
    cannot explain itself is a support ticket."""

    model_config = ConfigDict(frozen=True)

    decision: Decision
    tier: Tier
    base_tier: Tier
    rule: str
    reason: str = ""
    tainted: bool = False
    escalated: bool = False
    capabilities: frozenset[Capability] = Field(default_factory=frozenset)

    @property
    def allowed_outright(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision in (Decision.CONFIRM, Decision.CONFIRM_STRONG)


class PolicyError(Exception):
    """Configuration is unusable. The caller must fail closed, never open."""
