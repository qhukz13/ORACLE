<#
.SYNOPSIS
    Start oracled unless one is already serving.

.DESCRIPTION
    What the autostart task runs, rather than `uv run oracled` directly. The guard is the whole
    reason it exists: ADR-0030 lets the desktop shell start a daemon it owns when it finds none,
    and the logon task fires at roughly the moment somebody opens the window. Without a guard the
    two race for port 8787, one dies with an unhandled bind error, and which one depends on
    scheduler timing — a failure that reproduces once a fortnight.

    The check is the same one the shell makes (ADR-0030) and for the same reason: ask /health and
    require ORACLE's own answer, rather than reading "something is listening" as "ORACLE is up".
    A stranger on 8787 means stay out of the way and say so.

    **This was a .cmd file for about twenty minutes.** It was rewritten after the stranger branch
    was actually tested: batch expands `%BODY%` inside a parenthesised `if`, so a response body
    containing quotes — which is every JSON body — produced `else was unexpected at this time`
    and exit 255. The guard crashed in precisely the case it was written for, and the ORACLE
    branch passed, so testing only the happy path would have shipped it. The logic here is an
    HTTP call and a string match; batch is bad at both.

.NOTES
    Written for Windows PowerShell 5.1 as well as 7, because the scheduled task must run on a
    machine where pwsh may not be installed.
#>

[CmdletBinding()]
param(
    [int]$Port = 8787
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $Root 'logs\oracled-autostart.log'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Log) | Out-Null

function Write-Line([string]$Message) {
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    # Written through .NET rather than Add-Content, and both halves of that matter on 5.1.
    # Add-Content defaults to ANSI, which turned an em dash into a replacement character; adding
    # -Encoding UTF8 then wrote a BOM on *every* append, so lines began mid-timestamp ("26-09-10").
    # UTF8Encoding($false) is UTF-8 with no BOM, appended once per line, correct on 5.1 and 7.
    [System.IO.File]::AppendAllText($Log, "$stamp  $Message`r`n", [Text.UTF8Encoding]::new($false))
}

# A short timeout on purpose: this runs at logon, and a hung probe would sit between the user and
# a working ORACLE. /health is state-free and answers before startup completes.
$body = $null
try {
    $raw = (Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 -UseBasicParsing).Content
    # 5.1 hands back a byte[] when the response carries no recognised text content-type; 7 always
    # hands back a string. Measured, not assumed: the first run of this logged the stranger's body
    # as "123 34 97 112 112 ..." because nothing converted it. ORACLE's own reply is
    # application/json and would have come back as a string on both, so the happy path hid it.
    $body = if ($raw -is [byte[]]) { [Text.Encoding]::UTF8.GetString($raw) } else { [string]$raw }
} catch {
    $body = $null
}

if ($null -ne $body) {
    if ($body -match '"status"\s*:\s*"ok"') {
        Write-Line "already serving on $Port, nothing to do"
        exit 0
    }
    # Starting anyway would only fail to bind, and doing that every minute under the task's
    # retry policy would fail noisily forever.
    Write-Line "port $Port answered but not as ORACLE — refusing to start"
    Write-Line "  it said: $($body -replace '\s+', ' ')"
    exit 1
}

Write-Line "starting oracled"

# The daemon writes its own structured logs under log_dir; duplicating them here would grow an
# unrotated file forever.
& uv run oracled
Write-Line "oracled exited with $LASTEXITCODE"
