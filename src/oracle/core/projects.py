"""Project registry.

The classifier may only resolve names that exist here (docs/AGENT_RUNTIME.md step 3):
a hallucinated project resolves to nothing and triggers a clarification, rather than
becoming a filesystem path.
"""

from __future__ import annotations

from pathlib import Path

_SKIP = {".git", "node_modules", "target", "dist", "build", "__pycache__", ".venv"}


def discover_projects(root: Path) -> list[str]:
    """Top-level directories under `root`. Deliberately shallow and boring."""
    if not root.exists():
        return []
    names = [
        p.name
        for p in sorted(root.iterdir())
        if p.is_dir() and not p.name.startswith(".") and p.name not in _SKIP
    ]
    return names
