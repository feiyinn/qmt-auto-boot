$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$deploymentConfigPath = Join-Path $repoRoot "config\miniqmt_deploy.local.json"

function Get-DeploymentConfig {
    param(
        [string]$ConfigPath
    )

    if (-not (Test-Path $ConfigPath)) {
        throw "Deployment config not found: $ConfigPath. Please run scripts\windows\create_start_task.ps1 first."
    }

    $raw = Get-Content -Path $ConfigPath -Raw -Encoding UTF8
    $config = $raw | ConvertFrom-Json

    if (-not $config.broker) {
        throw "Deployment config is missing 'broker': $ConfigPath"
    }

    return $config
}

function Get-StartupScriptPath {
    param(
        [string]$RepoRoot,
        [string]$Broker
    )

    switch ($Broker.ToLowerInvariant()) {
        "gz" { return Join-Path $RepoRoot "src\utils\gz_mini_start.py" }
        "zj" { return Join-Path $RepoRoot "src\utils\zj_mini_start.py" }
        default { throw "Unsupported broker in deployment config: $Broker" }
    }
}

$deploymentConfig = Get-DeploymentConfig -ConfigPath $deploymentConfigPath
$scriptPath = Get-StartupScriptPath -RepoRoot $repoRoot -Broker $deploymentConfig.broker
if ($deploymentConfig.displayName) {
    $displayName = $deploymentConfig.displayName
} else {
    $displayName = $deploymentConfig.broker
}

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found: $pythonExe"
}

if (-not (Test-Path $scriptPath)) {
    throw "Stop script not found: $scriptPath"
}

Set-Location $repoRoot
Write-Host ("Selected broker: {0}" -f $displayName) -ForegroundColor Cyan
& $pythonExe $scriptPath stop
