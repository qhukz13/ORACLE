"""The Policy Gate.

One chokepoint (docs/ARCHITECTURE.md#4-layers). Every side effect crosses it exactly
once, and there is no second path.

Two properties do most of the work:

  * **Fail closed.** An unparseable or missing policy yields read-only mode, loudly.
    A security control that fails open is not a security control.
  * **Deny wins.** `deny_always` is not overridable from the UI, only by editing the
    file by hand — a deliberate speed bump.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from oracle.logsink import get_logger
from oracle.policy.model import (
    TIER_DECISION,
    UNTRUSTED,
    Capability,
    Decision,
    PolicyError,
    PolicyVerdict,
    Provenance,
    Tier,
)
from oracle.policy.paths import PathRejected, PathResolver, ResolvedPath, Scope

log = get_logger(__name__)


@dataclass(frozen=True)
class ToolRule:
    """Per-tool policy, from `config/policy.yaml`."""

    tier: Tier
    scopes: frozenset[str] = frozenset()
    #: A tier override applied when the resolved path lands in a named scope, so
    #: `fs.write` can be T1 in scratch and T2 in a project.
    scope_tiers: dict[str, Tier] = field(default_factory=dict)


@dataclass
class Policy:
    scopes: list[Scope]
    deny_always: list[str]
    tools: dict[str, ToolRule]
    #: True when we could not load real policy and are running locked down.
    read_only: bool = False
    source: str = "config/policy.yaml"

    @property
    def scope_names(self) -> frozenset[str]:
        return frozenset(s.name for s in self.scopes)


#: What we fall back to when policy cannot be trusted. Read-only, no scopes at all:
#: the agent can still talk, and can touch nothing.
LOCKDOWN = Policy(scopes=[], deny_always=["**"], tools={}, read_only=True, source="lockdown")


def load_policy(path: Path) -> Policy:
    """Load policy, or return LOCKDOWN. **Never raises to the caller** — a startup that
    crashes on bad policy is a startup that tempts someone to delete the policy file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise PolicyError(f"{path} is not a mapping")
        return _parse(raw, source=str(path))
    except FileNotFoundError:
        log.error("policy.missing", path=str(path), effect="read-only lockdown")
        return LOCKDOWN
    except Exception as exc:
        log.error("policy.invalid", path=str(path), error=str(exc), effect="read-only lockdown")
        return LOCKDOWN


def _parse(raw: dict[str, Any], source: str) -> Policy:
    scopes: list[Scope] = []
    for name, body in (raw.get("scopes") or {}).items():
        if name == "deny_always":
            continue
        for root in body.get("roots", []):
            scopes.append(
                Scope(
                    name=name,
                    root=Path(str(root["path"])),
                    writable=str(root.get("mode", "ro")).lower() == "rw",
                )
            )

    deny = list((raw.get("scopes") or {}).get("deny_always") or [])

    tools: dict[str, ToolRule] = {}
    for tool_id, body in (raw.get("tools") or {}).items():
        body = body or {}
        try:
            tier = Tier[str(body.get("tier", "T4")).upper()]
        except KeyError as exc:
            raise PolicyError(f"tool {tool_id}: unknown tier {body.get('tier')!r}") from exc
        scope_tiers = {
            str(k): Tier[str(v).upper()] for k, v in (body.get("scope_tiers") or {}).items()
        }
        tools[tool_id] = ToolRule(
            tier=tier,
            scopes=frozenset(body.get("scopes") or []),
            scope_tiers=scope_tiers,
        )

    if not scopes:
        raise PolicyError("policy declares no scopes; refusing to run wide open")

    return Policy(scopes=scopes, deny_always=deny, tools=tools, source=source)


