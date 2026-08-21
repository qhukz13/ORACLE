"""Runtime configuration.

Data lives on D: by default — C: has under 40 GB free. See docs/TECH_STACK.md#data-locations.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORACLE_", env_file=".env", extra="ignore")

    data_dir: Path = Path("D:/ORACLE/data")
    log_dir: Path = Path("D:/ORACLE/logs")

    host: str = "127.0.0.1"
    port: int = 8787

    # Event backlog kept for WS resume. Older gaps force a session.resync.
    resume_window: int = 10_000

    # Bounded per-connection queue. Overflow closes the socket; the client
    # reconnects with since_seq rather than silently losing critical events.
    ws_queue_size: int = 1_000

    log_level: str = "info"

    #: Router model. Chosen by measurement, not taste: qwen3.5:0.8b is the largest
    #: Qwen3.5 that runs 100% on this 4 GB Pascal card (OQ-01).
    #: Tests and CI set this False so the suite is hermetic — no test may require
    #: Ollama to be running (docs/TESTING.md).
    llm_enabled: bool = True
    router_model: str = "qwen3.5:0.8b"
    router_ctx: int = 16384

    projects_root: Path = Path("C:/Projects")

    #: Policy lives in the repo, not in the data dir: it is reviewed and versioned like
    #: code, and must be edited by a human (docs/SECURITY.md#2).
    policy_path: Path = Path("config/policy.yaml")

    #: Start the tool host at boot so the first tool call does not pay ~1.2 s of
    #: process startup. Tests disable it to stay hermetic and fast.
    prewarm_toolhost: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "oracle.db"

    @property
    def trash_dir(self) -> Path:
        # Previous versions live here. `fs.delete` is a move into this directory, never
        # an unlink (docs/TOOLS.md).
        return self.data_dir / "trash"

    @property
    def undo_journal(self) -> Path:
        return self.data_dir / "undo.jsonl"

    @property
    def blobs_dir(self) -> Path:
        # Full command output. The model gets structured fields; the human gets the
        # whole log, linked from the result (docs/TOOLS.md rule 4).
        return self.data_dir / "blobs"

    @property
    def apps_path(self) -> Path:
        # App aliases, like policy: versioned, human-edited, never written by a tool.
        return Path("config/apps.yaml")

    @property
    def audit_path(self) -> Path:
        # Append-only, hash-chained, never rotated away (docs/LOGGING.md).
        return self.log_dir / "audit" / "audit.jsonl"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(s: Settings) -> None:
    """Test hook. Production code never calls this."""
    global _settings
    _settings = s
