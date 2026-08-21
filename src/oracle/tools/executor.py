"""Tool execution, through the gate.

The only route from an intent to a side effect. The ordering below is the security
model, so it is written as one linear function rather than spread across helpers:

    validate args -> resolve paths -> POLICY GATE -> approval -> re-check -> execute -> audit

Every step can refuse. Nothing is executed before the gate returns ALLOW, and a path is
re-resolved immediately before execution so an approval cannot survive the file being
swapped underneath it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from oracle.logsink import get_logger, trace_id_var
from oracle.policy.audit import AuditLog, digest_args
from oracle.policy.engine import PolicyEngine
from oracle.policy.model import Capability, Decision, PolicyVerdict, Provenance, Tier
from oracle.policy.paths import PathRejected, ResolvedPath
from oracle.toolhost import ToolHost, ToolHostUnavailable
from oracle.tools.contract import ToolRegistry, ToolResult

log = get_logger(__name__)


class ToolErrorKind:
    NOT_FOUND = "not_found"
    DENIED = "denied"
    TIMEOUT = "timeout"
    INVALID_ARGS = "invalid_args"
    EXECUTION_FAILED = "execution_failed"
    CANCELLED = "cancelled"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_INVALID = "approval_invalid"


class ToolError(Exception):
    def __init__(
        self, kind: str, message: str, *, detail: str = "", retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail
        # A policy denial is NEVER retryable: retrying a denial is how an agent nags a
        # user into approving something.
        self.retryable = retryable and kind != ToolErrorKind.DENIED


@dataclass
class Approval:
    """A single-use, expiring grant bound to exact arguments."""

    approval_id: str
    tool: str
    args_digest: str
    tier: Tier
    expires_at: float
    device: str = "desktop"
    used: bool = False

    def valid_for(self, tool: str, args_digest: str, now: float) -> tuple[bool, str]:
        if self.used:
            return False, "approval already used"
        if now > self.expires_at:
            return False, "approval expired"
        if self.tool != tool:
            return False, f"approval is for {self.tool}, not {tool}"
        if self.args_digest != args_digest:
            # Approving a plan does not approve a mutated version of it.
            return False, "arguments changed since approval"
        return True, ""


@dataclass
class ToolInvocation:
    """What crosses into execution. Carries no policy and no secrets — everything it is
    allowed to do has already been decided (ADR-0003)."""

    tool: str
    args: dict[str, Any]
    verdict: PolicyVerdict
    resolved: dict[str, ResolvedPath] = field(default_factory=dict)
    cwd: Path | None = None
    dry_run: bool = False


@dataclass
class ToolOutcome:
    tool: str
    ok: bool
    result: ToolResult | None
    verdict: PolicyVerdict
    duration_ms: int
    error: ToolError | None = None


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        engine: PolicyEngine,
        audit: AuditLog,
        *,
        approvals: dict[str, Approval] | None = None,
        host: ToolHost | None = None,
    ) -> None:
        self._registry = registry
        self._engine = engine
        self._audit = audit
        self._approvals = approvals if approvals is not None else {}
        #: When set, execution crosses a process boundary (ADR-0003). Without it the
        #: handler runs in-process — acceptable only for read-only tools, and never for
        #: anything that spawns a process.
        self._host = host

    def grant(self, approval: Approval) -> None:
        self._approvals[approval.approval_id] = approval

    # ------------------------------------------------------------------ execute

    async def execute(
        self,
        tool_id: str,
        raw_args: dict[str, Any],
        *,
        provenances: frozenset[Provenance] = frozenset(),
        approval_id: str | None = None,
        cwd: Path | None = None,
        dry_run: bool = False,
    ) -> ToolOutcome:
        started = time.perf_counter()

        # 1. the tool must exist. An unknown tool is not a runtime negotiation.
        if not self._registry.has(tool_id):
            return self._fail(
                tool_id, ToolErrorKind.NOT_FOUND, f"unknown tool {tool_id!r}", started
            )
        contract = self._registry.get(tool_id)

        # 2. arguments must validate against the contract's schema.
        try:
            args = contract.args_model.model_validate(raw_args)
        except ValidationError as exc:
            return self._fail(
                tool_id,
                ToolErrorKind.INVALID_ARGS,
                "arguments did not validate",
                started,
                detail=str(exc)[:600],
            )

        # 3. resolve every path argument BEFORE the gate. The tier is a function of
        #    resolved arguments, not of the tool name.
        resolved: dict[str, ResolvedPath] = {}
        try:
            for fname in contract.path_fields:
                raw = getattr(args, fname)
                resolved[fname] = self._engine.resolve_path(str(raw), cwd=cwd)
        except PathRejected as exc:
            self._audit_denial(tool_id, raw_args, rule=f"path.{exc.reason}", reason=exc.detail)
            return self._fail(
                tool_id,
                ToolErrorKind.DENIED,
                f"path rejected: {exc.reason}",
                started,
                detail=exc.detail,
            )

        # 4. THE GATE.
        verdict = self._engine.evaluate(
            tool_id,
            capabilities=contract.capabilities,
            paths=list(resolved.values()),
            provenances=provenances,
            declared_tier=contract.risk,
        )

        args_digest = digest_args(
            {k: str(v) for k, v in {**raw_args, **{k: str(v) for k, v in resolved.items()}}.items()}
        )

        if verdict.decision is Decision.DENY:
            self._audit_denial(tool_id, raw_args, rule=verdict.rule, reason=verdict.reason)
            return self._fail(
                tool_id,
                ToolErrorKind.DENIED,
                verdict.reason or "denied by policy",
                started,
                verdict=verdict,
                detail=verdict.rule,
            )

        # 5. approval, bound to these exact arguments.
        if verdict.needs_approval:
            if approval_id is None:
                self._audit.append(
                    actor="agent",
                    tool=tool_id,
                    decision="approval_required",
                    rule=verdict.rule,
                    tier=verdict.tier.label,
                    args_digest=args_digest,
                    trace_id=trace_id_var.get(),
                )
                return self._fail(
                    tool_id,
                    ToolErrorKind.APPROVAL_REQUIRED,
                    f"{tool_id} needs approval ({verdict.decision})",
                    started,
                    verdict=verdict,
                )
            approval = self._approvals.get(approval_id)
            if approval is None:
                return self._fail(
                    tool_id,
                    ToolErrorKind.APPROVAL_INVALID,
                    "no such approval",
                    started,
                    verdict=verdict,
                )
            ok, why = approval.valid_for(tool_id, args_digest, time.time())
            if not ok:
                self._audit.append(
                    actor="agent",
                    tool=tool_id,
                    decision="approval_rejected",
                    reason=why,
                    approval_id=approval_id,
                    trace_id=trace_id_var.get(),
                )
                return self._fail(
                    tool_id, ToolErrorKind.APPROVAL_INVALID, why, started, verdict=verdict
                )
            approval.used = True  # single use

        # 6. TOCTOU re-check, immediately before execution.
        try:
            for rp in resolved.values():
                self._engine.resolver.recheck(rp)
        except PathRejected as exc:
            self._audit_denial(tool_id, raw_args, rule=f"toctou.{exc.reason}", reason=exc.detail)
            return self._fail(
                tool_id,
                ToolErrorKind.DENIED,
                "path changed after approval",
                started,
                verdict=verdict,
                detail=exc.detail,
            )

        # 7. execute. Across the process boundary when a host is configured; the
        #    in-process path exists only for read-only tools and tests.
        try:
            if self._host is not None:
                response = await self._host.call(
                    tool_id,
                    raw_args,
                    resolved={k: str(v.real) for k, v in resolved.items()},
                    timeout_s=contract.timeout_s,
                    cwd=cwd,
                    dry_run=dry_run,
                )
                if not response.ok:
                    # A timeout is NEVER retryable: the side effect may have happened.
                    return self._fail(
                        tool_id,
                        response.error_kind or ToolErrorKind.EXECUTION_FAILED,
                        response.error_message or "tool failed",
                        started,
                        verdict=verdict,
                    )
                result = contract.result_model.model_validate(response.result or {})
            else:
                result = await asyncio.wait_for(
                    contract.handler(resolved={k: v.real for k, v in resolved.items()}, args=args),
                    timeout=contract.timeout_s,
                )
        except ToolHostUnavailable as exc:
            return self._fail(
                tool_id,
                ToolErrorKind.EXECUTION_FAILED,
                f"tool host unavailable: {exc}",
                started,
                verdict=verdict,
            )
        except TimeoutError:
            return self._fail(
                tool_id,
                ToolErrorKind.TIMEOUT,
                f"{tool_id} exceeded {contract.timeout_s}s. "
                "The action may or may not have completed — it will not be retried.",
                started,
                verdict=verdict,
            )
        except asyncio.CancelledError:
            self._audit.append(
                actor="agent", tool=tool_id, decision="cancelled", trace_id=trace_id_var.get()
            )
            raise
        except Exception as exc:
            return self._fail(
                tool_id,
                ToolErrorKind.EXECUTION_FAILED,
                str(exc)[:200],
                started,
                verdict=verdict,
                detail=repr(exc)[:600],
            )

        duration = int((time.perf_counter() - started) * 1000)
        self._audit.append(
            actor="agent",
            tool=tool_id,
            decision=verdict.decision,
            rule=verdict.rule,
            tier=verdict.tier.label,
            tainted=verdict.tainted,
            args_digest=args_digest,
            outcome="ok",
            duration_ms=duration,
            trace_id=trace_id_var.get(),
            dry_run=dry_run,
        )
        return ToolOutcome(
            tool=tool_id, ok=True, result=result, verdict=verdict, duration_ms=duration
        )

    # ------------------------------------------------------------------ helpers

    def _audit_denial(self, tool_id: str, args: dict[str, Any], *, rule: str, reason: str) -> None:
        self._audit.append(
            actor="agent",
            tool=tool_id,
            decision="deny",
            rule=rule,
            reason=reason,
            args_digest=digest_args({k: str(v) for k, v in args.items()}),
            trace_id=trace_id_var.get(),
        )

    def _fail(
        self,
        tool_id: str,
        kind: str,
        message: str,
        started: float,
        *,
        verdict: PolicyVerdict | None = None,
        detail: str = "",
        retryable: bool = False,
    ) -> ToolOutcome:
        err = ToolError(kind, message, detail=detail, retryable=retryable)
        log.warning("tool.failed", tool=tool_id, kind=kind, message=message)
        return ToolOutcome(
            tool=tool_id,
            ok=False,
            result=None,
            verdict=verdict
            or PolicyVerdict(
                decision=Decision.DENY, tier=Tier.T4, base_tier=Tier.T4, rule=kind, reason=message
            ),
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=err,
        )


__all__ = [
    "Approval",
    "Capability",
    "ToolError",
    "ToolErrorKind",
    "ToolExecutor",
    "ToolInvocation",
    "ToolOutcome",
]
