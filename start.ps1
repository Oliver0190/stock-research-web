$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host '请先运行 python -m venv .venv，再安装 requirements.lock.txt 中的依赖。'
    exit 1
}
Write-Host '港股观察：http://127.0.0.1:8765'
Write-Host '保持此窗口运行，网页和后台更新才会保持可用。按 Ctrl+C 停止。'
& $pythonPath -m backend
