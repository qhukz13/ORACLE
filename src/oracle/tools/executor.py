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
from oracle.policy.programs import ProgramRejected
from oracle.toolhost import ToolHost, ToolHostUnavailable
from oracle.tools.contract import ToolContext, ToolContract, ToolRegistry, ToolResult
from oracle.tools.undo import UndoJournal, UndoKind, load_undo_plan

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
    #: Set when the mutation was journalled, so the caller can offer "undo that".
    undo_id: str | None = None


def _string_values(args: Any) -> list[str]:
    """Every model-supplied string that could end up inside an argv.

    Used to apply the program allowlist's argument-shape rules to intent-shaped tools,
    whose final argv is assembled on the far side of the boundary. The parent cannot
    see `git commit -m <message>`, but it can see `<message>` — which is the part the
    model chose and therefore the only part worth inspecting.
    """
    out: list[str] = []
    for name in type(args).model_fields:
        value = getattr(args, name, None)
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            out.extend(v for v in value if isinstance(v, str))
    return out


def _digest(raw_args: dict[str, Any], resolved: dict[str, ResolvedPath]) -> str:
    r"""The value an approval binds to.

    Computed from the RESOLVED arguments, not the raw ones: approving `..\..\a.txt` and
    approving the absolute path it resolves to must be the same decision, and two
    spellings of one path must not produce two different approvals.
    """
    merged: dict[str, Any] = {**raw_args, **{k: str(v.real) for k, v in resolved.items()}}
    return digest_args({k: str(v) for k, v in merged.items()})


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        engine: PolicyEngine,
        audit: AuditLog,
        *,
        approvals: dict[str, Approval] | None = None,
        host: ToolHost | None = None,
        undo: UndoJournal | None = None,
    ) -> None:
        self._registry = registry
        self._engine = engine
        self._audit = audit
        self._approvals = approvals if approvals is not None else {}
        #: When set, execution crosses a process boundary (ADR-0003). Without it the
        #: handler runs IN-PROCESS, which is acceptable only for tests and as a degraded
        #: fallback: handlers do blocking file I/O (correct inside the single-invocation
        #: child, a stalled event loop here) and nothing that spawns a process may ever
        #: take this path.
        self._host = host
        #: Records how to reverse each T1 mutation. Lives on the PARENT side: the child
        #: performs the backup and reports it, but must not hold the record of what it
        #: did (ADR-0003).
        self._undo = undo

    def grant(self, approval: Approval) -> None:
        self._approvals[approval.approval_id] = approval

    # ------------------------------------------------------------------ programs

    def _pin_programs(
        self, contract: ToolContract, args: Any
    ) -> tuple[dict[str, Path], tuple[str, Tier] | None]:
        """Resolve every program this call may spawn, on the parent side.

        Returns the pinned paths and, for the gated escape hatch, the tier floor its
        argv earned. Raises `ProgramRejected` — which the caller turns into a denial
        naming the rule, because "refused" without "by what" is useless.
        """
        pinned: dict[str, Path] = {}
        floor: tuple[str, Tier] | None = None

        for name in sorted(contract.programs):
            pinned[name] = self._engine.resolve_program(name)
            # The argv is built inside the child, but the VALUES in it came from the
            # model, so the shape rules still apply to them.
            self._engine.check_fixed_program(name, _string_values(args))

        if contract.program_field is not None:
            chosen = str(getattr(args, contract.program_field))
            argv = [str(a) for a in (getattr(args, "args", None) or [])]
            floor = (chosen, self._engine.check_program(chosen, argv))
            pinned[chosen] = self._engine.resolve_program(chosen)

        return pinned, floor

    def preview(
        self, tool_id: str, raw_args: dict[str, Any], *, cwd: Path | None = None
    ) -> tuple[PolicyVerdict, str]:
        """What WOULD happen, and the digest an approval must bind to.

        This is what the Confirmation Center calls to render a card: it needs the
        verdict to know whether to ask, and the digest so the approval it issues cannot
        be reused for different arguments. It performs nothing.
        """
        contract = self._registry.get(tool_id)
        args = contract.args_model.model_validate(raw_args)
        resolved = {
            f: self._engine.resolve_path(str(getattr(args, f)), cwd=cwd)
            for f in contract.path_fields
        }
        _, floor = self._pin_programs(contract, args)
        verdict = self._engine.evaluate(
            tool_id,
            capabilities=contract.capabilities,
            paths=list(resolved.values()),
            declared_tier=contract.risk,
            program_floor=floor,
        )
        return verdict, _digest(raw_args, resolved)

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

        # 3b. pin every program this call may spawn, on THIS side of the boundary. A
        #     program the allowlist does not name is refused here, before the gate ever
        #     sees a tier.
        try:
            pinned, program_floor = self._pin_programs(contract, args)
        except ProgramRejected as exc:
            self._audit_denial(tool_id, raw_args, rule=exc.rule, reason=exc.detail)
            return self._fail(
                tool_id,
                ToolErrorKind.DENIED,
                f"{exc.detail} (rule: {exc.rule})",
                started,
                detail=exc.rule,
            )

        # 4. THE GATE.
        verdict = self._engine.evaluate(
            tool_id,
            capabilities=contract.capabilities,
            paths=list(resolved.values()),
            provenances=provenances,
            declared_tier=contract.risk,
            program_floor=program_floor,
        )

        args_digest = _digest(raw_args, resolved)

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
                    programs={k: str(v) for k, v in pinned.items()},
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
                ctx = ToolContext(
                    resolved={k: v.real for k, v in resolved.items()},
                    programs=pinned,
                    cwd=cwd,
                    dry_run=dry_run,
                )
                result = await asyncio.wait_for(
                    contract.handler(ctx=ctx, args=args), timeout=contract.timeout_s
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

        # Journal the undo BEFORE reporting success. If we crashed between the mutation
        # and the record, the user would have a changed file and no way back.
        undo_id: str | None = None
        if self._undo is not None:
            plan = load_undo_plan(result)
            if plan is not None and plan.kind is not UndoKind.NONE:
                undo_id = self._undo.record(tool_id, plan, trace_id=trace_id_var.get()).id

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
            undo_id=undo_id,
        )
        return ToolOutcome(
            tool=tool_id,
            ok=True,
            result=result,
            verdict=verdict,
            duration_ms=duration,
            undo_id=undo_id,
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
