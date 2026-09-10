"""Every `.ps1` in this repo must start with a UTF-8 BOM.

This looks like a lint rule and is not. **Windows PowerShell 5.1 reads a `.ps1` file as ANSI
unless it begins with a BOM.** PowerShell 7 decodes UTF-8 either way and reports the same file as
parsing clean.

The mechanism, measured rather than assumed. An em dash is `E2 80 94` in UTF-8; read as cp1252
that is `â€"`, and the third byte is a quote character. So:

- non-ASCII in a **comment** is survivable — the comment renders as mojibake and the script runs;
- non-ASCII in a **string literal** is fatal, because the smuggled quote terminates the string
  early and the parser reports *"the string is missing the terminator"* at a column that has
  nothing to do with the real problem.

Both were run against `powershell.exe` to check which one actually bites, because the difference
decides whether this test is pedantry or a real guard. It is the second one, and it is why the
rule is "every `.ps1`" rather than "every `.ps1` with a string in it" — a comment's em dash today
is a string's em dash after one edit.

That asymmetry is what makes it worth a test rather than a convention:

- `scripts/run_oracled.ps1` is launched by the autostart task through `powershell.exe`, which is
  5.1 — chosen deliberately, because a machine may not have pwsh installed.
- It is launched **at logon**, where nobody is watching, into a log nobody reads until something
  is already wrong.
- The failure is invisible on the machine of anyone who checks with pwsh, which is everyone
  developing this.

It was a real failure, not a hypothetical one: the first version of that script was written
without a BOM, passed a PowerShell 7 parse check, and died under 5.1 on the em dashes in its own
comments. An editor that strips BOMs would reintroduce it silently, and the only symptom would be
that ORACLE stops starting itself one morning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UTF8_BOM = b"\xef\xbb\xbf"


def powershell_scripts() -> list[Path]:
    return sorted(
        p for p in ROOT.rglob("*.ps1") if ".venv" not in p.parts and "node_modules" not in p.parts
    )


def test_there_is_at_least_one_to_check() -> None:
    """A glob that silently matches nothing is a test that passes by finding no work."""
    assert powershell_scripts(), "no .ps1 files found — has the autostart task moved?"


@pytest.mark.parametrize("script", powershell_scripts(), ids=lambda p: p.name)
def test_a_powershell_script_starts_with_a_utf8_bom(script: Path) -> None:
    head = script.read_bytes()[:3]
    assert head == UTF8_BOM, (
        f"{script.relative_to(ROOT)} has no UTF-8 BOM. Windows PowerShell 5.1 will read it as "
        f"ANSI, and any non-ASCII character in it — an em dash in a comment is enough — becomes "
        f"a parse error at logon, on a machine where nobody is looking."
    )


@pytest.mark.parametrize("script", powershell_scripts(), ids=lambda p: p.name)
def test_the_bom_is_not_doubled(script: Path) -> None:
    """Re-encoding a file that already had one is the obvious way to 'fix' this and it produces
    a second BOM inside the text, which 5.1 renders as three garbage characters before the first
    statement — a different parse error with the same cause."""
    body = script.read_bytes()[3:]
    assert not body.startswith(UTF8_BOM), f"{script.relative_to(ROOT)} has two BOMs"
