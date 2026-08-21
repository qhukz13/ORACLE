"""Hash-chained audit log.

Separate from application logs, and unlike them it is a *security artifact*: append
only, never rotated away, and written **synchronously with fsync**. A security record
that might not have been written is worthless, and the volume is low enough that the
cost is irrelevant (docs/LOGGING.md, docs/SECURITY.md#9-audit-log).

Each record's `hash` covers `prev` plus the record body, so removing or editing any
line breaks the chain and `verify()` reports the first bad sequence number.

Arguments are stored as a **digest plus a redacted preview**, never raw: the audit log
must not become the place secrets end up.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oracle.logsink.redact import redact

GENESIS = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    """Stable serialisation. Key order must not vary or the chain breaks on rewrite."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_args(args: dict[str, Any]) -> str:
    """Bind an approval to exact arguments (docs/SECURITY.md#5)."""
    return "sha256:" + hashlib.sha256(_canonical(args).encode("utf-8")).hexdigest()


@dataclass
class ChainBreak:
    seq: int
    expected: str
    found: str
    detail: str


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq, self._prev = self._tail()

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        last: dict[str, Any] | None = None
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = json.loads(line)
        if last is None:
            return 0, GENESIS
        return int(last["seq"]), str(last["hash"])

    @property
    def seq(self) -> int:
        return self._seq

    def append(self, **fields: Any) -> dict[str, Any]:
        self._seq += 1
        body = {
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "prev": self._prev,
            **redact(fields),
        }
        body["hash"] = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
        line = json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n"

        # Synchronous + fsync. This is the one sink that must not be lossy.
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

        self._prev = str(body["hash"])
        return body

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def verify(self) -> list[ChainBreak]:
        """Return every break found. Empty list means the chain is intact."""
        breaks: list[ChainBreak] = []
        prev = GENESIS
        expected_seq = 1

        for rec in self.records():
            seq = int(rec.get("seq", -1))
            if seq != expected_seq:
                breaks.append(
                    ChainBreak(
                        seq, str(expected_seq), str(seq), "sequence gap — a record was removed"
                    )
                )
                expected_seq = seq
            if rec.get("prev") != prev:
                breaks.append(
                    ChainBreak(seq, prev, str(rec.get("prev")), "prev hash does not match")
                )
            body = {k: v for k, v in rec.items() if k != "hash"}
            recomputed = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
            if recomputed != rec.get("hash"):
                breaks.append(
                    ChainBreak(seq, recomputed, str(rec.get("hash")), "record body was edited")
                )
            prev = str(rec.get("hash"))
            expected_seq += 1

        return breaks
