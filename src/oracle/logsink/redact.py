"""Secret redaction.

One sink, no bypass (docs/SECURITY.md#7-secrets-and-egress). Every log record, event
payload and outbound render passes through `redact`. There is deliberately no
`logger.raw()` escape hatch.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Final

# High-precision patterns first. Each carries the label used in the placeholder so a
# redaction is visible as an occurrence without exposing the value.
_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    ),
    ("conn_string", re.compile(r"\b[a-z\+]{2,16}://[^\s:@/]+:[^\s:@/]+@[^\s/]+")),
    # key=value / "key": "value" where the key name looks secret-ish.
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(api[_\-]?key|secret|token|password|passwd|pwd|access[_\-]?key|auth)"
            r"\b\s*[:=]\s*[\"']?([^\s\"',;]{8,})[\"']?"
        ),
    ),
]

_PLACEHOLDER = "[REDACTED:{label}]"

# Entropy heuristic: only applied to long opaque runs, and only when the surrounding
# text hints at a credential. Tuned to avoid eating hashes in normal log lines.
_ENTROPY_CANDIDATE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_ENTROPY_THRESHOLD = 4.0


def _shannon(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def redact_text(text: str, *, entropy: bool = False) -> tuple[str, list[str]]:
    """Return redacted text and the labels that fired."""
    fired: list[str] = []
    out = text
    for label, pat in _PATTERNS:
        if label == "assigned_secret":

            def _sub(m: re.Match[str], _label: str = label) -> str:
                fired.append(_label)
                return f"{m.group(1)}={_PLACEHOLDER.format(label=_label)}"

            new = pat.sub(_sub, out)
        else:

            def _sub2(_m: re.Match[str], _label: str = label) -> str:
                fired.append(_label)
                return _PLACEHOLDER.format(label=_label)

            new = pat.sub(_sub2, out)
        out = new

    if entropy:

        def _ent(m: re.Match[str]) -> str:
            tok = m.group(0)
            if _shannon(tok) >= _ENTROPY_THRESHOLD:
                fired.append("high_entropy")
                return _PLACEHOLDER.format(label="high_entropy")
            return tok

        out = _ENTROPY_CANDIDATE.sub(_ent, out)

    return out, fired


# Field names whose *value* is always redacted regardless of shape.
_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "authorization",
        "auth",
        "cookie",
        "set-cookie",
        "private_key",
        "device_token",
        "pairing_code",
    }
)


def redact(value: Any, *, entropy: bool = False) -> Any:
    """Recursively redact a JSON-ish structure. Used by the log processor and the
    event serializer alike."""
    if isinstance(value, str):
        return redact_text(value, entropy=entropy)[0]
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = _PLACEHOLDER.format(label="field")
            else:
                out[k] = redact(v, entropy=entropy)
        return out
    if isinstance(value, list):
        return [redact(v, entropy=entropy) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v, entropy=entropy) for v in value)
    return value
