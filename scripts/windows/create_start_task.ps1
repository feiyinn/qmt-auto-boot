$ErrorActionPreference = "Stop"

function Assert-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "当前操作需要管理员权限。请关闭当前窗口后，使用 [以管理员身份运行 PowerShell] 重新执行 create_start_task.ps1。"
    }
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$startScript = Join-Path $repoRoot "scripts\windows\start_miniqmt.ps1"
$stopScript = Join-Path $repoRoot "scripts\windows\stop_miniqmt.ps1"
$shutdownScript = Join-Path $repoRoot "scripts\windows\shutdown_with_miniqmt_stop.bat"
$deploymentConfigPath = Join-Path $repoRoot "config\miniqmt_deploy.local.json"
$taskUser = "${env:COMPUTERNAME}\${env:USERNAME}"
$taskName = "Start miniQMT at logon"
$shutdownTaskName = "Daily shutdown with miniQMT stop"

if (-not (Test-Path $startScript)) {
    throw "Start script not found: $startScript"
}

if (-not (Test-Path $shutdownScript)) {
    throw "Shutdown script not found: $shutdownScript"
}

function Get-BrokerDefinition {
    param(
        [string]$Selection
    )

    switch ($Selection) {
        "1" {
            return @{
                Broker = "gz"
                DisplayName = "国金QMT"
                ScriptPath = Join-Path $repoRoot "src\utils\gz_mini_start.py"
                ClientPath = "C:\国金证券QMT交易端\bin.x64\XtMiniQmt.exe"
            }
        }
        "2" {
            return @{
                Broker = "zj"
                DisplayName = "中金QMT"
                ScriptPath = Join-Path $repoRoot "src\utils\zj_mini_start.py"
                ClientPath = "C:\中金财富QMT个人版交易端\bin.x64\XtMiniQmt.exe"
            }
        }
        default {
            return $null
        }
    }
}

function Save-DeploymentConfig {
    param(
        [hashtable]$BrokerDefinition
    )

    $configDir = Split-Path -Parent $deploymentConfigPath
    if (-not (Test-Path $configDir)) {
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }

    $payload = [ordered]@{
        broker = $BrokerDefinition.Broker
        displayName = $BrokerDefinition.DisplayName
        scriptPath = $BrokerDefinition.ScriptPath
        clientPath = $BrokerDefinition.ClientPath
        updatedAt = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    }

    $payload | ConvertTo-Json | Set-Content -Path $deploymentConfigPath -Encoding UTF8
}

function Register-StartTask {
    param(
        [hashtable]$BrokerDefinition
    )

    Save-DeploymentConfig -BrokerDefinition $BrokerDefinition

    $action = New-ScheduledTaskAction `
        -Execute $powerShellExe `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`""

    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
    $principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Highest

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Description "Start miniQMT automatically after user logon. Broker: $($BrokerDefinition.DisplayName)" `
        -Force | Out-Null

    Write-Host ""
    Write-Host "已安装登录自启动任务。" -ForegroundColor Green
    Write-Host ("券商: {0}" -f $BrokerDefinition.DisplayName) -ForegroundColor Green
    Write-Host ("任务名: {0}" -f $taskName) -ForegroundColor Green
    Write-Host ("部署配置: {0}" -f $deploymentConfigPath) -ForegroundColor Green
}

function Get-DeploymentConfig {
    if (-not (Test-Path $deploymentConfigPath)) {
        return $null
    }

    try {
        return (Get-Content -Path $deploymentConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        Write-Host ("部署配置读取失败: {0}" -f $_.Exception.Message) -ForegroundColor Red
        return $null
    }
}

function Show-TaskInfo {
    param(
        [string]$Name
    )

    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host ("任务不存在: {0}" -f $Name) -ForegroundColor Yellow
        return
    }

    $taskInfo = Get-ScheduledTaskInfo -TaskName $Name
    Write-Host ("任务存在: {0}" -f $Name) -ForegroundColor Green
    Write-Host ("State: {0}" -f $task.State)
    Write-Host ("LastRunTime: {0}" -f $taskInfo.LastRunTime)
    Write-Host ("NextRunTime: {0}" -f $taskInfo.NextRunTime)

    foreach ($action in $task.Actions) {
        Write-Host ("Execute: {0}" -f $action.Execute)
        Write-Host ("Arguments: {0}" -f $action.Arguments)
    }
}

