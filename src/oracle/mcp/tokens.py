"""Delegation capabilities: what a delegated agent is allowed to call back with.

Not a bearer token for the API. A token here is a **capability for one delegation**,
and it names its own limits: which tools, which directory, until when. The daemon
re-derives those limits on every call rather than trusting anything the bridge says,
which is what makes the bridge a dumb pipe (INTEGRATIONS.md §4).

Three properties, each because of a specific way this goes wrong:

* **Unforgeable.** HMAC-SHA256 over the claims with a key minted at daemon start and
  never written to disk. A delegate that could mint its own token would be a delegate
  with the owner's whole tool surface.
* **Expiring, and revoked at the end.** A token that outlives its delegation is a key
  left in the door: the run is over, nobody is watching the event stream, and the
  worktree may already be discarded. `revoke()` is called on every exit path.
* **Strictly less than the owner has.** The tool allowlist is a subset of the registry
  chosen in this module, the root is the delegation's own worktree, and the gate still
  runs afterwards. The token can only ever narrow.
"""

from __future__ import annotations

import base64
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from oracle.logsink import get_logger

log = get_logger(__name__)

#: What a delegate may call back with (P6-T3 requirement 4). Read and verify, nothing
#: that writes: the delegate edits inside its own disposable worktree with its own
#: tools, and ORACLE diffs the result anyway. Every entry is T0 or T1 by policy; the
#: daemon refuses anything higher over this path regardless of what is listed here.
DEFAULT_TOOLS: tuple[str, ...] = (
    "fs.read",
    "fs.list",
    "git.status",
    "git.diff",
    "know.search",
    "dev.run_tests",
)

#: A delegation that has not finished in this long has bigger problems than its token.
DEFAULT_TTL_S = 3600.0


class TokenError(Exception):
    """Verification failed. The message is for the log, never for the caller: a
    delegate learns "refused", not which check refused it."""


@dataclass(frozen=True)
class Capability:
    """The claims. Everything the daemon needs to decide, and nothing else."""

    task_id: str
    root: str
    tools: tuple[str, ...]
    expires_at: float

    def allows(self, tool: str) -> bool:
        return tool in self.tools

    def contains(self, path: Path) -> bool:
        """Is this path inside the delegation's worktree?

        Resolved on both sides before comparing — `..`, a symlink, or an 8.3 alias
        must not be able to walk out of the root (the OQ-04 rule: compare the real
        path, never the spelling).
        """
        try:
            return path.resolve().is_relative_to(Path(self.root).resolve())
        except OSError:  # pragma: no cover - unresolvable path is not inside anything
            return False


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TokenStore:
    """Mints and verifies delegation capabilities. One per daemon."""

    def __init__(self, ttl_s: float = DEFAULT_TTL_S) -> None:
        #: Process-lifetime key. Never persisted: a restart invalidating every
        #: outstanding token is correct — the delegations died with the daemon.
        self._key = secrets.token_bytes(32)
        self._ttl = ttl_s
        #: Live task ids. Revocation is a set membership test rather than a flag on the
        #: token, so ending a delegation kills its token instantly and irreversibly.
        self._live: set[str] = set()

    def mint(
        self,
        task_id: str,
        root: Path,
        *,
        tools: tuple[str, ...] = DEFAULT_TOOLS,
        now: float | None = None,
    ) -> str:
        cap = Capability(
            task_id=task_id,
            root=str(root.resolve()),
            tools=tools,
            expires_at=(now or time.time()) + self._ttl,
        )
        payload = json.dumps(
            {
                "task_id": cap.task_id,
                "root": cap.root,
                "tools": list(cap.tools),
                "expires_at": cap.expires_at,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        mac = hmac.new(self._key, payload, sha256).digest()
        self._live.add(task_id)
        log.info("mcp.token_minted", task_id=task_id, tools=len(tools))
        return f"{_b64(payload)}.{_b64(mac)}"

    def verify(self, token: str, *, now: float | None = None) -> Capability:
        """Claims, or `TokenError`. Order matters: signature first, so an unsigned
        payload never reaches `json.loads`."""
        try:
            body, signature = token.split(".", 1)
            payload, mac = _unb64(body), _unb64(signature)
        except (ValueError, TypeError) as exc:
            raise TokenError("malformed token") from exc

        expected = hmac.new(self._key, payload, sha256).digest()
        if not hmac.compare_digest(mac, expected):
            raise TokenError("bad signature")

        try:
            claims = json.loads(payload)
            cap = Capability(
                task_id=str(claims["task_id"]),
                root=str(claims["root"]),
                tools=tuple(str(t) for t in claims["tools"]),
                expires_at=float(claims["expires_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise TokenError("unreadable claims") from exc

        if (now or time.time()) > cap.expires_at:
            raise TokenError("expired")
        if cap.task_id not in self._live:
            # Revoked, or minted by a daemon that has since exited.
            raise TokenError("no such live delegation")
        return cap

    def revoke(self, task_id: str) -> None:
        """Called on every exit path of a delegation, HALT included."""
        if task_id in self._live:
            self._live.discard(task_id)
            log.info("mcp.token_revoked", task_id=task_id)
