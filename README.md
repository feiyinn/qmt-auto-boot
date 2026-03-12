# Windows 自动化脚本说明

本目录用于在 Windows 下自动启动、停止 miniQMT，并创建相关计划任务。

## 文件说明

- `start_miniqmt.ps1`
  - 根据本地部署配置选择券商，并调用对应的 miniQMT 启动脚本

- `stop_miniqmt.ps1`
  - 根据本地部署配置选择券商，并调用对应的 miniQMT 停止脚本

- `shutdown_with_miniqmt_stop.bat`
  - 先停止 miniQMT，再执行 Windows 关机

- `create_start_task.ps1`
  - 交互式创建“登录桌面后自动启动 miniQMT”的计划任务
  - 支持选择 `国金QMT` / `中金QMT`
  - 支持安装检查和卸载自启动任务

- `create_daily_shutdown_task.ps1`
  - 创建“每日定时先停止 miniQMT，再执行关机”的计划任务

- `check_tasks.ps1`
  - 检查当前部署配置以及上述两个计划任务是否已经创建，并输出状态、触发器和执行命令

## 前提条件

1. 已安装项目依赖，并存在虚拟环境：
   - `C:\Users\username\MyProjects\qmt-auto-boot\.venv\Scripts\python.exe`

2. 已配置本地账号文件：
   - `C:\Users\username\MyProjects\qmt-auto-boot\config\config.local.yaml`
   - 国金版本需要账号密码
   - 中金版本通常只依赖已记住的账号密码和验证码处理

3. Windows 已自动登录到桌面
   - 这类 UI 自动化依赖交互式桌面会话

## 手动测试

先确认对应券商的 Python 启动脚本工作正常：

```powershell
python .\src\utils\gz_mini_start.py start
python .\src\utils\gz_mini_start.py stop
```

或：

```powershell
python .\src\utils\zj_mini_start.py start
python .\src\utils\zj_mini_start.py stop
```

## 创建计划任务

请使用“管理员身份”打开 PowerShell，然后进入项目根目录执行。

### 1. 创建登录后自动启动任务

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\create_start_task.ps1
```

脚本执行后会显示交互菜单：

- `1`：国金开机自启动
- `2`：中金开机自启动
- `3`：创建每日关机任务
- `4`：安装检查
- `5`：卸载自动开关机部署
- `6`：退出

选择 `1` 或 `2` 后，会同时：

- 写入本地部署配置 `config\miniqmt_deploy.local.json`
- 创建登录自启动任务

注意：

- `1` 和 `2` 只负责登录自启动，不会自动创建每日关机任务
- 如果脚本检测到当前已经存在 `Start miniQMT at logon`，则不会覆盖现有配置，也不会改写任务，而是提示先选择 `5` 卸载后再重新安装

### 2. 创建每日定时关机任务

重新运行同一个脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\create_start_task.ps1
```

然后输入 `3`。

脚本会继续提示输入关机时间，格式为 `HH:mm`，例如：

- `23:00`
- `22:30`

直接回车时，默认使用 `23:00`。

创建的任务名：

- `Start miniQMT at logon`

创建的任务名：

- `Daily shutdown with miniQMT stop`

## 检查任务状态

执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\check_tasks.ps1
```

脚本会输出：

- 当前部署券商配置
- 任务是否存在
- 当前状态
- 上次运行时间
- 下次运行时间
- 触发器类型
- 实际执行命令

## 任务删除

如果后续需要重置整套自动开关机部署，可以直接重新运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\create_start_task.ps1
```

然后输入 `5`。

选项 `5` 会同时执行以下操作：

- 删除 `Start miniQMT at logon`
- 删除 `Daily shutdown with miniQMT stop`
- 删除 `config\miniqmt_deploy.local.json`

如果需要手动删除任务，也可以执行：

```powershell
Unregister-ScheduledTask -TaskName "Start miniQMT at logon" -Confirm:$false
Unregister-ScheduledTask -TaskName "Daily shutdown with miniQMT stop" -Confirm:$false
```

## 建议

- 首次创建计划任务后，先手动执行一次检查脚本确认任务已落地
- 再手动重启机器，确认“自动登录桌面 -> 自动启动 miniQMT”整条链路工作正常
- 定时关机建议先设置在一个便于观察的时间点做一次演练