function Get-ExistingStartTask {
    return Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

function Get-ExistingShutdownTask {
    return Get-ScheduledTask -TaskName $shutdownTaskName -ErrorAction SilentlyContinue
}

function Read-ConsoleInput {
    param(
        [string]$PromptText
    )

    Write-Host -NoNewline $PromptText
    $value = [Console]::ReadLine()
    if ($null -eq $value) {
        throw "当前终端未提供可交互输入。请在本地 PowerShell 控制台中运行此脚本后再输入选项。"
    }

    return $value.Trim()
}

function Show-PathCheck {
    param(
        [string]$Label,
        [string]$Path,
        [string]$MissingColor = "Red",
        [string]$MissingStatus = "MISSING"
    )

    if (Test-Path $Path) {
        Write-Host ("[OK] {0}: {1}" -f $Label, $Path) -ForegroundColor Green
    } else {
        Write-Host ("[{0}] {1}: {2}" -f $MissingStatus, $Label, $Path) -ForegroundColor $MissingColor
    }
}

function Run-InstallationCheck {
    Write-Host ""
    Write-Host "=== 安装检查 ===" -ForegroundColor Cyan

    $pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $config = Get-DeploymentConfig

    Show-PathCheck -Label "仓库根目录" -Path $repoRoot
    Show-PathCheck -Label "Python" -Path $pythonExe
    Show-PathCheck -Label "启动脚本" -Path $startScript
    Show-PathCheck -Label "停止脚本" -Path $stopScript
    Show-PathCheck -Label "关机脚本" -Path $shutdownScript

    if ($config) {
        Write-Host ("[OK] 当前券商: {0} ({1})" -f $config.displayName, $config.broker) -ForegroundColor Green
        if ($config.scriptPath) {
            Show-PathCheck -Label "券商脚本" -Path $config.scriptPath
        }
        if ($config.clientPath) {
            Show-PathCheck -Label "客户端路径" -Path $config.clientPath -MissingColor "Yellow" -MissingStatus "NOT FOUND"
        }
        Write-Host ("部署配置: {0}" -f $deploymentConfigPath)
    } else {
        Write-Host ("[WARN] 尚未找到部署配置: {0}" -f $deploymentConfigPath) -ForegroundColor Yellow
    }

    Write-Host ""
    Show-TaskInfo -Name $taskName
    Write-Host ""
    Show-TaskInfo -Name $shutdownTaskName
}

function Remove-ScheduledTaskSafely {
    param(
        [string]$Name,
        [string]$SuccessLabel,
        [string]$MissingLabel
    )

    try {
        $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction Stop
            Write-Host ("已卸载{0}: {1}" -f $SuccessLabel, $Name) -ForegroundColor Green
        } else {
            Write-Host ("未找到{0}: {1}" -f $MissingLabel, $Name) -ForegroundColor Yellow
        }
    } catch {
        Write-Host ("卸载任务时跳过并继续: {0} | 原因: {1}" -f $Name, $_.Exception.Message) -ForegroundColor Yellow
    }
}

function Remove-FileSafely {
    param(
        [string]$Path
    )

    try {
        if (Test-Path $Path) {
            Remove-Item -Path $Path -Force -ErrorAction Stop
            Write-Host ("已删除部署配置: {0}" -f $Path) -ForegroundColor Green
        } else {
            Write-Host ("未找到部署配置文件: {0}" -f $Path) -ForegroundColor Yellow
        }
    } catch {
        Write-Host ("删除部署配置时跳过并继续: {0} | 原因: {1}" -f $Path, $_.Exception.Message) -ForegroundColor Yellow
    }
}

function Reset-Deployment {
    Remove-ScheduledTaskSafely -Name $taskName -SuccessLabel "开机自启动任务" -MissingLabel "开机自启动任务"
    Remove-ScheduledTaskSafely -Name $shutdownTaskName -SuccessLabel "每日关机任务" -MissingLabel "每日关机任务"
    Remove-FileSafely -Path $deploymentConfigPath

    Write-Host ""
    Write-Host "自动开关机部署已重置完成。" -ForegroundColor Cyan
}

function Read-ShutdownTime {
    $defaultTime = "23:00"
    $raw = Read-ConsoleInput -PromptText "请输入每日关机时间（HH:mm，直接回车默认 $defaultTime）: "
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $defaultTime
    }

    if ($raw -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') {
        throw "关机时间格式无效，请输入 HH:mm，例如 23:00 或 22:30。"
    }

    return $raw
}