class PolicyEngine:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.resolver = PathResolver(policy.scopes, deny=policy.deny_always)
        #: HALT flips this. Nothing but an explicit human resume clears it
        #: (docs/SECURITY.md#emergency-stop-halt).
        self.halted = False
        self.halt_reason: str | None = None

    # ------------------------------------------------------------------- HALT

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        log.critical("policy.halted", reason=reason)

    def resume(self) -> None:
        self.halted = False
        self.halt_reason = None
        log.warning("policy.resumed")

    # ------------------------------------------------------------------ paths

    def resolve_path(self, raw: str, *, cwd: Path | None = None) -> ResolvedPath:
        return self.resolver.resolve(raw, cwd=cwd)

    # ------------------------------------------------------------------ evaluate

    def evaluate(
        self,
        tool_id: str,
        *,
        capabilities: frozenset[Capability] = frozenset(),
        paths: list[ResolvedPath] | None = None,
        provenances: frozenset[Provenance] = frozenset(),
        declared_tier: Tier | None = None,
    ) -> PolicyVerdict:
        """The gate. Returns a verdict; never performs anything."""
        paths = paths or []

        if self.halted:
            return PolicyVerdict(
                decision=Decision.DENY,
                tier=Tier.T4,
                base_tier=Tier.T4,
                rule="halt",
                reason=f"ORACLE is halted: {self.halt_reason}",
            )

        rule = self.policy.tools.get(tool_id)
        if rule is None:
            # Deny by default. An unlisted tool is not an implicitly safe tool.
            return PolicyVerdict(
                decision=Decision.DENY,
                tier=Tier.T4,
                base_tier=declared_tier or Tier.T4,
                rule="default-deny",
                reason=f"{tool_id} has no policy rule",
                capabilities=capabilities,
            )

        base = rule.tier
        rule_name = f"tools.{tool_id}.tier"

        # A tool may not exceed the tier its own contract declares; policy can only be
        # as permissive as the contract, never more.
        if declared_tier is not None and base < declared_tier:
            base = declared_tier
            rule_name = f"tools.{tool_id}.contract_tier"

        # Read-only lockdown: anything that can change the world is refused.
        writing = capabilities & {
            Capability.FS_WRITE,
            Capability.FS_DELETE,
            Capability.PROC_SPAWN,
            Capability.NET_EGRESS,
            Capability.GIT_WRITE,
            Capability.INPUT_SYNTH,
            Capability.SYS_SETTINGS,
        }
        if self.policy.read_only and writing:
            return PolicyVerdict(
                decision=Decision.DENY,
                tier=Tier.T4,
                base_tier=base,
                rule="lockdown.read_only",
                reason="policy could not be loaded; running read-only",
                capabilities=capabilities,
            )

        # Scope containment, and the per-scope tier override.
        for p in paths:
            if rule.scopes and p.scope.name not in rule.scopes:
                return PolicyVerdict(
                    decision=Decision.DENY,
                    tier=Tier.T4,
                    base_tier=base,
                    rule=f"tools.{tool_id}.scopes",
                    reason=f"{p.real} is in scope {p.scope.name!r}, not permitted for {tool_id}",
                    capabilities=capabilities,
                )
            if writing and not p.writable:
                return PolicyVerdict(
                    decision=Decision.DENY,
                    tier=Tier.T4,
                    base_tier=base,
                    rule=f"scopes.{p.scope.name}.mode",
                    reason=f"scope {p.scope.name!r} is read-only",
                    capabilities=capabilities,
                )
            override = rule.scope_tiers.get(p.scope.name)
            if override is not None and override > base:
                base = override
                rule_name = f"tools.{tool_id}.scope_tiers.{p.scope.name}"

        # Taint escalation: a plan built from untrusted content does not get to
        # auto-write. T0 is unaffected — reading more is not the risk.
        tainted = bool(provenances & UNTRUSTED)
        tier = base
        escalated = False
        if tainted and base > Tier.T0:
            tier = Tier(min(int(base) + 1, int(Tier.T4)))
            escalated = tier != base
            if escalated:
                rule_name = f"taint.escalate({rule_name})"

        return PolicyVerdict(
            decision=TIER_DECISION[tier],
            tier=tier,
            base_tier=base,
            rule=rule_name,
            reason="" if tier < Tier.T4 else f"{tool_id} is forbidden at {tier.label}",
            tainted=tainted,
            escalated=escalated,
            capabilities=capabilities,
        )


__all__ = [
    "LOCKDOWN",
    "PathRejected",
    "Policy",
    "PolicyEngine",
    "ToolRule",
    "load_policy",
]
