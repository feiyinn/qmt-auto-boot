$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$deploymentConfigPath = Join-Path $repoRoot "config\miniqmt_deploy.local.json"
$mainScriptPath = Join-Path $repoRoot "main.py"

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

function Get-MainAppShellPath {
    $pwshCommand = Get-Command "pwsh.exe" -ErrorAction SilentlyContinue
    if ($pwshCommand -and $pwshCommand.Source) {
        return $pwshCommand.Source
    }

    return Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
}

function Test-MainAppRunning {
    param(
        [string]$MainScriptPath
    )

    $escapedMainScriptPath = [Regex]::Escape($MainScriptPath)
    $pythonProcesses = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue

    foreach ($process in ($pythonProcesses | Where-Object { $_ })) {
        $commandLine = $process.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            continue
        }

        if ($commandLine -match $escapedMainScriptPath -or $commandLine -match '(?i)(^|[\s"''])main\.py($|[\s"''])') {
            return $true
        }
    }

    return $false
}

function Start-MainApp {
    param(
        [string]$PythonExe,
        [string]$RepoRoot,
        [string]$MainScriptPath
    )

    if (-not (Test-Path $MainScriptPath)) {
        throw "Main application script not found: $MainScriptPath"
    }

    if (Test-MainAppRunning -MainScriptPath $MainScriptPath) {
        Write-Host "main.py 已在运行，跳过重复启动。" -ForegroundColor Yellow
        return
    }

    $mainAppShellExe = Get-MainAppShellPath
    $escapedRepoRoot = $RepoRoot.Replace("'", "''")
    $escapedPythonExe = $PythonExe.Replace("'", "''")
    $escapedMainScriptPath = $MainScriptPath.Replace("'", "''")
    $windowCommand = @"
Set-Location -Path '$escapedRepoRoot'
if (-not `$env:APP_ENV) { `$env:APP_ENV = 'test' }
`$Host.UI.RawUI.WindowTitle = 'miniQMT Runner [APP_ENV=' + `$env:APP_ENV + ']'
& '$escapedPythonExe' '$escapedMainScriptPath'
if (`$LASTEXITCODE -ne 0) {
    Write-Host ('main.py exited with code: {0}' -f `$LASTEXITCODE) -ForegroundColor Red
}
"@
    $process = Start-Process `
        -FilePath $mainAppShellExe `
        -ArgumentList @(
            "-ExecutionPolicy",
            "Bypass",
            "-NoExit",
            "-Command",
            $windowCommand
        ) `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Normal `
        -PassThru

    Write-Host ("main.py 已通过 {0} 在可见窗口中启动，PID={1}" -f (Split-Path $mainAppShellExe -Leaf), $process.Id) -ForegroundColor Green
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
    throw "Startup script not found: $scriptPath"
}

Set-Location $repoRoot
Write-Host ("Selected broker: {0}" -f $displayName) -ForegroundColor Cyan
& $pythonExe $scriptPath start

if ($LASTEXITCODE -ne 0) {
    throw "miniQMT startup failed with exit code: $LASTEXITCODE"
}

Start-MainApp -PythonExe $pythonExe -RepoRoot $repoRoot -MainScriptPath $mainScriptPath