function Register-DailyShutdownTask {
    param(
        [string]$ShutdownTime
    )

    $config = Get-DeploymentConfig
    if (-not $config) {
        Write-Host ""
        Write-Host "尚未检测到券商部署配置，暂时不能创建每日关机任务。" -ForegroundColor Yellow
        Write-Host "请先使用选项 1 或 2 完成券商安装，再回来创建每日关机任务。" -ForegroundColor Yellow
        return
    }

    $existingTask = Get-ExistingShutdownTask
    if ($existingTask) {
        Write-Host ""
        Write-Host "检测到系统中已经存在每日关机任务。" -ForegroundColor Yellow
        Write-Host ("任务名: {0}" -f $shutdownTaskName) -ForegroundColor Yellow
        Write-Host "为避免误覆盖现有设置，本次不会修改关机任务。" -ForegroundColor Yellow
        Write-Host "如需调整关机时间，请先手动删除该任务，或扩展脚本新增单独的卸载/更新入口。" -ForegroundColor Yellow
        return
    }

    $action = New-ScheduledTaskAction `
        -Execute "cmd.exe" `
        -Argument "/c `"$shutdownScript`""

    $trigger = New-ScheduledTaskTrigger -Daily -At $ShutdownTime
    $principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Highest

    Register-ScheduledTask `
        -TaskName $shutdownTaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Description "Stop miniQMT gracefully and then shut down Windows daily." `
        -Force | Out-Null

    Write-Host ""
    Write-Host "已创建每日关机任务。" -ForegroundColor Green
    if ($config.displayName) {
        Write-Host ("当前券商: {0}" -f $config.displayName) -ForegroundColor Green
    }
    Write-Host ("任务名: {0}" -f $shutdownTaskName) -ForegroundColor Green
    Write-Host ("每日关机时间: {0}" -f $ShutdownTime) -ForegroundColor Green
}

Write-Host ""
Write-Host "欢迎使用QMT/miniQMT自动开关机部署脚本" -ForegroundColor Green
Write-Host ""
Write-Host "请选择操作：" -ForegroundColor Cyan
Write-Host "1. 国金开机自启动"
Write-Host "2. 中金开机自启动"
Write-Host "3. 创建每日关机任务"
Write-Host "4. 安装检查"
Write-Host "5. 卸载自动开关机部署"
Write-Host "6. 退出"

$selection = ""
while ([string]::IsNullOrWhiteSpace($selection)) {
    $selection = Read-ConsoleInput -PromptText "请输入选项编号: "
    if ([string]::IsNullOrWhiteSpace($selection)) {
        Write-Host "输入不能为空，请输入 1、2、3、4、5 或 6。" -ForegroundColor Yellow
    }
}

$brokerDefinition = Get-BrokerDefinition -Selection $selection

if ($brokerDefinition) {
    $existingTask = Get-ExistingStartTask
    if ($existingTask) {
        $config = Get-DeploymentConfig
        Write-Host ""
        Write-Host "检测到系统中已经存在 miniQMT 登录自启动任务。" -ForegroundColor Yellow
        Write-Host ("任务名: {0}" -f $taskName) -ForegroundColor Yellow

        if ($config -and $config.displayName) {
            Write-Host ("当前已部署券商: {0}" -f $config.displayName) -ForegroundColor Yellow
        } elseif ($config -and $config.broker) {
            Write-Host ("当前已部署券商标识: {0}" -f $config.broker) -ForegroundColor Yellow
        } else {
            Write-Host "当前任务已存在，但未识别到明确的券商部署配置。" -ForegroundColor Yellow
        }

        Write-Host ("你本次选择安装的券商是: {0}" -f $brokerDefinition.DisplayName) -ForegroundColor Cyan
        Write-Host ""
        Write-Host "为避免误覆盖现有部署，本次不会执行安装，也不会修改当前配置。" -ForegroundColor Yellow
        Write-Host "如果你确认要切换券商，请按下面顺序操作：" -ForegroundColor Yellow
        Write-Host "  第一步：重新运行本脚本，输入 5，卸载现有自启动任务" -ForegroundColor Yellow
        Write-Host "  第二步：再次运行本脚本，输入 1 或 2，安装目标券商" -ForegroundColor Yellow
        exit 0
    }

    Assert-Administrator
    Register-StartTask -BrokerDefinition $brokerDefinition
    exit 0
}

switch ($selection) {
    "3" {
        Assert-Administrator
        $shutdownTime = Read-ShutdownTime
        Register-DailyShutdownTask -ShutdownTime $shutdownTime
    }
    "4" {
        Run-InstallationCheck
    }
    "5" {
        Assert-Administrator
        Reset-Deployment
    }
    "6" {
        Write-Host "已退出部署脚本。" -ForegroundColor Cyan
    }
    default {
        throw "无效选项: $selection。请输入 1、2、3、4、5 或 6。"
    }
}
