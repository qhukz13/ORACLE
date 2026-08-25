"""The capability registry: what each agent is allowed and preferred to do (PLANNER.md §5).

`config/agents.yaml`, loaded the way policy is loaded — data the model cannot modify, read
at startup and on an explicit reload, never from a tool. It answers three questions a plan
is not permitted to answer for itself:

* is this **role** one ORACLE knows?
* is this **agent** one ORACLE has?
* does this agent hold that role?

A name the registry does not know is a **validation error, not a lookup that missed**. That
distinction is the whole point: a hallucinated role must never fall through to a default,
and a hallucinated project name must never become a path (ADR-0021).

Loading is deliberately forgiving in one direction only. A missing or unreadable file means
**no agent holds any role**, so planning cannot start and the fallback ladder takes over —
the same instinct as policy's read-only lockdown: a registry that fails open would let a
plan pick its own executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from oracle.logsink import get_logger

log = get_logger(__name__)

DEFAULT_PATH = Path("config/agents.yaml")


@dataclass(frozen=True)
class Role:
    name: str
    outcome: str
    workspace: str = "none"
    #: `verifier` is the one role no model holds: where code can judge, no model is asked.
    deterministic: bool = False


@dataclass(frozen=True)
class Agent:
    id: str
    adapter: str
    roles: frozenset[str]
    locality: str
    cost: str
    egress: str
    workspace: str = "none"
    structured_output: bool = False
    #: Measured, not declared: an agent that cannot write is one that can never hold a
    #: role whose outcome is a diff (INTEGRATIONS.md §5).
    read_only: bool = False
    effort: str | None = None


@dataclass(frozen=True)
class Registry:
    roles: dict[str, Role] = field(default_factory=dict)
    agents: dict[str, Agent] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    #: Why the registry is empty, when it is. Carried so a caller can say "planning is
    #: unavailable because …" instead of "planning is unavailable".
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return bool(self.agents) and bool(self.roles)

    def holders_of(self, role: str) -> list[Agent]:
        """Agents that may hold this role, cheapest first (PLANNER.md §5, rule 5):
        local < subscription < quota < metered. Cost order is a tiebreak, not a policy —
        policy already refused anything it disliked before this is consulted."""
        order = {"free": 0, "subscription": 1, "quota": 2, "metered": 3}
        holders = [a for a in self.agents.values() if role in a.roles]
        return sorted(holders, key=lambda a: (order.get(a.cost, 9), a.id))

    def default_for(self, role: str) -> Agent | None:
        """The registry's stated default, if it still holds the role. A default that has
        lost its role is ignored rather than honoured — that is exactly the line
        `defaults:` and `roles:` disagreeing would mean, and honouring it would resurrect
        a decision a measurement already overturned."""
        named = self.defaults.get(role)
        if named:
            agent = self.agents.get(named)
            if agent is not None and role in agent.roles:
                return agent
            log.warning("registry.stale_default", role=role, agent=named)
        holders = self.holders_of(role)
        return holders[0] if holders else None

    def role_can_be_held_by(self, role: str, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        if agent is None or role not in agent.roles:
            return False
        wanted = self.roles.get(role)
        if wanted is not None and wanted.outcome == "diff" and agent.read_only:
            # Belt and braces: the registry should not list such a pairing at all, and if
            # it ever does, the pairing loses rather than the measurement.
            return False
        return True


def load_registry(path: Path | None = None) -> Registry:
    """Read the registry. Never raises: an unreadable registry is a *routing fact*, the
    way a missing vendor binary is, and the caller degrades instead of crashing."""
    target = path or DEFAULT_PATH
    try:
        raw: Any = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.warning("registry.unreadable", path=str(target), error=str(exc))
        return Registry(problem=f"{target} could not be read: {exc}")
    if not isinstance(raw, dict):
        return Registry(problem=f"{target} is not a mapping")

    roles: dict[str, Role] = {}
    for name, body in (raw.get("roles") or {}).items():
        body = body if isinstance(body, dict) else {}
        roles[str(name)] = Role(
            name=str(name),
            outcome=str(body.get("outcome", "report")),
            workspace=str(body.get("workspace", "none")),
            deterministic=bool(body.get("deterministic", False)),
        )

    agents: dict[str, Agent] = {}
    for agent_id, body in (raw.get("agents") or {}).items():
        body = body if isinstance(body, dict) else {}
        declared = {str(r) for r in (body.get("roles") or [])}
        unknown = declared - set(roles)
        if unknown:
            # A typo here would silently give an agent a role nothing can schedule.
            log.warning("registry.unknown_roles", agent=str(agent_id), roles=sorted(unknown))
        agents[str(agent_id)] = Agent(
            id=str(agent_id),
            adapter=str(body.get("adapter", "")),
            roles=frozenset(declared & set(roles)),
            locality=str(body.get("locality", "cloud")),
            cost=str(body.get("cost", "metered")),
            egress=str(body.get("egress", "")),
            workspace=str(body.get("workspace", "none")),
            structured_output=bool(body.get("structured_output", False)),
            read_only=bool(body.get("read_only", False)),
            effort=(str(body["effort"]) if body.get("effort") else None),
        )

    defaults = {str(k): str(v) for k, v in (raw.get("defaults") or {}).items()}
    log.info("registry.loaded", roles=len(roles), agents=len(agents), path=str(target))
    return Registry(roles=roles, agents=agents, defaults=defaults)
