$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$deploymentConfigPath = Join-Path $repoRoot "config\miniqmt_deploy.local.json"
$taskNames = @(
    "Start miniQMT at logon",
    "Daily shutdown with miniQMT stop"
)

Write-Host "=== Deployment Config ===" -ForegroundColor Cyan
if (Test-Path $deploymentConfigPath) {
    try {
        $config = Get-Content -Path $deploymentConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host ("Broker: {0} ({1})" -f $config.displayName, $config.broker)
        Write-Host ("ScriptPath: {0}" -f $config.scriptPath)
        Write-Host ("ClientPath: {0}" -f $config.clientPath)
        Write-Host ("UpdatedAt: {0}" -f $config.updatedAt)
    } catch {
        Write-Host ("Config parse failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
    }
} else {
    Write-Host ("Config not found: {0}" -f $deploymentConfigPath) -ForegroundColor Yellow
}

foreach ($taskName in $taskNames) {
    Write-Host ""
    Write-Host "=== $taskName ===" -ForegroundColor Cyan

    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Task not found" -ForegroundColor Yellow
        continue
    }

    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName

    Write-Host ("State: {0}" -f $task.State)
    Write-Host ("LastRunTime: {0}" -f $taskInfo.LastRunTime)
    Write-Host ("NextRunTime: {0}" -f $taskInfo.NextRunTime)

    if ($task.Triggers) {
        foreach ($trigger in $task.Triggers) {
            $triggerType = $trigger.CimClass.CimClassName
            if ($trigger.StartBoundary) {
                $triggerStart = $trigger.StartBoundary
            } else {
                $triggerStart = "<none>"
            }
            Write-Host ("Trigger: {0} | StartBoundary={1}" -f $triggerType, $triggerStart)
        }
    } else {
        Write-Host "Trigger: <none>"
    }

    if ($task.Actions) {
        foreach ($action in $task.Actions) {
            Write-Host ("Execute: {0}" -f $action.Execute)
            Write-Host ("Arguments: {0}" -f $action.Arguments)
        }
    } else {
        Write-Host "Action: <none>"
    }
}
