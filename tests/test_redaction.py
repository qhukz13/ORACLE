"""Redaction is the sink every record crosses. If it leaks, everything downstream —
logs, events, prompts, handoff packets — leaks (docs/SECURITY.md#7-secrets-and-egress).
"""

from __future__ import annotations

import pytest

from oracle.logsink.redact import redact, redact_text

LEAKS = [
    ("sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGG", "anthropic_key"),
    ("ghp_AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH1234", "github_token"),
    ("AKIAIOSFODNN7EXAMPLE", "aws_key_id"),
    ("xoxb-1234567890-abcdefghijklmno", "slack_token"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g", "jwt"),
    ("postgres://admin:hunter2@db.internal:5432/app", "conn_string"),
]


@pytest.mark.parametrize("secret,label", LEAKS)
def test_known_secret_shapes_are_redacted(secret: str, label: str) -> None:
    out, fired = redact_text(f"connecting with {secret} now")
    assert secret not in out
    assert label in fired


def test_assigned_secret_keeps_the_key_but_kills_the_value() -> None:
    out, fired = redact_text('api_key = "s3cr3t-value-here-1234"')
    assert "s3cr3t-value-here-1234" not in out
    assert "api_key" in out  # the key name is diagnostic, the value is not
    assert "assigned_secret" in fired


def test_sensitive_field_names_are_redacted_by_key() -> None:
    out = redact({"user": "qhukz", "password": "anything at all", "nested": {"token": "abc123"}})
    assert out["user"] == "qhukz"
    assert "anything at all" not in str(out)
    assert "abc123" not in str(out)


def test_private_key_block() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK\n-----END RSA PRIVATE KEY-----"
    out, fired = redact_text(pem)
    assert "MIIEpAIBAAK" not in out
    assert "private_key" in fired


def test_recurses_through_lists_and_dicts() -> None:
    out = redact({"items": [{"note": "key sk-ant-api03-ZZZZYYYYXXXXWWWWVVVVUUUU"}]})
    assert "sk-ant" not in str(out)


def test_ordinary_text_is_untouched() -> None:
    text = "Ran 41 tests in 2.1s on branch fix/auth, commit a3f21c9."
    out, fired = redact_text(text)
    assert out == text
    assert fired == []


def test_entropy_heuristic_is_opt_in() -> None:
    """Off by default: a git SHA or content hash in a normal log line is not a secret,
    and eating them would make logs useless."""
    blob = "Xk92mQp4Lz7RtYb1Nv8WcJ3Hs5Gd0Fa6Ue2Ir4Ol9Pz"
    assert blob in redact_text(f"value {blob}")[0]
    assert blob not in redact_text(f"value {blob}", entropy=True)[0]
