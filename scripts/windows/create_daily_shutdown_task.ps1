$ErrorActionPreference = "Stop"

param(
    [string]$ShutdownTime = "23:00"
)

function Assert-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Please run this script as Administrator."
    }
}

Assert-Administrator

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$shutdownScript = Join-Path $repoRoot "scripts\windows\shutdown_with_miniqmt_stop.bat"
$taskUser = "${env:COMPUTERNAME}\${env:USERNAME}"
$taskName = "Daily shutdown with miniQMT stop"

if (-not (Test-Path $shutdownScript)) {
    throw "Shutdown script not found: $shutdownScript"
}

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$shutdownScript`""

$trigger = New-ScheduledTaskTrigger -Daily -At $ShutdownTime
$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Description "Stop miniQMT gracefully and then shut down Windows daily." `
    -Force | Out-Null

Write-Host "Scheduled task created: $taskName" -ForegroundColor Green
Write-Host "Daily shutdown time: $ShutdownTime" -ForegroundColor Green
