<#
.SYNOPSIS
    Register (or remove) the scheduled task that starts ORACLE at logon.

.DESCRIPTION
    ROADMAP P13, ADR-0025: "ORACLE is running before I look at it." This is the mechanism.

    **At logon, not at boot, and that is a decision rather than a shortcut.** A boot-time service
    runs as SYSTEM, before any user profile exists — with no user PATH, no `uv`, no Ollama, no
    access to C:\Projects under the owner's credentials, and no way to reach the desktop session.
    ORACLE would come up unable to do most of what it exists for, which is not "degraded-capable",
    it is broken with a green light. Logon costs the seconds between power-on and sign-in, and
    everything works.

    It deliberately does NOT create a Windows service. A service needs a wrapper to supervise a
    Python process, would run in session 0, and buys nothing here that a task with RestartCount
    does not — while adding a component that can fail invisibly, which ADR-0025 names as its own
    main risk.

    The task runs scripts\run_oracled.ps1 rather than `uv run oracled`, because the shell may also
    start a daemon it owns (ADR-0030) at almost the same moment. The guard in that script is what
    stops the two racing for port 8787.

.PARAMETER Uninstall
    Remove the task. Does not stop a daemon that is already running.

.PARAMETER StartNow
    Run the task once immediately after registering it, instead of waiting for the next logon.

.EXAMPLE
    pwsh -File scripts\install_oracled_task.ps1
    pwsh -File scripts\install_oracled_task.ps1 -StartNow
    pwsh -File scripts\install_oracled_task.ps1 -Uninstall

.NOTES
    This changes what your machine does at logon. It is not run by any agent or by the gate —
    installing it is the owner's decision, and the only way it happens is somebody running this.
#>

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$StartNow
)

$ErrorActionPreference = 'Stop'

$TaskName = 'ORACLE-daemon'
$Root = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $Root 'scripts\run_oracled.ps1'
# Windows PowerShell, not pwsh: the task has to start on a machine where PowerShell 7 was
# never installed, and 5.1 is the one that is always there. The runner is written for both.
$Host51 = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-Host "No task named '$TaskName' — nothing to remove."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'. A daemon already running is left alone — stop it yourself if you want it gone."
    exit 0
}

if (-not (Test-Path $Runner)) {
    throw "Runner not found at $Runner. Run this from a checkout, not a copied script."
}

$action = New-ScheduledTaskAction -Execute $Host51 `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Runner`"" `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Interactive, under the owner's own account: ORACLE reads their projects, their Obsidian vault
# and their agent CLIs, all of which are theirs and not SYSTEM's. `-RunLevel Limited` is
# deliberate — nothing ORACLE does at boot needs elevation, and a resident daemon running as
# administrator is a much larger blast radius for the same behaviour.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

# ExecutionTimeLimit of zero means "no limit", and it is required rather than tidy: this task's
# process is meant to run until the machine shuts down. The default 72 hours would kill the
# daemon mid-week, and the OQ-18 eval already lost two runs to exactly this kind of invisible
# scheduler ceiling.

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered '$TaskName':"
Write-Host "  runs      $Runner"
Write-Host "  as        $env:USERDOMAIN\$env:USERNAME (interactive, not elevated)"
Write-Host "  when      at logon, restarting up to 3 times a minute apart"
Write-Host "  no execution time limit — it is meant to outlive the window (ADR-0025)"
Write-Host ""
Write-Host "Remove it with: pwsh -File scripts\install_oracled_task.ps1 -Uninstall"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host ""
    Write-Host "Started. scripts\run_oracled.ps1 exits quietly if a daemon is already serving."
}
