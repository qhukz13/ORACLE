"""The application catalogue.

`app.launch` takes an **alias**, never a path. An executable path chosen by a model is
an arbitrary-execution primitive, and no amount of confirmation makes one safe to
accept — so the model's vocabulary here is `editor`, `explorer`, `browser`, and the
mapping to an executable is a file a human wrote.

Structurally this is the program allowlist's sibling, and it is deliberately a separate
file and a separate type, because the two are not the same kind of thing:

| | `programs` | `apps` |
|---|---|---|
| lifetime | seconds; we wait for it | until the user closes it |
| output | captured and parsed | none; it has a window |
| containment | inside the toolhost's Job Object | **must escape it** |

That last row is the load-bearing difference and the reason this type exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from oracle.logsink import get_logger
from oracle.policy.model import Tier

log = get_logger(__name__)


class AppRejected(Exception):
    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(detail)
        self.rule = rule
        self.detail = detail


@dataclass(frozen=True)
class AppEntry:
    alias: str
    path: Path
    tier: Tier = Tier.T2
    #: May be given one extra argument: a path, canonicalised and scope-checked first.
    accepts_path: bool = False
    description: str = ""
    args: tuple[str, ...] = ()
    installed: bool = True


@dataclass(frozen=True)
class AppCatalogue:
    apps: dict[str, AppEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> AppCatalogue:
        """Read the catalogue, or return an empty one.

        Never raises. A missing or broken apps file means ORACLE cannot open anything,
        which is the correct failure — the same reasoning as policy's read-only
        lockdown, one file down.
        """
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("apps.missing", path=str(path), effect="no application can be opened")
            return cls()
        except Exception as exc:
            log.error("apps.invalid", path=str(path), error=str(exc), effect="catalogue empty")
            return cls()
        if not isinstance(raw, dict):
            log.error("apps.invalid", path=str(path), error="not a mapping")
            return cls()
        return cls.parse(raw.get("apps"))

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> AppCatalogue:
        apps: dict[str, AppEntry] = {}
        for alias, body in (raw or {}).items():
            body = body or {}
            exe = Path(str(body.get("path", "")))
            try:
                tier = Tier[str(body.get("tier", "T2")).upper()]
            except KeyError:
                log.error("apps.bad_tier", alias=alias, tier=body.get("tier"))
                continue
            entry = AppEntry(
                alias=str(alias),
                path=exe,
                tier=tier,
                accepts_path=bool(body.get("accepts_path", False)),
                description=str(body.get("description", "")),
                args=tuple(str(a) for a in body.get("args") or []),
                installed=exe.exists(),
            )
            apps[entry.alias] = entry
            log.info("apps.pinned", alias=entry.alias, path=str(exe), installed=entry.installed)
        return cls(apps=apps)

    @property
    def aliases(self) -> list[str]:
        return sorted(self.apps)

    def resolve(self, alias: str) -> AppEntry:
        entry = self.apps.get(alias)
        if entry is None:
            raise AppRejected(
                "apps.catalogue",
                f"{alias!r} is not an application ORACLE knows "
                f"(known: {', '.join(self.aliases) or 'none'})",
            )
        if not entry.installed:
            raise AppRejected(
                f"apps.{alias}.path",
                f"{alias} is in the catalogue but {entry.path} does not exist on this machine",
            )
        return entry


EMPTY_CATALOGUE = AppCatalogue()

__all__ = ["EMPTY_CATALOGUE", "AppCatalogue", "AppEntry", "AppRejected"]
