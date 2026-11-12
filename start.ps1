$ErrorActionPreference = 'Stop'
$workspace = Get-Item -LiteralPath $PSScriptRoot
while ($null -ne $workspace -and $workspace.Name -ne 'Project') { $workspace = $workspace.Parent }
if ($null -eq $workspace) { throw '未找到 Project 工作区，请指定正式部署运行时。' }
$python = Join-Path $workspace.FullName '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '共享 Python 环境不存在，请运行工作区初始化共享环境.cmd。' }
& $python -B (Join-Path $PSScriptRoot 'server.py') @args
if ($LASTEXITCODE -ne 0) { throw "启动失败：$LASTEXITCODE" }
