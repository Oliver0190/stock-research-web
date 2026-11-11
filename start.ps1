param([switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host '请先运行 python -m venv .venv，再安装 requirements.lock.txt 中的依赖。'
    exit 1
}
$siteUrl = 'http://127.0.0.1:8765/'
if ($OpenBrowser) {
    function Test-ResearchSite {
        try {
            $page = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $siteUrl
            return ($page.Content -match 'id="brand-market"' -and $page.Content -match '/assets/app.js')
        } catch { return $false }
    }
    if (-not (Test-ResearchSite)) {
        $server = Start-Process -FilePath $pythonPath -ArgumentList '-m','backend' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
        $deadline = (Get-Date).AddSeconds(30)
        while (-not (Test-ResearchSite)) {
            if ($server.HasExited -or (Get-Date) -gt $deadline) {
                throw 'Could not open Stock Agent on port 8765. Run start.ps1 to see the server output.'
            }
            Start-Sleep -Milliseconds 400
        }
    }
    Start-Process $siteUrl
    return
}
Write-Host $siteUrl
Write-Host '保持此窗口运行，网页和后台更新才会保持可用。按 Ctrl+C 停止。'
& $pythonPath -m backend
